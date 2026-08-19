"""Low-latency live audio fan-out for the "GO LIVE" button.

Chunked WAV over HTTP is the easy way to do this and the wrong one: browsers
buffer seconds of it before they start, which is useless when you are trying to
hear what the spectrogram is showing *now*.

Instead the audible stream is broadcast as raw little-endian int16 mono over a
WebSocket and reassembled in an ``AudioWorklet`` with a deliberately small jitter
buffer. End-to-end latency is then dominated by that buffer, which the client
chooses and reports, rather than by an opaque media pipeline.

Each listener gets a bounded queue. A listener that cannot keep up loses audio and
is told how much — the alternative is unbounded memory growth on the Pi, or
back-pressure reaching the capture loop, and capture always wins.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field

import numpy as np
import structlog

log = structlog.get_logger(__name__)


@dataclass
class Listener:
    listener_id: int
    label: str
    queue: asyncio.Queue[bytes | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=48)
    )
    chunks_sent: int = 0
    chunks_dropped: int = 0
    bytes_sent: int = 0

    def offer(self, payload: bytes) -> None:
        try:
            self.queue.put_nowait(payload)
            self.chunks_sent += 1
            self.bytes_sent += len(payload)
        except asyncio.QueueFull:
            # Drop the oldest so the listener catches up to live rather than
            # falling further behind on stale audio.
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(payload)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
            self.chunks_dropped += 1

    def drain(self) -> int:
        """Discard anything already queued. Used before the send loop starts."""
        discarded = 0
        while True:
            try:
                self.queue.get_nowait()
                discarded += 1
            except asyncio.QueueEmpty:
                return discarded

    def close(self) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(None)


class LiveAudioBroadcaster:
    """Publishes int16 PCM chunks of the audible stream to WebSocket listeners."""

    def __init__(self, *, sample_rate: int = 48000, chunk_ms: float = 40.0) -> None:
        self.sample_rate = sample_rate
        self.chunk_ms = chunk_ms
        self.chunk_frames = max(1, round(sample_rate * chunk_ms / 1000.0))
        self._listeners: dict[int, Listener] = {}
        self._next_id = 1
        self._pending = np.zeros(0, dtype=np.float32)
        self.chunks_published = 0
        #: Peak level of the most recent chunk, so the UI can show a meter even
        #: before the browser has decoded any audio.
        self.last_peak = 0.0

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    def add_listener(self, label: str = "browser") -> Listener:
        listener = Listener(listener_id=self._next_id, label=label)
        self._next_id += 1
        self._listeners[listener.listener_id] = listener
        log.info("live_audio.listener_joined", listener=listener.listener_id, total=len(self._listeners))
        return listener

    def remove_listener(self, listener: Listener) -> None:
        self._listeners.pop(listener.listener_id, None)
        listener.close()
        log.info(
            "live_audio.listener_left",
            listener=listener.listener_id,
            sent=listener.chunks_sent,
            dropped=listener.chunks_dropped,
            total=len(self._listeners),
        )

    def reconfigure(self, sample_rate: int) -> None:
        if sample_rate == self.sample_rate:
            return
        self.sample_rate = sample_rate
        self.chunk_frames = max(1, round(sample_rate * self.chunk_ms / 1000.0))
        self._pending = np.zeros(0, dtype=np.float32)
        log.info("live_audio.reconfigured", sample_rate=sample_rate)

    def publish(self, pcm: np.ndarray) -> None:
        """Feed audible PCM. No-op with no listeners, so idle cost is zero."""
        if not self._listeners or pcm.size == 0:
            if not self._listeners and self._pending.size:
                self._pending = np.zeros(0, dtype=np.float32)
            return

        self._pending = (
            np.concatenate((self._pending, pcm)) if self._pending.size else np.asarray(pcm, np.float32)
        )
        while self._pending.shape[0] >= self.chunk_frames:
            block = self._pending[: self.chunk_frames]
            self._pending = self._pending[self.chunk_frames :]
            self.last_peak = float(np.abs(block).max())
            payload = (np.clip(block, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            self.chunks_published += 1
            for listener in list(self._listeners.values()):
                listener.offer(payload)

    def snapshot(self) -> dict[str, object]:
        return {
            "sample_rate": self.sample_rate,
            "chunk_ms": self.chunk_ms,
            "chunk_frames": self.chunk_frames,
            "listeners": len(self._listeners),
            "chunks_published": self.chunks_published,
            "last_peak": round(self.last_peak, 5),
            "per_listener": [
                {
                    "id": listener.listener_id,
                    "label": listener.label,
                    "queued": listener.queue.qsize(),
                    "chunks_sent": listener.chunks_sent,
                    "chunks_dropped": listener.chunks_dropped,
                    "bytes_sent": listener.bytes_sent,
                }
                for listener in self._listeners.values()
            ],
        }

    def close(self) -> None:
        for listener in list(self._listeners.values()):
            self.remove_listener(listener)
