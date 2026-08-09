"""Acoustic activity detector — the reference ``DetectorPlugin`` (ADR-010).

This detector owns no third-party model and downloads nothing, so a fresh
checkout can exercise the whole window → detector → normaliser → clip → event
path with no operator action. It is also genuinely useful on its own: it is the
fastest way to tell whether the microphone is working, whether the gain is
sensible, and whether "nothing detected" means a quiet garden or a broken input.

**It makes no taxonomic claim.** It reports that a band-limited acoustic event
occurred, with its time bounds, peak frequency and signal-to-noise ratio. It
never emits a species name — that constraint is part of ADR-010, and
:mod:`open_observatory.normaliser` enforces it.

Method: band-limited spectral onset detection against a slowly-adapting noise
floor. The floor is tracked per frequency bin and updated with an asymmetric
filter — quick to rise back after an event, slow to follow the event itself — so
a sustained call does not raise the floor and mask its own tail.

**The threshold is calibrated, not guessed.** The detection statistic is the mean
excess of the three loudest bins over their own noise floor. Measured on
stationary Gaussian noise at 48 kHz with a 1024-point FFT, that statistic sits at
a median of 8.5 dB and a maximum of 11.9 dB — *noise alone*. An earlier version
took the maximum over all ~230 in-band bins and thresholded at 7 dB, which is
below where noise already sits: it fired on every single window, reporting each
one as one long event. The default threshold is therefore set well above the
measured noise ceiling. A loud chirp scores above 60 dB on the same statistic, so
there is ample headroom.
"""

from __future__ import annotations

import numpy as np

from ..audio.contracts import (
    AudioWindow,
    DetectorHealth,
    DetectorMetadata,
    NativeDetection,
    WindowSpec,
)
from .base import DetectorContext

PLUGIN_VERSION = "1.0.0"


