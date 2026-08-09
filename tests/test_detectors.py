"""Detector behaviour tests, including the fixture self-tests the spec requires.

Each detector is checked against signals it *should* fire on and signals it
*should not*. The negative cases matter more: a detector that fires on silence or
on broadband noise would fill the database with confident nonsense.
"""

from __future__ import annotations

import math
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

    def test_every_day_of_a_leap_and_a_common_year_lands_in_1_to_48(self) -> None:
        """The re-audit HANDOVER 6.3 item 0 asked for (ADR-044).

        `birdnet_week` is written as `int(day / 7.25) + 1`, which is not the
        form the convention is usually stated in. This asserts it is exactly
        equivalent to the stated rule -- four weeks per calendar month, week 1
        being days 1-7, the fourth absorbing days 22-31 -- for every day of a
        common year and a leap year, and that the result never leaves [1, 48].

        The range matters as much as the mapping: the MData model treats *any*
        week outside 1-48 as "year round", which silently disables seasonality
        rather than failing. Measured at the station's coordinates: Common
        Swift is 0.913 at week 52, 0 or -1, and 0.000 in January.
        """
        import calendar
        from datetime import UTC, datetime

        for year in (2026, 2028):  # a common year and a leap year
            for month in range(1, 13):
                for day in range(1, calendar.monthrange(year, month)[1] + 1):
                    week = birdnet_week(datetime(year, month, day, 12, tzinfo=UTC))
                    assert week == (month - 1) * 4 + min(4, (day - 1) // 7 + 1)
                    assert 1 <= week <= 48

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
        # Measured: a Tawny Owl prior (0.019253) sits above the plausibility
        # floor but below range_threshold (0.03) -- it is "uncommon-and-then-
        # some", not near zero, so it still lands in ``out_of_range`` and
        # merely faces a high bar rather than outright suppression.
        common = detector._band_for(0.4, range_model_loaded=True)
        uncommon = detector._band_for(0.05, range_model_loaded=True)
        absent = detector._band_for(0.019253, range_model_loaded=True)
        assert common[0] == "in_range"
        assert uncommon[0] == "uncommon"
        assert absent[0] == "out_of_range"
        # A species that should not be here must clear a strictly higher bar.
        assert common[1] < uncommon[1] < absent[1]

    def test_no_range_model_means_no_invented_prior(self) -> None:
        """With no range model loaded at all, today's uniform behaviour holds."""
        detector = BirdNetDetector(model_dir="/nonexistent")
        band, threshold = detector._band_for(None, range_model_loaded=False)
        assert band == "unfiltered"
        assert threshold == detector._thresholds["in_range"]

    def test_missing_prior_with_range_model_loaded_gets_the_strict_bar(self) -> None:
        """Defect (b): a species the *loaded* range model is silent about must
        not receive the easiest (in_range) threshold -- it is not an
        endorsement. Measured: Great Horned Owl and Flammulated Owl both took
        this path with occ=None, 202 of 5833 named detections on the live
        station.
        """
        detector = BirdNetDetector(model_dir="/nonexistent")
        band, threshold = detector._band_for(None, range_model_loaded=True)
        assert band == "no_prior"
        assert band != "unfiltered"
        assert threshold != detector._thresholds["in_range"]
        assert threshold == detector._thresholds["out_of_range"]

    def test_near_zero_prior_is_suppressed_outright_flammulated_owl(self) -> None:
        """Defect (a): Flammulated Owl, occurrence 8e-06, score 0.959 on the
        live station -- admitted under the old out_of_range band (0.90). No
        score can be trusted for a species the range model puts at ~0 here
        this week, so this must be unreachable by any score, not just a
        higher one.
        """
        detector = BirdNetDetector(model_dir="/nonexistent")
        band, threshold = detector._band_for(8e-06, range_model_loaded=True)
        assert band == "implausible"
        assert threshold > 0.959
        assert threshold > 0.995  # no realistic score clears it

    def test_tawny_owl_survives_the_floor(self) -> None:
        """The discriminating case: the floor must keep a genuine, seasonally
        uncommon species while rejecting a continentally-absent one. Measured:
        Tawny Owl, occurrence 0.019253, score 0.974 on the live station.
        """
        detector = BirdNetDetector(model_dir="/nonexistent")
        band, threshold = detector._band_for(0.019253, range_model_loaded=True)
        assert band != "implausible"
        assert threshold <= 0.974

    def test_eurasian_jackdaw_unaffected(self) -> None:
        """A genuine, common local species (occurrence 0.772293, score 0.617
        on the live station) must land exactly where it did before this
        change.
        """
        detector = BirdNetDetector(model_dir="/nonexistent")
        band, threshold = detector._band_for(0.772293, range_model_loaded=True)
        assert band == "in_range"
        assert threshold == detector._thresholds["in_range"]
        assert threshold <= 0.617

    def test_plausibility_floor_default_sits_between_the_measured_cases(self) -> None:
        detector = BirdNetDetector(model_dir="/nonexistent")
        assert 1e-05 < detector._plausibility_floor < 0.019253

    def test_a_sound_category_is_exempt_from_the_plausibility_floor(self) -> None:
        """ADR-049. Measured on the live station: the range model returns
        4e-06 for "Engine" and 3e-06 for "Human vocal" -- far below the floor,
        and meaningless, because a car is not a taxon with a distribution.
        Applying the floor to them would have withdrawn 91 of the 114 rows the
        first live dry run proposed to flag.
        """
        detector = BirdNetDetector(model_dir="/nonexistent")
        band, threshold = detector._band_for(
            4e-06, range_model_loaded=True, non_taxonomic=True
        )
        assert band == "non_biological"
        assert threshold == detector._thresholds["in_range"]
        assert threshold < math.inf
        # The very same prior, for something that *is* a species, is still
        # suppressed outright -- the exemption is about the class, not the
        # number.
        assert detector._band_for(4e-06, range_model_loaded=True)[0] == "implausible"

    async def test_analyse_end_to_end_suppresses_and_labels_bands(self) -> None:
        """Wire the fixed ``_band_for`` into ``analyse`` with a stub
        interpreter and a stub range model reproducing the exact measured
        priors, so the whole path -- not just the pure function -- is
        exercised.
        """

        class _StubInterpreter:
            def __init__(self, logits: np.ndarray) -> None:
                self._logits = logits

            def set_tensor(self, index: int, value: object) -> None:
                return None

            def invoke(self) -> None:
                return None

            def get_tensor(self, index: int) -> np.ndarray:
                return self._logits.reshape(1, -1)

        class _StubRange:
            def __init__(self, priors: np.ndarray) -> None:
                self._priors = priors

            def probabilities(self, week: int) -> np.ndarray:
                return self._priors

        import math

        labels = [
            "Otus flammeolus_Flammulated Owl",
            "Strix aluco_Tawny Owl",
            "Coloeus monedula_Eurasian Jackdaw",
        ]
        # sigmoid^-1 of the exact measured scores from the live station.
        scores = [0.959, 0.974, 0.617]
        logits = np.array([math.log(p / (1.0 - p)) for p in scores], dtype=np.float32)
        priors = np.array([8e-06, 0.019253, 0.772293], dtype=np.float32)

        detector = BirdNetDetector(model_dir="/nonexistent", min_confidence=0.1)
        detector._labels = labels
        detector._parsed = [parse_label(label) for label in labels]
        detector._expected_samples = 48000 * 3
        detector._interpreter = _StubInterpreter(logits)
        detector._input_index = 0
        detector._output_index = 0
        detector._range = _StubRange(priors)

        pcm = np.zeros(48000 * 3, dtype=np.float32)
        window = make_window(pcm, 48000, detector.window_spec)

        detections = await detector.analyse(window)

        names = {d.common_name for d in detections}
        assert "Flammulated Owl" not in names
        assert "Tawny Owl" in names
        assert "Eurasian Jackdaw" in names
        assert detector._suppressed_implausible_prior == 1

        tawny = next(d for d in detections if d.common_name == "Tawny Owl")
        assert tawny.native_result["plausibility_band"] == "out_of_range"
        assert tawny.native_result["occurrence_probability"] == pytest.approx(0.019253)

        jackdaw = next(d for d in detections if d.common_name == "Eurasian Jackdaw")
        assert jackdaw.native_result["plausibility_band"] == "in_range"

    async def test_a_near_miss_is_recorded_with_species_score_prior_and_bar(self) -> None:
        """ADR-052, end to end through ``analyse``.

        The operator's actual complaint: birds audible, nothing reported, and
        the station able to say only that it suppressed N candidates. Three
        of these four candidates are refused for three different reasons, and
        exactly one of them -- the Eurasian Blackbird at 0.538 against the
        0.55 in-range bar -- is counted by *none* of ADR-032's four
        suppression counters, because those cover only the plausibility
        bands. It is also the one an operator is most likely to be asking
        about, which is why this assertion is the point of the test.
        """

        class _StubInterpreter:
            def __init__(self, logits: np.ndarray) -> None:
                self._logits = logits

            def set_tensor(self, index: int, value: object) -> None:
                return None

            def invoke(self) -> None:
                return None

            def get_tensor(self, index: int) -> np.ndarray:
                return self._logits.reshape(1, -1)

        class _StubRange:
            def __init__(self, priors: np.ndarray) -> None:
                self._priors = priors

            def probabilities(self, week: int) -> np.ndarray:
                return self._priors

        labels = [
            "Turdus merula_Eurasian Blackbird",  # plausible, just under the bar
            "Otus flammeolus_Flammulated Owl",  # near-zero prior: refused outright
            "Strix aluco_Tawny Owl",  # uncommon band, under its stricter bar
            "Coloeus monedula_Eurasian Jackdaw",  # admitted
        ]
        scores = [0.538, 0.959, 0.700, 0.812]
        logits = np.array([math.log(p / (1.0 - p)) for p in scores], dtype=np.float32)
        priors = np.array([0.9312, 8e-06, 0.05, 0.772293], dtype=np.float32)

        detector = BirdNetDetector(model_dir="/nonexistent", min_confidence=0.1)
        detector._labels = labels
        detector._parsed = [parse_label(label) for label in labels]
        detector._expected_samples = 48000 * 3
        detector._interpreter = _StubInterpreter(logits)
        detector._input_index = 0
        detector._output_index = 0
        detector._range = _StubRange(priors)

        window = make_window(np.zeros(48000 * 3, dtype=np.float32), 48000, detector.window_spec)
        detections = await detector.analyse(window)
        assert {d.common_name for d in detections} == {"Eurasian Jackdaw"}

        snapshot = detector.near_miss_snapshot()
        by_name = {row["common_name"]: row for row in snapshot["species"]}
        assert set(by_name) == {"Eurasian Blackbird", "Flammulated Owl", "Tawny Owl"}

        # The case no existing counter covers.
        blackbird = by_name["Eurasian Blackbird"]
        assert blackbird["band"] == "in_range"
        assert blackbird["best_score"] == pytest.approx(0.538, abs=1e-3)
        assert blackbird["occurrence_probability"] == pytest.approx(0.9312, abs=1e-4)
        assert blackbird["shortfall"] == pytest.approx(0.012, abs=1e-3)
        assert detector._suppressed_implausible_prior == 1
        assert (
            detector._suppressed_uncommon
            + detector._suppressed_out_of_range
            + detector._suppressed_no_prior
            == 1
        )

        # An unreachable bar reports no distance rather than a huge one.
        assert by_name["Flammulated Owl"]["band"] == "implausible"
        assert by_name["Flammulated Owl"]["shortfall"] is None

        # The admitted candidate gives the in_range band a denominator.
        in_range = next(b for b in snapshot["bands"] if b["band"] == "in_range")
        assert (in_range["rejected"], in_range["admitted"]) == (1, 1)

        # And the individual record carries a timestamp, so a person can line
        # it up against what they heard.
        recent = {row["common_name"]: row for row in snapshot["recent"]}
        assert recent["Eurasian Blackbird"]["at_ns"] == window.utc_start_ns
        assert recent["Eurasian Blackbird"]["threshold"] == pytest.approx(0.55)

    async def test_the_near_miss_ring_is_bounded_and_live_tunable(self) -> None:
        """Charter item 1: this runs per candidate on the detector's path, so
        it must have a hard ceiling, and ADR-048 requires the knob that sets
        that ceiling to genuinely take effect on a running detector."""
        detector = BirdNetDetector(model_dir="/nonexistent", near_miss_ring=3)
        for index in range(50):
            detector._near_misses.record_rejected(
                at_ns=index,
                label_index=0,
                common_name="Robin",
                scientific_name="Erithacus rubecula",
                score=0.2,
                occurrence=0.5,
                band="in_range",
                threshold=0.55,
            )
        assert detector.near_miss_snapshot()["held"] == 3
        detector.retune(near_miss_ring=25)
        assert detector.near_miss_snapshot()["capacity"] == 25
        # The cumulative record is not reset by a resize: comparing before and
        # after a threshold change is the entire use of this panel.
        assert detector.near_miss_snapshot()["rejected_total"] == 50

    async def test_a_sound_category_is_kept_but_is_not_a_species_claim(self) -> None:
        """ADR-049, end to end through ``analyse``.

        Reproduces the live station's own numbers: "Engine" at score 0.976
        with a range-model prior of 4e-06, and "Human vocal" at 0.984 with
        3e-06. Both must survive -- a car really did drive past -- and neither
        may be recorded as a bird.
        """

        class _StubInterpreter:
            def __init__(self, logits: np.ndarray) -> None:
                self._logits = logits

            def set_tensor(self, index: int, value: object) -> None:
                return None

            def invoke(self) -> None:
                return None

            def get_tensor(self, index: int) -> np.ndarray:
                return self._logits.reshape(1, -1)

        class _StubRange:
            def __init__(self, priors: np.ndarray) -> None:
                self._priors = priors

            def probabilities(self, week: int) -> np.ndarray:
                return self._priors

        labels = [
            "Engine_Engine",
            "Human vocal_Human vocal",
            "Strix aluco_Tawny Owl",
        ]
        scores = [0.976, 0.984, 0.974]
        logits = np.array([math.log(p / (1.0 - p)) for p in scores], dtype=np.float32)
        priors = np.array([4e-06, 3e-06, 0.019253], dtype=np.float32)

        detector = BirdNetDetector(model_dir="/nonexistent", min_confidence=0.1)
        detector._labels = labels
        detector._parsed = [parse_label(label) for label in labels]
        detector._expected_samples = 48000 * 3
        detector._interpreter = _StubInterpreter(logits)
        detector._input_index = 0
        detector._output_index = 0
        detector._range = _StubRange(priors)

        window = make_window(np.zeros(48000 * 3, dtype=np.float32), 48000, detector.window_spec)
        detections = await detector.analyse(window)

        names = {d.common_name for d in detections}
        assert names == {"Engine", "Human vocal", "Tawny Owl"}
        assert detector._suppressed_implausible_prior == 0
        assert detector.non_taxonomic_admitted() == 2

        engine = next(d for d in detections if d.common_name == "Engine")
        assert engine.rank is None
        assert engine.taxonomic_group == "acoustic_event"
        assert engine.scientific_name is None
        assert engine.native_result["plausibility_band"] == "non_biological"
        assert engine.native_result["sound_kind"] == "anthropogenic"
        # No prior was consulted, so none is recorded: an occurrence figure on
        # this row would be a number that does not mean what its label says.
        assert engine.native_result["occurrence_probability"] is None
        assert engine.native_result["range_model_used"] is False

        human = next(d for d in detections if d.common_name == "Human vocal")
        assert human.native_result["sound_kind"] == "human"

        # The real species alongside them is completely unaffected.
        tawny = next(d for d in detections if d.common_name == "Tawny Owl")
        assert tawny.rank == "species"
        assert tawny.taxonomic_group == "bird"
        assert tawny.scientific_name == "Strix aluco"
        assert tawny.native_result["occurrence_probability"] == pytest.approx(0.019253)

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
