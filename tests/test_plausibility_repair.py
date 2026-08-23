"""Tests for the historical BirdNET plausibility repair (ADR-032).

Mirrors `test_history.py`'s pattern for `find_suspect_streams` /
`apply_stream_reconciliation`: a seeded database, a pure read-only finder, and
an apply step that is only ever exercised after the finder's output has been
"shown to the operator" (here, just inspected by the test).

The range model itself is stubbed out (`monkeypatch`) with the exact measured
priors from the live station, so this never needs the real (unbundled,
ADR-006) BirdNET model assets.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from open_observatory import plausibility_repair as repair
from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope

#: Any coordinates would do here (the range model itself is never loaded --
#: model_dir points nowhere); these are the Royal Observatory, Greenwich, the
#: repository's neutral reference location, so no real deployment's site
#: leaks into committed code.
REFERENCE_LATITUDE = 51.4769
REFERENCE_LONGITUDE = -0.0005

LABELS = [
    "Otus flammeolus_Flammulated Owl",
    "Strix aluco_Tawny Owl",
    "Coloeus monedula_Eurasian Jackdaw",
    # ADR-049. Not a species; the range model returns 4e-06 for it because a
    # car has no distribution, not because engines are absent from this garden.
    "Engine_Engine",
    "Human vocal_Human vocal",
]
# Exact measured priors from the live station (HANDOVER.md section 6.3 item 0,
# and the 2026-08-09 re-measurement for the two sound categories).
PRIORS = np.array([8e-06, 0.019253, 0.772293, 4e-06, 3e-06], dtype=np.float32)
BASE = datetime(2026, 8, 4, 21, 0, tzinfo=UTC)

#: ADR-070. The live station has run `OO_BIRDNET_THRESHOLD_IN_RANGE=0.35` since
#: 2026-08-09, so this is what its rows were admitted under and what the repair
#: pass must judge them by. Passed explicitly by every test here, because the
#: defect this file now guards against was precisely a caller that passed
#: *nothing* and silently got 0.55.
STATION_THRESHOLDS = {"threshold_in_range": 0.35}


class _StubRange:
    def probabilities(self, week: int) -> np.ndarray:
        return PRIORS


@pytest.fixture(autouse=True)
def _stub_range_model(monkeypatch):
    def _fake_loader(model_dir, latitude, longitude, *, threads=1):
        parsed = [tuple(label.split("_", 1)) for label in LABELS]
        return LABELS, parsed, _StubRange()

    monkeypatch.setattr(repair, "load_range_model_for_repair", _fake_loader)


@pytest.fixture
def seeded_detections(settings) -> dict[str, uuid.UUID]:
    init_engine(settings)
    create_all()
    station_id = uuid.uuid4()
    stream_id = uuid.uuid4()
    detector_id = uuid.uuid4()
    ids: dict[str, uuid.UUID] = {}

    with session_scope() as session:
        session.add(orm.Station(id=station_id, name="test", timezone="Europe/London"))
        session.add(
            orm.Detector(
                id=detector_id,
                plugin_id="birdnet-v2.4",
                plugin_version="1",
                model_id="m",
                model_version="1",
            )
        )
        session.add(
            orm.AudioStream(
                id=stream_id,
                source_kind="alsa",
                start_utc=BASE,
                end_utc=BASE + timedelta(hours=1),
                start_monotonic_ns=0,
                sample_rate=48000,
                sample_format="FLOAT_LE",
                frame_count=48000 * 3600,
            )
        )

        def add(
            name: str,
            label: str,
            score: float,
            occurrence: float | None,
            band: str | None,
            threshold_applied: float | None = None,
        ) -> None:
            det_id = uuid.uuid4()
            native_result: dict[str, object] = {
                "detector": "birdnet-v2.4",
                "week": 20,
                "occurrence_probability": occurrence,
                "plausibility_band": band,
            }
            if threshold_applied is not None:
                # What `detectors/birdnet.py` has stamped on every row it wrote
                # since 2026-08-04: the bar this row actually had to clear.
                native_result["threshold_applied"] = threshold_applied
            session.add(
                orm.Detection(
                    id=det_id,
                    station_id=station_id,
                    detector_id=detector_id,
                    stream_id=stream_id,
                    window_id=uuid.uuid4(),
                    event_start_utc=BASE,
                    event_end_utc=BASE + timedelta(seconds=3),
                    source_start_frame=0,
                    source_end_frame=1,
                    detector_label=label,
                    common_name=name,
                    scientific_name=label.split("_", 1)[0],
                    rank="species",
                    taxonomic_group="bird",
                    score=score,
                    native_result=native_result,
                )
            )
            ids[name] = det_id

        # Old logic admitted this under out_of_range (0.90 <= 0.959) despite the
        # range model saying "essentially impossible" -- defect (a).
        add("Flammulated Owl", LABELS[0], 0.959, 8e-06, "out_of_range")
        # Genuinely admissible, both before and after the fix.
        add("Tawny Owl", LABELS[1], 0.974, 0.019253, "out_of_range")
        # Common local species, unaffected either way.
        add("Eurasian Jackdaw", LABELS[2], 0.617, 0.772293, "in_range")
        # Two of BirdNET's sound categories, stored exactly as the live
        # station stored them before ADR-049 -- scientific_name repeating the
        # common name, rank "species", group "bird".
        add("Engine", LABELS[3], 0.976, 4e-06, "out_of_range", 0.55)
        add("Human vocal", LABELS[4], 0.984, 3e-06, "out_of_range", 0.55)

    return ids


class TestFindImplausibleDetections:
    def test_flags_only_the_species_the_current_floor_rejects(
        self, settings, seeded_detections
    ) -> None:
        with session_scope() as session:
            findings = repair.find_implausible_detections(
                session,
                model_dir=Path("/nonexistent"),
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
                **STATION_THRESHOLDS,
            )

        names = {item.common_name for item in findings}
        assert names == {"Flammulated Owl"}
        [flam] = findings
        assert flam.recomputed_band == "implausible"
        assert math.isinf(flam.recomputed_threshold)
        assert flam.stored_band == "out_of_range"  # what the old logic actually did
        assert "floor" in flam.reason

    def test_sound_categories_are_never_withdrawn_by_the_floor(
        self, settings, seeded_detections
    ) -> None:
        """ADR-049. The bug this closes was measured, not hypothetical.

        The first dry run against the live station's 67,679 rows proposed 114
        findings, of which 62 were "Engine", 24 "Human vocal" and 5 "Dog" --
        91 correct detections of things that really happened, about to be
        withdrawn because a range model was asked a question it cannot answer.
        """
        with session_scope() as session:
            findings = repair.find_implausible_detections(
                session,
                model_dir=Path("/nonexistent"),
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
                **STATION_THRESHOLDS,
            )

        names = {item.common_name for item in findings}
        assert "Engine" not in names
        assert "Human vocal" not in names
        # And the genuinely implausible owl alongside them is still caught, so
        # this is an exemption rather than a weakening of the floor.
        assert names == {"Flammulated Owl"}

    def test_is_read_only(self, settings, seeded_detections) -> None:
        """Dry-run fidelity: finding must never write anything."""
        with session_scope() as session:
            repair.find_implausible_detections(
                session,
                model_dir=Path("/nonexistent"),
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
                **STATION_THRESHOLDS,
            )
        with session_scope() as session:
            row = session.get(orm.Detection, seeded_detections["Flammulated Owl"])
            assert "plausibility_review" not in (row.native_result or {})

    def test_no_detections_found_when_nothing_seeded(self, settings) -> None:
        init_engine(settings)
        create_all()
        with session_scope() as session:
            findings = repair.find_implausible_detections(
                session,
                model_dir=Path("/nonexistent"),
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
                **STATION_THRESHOLDS,
            )
        assert findings == []

    def test_a_human_reviewed_detection_is_never_flagged(
        self, settings, seeded_detections
    ) -> None:
        """ADR-043 / charter priority 5: a human's ear outranks a later
        machine refinement. A detection a human has already looked at --
        confirmed, rejected, corrected or held, it does not matter which --
        must never be re-flagged by this repair pass, even though nothing
        about the machine's own plausibility finding changed."""
        with session_scope() as session:
            session.add(
                orm.Review(
                    detection_id=seeded_detections["Flammulated Owl"],
                    actor="op",
                    status="confirmed",
                )
            )

        with session_scope() as session:
            findings = repair.find_implausible_detections(
                session,
                model_dir=Path("/nonexistent"),
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
                **STATION_THRESHOLDS,
            )
        assert findings == []


