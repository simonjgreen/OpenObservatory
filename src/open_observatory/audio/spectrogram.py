"""Streaming spectrogram encoder for the live view.

Computed once on the server for all viewers: a Pi can afford one STFT, not one
per browser tab, and every client then sees an identical picture — which matters
when the picture is the diagnostic.

Frequency bins are log-spaced. Linear bins waste most of the display on the
8-24 kHz range where little bird song lives, and squash the 1-5 kHz band where
almost all of it does. Each output bin takes the *maximum* of the linear bins it
covers, not the mean: a brief narrow-band chirp must stay visible rather than be
averaged into the noise floor.
"""

from __future__ import annotations

import struct
from collections import deque
from dataclasses import dataclass

import numpy as np

#: Binary live-frame types shared with the web client.
FRAME_SPECTROGRAM = 1

HEADER_STRUCT = struct.Struct("<BBHHHd")
assert HEADER_STRUCT.size == 16


def decode_header_size() -> int:
    """Byte length of the binary live-frame header, shared with the web client."""
    return HEADER_STRUCT.size


@dataclass(frozen=True, slots=True)
class SpectrogramColumns:
    """One or more freshly-computed columns, ready to push to clients."""

    channel: int
    bins: int
    columns: int
    #: UTC seconds of the *centre* of the first column.
    first_utc_s: float
    #: Shape ``(columns, bins)``, uint8, row-major.
    data: np.ndarray

    def to_binary(self) -> bytes:
        header = HEADER_STRUCT.pack(
            FRAME_SPECTROGRAM, self.channel, self.bins, self.columns, 0, self.first_utc_s
        )
        return header + self.data.tobytes()


