"""Tests for segmentation, normalisation, clip policy and the replay sources.

The replay source is what makes these possible without a microphone, which is
exactly why the audio pipeline spec makes it mandatory rather than optional.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from itertools import pairwise
from types import SimpleNamespace

import numpy as np
import pytest

from open_observatory.audio.contracts import (
    NS_PER_S,
    AudioWindow,
    DetectorMetadata,
    DiscontinuityReason,
    NativeDetection,
    WindowSpec,
)
from open_observatory.audio.replay_source import ReplaySource, SyntheticSource
from open_observatory.clips import ClipManager, _slug
from open_observatory.normaliser import ClaimViolation, Normaliser
from open_observatory.segmenter import StreamSegmenter, TransientAssetStore, WindowRouter

AUDIBLE = WindowSpec(stream_kind="audible48", sample_rate=48000, duration_s=1.0, stride_s=0.5)


def make_metadata(plugin_id: str, *, calibrated: bool = False) -> DetectorMetadata:
    return DetectorMetadata(
        plugin_id=plugin_id,
        plugin_version="1.0.0",
        model_id="test",
        model_version="1",
        model_sha256=None,
        taxonomy_version=None,
        licence_name="Apache-2.0",
        licence_url=None,
        claim="test detector",
        calibrated=calibrated,
    )


class TestSegmenter:
    def _segmenter(self, native_rate: int = 384000) -> StreamSegmenter:
        return StreamSegmenter(
            AUDIBLE, stream_id=uuid.uuid4(), sample_rate=48000, native_rate=native_rate
        )

    def test_windows_have_exact_frame_bounds_and_stride(self) -> None:
        segmenter = self._segmenter()
        windows = segmenter.push(np.zeros(48000 * 3, dtype=np.float32), 0, 0, 0)
        # 3 s of audio, 1 s windows on a 0.5 s stride -> starts at 0, 0.5 ... 2.0
        assert [w.start_frame for w in windows] == [0, 24000, 48000, 72000, 96000]
        for window in windows:
            assert window.end_frame - window.start_frame == 48000
            assert window.pcm.shape[0] == 48000

    def test_native_frame_mapping_targets_the_authoritative_stream(self) -> None:
        segmenter = self._segmenter(native_rate=384000)
        windows = segmenter.push(np.zeros(48000 * 2, dtype=np.float32), 0, 0, 0)
        window = windows[1]
        assert window.native_start_frame == window.start_frame * 8
        assert window.native_end_frame == window.end_frame * 8

    def test_utc_bounds_come_from_frame_index(self) -> None:
        segmenter = self._segmenter()
        utc0 = 1_700_000_000 * NS_PER_S
        windows = segmenter.push(np.zeros(48000 * 2, dtype=np.float32), 0, utc0, 0)
        for window in windows:
            expected = utc0 + window.start_frame * NS_PER_S // 48000
            assert window.utc_start_ns == expected
            assert window.utc_end_ns - window.utc_start_ns == NS_PER_S

    def test_windows_are_contiguous_across_pushes(self) -> None:
        """The stride continues over a push boundary rather than restarting.

        One second of audio yields exactly one 1 s window (at frame 0) and leaves
        the 0.5 s of overlap buffered; the next push must resume from there, so the
        second push's first window starts at the stride, not at its own first frame.
        """
        segmenter = self._segmenter()
        first = segmenter.push(np.zeros(48000, dtype=np.float32), 0, 0, 0)
        second = segmenter.push(np.zeros(48000, dtype=np.float32), 48000, 0, 0)
        assert [w.start_frame for w in first] == [0]
        assert [w.start_frame for w in second] == [24000, 48000]
        # No frame range is ever emitted twice, and none is skipped.
        starts = [w.start_frame for w in first + second]
        assert starts == sorted(starts)
        assert all(b - a == 24000 for a, b in pairwise(starts))

    def test_discontinuity_drops_the_tail_rather_than_splicing(self) -> None:
        """A window spanning a gap would read to a detector as one continuous call."""
        segmenter = self._segmenter()
        segmenter.push(np.zeros(30000, dtype=np.float32), 0, 0, 0)  # partial, buffered
        windows = segmenter.push(
            np.zeros(30000, dtype=np.float32), 90000, 0, 0, discontinuous=True
        )
        # The buffered 30000 frames were discarded, so nothing completes yet.
        assert windows == []
        assert segmenter.stats.windows_emitted == 0

    def test_window_pcm_is_immutable(self) -> None:
        segmenter = self._segmenter()
        window = segmenter.push(np.ones(48000, dtype=np.float32), 0, 0, 0)[0]
        with pytest.raises(ValueError):
            window.pcm[0] = 2.0

    def test_spec_rate_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Hz"):
            StreamSegmenter(AUDIBLE, stream_id=uuid.uuid4(), sample_rate=96000, native_rate=96000)


class TestWindowRouter:
    def test_identical_specs_share_one_segmenter(self) -> None:
        router = WindowRouter(native_rate=48000, stream_id=uuid.uuid4())
        router.register(AUDIBLE, "a", sample_rate=48000)
        router.register(AUDIBLE, "b", sample_rate=48000)
        assert len(router.snapshot()) == 1
        assert router.snapshot()[0]["consumers"] == ["a", "b"]

    def test_each_window_is_offered_to_every_consumer(self) -> None:
        router = WindowRouter(native_rate=48000, stream_id=uuid.uuid4())
        router.register(AUDIBLE, "a", sample_rate=48000)
        router.register(AUDIBLE, "b", sample_rate=48000)
        seen: list[tuple[int, list[str]]] = []
        router.push(
            "audible48",
            np.zeros(48000, dtype=np.float32),
            0,
            0,
            0,
            on_window=lambda window, consumers: seen.append((window.start_frame, consumers)),
        )
        assert seen
        assert all(consumers == ["a", "b"] for _, consumers in seen)

    def test_streams_of_a_different_kind_are_not_routed(self) -> None:
        router = WindowRouter(native_rate=384000, stream_id=uuid.uuid4())
        router.register(AUDIBLE, "a", sample_rate=48000)
        calls: list[AudioWindow] = []
        router.push(
            "native",
            np.zeros(384000, dtype=np.float32),
            0,
            0,
            0,
            on_window=lambda window, _consumers: calls.append(window),
        )
        assert calls == []


class TestLeases:
    def test_grant_and_release_balance(self) -> None:
        store = TransientAssetStore()
        window_id = uuid.uuid4()
        store.grant(window_id, "plugin")
        assert store.snapshot()["outstanding"] == 1
        store.release(window_id, "plugin")
        assert store.snapshot()["outstanding"] == 0
        assert store.released == 1

    def test_expiry_is_swept_and_counted(self) -> None:
        store = TransientAssetStore(default_lease_s=0.0)
        store.grant(uuid.uuid4(), "plugin", lease_s=-1.0)
        assert store.sweep() == 1
        assert store.expired == 1


class TestNormaliser:
    def _window(self, *, rate: int = 48000, start_frame: int = 48000) -> AudioWindow:
        utc0 = 1_700_000_000 * NS_PER_S
        return AudioWindow(
            window_id=uuid.uuid4(),
            stream_id=uuid.uuid4(),
            stream_kind="audible48",
            sample_rate=rate,
            start_frame=start_frame,
            end_frame=start_frame + rate,
            native_start_frame=start_frame * 8,
            native_end_frame=(start_frame + rate) * 8,
            utc_start_ns=utc0,
            utc_end_ns=utc0 + NS_PER_S,
            monotonic_start_ns=0,
            pcm=np.zeros(rate, dtype=np.float32),
            spec=AUDIBLE,
            created_monotonic_ns=0,
        )

    def test_offsets_become_native_frames(self) -> None:
        normaliser = Normaliser()
        window = self._window(start_frame=48000)
        detection = NativeDetection(offset_start_s=0.25, offset_end_s=0.75, score=0.9, label="x")
        result = normaliser.normalise(
            make_metadata("birdnet-v2.4"), window, detection, native_sample_rate=384000
        )
        assert result is not None
        # 48000 + 0.25 s at 48 kHz = frame 60000 in the derived stream, x8 native.
        assert result.source_start_frame == 60000 * 8
        assert result.source_end_frame == 84000 * 8

    def test_utc_bounds_follow_the_window(self) -> None:
        normaliser = Normaliser()
        window = self._window()
        detection = NativeDetection(offset_start_s=0.5, offset_end_s=1.0, score=0.5, label="x")
        result = normaliser.normalise(
            make_metadata("birdnet-v2.4"), window, detection, native_sample_rate=48000
        )
        assert result is not None
        assert result.event_start_utc.timestamp() == pytest.approx(1_700_000_000.5, abs=1e-6)
        assert result.duration_s == pytest.approx(0.5, abs=1e-6)

    def test_activity_detector_cannot_emit_a_species(self) -> None:
        """ADR-010 enforced in code, not just written down."""
        normaliser = Normaliser()
        window = self._window()
        offender = NativeDetection(
            offset_start_s=0.0,
            offset_end_s=0.5,
            score=0.9,
            label="acoustic event",
            common_name="European Robin",
        )
        with pytest.raises(ClaimViolation, match="not permitted"):
            normaliser.normalise(
                make_metadata("activity-v1"), window, offender, native_sample_rate=48000
            )
        assert normaliser.stats.claim_violations == 1

    def test_uncalibrated_detector_cannot_claim_a_probability(self) -> None:
        normaliser = Normaliser()
        window = self._window()
        offender = NativeDetection(
            offset_start_s=0.0,
            offset_end_s=0.5,
            score=0.9,
            label="x",
            calibrated_probability=0.9,
        )
        with pytest.raises(ClaimViolation, match="calibrated"):
            normaliser.normalise(
                make_metadata("birdnet-v2.4", calibrated=False),
                window,
                offender,
                native_sample_rate=48000,
            )

    def test_overlapping_duplicate_is_suppressed(self) -> None:
        normaliser = Normaliser()
        metadata = make_metadata("birdnet-v2.4")
        first = self._window(start_frame=0)
        second = self._window(start_frame=24000)  # 50% overlapping window
        detection = NativeDetection(offset_start_s=0.5, offset_end_s=1.0, score=0.8, label="robin")
        assert normaliser.normalise(metadata, first, detection, native_sample_rate=48000)
        again = NativeDetection(offset_start_s=0.0, offset_end_s=0.5, score=0.8, label="robin")
        assert normaliser.normalise(metadata, second, again, native_sample_rate=48000) is None
        assert normaliser.stats.duplicates_suppressed == 1

    def test_different_labels_are_not_duplicates(self) -> None:
        normaliser = Normaliser()
        metadata = make_metadata("birdnet-v2.4")
        window = self._window()
        a = NativeDetection(offset_start_s=0.0, offset_end_s=1.0, score=0.8, label="robin")
        b = NativeDetection(offset_start_s=0.0, offset_end_s=1.0, score=0.7, label="wren")
        assert normaliser.normalise(metadata, window, a, native_sample_rate=48000)
        assert normaliser.normalise(metadata, window, b, native_sample_rate=48000)

    def test_taxon_id_only_asserted_for_species_rank(self) -> None:
        normaliser = Normaliser()
        window = self._window()
        species = NativeDetection(
            offset_start_s=0.0,
            offset_end_s=1.0,
            score=0.8,
            label="x",
            rank="species",
            scientific_name="Erithacus rubecula",
            taxonomic_group="bird",
        )
        result = normaliser.normalise(
            make_metadata("birdnet-v2.4"), window, species, native_sample_rate=48000
        )
        assert result is not None
        assert result.canonical_taxon_id == "sci:erithacus_rubecula"

        event = NativeDetection(
            offset_start_s=2.0, offset_end_s=3.0, score=0.8, label="e", taxonomic_group="acoustic_event"
        )
        other = normaliser.normalise(
            make_metadata("activity-v1"), window, event, native_sample_rate=48000
        )
        assert other is not None
        assert other.canonical_taxon_id is None

    def test_bat_pass_gets_a_title_hint_but_no_taxonomic_claim(self) -> None:
        """A bat pass may get a presentational title_hint, but the candidate name
        must never leak into common_name/scientific_name/canonical_taxon_id/rank —
        those stay exactly as the ultrasonic detector (deliberately) left them."""
        normaliser = Normaliser()
        window = self._window()
        pass_ = NativeDetection(
            offset_start_s=0.0,
            offset_end_s=1.0,
            score=0.8,
            label="bat pass",
            rank=None,
            taxonomic_group="bat",
            peak_frequency_hz=45_000.0,
            native_result={"detector": "ultrasonic-pass-v1"},
        )
        result = normaliser.normalise(
            make_metadata("ultrasonic-pass-v1"), window, pass_, native_sample_rate=384000
        )
        assert result is not None
        assert result.title_hint is not None
        assert result.common_name is None
        assert result.scientific_name is None
        assert result.canonical_taxon_id is None
        assert result.rank is None

    def test_non_bat_detection_gets_no_title_hint(self) -> None:
        normaliser = Normaliser()
        window = self._window()
        bird = NativeDetection(
            offset_start_s=0.0,
            offset_end_s=1.0,
            score=0.8,
            label="x",
            common_name="European Robin",
            taxonomic_group="bird",
        )
        result = normaliser.normalise(
            make_metadata("birdnet-v2.4"), window, bird, native_sample_rate=48000
        )
        assert result is not None
        assert result.title_hint is None

    def test_claim_violation_still_fires_alongside_title_hint_support(self) -> None:
        """Adding title_hint must not weaken the ADR-010 guard: a non-taxonomic
        plugin (e.g. the ultrasonic detector) still cannot emit a species name."""
        normaliser = Normaliser()
        window = self._window()
        offender = NativeDetection(
            offset_start_s=0.0,
            offset_end_s=0.5,
            score=0.9,
            label="acoustic event",
            common_name="European Robin",
        )
        with pytest.raises(ClaimViolation, match="not permitted"):
            normaliser.normalise(
                make_metadata("activity-v1"), window, offender, native_sample_rate=48000
            )


class TestClipPolicy:
    def _manager(self, tmp_path, **kwargs) -> ClipManager:
        defaults = dict(
            clip_dir=tmp_path / "clips",
            clip_plugins=("birdnet-v2.4",),
            min_score=0.25,
            max_per_minute=3,
        )
        defaults.update(kwargs)
        return ClipManager(**defaults)  # type: ignore[arg-type]

    def test_only_configured_plugins_are_clipped(self, tmp_path) -> None:
        manager = self._manager(tmp_path)
        assert manager.admits("birdnet-v2.4", 0.9)[0] is True
        admitted, reason = manager.admits("activity-v1", 0.9)
        assert admitted is False
        assert "clip_plugins" in reason
        assert manager.stats.skipped_plugin_not_clipped == 1

    def test_low_scores_are_refused(self, tmp_path) -> None:
        manager = self._manager(tmp_path)
        admitted, reason = manager.admits("birdnet-v2.4", 0.1)
        assert admitted is False
        assert "below" in reason

    def test_rate_limit_caps_writes(self, tmp_path) -> None:
        manager = self._manager(tmp_path, max_per_minute=3)
        for _ in range(3):
            assert manager.admits("birdnet-v2.4", 0.9)[0]
            manager._recent_writes.append(__import__("time").monotonic())
        admitted, reason = manager.admits("birdnet-v2.4", 0.9)
        assert admitted is False
        assert "rate limit" in reason

    def test_disk_reserve_stops_writes(self, tmp_path) -> None:
        # An impossible reserve stands in for a nearly-full disk.
        manager = self._manager(tmp_path, min_free_bytes=1 << 62)
        admitted, reason = manager.admits("birdnet-v2.4", 0.9)
        assert admitted is False
        assert "free" in reason
        assert manager.stats.skipped_disk_guard == 1

    def test_retention_deletes_over_budget_oldest_first(self, tmp_path) -> None:
        manager = self._manager(tmp_path, max_total_bytes=2048, retention_days=0)
        day = manager.clip_dir / "2026-08-04"
        day.mkdir(parents=True)
        import os
        import time

        for index in range(5):
            path = day / f"clip{index}.wav"
            path.write_bytes(b"\x00" * 1024)
            os.utime(path, (time.time() - (5 - index) * 100,) * 2)
        result = manager.enforce_retention()
        assert result["budget_deleted"] == 3
        remaining = sorted(p.name for p in day.glob("*.wav"))
        assert remaining == ["clip3.wav", "clip4.wav"]

    def test_labels_from_models_are_made_filesystem_safe(self) -> None:
        assert _slug("../../etc/passwd") == "etc-passwd"
        assert _slug("Erithacus rubecula_European Robin") == "erithacus-rubecula-european-robin"
        assert _slug("") == "event"
        assert "/" not in _slug("a/b/c")


class TestSyntheticSource:
    async def test_step_mode_is_deterministic(self) -> None:
        source = SyntheticSource(scene="tone", sample_rate=48000, block_ms=100, mode="step")
        await source.open()
        await source.step(1)
        block = await source.read()
        assert block is not None
        assert block.frame_count == 4800
        assert block.first_frame == 0
        assert block.discontinuity == DiscontinuityReason.STREAM_START
        await source.close()

    async def test_frames_and_timestamps_advance_together(self) -> None:
        source = SyntheticSource(scene="tone", sample_rate=48000, block_ms=100, mode="accelerated")
        info = await source.open()
        previous_end = info.started_monotonic_ns
        for index in range(20):
            block = await source.read()
            assert block is not None
            assert block.first_frame == index * 4800
            assert block.monotonic_start_ns == previous_end
            previous_end = block.monotonic_end_ns
        await source.close()

    async def test_injected_gap_shows_as_missing_frames(self) -> None:
        source = SyntheticSource(scene="tone", sample_rate=48000, block_ms=100, mode="accelerated")
        await source.open()
        await source.read()
        source.inject_gap(4800)
        block = await source.read()
        assert block is not None
        assert block.missing_frames == 4800
        assert block.discontinuity == DiscontinuityReason.OVERRUN
        # The frame index skips the lost audio rather than pretending it existed.
        assert block.first_frame == 4800 + 4800
        await source.close()

    async def test_bat_scene_is_silent_when_the_rate_cannot_carry_it(self) -> None:
        """Honesty check: no fake ultrasound on a 48 kHz stream."""
        source = SyntheticSource(scene="bat-pass", sample_rate=48000, block_ms=100, mode="accelerated")
        await source.open()
        peak = 0.0
        for _ in range(130):
            block = await source.read()
            assert block is not None
            peak = max(peak, float(np.abs(block.pcm).max()))
        await source.close()
        assert peak < 0.05  # noise floor only

    async def test_bat_scene_present_at_high_rate(self) -> None:
        source = SyntheticSource(scene="bat-pass", sample_rate=384000, block_ms=100, mode="accelerated")
        await source.open()
        peak = 0.0
        for _ in range(130):
            block = await source.read()
            assert block is not None
            peak = max(peak, float(np.abs(block.pcm).max()))
        await source.close()
        assert peak > 0.2


class TestReplaySource:
    async def test_wav_fixture_round_trips(self, tmp_path) -> None:
        import soundfile as sf

        path = tmp_path / "fixture.wav"
        rate = 48000
        t = np.arange(rate) / rate
        sf.write(str(path), (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), rate)

        source = ReplaySource(path, block_ms=100, mode="accelerated", loop=False)
        info = await source.open()
        assert info.fmt.sample_rate == rate
        frames = 0
        while True:
            block = await source.read()
            if block is None:
                break
            frames += block.frame_count
        await source.close()
        assert frames == rate

    async def test_loop_marks_the_wrap_as_a_discontinuity(self, tmp_path) -> None:
        import soundfile as sf

        path = tmp_path / "short.wav"
        sf.write(str(path), np.zeros(4800, dtype=np.float32), 48000)
        source = ReplaySource(path, block_ms=100, mode="accelerated", loop=True)
        await source.open()
        await source.read()
        second = await source.read()
        await source.close()
        assert second is not None
        assert second.discontinuity == DiscontinuityReason.REPLAY_WRAP


class TestEvidenceIsOffTheDetectorPath:
    """Writing evidence must never block the detector that produced it.

    Measured at the development station on a busy night: `_on_detections` awaited clip
    extraction inline, and because an ultrasonic detection writes four clips —
    including a time expansion turning 6 s of 384 kHz audio into ~54 s of output
    — the worker stalled on disk I/O. `ultrasonic-pass-v1` analysed 29 windows
    and dropped 69, with a 42 s lag, while its own inference p95 was 57 ms. The
    detector was missing bats because of file writing.
    """

    async def test_slow_evidence_does_not_stall_the_producer(self) -> None:
        import asyncio

        from open_observatory.config import Settings
        from open_observatory.station import DetectionRecord, Station

        station = Station(Settings(clips_enabled=True))
        started = asyncio.get_running_loop().time()
        attached: list[str] = []

        def slow_attach(record: DetectionRecord, metadata: object) -> None:
            time.sleep(0.25)
            attached.append("done")

        station._attach_evidence = slow_attach  # type: ignore[method-assign]
        station._evidence_task = asyncio.create_task(station._evidence_loop())
        try:
            for _ in range(4):
                station._evidence_queue.put_nowait((object(), object()))  # type: ignore[arg-type]
            # Handing work over must be immediate: the producer is the detector.
            assert asyncio.get_running_loop().time() - started < 0.05
            await asyncio.sleep(0.35)
            assert attached, "evidence work must actually run in the background"
        finally:
            station._evidence_queue.put_nowait(None)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(station._evidence_task, timeout=2.0)

    async def test_full_queue_drops_evidence_rather_than_blocking(self) -> None:
        from open_observatory.config import Settings
        from open_observatory.station import Station

        station = Station(Settings())
        while not station._evidence_queue.full():
            station._evidence_queue.put_nowait((object(), object()))  # type: ignore[arg-type]

        # The queue is bounded, so the next offer must be refused rather than
        # awaited. Capture always wins.
        with pytest.raises(asyncio.QueueFull):
            station._evidence_queue.put_nowait((object(), object()))  # type: ignore[arg-type]


class TestHardwareReturnsAfterFallback:
    """Graceful degradation has to include coming back.

    On 2026-08-08 the AudioMoth's mode switch was moved to USB/OFF, so it stopped
    presenting an ALSA card. `auto` correctly fell back to the synthetic scene and
    correctly reported itself degraded — and then never looked again, because the
    synthetic source never ends and the capture supervisor only rebuilds once a
    source has ended. Reattaching the microphone did nothing until the service was
    restarted by hand.
    """

    async def test_no_watch_when_already_on_hardware(self) -> None:
        from open_observatory.audio.contracts import SourceKind
        from open_observatory.config import Settings
        from open_observatory.station import Station

        station = Station(Settings(source="auto"))
        info = SimpleNamespace(source_kind=SourceKind.ALSA)

        assert station._start_hardware_watch(object(), info) is None

    async def test_no_watch_when_synthetic_was_chosen_deliberately(self) -> None:
        from open_observatory.audio.contracts import SourceKind
        from open_observatory.config import Settings
        from open_observatory.station import Station

        # An operator who asked for synthetic must not be switched to hardware
        # underneath them.
        station = Station(Settings(source="synthetic"))
        info = SimpleNamespace(source_kind=SourceKind.SYNTHETIC)

        assert station._start_hardware_watch(object(), info) is None

    async def test_returning_device_ends_the_synthetic_source(self, monkeypatch) -> None:
        from open_observatory import station as station_module
        from open_observatory.config import Settings
        from open_observatory.station import Station

        station = Station(Settings(source="auto", hardware_recheck_s=0.01))
        station._running = True
        closed = asyncio.Event()

        class FakeSource:
            async def close(self) -> None:
                closed.set()

        device = SimpleNamespace(stable_device_key="usb-16d0:06f3:0384_X", card_name="AudioMoth")
        calls = {"n": 0}

        def fake_find_device(key):
            calls["n"] += 1
            # Absent for the first couple of probes, then reattached.
            return device if calls["n"] >= 2 else None

        monkeypatch.setattr(station_module, "find_device", fake_find_device)

        task = asyncio.create_task(station._hardware_watch_loop(FakeSource()))
        await asyncio.wait_for(closed.wait(), timeout=2.0)
        # The loop returns of its own accord once it has handed control back, so
        # await it rather than cancelling: a cancel here would leave the in-flight
        # probe thread dangling and say nothing about whether the loop terminates.
        await asyncio.wait_for(task, timeout=2.0)

        assert calls["n"] >= 2, "must keep probing until the device reappears"


class TestGapsAreSplitByWhetherAudioWasLost:
    """`grep -c capture.gap` counts two different events and always has.

    Many gap records carry `missing_frames=0`: ALSA reported an overrun but frame
    accounting shows nothing was actually lost. A smaller number lose real audio —
    measured at the development station on 2026-08-08, 9 of 24 gap lines in 45 minutes, for
    1.16 s of recording. Counting log lines overstated the damage by 2.7x, and the
    health endpoint could not tell the operator which was which.
    """

    def _block(self, missing_frames: int):
        import numpy as np

        from open_observatory.audio.contracts import (
            CaptureBlock,
            ClockCorrelation,
            DiscontinuityReason,
        )

        return CaptureBlock(
            stream_id=uuid.uuid4(),
            sequence=0,
            first_frame=0,
            sample_rate=384000,
            pcm=np.zeros(8, dtype="float32"),
            monotonic_start_ns=0,
            clock=ClockCorrelation.sample(),
            discontinuity=DiscontinuityReason.OVERRUN,
            missing_frames=missing_frames,
        )

    def _station(self):
        from open_observatory.config import Settings
        from open_observatory.station import Station

        station = Station(Settings())
        # The gap *row* is another agent's territory and needs a database; these
        # tests are about the counters, so keep the write out of the way.
        station._insert_gap_row = lambda block: None  # type: ignore[method-assign]
        return station

    async def test_counters_separate_the_two_kinds(self) -> None:
        station = self._station()
        station._record_gap(self._block(0))
        station._record_gap(self._block(0))
        station._record_gap(self._block(42_505))
        await asyncio.sleep(0.05)  # let the gap-row task finish

        assert station.counters.discontinuities == 3
        assert station.counters.gaps_without_loss == 2
        assert station.counters.gaps_with_loss == 1
        assert station.counters.estimated_missing_frames == 42_505

    async def test_health_reports_the_split_and_the_seconds(self) -> None:
        station = self._station()
        station._record_gap(self._block(0))
        station._record_gap(self._block(38_400))
        await asyncio.sleep(0.05)

        capture = station.status_snapshot()["capture"]
        assert capture["gaps_with_loss"] == 1
        assert capture["gaps_without_loss"] == 1
        # Without a stream there is no rate to divide by, so this stays 0.0
        # rather than guessing one.
        assert capture["estimated_missing_frames"] == 38_400


class TestStreamRowRecordsProgressBeforeItEnds:
    """A crashed process must not leave a row claiming the stream recorded nothing.

    `frame_count` and `discontinuity_count` used to be written only by
    `_close_stream_row`, which runs on a graceful stop. Measured on the station on
    2026-08-08: 48 of 49 `audio_stream` rows carried `frame_count = 0`, the single
    exception being the one stream that ended through the supervisor's own error
    path. Capture coverage computed from those rows reads zero for every session
    that was ever killed or restarted.
    """

    async def test_checkpoint_writes_frames_to_the_open_row(self, settings) -> None:
        from open_observatory.config import Settings
        from open_observatory.station import Station

        assert isinstance(settings, Settings)
        station = Station(settings)
        written: list[tuple] = []
        station._checkpoint_stream_row_blocking = (  # type: ignore[method-assign]
            lambda stream_id, frames, discontinuities: written.append(
                (stream_id, frames, discontinuities)
            )
        )
        station._running = True
        station.stream = SimpleNamespace(stream_id=uuid.uuid4())  # type: ignore[assignment]
        station.counters.frames = 991_488_768
        station.counters.discontinuities = 22

        await station._checkpoint_stream_row()

        assert written == [(station.stream.stream_id, 991_488_768, 22)]

    async def test_checkpoint_is_a_no_op_with_no_open_stream(self, settings) -> None:
        from open_observatory.station import Station

        station = Station(settings)
        station._running = True
        station.stream = None
        # Must not raise, and must not need a database.
        await station._checkpoint_stream_row()

    async def test_a_failing_checkpoint_does_not_propagate(self, settings) -> None:
        from open_observatory.station import Station

        station = Station(settings)

        def boom(*args: object) -> None:
            raise RuntimeError("database is locked")

        station._checkpoint_stream_row_blocking = boom  # type: ignore[method-assign]
        station._running = True
        station.stream = SimpleNamespace(stream_id=uuid.uuid4())  # type: ignore[assignment]

        # Bookkeeping must never be able to take capture down. Capture always wins.
        await station._checkpoint_stream_row()
