"""Ultrasonic bat-pass detector, running on the native high-rate stream.

This exists because the derived 48 kHz stream is band-limited to 24 kHz and
essentially every UK bat echolocates between 20 and 120 kHz — so on the audible
stream there is nothing to find, and a detector that pretended otherwise would be
lying. The AudioMoth on this station captures at 384 kHz, giving a 192 kHz
Nyquist, which covers every UK species.

The method is the band-energy/pulse-train approach a hardware heterodyne detector
uses: find short high-SNR pulses in the ultrasonic band, measure each pulse's
peak frequency and duration, and group pulse trains into "passes". That produces
real, useful activity data — passes per night, peak-frequency distribution —
without a PyTorch install on a Pi.

**It does not identify species.** Peak frequency yields at best a coarse group
hint, which is reported as a hint and stored as such. BatDetect2 would be a
drop-in replacement for :meth:`_summarise` once benchmarked on this hardware
(Milestone 5); this detector is deliberately not called a classifier.

Derived from the ultrasonic detector prototyped in the OutdoorAcousticEvents
project, reworked onto this project's window contract.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..audio.contracts import (
    AudioWindow,
    DetectorHealth,
    DetectorMetadata,
    NativeDetection,
    WindowSpec,
)
from .base import DetectorContext, DetectorUnavailable

PLUGIN_VERSION = "1.0.0"

#: Coarse peak-frequency groups for UK species. A hint on top of a pass, never
#: an identification — several of these bands overlap between species.
FREQUENCY_HINTS: tuple[tuple[float, float, str], ...] = (
    (17_000, 26_000, "Nyctalus / Eptesicus group (noctule, serotine)"),
    (26_000, 38_000, "Myotis / Barbastelle group"),
    (38_000, 50_000, "Pipistrellus pipistrellus group (common pipistrelle)"),
    (50_000, 62_000, "Pipistrellus pygmaeus group (soprano pipistrelle)"),
    (62_000, 90_000, "Rhinolophus ferrumequinum / high-frequency group"),
    (90_000, 130_000, "Rhinolophus hipposideros group (lesser horseshoe)"),
)


def frequency_hint(hz: float) -> str | None:
    for low, high, name in FREQUENCY_HINTS:
        if low <= hz < high:
            return name
    return None


@dataclass(frozen=True, slots=True)
class Pulse:
    offset_s: float
    duration_s: float
    peak_hz: float
    snr_db: float


class UltrasonicDetector:
    """Detects bat passes as trains of short ultrasonic pulses."""

    metadata = DetectorMetadata(
        plugin_id="ultrasonic-pass-v1",
        plugin_version=PLUGIN_VERSION,
        model_id="band-energy-pulse-train",
        model_version=PLUGIN_VERSION,
        model_sha256=None,
        taxonomy_version=None,
        licence_name="Apache-2.0",
        licence_url=None,
        claim=(
            "Detects trains of short ultrasonic pulses consistent with bat "
            "echolocation and reports pulse count, timing and peak frequency. "
            "Peak frequency gives a coarse group hint only; this is not a "
            "species identification."
        ),
        resource_class="moderate",
        calibrated=False,
        external_network="none",
    )

    #: Below this native rate there is no ultrasonic band to inspect.
    MIN_SAMPLE_RATE = 96_000

    def __init__(
        self,
        *,
        native_sample_rate: int,
        window_s: float = 2.0,
        stride_s: float = 2.0,
        band_hz: tuple[float, float] = (15_000.0, 125_000.0),
        min_snr_db: float = 12.0,
        min_pulse_ms: float = 1.5,
        max_pulse_ms: float = 40.0,
        pass_gap_s: float = 1.5,
        min_pulses_per_pass: int = 3,
    ) -> None:
        self.native_sample_rate = native_sample_rate
        self.window_spec = WindowSpec(
            stream_kind="native",
            sample_rate=native_sample_rate,
            duration_s=window_s,
            stride_s=stride_s,
            # Bats are a night-time, lower-priority load; tolerate more lag than
            # the audible path without dropping work.
            max_delivery_latency_s=60.0,
            priority=50,
        )
        self._band = band_hz
        self._min_snr_db = min_snr_db
        self._min_pulse_s = min_pulse_ms / 1000.0
        self._max_pulse_s = max_pulse_ms / 1000.0
        self._pass_gap_s = pass_gap_s
        self._min_pulses = min_pulses_per_pass
        self._noise_floor_db: float | None = None
        self.pulses_found = 0
        self.passes_found = 0
        self._blocks = 0

    async def initialise(self, context: DetectorContext) -> None:
        if self.native_sample_rate < self.MIN_SAMPLE_RATE:
            raise DetectorUnavailable(
                f"native stream is {self.native_sample_rate} Hz; ultrasonic detection "
                f"needs at least {self.MIN_SAMPLE_RATE} Hz to see above 24 kHz"
            )
        self._noise_floor_db = None

    async def analyse(self, window: AudioWindow) -> list[NativeDetection]:
        pcm = np.asarray(window.pcm, dtype=np.float32)
        rate = window.sample_rate
        self._blocks += 1
        pulses = self._find_pulses(pcm, rate)
        self.pulses_found += len(pulses)
        if len(pulses) < self._min_pulses:
            return []

        # Split the pulse list into passes at gaps longer than pass_gap_s.
        detections: list[NativeDetection] = []
        group: list[Pulse] = []
        for pulse in pulses:
            if group and pulse.offset_s - group[-1].offset_s > self._pass_gap_s:
                if len(group) >= self._min_pulses:
                    detections.append(self._summarise(group))
                group = []
            group.append(pulse)
        if len(group) >= self._min_pulses:
            detections.append(self._summarise(group))
        self.passes_found += len(detections)
        return detections

    def _find_pulses(self, pcm: np.ndarray, rate: int) -> list[Pulse]:
        # ~0.5 ms time resolution, enough to resolve individual echolocation calls.
        nperseg = max(64, 1 << int(np.log2(max(64, rate // 2000))))
        hop = nperseg // 2
        if pcm.shape[0] < nperseg:
            return []
        count = 1 + (pcm.shape[0] - nperseg) // hop
        indices = np.arange(nperseg)[None, :] + hop * np.arange(count)[:, None]
        frames = pcm[indices] * np.hanning(nperseg).astype(np.float32)
        power = (np.abs(np.fft.rfft(frames, axis=1)) ** 2).T
        freqs = np.fft.rfftfreq(nperseg, 1.0 / rate)
        times = (np.arange(count) * hop + nperseg / 2) / rate

        mask = (freqs >= self._band[0]) & (freqs <= min(self._band[1], rate / 2 - 1))
        if not mask.any():
            return []
        band_power = power[mask, :]
        band_freqs = freqs[mask]
        energy_db = 10.0 * np.log10(band_power.sum(axis=0) + 1e-20)

        block_floor = float(np.median(energy_db))
        floor = (
            block_floor
            if self._noise_floor_db is None
            else 0.9 * self._noise_floor_db + 0.1 * block_floor
        )
        self._noise_floor_db = floor

        above = energy_db > floor + self._min_snr_db
        bin_dt = float(times[1] - times[0]) if times.size > 1 else hop / rate

        pulses: list[Pulse] = []
        start: int | None = None
        for index, flag in enumerate(above):
            if flag and start is None:
                start = index
            elif not flag and start is not None:
                pulses.append(
                    self._make_pulse(
                        start, index, times, bin_dt, band_power, band_freqs, energy_db, floor
                    )
                )
                start = None
        if start is not None:
            pulses.append(
                self._make_pulse(
                    start, len(above), times, bin_dt, band_power, band_freqs, energy_db, floor
                )
            )
        return [p for p in pulses if self._min_pulse_s <= p.duration_s <= self._max_pulse_s]

    @staticmethod
    def _make_pulse(
        start: int,
        end: int,
        times: np.ndarray,
        bin_dt: float,
        band_power: np.ndarray,
        band_freqs: np.ndarray,
        energy_db: np.ndarray,
        floor: float,
    ) -> Pulse:
        segment = band_power[:, start:end]
        peak_bin = int(np.unravel_index(int(np.argmax(segment)), segment.shape)[0])
        return Pulse(
            offset_s=float(times[start]),
            duration_s=(end - start) * bin_dt,
            peak_hz=float(band_freqs[peak_bin]),
            snr_db=float(energy_db[start:end].max() - floor),
        )

    def _summarise(self, pulses: list[Pulse]) -> NativeDetection:
        peaks = np.array([p.peak_hz for p in pulses])
        starts = np.array([p.offset_s for p in pulses])
        intervals = np.diff(starts)
        median_peak = float(np.median(peaks))
        peak_snr = max(p.snr_db for p in pulses)
        hint = frequency_hint(median_peak)
        return NativeDetection(
            offset_start_s=float(starts[0]),
            offset_end_s=float(starts[-1] + pulses[-1].duration_s),
            # Score rises with both pulse count and SNR: a coherent train of many
            # pulses is stronger evidence than one loud click.
            score=float(
                np.clip(
                    0.4 * min(1.0, len(pulses) / 8.0) + 0.6 * min(1.0, (peak_snr - 12.0) / 24.0),
                    0.0,
                    1.0,
                )
            ),
            label="bat pass",
            rank=None,
            taxonomic_group="bat",
            peak_frequency_hz=median_peak,
            native_result={
                "detector": "ultrasonic-pass-v1",
                "pulse_count": len(pulses),
                "median_peak_hz": round(median_peak, 1),
                "min_peak_hz": round(float(peaks.min()), 1),
                "max_peak_hz": round(float(peaks.max()), 1),
                "median_interval_ms": round(float(np.median(intervals)) * 1000.0, 1)
                if intervals.size
                else None,
                "mean_pulse_duration_ms": round(
                    float(np.mean([p.duration_s for p in pulses])) * 1000.0, 2
                ),
                "peak_snr_db": round(float(peak_snr), 1),
                "frequency_group_hint": hint,
                "hint_is_not_identification": True,
                "score_definition": (
                    "0.4*min(1, pulses/8) + 0.6*min(1, (peak_snr_db - 12)/24)"
                ),
            },
        )

    async def health(self) -> DetectorHealth:
        return DetectorHealth(
            available=True,
            state="ok",
            detail=(
                f"{self.pulses_found} pulses, {self.passes_found} passes over "
                f"{self._blocks} windows; noise floor "
                f"{self._noise_floor_db:.1f} dB"
                if self._noise_floor_db is not None
                else "awaiting audio"
            ),
        )

    async def shutdown(self) -> None:
        self._noise_floor_db = None
