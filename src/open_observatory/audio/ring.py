"""Single-writer, multiple-reader rolling audio buffer.

Addressed by absolute stream frame index, not by time, so evidence extraction is
exact and unaffected by clock adjustment. Sized in seconds rather than bytes
(technical spec §4.4): at 384 kHz mono the arithmetic is 768 kB/s, so a
120-second ring is about 92 MB.

The implementation is a bounded deque of immutable chunks, as the audio pipeline
spec directs — no premature lock-free native extension.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class Chunk:
    first_frame: int
    pcm: np.ndarray
    monotonic_start_ns: int

    @property
    def last_frame(self) -> int:
        return self.first_frame + int(self.pcm.shape[0])


@dataclass(slots=True)
class RingStats:
    frames_written: int = 0
    frames_evicted: int = 0
    chunks: int = 0
    extractions: int = 0
    #: Requests that could not be served in full because the frames had aged out.
    extraction_misses: int = 0
    #: Requests served only partially; the caller got what still existed.
    extraction_partial: int = 0
    wraps: int = 0


class RingBuffer:
    """Rolling window of one stream's PCM, addressed by absolute frame index."""

    def __init__(self, sample_rate: int, seconds: float, *, dtype: type = np.float32) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if seconds <= 0:
            raise ValueError("seconds must be positive")
        self.sample_rate = sample_rate
        self.capacity_frames = int(sample_rate * seconds)
        self.dtype = dtype
        self._chunks: deque[Chunk] = deque()
        self._held_frames = 0
        self._lock = threading.Lock()
        self.stats = RingStats()

    # -- writer side --------------------------------------------------------

    def append(self, first_frame: int, pcm: np.ndarray, monotonic_start_ns: int) -> None:
        """Add one immutable chunk. Called only by the capture writer."""
        if pcm.ndim != 1:
            raise ValueError("ring buffer holds mono PCM only")
        # A real copy, always. `ascontiguousarray` hands back the *same* object when
        # the input already matches, so freezing its result would mark the caller's
        # array read-only and leave the ring aliasing memory it does not own — the
        # stored evidence would then change if the caller reused its buffer. At
        # 384 kHz this copy is ~150 kB per 100 ms block, which is not worth the risk
        # of getting wrong.
        block = np.array(pcm, dtype=self.dtype, copy=True, order="C")
        block.flags.writeable = False
        with self._lock:
            self._chunks.append(Chunk(first_frame, block, monotonic_start_ns))
            self._held_frames += block.shape[0]
            self.stats.frames_written += block.shape[0]
            self.stats.chunks = len(self._chunks)
            evicted = 0
            while self._held_frames > self.capacity_frames and len(self._chunks) > 1:
                dropped = self._chunks.popleft()
                count = int(dropped.pcm.shape[0])
                self._held_frames -= count
                evicted += count
            if evicted:
                self.stats.frames_evicted += evicted
                self.stats.wraps += 1
            self.stats.chunks = len(self._chunks)

    def reset(self) -> None:
        """Drop all content, e.g. when a new stream id begins."""
        with self._lock:
            self._chunks.clear()
            self._held_frames = 0
            self.stats.chunks = 0

    # -- reader side --------------------------------------------------------

    @property
    def available_range(self) -> tuple[int, int]:
        """Inclusive-exclusive frame bounds currently retained."""
        with self._lock:
            if not self._chunks:
                return (0, 0)
            return (self._chunks[0].first_frame, self._chunks[-1].last_frame)

    @property
    def held_frames(self) -> int:
        with self._lock:
            return self._held_frames

    @property
    def fill_ratio(self) -> float:
        with self._lock:
            return min(1.0, self._held_frames / self.capacity_frames)

    @property
    def held_seconds(self) -> float:
        with self._lock:
            return self._held_frames / self.sample_rate

    def extract(self, start_frame: int, end_frame: int, *, allow_partial: bool = False) -> np.ndarray | None:
        """Return frames ``[start_frame, end_frame)``.

        ``None`` means the request could not be satisfied. With
        ``allow_partial`` the overlap with what is retained is returned instead,
        which is what evidence extraction wants — a clipped clip beats no clip.
        """
        if end_frame <= start_frame:
            return np.zeros(0, dtype=self.dtype)
        with self._lock:
            self.stats.extractions += 1
            if not self._chunks:
                self.stats.extraction_misses += 1
                return None
            oldest = self._chunks[0].first_frame
            newest = self._chunks[-1].last_frame
            lo, hi = start_frame, end_frame
            if lo < oldest or hi > newest:
                if not allow_partial:
                    self.stats.extraction_misses += 1
                    return None
                lo, hi = max(lo, oldest), min(hi, newest)
                if hi <= lo:
                    self.stats.extraction_misses += 1
                    return None
                self.stats.extraction_partial += 1

            pieces: list[np.ndarray] = []
            for chunk in self._chunks:
                if chunk.last_frame <= lo:
                    continue
                if chunk.first_frame >= hi:
                    break
                take_from = max(0, lo - chunk.first_frame)
                take_to = min(int(chunk.pcm.shape[0]), hi - chunk.first_frame)
                pieces.append(chunk.pcm[take_from:take_to])
            if not pieces:
                self.stats.extraction_misses += 1
                return None
            result = np.concatenate(pieces) if len(pieces) > 1 else pieces[0].copy()
            expected = hi - lo
            if int(result.shape[0]) != expected:
                # A gap inside the retained span; report rather than silently
                # returning misaligned audio.
                self.stats.extraction_misses += 1
                return None if not allow_partial else result
            return result

    def monotonic_ns_for_frame(self, frame: int) -> int | None:
        """Best-effort monotonic timestamp of a retained frame."""
        with self._lock:
            for chunk in self._chunks:
                if chunk.first_frame <= frame < chunk.last_frame:
                    offset = frame - chunk.first_frame
                    return chunk.monotonic_start_ns + offset * 1_000_000_000 // self.sample_rate
        return None

    def snapshot(self) -> dict[str, object]:
        oldest, newest = self.available_range
        with self._lock:
            stats = self.stats
            return {
                "sample_rate": self.sample_rate,
                "capacity_seconds": round(self.capacity_frames / self.sample_rate, 2),
                "held_seconds": round(self._held_frames / self.sample_rate, 2),
                "fill_ratio": round(min(1.0, self._held_frames / self.capacity_frames), 4),
                "oldest_frame": oldest,
                "newest_frame": newest,
                "chunks": len(self._chunks),
                "frames_written": stats.frames_written,
                "frames_evicted": stats.frames_evicted,
                "extractions": stats.extractions,
                "extraction_misses": stats.extraction_misses,
                "extraction_partial": stats.extraction_partial,
                "estimated_bytes": self._held_frames * np.dtype(self.dtype).itemsize,
            }
