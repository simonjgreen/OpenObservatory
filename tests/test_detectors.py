"""Detector behaviour tests, including the fixture self-tests the spec requires.

Each detector is checked against signals it *should* fire on and signals it
*should not*. The negative cases matter more: a detector that fires on silence or
on broadband noise would fill the database with confident nonsense.
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest

from open_observatory.audio.contracts import NS_PER_S, AudioWindow, WindowSpec
from open_observatory.detectors.activity import ActivityDetector
from open_observatory.detectors.base import DetectorContext, DetectorUnavailable, DetectorWorker
from open_observatory.detectors.birdnet import BirdNetDetector, birdnet_week, parse_label
from open_observatory.detectors.ultrasonic import UltrasonicDetector, frequency_hint


def make_window(pcm: np.ndarray, rate: int, spec: WindowSpec, *, start_frame: int = 0) -> AudioWindow:
    duration_ns = int(pcm.shape[0] * NS_PER_S / rate)
    utc0 = 1_700_000_000 * NS_PER_S
    return AudioWindow(
        window_id=uuid.uuid4(),
        stream_id=uuid.uuid4(),
        stream_kind=spec.stream_kind,
        sample_rate=rate,
        start_frame=start_frame,
        end_frame=start_frame + int(pcm.shape[0]),
        native_start_frame=start_frame,
        native_end_frame=start_frame + int(pcm.shape[0]),
        utc_start_ns=utc0,
        utc_end_ns=utc0 + duration_ns,
        monotonic_start_ns=0,
        pcm=np.ascontiguousarray(pcm, dtype=np.float32),
        spec=spec,
        created_monotonic_ns=0,
    )


def chirp(rate: int, duration_s: float, f0: float, f1: float, amplitude: float = 0.3) -> np.ndarray:
    n = int(rate * duration_s)
    t = np.arange(n) / rate
    freq = f0 + (f1 - f0) * (t / max(duration_s, 1e-9))
    envelope = np.sin(np.pi * np.clip(t / duration_s, 0, 1)) ** 1.5
    return (amplitude * envelope * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class TestActivityDetector:
    RATE = 48000

    async def _detector(self) -> ActivityDetector:
        # Deliberately no min_snr_db override: these tests must exercise the
        # shipped default, which is calibrated against measured noise.
        detector = ActivityDetector(sample_rate=self.RATE)
        await detector.initialise(
            DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        )
        return detector

    async def _settle_noise_floor(self, detector: ActivityDetector, rng) -> None:
        """Feed quiet noise so the adaptive floor is established first."""
        for _ in range(6):
            quiet = rng.normal(0, 0.002, self.RATE).astype(np.float32)
            await detector.analyse(make_window(quiet, self.RATE, detector.window_spec))

    async def test_silence_produces_nothing(self) -> None:
        detector = await self._detector()
        for _ in range(5):
            window = make_window(np.zeros(self.RATE, dtype=np.float32), self.RATE, detector.window_spec)
            assert await detector.analyse(window) == []

    async def test_quiet_noise_produces_nothing(self) -> None:
        detector = await self._detector()
        rng = np.random.default_rng(7)
        detections: list = []
        for _ in range(10):
            quiet = rng.normal(0, 0.002, self.RATE).astype(np.float32)
            detections += await detector.analyse(
                make_window(quiet, self.RATE, detector.window_spec)
            )
        assert detections == [], "stationary noise must not read as acoustic events"

    async def test_chirp_is_detected_with_plausible_frequency(self) -> None:
        detector = await self._detector()
        rng = np.random.default_rng(3)
        await self._settle_noise_floor(detector, rng)

        signal = rng.normal(0, 0.002, self.RATE).astype(np.float32)
        call = chirp(self.RATE, 0.25, 3000, 5000, amplitude=0.4)
        signal[8000 : 8000 + call.shape[0]] += call
        found = await detector.analyse(
            make_window(signal, self.RATE, detector.window_spec, start_frame=self.RATE * 10)
        )
        assert found, "a loud 3-5 kHz chirp above the noise floor must be detected"
        best = max(found, key=lambda d: d.score)
        assert 2000 < (best.peak_frequency_hz or 0) < 7000
        assert 0.0 <= best.score <= 1.0
        assert best.offset_start_s == pytest.approx(8000 / self.RATE, abs=0.08)

    async def test_never_emits_taxonomy(self) -> None:
        """ADR-010: this detector must not name an organism."""
        detector = await self._detector()
        rng = np.random.default_rng(11)
        await self._settle_noise_floor(detector, rng)
        signal = rng.normal(0, 0.002, self.RATE).astype(np.float32)
        signal[5000:12000] += chirp(self.RATE, 7000 / self.RATE, 4000, 4200, amplitude=0.5)
        for detection in await detector.analyse(
            make_window(signal, self.RATE, detector.window_spec, start_frame=self.RATE * 10)
        ):
            assert detection.common_name is None
            assert detection.scientific_name is None
            assert detection.rank is None
            assert detection.taxonomic_group == "acoustic_event"
            assert detection.calibrated_probability is None

    async def test_out_of_band_energy_is_ignored(self) -> None:
        """A 200 Hz rumble is below the configured band and must not fire."""
        detector = ActivityDetector(sample_rate=self.RATE, band_hz=(1200.0, 11000.0))
        await detector.initialise(
            DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        )
        rng = np.random.default_rng(5)
        await self._settle_noise_floor(detector, rng)
        t = np.arange(self.RATE) / self.RATE
        rumble = (0.5 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
        found = await detector.analyse(
            make_window(rumble, self.RATE, detector.window_spec, start_frame=self.RATE * 10)
        )
        assert found == []

    async def test_health_reports_floor_progress(self) -> None:
        detector = await self._detector()
        health = await detector.health()
        assert health.available
        await detector.analyse(
            make_window(np.zeros(self.RATE, dtype=np.float32), self.RATE, detector.window_spec)
        )
        assert (await detector.health()).state == "ok"


class TestUltrasonicDetector:
    RATE = 384000

    async def _detector(self, rate: int | None = None) -> UltrasonicDetector:
        detector = UltrasonicDetector(native_sample_rate=rate or self.RATE)
        await detector.initialise(
            DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        )
        return detector

    async def test_unavailable_below_the_useful_rate(self) -> None:
        """A 48 kHz stream cannot contain bat calls; saying so beats guessing."""
        detector = UltrasonicDetector(native_sample_rate=48000)
        with pytest.raises(DetectorUnavailable, match="at least"):
            await detector.initialise(
                DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
            )

    async def test_pulse_train_is_detected(self) -> None:
        detector = await self._detector()
        rng = np.random.default_rng(2)
        signal = rng.normal(0, 0.0005, self.RATE * 2).astype(np.float32)
        # Eight downward-sweeping 4 ms pulses at 90 ms intervals, ~45 kHz.
        for index in range(8):
            begin = int(0.2 * self.RATE) + index * int(0.09 * self.RATE)
            pulse = chirp(self.RATE, 0.004, 55_000, 40_000, amplitude=0.5)
            signal[begin : begin + pulse.shape[0]] += pulse
        found = await detector.analyse(make_window(signal, self.RATE, detector.window_spec))
        assert found, "a coherent ultrasonic pulse train must be detected"
        best = found[0]
        assert best.taxonomic_group == "bat"
        assert best.native_result["pulse_count"] >= 3
        assert 35_000 < float(best.native_result["median_peak_hz"]) < 60_000

    async def test_a_single_click_is_not_a_pass(self) -> None:
        detector = await self._detector()
        rng = np.random.default_rng(4)
        signal = rng.normal(0, 0.0005, self.RATE * 2).astype(np.float32)
        pulse = chirp(self.RATE, 0.004, 55_000, 40_000, amplitude=0.6)
        signal[10_000 : 10_000 + pulse.shape[0]] += pulse
        assert await detector.analyse(make_window(signal, self.RATE, detector.window_spec)) == []

    async def test_audible_only_signal_is_ignored(self) -> None:
        detector = await self._detector()
        rng = np.random.default_rng(6)
        signal = rng.normal(0, 0.0005, self.RATE * 2).astype(np.float32)
        t = np.arange(signal.shape[0]) / self.RATE
        signal += (0.4 * np.sin(2 * np.pi * 4000 * t)).astype(np.float32)
        assert await detector.analyse(make_window(signal, self.RATE, detector.window_spec)) == []

    async def test_never_claims_a_species(self) -> None:
        detector = await self._detector()
        rng = np.random.default_rng(8)
        signal = rng.normal(0, 0.0005, self.RATE * 2).astype(np.float32)
        for index in range(8):
            begin = int(0.2 * self.RATE) + index * int(0.09 * self.RATE)
            pulse = chirp(self.RATE, 0.004, 50_000, 42_000, amplitude=0.5)
            signal[begin : begin + pulse.shape[0]] += pulse
        for detection in await detector.analyse(make_window(signal, self.RATE, detector.window_spec)):
            assert detection.scientific_name is None
            assert detection.rank is None
            assert detection.native_result["hint_is_not_identification"] is True

    def test_frequency_hints_cover_uk_bands(self) -> None:
        assert "pipistrellus" in (frequency_hint(45_000) or "").lower()
        assert "pygmaeus" in (frequency_hint(55_000) or "").lower()
        assert frequency_hint(1_000) is None


class TestBirdNetAdapter:
    """Pure logic that can be checked without the (unbundled) model assets."""

    def test_week_calculation_matches_birdnets_48_week_year(self) -> None:
        from datetime import UTC, datetime

        assert birdnet_week(datetime(2026, 1, 1, tzinfo=UTC)) == 1
        assert birdnet_week(datetime(2026, 1, 31, tzinfo=UTC)) == 4
        assert birdnet_week(datetime(2026, 2, 1, tzinfo=UTC)) == 5
        assert birdnet_week(datetime(2026, 12, 31, tzinfo=UTC)) == 48

    def test_label_parsing(self) -> None:
        assert parse_label("Erithacus rubecula_European Robin") == (
            "Erithacus rubecula",
            "European Robin",
        )
        assert parse_label("Engine") == (None, "Engine")

    async def test_missing_assets_report_unavailable_not_crash(self, tmp_path) -> None:
        """ADR-006: a checkout with no model assets must degrade, not fail."""
        detector = BirdNetDetector(model_dir=tmp_path / "absent")
        assert set(detector.missing_assets()) == {
            "birdnet.tflite",
            "birdnet_mdata.tflite",
            "birdnet_labels.txt",
        }
        with pytest.raises(DetectorUnavailable, match="oo models fetch"):
            await detector.initialise(
                DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
            )
        health = await detector.health()
        assert not health.available
        assert health.state == "unavailable"

    def test_plausibility_bands_raise_the_bar_for_implausible_species(self) -> None:
        detector = BirdNetDetector(model_dir="/nonexistent")
        common = detector._band_for(0.4)
        uncommon = detector._band_for(0.05)
        absent = detector._band_for(0.001)
        assert common[0] == "in_range"
        assert uncommon[0] == "uncommon"
        assert absent[0] == "out_of_range"
        # A species that should not be here must clear a strictly higher bar.
        assert common[1] < uncommon[1] < absent[1]

    def test_no_range_model_means_no_invented_prior(self) -> None:
        detector = BirdNetDetector(model_dir="/nonexistent")
        band, threshold = detector._band_for(None)
        assert band == "unfiltered"
        assert threshold == detector._thresholds["in_range"]

    def test_licence_metadata_is_declared(self) -> None:
        metadata = BirdNetDetector.metadata
        assert "NC" in metadata.licence_name  # non-commercial terms must be visible
        assert metadata.licence_url
        assert metadata.calibrated is False
        assert metadata.external_network == "none"


class TestDetectorWorker:
    """The worker's job is to protect capture from detectors."""

    class _Slow:
        metadata = ActivityDetector.metadata
        window_spec = WindowSpec(
            stream_kind="audible48",
            sample_rate=48000,
            duration_s=1.0,
            stride_s=0.5,
            max_delivery_latency_s=0.0,  # everything is immediately "too old"
        )

        async def initialise(self, context: DetectorContext) -> None:
            return None

        async def analyse(self, window: AudioWindow) -> list:
            raise AssertionError("must not be called: the window was stale")

        async def health(self):
            from open_observatory.audio.contracts import DetectorHealth

            return DetectorHealth(available=True, state="ok")

        async def shutdown(self) -> None:
            return None

    async def test_full_queue_drops_rather_than_blocking(self) -> None:
        detector = ActivityDetector(sample_rate=48000)

        async def never(*_args) -> None:
            return None

        worker = DetectorWorker(detector, queue_depth=2, on_detections=never)
        worker.state = "ok"  # bypass start(), no event loop task wanted here
        window = make_window(np.zeros(48000, dtype=np.float32), 48000, detector.window_spec)
        assert worker.offer(window) is True
        assert worker.offer(window) is True
        assert worker.offer(window) is False
        assert worker.windows_dropped_queue_full == 1

    async def test_unavailable_worker_refuses_windows(self) -> None:
        detector = ActivityDetector(sample_rate=48000)

        async def never(*_args) -> None:
            return None

        worker = DetectorWorker(detector, on_detections=never)
        worker.state = "unavailable"
        window = make_window(np.zeros(48000, dtype=np.float32), 48000, detector.window_spec)
        assert worker.offer(window) is False

    async def test_stale_windows_are_dropped_not_analysed(self) -> None:
        """Lag must be reported as lag, not as detections timestamped in the past."""
        released: list = []

        async def collect(*args) -> None:
            released.append(args)

        worker = DetectorWorker(
            self._Slow(),
            on_detections=collect,
            on_window_done=lambda *_: None,
        )
        started = await worker.start(
            DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        )
        assert started
        window = make_window(
            np.zeros(48000, dtype=np.float32), 48000, self._Slow.window_spec
        )
        await worker._process(window)
        assert worker.windows_dropped_stale == 1
        assert released == []
        await worker.stop()

    async def test_window_done_fires_for_quiet_windows(self) -> None:
        """The lease-leak regression: releases must not depend on finding anything."""
        detector = ActivityDetector(sample_rate=48000)
        done: list[str] = []

        async def never(*_args) -> None:
            return None

        worker = DetectorWorker(
            detector,
            on_detections=never,
            on_window_done=lambda w, _window: done.append(w.plugin_id),
        )
        assert await worker.start(
            DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
        )
        window = make_window(np.zeros(48000, dtype=np.float32), 48000, detector.window_spec)
        assert worker.offer(window)
        await worker.queue.join()
        await worker.stop()
        assert done == ["activity-v1"], "silence must still release the window"
