"""Window segmentation and transient asset leasing.

Detectors do not read live audio. They receive immutable, time-addressed windows
cut to their own ``WindowSpec`` (ADR-003), which is what lets a 3-second BirdNET
window and a 1-second onset window coexist over one microphone, lets a slow
detector fall behind without stalling capture, and lets a failed job be retried
against exactly the audio that produced it.

Windows carry native frame bounds as well as their own, so a detection found in
the derived 48 kHz stream can still be turned into evidence cut from the
authoritative 384 kHz recording.

In-process, a "transient asset reference" is an object reference with a lease
rather than a file in tmpfs. That is the local analogue of the spec's
requirement that large PCM never travels through the message body; the contract
(lease, expiry, consumer count) is unchanged, so a Redis Streams deployment can
substitute a file URI without touching detectors.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import structlog

from .audio.contracts import NS_PER_S, AudioWindow, StreamKind, WindowSpec

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class SegmenterStats:
    windows_emitted: int = 0
    frames_consumed: int = 0
    resets: int = 0


class StreamSegmenter:
    """Cuts one stream into windows for a single ``WindowSpec``.

    Maintains a rolling tail of exactly the overlap the spec requires, so memory
    is bounded by the window length rather than by how far behind detectors are.
    """

    def __init__(
        self,
        spec: WindowSpec,
        *,
        stream_id: uuid.UUID,
        sample_rate: int,
        native_rate: int,
    ) -> None:
        if spec.sample_rate != sample_rate:
            raise ValueError(
                f"spec wants {spec.sample_rate} Hz but stream is {sample_rate} Hz"
            )
        self.spec = spec
        self.stream_id = stream_id
        self.sample_rate = sample_rate
        self.native_rate = native_rate
        self.stats = SegmenterStats()

        self._buffer = np.zeros(0, dtype=np.float32)
        self._buffer_first_frame = 0
        self._utc_ns_at_frame_zero: int | None = None
        self._monotonic_ns_at_frame_zero: int | None = None

    def reset(self, *, stream_id: uuid.UUID | None = None) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._utc_ns_at_frame_zero = None
        self._monotonic_ns_at_frame_zero = None
        self.stats.resets += 1
        if stream_id is not None:
            self.stream_id = stream_id

    def _native_frame(self, frame: int) -> int:
        if self.sample_rate == self.native_rate:
            return frame
        return round(frame * self.native_rate / self.sample_rate)

    def push(
        self,
        pcm: np.ndarray,
        first_frame: int,
        utc_start_ns: int,
        monotonic_start_ns: int,
        *,
        discontinuous: bool = False,
    ) -> list[AudioWindow]:
        """Add a block of derived audio and return any completed windows."""
        if discontinuous:
            # A gap means the buffered tail and the new audio are not contiguous.
            # Emitting a window across that boundary would hand a detector audio
            # it would read as one continuous event. Drop the tail instead.
            self._buffer = np.zeros(0, dtype=np.float32)
            self._utc_ns_at_frame_zero = None

        if pcm.size == 0:
            return []

        if self._buffer.size == 0:
            self._buffer_first_frame = first_frame
        if self._utc_ns_at_frame_zero is None:
            self._utc_ns_at_frame_zero = utc_start_ns - int(
                first_frame * NS_PER_S / self.sample_rate
            )
            self._monotonic_ns_at_frame_zero = monotonic_start_ns - int(
                first_frame * NS_PER_S / self.sample_rate
            )

        self._buffer = (
            np.concatenate((self._buffer, pcm))
            if self._buffer.size
            else np.asarray(pcm, dtype=np.float32)
        )

        window_frames = self.spec.frame_count
        stride = self.spec.stride_frames
        windows: list[AudioWindow] = []
        offset = 0
        now_monotonic = time.monotonic_ns()

        while offset + window_frames <= self._buffer.shape[0]:
            start_frame = self._buffer_first_frame + offset
            end_frame = start_frame + window_frames
            samples = self._buffer[offset : offset + window_frames].copy()
            samples.flags.writeable = False
            assert self._utc_ns_at_frame_zero is not None
            assert self._monotonic_ns_at_frame_zero is not None
            windows.append(
                AudioWindow(
                    window_id=uuid.uuid4(),
                    stream_id=self.stream_id,
                    stream_kind=self.spec.stream_kind,
                    sample_rate=self.sample_rate,
                    start_frame=start_frame,
                    end_frame=end_frame,
                    native_start_frame=self._native_frame(start_frame),
                    native_end_frame=self._native_frame(end_frame),
                    utc_start_ns=self._utc_ns_at_frame_zero
                    + int(start_frame * NS_PER_S / self.sample_rate),
                    utc_end_ns=self._utc_ns_at_frame_zero
                    + int(end_frame * NS_PER_S / self.sample_rate),
                    monotonic_start_ns=self._monotonic_ns_at_frame_zero
                    + int(start_frame * NS_PER_S / self.sample_rate),
                    pcm=samples,
                    spec=self.spec,
                    created_monotonic_ns=now_monotonic,
                )
            )
            offset += stride

        if offset:
            # Keep only the overlap the spec asks for.
            self._buffer = self._buffer[offset:].copy()
            self._buffer_first_frame += offset
            self.stats.frames_consumed += offset
        self.stats.windows_emitted += len(windows)
        return windows

    def snapshot(self) -> dict[str, object]:
        return {
            "stream_kind": self.spec.stream_kind,
            "sample_rate": self.sample_rate,
            "duration_s": self.spec.duration_s,
            "stride_s": self.spec.stride_s,
            "buffered_frames": int(self._buffer.shape[0]),
            "buffered_s": round(float(self._buffer.shape[0]) / self.sample_rate, 3),
            "windows_emitted": self.stats.windows_emitted,
            "resets": self.stats.resets,
        }


@dataclass(slots=True)
class Lease:
    """A claim on one transient window, released when the consumer is done."""

    window_id: uuid.UUID
    consumer: str
    expires_monotonic_ns: int

    @property
    def expired(self) -> bool:
        return time.monotonic_ns() > self.expires_monotonic_ns


@dataclass
class TransientAssetStore:
    """Tracks outstanding leases so windows expire predictably.

    Nothing here owns the PCM — the window object does — but the lease ledger is
    what makes expiry observable and gives the debug UI an honest answer to "how
    many windows are in flight, and is anything stuck?".
    """

    default_lease_s: float = 60.0
    leases: dict[tuple[uuid.UUID, str], Lease] = field(default_factory=dict)
    granted: int = 0
    released: int = 0
    expired: int = 0

    def grant(self, window_id: uuid.UUID, consumer: str, lease_s: float | None = None) -> Lease:
        lease = Lease(
            window_id=window_id,
            consumer=consumer,
            expires_monotonic_ns=time.monotonic_ns()
            + int((lease_s or self.default_lease_s) * NS_PER_S),
        )
        self.leases[(window_id, consumer)] = lease
        self.granted += 1
        return lease

    def release(self, window_id: uuid.UUID, consumer: str) -> None:
        if self.leases.pop((window_id, consumer), None) is not None:
            self.released += 1

    def sweep(self) -> int:
        stale = [key for key, lease in self.leases.items() if lease.expired]
        for key in stale:
            self.leases.pop(key, None)
            self.expired += 1
        if stale:
            log.warning("lease.expired", count=len(stale))
        return len(stale)

    def snapshot(self) -> dict[str, object]:
        return {
            "outstanding": len(self.leases),
            "granted": self.granted,
            "released": self.released,
            "expired": self.expired,
            "consumers": sorted({consumer for _, consumer in self.leases}),
        }


class WindowRouter:
    """Owns one segmenter per distinct ``WindowSpec`` on a stream kind.

    Two detectors that want the same window shape share one segmenter, so the
    STFT-sized slicing work is done once no matter how many detectors subscribe.
    """

    def __init__(self, *, native_rate: int, stream_id: uuid.UUID) -> None:
        self.native_rate = native_rate
        self.stream_id = stream_id
        self._segmenters: dict[tuple, StreamSegmenter] = {}
        self._consumers: dict[tuple, list[str]] = {}

    def register(self, spec: WindowSpec, consumer: str, *, sample_rate: int) -> None:
        key = spec.key()
        if key not in self._segmenters:
            self._segmenters[key] = StreamSegmenter(
                spec,
                stream_id=self.stream_id,
                sample_rate=sample_rate,
                native_rate=self.native_rate,
            )
            self._consumers[key] = []
        self._consumers[key].append(consumer)

    def rebind(self, stream_id: uuid.UUID, *, native_rate: int | None = None) -> None:
        self.stream_id = stream_id
        if native_rate is not None:
            self.native_rate = native_rate
        for segmenter in self._segmenters.values():
            segmenter.native_rate = self.native_rate
            segmenter.reset(stream_id=stream_id)

    def push(
        self,
        stream_kind: StreamKind,
        pcm: np.ndarray,
        first_frame: int,
        utc_start_ns: int,
        monotonic_start_ns: int,
        *,
        discontinuous: bool = False,
        on_window: Callable[[AudioWindow, list[str]], None],
    ) -> int:
        """Route one derived block to every segmenter for that stream kind."""
        emitted = 0
        for key, segmenter in self._segmenters.items():
            if key[0] != stream_kind:
                continue
            for window in segmenter.push(
                pcm,
                first_frame,
                utc_start_ns,
                monotonic_start_ns,
                discontinuous=discontinuous,
            ):
                on_window(window, self._consumers[key])
                emitted += 1
        return emitted

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {**segmenter.snapshot(), "consumers": self._consumers[key]}
            for key, segmenter in self._segmenters.items()
        ]
