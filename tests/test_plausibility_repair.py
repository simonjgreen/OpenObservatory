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
]
# Exact measured priors from the live station (HANDOVER.md section 6.3 item 0).
PRIORS = np.array([8e-06, 0.019253, 0.772293], dtype=np.float32)
BASE = datetime(2026, 8, 4, 21, 0, tzinfo=UTC)


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
        ) -> None:
            det_id = uuid.uuid4()
            native_result = {
                "detector": "birdnet-v2.4",
                "week": 20,
                "occurrence_probability": occurrence,
                "plausibility_band": band,
            }
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
            )

        names = {item.common_name for item in findings}
        assert names == {"Flammulated Owl"}
        [flam] = findings
        assert flam.recomputed_band == "implausible"
        assert math.isinf(flam.recomputed_threshold)
        assert flam.stored_band == "out_of_range"  # what the old logic actually did
        assert "floor" in flam.reason

    def test_is_read_only(self, settings, seeded_detections) -> None:
        """Dry-run fidelity: finding must never write anything."""
        with session_scope() as session:
            repair.find_implausible_detections(
                session,
                model_dir=Path("/nonexistent"),
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
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
                session, model_dir=Path("/nonexistent"), latitude=51.4769, longitude=-0.0005
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
            )
            repair.apply_plausibility_flag(session, finding)

        with session_scope() as session:
            findings_again = repair.find_implausible_detections(
                session,
                model_dir=Path("/nonexistent"),
                latitude=REFERENCE_LATITUDE,
                longitude=REFERENCE_LONGITUDE,
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
                session, model_dir=Path("/nonexistent"), latitude=51.4769, longitude=-0.0005
            )
            session.add(
                orm.Review(detection_id=finding.detection_id, actor="op", status="confirmed")
            )

        with session_scope() as session:
            repair.apply_plausibility_flag(session, finding)

        with session_scope() as session:
            row = session.get(orm.Detection, finding.detection_id)
            assert "plausibility_review" not in (row.native_result or {})
