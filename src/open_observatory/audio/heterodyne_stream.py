"""Live heterodyne: the native ultrasonic stream mixed down to the audible band
in real time, exactly as a handheld bat detector does.

``ultrasound.py`` renders a *clip* — a fixed array, processed once, with no
constraint on how long the maths takes. This module renders a stream: capture
never stops, so every call must return in less time than the audio it was
given represents, and every call must sound like a continuation of the last
one rather than a fresh start.

That continuity requirement is the whole point of this file. A clip renderer
called once per chunk would regenerate ``cos(2*pi*f*t)`` from a chunk-local
``t = 0``, and reset the low-pass filter's memory to zero, on every call. Both
produce an audible click at the join — the oscillator's phase jumps and the
filter briefly rings from a false silent start — often several times a
second. Two pieces of state are therefore carried across calls, not
recomputed from scratch:

1. **Oscillator phase.** Accumulated a chunk at a time rather than evaluated
   from an absolute sample count, so it can run for hours without losing
   float precision. Only the small, wrapped residual (``[0, 2*pi)``) persists
   between calls — never a growing sample counter multiplied by a frequency.

2. **Low-pass filter memory.** Implemented as overlap-save: the last
   ``taps - 1`` samples of each chunk are kept and prepended to the next, so
   the FIR filter always sees real history instead of implicit zeros at every
   chunk boundary. This is mathematically identical, to floating-point
   rounding, to filtering the whole continuous signal in one pass — which is
   exactly what the continuity test checks.

Decimation (384 kHz -> 48 kHz is exactly 1/8) also carries a running sample
count, so which samples are kept never depends on where a chunk happened to
start or end.

Retuning at runtime ramps the oscillator's *frequency* linearly across the
next chunk rather than jumping it, so turning the dial does not click either.
"""

from __future__ import annotations

import numpy as np

TWO_PI = 2.0 * np.pi

#: Odd tap count keeps the sinc kernel's centre sample exact (no fractional
#: group delay to reason about) while staying cheap enough for continuous
#: 384 kHz operation on a Raspberry Pi 5: taps * chunk_frames multiply-adds
#: per call, done in numpy's C loop rather than Python.
DEFAULT_TAPS = 129


def _design_lowpass(cutoff_hz: float, sample_rate: int, *, taps: int = DEFAULT_TAPS) -> np.ndarray:
    """A windowed-sinc FIR low-pass, used both to select the tuned band and as
    the anti-alias filter ahead of decimation.

    Not scipy: this runs continuously on the hot path, and a hand-rolled
    windowed sinc is a few lines, needs no optional dependency, and its
    frequency response is easy to reason about (see the attenuation test).
    """
    if taps % 2 == 0:
        taps += 1
    nyquist = sample_rate / 2.0
    cutoff = max(1.0, min(float(cutoff_hz), nyquist * 0.95))
    n = np.arange(taps, dtype=np.float64) - (taps - 1) / 2.0
    with np.errstate(divide="ignore", invalid="ignore"):
        kernel = np.sin(TWO_PI * cutoff / sample_rate * n) / (np.pi * n)
    centre = taps // 2
    kernel[centre] = 2.0 * cutoff / sample_rate
    window = np.hamming(taps)
    kernel = kernel * window
    total = np.sum(kernel)
    if total != 0:
        kernel = kernel / total
    return kernel.astype(np.float64)


