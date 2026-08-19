"""Detector plugin contract and the worker that drives it.

The contract is the one in the technical spec §5. Two properties matter most:

* A detector returns its *native* output. Normalisation happens elsewhere, and
  the original result JSON is retained, so the product never loses what the model
  actually said.
* A detector may fail, be slow, or be unavailable, and the pipeline must keep
  capturing regardless. The worker enforces bounded queues, a delivery deadline,
  and a circuit breaker, and reports lag honestly rather than hiding it.
"""

from __future__ import annotations

import asyncio
import contextlib
import statistics
import time
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor
from typing import Protocol, runtime_checkable

import structlog

from ..audio.contracts import (
    NS_PER_S,
    AudioWindow,
    DetectorHealth,
    DetectorMetadata,
    DetectorState,
    NativeDetection,
    WindowSpec,
)

log = structlog.get_logger(__name__)


class DetectorContext:
    """What the pipeline gives a detector at initialisation time."""

    def __init__(
        self,
        *,
        station_name: str,
        timezone: str,
        latitude: float | None,
        longitude: float | None,
        model_dir: object | None = None,
    ) -> None:
        self.station_name = station_name
        self.timezone = timezone
        self.latitude = latitude
        self.longitude = longitude
        self.model_dir = model_dir


@runtime_checkable
class DetectorPlugin(Protocol):
    metadata: DetectorMetadata
    window_spec: WindowSpec

    async def initialise(self, context: DetectorContext) -> None: ...
    async def analyse(self, window: AudioWindow) -> list[NativeDetection]: ...
    async def health(self) -> DetectorHealth: ...
    async def shutdown(self) -> None: ...


class DetectorUnavailable(RuntimeError):
    """Raised by ``initialise`` when required assets are absent.

    This is an expected outcome, not a bug: ADR-006 forbids bundling third-party
    model binaries, so a fresh checkout legitimately has no BirdNET model.
    """