class TestApplyPlausibilityFlag:
    def test_flags_without_deleting_or_overwriting_the_original_claim(
        self, settings, seeded_detections
    ) -> None:
        with session_scope() as session:
            [finding] = repair.find_implausible_detections(
                session,
                model_dir=Path("/nonexistent"),
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
                **STATION_THRESHOLDS,
            )
            repair.apply_plausibility_flag(session, finding)

        with session_scope() as session:
            row = session.get(orm.Detection, seeded_detections["Flammulated Owl"])
            assert row is not None  # never deleted
            assert row.common_name == "Flammulated Owl"  # original claim untouched
            assert row.native_result["occurrence_probability"] == 8e-06  # preserved verbatim
            assert row.native_result["plausibility_band"] == "out_of_range"  # preserved verbatim
            review = row.native_result["plausibility_review"]
            assert review["implausible"] is True
            assert review["recomputed_band"] == "implausible"
            assert review["recomputed_threshold"] is None  # inf is not JSON-serialisable

    def test_a_reviewed_row_is_never_re_flagged(self, settings, seeded_detections) -> None:
        with session_scope() as session:
            [finding] = repair.find_implausible_detections(
                session,
                model_dir=Path("/nonexistent"),
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
                **STATION_THRESHOLDS,
            )
            repair.apply_plausibility_flag(session, finding)

        with session_scope() as session:
            findings_again = repair.find_implausible_detections(
                session,
                model_dir=Path("/nonexistent"),
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
                **STATION_THRESHOLDS,
            )
        assert findings_again == []

    def test_apply_is_a_no_op_if_reviewed_since_the_finding_was_computed(
        self, settings, seeded_detections
    ) -> None:
        """Defensive re-check in `apply_plausibility_flag` itself: real time
        passes between an interactive CLI's finding step and its confirm
        step, so a human review landing in that window must still win."""
        with session_scope() as session:
            [finding] = repair.find_implausible_detections(
                session,
                model_dir=Path("/nonexistent"),
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
                **STATION_THRESHOLDS,
            )
            session.add(
                orm.Review(detection_id=finding.detection_id, actor="op", status="confirmed")
            )

        with session_scope() as session:
            repair.apply_plausibility_flag(session, finding)

        with session_scope() as session:
            row = session.get(orm.Detection, finding.detection_id)
            assert "plausibility_review" not in (row.native_result or {})


