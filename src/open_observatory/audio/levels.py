"""Per-block and per-second audio level telemetry.

These are *operational measurements*, not calibrated sound pressure levels. The
audio pipeline spec is explicit about that, and the UI must label them as such:
without a calibration procedure, dBFS is a statement about the ADC, not about
the garden.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

#: Anything at or beyond this magnitude is treated as clipped in float PCM.
CLIP_THRESHOLD = 0.999


def _db(value: float) -> float:
    return 20.0 * float(np.log10(max(value, 1e-12)))


@dataclass(frozen=True, slots=True)
class LevelSample:
    """Levels for one contiguous span of audio."""

    frames: int
    sample_rate: int
    rms: float
    peak: float
    clipped_samples: int
    dc_offset: float

    @property
    def rms_dbfs(self) -> float:
        return _db(self.rms)

    @property
    def peak_dbfs(self) -> float:
        return _db(self.peak)

    @property
    def crest_factor_db(self) -> float:
        return self.peak_dbfs - self.rms_dbfs

    @property
    def clipping_ratio(self) -> float:
        return self.clipped_samples / self.frames if self.frames else 0.0

    @property
    def silent(self) -> bool:
        """True when the signal is implausibly quiet — usually a dead input."""
        return self.peak < 1e-5

    @property
    def constant(self) -> bool:
        """True when there is level but no variation — a stuck ADC or DC only."""
        return self.peak > 1e-5 and self.rms > 0 and abs(self.peak - self.rms) < 1e-9

    def to_dict(self) -> dict[str, object]:
        return {
            "frames": self.frames,
            "sample_rate": self.sample_rate,
            "rms_dbfs": round(self.rms_dbfs, 2),
            "peak_dbfs": round(self.peak_dbfs, 2),
            "crest_factor_db": round(self.crest_factor_db, 2),
            "clipped_samples": self.clipped_samples,
            "clipping_ratio": round(self.clipping_ratio, 6),
            "dc_offset": round(self.dc_offset, 6),
            "silent": self.silent,
        }


def measure(pcm: np.ndarray, sample_rate: int) -> LevelSample:
    if pcm.size == 0:
        return LevelSample(0, sample_rate, 0.0, 0.0, 0, 0.0)
    absolute = np.abs(pcm)
    return LevelSample(
        frames=int(pcm.shape[0]),
        sample_rate=sample_rate,
        rms=float(np.sqrt(np.mean(np.square(pcm, dtype=np.float64)))),
        peak=float(absolute.max()),
        clipped_samples=int(np.count_nonzero(absolute >= CLIP_THRESHOLD)),
        dc_offset=float(np.mean(pcm, dtype=np.float64)),
    )


def band_energies(
    pcm: np.ndarray, sample_rate: int, bands: tuple[tuple[float, float], ...]
) -> list[float]:
    """Mean power in dB for each ``(low_hz, high_hz)`` band."""
    if pcm.size < 16:
        return [-120.0] * len(bands)
    size = 1 << int(np.ceil(np.log2(min(pcm.shape[0], 8192))))
    window = np.hanning(size)
    segment = pcm[:size] if pcm.shape[0] >= size else np.pad(pcm, (0, size - pcm.shape[0]))
    spectrum = np.abs(np.fft.rfft(segment * window)) ** 2
    freqs = np.fft.rfftfreq(size, 1.0 / sample_rate)
    results: list[float] = []
    for low, high in bands:
        mask = (freqs >= low) & (freqs < min(high, sample_rate / 2))
        results.append(10.0 * float(np.log10(spectrum[mask].mean() + 1e-20)) if mask.any() else -120.0)
    return results


@dataclass
class LevelAggregator:
    """Accumulates block levels into one sample per second of stream time."""

    sample_rate: int
    history_seconds: int = 900
    _accumulated_frames: int = 0
    _sum_squares: float = 0.0
    _peak: float = 0.0
    _clipped: int = 0
    _dc_sum: float = 0.0
    history: deque[LevelSample] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self.history = deque(maxlen=self.history_seconds)

    def push(self, pcm: np.ndarray) -> LevelSample | None:
        """Add a block; returns a per-second sample when one completes."""
        if pcm.size == 0:
            return None
        absolute = np.abs(pcm)
        self._accumulated_frames += int(pcm.shape[0])
        self._sum_squares += float(np.sum(np.square(pcm, dtype=np.float64)))
        self._peak = max(self._peak, float(absolute.max()))
        self._clipped += int(np.count_nonzero(absolute >= CLIP_THRESHOLD))
        self._dc_sum += float(np.sum(pcm, dtype=np.float64))

        if self._accumulated_frames < self.sample_rate:
            return None
        sample = LevelSample(
            frames=self._accumulated_frames,
            sample_rate=self.sample_rate,
            rms=float(np.sqrt(self._sum_squares / self._accumulated_frames)),
            peak=self._peak,
            clipped_samples=self._clipped,
            dc_offset=self._dc_sum / self._accumulated_frames,
        )
        self.history.append(sample)
        self._accumulated_frames = 0
        self._sum_squares = 0.0
        self._peak = 0.0
        self._clipped = 0
        self._dc_sum = 0.0
        return sample