class StreamingHeterodyne:
    """Continuous-phase heterodyne + decimation, safe to call chunk after chunk.

    ``process()`` may be called with chunks of any (nonzero) length — the
    capture block size, not this class, decides that — and the output is
    exactly as if the entire history since construction had been heterodyned
    in one pass.
    """

    def __init__(
        self,
        native_rate: int,
        *,
        output_rate: int = 48000,
        tune_hz: float,
        bandwidth_hz: float,
        taps: int = DEFAULT_TAPS,
    ) -> None:
        if native_rate <= 0 or output_rate <= 0:
            raise ValueError("sample rates must be positive")
        if native_rate % output_rate != 0:
            # The brief this module was built against assumes exactly 1/8
            # (384 kHz -> 48 kHz), which needs no fractional resampling.
            # Anything that doesn't divide evenly would need the same
            # machinery as AudibleResampler; rather than silently produce a
            # subtly wrong ratio, refuse construction and let the caller
            # decide (station.py leaves the ultrasonic channel unavailable).
            raise ValueError(
                f"streaming heterodyne needs native_rate to be an integer multiple of "
                f"output_rate; {native_rate} Hz is not a multiple of {output_rate} Hz"
            )
        if tune_hz <= 0 or tune_hz >= native_rate / 2.0:
            raise ValueError("tune_hz must be within (0, native nyquist)")

        self.native_rate = native_rate
        self.output_rate = output_rate
        self.decimation = native_rate // output_rate
        self.taps = taps if taps % 2 else taps + 1

        self._tune_hz = float(tune_hz)
        self._target_tune_hz = float(tune_hz)
        self.bandwidth_hz = float(bandwidth_hz)

        #: Radians, wrapped to [0, 2*pi) after every call. This is the only
        #: state that survives across hours of runtime, so it must never grow
        #: unbounded.
        self._phase = 0.0
        self._native_samples_seen = 0
        self._kernel = _design_lowpass(self._filter_cutoff_hz(), native_rate, taps=self.taps)
        self._tail = np.zeros(self.taps - 1, dtype=np.float64)

    def _filter_cutoff_hz(self) -> float:
        # The kept band must satisfy two needs at once: select only what is
        # near the tuning frequency, and act as the anti-alias filter for
        # decimating to output_rate. Whichever is stricter wins.
        return min(self.bandwidth_hz, self.output_rate / 2.0 * 0.95)

    @property
    def tune_hz(self) -> float:
        """The requested tuning frequency (the oscillator ramps towards it)."""
        return self._target_tune_hz

    def set_tune_hz(self, value: float) -> None:
        """Retune without a click.

        The oscillator's *frequency* ramps linearly from the current value to
        ``value`` across the next chunk it processes, rather than jumping —
        phase stays continuous throughout, so there is no discontinuity to
        click.
        """
        if value <= 0 or value >= self.native_rate / 2.0:
            raise ValueError("tune_hz must be within (0, native nyquist)")
        self._target_tune_hz = float(value)

    def set_bandwidth_hz(self, value: float) -> None:
        """Change the kept bandwidth. Redesigns the filter kernel; the tap
        count (and therefore the retained tail length) is unchanged, so state
        continuity across the switch is preserved even though the exact
        samples at the switch point reflect a brief blend of old and new
        filter characteristics — the ordinary behaviour of retuning any real
        filter, not a bug."""
        if value <= 0:
            raise ValueError("bandwidth_hz must be positive")
        self.bandwidth_hz = float(value)
        self._kernel = _design_lowpass(self._filter_cutoff_hz(), self.native_rate, taps=self.taps)

    def process(self, native_pcm: np.ndarray) -> np.ndarray:
        """Heterodyne, low-pass and decimate one native-rate chunk.

        Returns float32 PCM at ``output_rate``, continuous with every
        previous call to this instance.
        """
        if native_pcm.size == 0:
            return np.zeros(0, dtype=np.float32)
        x = np.asarray(native_pcm, dtype=np.float64)
        n = x.shape[0]

        # 1. Local oscillator. Frequency is linearly ramped across the chunk
        #    from wherever it last was to the current target, so a runtime
        #    retune cannot click; phase is the running integral of that
        #    frequency, continuing exactly where the previous chunk left off.
        freqs = np.linspace(self._tune_hz, self._target_tune_hz, n, dtype=np.float64)
        self._tune_hz = self._target_tune_hz
        phase_inc = TWO_PI * freqs / self.native_rate
        phases = self._phase + np.cumsum(phase_inc)
        oscillator = np.cos(phases)
        # Only the small wrapped residual is kept — never an unbounded count.
        self._phase = float(phases[-1] % TWO_PI)

        mixed = x * oscillator

        # 2. Stateful low-pass (overlap-save FIR). The retained tail supplies
        #    real history instead of implicit zeros, so this is identical,
        #    to floating-point rounding, to filtering the whole continuous
        #    signal in one pass.
        padded = np.concatenate((self._tail, mixed))
        filtered = np.convolve(padded, self._kernel, mode="valid")
        self._tail = padded[-(self.taps - 1):].copy() if self.taps > 1 else self._tail

        # 3. Decimate with a running sample count, so which samples are kept
        #    never depends on where a chunk happened to start or end.
        offset = (-self._native_samples_seen) % self.decimation
        self._native_samples_seen += n
        decimated = filtered[offset :: self.decimation]

        return decimated.astype(np.float32)

    def describe(self) -> dict[str, object]:
        return {
            "native_rate": self.native_rate,
            "output_rate": self.output_rate,
            "decimation": self.decimation,
            "tune_hz": round(self._target_tune_hz, 1),
            "bandwidth_hz": self.bandwidth_hz,
            "taps": self.taps,
            "native_samples_processed": self._native_samples_seen,
            "real_time": True,
            "discards": "everything outside the tuned band; for listening, not measurement",
            "amplitudes_comparable_to_native": False,
        }
