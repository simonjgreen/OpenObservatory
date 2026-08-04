"""Rendering ultrasound into something a human can actually hear.

A 45 kHz bat call is real, recorded, and completely inaudible: human hearing stops
around 18 kHz and a browser cannot even decode a 384 kHz WAV. An evidence clip you
cannot listen to is only half a piece of evidence, so ultrasonic detections get an
extra derivative alongside the authoritative native recording.

Two methods, because they answer different questions.

**Time expansion** replays the recording slowed by a factor *N*, so every frequency
divides by *N* and a 45 kHz call becomes 4.5 kHz. Nothing is discarded: harmonics,
sweep shape, amplitude envelope and the relative timing of pulses all survive
exactly. This is what hardware detectors call TE mode and what recordists use for
identification. The cost is that the clip lasts *N* times longer.

Implementation note: no resampling is involved. The samples are written unchanged
with a lower rate in the WAV header, which *is* time expansion, exactly and without
any filter artefacts.

**Heterodyning** multiplies the signal by a local oscillator tuned near the call and
keeps the difference frequency, which is what a handheld bat detector does. Real-time
duration is preserved, so a pass sounds like the sequence of clicks a surveyor would
recognise. Everything outside the tuned band is thrown away, so it is for listening
and not for measurement — and this module labels it as such.

Both start by removing everything below the ultrasonic band. Wind, traffic and
handling noise dominate the low end, and either method would otherwise fold that
rumble into the output on top of the call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

Method = Literal["time-expansion", "heterodyne"]


@dataclass(frozen=True, slots=True)
class AudibleRender:
    """PCM plus the sample rate it must be written at, and how it was made."""

    pcm: np.ndarray
    sample_rate: int
    method: Method
    #: Human-readable description for the UI, e.g. "x10 time expansion".
    description: str
    detail: dict[str, object]

    @property
    def duration_s(self) -> float:
        return float(self.pcm.shape[0]) / self.sample_rate


def highpass(pcm: np.ndarray, sample_rate: int, cutoff_hz: float) -> np.ndarray:
    """Zero every spectral component below ``cutoff_hz``.

    A brick wall in the frequency domain rather than an IIR design: this runs
    offline on a short clip, so there is no reason to accept passband ripple or to
    take a dependency on scipy, which is optional in this project.
    """
    if pcm.size == 0 or cutoff_hz <= 0:
        return pcm
    count = int(pcm.shape[0])
    spectrum = np.fft.rfft(pcm.astype(np.float64))
    freqs = np.fft.rfftfreq(count, 1.0 / sample_rate)
    spectrum[freqs < cutoff_hz] = 0.0
    filtered = np.fft.irfft(spectrum, n=count).astype(np.float32)
    return _fade_edges(filtered, sample_rate)


def bandpass_around(
    pcm: np.ndarray, sample_rate: int, centre_hz: float, bandwidth_hz: float
) -> np.ndarray:
    """Keep only ``centre_hz ± bandwidth_hz``."""
    if pcm.size == 0:
        return pcm
    count = int(pcm.shape[0])
    spectrum = np.fft.rfft(pcm.astype(np.float64))
    freqs = np.fft.rfftfreq(count, 1.0 / sample_rate)
    keep = (freqs >= max(0.0, centre_hz - bandwidth_hz)) & (freqs <= centre_hz + bandwidth_hz)
    spectrum[~keep] = 0.0
    return np.fft.irfft(spectrum, n=count).astype(np.float32)


def _fade_edges(pcm: np.ndarray, sample_rate: int, milliseconds: float = 2.0) -> np.ndarray:
    """Short fade in/out, so brick-wall filtering cannot leave an edge click."""
    length = min(int(sample_rate * milliseconds / 1000.0), pcm.shape[0] // 2)
    if length <= 1:
        return pcm
    ramp = np.linspace(0.0, 1.0, length, dtype=np.float32)
    out = pcm.copy()
    out[:length] *= ramp
    out[-length:] *= ramp[::-1]
    return out


def _normalise(pcm: np.ndarray, headroom_dbfs: float = -3.0) -> tuple[np.ndarray, float]:
    """Scale to a comfortable listening level, reporting the gain applied.

    Ultrasonic calls are often 40 dB below full scale after high-pass filtering, so
    an un-normalised derivative is inaudible even after being shifted into the
    audible band. The gain is recorded rather than hidden, because it means this
    derivative's amplitudes are not comparable with the native recording's.
    """
    peak = float(np.abs(pcm).max()) if pcm.size else 0.0
    if peak <= 1e-9:
        return pcm, 0.0
    target = 10.0 ** (headroom_dbfs / 20.0)
    gain = target / peak
    return (pcm * gain).astype(np.float32), 20.0 * float(np.log10(gain))


def choose_expansion_factor(
    peak_hz: float | None, target_hz: float, *, fixed: float = 0.0
) -> float:
    """Pick a factor that lands the call near ``target_hz``.

    An adaptive factor keeps a 25 kHz noctule and a 110 kHz lesser horseshoe equally
    listenable; a single fixed factor would suit one and bury the other. Rounded to
    a whole number because "slowed 10 times" is far easier to reason about than
    "slowed 9.4 times".
    """
    if fixed and fixed > 1:
        return float(fixed)
    if not peak_hz or peak_hz <= target_hz:
        return 1.0
    return float(max(2, min(64, round(peak_hz / target_hz))))


def time_expand(
    pcm: np.ndarray,
    sample_rate: int,
    *,
    peak_hz: float | None,
    target_hz: float = 4000.0,
    fixed_factor: float = 0.0,
    highpass_hz: float = 12000.0,
    max_seconds: float = 60.0,
) -> AudibleRender | None:
    """Slow the recording so its ultrasound lands in the audible band."""
    if pcm.size == 0:
        return None
    factor = choose_expansion_factor(peak_hz, target_hz, fixed=fixed_factor)
    if factor <= 1:
        return None

    filtered = highpass(pcm, sample_rate, highpass_hz)
    output_rate = int(round(sample_rate / factor))
    if output_rate < 8000:
        # Below this a browser may refuse the file outright; back the factor off.
        factor = sample_rate / 8000.0
        output_rate = 8000

    # Trim before expansion, since expansion multiplies duration.
    max_input_frames = int(max_seconds * output_rate)
    if filtered.shape[0] > max_input_frames:
        centre = filtered.shape[0] // 2
        half = max_input_frames // 2
        filtered = filtered[max(0, centre - half) : max(0, centre - half) + max_input_frames]

    normalised, gain_db = _normalise(filtered)
    audible_peak = (peak_hz / factor) if peak_hz else None
    return AudibleRender(
        pcm=normalised,
        sample_rate=output_rate,
        method="time-expansion",
        description=f"x{factor:g} time expansion",
        detail={
            "method": "time-expansion",
            "factor": factor,
            "source_sample_rate": sample_rate,
            "written_sample_rate": output_rate,
            "highpass_hz": highpass_hz,
            "source_peak_hz": round(peak_hz, 1) if peak_hz else None,
            "audible_peak_hz": round(audible_peak, 1) if audible_peak else None,
            "normalisation_gain_db": round(gain_db, 1),
            "plays_slower_by": factor,
            "preserves": "harmonics, sweep shape, pulse timing (all divided by the factor)",
            "amplitudes_comparable_to_native": False,
        },
    )


def heterodyne(
    pcm: np.ndarray,
    sample_rate: int,
    *,
    tune_hz: float | None,
    bandwidth_hz: float = 5000.0,
    output_rate: int = 48000,
    highpass_hz: float = 12000.0,
    max_seconds: float = 60.0,
) -> AudibleRender | None:
    """Mix the tuned band down to baseband, as a handheld bat detector does."""
    if pcm.size == 0 or not tune_hz or tune_hz <= 0:
        return None
    nyquist = sample_rate / 2.0
    if tune_hz >= nyquist:
        return None

    filtered = highpass(pcm, sample_rate, min(highpass_hz, tune_hz * 0.5))
    if filtered.shape[0] > int(max_seconds * sample_rate):
        filtered = filtered[: int(max_seconds * sample_rate)]

    # Isolate the band of interest, then shift it to baseband. Multiplying by a
    # cosine produces sum and difference frequencies; the low-pass that follows
    # keeps the difference, which is the audible copy.
    banded = bandpass_around(filtered, sample_rate, tune_hz, bandwidth_hz)
    t = np.arange(banded.shape[0], dtype=np.float64) / sample_rate
    mixed = (banded.astype(np.float64) * np.cos(2 * np.pi * tune_hz * t)).astype(np.float32)

    # Low-pass to the retained bandwidth before decimating, or the discarded sum
    # component would alias straight back into the result.
    count = int(mixed.shape[0])
    spectrum = np.fft.rfft(mixed.astype(np.float64))
    freqs = np.fft.rfftfreq(count, 1.0 / sample_rate)
    spectrum[freqs > min(bandwidth_hz, output_rate / 2 * 0.95)] = 0.0
    baseband = np.fft.irfft(spectrum, n=count).astype(np.float32)

    from .resample import AudibleResampler

    decimated = (
        AudibleResampler(sample_rate, output_rate).process(baseband).pcm
        if sample_rate != output_rate
        else baseband
    )
    normalised, gain_db = _normalise(_fade_edges(decimated, output_rate))
    return AudibleRender(
        pcm=normalised,
        sample_rate=output_rate,
        method="heterodyne",
        description=f"heterodyne tuned to {tune_hz / 1000:.1f} kHz",
        detail={
            "method": "heterodyne",
            "tuned_hz": round(tune_hz, 1),
            "bandwidth_hz": bandwidth_hz,
            "source_sample_rate": sample_rate,
            "written_sample_rate": output_rate,
            "normalisation_gain_db": round(gain_db, 1),
            "real_time": True,
            "discards": (
                "everything outside the tuned band; for listening, not measurement"
            ),
            "amplitudes_comparable_to_native": False,
        },
    )


def render(
    pcm: np.ndarray,
    sample_rate: int,
    *,
    method: str,
    peak_hz: float | None,
    target_hz: float = 4000.0,
    fixed_factor: float = 0.0,
    highpass_hz: float = 12000.0,
    bandwidth_hz: float = 5000.0,
    max_seconds: float = 60.0,
) -> list[AudibleRender]:
    """Produce the configured audible derivative(s) of an ultrasonic clip."""
    if method == "none":
        return []
    wanted: list[Method] = (
        ["time-expansion", "heterodyne"] if method == "both" else [method]  # type: ignore[list-item]
    )
    results: list[AudibleRender] = []
    for choice in wanted:
        if choice == "time-expansion":
            rendered = time_expand(
                pcm,
                sample_rate,
                peak_hz=peak_hz,
                target_hz=target_hz,
                fixed_factor=fixed_factor,
                highpass_hz=highpass_hz,
                max_seconds=max_seconds,
            )
        elif choice == "heterodyne":
            rendered = heterodyne(
                pcm,
                sample_rate,
                tune_hz=peak_hz,
                bandwidth_hz=bandwidth_hz,
                highpass_hz=highpass_hz,
                max_seconds=max_seconds,
            )
        else:
            continue
        if rendered is not None:
            results.append(rendered)
    return results
