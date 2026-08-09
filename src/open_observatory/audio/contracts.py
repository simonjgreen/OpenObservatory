"""Transport-neutral audio contracts.

These types are the boundary between capture and everything downstream. They are
deliberately plain: a capture block is an immutable record of *what was read*,
*when it was read* and *where it sits in the stream*. Monotonic time is
authoritative for ordering and duration; UTC is derived for presentation and
correlation only, so an NTP step can never reorder frames (technical spec §4.3).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

import numpy as np

NS_PER_S = 1_000_000_000

StreamKind = Literal["native", "audible48"]


class SourceKind(StrEnum):
    ALSA = "alsa"
    REPLAY = "replay"
    SYNTHETIC = "synthetic"


class DiscontinuityReason(StrEnum):
    OVERRUN = "overrun"
    #: Frames the stream clock says are permanently gone, without ALSA ever
    #: raising EPIPE. Distinct from ``OVERRUN`` on purpose: labelling an
    #: unreported deficit "overrun" was how the station came to claim ring
    #: overflows the driver had never seen (ADR-039).
    FRAME_DEFICIT = "frame_deficit"
    SHORT_READ = "short_read"
    DEVICE_RESET = "device_reset"
    TIMESTAMP_IMPLAUSIBLE = "timestamp_implausible"
    STREAM_START = "stream_start"
    REPLAY_WRAP = "replay_wrap"


@dataclass(frozen=True, slots=True)
class ClockCorrelation:
    """A single simultaneous read of the monotonic and wall clocks."""

    monotonic_ns: int
    utc_ns: int

    @classmethod
    def sample(cls) -> ClockCorrelation:
        # Read monotonic either side of the wall clock and take the midpoint, so
        # the pairing error is bounded by the span of the two monotonic reads.
        before = time.monotonic_ns()
        utc = time.time_ns()
        after = time.monotonic_ns()
        return cls(monotonic_ns=(before + after) // 2, utc_ns=utc)

    def utc_ns_for(self, monotonic_ns: int) -> int:
        return self.utc_ns + (monotonic_ns - self.monotonic_ns)

    @property
    def skew_ns(self) -> int:
        return self.utc_ns - self.monotonic_ns


@dataclass(frozen=True, slots=True)
class StreamClock:
    """Maps a stream frame index to wall and monotonic time, without drift.

    Why this exists rather than using each block's measured timestamp: the
    resampler emits ragged chunk sizes, so at any instant the number of derived
    frames produced lags the exact rate ratio by a bounded but *varying* amount
    (measured at 112-924 frames for 384 kHz to 48 kHz with libsoxr). Timestamping
    derived audio with the native block's clock therefore wanders by up to ~19 ms.

    Anchoring on the stream's start instead makes position-to-time exact for every
    stream at every rate, and it stays correct across gaps: a lost block advances
    the frame index, so the mapping accounts for the missing audio automatically.

    Each block's own measured ``monotonic_start_ns`` is still the authority for
    gap detection and lag — this type answers "when did frame N happen?", not
    "when did we read it?".
    """

    utc_ns_at_frame_zero: int
    monotonic_ns_at_frame_zero: int

    @classmethod
    def from_stream(cls, info: StreamInfo) -> StreamClock:
        return cls(
            utc_ns_at_frame_zero=info.clock.utc_ns_for(info.started_monotonic_ns),
            monotonic_ns_at_frame_zero=info.started_monotonic_ns,
        )

    def utc_ns(self, frame: int, sample_rate: int) -> int:
        return self.utc_ns_at_frame_zero + frame * NS_PER_S // sample_rate

    def monotonic_ns(self, frame: int, sample_rate: int) -> int:
        return self.monotonic_ns_at_frame_zero + frame * NS_PER_S // sample_rate


@dataclass(frozen=True, slots=True)
class AudioFormat:
    sample_rate: int
    channels: int
    #: ALSA-style name, e.g. ``S16_LE``. Governs the on-wire capture format only;
    #: PCM is normalised to float32 in [-1, 1) before leaving the source.
    sample_format: str

    @property
    def bytes_per_frame(self) -> int:
        width = {"S16_LE": 2, "S24_3LE": 3, "S32_LE": 4, "FLOAT_LE": 4}.get(
            self.sample_format, 2
        )
        return width * self.channels

    def frames_for_ms(self, milliseconds: float) -> int:
        return max(1, int(round(self.sample_rate * milliseconds / 1000.0)))


@dataclass(frozen=True, slots=True)
class StreamInfo:
    """Identity and negotiated parameters of one continuous capture stream."""

    stream_id: uuid.UUID
    source_kind: SourceKind
    device_key: str
    device_label: str
    fmt: AudioFormat
    started_monotonic_ns: int
    clock: ClockCorrelation
    #: Free-form provenance: ALSA hw id, replay file, synthetic scene name.
    detail: dict[str, object] = field(default_factory=dict)

    @property
    def started_utc(self) -> datetime:
        return datetime.fromtimestamp(
            self.clock.utc_ns_for(self.started_monotonic_ns) / NS_PER_S, tz=UTC
        )


@dataclass(frozen=True, slots=True)
class CaptureBlock:
    """One immutable read from the authoritative source.

    ``pcm`` is mono float32 in [-1, 1). ``first_frame`` counts frames from the
    start of the stream, so it — not any timestamp — is what downstream
    components use to address audio.
    """

    stream_id: uuid.UUID
    sequence: int
    first_frame: int
    sample_rate: int
    pcm: np.ndarray
    monotonic_start_ns: int
    clock: ClockCorrelation
    discontinuity: DiscontinuityReason | None = None
    #: Frames the source believes were lost immediately before this block.
    missing_frames: int = 0
    #: Where the loss actually happened, when that is not this block's own
    #: boundary. A source that confirms a loss before reporting it (see
    #: :class:`~open_observatory.audio.alsa_source.AlsaSource`) publishes the
    #: gap a few blocks after the event, and the record must still say where.
    discontinuity_at_frame: int | None = None

    @property
    def gap_at_frame(self) -> int:
        """The frame a reported discontinuity belongs to."""
        return self.first_frame if self.discontinuity_at_frame is None else self.discontinuity_at_frame

    @property
    def frame_count(self) -> int:
        return int(self.pcm.shape[0])

    @property
    def last_frame(self) -> int:
        """Exclusive upper frame bound."""
        return self.first_frame + self.frame_count

    @property
    def duration_ns(self) -> int:
        return self.frame_count * NS_PER_S // self.sample_rate

    @property
    def monotonic_end_ns(self) -> int:
        return self.monotonic_start_ns + self.duration_ns

    @property
    def utc_start_ns(self) -> int:
        return self.clock.utc_ns_for(self.monotonic_start_ns)

    @property
    def utc_end_ns(self) -> int:
        return self.clock.utc_ns_for(self.monotonic_end_ns)


@dataclass(frozen=True, slots=True)
class WindowSpec:
    """What a detector needs from the pipeline (technical spec §4.6)."""

    stream_kind: StreamKind
    sample_rate: int
    duration_s: float
    stride_s: float
    #: Windows older than this by the time they are dequeued are dropped rather
    #: than analysed, so a slow detector degrades as lag, not as unbounded queue.
    max_delivery_latency_s: float = 30.0
    priority: int = 100

    @property
    def frame_count(self) -> int:
        return int(round(self.duration_s * self.sample_rate))

    @property
    def stride_frames(self) -> int:
        return max(1, int(round(self.stride_s * self.sample_rate)))

    @property
    def overlap_s(self) -> float:
        return max(0.0, self.duration_s - self.stride_s)

    def key(self) -> tuple[str, int, float, float]:
        return (self.stream_kind, self.sample_rate, self.duration_s, self.stride_s)


@dataclass(frozen=True, slots=True)
class AudioWindow:
    """An immutable, time-addressed slice of a derived or native stream."""

    window_id: uuid.UUID
    stream_id: uuid.UUID
    stream_kind: StreamKind
    sample_rate: int
    #: Frame bounds within the stream this window was cut from.
    start_frame: int
    end_frame: int
    #: Frame bounds within the *native* stream, for evidence extraction.
    native_start_frame: int
    native_end_frame: int
    utc_start_ns: int
    utc_end_ns: int
    monotonic_start_ns: int
    pcm: np.ndarray
    spec: WindowSpec
    created_monotonic_ns: int

    @property
    def duration_s(self) -> float:
        return (self.end_frame - self.start_frame) / self.sample_rate

    def age_s(self) -> float:
        return (time.monotonic_ns() - self.created_monotonic_ns) / NS_PER_S


@dataclass(frozen=True, slots=True)
class NativeDetection:
    """A detector's own output, before normalisation.

    ``native_result`` is preserved verbatim so the product never loses what the
    model actually said (technical spec §5).
    """

    #: Offsets within the analysed window, not within the stream.
    offset_start_s: float
    offset_end_s: float
    score: float
    label: str | None = None
    common_name: str | None = None
    scientific_name: str | None = None
    rank: str | None = None
    taxonomic_group: str = "unknown"
    calibrated_probability: float | None = None
    peak_frequency_hz: float | None = None
    native_result: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DetectorMetadata:
    plugin_id: str
    plugin_version: str
    model_id: str
    model_version: str
    model_sha256: str | None
    taxonomy_version: str | None
    licence_name: str
    licence_url: str | None
    #: Human-readable statement of what the detector does and does not claim.
    claim: str
    resource_class: Literal["light", "moderate", "heavy"] = "light"
    calibrated: bool = False
    external_network: Literal["none", "optional", "required"] = "none"


@dataclass(frozen=True, slots=True)
class DetectorHealth:
    available: bool
    state: Literal["ok", "degraded", "unavailable", "error"]
    detail: str = ""
    windows_analysed: int = 0
    windows_dropped: int = 0
    failures: int = 0
    last_runtime_ms: float | None = None
    p95_runtime_ms: float | None = None
    lag_s: float | None = None


@runtime_checkable
class AudioSource(Protocol):
    """Anything that can produce capture blocks with honest timing."""

    info: StreamInfo

    async def open(self) -> StreamInfo: ...

    async def read(self) -> CaptureBlock | None:
        """Return the next block, or ``None`` when the stream has ended."""

    async def close(self) -> None: ...