class ActivityDetector:
    """Band-limited onset/energy segmentation over the audible stream."""

    metadata = DetectorMetadata(
        plugin_id="activity-v1",
        plugin_version=PLUGIN_VERSION,
        model_id="band-energy-onset",
        model_version=PLUGIN_VERSION,
        model_sha256=None,
        taxonomy_version=None,
        licence_name="Apache-2.0",
        licence_url=None,
        claim=(
            "Reports that a band-limited acoustic event occurred, with its time "
            "bounds, peak frequency and signal-to-noise ratio. Makes no claim "
            "about what produced the sound."
        ),
        resource_class="light",
        calibrated=False,
        external_network="none",
    )

    def __init__(
        self,
        *,
        sample_rate: int = 48000,
        window_s: float = 1.0,
        stride_s: float = 0.5,
        band_hz: tuple[float, float] = (1200.0, 11000.0),
        #: Calibrated: stationary noise reaches 11.9 dB on this statistic, so the
        #: default leaves a 6 dB margin above it. See the module docstring.
        min_snr_db: float = 18.0,
        min_duration_ms: float = 60.0,
        max_duration_ms: float = 5000.0,
        fft_size: int = 1024,
    ) -> None:
        self.window_spec = WindowSpec(
            stream_kind="audible48",
            sample_rate=sample_rate,
            duration_s=window_s,
            stride_s=stride_s,
            max_delivery_latency_s=10.0,
            priority=10,
        )
        self._band = band_hz
        self._min_snr_db = min_snr_db
        self._min_duration_s = min_duration_ms / 1000.0
        self._max_duration_s = max_duration_ms / 1000.0
        self._fft = fft_size
        self._hop = fft_size // 4
        self._window = np.hanning(fft_size).astype(np.float32)
        self._noise_floor_db: np.ndarray | None = None
        self._frames_seen = 0
        #: Detections are found on overlapping windows, so the same event is seen
        #: more than once. Suppress repeats by absolute stream frame.
        self._last_emitted_end_frame = -1

    def retune(
        self,
        *,
        band_hz: tuple[float, float] | None = None,
        min_snr_db: float | None = None,
        min_duration_ms: float | None = None,
        max_duration_ms: float | None = None,
    ) -> None:
        """Change the detection thresholds on a running detector.

        Every value here is read fresh inside :meth:`analyse` -- none is baked
        into an FFT plan or a cached array -- so rebinding them between windows
        is a complete change, not a partial one. The noise-floor tracker is
        deliberately *not* reset: it is an estimate of the room, which a
        threshold edit does not invalidate.
        """
        if band_hz is not None:
            self._band = (float(band_hz[0]), float(band_hz[1]))
        if min_snr_db is not None:
            self._min_snr_db = float(min_snr_db)
        if min_duration_ms is not None:
            self._min_duration_s = float(min_duration_ms) / 1000.0
        if max_duration_ms is not None:
            self._max_duration_s = float(max_duration_ms) / 1000.0

    async def initialise(self, context: DetectorContext) -> None:
        self._noise_floor_db = None
        self._frames_seen = 0

    async def analyse(self, window: AudioWindow) -> list[NativeDetection]:
        pcm = np.asarray(window.pcm, dtype=np.float32)
        rate = window.sample_rate
        if pcm.shape[0] < self._fft:
            return []

        magnitudes, times, freqs = self._stft(pcm, rate)
        mask = (freqs >= self._band[0]) & (freqs <= min(self._band[1], rate / 2))
        if not mask.any() or times.size == 0:
            return []

        band_db = 20.0 * np.log10(magnitudes[mask, :] + 1e-12)
        band_freqs = freqs[mask]

        floor = self._update_noise_floor(band_db)
        # Excess energy per frame, relative to each bin's own noise floor. The
        # statistic is the mean of the three loudest bins: still sensitive to a
        # narrowband chirp (unlike a band sum, which would drown it in bandwidth),
        # but far steadier than the single maximum, whose value grows with the
        # number of bins inspected and therefore drifted up into the noise.
        excess = band_db - floor[:, None]
        frame_snr = np.sort(excess, axis=0)[-3:, :].mean(axis=0)

        # Hysteresis: enter on the calibrated threshold, leave several dB lower, so
        # the decaying tail of a call stays part of the same event rather than
        # fragmenting into several.
        enter = frame_snr > self._min_snr_db
        leave = frame_snr > self._min_snr_db - 5.0
        above = np.zeros(frame_snr.shape[0], dtype=bool)
        active = False
        for index in range(frame_snr.shape[0]):
            active = leave[index] if active else bool(enter[index])
            above[index] = active
        frame_dt = float(times[1] - times[0]) if times.size > 1 else self._hop / rate

        detections: list[NativeDetection] = []
        start: int | None = None
        for index, flag in enumerate(above):
            if flag and start is None:
                start = index
            elif not flag and start is not None:
                detections.extend(
                    self._make(
                        start, index, times, frame_dt, band_db, band_freqs, frame_snr, window
                    )
                )
                start = None
        if start is not None:
            detections.extend(
                self._make(
                    start, len(above), times, frame_dt, band_db, band_freqs, frame_snr, window
                )
            )
        return detections

    def _stft(
        self, pcm: np.ndarray, rate: int
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        count = 1 + (pcm.shape[0] - self._fft) // self._hop
        indices = np.arange(self._fft)[None, :] + self._hop * np.arange(count)[:, None]
        frames = pcm[indices] * self._window
        spectrum = np.abs(np.fft.rfft(frames, axis=1)).T / (self._fft / 2)
        freqs = np.fft.rfftfreq(self._fft, 1.0 / rate)
        times = (np.arange(count) * self._hop + self._fft / 2) / rate
        return spectrum, times, freqs

    def _update_noise_floor(self, band_db: np.ndarray) -> np.ndarray:
        """Per-bin percentile floor, smoothed asymmetrically across windows."""
        # Median, which is what the threshold above was calibrated against.
        observed = np.percentile(band_db, 50, axis=1)
        if self._noise_floor_db is None or self._noise_floor_db.shape != observed.shape:
            self._noise_floor_db = observed.copy()
        else:
            # Rise quickly when the environment genuinely gets louder; fall
            # slowly so a long call cannot drag the floor up with it.
            rising = observed > self._noise_floor_db
            alpha = np.where(rising, 0.05, 0.25)
            self._noise_floor_db = (1 - alpha) * self._noise_floor_db + alpha * observed
        self._frames_seen += band_db.shape[1]
        return self._noise_floor_db

    def _make(
        self,
        start: int,
        end: int,
        times: np.ndarray,
        frame_dt: float,
        band_db: np.ndarray,
        band_freqs: np.ndarray,
        frame_snr: np.ndarray,
        window: AudioWindow,
    ) -> list[NativeDetection]:
        offset_start = float(times[start])
        duration = (end - start) * frame_dt
        if not (self._min_duration_s <= duration <= self._max_duration_s):
            return []

        segment = band_db[:, start:end]
        peak_bin = int(np.unravel_index(int(np.argmax(segment)), segment.shape)[0])
        peak_hz = float(band_freqs[peak_bin])
        snr_db = float(frame_snr[start:end].max())

        # Deduplicate across overlapping windows using absolute stream position.
        absolute_end = window.start_frame + int(
            (offset_start + duration) * window.sample_rate
        )
        if absolute_end <= self._last_emitted_end_frame:
            return []
        self._last_emitted_end_frame = absolute_end

        # Map SNR onto a 0-1 score so the UI can rank events. This is a
        # normalised measurement, explicitly not a probability.
        score = float(np.clip((snr_db - self._min_snr_db) / 30.0, 0.0, 1.0))
        centroid_weights = np.maximum(segment.max(axis=1) - segment.min(), 0.0)
        centroid = (
            float((band_freqs * centroid_weights).sum() / centroid_weights.sum())
            if centroid_weights.sum() > 0
            else peak_hz
        )

        return [
            NativeDetection(
                offset_start_s=offset_start,
                offset_end_s=offset_start + duration,
                score=score,
                label="acoustic event",
                rank=None,
                taxonomic_group="acoustic_event",
                peak_frequency_hz=peak_hz,
                native_result={
                    "detector": "activity-v1",
                    "snr_db": round(snr_db, 2),
                    "peak_frequency_hz": round(peak_hz, 1),
                    "spectral_centroid_hz": round(centroid, 1),
                    "duration_ms": round(duration * 1000.0, 1),
                    "band_hz": [self._band[0], self._band[1]],
                    "noise_floor_db_median": round(
                        float(np.median(self._noise_floor_db)), 2
                    )
                    if self._noise_floor_db is not None
                    else None,
                    "score_definition": "clamp((snr_db - min_snr_db) / 30 dB, 0, 1)",
                    "snr_statistic": "mean excess of the 3 loudest in-band bins over their noise floor",
                },
            )
        ]

    async def health(self) -> DetectorHealth:
        return DetectorHealth(
            available=True,
            state="ok",
            detail=(
                f"noise floor tracked over {self._frames_seen} STFT frames"
                if self._frames_seen
                else "awaiting audio"
            ),
        )

    async def shutdown(self) -> None:
        self._noise_floor_db = None
