"""Deferred-mode detector queue tests.

``DETECTOR_STRATEGY.md`` (via ADR-017 and IMPLEMENTATION_PLAN.md Milestone 5 item
3) requires a bounded deferred-night queue for a detector too slow to run
inline, with lag reported honestly rather than absorbed silently. There is
deliberately no shipped BatDetect2 adapter (ADR-017: evaluated, not adopted), so
the consumer exercised here is a synthetic slow plugin — a stand-in for any
future expensive model, not BatDetect2 itself.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import numpy as np
import pytest

from open_observatory.audio.contracts import (
    NS_PER_S,
    AudioWindow,
    DetectorHealth,
    DetectorMetadata,
    NativeDetection,
    WindowSpec,
)
from open_observatory.detectors.base import DetectorContext, DetectorWorker
from open_observatory.detectors.deferred import DeferredDetectorWorker

RATE = 48000


def make_window(
    rate: int = RATE,
    duration_s: float = 0.5,
    *,
    spec: WindowSpec | None = None,
    start_frame: int = 0,
) -> AudioWindow:
    n = int(rate * duration_s)
    pcm = np.zeros(n, dtype=np.float32)
    spec = spec or WindowSpec(
        stream_kind="audible48", sample_rate=rate, duration_s=duration_s, stride_s=duration_s
    )
    utc0 = 1_700_000_000 * NS_PER_S
    duration_ns = int(n * NS_PER_S / rate)
    return AudioWindow(
        window_id=uuid.uuid4(),
        stream_id=uuid.uuid4(),
        stream_kind=spec.stream_kind,
        sample_rate=rate,
        start_frame=start_frame,
        end_frame=start_frame + n,
        native_start_frame=start_frame,
        native_end_frame=start_frame + n,
        utc_start_ns=utc0,
        utc_end_ns=utc0 + duration_ns,
        monotonic_start_ns=time.monotonic_ns(),
        pcm=pcm,
        spec=spec,
        created_monotonic_ns=time.monotonic_ns(),
    )


class SyntheticSlowDetector:
    """Stands in for an expensive model (e.g. a future BatDetect2 adapter).

    ``analyse`` blocks the calling thread for ``sleep_s`` — a synchronous
    ``time.sleep`` deliberately, since real model inference is CPU-bound
    blocking work, not an awaitable I/O wait, and this worker always runs
    ``analyse`` off the event loop (see ``DetectorWorker._analyse_sync``).
    """

    metadata = DetectorMetadata(
        plugin_id="synthetic-slow-v1",
        plugin_version="0.0.1",
        model_id="synthetic",
        model_version="0.0.1",
        model_sha256=None,
        taxonomy_version=None,
        licence_name="n/a",
        licence_url=None,
        claim="Test fixture only; not a real detector.",
        resource_class="heavy",
        calibrated=False,
    )
    window_spec = WindowSpec(
        stream_kind="audible48",
        sample_rate=RATE,
        duration_s=0.5,
        stride_s=0.5,
        # Deferred windows may wait a long time; this plugin says so explicitly
        # rather than relying on the live path's short default.
        max_delivery_latency_s=3600.0,
    )

    def __init__(self, sleep_s: float = 0.2, *, emit: bool = False) -> None:
        self.sleep_s = sleep_s
        self.emit = emit
        self.deferred = True  # the declaration requirement 1 asks for
        self.calls = 0

    async def initialise(self, context: DetectorContext) -> None:
        return None

    def _block(self) -> None:
        """Runs on the dedicated deferred-analysis thread, never on the event
        loop (``DetectorWorker._analyse_sync`` calls ``analyse`` via
        ``asyncio.run`` inside ``run_in_executor``), so a genuine blocking
        ``time.sleep`` here is a faithful stand-in for CPU-bound inference —
        unlike ``await asyncio.sleep``, which would yield instead of occupying
        the thread the way a real model call does.
        """
        time.sleep(self.sleep_s)

    async def analyse(self, window: AudioWindow) -> list[NativeDetection]:
        self.calls += 1
        self._block()
        if not self.emit:
            return []
        return [NativeDetection(offset_start_s=0.0, offset_end_s=0.1, score=0.9, label="synthetic")]

    async def health(self) -> DetectorHealth:
        return DetectorHealth(available=True, state="ok")

    async def shutdown(self) -> None:
        return None


class InstantDetector:
    """A trivial fast plugin standing in for a live detector like ``activity-v1``."""

    metadata = DetectorMetadata(
        plugin_id="instant-v1",
        plugin_version="0.0.1",
        model_id="none",
        model_version="0.0.1",
        model_sha256=None,
        taxonomy_version=None,
        licence_name="n/a",
        licence_url=None,
        claim="Test fixture only.",
        resource_class="light",
    )
    window_spec = WindowSpec(
        stream_kind="audible48", sample_rate=RATE, duration_s=0.5, stride_s=0.5
    )

    async def initialise(self, context: DetectorContext) -> None:
        return None

    async def analyse(self, window: AudioWindow) -> list[NativeDetection]:
        return []

    async def health(self) -> DetectorHealth:
        return DetectorHealth(available=True, state="ok")

    async def shutdown(self) -> None:
        return None


async def _never(*_args) -> None:
    return None


class TestDeferredAdmission:
    """Requirement 1 & 2: opt-in, bounded, drop-and-count rather than grow."""

    async def test_offer_admits_up_to_capacity_then_drops_and_counts(self) -> None:
        detector = SyntheticSlowDetector()
        worker = DeferredDetectorWorker(detector, queue_depth=2, on_detections=_never)
        worker.state = "ok"  # bypass start(): no consumer task wanted for this test

        w1, w2, w3 = make_window(), make_window(), make_window()
        assert worker.offer(w1) is True
        assert worker.offer(w2) is True
        assert worker.offer(w3) is False  # queue is full: dropped, not blocked
        assert worker.windows_dropped_queue_full == 1
        assert worker.queue.qsize() == 2
        # Never grows past the configured bound.
        assert worker.queue.maxsize == 2

    async def test_unmarked_plugin_still_works_but_warns(self) -> None:
        """A plugin that forgot ``self.deferred = True`` is still usable — warned, not refused."""
        detector = SyntheticSlowDetector()
        detector.deferred = False
        worker = DeferredDetectorWorker(detector, on_detections=_never)
        worker.state = "ok"
        assert worker.offer(make_window()) is True


class TestLiveOwnsCapture:
    """Requirement 1: the live path must never be blocked by a slow deferred worker."""

    async def test_fast_worker_is_not_delayed_by_a_backlogged_deferred_worker(self) -> None:
        deferred_detector = SyntheticSlowDetector(sleep_s=0.4)
        deferred_worker = DeferredDetectorWorker(deferred_detector, on_detections=_never)
        fast_worker = DetectorWorker(InstantDetector(), on_detections=_never)

        context = DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        assert await deferred_worker.start(context)
        assert await fast_worker.start(context)
        try:
            # Give the deferred worker a backlog that would take ~1.2s to drain.
            for _ in range(3):
                assert deferred_worker.offer(make_window())

            fast_done = asyncio.Event()

            def on_fast_done(_w: DetectorWorker, _window: AudioWindow) -> None:
                # on_window_done is called synchronously by the worker loop, never awaited.
                fast_done.set()

            fast_worker._on_window_done = on_fast_done  # type: ignore[method-assign]
            assert fast_worker.offer(make_window())

            # The fast worker must finish long before the deferred backlog could.
            await asyncio.wait_for(fast_done.wait(), timeout=0.3)
            assert deferred_detector.calls < 3, "deferred backlog must still be in flight"
        finally:
            await deferred_worker.stop()
            await fast_worker.stop()


class TestLagAndDepthReporting:
    """Requirement 3: lag, depth, oldest age, processed and dropped counts are all visible."""

    async def test_snapshot_reports_deferred_fields(self) -> None:
        detector = SyntheticSlowDetector(sleep_s=0.05)
        worker = DeferredDetectorWorker(detector, on_detections=_never)
        context = DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        assert await worker.start(context)
        try:
            worker.offer(make_window())
            worker.offer(make_window())
            # Sample before the queue drains: oldest age must be nonzero and depth visible.
            await asyncio.sleep(0.02)
            snap = worker.snapshot()
            assert snap["deferred"]["enabled"] is True
            assert snap["queue_depth"] >= 1
            assert snap["deferred"]["oldest_queued_age_s"] is not None
            assert snap["deferred"]["oldest_queued_age_s"] >= 0.0

            await worker.queue.join()
            snap = worker.snapshot()
            assert snap["deferred"]["items_processed"] == 2
            assert snap["deferred"]["oldest_queued_age_s"] is None  # nothing left queued
            assert snap["deferred"]["processing_lag_s"] is not None
            assert snap["windows_dropped"] == 0
        finally:
            await worker.stop()

    async def test_health_detail_mentions_the_backlog(self) -> None:
        detector = SyntheticSlowDetector(sleep_s=0.3)
        worker = DeferredDetectorWorker(detector, on_detections=_never)
        context = DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        assert await worker.start(context)
        try:
            worker.offer(make_window())
            worker.offer(make_window())
            await asyncio.sleep(0.02)
            health = await worker.health()
            assert "deferred queue=" in health.detail
        finally:
            await worker.stop()


class TestLeaseLifetime:
    """Requirement 4: leases are released on every path a window can leave by."""

    async def test_lease_released_when_processed(self) -> None:
        detector = SyntheticSlowDetector(sleep_s=0.01)
        leases: dict[uuid.UUID, str] = {}

        def admit(worker: DetectorWorker, window: AudioWindow) -> None:
            leases[window.window_id] = worker.plugin_id

        def release(worker: DetectorWorker, window: AudioWindow) -> None:
            leases.pop(window.window_id, None)

        worker = DeferredDetectorWorker(
            detector, on_detections=_never, on_window_admitted=admit, on_window_done=release
        )
        context = DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        assert await worker.start(context)
        try:
            window = make_window()
            assert worker.offer(window)
            assert window.window_id in leases, "lease must be granted on admission"
            await worker.queue.join()
            assert leases == {}, "lease must be released once the window is processed"
        finally:
            await worker.stop()

    async def test_lease_released_when_dropped_as_stale(self) -> None:
        """A window that waited past its own deadline is dropped, not analysed — still released."""
        detector = SyntheticSlowDetector(sleep_s=0.0)
        detector.window_spec = WindowSpec(
            stream_kind="audible48",
            sample_rate=RATE,
            duration_s=0.5,
            stride_s=0.5,
            max_delivery_latency_s=0.0,  # everything is immediately "too old"
        )
        leases: dict[uuid.UUID, str] = {}
        worker = DeferredDetectorWorker(
            detector,
            on_detections=_never,
            on_window_admitted=lambda w, window: leases.__setitem__(window.window_id, w.plugin_id),
            on_window_done=lambda w, window: leases.pop(window.window_id, None),
        )
        context = DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        assert await worker.start(context)
        try:
            window = make_window()
            assert worker.offer(window)
            await worker.queue.join()
            assert worker.windows_dropped_stale == 1
            assert detector.calls == 0, "a stale window must never reach analyse()"
            assert leases == {}, "lease must still be released on the dropped path"
        finally:
            await worker.stop()

    async def test_lease_never_granted_for_a_queue_full_rejection(self) -> None:
        detector = SyntheticSlowDetector()
        admitted: list[uuid.UUID] = []
        worker = DeferredDetectorWorker(
            detector,
            queue_depth=1,
            on_detections=_never,
            on_window_admitted=lambda _w, window: admitted.append(window.window_id),
        )
        worker.state = "ok"
        w1, w2 = make_window(), make_window()
        assert worker.offer(w1) is True
        assert worker.offer(w2) is False
        assert admitted == [w1.window_id], "rejected window must never be admitted, so nothing to leak"


class TestGracefulShutdown:
    """Requirement 5: shutdown drains-or-abandons deterministically and logs the count."""

    async def test_never_started_queue_is_fully_abandoned_and_released(self) -> None:
        detector = SyntheticSlowDetector()
        leases: dict[uuid.UUID, str] = {}
        worker = DeferredDetectorWorker(
            detector,
            on_detections=_never,
            on_window_admitted=lambda w, window: leases.__setitem__(window.window_id, w.plugin_id),
            on_window_done=lambda w, window: leases.pop(window.window_id, None),
        )
        worker.state = "ok"  # bypass start(): no consumer task, so nothing can be processed
        for _ in range(3):
            worker.offer(make_window())
        assert len(leases) == 3

        await worker.stop()

        assert worker.items_abandoned_on_shutdown == 3
        assert leases == {}, "abandoned windows must still release their lease"
        assert worker.queue.qsize() == 0

    async def test_backlog_drains_within_the_timeout_and_reports_zero_abandoned(self) -> None:
        detector = SyntheticSlowDetector(sleep_s=0.02)
        worker = DeferredDetectorWorker(
            detector, on_detections=_never, shutdown_drain_timeout_s=2.0
        )
        context = DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        assert await worker.start(context)
        for _ in range(3):
            worker.offer(make_window())

        await worker.stop()

        assert worker.windows_analysed == 3
        assert worker.items_abandoned_on_shutdown == 0

    async def test_slow_backlog_is_abandoned_past_the_drain_timeout(self) -> None:
        detector = SyntheticSlowDetector(sleep_s=0.3)
        worker = DeferredDetectorWorker(
            detector, on_detections=_never, shutdown_drain_timeout_s=0.05
        )
        context = DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        assert await worker.start(context)
        for _ in range(5):
            worker.offer(make_window())
        # Let the worker pick up exactly the first item before we cut the drain short.
        await asyncio.sleep(0.02)

        await worker.stop()

        # The in-flight item finishes (it cannot be interrupted); the rest are abandoned.
        assert worker.windows_analysed == 1
        assert worker.items_abandoned_on_shutdown == 4
        assert worker.queue.qsize() == 0


class TestDetectionsStillFlowThroughDeferredPath:
    async def test_a_deferred_detection_is_still_emitted(self) -> None:
        detector = SyntheticSlowDetector(sleep_s=0.0, emit=True)
        emitted: list[list[NativeDetection]] = []

        async def collect(_w, _window, detections) -> None:
            emitted.append(detections)

        worker = DeferredDetectorWorker(detector, on_detections=collect)
        context = DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        assert await worker.start(context)
        try:
            worker.offer(make_window())
            await worker.queue.join()
            assert len(emitted) == 1
            assert emitted[0][0].label == "synthetic"
        finally:
            await worker.stop()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
