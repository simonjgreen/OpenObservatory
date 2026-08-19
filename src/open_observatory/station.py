"""The station runtime: the single process that owns the microphone.

ADR-008 runs capture, derivation, segmentation, detection, normalisation,
clip writing and the API in one process on the Pi. The *boundaries* are still
real — components talk through the event bus, window references and the
normaliser, never by reaching into each other — so the Compose topology in the
technical spec remains a deployment change rather than a rewrite.

Ordering of concerns in the capture loop is deliberate and follows the spec's
"capture and resampling always win":

1. read a block and account for its frames;
2. append to the native ring (evidence must be extractable even if everything
   downstream is broken);
3. derive the audible stream;
4. update telemetry and the live view;
5. hand windows to detectors, dropping rather than blocking when they are behind.

Nothing in steps 1-4 awaits detector work, and every queue between here and a
detector is bounded.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import structlog
from sqlalchemy import func

from . import models as model_registry
from . import tuning
from .audio.contracts import (
    NS_PER_S,
    AudioWindow,
    CaptureBlock,
    ClockCorrelation,
    DetectorMetadata,
    DiscontinuityReason,
    NativeDetection,
    SourceKind,
    StreamClock,
    StreamInfo,
)
from .audio.heterodyne_stream import StreamingHeterodyne
from .audio.levels import LevelAggregator
from .audio.probe import enumerate_capture_devices, find_device
from .audio.resample import AudibleResampler
from .audio.ring import RingBuffer
from .audio.spectrogram import SpectrogramEncoder
from .clips import ClipManager
from .config import Settings
from .db import models as orm
from .db.session import session_scope
from .detectors.activity import ActivityDetector
from .detectors.base import DetectorContext, DetectorWorker
from .detectors.birdnet import BirdNetDetector
from .detectors.ultrasonic import UltrasonicDetector
from .events import EventBus, EventType
from .live import LiveAudioBroadcaster
from .normaliser import CanonicalDetection, ClaimViolation, Normaliser
from .pause import PauseController
from .retention import RetentionSweeper
from .schedule import NightSchedule
from .segmenter import TransientAssetStore, WindowRouter

log = structlog.get_logger(__name__)

SPECTROGRAM_AUDIBLE = 0
SPECTROGRAM_ULTRASONIC = 1


class _Unapplied:
    """Placeholder recorded in ``applied_site`` for a live setting that was
    saved while the object meant to receive it did not exist.

    It compares equal to nothing, so the setting reports as pending until a
    restart builds the component and binds the saved value. Recording the
    *old* value would be wrong (there was no old value in use) and recording
    the new one would be a claim that something is using it."""

    def __eq__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return id(self)

    def __repr__(self) -> str:
        return "<not applied>"


UNAPPLIED = _Unapplied()

#: A housekeeping tick that blocks the event loop for longer than this is
#: reported at warning level. One capture block is 100 ms, so anything at or
#: above this delays the next read by an appreciable fraction of a block.
_HOUSEKEEPING_SLOW_S = 0.05

#: How far the wall clock must move relative to the monotonic clock before the
#: stream clock is re-anchored (ADR-063). One second is far above any legitimate
#: NTP *slew* -- capped at 500 ppm, so ~5 ms across a 10 s housekeeping tick --
#: and far below the 106 s step that made this necessary. A step smaller than
#: this is not worth the discontinuity in the timeline that correcting it
#: introduces.
_CLOCK_STEP_THRESHOLD_NS = 1_000_000_000

#: Event-loop lag above this is logged individually. Half a capture block.
_LOOP_LAG_SLOW_S = 0.05


@dataclass(slots=True)
class CaptureCounters:
    """Process-lifetime counters.

    Deliberately **not** reset when a stream reopens -- these answer "how has
    this process behaved since it started", which is what the CPU-budget and
    open-failure checks want. Anything that is written into an `audio_stream`
    row (``frame_count``, ``discontinuity_count``) must come from the
    *stream*-scoped counters on :class:`Station` instead
    (``_stream_frames``/``_stream_discontinuities``), or a process that reopens
    the device mid-life writes a later stream's row with an earlier stream's
    frames added in -- exactly the kind of arithmetic that makes coverage lie.
    """

    blocks: int = 0
    frames: int = 0
    discontinuities: int = 0
    #: `discontinuities` split by whether audio was actually lost. An ALSA
    #: overrun and a lost tenth of a second are different events with the same
    #: log line, and a triage that counts `capture.gap` lines cannot tell them
    #: apart. Neither is harmless — a zero-loss overrun still means the ring came
    #: within one period of underflowing — but only one of them costs recording.
    gaps_with_loss: int = 0
    gaps_without_loss: int = 0
    estimated_missing_frames: int = 0
    stream_restarts: int = 0
    open_failures: int = 0
    last_block_monotonic_ns: int = 0
    #: How many times the stream clock has been re-anchored onto a stepped
    #: system wall clock, and the size of the last step in seconds (ADR-063).
    #: Non-zero means some timestamps this process wrote are wrong by roughly
    #: `clock_last_step_s`, and which ones is decided by whether they were
    #: written before or after `station.clock_reanchored` in the log.
    clock_reanchors: int = 0
    clock_last_step_s: float = 0.0
    #: Wall time spent inside the per-block hot path, for the CPU budget check.
    hot_path_seconds: float = 0.0
    #: Last housekeeping tick's synchronous, event-loop-blocking cost, and the
    #: overshoot of its own 10 s sleep. Capture runs on a private executor but
    #: the loop still has to issue and await each read, so anything that blocks
    #: the loop delays capture by the same amount.
    housekeeping_blocking_s: float = 0.0
    loop_lag_s: float = 0.0
    #: Worst event-loop lag seen by the watchdog, and how many times it went
    #: past the reporting threshold.
    loop_lag_max_s: float = 0.0
    loop_lag_events: int = 0


@dataclass
class DetectionRecord:
    """A normalised detection plus whatever evidence was produced for it."""

    detection: CanonicalDetection
    media: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = self.detection.to_dict()
        payload["media"] = self.media
        return payload


class Station:
    """Owns the capture device and everything derived from it."""

    def __init__(self, settings: Settings, *, bus: EventBus | None = None) -> None:
        self.settings = settings
        self.bus = bus or EventBus(history=800)
        self.counters = CaptureCounters()
        #: Per-contributor cost of the last `status_snapshot()`, in seconds.
        self._snapshot_phase_s: dict[str, float] = {}
        self.started_monotonic_ns = time.monotonic_ns()

        self.station_id: uuid.UUID | None = None
        #: Whether the *previous* run of this process ended without a graceful
        #: close, as evidenced by `audio_stream` rows left open (ADR-065). Set
        #: by `_close_orphaned_streams` at startup, reported by
        #: `/api/v1/health` as a note, and the reason a soak in progress at
        #: that moment must be treated as void.
        self.unclean_restart: bool = False
        self.recovered_stream_count: int = 0
        self.recovered_stream_end_utc: datetime | None = None
        self.stream: StreamInfo | None = None
        self.clock: StreamClock | None = None
        self.source: Any = None
        self.capture_state: str = "starting"
        self.capture_detail: str = ""

        #: Scoped to the *current* stream, reset in `_on_stream_open`. What gets
        #: written into `audio_stream.frame_count`/`discontinuity_count` -- see
        #: `CaptureCounters` for why the process-lifetime counters must not be
        #: used for that.
        self._stream_frames: int = 0
        self._stream_discontinuities: int = 0
        #: UTC time of the most recently delivered block for the current stream,
        #: from the block's own frame-derived timestamp -- not `datetime.now()` --
        #: so it reflects when audio was actually flowing, not when this line
        #: happened to run. Persisted periodically as `last_frame_at_utc` so a
        #: crashed process's row can be closed honestly (ADR-024).
        self._stream_last_frame_utc: datetime | None = None

        self.native_ring: RingBuffer | None = None
        self.audible_ring: RingBuffer | None = None
        self.resampler: AudibleResampler | None = None
        self.native_levels: LevelAggregator | None = None
        self.audible_levels: LevelAggregator | None = None
        self.spectrograms: dict[int, SpectrogramEncoder] = {}
        #: How many browsers are watching the live view, supplied by the API
        #: layer (ADR-040). Defaults to nobody -- see `set_spectrogram_consumer_count`.
        self._spectrogram_consumers: Callable[[], int] = lambda: 0
        #: Whether the last block encoded, so the gate closing can be seen as an
        #: edge and the now-unservable history discarded exactly once.
        self._spectrograms_encoding = False
        self.router: WindowRouter | None = None
        self.leases = TransientAssetStore()
        self.normaliser = Normaliser()
        self.live_audio = LiveAudioBroadcaster(sample_rate=settings.audible_sample_rate)
        # A second broadcaster, not a second class: same chunk framing, same
        # int16 LE encoding, same bounded per-listener queue — the client's
        # existing jitter buffer keeps working unchanged for either channel.
        # Fed only from `_handle_block` when it has listeners, so an idle
        # ultrasonic channel costs nothing beyond this object's existence.
        self.live_audio_ultrasonic = LiveAudioBroadcaster(sample_rate=settings.audible_sample_rate)
        self.heterodyne: StreamingHeterodyne | None = None
        #: Why `heterodyne` is None, for the API to explain to a client that
        #: connects to the ultrasonic channel before/without a usable native
        #: rate (e.g. the device negotiated a rate live monitoring can't
        #: evenly decimate).
        self.heterodyne_unavailable_reason: str = "capture not yet open"
        self.clips = ClipManager(
            clip_dir=settings.clip_dir,
            pre_roll_s=settings.clip_pre_roll_s,
            post_roll_s=settings.clip_post_roll_s,
            max_duration_s=settings.clip_max_s,
            min_score=settings.clip_min_score,
            retention_days=settings.clip_retention_days,
            clip_plugins=settings.clip_plugins,
            clip_human_audio=settings.clip_human_audio,
            max_per_minute=settings.clip_max_per_minute,
            max_total_bytes=int(settings.clip_max_total_gb * 1024**3),
            min_free_bytes=int(settings.clip_min_free_gb * 1024**3),
            ultrasonic_audible_method=settings.ultrasonic_audible_method,
            ultrasonic_time_expansion_factor=settings.ultrasonic_time_expansion_factor,
            ultrasonic_target_hz=settings.ultrasonic_target_hz,
            ultrasonic_highpass_hz=settings.ultrasonic_highpass_hz,
            ultrasonic_heterodyne_bandwidth_hz=settings.ultrasonic_heterodyne_bandwidth_hz,
            ultrasonic_audible_max_s=settings.ultrasonic_audible_max_s,
            ultrasonic_audible_min_peak_hz=settings.ultrasonic_audible_min_peak_hz,
        )
        #: NVR-style tiered aging (ADR-026), separate from `self.clips` above:
        #: this decides *which* clips a detection keeps as it ages, driven by
        #: the database (kind, species, score), not a filesystem walk.
        self.retention = RetentionSweeper(
            clip_dir=settings.clip_dir,
            session_factory=session_scope,
            native_days=settings.retention_native_days,
            audible_only_days=settings.retention_audible_only_days,
            watermark_ratio=settings.retention_watermark_ratio,
            batch_size=settings.retention_batch_size,
            batch_budget_s=settings.retention_batch_budget_s,
        )

        #: The operator's privacy pause (ADR-055). Owned here because every
        #: gate that consults it is here: the live-audio publish in
        #: `_handle_block`, and the detection path in `_on_detections`. It is
        #: constructed before capture starts and its deadline is re-adopted from
        #: the database in `start()`, so a station that reboots mid-pause comes
        #: back paused rather than recording.
        self.pause = PauseController(
            session_factory=session_scope,
            # Read through lambdas rather than copied: the timezone is a live
            # setting, and reading a stale copy would resolve "until midnight"
            # against the zone the process started in.
            timezone_provider=lambda: self.settings.timezone,
            station_id_provider=lambda: self.station_id,
        )

        #: What the running pipeline is actually using, recorded as each part
        #: of it is built (`_record_applied`) and updated as live settings are
        #: pushed into it (`apply_tuning`). The settings object can change under
        #: a live process -- the UI settings page persists and applies edits --
        #: but a restart-pinned value is deliberately *not* re-injected: swapping
        #: coordinates under a running BirdNET range filter would change what
        #: "plausible" means mid-stream, and re-negotiating capture geometry
        #: would cost audio. This snapshot is what lets the API report "saved,
        #: in force after restart" honestly instead of pretending. It covers
        #: live-tier settings too, so a live edit that could not reach its
        #: object is reported as pending rather than as done (ADR-048).
        self.applied_site: dict[str, Any] | None = None
        self.workers: list[DetectorWorker] = []
        self.recent_detections: list[DetectionRecord] = []
        self._detector_rows: dict[str, uuid.UUID] = {}
        self._device_row_id: uuid.UUID | None = None

        self._capture_task: asyncio.Task[None] | None = None
        self._persist_task: asyncio.Task[None] | None = None
        self._housekeeping_task: asyncio.Task[None] | None = None
        self._loop_lag_task: asyncio.Task[None] | None = None
        self._persist_queue: asyncio.Queue[DetectionRecord | None] = asyncio.Queue(maxsize=512)
        self._running = False
        self.persist_dropped = 0
        self.persist_written = 0
        self.persist_failures = 0
        #: Evidence extraction is slow (disk I/O plus ultrasound rendering), so it
        #: runs off the detector's task. Bounded, with an explicit drop policy.
        #:
        #: The bound is not arbitrary. Deferring extraction means the audio must
        #: still be in the native ring when its turn comes, and the ring holds
        #: `native_ring_seconds` (120 s by default). A queue deep enough to outlive
        #: the ring converts an honest, counted drop into a silent extraction miss
        #: — the detection survives but its evidence is empty for a reason the
        #: operator cannot see from the drop counter. Sizing the queue below what
        #: the ring can cover keeps refusals at the door, where they are counted.
        #: Measured on this station, an ultrasonic detection's four clips take
        #: roughly 1.5 s to write, so 32 items is about 48 s of backlog against a
        #: 120 s ring.
        self._evidence_queue: asyncio.Queue[tuple[DetectionRecord, DetectorMetadata] | None] = (
            asyncio.Queue(maxsize=32)
        )
        self._evidence_task: asyncio.Task[None] | None = None
        #: Its own thread, deliberately. `asyncio.to_thread` uses the default
        #: executor, and so does the ALSA read in `alsa_source.read`. On a 4-core
        #: Pi that pool has 8 workers shared with clip writing, retention sweeps
        #: and database inserts, so a burst of multi-megabyte clip writes to the
        #: SD card can delay the capture read long enough to overrun the ALSA
        #: ring. Measured: 11 gaps and 8 overruns in five minutes once the
        #: detector stopped stalling and evidence volume roughly tripled.
        #: Capture always wins, so evidence gets an isolated single thread that
        #: cannot occupy a slot capture needs.
        self._evidence_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="oo-evidence"
        )
        self.evidence_dropped = 0
        self.evidence_written = 0

    # -- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        self.settings.ensure_directories()
        # Logging, the metrics mount and the replay/synthetic sources are
        # built from these once per process. Recording them here is what makes
        # a later edit report as "saved, awaiting restart" rather than as done.
        self._record_applied(tuning.PINNED_AT_PROCESS_START)
        self.station_id = await asyncio.to_thread(self._ensure_station_row)
        await asyncio.to_thread(self._close_orphaned_streams)
        # Before capture, deliberately: if this process is coming back up in
        # the middle of a pause, the gates must already be closed by the time
        # the first block arrives. Restoring afterwards would leave a window --
        # short, but a window during which a paused station records.
        await asyncio.to_thread(self.pause.restore)
        await asyncio.to_thread(self.pause.close_stale_rows)
        self._running = True
        self._persist_task = asyncio.create_task(self._persist_loop(), name="persist")
        self._evidence_task = asyncio.create_task(self._evidence_loop(), name="evidence")
        self._housekeeping_task = asyncio.create_task(self._housekeeping_loop(), name="housekeeping")
        self._capture_task = asyncio.create_task(self._capture_supervisor(), name="capture")
        self._loop_lag_task = asyncio.create_task(self._loop_lag_watch(), name="loop-lag")
        log.info("station.started", station_id=str(self.station_id))

    async def stop(self) -> None:
        self._running = False
        for task in (self._capture_task, self._housekeeping_task, self._loop_lag_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        # ADR-066. The supervisor loop closes the stream row in
        # `_on_stream_close` *after* `_capture_loop` returns -- and on shutdown
        # `_capture_loop` does not return, it is cancelled. `CancelledError` is
        # re-raised out of the supervisor by design, so it propagates straight
        # past that call and the row is never closed.
        #
        # The result was invisible because `_close_orphaned_streams` repaired
        # it at the next startup: every `audio_stream` row on the live station
        # carried `end_reason='process_exited'`, including every clean deploy.
        # The graceful close path had never once run in production, and the
        # only reason anyone noticed is that ADR-065 started reporting an
        # unclean restart after a perfectly clean `systemctl restart`.
        #
        # Closed here, before the executors are shut down and while the frame
        # counters are still meaningful.
        if self.stream is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    self._close_stream_row,
                    self.stream.stream_id,
                    "station_stopped",
                    self._stream_frames,
                    self._stream_discontinuities,
                    self._stream_last_frame_utc,
                )
        for worker in self.workers:
            await worker.stop()
        if self._evidence_task:
            with contextlib.suppress(asyncio.QueueFull):
                self._evidence_queue.put_nowait(None)
            with contextlib.suppress(asyncio.CancelledError, Exception, TimeoutError):
                await asyncio.wait_for(self._evidence_task, timeout=5.0)
            self._evidence_executor.shutdown(wait=False, cancel_futures=True)
        if self._persist_task:
            with contextlib.suppress(asyncio.QueueFull):
                self._persist_queue.put_nowait(None)
            with contextlib.suppress(asyncio.CancelledError, Exception, TimeoutError):
                await asyncio.wait_for(self._persist_task, timeout=5.0)
        if self.source is not None:
            with contextlib.suppress(Exception):
                await self.source.close()
        self.live_audio.close()
        self.live_audio_ultrasonic.close()
        self.bus.close()
        log.info("station.stopped")

    def _ensure_station_row(self) -> uuid.UUID:
        with session_scope() as session:
            existing = session.query(orm.Station).first()
            if existing is not None:
                existing.name = self.settings.station_name
                existing.timezone = self.settings.timezone
                existing.latitude = self.settings.latitude
                existing.longitude = self.settings.longitude
                return existing.id
            row = orm.Station(
                name=self.settings.station_name,
                timezone=self.settings.timezone,
                latitude=self.settings.latitude,
                longitude=self.settings.longitude,
                software_version=_version(),
            )
            session.add(row)
            session.flush()
            return row.id

    # -- source selection -----------------------------------------------

    async def _build_source(self) -> Any:
        """Choose a capture source according to configuration.

        ``auto`` prefers real hardware and falls back to the synthetic scene, so
        the station is observable even with no microphone attached — and says
        loudly which one it is using, because a synthetic stream that looked like
        a real one would be the most dangerous failure mode in the whole system.
        """
        from .audio.alsa_source import AlsaSource
        from .audio.replay_source import ReplaySource, SyntheticSource

        setting = self.settings.source
        if setting == "replay":
            if not self.settings.replay_path:
                raise RuntimeError("OO_SOURCE=replay requires OO_REPLAY_PATH")
            return ReplaySource(
                self.settings.replay_path,
                block_ms=self.settings.capture_block_ms,
                loop=self.settings.replay_loop,
                speed=self.settings.replay_speed,
            )
        if setting == "synthetic":
            return SyntheticSource(
                scene=self.settings.synthetic_scene,
                sample_rate=self.settings.synthetic_sample_rate,
                block_ms=self.settings.capture_block_ms,
            )

        alsa = AlsaSource(
            device_key=self.settings.audio_device,
            preferred_rates=self.settings.preferred_sample_rates,
            preferred_formats=self.settings.preferred_formats,
            channels=self.settings.capture_channels,
            block_ms=self.settings.capture_block_ms,
            buffer_ms=self.settings.capture_buffer_ms,
        )
        if setting == "alsa":
            return alsa
        try:
            await alsa.open()
            return alsa
        except Exception as exc:
            log.warning("station.alsa_unavailable_falling_back", error=str(exc))
            self._emit_health(
                "warning",
                "capture.device_unavailable",
                {
                    "error": str(exc),
                    "fallback": f"synthetic:{self.settings.synthetic_scene}",
                    "devices_seen": [d.to_dict() for d in enumerate_capture_devices()],
                },
            )
            return SyntheticSource(
                scene=self.settings.synthetic_scene,
                sample_rate=self.settings.synthetic_sample_rate,
                block_ms=self.settings.capture_block_ms,
            )

    async def _capture_supervisor(self) -> None:
        """Reopen the device with bounded backoff after any loss."""
        backoff = self.settings.reopen_backoff_min_s
        while self._running:
            try:
                source = await self._build_source()
                info = source.info if getattr(source, "info", None) else await source.open()
                if getattr(source, "info", None) is None:
                    info = await source.open()
                self.source = source
                self.stream = info
                await self._on_stream_open(info)
                backoff = self.settings.reopen_backoff_min_s
                watcher = self._start_hardware_watch(source, info)
                try:
                    await self._capture_loop(source)
                finally:
                    if watcher is not None:
                        watcher.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await watcher
                reason = "source_exhausted"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.counters.open_failures += 1
                reason = f"{type(exc).__name__}: {exc}"
                log.exception("station.capture_failed")
                self._set_capture_state("error", reason)
                self._emit_health("critical", "capture.failed", {"error": reason})
            else:
                self._set_capture_state("stopped", reason)

            await self._on_stream_close(reason)
            if not self._running:
                return
            self.counters.stream_restarts += 1
            log.info("station.reopening", backoff_s=round(backoff, 2))
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.settings.reopen_backoff_max_s)

    def _start_hardware_watch(self, source: Any, info: StreamInfo) -> asyncio.Task[None] | None:
        """Watch for the real microphone returning, while on the synthetic fallback.

        `auto` falling back to a synthetic scene is deliberate: the station stays
        observable with no microphone attached, and says loudly that it is not
        live. What it did *not* do was ever look again. The synthetic source never
        ends, so the capture supervisor — which only rebuilds after a source ends —
        never got another chance to choose. Reattaching the microphone therefore
        did nothing until someone restarted the service, which on 2026-08-08 meant
        a day of synthetic audio after the AudioMoth's mode switch was moved.

        Graceful degradation has to include coming back.
        """
        if self.settings.source != "auto" or info.source_kind == SourceKind.ALSA:
            return None
        if self.settings.hardware_recheck_s <= 0:
            return None
        return asyncio.create_task(self._hardware_watch_loop(source), name="hardware-watch")

    async def _hardware_watch_loop(self, source: Any) -> None:
        while self._running:
            await asyncio.sleep(self.settings.hardware_recheck_s)
            try:
                device = await asyncio.to_thread(find_device, self.settings.audio_device)
            except Exception:
                log.exception("hardware_watch.probe_failed")
                continue
            if device is None:
                continue
            log.info(
                "hardware_watch.device_returned",
                device_key=device.stable_device_key,
                label=device.card_name,
            )
            self._emit_health(
                "info",
                "capture.device_returned",
                {"device_key": device.stable_device_key, "label": device.card_name},
            )
            # Ending the synthetic source is what hands control back to the
            # supervisor, which rebuilds and prefers real hardware.
            with contextlib.suppress(Exception):
                await source.close()
            return

    async def _on_stream_open(self, info: StreamInfo) -> None:
        rate = info.fmt.sample_rate
        # Anchored on the first block actually read, not here: opening and priming
        # an ALSA device takes a couple of hundred milliseconds, and anchoring on
        # open() would put every detection timestamp that far early.
        self.clock = None
        self._stream_frames = 0
        self._stream_discontinuities = 0
        self._stream_last_frame_utc = None
        self.native_ring = RingBuffer(rate, self.settings.native_ring_seconds)
        self.audible_ring = RingBuffer(
            self.settings.audible_sample_rate, self.settings.audible_ring_seconds
        )
        self.resampler = AudibleResampler(rate, self.settings.audible_sample_rate)
        self.native_levels = LevelAggregator(sample_rate=rate)
        self.audible_levels = LevelAggregator(sample_rate=self.settings.audible_sample_rate)
        self.normaliser.reset()
        self.live_audio.reconfigure(self.settings.audible_sample_rate)
        self.live_audio_ultrasonic.reconfigure(self.settings.audible_sample_rate)
        self._build_heterodyne(rate)
        self._build_spectrograms(rate)
        # Capture geometry has now been negotiated with the device and copied
        # into rings, resamplers and encoders. Record it, so a later settings
        # edit to any of it is reported as "saved, awaiting restart" rather
        # than silently doing nothing (ADR-048).
        self._record_applied(tuning.PINNED_AT_CAPTURE_START)

        if self.router is None:
            self.router = WindowRouter(native_rate=rate, stream_id=info.stream_id)
            await self._build_detectors(rate)
        else:
            self.router.rebind(info.stream_id, native_rate=rate)
            await self._rebind_detectors(rate)
        # Whatever exists now holds the live-tier values it was built with.
        # Recording them here means a subsequent edit is reported as applied
        # only once it has actually reached the object (ADR-048).
        self.record_live_targets()

        self._set_capture_state("capturing")
        self._device_row_id = await asyncio.to_thread(self._upsert_device_and_stream, info)

        log.info(
            "station.stream_open",
            stream=str(info.stream_id),
            source=info.source_kind,
            rate=rate,
            resampler=self.resampler.backend,
        )
        self.bus.emit(
            EventType.CAPTURE_STARTED,
            {
                "stream_id": str(info.stream_id),
                "source_kind": str(info.source_kind),
                "device_key": info.device_key,
                "device_label": info.device_label,
                "sample_rate": rate,
                "sample_format": info.fmt.sample_format,
                "channels": info.fmt.channels,
                "started_utc": info.started_utc.isoformat().replace("+00:00", "Z"),
                "resampler": self.resampler.describe(),
                "synthetic": info.source_kind != SourceKind.ALSA,
                "detail": _jsonable(info.detail),
            },
            station_id=self.station_id,
        )

    def _build_heterodyne(self, native_rate: int) -> None:
        """(Re)build the live ultrasonic monitor for the stream's native rate.

        Construction is cheap and unconditional — the actual CPU cost only
        happens in `_handle_block` when there is a listener attached, exactly
        as `LiveAudioBroadcaster.publish` is a no-op with none. Preserves the
        currently-tuned frequency across a device reopen rather than
        resetting to the configured default, so an operator's tuning survives
        a USB blip.
        """
        settings = self.settings
        tune_hz = self.heterodyne.tune_hz if self.heterodyne is not None else settings.ultrasonic_live_tune_hz
        output_rate = settings.audible_sample_rate
        if native_rate % output_rate != 0:
            self.heterodyne = None
            self.heterodyne_unavailable_reason = (
                f"native rate {native_rate} Hz is not an integer multiple of "
                f"{output_rate} Hz; live ultrasonic monitoring needs an exact "
                "decimation ratio"
            )
            log.warning("station.heterodyne_unavailable", reason=self.heterodyne_unavailable_reason)
            return
        nyquist = native_rate / 2.0
        if tune_hz >= nyquist:
            tune_hz = min(tune_hz, nyquist * 0.9)
        try:
            self.heterodyne = StreamingHeterodyne(
                native_rate,
                output_rate=output_rate,
                tune_hz=tune_hz,
                bandwidth_hz=settings.ultrasonic_heterodyne_bandwidth_hz,
            )
            self.heterodyne_unavailable_reason = ""
        except ValueError as exc:
            self.heterodyne = None
            self.heterodyne_unavailable_reason = str(exc)
            log.warning("station.heterodyne_unavailable", reason=self.heterodyne_unavailable_reason)

    def set_ultrasonic_tune_hz(self, hz: float) -> float:
        """Retune the live ultrasonic monitor. Returns the value actually applied.

        Clamped to the configured ultrasonic band and to just under the
        native Nyquist, so a client cannot request a tuning the oscillator
        cannot represent.
        """
        low, high = self.settings.ultrasonic_band_hz
        if self.heterodyne is not None:
            high = min(high, self.heterodyne.native_rate / 2.0 * 0.98)
        clamped = max(low, min(high, hz))
        if self.heterodyne is not None:
            self.heterodyne.set_tune_hz(clamped)
        return clamped

    def _build_spectrograms(self, native_rate: int) -> None:
        settings = self.settings
        self.spectrograms = {
            SPECTROGRAM_AUDIBLE: SpectrogramEncoder(
                channel=SPECTROGRAM_AUDIBLE,
                name="audible",
                sample_rate=settings.audible_sample_rate,
                fft_size=settings.spectrogram_fft,
                hop_ms=settings.spectrogram_hop_ms,
                bins=settings.spectrogram_bins,
                min_hz=settings.spectrogram_min_hz,
                max_hz=settings.spectrogram_max_hz,
                floor_db=settings.spectrogram_floor_db,
                ceiling_db=settings.spectrogram_ceiling_db,
                history_columns=settings.spectrogram_history_columns,
            )
        }
        if native_rate >= 96_000:
            # A separate ultrasonic view: the audible channel physically cannot
            # show anything above 24 kHz, and bat activity lives above it.
            self.spectrograms[SPECTROGRAM_ULTRASONIC] = SpectrogramEncoder(
                channel=SPECTROGRAM_ULTRASONIC,
                name="ultrasonic",
                sample_rate=native_rate,
                fft_size=4096,
                hop_ms=settings.spectrogram_hop_ms,
                bins=128,
                min_hz=15_000.0,
                max_hz=min(150_000.0, native_rate / 2 * 0.98),
                floor_db=settings.ultrasonic_spectrogram_floor_db,
                ceiling_db=settings.ultrasonic_spectrogram_ceiling_db,
                history_columns=settings.spectrogram_history_columns,
            )

    async def _build_detectors(self, native_rate: int) -> None:
        settings = self.settings
        self._record_applied(tuning.PINNED_AT_DETECTOR_START)
        context = DetectorContext(
            station_name=settings.station_name,
            timezone=settings.timezone,
            latitude=settings.latitude,
            longitude=settings.longitude,
            model_dir=model_registry.DEFAULT_MODEL_DIR,
        )
        plugins: list[Any] = []
        if settings.activity_enabled:
            plugins.append(
                ActivityDetector(
                    sample_rate=settings.audible_sample_rate,
                    band_hz=settings.activity_band_hz,
                    min_snr_db=settings.activity_min_snr_db,
                    min_duration_ms=settings.activity_min_duration_ms,
                )
            )
        if settings.birdnet_enabled:
            plugins.append(
                BirdNetDetector(
                    model_dir=settings.birdnet_model_dir or model_registry.DEFAULT_MODEL_DIR,
                    sample_rate=settings.audible_sample_rate,
                    stride_s=settings.birdnet_window_stride_s,
                    min_confidence=settings.birdnet_min_confidence,
                    plausibility_floor=settings.birdnet_plausibility_floor,
                    common_prior=settings.birdnet_common_prior,
                    range_threshold=settings.birdnet_range_threshold,
                    threshold_in_range=settings.birdnet_threshold_in_range,
                    threshold_uncommon=settings.birdnet_threshold_uncommon,
                    threshold_out_of_range=settings.birdnet_threshold_out_of_range,
                    near_miss_ring=settings.birdnet_near_miss_ring,
                    use_location_filter=(
                        settings.birdnet_use_location_filter
                        and settings.latitude is not None
                        and settings.longitude is not None
                    ),
                )
            )
        if settings.ultrasonic_enabled:
            plugins.append(
                UltrasonicDetector(
                    native_sample_rate=native_rate,
                    band_hz=settings.ultrasonic_band_hz,
                    min_snr_db=settings.ultrasonic_min_snr_db,
                    min_pulse_ms=settings.ultrasonic_min_pulse_ms,
                    max_pulse_ms=settings.ultrasonic_max_pulse_ms,
                    merge_gap_ms=settings.ultrasonic_merge_gap_ms,
                    pass_gap_s=settings.ultrasonic_pass_gap_s,
                    min_pulses_per_pass=settings.ultrasonic_min_pulses_per_pass,
                    buzz_max_interval_ms=settings.ultrasonic_buzz_max_interval_ms,
                    buzz_min_pulses=settings.ultrasonic_buzz_min_pulses,
                    buzz_interval_ratio=settings.ultrasonic_buzz_interval_ratio,
                    schedule=NightSchedule(
                        mode=settings.ultrasonic_schedule,
                        latitude=settings.latitude,
                        longitude=settings.longitude,
                        dusk_margin_min=settings.ultrasonic_schedule_dusk_margin_min,
                        dawn_margin_min=settings.ultrasonic_schedule_dawn_margin_min,
                    ),
                )
            )

        assert self.router is not None
        for plugin in plugins:
            worker = DetectorWorker(
                plugin,
                queue_depth=settings.detector_queue_depth,
                on_detections=self._on_detections,
                on_window_done=lambda w, window: self.leases.release(
                    window.window_id, w.plugin_id
                ),
                on_state_change=self._publish_detector_state,
            )
            started = await worker.start(context)
            self.workers.append(worker)
            if started:
                self.router.register(
                    worker.window_spec,
                    worker.plugin_id,
                    sample_rate=worker.window_spec.sample_rate,
                )
        await asyncio.to_thread(self._upsert_detector_rows)

    async def _rebind_detectors(self, native_rate: int) -> None:
        """Re-register window specs after a stream restart at a new rate."""
        assert self.router is not None
        for worker in self.workers:
            if not worker.available:
                continue
            if worker.window_spec.stream_kind == "native" and worker.window_spec.sample_rate != native_rate:
                # An ultrasonic detector bound to the old rate cannot serve the new
                # one; mark it and move on rather than feeding it wrong audio.
                # Deliberate reach into the worker: detector state is worker-owned,
                # and this is the pipeline telling it that its binding is now invalid.
                worker._set_state(
                    "unavailable",
                    f"stream reopened at {native_rate} Hz; detector was built for "
                    f"{worker.window_spec.sample_rate} Hz — restart required",
                )
                continue
            self.router.register(
                worker.window_spec, worker.plugin_id, sample_rate=worker.window_spec.sample_rate
            )

    async def _on_stream_close(self, reason: str) -> None:
        if self.stream is None:
            return
        stream_id = self.stream.stream_id
        # Stream-scoped, not `self.counters.frames`: that counter is process
        # lifetime and would carry an earlier stream's frames into this row's
        # count if the process had already reopened the device once before.
        frames = self._stream_frames
        await asyncio.to_thread(
            self._close_stream_row,
            stream_id,
            reason,
            frames,
            self._stream_discontinuities,
            self._stream_last_frame_utc,
        )
        self.bus.emit(
            EventType.CAPTURE_STOPPED,
            {"stream_id": str(stream_id), "reason": reason, "frames": frames},
            station_id=self.station_id,
        )
        if self.source is not None:
            with contextlib.suppress(Exception):
                await self.source.close()
        self.source = None

    # -- the hot path ---------------------------------------------------

    async def _capture_loop(self, source: Any) -> None:
        # Live hardware is expected to deliver on the crystal's schedule, so a
        # read that takes many seconds is a dead stream, not a slow one, and the
        # supervisor's reopen is the answer. A replay source has no such
        # obligation -- `step` mode blocks until a test says otherwise -- so it
        # is left alone. HANDOVER §1e.
        timeout = (
            self.settings.capture_read_timeout_s
            if self.stream is not None and self.stream.source_kind == SourceKind.ALSA
            else None
        )
        while self._running:
            if timeout is None:
                block = await source.read()
            else:
                block = await asyncio.wait_for(source.read(), timeout)
            if block is None:
                return
            began = time.perf_counter()
            self._handle_block(block)
            self.counters.hot_path_seconds += time.perf_counter() - began

    def _handle_block(self, block: CaptureBlock) -> None:
        assert self.native_ring is not None
        assert self.audible_ring is not None
        assert self.resampler is not None
        assert self.router is not None

        if self.clock is None:
            # Frame zero of this stream happened when this first block's audio
            # began, which is the only measurement that ties frames to wall time.
            offset_ns = block.first_frame * NS_PER_S // block.sample_rate
            self.clock = StreamClock(
                utc_ns_at_frame_zero=block.utc_start_ns - offset_ns,
                monotonic_ns_at_frame_zero=block.monotonic_start_ns - offset_ns,
            )
            log.info(
                "station.clock_anchored",
                stream=str(block.stream_id),
                at_frame=block.first_frame,
                utc=datetime.fromtimestamp(
                    self.clock.utc_ns_at_frame_zero / NS_PER_S, tz=UTC
                ).isoformat(),
            )

        self.counters.blocks += 1
        self.counters.frames += block.frame_count
        self.counters.last_block_monotonic_ns = block.monotonic_start_ns
        self._stream_frames += block.frame_count
        self._stream_last_frame_utc = datetime.fromtimestamp(
            block.utc_start_ns / NS_PER_S, tz=UTC
        )
        discontinuous = block.discontinuity is not None

        # 1. Evidence first: the native ring must hold this audio even if every
        #    downstream stage is broken.
        self.native_ring.append(block.first_frame, block.pcm, block.monotonic_start_ns)

        if discontinuous and block.discontinuity != DiscontinuityReason.STREAM_START:
            self._record_gap(block)

        # 2. Derive the audible stream. Its timing comes from its own frame index
        #    against the stream anchor, never from this block's read timestamp —
        #    see StreamClock for why.
        derived = self.resampler.process(block.pcm)
        audible_rate = self.settings.audible_sample_rate
        derived_utc_ns = self.clock.utc_ns(derived.first_frame, audible_rate)
        derived_monotonic_ns = self.clock.monotonic_ns(derived.first_frame, audible_rate)
        native_utc_ns = self.clock.utc_ns(block.first_frame, block.sample_rate)
        native_monotonic_ns = self.clock.monotonic_ns(block.first_frame, block.sample_rate)
        self.audible_ring.append(derived.first_frame, derived.pcm, derived_monotonic_ns)

        # 3. Telemetry and the live view.
        if self.native_levels is not None:
            sample = self.native_levels.push(block.pcm)
            if sample is not None:
                self._publish_levels(sample, block)
        if self.audible_levels is not None:
            self.audible_levels.push(derived.pcm)

        # 3a. Spectrograms, on the same terms as the heterodyne below: encode
        # only when someone is actually looking. Two encoders measured 0.0554 of
        # a core on the target against a whole-hot-path 0.1067 -- more than half
        # the per-block work, and in this station's steady state (the counter-top
        # display is the first-class surface, no browser open) all of it was
        # being spent on pictures nothing would ever read. See ADR-040.
        #
        # Unlike the heterodyne, resuming is *not* free: the encoder carries a
        # part-filled buffer and a history ring, both of which are stale after an
        # idle period, so the gate opening resets them. That is what makes a
        # viewer's first canvas honestly blank rather than quietly wrong; see
        # `SpectrogramEncoder.reset`.
        watching = self._spectrogram_consumers() >= self.settings.spectrogram_encode_min_viewers
        if self._spectrograms_encoding and not watching:
            # Discard at the moment the gate closes rather than when it reopens,
            # so the invariant is simply "whatever history an encoder holds is
            # contiguous and recent". A client connecting during an idle period
            # then finds nothing to back-fill and is told so, instead of finding
            # something that would have to be checked for staleness first.
            self._discard_idle_spectrogram_history()
        audible = self.spectrograms.get(SPECTROGRAM_AUDIBLE)
        if audible is not None and (watching or self.settings.spectrogram_keep_audible_warm):
            if discontinuous:
                audible.reset()
            columns = audible.push(derived.pcm, derived.first_frame, derived_utc_ns)
            if columns is not None:
                self._spectrogram_sink(columns)
        ultrasonic = self.spectrograms.get(SPECTROGRAM_ULTRASONIC)
        if ultrasonic is not None and watching:
            if discontinuous:
                ultrasonic.reset()
            columns = ultrasonic.push(block.pcm, block.first_frame, native_utc_ns)
            if columns is not None:
                self._spectrogram_sink(columns)
        self._spectrograms_encoding = watching

        # 3c. Live listening, gated by the operator pause (ADR-055).
        #
        # One float comparison against a cached deadline -- no lock, no
        # database, nothing that can block -- because this is the capture hot
        # path and capture always wins. It is checked here as well as at the
        # two endpoints because refusing a *connection* only stops the people
        # who connect after the pause starts, and the guarantee has to be that
        # no audio leaves this process while paused, not that no new listener
        # arrives. `publish` is a no-op with no listeners anyway, so on a
        # station nobody is listening to this costs the comparison and nothing
        # else.
        paused = self.pause.active
        if not paused:
            self.live_audio.publish(derived.pcm)

        # 3b. Live ultrasonic monitor: only heterodyne when someone is actually
        # listening. Capture always wins, and continuously heterodyning
        # 384 kHz for nobody would waste real CPU on a device that must never
        # be starved of it. `StreamingHeterodyne.process` carries oscillator
        # phase and filter state across calls itself, so skipping calls while
        # idle and resuming later is safe — it simply continues from wherever
        # it left off, exactly like `live_audio` skipping idle publishes.
        if (
            not paused
            and self.heterodyne is not None
            and self.live_audio_ultrasonic.listener_count
        ):
            ultrasonic_pcm = self.heterodyne.process(block.pcm)
            self.live_audio_ultrasonic.publish(ultrasonic_pcm)

        # 4. Windows for detectors, dropped rather than blocking.
        self.router.push(
            "audible48",
            derived.pcm,
            derived.first_frame,
            derived_utc_ns,
            derived_monotonic_ns,
            discontinuous=discontinuous,
            on_window=self._dispatch_window,
        )
        self.router.push(
            "native",
            block.pcm,
            block.first_frame,
            native_utc_ns,
            native_monotonic_ns,
            discontinuous=discontinuous,
            on_window=self._dispatch_window,
        )

    #: Replaced by the API layer with a function that pushes to WebSocket clients.
    def _spectrogram_sink(self, columns: Any) -> None:
        return None

    def set_spectrogram_sink(self, sink: Any) -> None:
        self._spectrogram_sink = sink  # type: ignore[method-assign]

    def set_spectrogram_consumer_count(self, provider: Callable[[], int]) -> None:
        """Tell the station how many live viewers there are (ADR-040).

        The sink cannot answer this: the API layer installs it once at startup
        and it stays installed whether or not a browser is connected, so its
        presence says nothing about whether anyone is watching. The default
        provider reports nobody, which means a Station that no API layer has
        wired a hub into does not spend CPU drawing pictures no code will read.
        """
        self._spectrogram_consumers = provider

    def _discard_idle_spectrogram_history(self) -> None:
        for key, encoder in self.spectrograms.items():
            if key == SPECTROGRAM_AUDIBLE and self.settings.spectrogram_keep_audible_warm:
                # Still running, so its history is still contiguous and current.
                continue
            encoder.reset(clear_history=True)

    def describe_spectrograms(self, *, include_frequencies: bool = True) -> list[dict[str, Any]]:
        """Channel descriptors, plus what the client needs to explain a blank one.

        `LiveHub` is right that an empty canvas looks like a broken pipeline, and
        gating (ADR-040) makes empty canvases a normal thing to open the page
        into. The fix is not to go back to encoding for nobody; it is to say
        which channels only record while watched and how much they currently
        hold, so the UI can label a deliberate blank as filling rather than let
        it read as failure.
        """
        gated = self.settings.spectrogram_encode_min_viewers > 0
        descriptors = []
        for key, encoder in self.spectrograms.items():
            payload = encoder.describe(include_frequencies=include_frequencies)
            channel_gated = gated and not (
                key == SPECTROGRAM_AUDIBLE and self.settings.spectrogram_keep_audible_warm
            )
            payload["viewer_gated"] = channel_gated
            payload["history_seconds"] = round(len(encoder.history) * encoder.hop_s, 3)
            descriptors.append(payload)
        return descriptors

    def _dispatch_window(self, window: AudioWindow, consumers: list[str]) -> None:
        for worker in self.workers:
            if worker.plugin_id not in consumers:
                continue
            if worker.offer(window):
                self.leases.grant(window.window_id, worker.plugin_id, lease_s=60.0)
            else:
                self.bus.emit(
                    EventType.WINDOW_DROPPED,
                    {
                        "window_id": str(window.window_id),
                        "plugin_id": worker.plugin_id,
                        "reason": "queue_full",
                        "queue_depth": worker.queue.qsize(),
                    },
                    station_id=self.station_id,
                )

    def _record_gap(self, block: CaptureBlock) -> None:
        self.counters.discontinuities += 1
        self.counters.estimated_missing_frames += block.missing_frames
        self._stream_discontinuities += 1
        lost_audio = block.missing_frames > 0
        if lost_audio:
            self.counters.gaps_with_loss += 1
        else:
            self.counters.gaps_without_loss += 1
        reason = str(block.discontinuity)
        duration_s = block.missing_frames / block.sample_rate if block.sample_rate else 0.0
        log.warning(
            "capture.gap",
            reason=reason,
            # Explicit, because `grep -c capture.gap` counts both kinds and every
            # triage in this project's history has over-read the result.
            lost_audio=lost_audio,
            missing_frames=block.missing_frames,
            seconds=round(duration_s, 4),
        )
        self.bus.emit(
            EventType.CAPTURE_GAP,
            {
                "stream_id": str(block.stream_id),
                "reason": reason,
                "estimated_missing_frames": block.missing_frames,
                "estimated_seconds": round(duration_s, 4),
                "at_frame": block.gap_at_frame,
                "occurred_utc": datetime.fromtimestamp(
                    block.utc_start_ns / NS_PER_S, tz=UTC
                ).isoformat().replace("+00:00", "Z"),
            },
            station_id=self.station_id,
        )
        if block.missing_frames > 0:
            # Fire and forget, because capture must not wait on a database — but
            # *not* silently. A bare `create_task` swallows the exception into an
            # unretrieved task, and a gap row that fails to insert is exactly the
            # record you need later to work out what a stream really captured.
            # The station has one stream row covering 08-07 03:38 to 08-08 11:36
            # whose gap rows stop at 06:24 on the first day, while `capture.gap`
            # was still being logged 29 hours later; a failing insert would look
            # precisely like that, and nothing would have said so.
            task = asyncio.get_running_loop().create_task(
                asyncio.to_thread(self._insert_gap_row, block)
            )
            task.add_done_callback(self._log_gap_row_result)

    @staticmethod
    def _log_gap_row_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error("capture.gap_row_failed", error=str(error), error_type=type(error).__name__)

    def _publish_levels(self, sample: Any, block: CaptureBlock) -> None:
        payload = sample.to_dict()
        payload["stream_id"] = str(block.stream_id)
        payload["measurement_note"] = "dBFS relative to full scale; not calibrated SPL"
        if self.audible_levels is not None and self.audible_levels.history:
            payload["audible"] = self.audible_levels.history[-1].to_dict()
        self.bus.emit(EventType.CAPTURE_LEVELS, payload, station_id=self.station_id)

    # -- detections -----------------------------------------------------

    async def _on_detections(
        self, worker: DetectorWorker, window: AudioWindow, detections: list[NativeDetection]
    ) -> None:
        # ADR-055. The single gate that makes "paused" mean "persists nothing".
        #
        # Placed at the mouth of the detection path rather than at each of its
        # ends, because everything downstream of here is a way for a claim
        # about the garden to escape the process: the detection row, the
        # evidence clip, the event bus -- and through the bus, MQTT and the
        # counter-top display. Gating once, before normalisation, means a new
        # consumer added to the bus later is paused by construction rather than
        # by whoever adds it remembering to check.
        #
        # Detectors keep running. Their windows, queues and lag counters stay
        # honest, so a pause does not leave the diagnostics looking like a
        # stalled pipeline, and the detector state a person reads after
        # resuming is continuous with the state before.
        if self.pause.active:
            self.pause.note_suppressed(len(detections))
            return
        native_rate = self.stream.fmt.sample_rate if self.stream else window.sample_rate
        for detection in detections:
            try:
                canonical = self.normaliser.normalise(
                    worker.metadata, window, detection, native_sample_rate=native_rate
                )
            except ClaimViolation as exc:
                # A detector broke its own contract. Refuse the detection loudly
                # rather than publishing a claim the product is not allowed to make.
                log.error("normalise.claim_violation", plugin=worker.plugin_id, error=str(exc))
                self._emit_health(
                    "critical",
                    "detector.claim_violation",
                    {"plugin_id": worker.plugin_id, "error": str(exc)},
                )
                continue
            if canonical is None:
                continue
            record = DetectionRecord(detection=canonical)
            # Evidence writing must not run in the detector's own task. Awaiting it
            # here blocks the worker until the clips are on disk, and an ultrasonic
            # detection writes four of them — including a time expansion that turns
            # 6 s of 384 kHz audio into ~54 s of output. Measured on a busy night on
            # the live station, that stalled `ultrasonic-pass-v1` badly enough to drop
            # 69 of 98 windows with a 42 s lag, while its own inference p95 was
            # 57 ms. The detector was missing bats because of disk I/O.
            try:
                self._evidence_queue.put_nowait((record, worker.metadata))
            except asyncio.QueueFull:
                # Capture always wins, and a bounded queue must drop rather than
                # block. Losing the evidence for one detection is much cheaper than
                # falling behind the live audio, so the drop is counted and the
                # detection is published without media rather than lost entirely.
                self.evidence_dropped += 1
                log.warning(
                    "evidence.dropped",
                    plugin=worker.plugin_id,
                    queued=self._evidence_queue.qsize(),
                    dropped_total=self.evidence_dropped,
                )
                self._publish_detection(record)
        # The lease is released by the worker's on_window_done hook, which runs for
        # quiet windows too.

    async def _evidence_loop(self) -> None:
        """Attach evidence off the detector path, then publish and persist.

        Ordering within a detection is unchanged: clips are attached before the
        record is emitted, so a live client still sees its media. What changed is
        that the detector worker no longer waits for any of it.
        """
        while True:
            item = await self._evidence_queue.get()
            if item is None:
                return
            record, metadata = item
            if self.settings.clips_enabled:
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        self._evidence_executor, self._attach_evidence, record, metadata
                    )
                    self.evidence_written += 1
                except Exception:
                    # Evidence is supporting material. Losing it must never lose the
                    # detection itself.
                    log.exception("evidence.failed", plugin=metadata.plugin_id)
            self._publish_detection(record)

    def _publish_detection(self, record: DetectionRecord) -> None:
        self._remember(record)
        self.bus.emit(EventType.DETECTION_CREATED, record.to_dict(), station_id=self.station_id)
        try:
            self._persist_queue.put_nowait(record)
        except asyncio.QueueFull:
            self.persist_dropped += 1

    def _attach_evidence(self, record: DetectionRecord, metadata: DetectorMetadata) -> None:
        if self.native_ring is None or self.stream is None:
            return
        detection = record.detection
        assets = self.clips.extract(
            ring=self.native_ring,
            detection_id=detection.detection_id,
            stream_id=detection.stream_id,
            event_start_frame=detection.source_start_frame,
            event_end_frame=detection.source_end_frame,
            score=detection.score,
            label=detection.detector_label,
            event_start_utc=detection.event_start_utc,
            plugin_id=metadata.plugin_id,
            peak_frequency_hz=detection.peak_frequency_hz,
        )
        for asset in assets:
            record.media.append(
                {
                    "id": str(asset.asset_id),
                    "kind": asset.kind,
                    "role": str(asset.detail.get("role", "evidence")),
                    "description": asset.detail.get("description"),
                    "sample_rate": asset.sample_rate,
                    "duration_s": round(asset.duration_s, 3),
                    "byte_length": asset.byte_length,
                    "sha256": asset.sha256,
                    "url": f"/api/v1/media/{asset.asset_id}",
                }
            )
            self.bus.emit(
                EventType.CLIP_WRITTEN,
                {
                    "detection_id": str(detection.detection_id),
                    "asset_id": str(asset.asset_id),
                    "kind": asset.kind,
                    "sample_rate": asset.sample_rate,
                    "duration_s": round(asset.duration_s, 3),
                },
                station_id=self.station_id,
            )
        record.media_assets = assets  # type: ignore[attr-defined]

    def _remember(self, record: DetectionRecord) -> None:
        self.recent_detections.append(record)
        if len(self.recent_detections) > 400:
            del self.recent_detections[:-400]

    def _publish_detector_state(self, worker: DetectorWorker) -> None:
        self.bus.emit(
            EventType.DETECTOR_STATE,
            worker.snapshot(),
            station_id=self.station_id,
        )

    def _emit_health(self, severity: str, event_type: str, detail: dict[str, Any]) -> None:
        payload = {
            "service": "station",
            "severity": severity,
            "event_type": event_type,
            "detail": _jsonable(detail),
        }
        self.bus.emit(EventType.HEALTH_EVENT, payload, station_id=self.station_id)
        with contextlib.suppress(Exception):
            asyncio.get_running_loop().create_task(
                asyncio.to_thread(self._insert_health_row, severity, event_type, detail)
            )

    def site_pending_restart(self) -> list[str]:
        """Settings whose saved value differs from the one the running
        components are actually using. Empty before anything is built: nothing
        has been pinned yet, so nothing can be stale.

        Covers live-tier settings as well as restart-pinned ones (ADR-048).
        A live setting is removed from this list by :meth:`apply_tuning`
        succeeding, not by the save succeeding -- if the owning object is not
        there to receive it (no ultrasonic encoder at 48 kHz, a detector that
        is switched off), the setting is saved and honestly reported as not
        yet in force."""
        if self.applied_site is None:
            return []
        return [
            name
            for name, applied in self.applied_site.items()
            if applied != getattr(self.settings, name)
        ]

    def _record_applied(self, names: Iterable[str]) -> None:
        """Record what a freshly built component was built with."""
        if self.applied_site is None:
            self.applied_site = {}
        for name in names:
            self.applied_site[name] = getattr(self.settings, name)

    def record_live_targets(self) -> None:
        """Snapshot every live-tier setting whose owning object now exists.

        Called after capture and the detectors are built. A setting whose
        owner is absent is deliberately *not* recorded: an absent key means
        "nothing here is using this", which reports as neither applied nor
        pending, because both would be a claim about a component that is not
        running.
        """
        self._record_applied(
            name
            for name, target in tuning.LIVE_TARGETS.items()
            if self._tuning_owner(target) is not None
        )

    def _tuning_owner(self, target: tuning.LiveTarget) -> Any | None:
        if target.kind == "clips":
            return self.clips
        if target.kind == "retention":
            return self.retention
        if target.kind == "spectrogram":
            channel = SPECTROGRAM_AUDIBLE if target.owner == "audible" else SPECTROGRAM_ULTRASONIC
            return self.spectrograms.get(channel)
        if target.kind == "detector":
            for worker in self.workers:
                if worker.plugin_id == target.owner and hasattr(worker.plugin, "retune"):
                    return worker.plugin
        return None

    def apply_tuning(self, names: Iterable[str]) -> list[str]:
        """Push live-tier settings into the objects that hold their values.

        Returns the names that could **not** be applied, so the caller can
        report them as pending rather than as done. Nothing here touches
        capture: the clip manager and retention sweeper take plain attribute
        assignments, and the encoders and detectors take a ``retune()`` that
        rebinds values read per column or per window. No device is reopened,
        no thread is joined, no queue is drained -- charter item 1 holds.
        """
        unapplied: list[str] = []
        retunes: dict[int, dict[str, Any]] = {}
        owners: dict[int, Any] = {}
        for name in names:
            target = tuning.LIVE_TARGETS.get(name)
            if target is None:
                # Read fresh from Settings on every use; already applied by
                # the caller mutating the settings object.
                continue
            owner = self._tuning_owner(target)
            if owner is None:
                unapplied.append(name)
                self.applied_site = self.applied_site or {}
                self.applied_site[name] = UNAPPLIED
                continue
            value = getattr(self.settings, name)
            if target.kind in ("clips", "retention"):
                scaled = int(value * target.scale) if target.scale != 1.0 else value
                if target.parameter == "clip_plugins":
                    scaled = frozenset(value)
                setattr(owner, target.parameter, scaled)
                self.applied_site = self.applied_site or {}
                self.applied_site[name] = value
            else:
                owners[id(owner)] = owner
                retunes.setdefault(id(owner), {})[target.parameter] = value
                self.applied_site = self.applied_site or {}
                self.applied_site[name] = value
        for key, kwargs in retunes.items():
            owners[key].retune(**kwargs)
        if unapplied:
            log.info("settings.tuning_not_applied", fields=sorted(unapplied))
        return unapplied

    async def apply_site_identity(self) -> None:
        """Push the (already-mutated) settings' identity fields to the station
        row, so /api/v1/station and MQTT discovery reflect a UI edit without a
        restart. Coordinates are written to the row too -- the row records the
        operator's declaration; `applied_site` records what the detectors are
        actually using, and `site_pending_restart` reports any daylight
        between the two."""
        self.station_id = await asyncio.to_thread(self._ensure_station_row)

    def _set_capture_state(self, state: str, detail: str = "") -> None:
        self.capture_state = state
        self.capture_detail = detail

    # -- persistence ----------------------------------------------------

    async def _persist_loop(self) -> None:
        while True:
            record = await self._persist_queue.get()
            if record is None:
                return
            try:
                await asyncio.to_thread(self._insert_detection, record)
                self.persist_written += 1
            except Exception:
                self.persist_failures += 1
                log.exception("persist.detection_failed")

    def _insert_detection(self, record: DetectionRecord) -> None:
        detection = record.detection
        detector_id = self._detector_rows.get(detection.plugin_id)
        with session_scope() as session:
            if detector_id is None:
                detector_id = self._detector_row_id(session, detection)
            row = orm.Detection(
                id=detection.detection_id,
                station_id=self.station_id,
                detector_id=detector_id,
                stream_id=detection.stream_id,
                window_id=detection.window_id,
                event_start_utc=detection.event_start_utc,
                event_end_utc=detection.event_end_utc,
                source_start_frame=detection.source_start_frame,
                source_end_frame=detection.source_end_frame,
                detector_label=detection.detector_label,
                common_name=detection.common_name,
                scientific_name=detection.scientific_name,
                canonical_taxon_id=detection.canonical_taxon_id,
                rank=detection.rank,
                taxonomic_group=detection.taxonomic_group,
                score=detection.score,
                calibrated_probability=detection.calibrated_probability,
                peak_frequency_hz=detection.peak_frequency_hz,
                native_result=detection.native_result,
            )
            session.add(row)
            for asset in getattr(record, "media_assets", []):
                session.add(
                    orm.MediaAsset(
                        id=asset.asset_id,
                        kind=asset.kind,
                        storage_uri=str(asset.path),
                        mime_type=asset.mime_type,
                        stream_id=detection.stream_id,
                        source_start_frame=asset.start_frame,
                        source_end_frame=asset.end_frame,
                        sample_rate=asset.sample_rate,
                        byte_length=asset.byte_length,
                        sha256=asset.sha256,
                        expires_at=asset.expires_at,
                        detail=_jsonable(asset.detail),
                    )
                )
                session.add(
                    orm.DetectionMedia(
                        detection_id=detection.detection_id,
                        media_asset_id=asset.asset_id,
                        role=str(asset.detail.get("role", "evidence")),
                    )
                )

    def _detector_row_id(self, session: Any, detection: CanonicalDetection) -> uuid.UUID:
        row = (
            session.query(orm.Detector)
            .filter_by(
                plugin_id=detection.plugin_id,
                plugin_version=detection.plugin_version,
                model_version=detection.model_version,
            )
            .first()
        )
        if row is None:
            row = orm.Detector(
                plugin_id=detection.plugin_id,
                plugin_version=detection.plugin_version,
                model_id=detection.model_id,
                model_version=detection.model_version,
                model_sha256=detection.model_sha256,
            )
            session.add(row)
            session.flush()
        self._detector_rows[detection.plugin_id] = row.id
        return row.id

    def _upsert_detector_rows(self) -> None:
        with session_scope() as session:
            for worker in self.workers:
                meta = worker.metadata
                row = (
                    session.query(orm.Detector)
                    .filter_by(
                        plugin_id=meta.plugin_id,
                        plugin_version=meta.plugin_version,
                        model_version=meta.model_version,
                    )
                    .first()
                )
                if row is None:
                    row = orm.Detector(
                        plugin_id=meta.plugin_id,
                        plugin_version=meta.plugin_version,
                        model_id=meta.model_id,
                        model_version=meta.model_version,
                        model_sha256=meta.model_sha256,
                        taxonomy_version=meta.taxonomy_version,
                        licence_name=meta.licence_name,
                        licence_url=meta.licence_url,
                        claim=meta.claim,
                        calibrated=meta.calibrated,
                        configuration={
                            "stream_kind": worker.window_spec.stream_kind,
                            "sample_rate": worker.window_spec.sample_rate,
                            "duration_s": worker.window_spec.duration_s,
                            "stride_s": worker.window_spec.stride_s,
                        },
                    )
                    session.add(row)
                    session.flush()
                else:
                    row.model_sha256 = meta.model_sha256 or row.model_sha256
                    row.licence_name = meta.licence_name
                    row.claim = meta.claim
                self._detector_rows[meta.plugin_id] = row.id

    def _upsert_device_and_stream(self, info: StreamInfo) -> uuid.UUID | None:
        with session_scope() as session:
            device_id: uuid.UUID | None = None
            if info.source_kind == SourceKind.ALSA:
                device = session.query(orm.AudioDevice).filter_by(
                    stable_device_key=info.device_key
                ).first()
                probe = find_device(info.device_key)
                if device is None:
                    device = orm.AudioDevice(
                        station_id=self.station_id,
                        stable_device_key=info.device_key,
                        label=info.device_label,
                    )
                    session.add(device)
                device.last_seen = datetime.now(UTC)
                device.negotiated_sample_rate = info.fmt.sample_rate
                device.negotiated_format = info.fmt.sample_format
                device.negotiated_channels = info.fmt.channels
                device.alsa_address = str(info.detail.get("alsa_address") or "")
                if probe is not None:
                    device.usb_vendor_id = probe.usb_vendor_id
                    device.usb_product_id = probe.usb_product_id
                    device.usb_serial = probe.usb_serial
                    device.configuration = probe.to_dict()
                session.flush()
                device_id = device.id

            session.add(
                orm.AudioStream(
                    id=info.stream_id,
                    audio_device_id=device_id,
                    source_kind=str(info.source_kind),
                    start_utc=info.started_utc,
                    start_monotonic_ns=info.started_monotonic_ns,
                    sample_rate=info.fmt.sample_rate,
                    sample_format=info.fmt.sample_format,
                    channels=info.fmt.channels,
                    detail=_jsonable(info.detail),
                )
            )
            return device_id

    def _close_orphaned_streams(self) -> None:
        """End any stream a previous process left open, honestly.

        `_close_stream_row` only runs on a graceful shutdown, so a killed or crashed
        process leaves `end_utc` NULL forever. Anything reading history then treats
        that stream as still running, and overlapping open streams made capture
        coverage of a twelve hour night report as 1300% — a figure that discredits
        every other number beside it. Each is closed at the last moment there is
        evidence it was actually recording.

        Preference order for that moment, best evidence first:

        1. `last_frame_at_utc` — the heartbeat written every housekeeping tick
           (ADR-024) while the stream was open. Direct evidence of when audio was
           last actually delivered, not inferred from anything downstream.
        2. The latest detection or capture-gap timestamp on the stream — the
           original heuristic, kept as a fallback for rows written before the
           heartbeat existed.
        3. `start_utc` — nothing else to go on, so treat it as having captured
           nothing rather than guessing.

        Whichever was used is recorded in `detail.orphan_recovery` so the repair
        is auditable rather than a silent rewrite of history.
        """
        with session_scope() as session:
            open_streams = (
                session.query(orm.AudioStream).filter(orm.AudioStream.end_utc.is_(None)).all()
            )
            for row in open_streams:
                if row.id == getattr(self.stream, "stream_id", None):
                    continue
                method = "start_utc_fallback"
                recovered_end = row.start_utc
                if row.last_frame_at_utc is not None:
                    method = "heartbeat"
                    recovered_end = row.last_frame_at_utc
                else:
                    last_detection = (
                        session.query(func.max(orm.Detection.event_end_utc))
                        .filter(orm.Detection.stream_id == row.id)
                        .scalar()
                    )
                    last_gap = (
                        session.query(func.max(orm.CaptureGap.start_utc))
                        .filter(orm.CaptureGap.stream_id == row.id)
                        .scalar()
                    )
                    candidates = [
                        value for value in (last_detection, last_gap) if value is not None
                    ]
                    if candidates:
                        method = "detection_or_gap_timestamp"
                        recovered_end = max(candidates)
                detail = dict(row.detail or {})
                detail["orphan_recovery"] = {
                    "method": method,
                    "recovered_end_utc": recovered_end.isoformat(),
                    "frame_count_at_recovery": row.frame_count,
                }
                row.detail = detail
                row.end_utc = recovered_end
                row.end_reason = "process_exited"
            if open_streams:
                # ADR-065. This is the station's own evidence that its previous
                # run ended without a graceful close -- a crash, a kill, or the
                # power going out. It was already being computed and logged at
                # `info`, and then dropped on the floor.
                #
                # On 2026-08-17 the Raspberry Pi restarted at 09:07 UTC, 8.9
                # hours short of a 72-hour acceptance soak that was passing at
                # 99.9935% continuity. Nothing reported it. The run appeared to
                # continue -- capture reopened, health returned `ok`, the
                # continuity ratio reset and climbed again -- and the restart
                # was found two days later only because somebody ran `uptime`.
                # A soak whose invalidating event is invisible is not a soak.
                self.unclean_restart = True
                self.recovered_stream_count = len(open_streams)
                self.recovered_stream_end_utc = max(
                    (row.end_utc for row in open_streams if row.end_utc is not None),
                    default=None,
                )
                log.warning(
                    "station.previous_run_ended_uncleanly",
                    count=len(open_streams),
                    last_audio_utc=(
                        self.recovered_stream_end_utc.isoformat()
                        if self.recovered_stream_end_utc is not None
                        else None
                    ),
                    note=(
                        "no graceful shutdown was recorded; any soak or "
                        "measurement in progress at that moment is void"
                    ),
                )

    def _close_stream_row(
        self,
        stream_id: uuid.UUID,
        reason: str,
        frames: int,
        discontinuities: int,
        last_frame_at_utc: datetime | None,
    ) -> None:
        with session_scope() as session:
            row = session.get(orm.AudioStream, stream_id)
            if row is None or row.end_utc is not None:
                # Already closed. `stop()` closes the row directly (ADR-066)
                # and the supervisor's own `_on_stream_close` may also reach it
                # on an orderly source exhaustion, so this has to be safe to
                # call twice -- and the *first* close is the honest one.
                return
            # `end_utc` is honestly "when this row stopped being believed live" --
            # which can be much later than when audio actually stopped, if the
            # read loop wedged instead of erroring (see ADR-024). That gap is not
            # hidden: `last_frame_at_utc` records the truth separately, and
            # `history.coverage()` reconciles the two rather than trusting
            # `end_utc` alone.
            row.end_utc = datetime.now(UTC)
            row.end_monotonic_ns = time.monotonic_ns()
            row.frame_count = frames
            row.discontinuity_count = discontinuities
            row.end_reason = reason[:64]
            row.last_frame_at_utc = last_frame_at_utc

    def _heartbeat_stream_row(
        self,
        stream_id: uuid.UUID,
        frames: int,
        discontinuities: int,
        last_frame_at_utc: datetime,
    ) -> None:
        """Write the running frame count and heartbeat while the stream is still open.

        Cheap and idempotent: if this never runs again (process killed a moment
        later), the row still carries the truth as of the last tick rather than
        the zeros a freshly-created row starts with -- which is what let orphaned
        rows be closed with `frame_count=0` and an invented end time before this
        existed.
        """
        with session_scope() as session:
            row = session.get(orm.AudioStream, stream_id)
            if row is None or row.end_utc is not None:
                return
            row.frame_count = frames
            row.discontinuity_count = discontinuities
            row.last_frame_at_utc = last_frame_at_utc

    def _insert_gap_row(self, block: CaptureBlock) -> None:
        with session_scope() as session:
            session.add(
                orm.CaptureGap(
                    stream_id=block.stream_id,
                    start_monotonic_ns=block.monotonic_start_ns,
                    end_monotonic_ns=block.monotonic_end_ns,
                    start_utc=datetime.fromtimestamp(block.utc_start_ns / NS_PER_S, tz=UTC),
                    estimated_missing_frames=block.missing_frames,
                    reason=str(block.discontinuity),
                    detail={"at_frame": block.gap_at_frame, "sequence": block.sequence},
                )
            )

    def _insert_health_row(self, severity: str, event_type: str, detail: dict[str, Any]) -> None:
        with session_scope() as session:
            session.add(
                orm.HealthEvent(
                    service="station",
                    component="capture",
                    severity=severity,
                    event_type=event_type,
                    detail=_jsonable(detail),
                )
            )

    # -- housekeeping ---------------------------------------------------

    async def _loop_lag_watch(self) -> None:
        """Report how late the event loop is running.

        The capture read has its own executor (ADR-030), but the loop still has
        to *issue* each read and consume its result, so a blocked loop delays
        capture by exactly as much as it is blocked. Sleeping a short, fixed
        interval and measuring the overshoot is the whole measurement: a task
        asked for 100 ms that wakes 300 ms later was starved for 200 ms, and it
        does not matter whether the culprit was a synchronous callback, the GIL
        or a thread that never yielded.
        """
        interval = 0.1
        while self._running:
            before = time.monotonic()
            await asyncio.sleep(interval)
            lag = time.monotonic() - before - interval
            if lag > self.counters.loop_lag_max_s:
                self.counters.loop_lag_max_s = round(lag, 4)
            if lag > _LOOP_LAG_SLOW_S:
                self.counters.loop_lag_events += 1
                log.warning("loop.lag", lag_s=round(lag, 4))

    def _reanchor_clock_if_stepped(self) -> None:
        """Move the stream clock onto the wall clock's timeline if it stepped.

        ADR-063. `StreamClock` anchors frame zero to UTC once and then counts
        frames, which is what makes it drift-free -- and is also why a *step* to
        the system wall clock afterwards is baked in for the life of the stream.
        On this hardware that is the normal boot path, not an edge case: a
        Raspberry Pi has no battery-backed RTC, so it starts with the timestamp
        systemd saved at last shutdown and NTP steps it forward once the network
        is up. On 2026-08-17 capture anchored 1 minute 45 seconds before that
        step and spent the next 49 hours stamping every detection, clip filename
        and spectrogram column 106 seconds early.

        Only *steps* are corrected, never slew. NTP slews at up to 500 ppm, so a
        legitimate correction moves the two clocks apart by at most ~5 ms across
        a 10 s tick -- three orders of magnitude below the threshold. Chasing
        slew would reintroduce exactly the per-block timestamp jitter
        `StreamClock` exists to remove.

        Nothing here touches `monotonic_ns_at_frame_zero`: ordering, gap
        detection and duration are all keyed to the monotonic clock, which does
        not step, and they stay valid across a re-anchor. Only the UTC *name* of
        an instant changes, and only for audio timestamped after this call --
        rows already written keep the old, wrong name.
        """
        if self.clock is None:
            return
        sample = ClockCorrelation.sample()
        stepped_ns = self.clock.stepped_by(sample)
        if abs(stepped_ns) < _CLOCK_STEP_THRESHOLD_NS:
            return
        before = self.clock
        self.clock = before.reanchored(sample)
        self.counters.clock_reanchors += 1
        self.counters.clock_last_step_s = round(stepped_ns / NS_PER_S, 6)
        log.warning(
            "station.clock_reanchored",
            stepped_s=round(stepped_ns / NS_PER_S, 6),
            was_utc_at_frame_zero=datetime.fromtimestamp(
                before.utc_ns_at_frame_zero / NS_PER_S, tz=UTC
            ).isoformat(),
            now_utc_at_frame_zero=datetime.fromtimestamp(
                self.clock.utc_ns_at_frame_zero / NS_PER_S, tz=UTC
            ).isoformat(),
            stream=str(self.stream.stream_id) if self.stream is not None else None,
            note=(
                "timestamps written before this moment keep the old value; "
                "this corrects the future, not the past (ADR-063)"
            ),
        )

    async def _housekeeping_loop(self) -> None:
        ticks = 0
        while self._running:
            slept_at = time.monotonic()
            await asyncio.sleep(10.0)
            # Overshoot of a plain sleep is the cheapest event-loop-lag measure
            # there is: a loop that was asked for 10 s and woke at 10.6 s was
            # blocked for 0.6 s by somebody, and the capture read -- which the
            # loop must issue and consume even though it runs on its own
            # executor (ADR-030) -- was late by the same amount.
            loop_lag_s = time.monotonic() - slept_at - 10.0
            ticks += 1
            t0 = time.monotonic()
            self._reanchor_clock_if_stepped()
            self.leases.sweep()
            # ADR-055. This does not *end* a pause -- a pause ends at its
            # deadline, in `PauseController.active`, with nothing needing to
            # run. All this does is close the durable row promptly so the
            # history view stops showing a finished pause as still running. It
            # is a single-row UPDATE and only when one has just expired.
            try:
                await asyncio.to_thread(self.pause.sync)
            except Exception:
                log.exception("housekeeping.pause_sync_failed")
            t_leases = time.monotonic()
            snapshot = self.status_snapshot()
            t_snapshot = time.monotonic()
            self.bus.emit(EventType.STATION_STATUS, snapshot, station_id=self.station_id)
            t_emit = time.monotonic()
            self.counters.housekeeping_blocking_s = round(t_emit - t0, 4)
            self.counters.loop_lag_s = round(loop_lag_s, 4)
            log_tick = (
                log.warning
                if (t_emit - t0) > _HOUSEKEEPING_SLOW_S or loop_lag_s > _HOUSEKEEPING_SLOW_S
                else log.debug
            )
            log_tick(
                "housekeeping.tick",
                loop_lag_s=round(loop_lag_s, 4),
                leases_s=round(t_leases - t0, 4),
                snapshot_s=round(t_snapshot - t_leases, 4),
                emit_s=round(t_emit - t_snapshot, 4),
                blocking_total_s=round(t_emit - t0, 4),
                # Only the contributors worth reading: a dozen sub-millisecond
                # entries would bury the one that matters.
                snapshot_phases={
                    name: cost
                    for name, cost in sorted(
                        self._snapshot_phase_s.items(), key=lambda kv: -kv[1]
                    )[:4]
                    if cost >= 0.001
                },
            )
            # A heartbeat every tick, so a crash between now and the next graceful
            # close still leaves the row with a recent, honest `last_frame_at_utc`
            # and frame count instead of nothing at all (ADR-024). Measured on the
            # station 2026-08-08, 48 of 49 stream rows carried `frame_count = 0`,
            # because every stream but one was ended by the orphan sweep after a
            # kill or a crash rather than by a graceful close -- so anything
            # computing capture coverage from those rows read "recorded nothing".
            #
            # This deliberately writes the *stream*-scoped counters, never
            # `self.counters`, which are process-lifetime: a process that reopens
            # the device mid-life would otherwise write a later stream's row with
            # an earlier stream's frames added in. See `CaptureCounters`.
            #
            # One indexed single-row UPDATE every ~10 s is trivial next to a clip
            # write, so this stays on the default executor like the other small
            # writes below, not the dedicated evidence one.
            if self.stream is not None and self._stream_last_frame_utc is not None:
                try:
                    await asyncio.to_thread(
                        self._heartbeat_stream_row,
                        self.stream.stream_id,
                        self._stream_frames,
                        self._stream_discontinuities,
                        self._stream_last_frame_utc,
                    )
                except Exception:
                    log.exception("housekeeping.heartbeat_failed")
            # Re-measure the clip archive, in chunks, off the snapshot path.
            #
            # ADR-059. This used to happen *inside* `clips.disk_usage()`, on
            # this loop, whenever a 30 s cache had expired -- and by 2026-08-10
            # the station's 40,888-file archive made that a 0.45 s stall. It was
            # the sole cause of all 262 `capture.late_read` events in a clean
            # 2.02 h window, at 30 s intervals, eating 74% of the 500 ms ring.
            # The cadence below is deliberately the same 30 s the cache TTL gave,
            # so this change moves the stall and does not also move the
            # frequency: the next person measuring has one variable, not two.
            if ticks % 3 == 0:
                try:
                    await self.clips.refresh_disk_usage()
                except Exception:
                    log.exception("housekeeping.clip_usage_failed")
            # Retention runs in the evidence executor's dedicated thread, never
            # the default pool (ADR-021, ADR-026), and every call is bounded by
            # batch size and a wall-clock budget so a backlog drains gradually.
            #
            # It is also *paced*, at `retention_interval_s` (default 300 s), not
            # run on every tick. A dedicated thread keeps the sweep off the
            # loop's back but not out of its way: the sweep is ORM work in
            # Python, and 0.30 s of it starved the event loop for 55-150 ms at a
            # time. The loop still issues and awaits every capture read, so that
            # lag lands directly on capture. Measured on the station 2026-08-08:
            # 1.6 `capture.gap` records per minute with a 10 s cadence, zero
            # over the following seven minutes with the sweep disabled. ADR-033.
            interval_ticks = max(1, round(self.settings.retention_interval_s / 10.0))
            if self.settings.retention_enabled and ticks % interval_ticks == 0:
                try:
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        self._evidence_executor, self.retention.sweep
                    )
                except Exception:
                    log.exception("housekeeping.retention_failed")
                else:
                    if result.total_deleted:
                        self._emit_health("info", "retention.swept", result.to_dict())
                    if not result.complete:
                        log.warning(
                            "housekeeping.retention_not_keeping_up",
                            **result.to_dict(),
                        )
                    # ADR-061. `not result.complete` above fires just as
                    # readily for an ordinary backlog drain (self-correcting:
                    # the next sweep continues) as it did, silently, for nine
                    # days when an unbounded preamble query exhausted the
                    # whole budget before the first tier guard ran. The two
                    # look identical in that log line and in a flat zero
                    # deletion count. `tiers_skipped` is what tells them
                    # apart: a backlog drain only ever skips a trailing
                    # suffix of tiers, because the ones before it consumed
                    # real budget or time, whereas all three tiers skipped
                    # together means no tier ever ran -- which a healthy
                    # station in steady state (nothing left to delete) never
                    # produces, because every guard is still reached and each
                    # tier's own query simply finds nothing.
                    if len(result.tiers_skipped) == 3:
                        log.error(
                            "housekeeping.retention_never_reached_a_tier",
                            **result.to_dict(),
                        )
            # ADR-057: on the same pacing and the same dedicated thread as the
            # sweep, stat one bounded slice of live media rows and count the
            # ones whose file is gone. A row asserting evidence that does not
            # exist went unnoticed on this station for five days because
            # nothing ever checked.
            #
            # Deliberately a rolling sample -- default 200 rows, ~1 ms of
            # `stat` measured on target, a full pass over ~50k live rows in
            # about 20 h -- and not a census: 48,989 stats cost 0.27 s, the
            # same order as the sweep ADR-033 had to pace after it cost ~1.9
            # capture gaps a minute, and capture always wins. It never marks
            # or deletes anything; reconciling is `oo clips reconcile-missing`.
            #
            # Not gated on `retention_enabled`, unlike the sweep: whether the
            # station is aging clips out has no bearing on whether its storage
            # numbers are true, and switching retention off must not switch
            # off the check that would notice them going wrong.
            if ticks % interval_ticks == 0:
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        self._evidence_executor, self.retention.audit_missing_files
                    )
                except Exception:
                    log.exception("housekeeping.missing_audit_failed")

    # -- introspection --------------------------------------------------

    def status_snapshot(self) -> dict[str, Any]:
        """Everything the debug UI needs about the current state of the station."""
        uptime_s = (time.monotonic_ns() - self.started_monotonic_ns) / NS_PER_S
        stream = self.stream
        lag_s = None
        if self.counters.last_block_monotonic_ns:
            lag_s = round(
                (time.monotonic_ns() - self.counters.last_block_monotonic_ns) / NS_PER_S, 3
            )
        expected_frames = None
        continuity = None
        if stream is not None and self.clock is not None:
            # Measured from frame zero, not from open(): priming the device takes a
            # couple of hundred milliseconds during which no audio exists to capture,
            # and charging that to continuity would understate it forever.
            elapsed_ns = time.monotonic_ns() - self.clock.monotonic_ns_at_frame_zero
            expected_frames = int(elapsed_ns * stream.fmt.sample_rate / NS_PER_S)
            if expected_frames > 0:
                # `_stream_frames`, not `self.counters.frames`: the elapsed time above
                # is measured from *this* stream's clock anchor, which resets on every
                # reopen, but `counters.frames` never does. After any reopen within a
                # process's life that mismatch was silently absorbed by `min(1.0, ...)`
                # rather than surfaced -- the same shape of error this session's
                # `audio_stream.frame_count` bug turned out to be (ADR-024).
                continuity = round(min(1.0, self._stream_frames / expected_frames), 6)

        hot_path_ratio = None
        if self.counters.frames and stream is not None:
            audio_seconds = self.counters.frames / stream.fmt.sample_rate
            if audio_seconds > 0:
                hot_path_ratio = round(self.counters.hot_path_seconds / audio_seconds, 4)

        # This whole method runs synchronously on the event loop, once per
        # housekeeping tick and again for every live viewer and API caller, so
        # each contributor's cost is worth knowing rather than guessing at. The
        # timing itself is a handful of `monotonic()` calls.
        phases: dict[str, float] = {}
        _t = time.monotonic

        def _timed(name: str, fn: Callable[[], Any]) -> Any:
            start = _t()
            value = fn()
            phases[name] = round(_t() - start, 4)
            return value

        version = _timed("version", _version)
        stream_detail = _timed("stream_detail", lambda: _jsonable(stream.detail) if stream else {})
        resampler = _timed("resampler", lambda: self.resampler.describe() if self.resampler else None)
        rings = _timed(
            "rings",
            lambda: {
                "native": self.native_ring.snapshot() if self.native_ring else None,
                "audible": self.audible_ring.snapshot() if self.audible_ring else None,
            },
        )
        spectrograms = _timed(
            "spectrograms",
            lambda: self.describe_spectrograms(include_frequencies=False),
        )
        segmenters = _timed("segmenters", lambda: self.router.snapshot() if self.router else [])
        leases = _timed("leases", self.leases.snapshot)
        detectors = _timed("detectors", lambda: [worker.snapshot() for worker in self.workers])
        clips = _timed("clips", self.clips.snapshot)
        storage = _timed("storage", self.clips.disk_usage)
        # `RetentionSweeper` does not know whether the station calls it, so its
        # own snapshot always claimed `enabled: true`. The station does know.
        retention = _timed(
            "retention",
            lambda: {
                **self.retention.snapshot(),
                "enabled": self.settings.retention_enabled,
                "interval_s": self.settings.retention_interval_s,
            },
        )
        live_audio = _timed("live_audio", self.live_audio.snapshot)
        live_audio_ultrasonic = _timed(
            "live_audio_ultrasonic",
            lambda: {
                **self.live_audio_ultrasonic.snapshot(),
                "heterodyne": self.heterodyne.describe() if self.heterodyne else None,
                "unavailable_reason": self.heterodyne_unavailable_reason
                if self.heterodyne is None
                else None,
            },
        )
        bus_stats = _timed("bus", self.bus.stats)
        self._snapshot_phase_s = phases

        return {
            "station": {
                "id": str(self.station_id) if self.station_id else None,
                "name": self.settings.station_name,
                "timezone": self.settings.timezone,
                "latitude": self.settings.latitude,
                "longitude": self.settings.longitude,
                "software_version": version,
                "uptime_s": round(uptime_s, 1),
                # First-run honesty: with no location the station is healthy but
                # runs unfiltered -- the UI banners this rather than anyone
                # discovering it from confidently unfiltered candidate lists.
                "location_configured": self.settings.latitude is not None
                and self.settings.longitude is not None,
                "site_pending_restart": self.site_pending_restart(),
            },
            # ADR-055. Top-level rather than inside `capture`, because it is not
            # a fact about capture: capture is running normally throughout a
            # pause. It is a fact about what the station is allowed to keep.
            "pause": self.pause.snapshot(),
            "capture": {
                "state": self.capture_state,
                "detail": self.capture_detail,
                "stream_id": str(stream.stream_id) if stream else None,
                "source_kind": str(stream.source_kind) if stream else None,
                "is_live_hardware": bool(stream and stream.source_kind == SourceKind.ALSA),
                "device_key": stream.device_key if stream else None,
                "device_label": stream.device_label if stream else None,
                "sample_rate": stream.fmt.sample_rate if stream else None,
                "sample_format": stream.fmt.sample_format if stream else None,
                "channels": stream.fmt.channels if stream else None,
                "started_utc": stream.started_utc.isoformat().replace("+00:00", "Z")
                if stream
                else None,
                "blocks": self.counters.blocks,
                "frames": self.counters.frames,
                "expected_frames": expected_frames,
                "continuity_ratio": continuity,
                "discontinuities": self.counters.discontinuities,
                # `discontinuities` is the sum of these two. Report the split, so
                # nobody has to infer lost recording from a count of log lines.
                "gaps_with_loss": self.counters.gaps_with_loss,
                "gaps_without_loss": self.counters.gaps_without_loss,
                "estimated_missing_frames": self.counters.estimated_missing_frames,
                "estimated_missing_seconds": round(
                    self.counters.estimated_missing_frames / stream.fmt.sample_rate, 4
                )
                if stream and stream.fmt.sample_rate
                else 0.0,
                "stream_restarts": self.counters.stream_restarts,
                "open_failures": self.counters.open_failures,
                # ADR-063. Non-zero means this process has written timestamps
                # that are wrong by roughly `clock_last_step_s`, and the log's
                # `station.clock_reanchored` line is what separates the wrong
                # ones from the right ones.
                "clock_reanchors": self.counters.clock_reanchors,
                "clock_last_step_s": self.counters.clock_last_step_s,
                # ADR-065. About the *previous* process, not this one: the
                # restart that voided the 2026-08-17 soak was invisible because
                # every counter beside it had reset to zero and looked healthy.
                "unclean_restart": self.unclean_restart,
                "recovered_streams": self.recovered_stream_count,
                "last_audio_before_restart_utc": (
                    self.recovered_stream_end_utc.isoformat()
                    if self.recovered_stream_end_utc is not None
                    else None
                ),
                "block_age_s": lag_s,
                "hot_path_cpu_ratio": hot_path_ratio,
                # A USB audio device runs on its own crystal. Reporting the
                # measured offset from nominal is more useful than pretending the
                # negotiated rate is exact.
                "observed_rate_hz": round(getattr(self.source, "observed_rate_hz", None) or 0.0, 3)
                or None,
                "rate_offset_ppm": round(getattr(self.source, "rate_offset_ppm", None) or 0.0, 2)
                or None,
                # Events ALSA itself reported, which is *not* the same as gaps:
                # one overrun can span several blocks, and a gap can be detected
                # from frame accounting without ALSA raising anything at all.
                "overruns": getattr(self.source, "overrun_count", None),
                # Reads that arrived late and cost nothing because the ring held
                # the audio. These used to be reported as gaps with lost audio,
                # which is how the station came to overstate its own losses ~13x
                # (ADR-039). They are a scheduling symptom, not lost recording,
                # and `late_read_max_frames` against `alsa_buffer_frames` is the
                # margin the ring still has.
                "late_reads": getattr(self.source, "late_reads", None),
                "late_read_max_frames": getattr(self.source, "late_read_max_frames", None),
                "alsa_buffer_frames": getattr(self.source, "buffer_frames", None) or None,
                # Distinct from "detail" above, which is the capture *state* message.
                # These were both called "detail" and the provenance dict silently
                # overwrote the message, so /api/v1/health interpolated a whole dict
                # into its problem strings.
                "stream_detail": stream_detail,
                # What the last housekeeping tick cost the event loop, and how
                # late that loop's own sleep woke. Capture has a private
                # executor, but the loop still issues and awaits every read.
                "housekeeping_blocking_s": self.counters.housekeeping_blocking_s,
                "loop_lag_s": self.counters.loop_lag_s,
                "loop_lag_max_s": self.counters.loop_lag_max_s,
                "loop_lag_events": self.counters.loop_lag_events,
            },
            "resampler": resampler,
            "rings": rings,
            "levels": {
                "native": self.native_levels.history[-1].to_dict()
                if self.native_levels and self.native_levels.history
                else None,
                "audible": self.audible_levels.history[-1].to_dict()
                if self.audible_levels and self.audible_levels.history
                else None,
                "note": "dBFS relative to digital full scale; not calibrated SPL",
            },
            # Frequency tables are sent once, in the live channel's hello frame.
            "spectrograms": spectrograms,
            "segmenters": segmenters,
            "leases": leases,
            "detectors": detectors,
            "normaliser": {
                "normalised": self.normaliser.stats.normalised,
                "duplicates_suppressed": self.normaliser.stats.duplicates_suppressed,
                "claim_violations": self.normaliser.stats.claim_violations,
            },
            "clips": clips,
            "storage": storage,
            "retention": retention,
            "live_audio": live_audio,
            "live_audio_ultrasonic": live_audio_ultrasonic,
            "bus": bus_stats,
            "snapshot_phase_s": phases,
            "persistence": {
                "written": self.persist_written,
                "queued": self._persist_queue.qsize(),
                "dropped": self.persist_dropped,
                "failures": self.persist_failures,
            },
            "evidence": {
                "queued": self._evidence_queue.qsize(),
                "written": self.evidence_written,
                "dropped": self.evidence_dropped,
            },
        }

    def level_history(self, seconds: int = 300) -> list[dict[str, Any]]:
        if self.audible_levels is None:
            return []
        history = list(self.audible_levels.history)[-seconds:]
        return [sample.to_dict() for sample in history]


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("open-observatory")
    except Exception:
        return "0.0.0+dev"


def _jsonable(value: Any) -> Any:
    """Coerce arbitrary provenance detail into something JSON-serialisable."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
