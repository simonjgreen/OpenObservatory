"""Deferred mode: a bounded, honestly-lagging queue for detectors too slow to run inline.

``DETECTOR_STRATEGY.md`` states the rule this module implements: "If real-time
[inference] is not sustainable, use a bounded deferred-night queue and process
windows after capture. The product must report lag honestly." A Pi 5 benchmark
measured a candidate model (not shipped — see the module docstring in
``station.py``'s detector wiring and ADR-017) at 0.52x realtime for a 0.5 s clip,
against 36-40x for the detectors that do run inline. That settles it for that
model, but the capability below is deliberately generic: any detector plugin may
opt into deferred mode, and this module has no dependency on, or knowledge of,
any specific model.

A plugin declares itself deferred by convention, not by a required protocol
member: set an instance attribute ``self.deferred = True`` in ``__init__``.
This is duck-typed (``getattr(plugin, "deferred", False)``) rather than added to
``DetectorPlugin`` in ``base.py``, deliberately, so the three shipped detectors
never need to be touched to keep conforming to the protocol.

Design summary
---------------
* :class:`DeferredDetectorWorker` subclasses :class:`~.base.DetectorWorker` and
  keeps every inherited safety property: bounded queue, drop-and-count on
  overflow, stale-window rejection, circuit breaker, per-window release via
  ``on_window_done``. Nothing about the live-path contract changes for the
  three shipped detectors — ``base.DetectorWorker`` behaves exactly as before
  when ``analysis_executor`` is not supplied.
* **Lower priority.** Analysis runs on a dedicated single-worker
  ``ThreadPoolExecutor`` rather than the event loop's shared default pool, so a
  slow deferred job can never take a thread slot away from a live detector's
  ``asyncio.to_thread`` call. Capture and the live detectors do not share a
  resource with the deferred worker at all beyond the GIL itself.
* **Honest lag.** In addition to the inherited ``lag_s`` (now - window end, for
  the last processed window), this worker tracks how long the *oldest still
  queued* window has been waiting, via a small side deque of admission
  timestamps kept in lock-step with the queue (``asyncio.Queue`` cannot be
  peeked). All of it is surfaced through :meth:`snapshot` for
  ``GET /api/v1/detectors`` and through ``api/metrics.py``.
* **Lease lifetime.** A deferred window can sit in queue for a large fraction
  of a night, far longer than the ~60s lease the live path grants. Rather than
  invent a long-lived lease policy here (out of scope for a worker that has no
  visibility into the station's ``TransientAssetStore``), this worker exposes
  ``on_window_admitted`` — fired synchronously the moment a window is accepted
  into the queue, mirroring ``on_window_done`` fired on every removal path
  (processed, stale-dropped, and abandoned-on-shutdown below). A caller that
  wants long-lived leases grants one in ``on_window_admitted`` and releases it
  in ``on_window_done``; because admission and the eventual release are both
  guaranteed to fire exactly once per accepted window, nothing can leak. A
  window that is rejected at ``offer()`` (queue full) never had a lease granted
  in the first place, matching the convention the live path already uses in
  ``station.py`` (grant only after a successful ``offer()``).
* **Deterministic shutdown.** ``stop()`` gives the queue a bounded window to
  drain naturally, then abandons whatever is left: those windows are popped
  without analysis, ``on_window_done`` still fires for each (so leases are
  still released), and the abandoned count is logged and counted. One caveat,
  stated rather than hidden: a window already handed to the executor thread
  cannot be interrupted mid-analysis — the same is true of real model inference
  in PyTorch or TFLite — so shutdown waits for that single in-flight item to
  finish before abandoning the rest of the queue. What happens to each item is
  still deterministic; only its wall-clock duration is not.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import time
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor

import structlog

from ..audio.contracts import AudioWindow, DetectorHealth, NativeDetection
from .base import DetectorPlugin, DetectorWorker

log = structlog.get_logger(__name__)

DEFAULT_QUEUE_DEPTH = 512
DEFAULT_SHUTDOWN_DRAIN_TIMEOUT_S = 5.0


class DeferredDetectorWorker(DetectorWorker):
    """A :class:`DetectorWorker` for a plugin that must not run inline.

    Everything about queueing, staleness and the circuit breaker is inherited
    unchanged. This subclass adds the three things a queue meant to survive a
    whole night, not a few seconds, needs on top: an isolated executor so it
    cannot steal thread slots from real-time detectors, an admission hook so a
    long-lived lease can be granted exactly when a window is actually accepted,
    and a shutdown path that drains-or-abandons deterministically instead of
    silently dropping whatever is still queued when the process stops.
    """

    def __init__(
        self,
        plugin: DetectorPlugin,
        *,
        queue_depth: int = DEFAULT_QUEUE_DEPTH,
        on_detections: Callable[[DetectorWorker, AudioWindow, list[NativeDetection]], Awaitable[None]],
        on_window_done: Callable[[DetectorWorker, AudioWindow], None] | None = None,
        #: Fired synchronously the instant a window is admitted to the queue —
        #: i.e. exactly once per window that will later, guaranteed, also reach
        #: ``on_window_done``. Intended for granting a long-lived lease.
        on_window_admitted: Callable[[DetectorWorker, AudioWindow], None] | None = None,
        on_state_change: Callable[[DetectorWorker], None] | None = None,
        failure_threshold: int = 5,
        recovery_delay_s: float = 60.0,
        shutdown_drain_timeout_s: float = DEFAULT_SHUTDOWN_DRAIN_TIMEOUT_S,
    ) -> None:
        if not getattr(plugin, "deferred", False):
            log.warning(
                "deferred.plugin_not_marked",
                plugin=getattr(getattr(plugin, "metadata", None), "plugin_id", plugin),
                detail="running under DeferredDetectorWorker but plugin.deferred is not True",
            )
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"deferred-{getattr(plugin.metadata, 'plugin_id', 'detector')}",
        )
        super().__init__(
            plugin,
            queue_depth=queue_depth,
            on_detections=on_detections,
            on_window_done=on_window_done,
            on_state_change=on_state_change,
            failure_threshold=failure_threshold,
            recovery_delay_s=recovery_delay_s,
            analysis_executor=self._executor,
        )
        self._on_window_admitted = on_window_admitted
        self._shutdown_drain_timeout_s = shutdown_drain_timeout_s
        #: Admission timestamps in strict FIFO order alongside ``self.queue``,
        #: since ``asyncio.Queue`` offers no peek. Popped in ``_process`` (every
        #: dequeue reaches it, success or failure) and in the shutdown abandon
        #: loop, so it always mirrors exactly what is still queued.
        self._enqueued_monotonic: deque[float] = deque()
        self.items_abandoned_on_shutdown = 0
        #: True only while a window is actually inside ``_run_analysis`` — the
        #: one span that genuinely cannot be interrupted. ``stop()`` reads this
        #: to decide whether cancelling the run task would discard a result.
        self._mid_flight = False

    # -- admission --------------------------------------------------------

    def offer(self, window: AudioWindow) -> bool:
        """Enqueue without blocking. Returns False when dropped for a full queue.

        A window that is rejected here never had a lease granted for it (the
        caller is expected to grant only after ``True``, exactly as the live
        path in ``station.py`` already does), so there is nothing to release —
        drop-before-admission cannot leak by construction.
        """
        if not self.available:
            return False
        try:
            self.queue.put_nowait(window)
        except asyncio.QueueFull:
            self.windows_dropped_queue_full += 1
            if self.windows_dropped_queue_full % 20 == 1:
                log.warning(
                    "deferred.queue_full",
                    plugin=self.plugin_id,
                    dropped=self.windows_dropped_queue_full,
                )
            return False
        self._enqueued_monotonic.append(time.monotonic())
        if self._on_window_admitted is not None:
            try:
                self._on_window_admitted(self, window)
            except Exception:
                log.exception("deferred.on_window_admitted_failed", plugin=self.plugin_id)
        return True

    async def _process(self, window: AudioWindow) -> None:
        if self._enqueued_monotonic:
            self._enqueued_monotonic.popleft()
        await super()._process(window)

    async def _run_analysis(self, window: AudioWindow) -> list[NativeDetection]:
        """Bracket the one span that truly cannot be interrupted.

        The base run loop (unchanged — inherited as-is) checks ``self._stopping``
        only between iterations and otherwise blocks on ``queue.get()`` or on
        this call. ``stop()`` uses ``_mid_flight`` to tell those two blocking
        states apart: cancelling a task blocked on ``queue.get()`` is instant
        and loses nothing; cancelling one blocked here would, per Python's
        ``asyncio`` semantics for a running executor future, discard the
        analysis result once the underlying thread eventually finishes rather
        than actually interrupting it — silently turning a processed window
        into neither processed nor honestly abandoned. So ``stop()`` never
        cancels while this is ``True``; it waits instead.
        """
        self._mid_flight = True
        try:
            return await super()._run_analysis(window)
        finally:
            self._mid_flight = False

    # -- observability ------------------------------------------------------

    @property
    def oldest_queued_age_s(self) -> float | None:
        """How long the longest-waiting queued window has been waiting, or None if empty."""
        if not self._enqueued_monotonic:
            return None
        return max(0.0, time.monotonic() - self._enqueued_monotonic[0])

    @property
    def items_dropped_total(self) -> int:
        return self.windows_dropped_queue_full + self.windows_dropped_stale + self.items_abandoned_on_shutdown

    async def health(self) -> DetectorHealth:
        base = await super().health()
        oldest = self.oldest_queued_age_s
        extra = (
            f"deferred queue={self.queue.qsize()}/{self.queue.maxsize} "
            f"oldest_queued_age_s={oldest:.1f} abandoned={self.items_abandoned_on_shutdown}"
            if oldest is not None
            else (
                f"deferred queue={self.queue.qsize()}/{self.queue.maxsize} "
                f"abandoned={self.items_abandoned_on_shutdown}"
            )
        )
        return dataclasses.replace(
            base,
            detail=f"{base.detail}; {extra}" if base.detail else extra,
            windows_dropped=base.windows_dropped + self.items_abandoned_on_shutdown,
        )

    def snapshot(self) -> dict[str, object]:
        snap = super().snapshot()
        snap["windows_dropped_abandoned_shutdown"] = self.items_abandoned_on_shutdown
        snap["windows_dropped"] = self.items_dropped_total
        snap["deferred"] = {
            "enabled": True,
            "oldest_queued_age_s": (
                round(self.oldest_queued_age_s, 3) if self.oldest_queued_age_s is not None else None
            ),
            "items_processed": self.windows_analysed,
            "items_dropped": self.items_dropped_total,
            "items_dropped_queue_full": self.windows_dropped_queue_full,
            "items_dropped_stale": self.windows_dropped_stale,
            "items_abandoned_on_shutdown": self.items_abandoned_on_shutdown,
            "processing_lag_s": self._last_lag_s,
        }
        return snap

    # -- shutdown -----------------------------------------------------------

    async def stop(self) -> None:
        """Drain the queue for up to ``shutdown_drain_timeout_s``, then abandon the rest.

        Deliberately built on ``asyncio.Queue.join()`` / ``task_done()`` rather
        than a bespoke polling loop: it is the same, already-exercised
        machinery ``tests/test_deferred.py`` uses directly elsewhere, and it
        lets the *unmodified* base ``_run`` loop keep consuming the queue
        normally — ``self._stopping`` is not set yet — for up to the drain
        budget, so a backlog that would clear quickly does, honestly, rather
        than being abandoned on principle.

        Once the budget is spent (or the queue drains before it), stopping is
        requested. Whether it is then safe to cancel the run task depends on
        what it is doing: idle on ``queue.get()`` (nothing in flight, nothing to
        lose) or genuinely analysing a window (``_mid_flight`` — see
        ``_run_analysis`` above, which cannot be interrupted without silently
        discarding its result). Only the first case is cancelled; the second is
        awaited to its own natural completion. Everything still queued once the
        task exits either way is abandoned: popped, released via
        ``on_window_done`` exactly as the processed path is, and counted.
        """
        began = time.monotonic()
        queued_at_shutdown = self.queue.qsize()
        drained_fully = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self.queue.join(), timeout=self._shutdown_drain_timeout_s)
            except TimeoutError:
                drained_fully = False
            self._stopping = True
            if not self._task.done() and not self._mid_flight:
                self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        drain_took_s = max(0.0, time.monotonic() - began)

        abandoned = 0
        while True:
            try:
                window = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            abandoned += 1
            if self._enqueued_monotonic:
                self._enqueued_monotonic.popleft()
            if self._on_window_done is not None:
                try:
                    self._on_window_done(self, window)
                except Exception:
                    log.exception("deferred.abandon_release_failed", plugin=self.plugin_id)
            self.queue.task_done()
        self.items_abandoned_on_shutdown += abandoned

        log.info(
            "deferred.shutdown",
            plugin=self.plugin_id,
            queued_at_shutdown=queued_at_shutdown,
            drained_fully=drained_fully,
            drain_took_s=round(drain_took_s, 3),
            abandoned=abandoned,
            processed=self.windows_analysed,
        )

        try:
            await self.plugin.shutdown()
        except Exception:
            log.exception("detector.shutdown_failed", plugin=self.plugin_id)
        self._executor.shutdown(wait=False, cancel_futures=False)
        self._set_state("stopped")
