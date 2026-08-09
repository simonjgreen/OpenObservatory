"""The live-tuning map is a promise, and this is what keeps it honest.

``site_settings.py`` tells an operator that a setting is "live" -- saved and
in force now. For most settings that is free, because the station reads them
from ``Settings`` on every use. For the ones in ``tuning.LIVE_TARGETS`` it is
a claim about a specific object accepting a specific parameter, and a rename
anywhere along that path would turn a live setting into one that saves and
does nothing while still reporting itself applied. That is the precise
failure ADR-048's tier system exists to prevent, so it is asserted rather than
assumed:

1. every mapped setting is a real, editable, live-tier setting;
2. every target exists and accepts the parameter named;
3. every restart-tier setting is recorded in one of the pinned snapshots, so
   editing it is reported as pending rather than silently ignored;
4. a retune actually changes what the component does.
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from open_observatory import tuning
from open_observatory.audio.spectrogram import SpectrogramEncoder
from open_observatory.clips import ClipManager
from open_observatory.config import Settings
from open_observatory.detectors.activity import ActivityDetector
from open_observatory.detectors.birdnet import BirdNetDetector
from open_observatory.detectors.ultrasonic import UltrasonicDetector
from open_observatory.retention import RetentionSweeper
from open_observatory.site_settings import EDITABLE_BY_NAME

DETECTOR_CLASSES = {
    "activity-v1": ActivityDetector,
    "birdnet-v2.4": BirdNetDetector,
    "ultrasonic-pass-v1": UltrasonicDetector,
}


class TestLiveTargetsAreReal:
    def test_every_mapped_setting_is_editable_and_live(self) -> None:
        for name in tuning.LIVE_TARGETS:
            assert name in Settings.model_fields, name
            spec = EDITABLE_BY_NAME.get(name)
            assert spec is not None, f"{name} is mapped but not editable"
            assert spec.tier == "live", f"{name} is mapped for live tuning but pinned"

    def test_clip_and_retention_targets_name_real_attributes(self) -> None:
        owners = {"clips": ClipManager, "retention": RetentionSweeper}
        for name, target in tuning.LIVE_TARGETS.items():
            if target.kind not in owners:
                continue
            parameters = inspect.signature(owners[target.kind].__init__).parameters
            assert target.parameter in parameters or hasattr(
                owners[target.kind], target.parameter
            ), f"{name} -> {target.kind}.{target.parameter}"

    def test_detector_and_encoder_targets_are_accepted_by_retune(self) -> None:
        for name, target in tuning.LIVE_TARGETS.items():
            if target.kind == "spectrogram":
                accepted = inspect.signature(SpectrogramEncoder.retune).parameters
            elif target.kind == "detector":
                accepted = inspect.signature(DETECTOR_CLASSES[target.owner].retune).parameters
            else:
                continue
            assert target.parameter in accepted, f"{name} -> {target.owner}.{target.parameter}"

    def test_every_restart_tier_setting_is_recorded_somewhere(self) -> None:
        """A pinned setting that nothing snapshots would be saved and then
        never reported as pending -- the UI would show "restart to apply" and
        a restart would change nothing, because nothing was tracking it."""
        pinned = set(
            tuning.PINNED_AT_PROCESS_START
            + tuning.PINNED_AT_CAPTURE_START
            + tuning.PINNED_AT_DETECTOR_START
        )
        restart_tier = {
            spec.name for spec in EDITABLE_BY_NAME.values() if spec.tier == "restart"
        }
        assert restart_tier - pinned == set()

    def test_pinned_names_are_real_settings_and_not_double_counted(self) -> None:
        groups = (
            tuning.PINNED_AT_PROCESS_START,
            tuning.PINNED_AT_CAPTURE_START,
            tuning.PINNED_AT_DETECTOR_START,
        )
        seen: set[str] = set()
        for group in groups:
            for name in group:
                assert name in Settings.model_fields, name
                assert name not in seen, f"{name} is pinned in two places"
                seen.add(name)


class TestRetuneChangesBehaviour:
    def test_spectrogram_retune_moves_the_db_window_and_drops_stale_history(self) -> None:
        encoder = SpectrogramEncoder(
            channel=0, name="audible", sample_rate=48000, fft_size=512, bins=16,
            min_hz=100.0, max_hz=10000.0, floor_db=-95.0, ceiling_db=-15.0,
        )
        encoder.history.append(np.zeros(16, dtype=np.uint8))
        encoder.history_first_utc_s = 1.0

        assert encoder.retune(floor_db=-60.0) is True
        assert encoder.floor_db == -60.0
        # Columns already quantised through the old window cannot be remapped,
        # so they are dropped rather than rendered at a second contrast.
        assert len(encoder.history) == 0
        assert encoder.history_first_utc_s is None
        # A no-op retune says so, and leaves history alone.
        encoder.history.append(np.zeros(16, dtype=np.uint8))
        assert encoder.retune(floor_db=-60.0) is False
        assert len(encoder.history) == 1

    def test_ultrasonic_retune_raises_the_bar_a_pass_must_clear(self) -> None:
        detector = UltrasonicDetector(native_sample_rate=384_000)
        detector.retune(min_snr_db=30.0, min_pulses_per_pass=9, band_hz=(20_000.0, 90_000.0))
        assert detector._min_snr_db == 30.0
        assert detector._min_pulses == 9
        assert detector._band == (20_000.0, 90_000.0)

    def test_ultrasonic_retune_converts_milliseconds_the_same_way_the_constructor_does(
        self,
    ) -> None:
        detector = UltrasonicDetector(native_sample_rate=384_000)
        detector.retune(min_pulse_ms=4.0, max_pulse_ms=60.0, merge_gap_ms=3.0)
        assert detector._min_pulse_s == pytest.approx(0.004)
        assert detector._max_pulse_s == pytest.approx(0.060)
        assert detector._merge_gap_s == pytest.approx(0.003)

    def test_activity_retune_keeps_the_noise_floor_estimate(self) -> None:
        """The floor describes the room; a threshold edit does not
        invalidate it, and discarding it would blind the detector for as long
        as it takes to re-learn."""
        detector = ActivityDetector()
        detector._noise_floor_db = np.zeros(8, dtype=np.float32)
        detector.retune(min_snr_db=25.0)
        assert detector._min_snr_db == 25.0
        assert detector._noise_floor_db is not None

    def test_birdnet_retune_moves_the_band_thresholds(self, tmp_path) -> None:
        detector = BirdNetDetector(model_dir=tmp_path)
        detector.retune(
            threshold_out_of_range=0.99, plausibility_floor=0.001, min_confidence=0.3
        )
        assert detector._thresholds["out_of_range"] == 0.99
        assert detector._plausibility_floor == 0.001
        assert detector._min_confidence == 0.3