class DetectorWorker:
    """Runs one detector over a bounded queue of windows.

    Analysis runs in a thread because model inference and NumPy work are
    CPU-bound and would otherwise block capture. Capture always wins: if the
    queue is full, windows are dropped and counted.
    """

    def __init__(
        self,
        plugin: DetectorPlugin,
        *,
        queue_depth: int = 16,
        on_detections: Callable[[DetectorWorker, AudioWindow, list[NativeDetection]], Awaitable[None]],
        #: Called for every window once the detector is finished with it, whether
        #: or not it found anything. Releasing the transient-asset lease only on
        #: the detection path leaks a lease per quiet window, which is most of them.
        on_window_done: Callable[[DetectorWorker, AudioWindow], None] | None = None,
        on_state_change: Callable[[DetectorWorker], None] | None = None,
        failure_threshold: int = 5,
        recovery_delay_s: float = 60.0,
        #: When set, analysis runs on this executor instead of the loop's shared
        #: default thread pool. ``DeferredDetectorWorker`` (detectors/deferred.py)
        #: passes its own single-worker executor here so an expensive deferred
        #: model can never take a thread slot away from a real-time detector
        #: sharing the process default pool — that is the "lower priority" the
        #: deferred-mode spec asks for. ``None`` preserves the exact behaviour
        #: every live detector already has.
        analysis_executor: Executor | None = None,
    ) -> None:
        self.plugin = plugin
        self.metadata = plugin.metadata
        self.window_spec = plugin.window_spec
        self.queue: asyncio.Queue[AudioWindow] = asyncio.Queue(maxsize=queue_depth)
        self._on_detections = on_detections
        self._on_window_done = on_window_done
        self._on_state_change = on_state_change
        self._failure_threshold = failure_threshold
        self._recovery_delay_s = recovery_delay_s
        self._analysis_executor = analysis_executor

        self.state: DetectorState = "starting"
        self.detail: str = ""
        self.windows_analysed = 0
        self.windows_dropped_queue_full = 0
        self.windows_dropped_stale = 0
        self.detections_emitted = 0
        self.failures = 0
        self._consecutive_failures = 0
        self._breaker_until_monotonic = 0.0
        self._runtimes_ms: deque[float] = deque(maxlen=200)
        self._last_lag_s: float | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    @property
    def plugin_id(self) -> str:
        return self.metadata.plugin_id

    @property
    def available(self) -> bool:
        return self.state in ("ok", "degraded")

    def _set_state(self, state: DetectorState, detail: str = "") -> None:
        if (state, detail) != (self.state, self.detail):
            self.state = state
            self.detail = detail
            log.info("detector.state", plugin=self.plugin_id, state=state, detail=detail)
            if self._on_state_change:
                self._on_state_change(self)

    async def start(self, context: DetectorContext) -> bool:
        try:
            await self.plugin.initialise(context)
        except DetectorUnavailable as exc:
            self._set_state("unavailable", str(exc))
            return False
        except Exception as exc:
            log.exception("detector.initialise_failed", plugin=self.plugin_id)
            self._set_state("error", f"{type(exc).__name__}: {exc}")
            return False
        self._set_state("ok")
        self._task = asyncio.create_task(self._run(), name=f"detector:{self.plugin_id}")
        return True

    def offer(self, window: AudioWindow) -> bool:
        """Enqueue a window without blocking. Returns False when dropped."""
        if not self.available:
            return False
        try:
            self.queue.put_nowait(window)
            return True
        except asyncio.QueueFull:
            self.windows_dropped_queue_full += 1
            if self.windows_dropped_queue_full % 20 == 1:
                log.warning(
                    "detector.queue_full",
                    plugin=self.plugin_id,
                    dropped=self.windows_dropped_queue_full,
                )
            return False

    async def _run(self) -> None:
        while not self._stopping:
            window = await self.queue.get()
            try:
                await self._process(window)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("detector.loop_error", plugin=self.plugin_id)
            finally:
                # Every exit path releases the window, including the ones where
                # analysis was skipped or raised.
                if self._on_window_done is not None:
                    try:
                        self._on_window_done(self, window)
                    except Exception:
                        log.exception("detector.window_done_failed", plugin=self.plugin_id)
                self.queue.task_done()

    async def _process(self, window: AudioWindow) -> None:
        now = time.monotonic()
        if now < self._breaker_until_monotonic:
            self.windows_dropped_stale += 1
            return

        age_s = window.age_s()
        if age_s > self.window_spec.max_delivery_latency_s:
            # Better to admit the window is too old than to publish a detection
            # timestamped a minute ago as though it were live.
            self.windows_dropped_stale += 1
            if self.windows_dropped_stale % 20 == 1:
                log.warning(
                    "detector.window_stale",
                    plugin=self.plugin_id,
                    age_s=round(age_s, 2),
                    limit_s=self.window_spec.max_delivery_latency_s,
                )
            return

        began = time.perf_counter()
        try:
            detections = await self._run_analysis(window)
        except Exception as exc:
            self.failures += 1
            self._consecutive_failures += 1
            log.exception("detector.analyse_failed", plugin=self.plugin_id)
            if self._consecutive_failures >= self._failure_threshold:
                self._breaker_until_monotonic = time.monotonic() + self._recovery_delay_s
                self._set_state(
                    "degraded",
                    f"circuit breaker open for {self._recovery_delay_s:.0f}s after "
                    f"{self._consecutive_failures} consecutive failures: {exc}",
                )
            return

        elapsed_ms = (time.perf_counter() - began) * 1000.0
        self._runtimes_ms.append(elapsed_ms)
        self.windows_analysed += 1
        self._consecutive_failures = 0
        if self.state == "degraded" and not self.detail.startswith("circuit"):
            pass
        elif self.state == "degraded":
            self._set_state("ok")

        # Lag as the spec defines it: how far behind real time the window end is.
        self._last_lag_s = max(
            0.0,
            (time.monotonic_ns() - (window.monotonic_start_ns + int(window.duration_s * NS_PER_S)))
            / NS_PER_S,
        )
        if detections:
            self.detections_emitted += len(detections)
            await self._on_detections(self, window, detections)

    async def _run_analysis(self, window: AudioWindow) -> list[NativeDetection]:
        """Run ``_analyse_sync`` off the event loop, on whichever executor applies."""
        if self._analysis_executor is not None:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._analysis_executor, self._analyse_sync, window)
        return await asyncio.to_thread(self._analyse_sync, window)

    def _analyse_sync(self, window: AudioWindow) -> list[NativeDetection]:
        """Bridge the async plugin API onto the worker thread.

        Plugins are declared ``async`` by the spec but do CPU-bound work, so each
        call gets its own short-lived loop on the worker thread rather than
        competing with the capture loop.
        """
        return asyncio.run(self.plugin.analyse(window))

    async def health(self) -> DetectorHealth:
        try:
            reported = await self.plugin.health()
        except Exception as exc:
            reported = DetectorHealth(available=False, state="error", detail=str(exc))
        runtimes = sorted(self._runtimes_ms)
        p95 = runtimes[min(len(runtimes) - 1, int(len(runtimes) * 0.95))] if runtimes else None
        return DetectorHealth(
            available=self.available and reported.available,
            state=self.state if self.state != "ok" else reported.state,
            detail=self.detail or reported.detail,
            windows_analysed=self.windows_analysed,
            windows_dropped=self.windows_dropped_queue_full + self.windows_dropped_stale,
            failures=self.failures,
            last_runtime_ms=round(self._runtimes_ms[-1], 2) if self._runtimes_ms else None,
            p95_runtime_ms=round(p95, 2) if p95 is not None else None,
            lag_s=round(self._last_lag_s, 3) if self._last_lag_s is not None else None,
        )

    def snapshot(self) -> dict[str, object]:
        runtimes = sorted(self._runtimes_ms)
        p95 = runtimes[min(len(runtimes) - 1, int(len(runtimes) * 0.95))] if runtimes else None
        realtime_factor = None
        if self._runtimes_ms:
            mean_ms = statistics.fmean(self._runtimes_ms)
            if mean_ms > 0:
                realtime_factor = round(self.window_spec.duration_s * 1000.0 / mean_ms, 2)
        return {
            "plugin_id": self.plugin_id,
            "plugin_version": self.metadata.plugin_version,
            "model_id": self.metadata.model_id,
            "model_version": self.metadata.model_version,
            "licence_name": self.metadata.licence_name,
            "licence_url": self.metadata.licence_url,
            "claim": self.metadata.claim,
            "calibrated": self.metadata.calibrated,
            "resource_class": self.metadata.resource_class,
            "state": self.state,
            "detail": self.detail,
            "window": {
                "stream_kind": self.window_spec.stream_kind,
                "sample_rate": self.window_spec.sample_rate,
                "duration_s": self.window_spec.duration_s,
                "stride_s": self.window_spec.stride_s,
            },
            "queue_depth": self.queue.qsize(),
            "queue_capacity": self.queue.maxsize,
            "windows_analysed": self.windows_analysed,
            "windows_dropped_queue_full": self.windows_dropped_queue_full,
            "windows_dropped_stale": self.windows_dropped_stale,
            "detections_emitted": self.detections_emitted,
            "failures": self.failures,
            "last_runtime_ms": round(self._runtimes_ms[-1], 2) if self._runtimes_ms else None,
            "p95_runtime_ms": round(p95, 2) if p95 is not None else None,
            "realtime_factor": realtime_factor,
            "lag_s": round(self._last_lag_s, 3) if self._last_lag_s is not None else None,
            "circuit_open": time.monotonic() < self._breaker_until_monotonic,
        }

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        try:
            await self.plugin.shutdown()
        except Exception:
            log.exception("detector.shutdown_failed", plugin=self.plugin_id)
        self._set_state("stopped")
