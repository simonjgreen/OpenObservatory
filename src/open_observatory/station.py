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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import structlog
from sqlalchemy import func

from . import models as model_registry
from .audio.contracts import (
    NS_PER_S,
    AudioWindow,
    CaptureBlock,
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
from .schedule import NightSchedule
from .segmenter import TransientAssetStore, WindowRouter

log = structlog.get_logger(__name__)

SPECTROGRAM_AUDIBLE = 0
SPECTROGRAM_ULTRASONIC = 1


@dataclass(slots=True)
class CaptureCounters:
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
    #: Wall time spent inside the per-block hot path, for the CPU budget check.
    hot_path_seconds: float = 0.0


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
        self.started_monotonic_ns = time.monotonic_ns()

        self.station_id: uuid.UUID | None = None
        self.stream: StreamInfo | None = None
        self.clock: StreamClock | None = None
        self.source: Any = None
        self.capture_state: str = "starting"
        self.capture_detail: str = ""

        self.native_ring: RingBuffer | None = None
        self.audible_ring: RingBuffer | None = None
        self.resampler: AudibleResampler | None = None
        self.native_levels: LevelAggregator | None = None
        self.audible_levels: LevelAggregator | None = None
        self.spectrograms: dict[int, SpectrogramEncoder] = {}
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

        self.workers: list[DetectorWorker] = []
        self.recent_detections: list[DetectionRecord] = []
        self._detector_rows: dict[str, uuid.UUID] = {}
        self._device_row_id: uuid.UUID | None = None

        self._capture_task: asyncio.Task[None] | None = None
        self._persist_task: asyncio.Task[None] | None = None
        self._housekeeping_task: asyncio.Task[None] | None = None
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
        self.station_id = await asyncio.to_thread(self._ensure_station_row)
        await asyncio.to_thread(self._close_orphaned_streams)
        self._running = True
        self._persist_task = asyncio.create_task(self._persist_loop(), name="persist")
        self._evidence_task = asyncio.create_task(self._evidence_loop(), name="evidence")
        self._housekeeping_task = asyncio.create_task(self._housekeeping_loop(), name="housekeeping")
        self._capture_task = asyncio.create_task(self._capture_supervisor(), name="capture")
        log.info("station.started", station_id=str(self.station_id))

    async def stop(self) -> None:
        self._running = False
        for task in (self._capture_task, self._housekeeping_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
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

        if self.router is None:
            self.router = WindowRouter(native_rate=rate, stream_id=info.stream_id)
            await self._build_detectors(rate)
        else:
            self.router.rebind(info.stream_id, native_rate=rate)
            await self._rebind_detectors(rate)

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
                floor_db=-105.0,
                ceiling_db=-25.0,
                history_columns=settings.spectrogram_history_columns,
            )

    async def _build_detectors(self, native_rate: int) -> None:
        settings = self.settings
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
        frames = self.counters.frames
        await asyncio.to_thread(self._close_stream_row, stream_id, reason, frames)
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
        while self._running:
            block = await source.read()
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

        audible = self.spectrograms.get(SPECTROGRAM_AUDIBLE)
        if audible is not None:
            if discontinuous:
                audible.reset()
            columns = audible.push(derived.pcm, derived.first_frame, derived_utc_ns)
            if columns is not None:
                self._spectrogram_sink(columns)
        ultrasonic = self.spectrograms.get(SPECTROGRAM_ULTRASONIC)
        if ultrasonic is not None:
            if discontinuous:
                ultrasonic.reset()
            columns = ultrasonic.push(block.pcm, block.first_frame, native_utc_ns)
            if columns is not None:
                self._spectrogram_sink(columns)

        self.live_audio.publish(derived.pcm)

        # 3b. Live ultrasonic monitor: only heterodyne when someone is actually
        # listening. Capture always wins, and continuously heterodyning
        # 384 kHz for nobody would waste real CPU on a device that must never
        # be starved of it. `StreamingHeterodyne.process` carries oscillator
        # phase and filter state across calls itself, so skipping calls while
        # idle and resuming later is safe — it simply continues from wherever
        # it left off, exactly like `live_audio` skipping idle publishes.
        if self.heterodyne is not None and self.live_audio_ultrasonic.listener_count:
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
                "at_frame": block.first_frame,
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
            # 6 s of 384 kHz audio into ~54 s of output. Measured on a busy night at
            # the development station, that stalled `ultrasonic-pass-v1` badly enough to drop
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
        """End any stream a previous process left open.

        `_close_stream_row` only runs on a graceful shutdown, so a killed or crashed
        process leaves `end_utc` NULL forever. Anything reading history then treats
        that stream as still running, and overlapping open streams made capture
        coverage of a twelve hour night report as 1300% — a figure that discredits
        every other number beside it. Each is closed at the last moment there is
        evidence it was actually recording.
        """
        with session_scope() as session:
            open_streams = (
                session.query(orm.AudioStream).filter(orm.AudioStream.end_utc.is_(None)).all()
            )
            for row in open_streams:
                if row.id == getattr(self.stream, "stream_id", None):
                    continue
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
                candidates = [value for value in (last_detection, last_gap) if value is not None]
                row.end_utc = max(candidates) if candidates else row.start_utc
                row.end_reason = "process_exited"
            if open_streams:
                log.info("station.closed_orphaned_streams", count=len(open_streams))

    def _close_stream_row(self, stream_id: uuid.UUID, reason: str, frames: int) -> None:
        with session_scope() as session:
            row = session.get(orm.AudioStream, stream_id)
            if row is None:
                return
            row.end_utc = datetime.now(UTC)
            row.end_monotonic_ns = time.monotonic_ns()
            row.frame_count = frames
            row.discontinuity_count = self.counters.discontinuities
            row.end_reason = reason[:64]

    async def _checkpoint_stream_row(self) -> None:
        """Write the running frame and discontinuity totals to the open stream row.

        Cheap — one UPDATE every 30 s against a row that is already in cache —
        and it is what makes a crashed stream's row say how much it recorded
        instead of zero. The orphan sweep deliberately does not invent a frame
        count; this makes sure it does not have to.
        """
        if self.stream is None or not self._running:
            return
        stream_id = self.stream.stream_id
        frames = self.counters.frames
        discontinuities = self.counters.discontinuities
        try:
            await asyncio.to_thread(
                self._checkpoint_stream_row_blocking, stream_id, frames, discontinuities
            )
        except Exception:
            # Progress bookkeeping must never take capture down with it.
            log.exception("housekeeping.stream_checkpoint_failed")

    def _checkpoint_stream_row_blocking(
        self, stream_id: uuid.UUID, frames: int, discontinuities: int
    ) -> None:
        with session_scope() as session:
            row = session.get(orm.AudioStream, stream_id)
            if row is None or row.end_utc is not None:
                return
            row.frame_count = frames
            row.discontinuity_count = discontinuities

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
                    detail={"at_frame": block.first_frame, "sequence": block.sequence},
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

    async def _housekeeping_loop(self) -> None:
        ticks = 0
        while self._running:
            await asyncio.sleep(10.0)
            ticks += 1
            self.leases.sweep()
            self.bus.emit(
                EventType.STATION_STATUS, self.status_snapshot(), station_id=self.station_id
            )
            # Checkpoint how much audio this stream has actually taken. Without
            # this, `frame_count` and `discontinuity_count` are written *only* by
            # `_close_stream_row` on a graceful stop — and measured on the station
            # on 2026-08-08, 48 of 49 stream rows carried `frame_count = 0`,
            # because every other stream was ended by the orphan sweep after a
            # kill or a crash. Anything computing capture coverage from those
            # rows was reading a row that says the stream recorded nothing.
            if ticks % 3 == 0:
                await self._checkpoint_stream_row()
            # Retention every 5 minutes, in a thread: it walks the clip tree, and a
            # slow filesystem must never stall capture. Without this the size budget
            # and expiry policy would be configuration that does nothing.
            if ticks % 30 == 0:
                try:
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        self._evidence_executor, self.clips.enforce_retention
                    )
                except Exception:
                    log.exception("housekeeping.retention_failed")
                else:
                    if result["expired_deleted"] or result["budget_deleted"]:
                        self._emit_health("info", "clips.retention", result)

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
                continuity = round(min(1.0, self.counters.frames / expected_frames), 6)

        hot_path_ratio = None
        if self.counters.frames and stream is not None:
            audio_seconds = self.counters.frames / stream.fmt.sample_rate
            if audio_seconds > 0:
                hot_path_ratio = round(self.counters.hot_path_seconds / audio_seconds, 4)

        return {
            "station": {
                "id": str(self.station_id) if self.station_id else None,
                "name": self.settings.station_name,
                "timezone": self.settings.timezone,
                "latitude": self.settings.latitude,
                "longitude": self.settings.longitude,
                "software_version": _version(),
                "uptime_s": round(uptime_s, 1),
            },
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
                "alsa_buffer_frames": getattr(self.source, "buffer_frames", None) or None,
                # Distinct from "detail" above, which is the capture *state* message.
                # These were both called "detail" and the provenance dict silently
                # overwrote the message, so /api/v1/health interpolated a whole dict
                # into its problem strings.
                "stream_detail": _jsonable(stream.detail) if stream else {},
            },
            "resampler": self.resampler.describe() if self.resampler else None,
            "rings": {
                "native": self.native_ring.snapshot() if self.native_ring else None,
                "audible": self.audible_ring.snapshot() if self.audible_ring else None,
            },
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
            "spectrograms": [
                encoder.describe(include_frequencies=False)
                for encoder in self.spectrograms.values()
            ],
            "segmenters": self.router.snapshot() if self.router else [],
            "leases": self.leases.snapshot(),
            "detectors": [worker.snapshot() for worker in self.workers],
            "normaliser": {
                "normalised": self.normaliser.stats.normalised,
                "duplicates_suppressed": self.normaliser.stats.duplicates_suppressed,
                "claim_violations": self.normaliser.stats.claim_violations,
            },
            "clips": self.clips.snapshot(),
            "storage": self.clips.disk_usage(),
            "live_audio": self.live_audio.snapshot(),
            "live_audio_ultrasonic": {
                **self.live_audio_ultrasonic.snapshot(),
                "heterodyne": self.heterodyne.describe() if self.heterodyne else None,
                "unavailable_reason": self.heterodyne_unavailable_reason if self.heterodyne is None else None,
            },
            "bus": self.bus.stats(),
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