class SpectrogramEncoder:
    """Turns a continuous PCM stream into uint8 log-frequency columns."""

    def __init__(
        self,
        *,
        channel: int,
        name: str,
        sample_rate: int,
        fft_size: int = 2048,
        hop_ms: float = 24.0,
        bins: int = 192,
        min_hz: float = 80.0,
        max_hz: float = 15000.0,
        floor_db: float = -95.0,
        ceiling_db: float = -15.0,
        history_columns: int = 2400,
    ) -> None:
        self.channel = channel
        self.name = name
        self.sample_rate = sample_rate
        nyquist = sample_rate / 2.0
        self.max_hz = min(max_hz, nyquist * 0.98)
        self.min_hz = max(min_hz, sample_rate / fft_size)
        if self.min_hz >= self.max_hz:
            self.min_hz = self.max_hz / 100.0
        self.fft_size = fft_size
        self.hop_frames = max(1, int(round(sample_rate * hop_ms / 1000.0)))
        self.hop_s = self.hop_frames / sample_rate
        self.bins = bins
        self.floor_db = floor_db
        self.ceiling_db = ceiling_db

        self._window = np.hanning(fft_size).astype(np.float32)
        # Normalise so a full-scale sine reads near 0 dBFS regardless of FFT size.
        self._window_gain = float(np.sum(self._window)) / 2.0

        # A column must summarise its whole hop, not just one FFT window at the
        # start of it. On the ultrasonic channel the hop (9216 frames at 384 kHz)
        # is more than twice the FFT (4096), so a single window per column looked
        # at 4096 frames and ignored the other 5120 — 55% of the audio, in which a
        # 4 ms bat pulse could sit and be entirely invisible. Where the hop is
        # wider than the window, several sub-windows are taken across it and the
        # per-bin maximum kept, which covers the gap and preserves transients.
        self._column_span = max(self.hop_frames, fft_size)
        self._sub_offsets = list(range(0, self._column_span - fft_size + 1, max(1, fft_size // 2)))
        if self._sub_offsets[-1] + fft_size < self._column_span:
            self._sub_offsets.append(self._column_span - fft_size)
        self._buffer = np.zeros(0, dtype=np.float32)
        #: Frame index in the stream of ``_buffer[0]``.
        self._buffer_first_frame = 0
        self._utc_ns_at_frame_zero: int | None = None

        self._bin_edges = self._compute_bin_edges()
        self.centre_frequencies = self._compute_bin_centres()
        self.history: deque[np.ndarray] = deque(maxlen=history_columns)
        self.history_first_utc_s: float | None = None
        self.columns_emitted = 0

    # ------------------------------------------------------------------

    def _compute_bin_edges(self) -> list[tuple[int, int]]:
        """Map each output bin to a half-open slice of rfft bins."""
        fft_freqs = np.fft.rfftfreq(self.fft_size, 1.0 / self.sample_rate)
        edges = np.geomspace(self.min_hz, self.max_hz, self.bins + 1)
        mapping: list[tuple[int, int]] = []
        for index in range(self.bins):
            low = int(np.searchsorted(fft_freqs, edges[index], side="left"))
            high = int(np.searchsorted(fft_freqs, edges[index + 1], side="left"))
            if high <= low:
                # Below the FFT's resolution: reuse the nearest single bin so the
                # display stays continuous instead of showing a dead stripe.
                high = min(low + 1, fft_freqs.shape[0])
                low = max(0, high - 1)
            mapping.append((low, high))
        return mapping

    def _compute_bin_centres(self) -> list[float]:
        edges = np.geomspace(self.min_hz, self.max_hz, self.bins + 1)
        return [float(np.sqrt(edges[i] * edges[i + 1])) for i in range(self.bins)]

    def describe(self, *, include_frequencies: bool = True) -> dict[str, object]:
        """Channel descriptor.

        ``include_frequencies`` exists because the bin centre table is ~2.5 kB
        across both channels and never changes, yet it was being re-sent inside
        every two-second status frame — about a quarter of that frame for nothing.
        The client keeps the copy it got in ``hello``.
        """
        payload: dict[str, object] = {
            "channel": self.channel,
            "name": self.name,
            "sample_rate": self.sample_rate,
            "bins": self.bins,
            "min_hz": round(self.min_hz, 2),
            "max_hz": round(self.max_hz, 2),
            "hop_s": round(self.hop_s, 6),
            "fft_size": self.fft_size,
            "floor_db": self.floor_db,
            "ceiling_db": self.ceiling_db,
            "columns_emitted": self.columns_emitted,
            "history_columns": len(self.history),
            "column_span_frames": self._column_span,
            "sub_windows_per_column": len(self._sub_offsets),
        }
        if include_frequencies:
            payload["centre_frequencies"] = [round(f, 1) for f in self.centre_frequencies]
        return payload

    # ------------------------------------------------------------------

    def push(self, pcm: np.ndarray, first_frame: int, utc_start_ns: int) -> SpectrogramColumns | None:
        """Feed a block; returns any columns that became complete."""
        if pcm.size == 0:
            return None
        if self._utc_ns_at_frame_zero is None or self._buffer.size == 0:
            # Anchor stream-frame → UTC using this block, then keep deriving
            # column times from frame indices so they cannot drift apart.
            self._utc_ns_at_frame_zero = utc_start_ns - int(
                first_frame * 1_000_000_000 / self.sample_rate
            )
            if self._buffer.size == 0:
                self._buffer_first_frame = first_frame

        self._buffer = (
            np.concatenate((self._buffer, pcm)) if self._buffer.size else np.asarray(pcm, np.float32)
        )

        if self._buffer.shape[0] < self._column_span:
            return None

        columns: list[np.ndarray] = []
        first_column_frame = self._buffer_first_frame
        offset = 0
        while offset + self._column_span <= self._buffer.shape[0]:
            columns.append(self._column(offset))
            offset += self.hop_frames

        if not columns:
            return None

        # Advance by what was actually consumed. `offset` can overshoot the buffer
        # when the hop exceeds the window, and advancing the frame index by the
        # overshoot silently claimed frames that were never in the buffer — which
        # showed up as ~4% too many columns and overlapping column timestamps on
        # the ultrasonic channel.
        consumed = min(offset, int(self._buffer.shape[0]))
        self._buffer = self._buffer[consumed:].copy()
        self._buffer_first_frame += consumed

        centre_frame = first_column_frame + self.fft_size / 2.0
        first_utc_s = (
            self._utc_ns_at_frame_zero + centre_frame * 1_000_000_000 / self.sample_rate
        ) / 1_000_000_000

        stacked = np.stack(columns)
        for column in columns:
            if self.history_first_utc_s is None or len(self.history) == 0:
                self.history_first_utc_s = first_utc_s
            self.history.append(column)
        # Once the ring is full, the oldest retained column is no longer the
        # first we ever emitted; recompute what the history actually starts at.
        self.columns_emitted += len(columns)
        last_utc_s = first_utc_s + (len(columns) - 1) * self.hop_s
        self.history_first_utc_s = last_utc_s - (len(self.history) - 1) * self.hop_s

        return SpectrogramColumns(
            channel=self.channel,
            bins=self.bins,
            columns=len(columns),
            first_utc_s=first_utc_s,
            data=stacked,
        )

    def _column(self, offset: int) -> np.ndarray:
        """One display column, covering ``_column_span`` frames from ``offset``."""
        peak: np.ndarray | None = None
        for sub in self._sub_offsets:
            begin = offset + sub
            segment = self._buffer[begin : begin + self.fft_size]
            if segment.shape[0] < self.fft_size:
                break
            spectrum = np.abs(np.fft.rfft(segment * self._window)) / self._window_gain
            peak = spectrum if peak is None else np.maximum(peak, spectrum)
        if peak is None:
            return np.zeros(self.bins, dtype=np.uint8)

        power_db = 20.0 * np.log10(peak + 1e-12)
        out = np.empty(self.bins, dtype=np.float32)
        for index, (low, high) in enumerate(self._bin_edges):
            out[index] = power_db[low:high].max()
        span = max(1e-6, self.ceiling_db - self.floor_db)
        scaled = (out - self.floor_db) / span
        return (np.clip(scaled, 0.0, 1.0) * 255.0).astype(np.uint8)

    # ------------------------------------------------------------------

    def history_frame(self, max_columns: int | None = None) -> SpectrogramColumns | None:
        """Backfill for a newly-connected client."""
        if not self.history:
            return None
        columns = list(self.history)
        if max_columns is not None and len(columns) > max_columns:
            columns = columns[-max_columns:]
        first_utc = (self.history_first_utc_s or 0.0) + (len(self.history) - len(columns)) * self.hop_s
        return SpectrogramColumns(
            channel=self.channel,
            bins=self.bins,
            columns=len(columns),
            first_utc_s=first_utc,
            data=np.stack(columns),
        )

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._utc_ns_at_frame_zero = None
        self._buffer_first_frame = 0