class TestThresholdChangesAreNotDefects:
    """ADR-070. The repair pass must judge a row by the bar it was admitted under.

    The defect: `cli.py`'s `reconcile-plausibility` passed only
    `plausibility_floor` and `limit`, so the five band thresholds fell back to
    `find_implausible_detections`'s own defaults (0.55/0.75/0.90) no matter what
    the station was configured with. The live station has run
    `OO_BIRDNET_THRESHOLD_IN_RANGE=0.35` since 2026-08-09, so the detector
    admitted and stored rows at 0.35 while this pass re-judged them at 0.55.
    Every row in that gap was reported implausible: a full-depth dry run on
    2026-08-23 returned 32,660 findings, the highest flagged score 0.549992,
    led by Common Woodpigeon (9,168), European Robin (7,434) and Collared Dove
    (2,477) -- the operator's actual garden birds, one `--apply` from being
    withdrawn, and unrecoverable by re-running because a flagged row is skipped
    on the next pass.
    """

    @pytest.fixture
    def retuned_rows(self, settings, seeded_detections) -> dict[str, uuid.UUID]:
        """Two common, entirely plausible birds scoring 0.42, in the gap.

        Identical except for one thing: whether the row records the bar it was
        admitted under. That is the whole difference between a row this command
        can judge safely and one it cannot.
        """
        ids = dict(seeded_detections)
        with session_scope() as session:
            template = session.get(orm.Detection, seeded_detections["Eurasian Jackdaw"])
            assert template is not None
            for name, threshold_applied in (
                ("Jackdaw admitted at 0.35", 0.35),
                ("Jackdaw with no recorded bar", None),
            ):
                native_result: dict[str, object] = {
                    "detector": "birdnet-v2.4",
                    "week": 20,
                    "occurrence_probability": 0.772293,
                    "plausibility_band": "in_range",
                }
                if threshold_applied is not None:
                    native_result["threshold_applied"] = threshold_applied
                det_id = uuid.uuid4()
                session.add(
                    orm.Detection(
                        id=det_id,
                        station_id=template.station_id,
                        detector_id=template.detector_id,
                        stream_id=template.stream_id,
                        window_id=uuid.uuid4(),
                        event_start_utc=BASE,
                        event_end_utc=BASE + timedelta(seconds=3),
                        source_start_frame=0,
                        source_end_frame=1,
                        detector_label=LABELS[2],
                        common_name=name,
                        scientific_name="Coloeus monedula",
                        rank="species",
                        taxonomic_group="bird",
                        score=0.42,
                        native_result=native_result,
                    )
                )
                ids[name] = det_id
        return ids

    def _find(self, **overrides):
        with session_scope() as session:
            return repair.find_implausible_detections(
                session,
                model_dir=Path("/nonexistent"),
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
                **overrides,
            )

    def test_a_row_admitted_at_the_configured_bar_is_never_flagged(self, settings, retuned_rows) -> None:
        """The regression proper. Fails before ADR-070 in two distinct ways:
        the CLI did not pass the configured bar at all, and even given it, a
        row stored under a bar that has since moved was re-judged at the new
        one."""
        names = {item.common_name for item in self._find(**STATION_THRESHOLDS)}
        assert "Jackdaw admitted at 0.35" not in names
        assert "Jackdaw with no recorded bar" not in names

        # Both directions. The genuinely implausible species -- one the range
        # model puts below the plausibility floor, where no score is admissible
        # -- is still caught by the same call. This is an exemption for tuning
        # changes, not a weakening of the floor.
        assert "Flammulated Owl" in names

    def test_the_admitting_bar_on_the_row_survives_a_later_retune(self, settings, retuned_rows) -> None:
        """The general case, not just today's configuration: judged at 0.55 --
        a bar raised *after* these rows were written -- the row that recorded
        its own 0.35 is still left alone, because it cleared the bar that was
        in force for it."""
        findings = {item.common_name: item for item in self._find(threshold_in_range=0.55)}
        assert "Jackdaw admitted at 0.35" not in findings
        # And the limitation, asserted rather than merely documented: a row
        # that does not say what bar admitted it cannot be told apart from a
        # genuine defect, so it is reported -- with `admitting_threshold` null,
        # which is how an operator reading a dry run can see the difference.
        unknown = findings["Jackdaw with no recorded bar"]
        assert unknown.admitting_threshold is None
        assert unknown.to_dict()["admitting_threshold"] is None
        assert "Flammulated Owl" in findings
        assert findings["Flammulated Owl"].admitting_threshold is None

    def test_a_band_change_is_still_a_defect_and_is_still_flagged(self, settings, seeded_detections) -> None:
        """ADR-032 defect (b) must survive the ADR-070 exemption.

        A row whose *band* is different today was mis-decided, not merely
        judged against a bar that has since moved -- the exemption is keyed on
        the band being unchanged precisely so this case still bites. Here the
        floor is raised past the Tawny Owl's measured 0.019253 prior, moving it
        from `uncommon` to `implausible`, where no score is admissible at all.
        """
        names = {item.common_name for item in self._find(plausibility_floor=0.05, **STATION_THRESHOLDS)}
        assert "Tawny Owl" in names
        assert "Flammulated Owl" in names

    def test_sound_categories_are_judged_at_the_configured_in_range_bar(
        self, settings, seeded_detections
    ) -> None:
        """The `non_biological` puzzle in the 2026-08-23 dry run, resolved.

        `find_implausible_detections`'s docstring used to say it skipped the
        eleven non-taxonomic classes "entirely" (ADR-049), yet the dry run
        reported 5,890 findings in the `non_biological` band. Both were true of
        different things: the classes are exempt from the *occurrence prior*
        and the floor, not from a score bar. `band_for` puts them in
        `non_biological` at the in-range threshold, so they moved with the
        in-range bar exactly like every other row -- which is why the count was
        large and why it disappears once the configured 0.35 is passed through.
        """
        engine_at_high_bar = {item.common_name for item in self._find(threshold_in_range=0.99)}
        assert "Engine" in engine_at_high_bar  # not skipped: judged on score
        [engine] = [item for item in self._find(threshold_in_range=0.99) if item.common_name == "Engine"]
        assert engine.recomputed_band == "non_biological"
        # ...but never withdrawn by the floor, whatever the floor is set to.
        assert "Engine" not in {
            item.common_name for item in self._find(plausibility_floor=0.5, **STATION_THRESHOLDS)
        }
