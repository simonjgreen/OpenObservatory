"""Tests for ``oo detections reconcile-plausibility``, the ADR-032 repair path.

Mirrors ``test_cli_history.py``'s shape for ``oo history reconcile-streams``:
dry-run by default, ``--apply`` alone changes nothing without confirmation,
``--apply --yes`` writes, and the original claim is never overwritten.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from open_observatory import plausibility_repair as repair
from open_observatory.cli import app
from open_observatory.config import set_settings
from open_observatory.db import models as orm
from open_observatory.db.session import ensure_schema_at_head, init_engine, session_scope

# mix_stderr=False: stdout carries the JSON document and nothing else, which
# is the contract emit_json exists to keep. Mixing the streams hid a real
# defect -- `oo refine status --json` printed through rich and nobody saw,
# because the logger used to hold the pre-redirect stderr and its output
# never reached the captured stdout at all.
runner = CliRunner()

BASE = datetime(2026, 8, 4, 21, 0, tzinfo=UTC)
LABELS = [
    "Otus flammeolus_Flammulated Owl",
    "Coloeus monedula_Eurasian Jackdaw",
]
PRIORS = np.array([8e-06, 0.772293], dtype=np.float32)


class _StubRange:
    def probabilities(self, week: int) -> np.ndarray:
        return PRIORS


@pytest.fixture(autouse=True)
def _stub_range_model(monkeypatch):
    def _fake_loader(model_dir, latitude, longitude, *, threads=1):
        parsed = [tuple(label.split("_", 1)) for label in LABELS]
        return LABELS, parsed, _StubRange()

    monkeypatch.setattr(repair, "load_range_model_for_repair", _fake_loader)


def _seed(settings) -> uuid.UUID:
    init_engine(settings)
    ensure_schema_at_head()
    station_id = uuid.uuid4()
    stream_id = uuid.uuid4()
    detector_id = uuid.uuid4()
    detection_id = uuid.uuid4()
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
        session.add(
            orm.Detection(
                id=detection_id,
                station_id=station_id,
                detector_id=detector_id,
                stream_id=stream_id,
                window_id=uuid.uuid4(),
                event_start_utc=BASE,
                event_end_utc=BASE + timedelta(seconds=3),
                source_start_frame=0,
                source_end_frame=1,
                detector_label=LABELS[0],
                common_name="Flammulated Owl",
                scientific_name="Otus flammeolus",
                rank="species",
                taxonomic_group="bird",
                score=0.959,
                native_result={
                    "detector": "birdnet-v2.4",
                    "week": 20,
                    "occurrence_probability": 8e-06,
                    "plausibility_band": "out_of_range",
                },
            )
        )
    return detection_id


#: The Royal Observatory, Greenwich -- the repository's neutral reference
#: location. The range model is never loaded in these tests (the CLI is fed a
#: nonexistent model dir), so the values only need to be *some* configured
#: location, never a real deployment's.
REFERENCE_LATITUDE = 51.4769
REFERENCE_LONGITUDE = -0.0005


def _with_coordinates(settings):
    return settings.model_copy(
        update={"latitude": REFERENCE_LATITUDE, "longitude": REFERENCE_LONGITUDE}
    )


def test_dry_run_reports_but_does_not_change_anything(settings) -> None:
    settings = _with_coordinates(settings)
    set_settings(settings)
    detection_id = _seed(settings)

    result = runner.invoke(app, ["detections", "reconcile-plausibility"])

    assert result.exit_code == 0, result.output
    assert "Flammulated Owl" in result.output
    assert "Dry run only" in result.output

    with session_scope() as session:
        row = session.get(orm.Detection, detection_id)
        assert "plausibility_review" not in (row.native_result or {})


def test_apply_without_yes_still_requires_confirmation(settings) -> None:
    settings = _with_coordinates(settings)
    set_settings(settings)
    detection_id = _seed(settings)

    result = runner.invoke(
        app, ["detections", "reconcile-plausibility", "--apply"], input="n\n"
    )

    assert result.exit_code != 0
    with session_scope() as session:
        row = session.get(orm.Detection, detection_id)
        assert "plausibility_review" not in (row.native_result or {})


def test_apply_with_yes_flags_the_row_and_preserves_the_original_claim(settings) -> None:
    settings = _with_coordinates(settings)
    set_settings(settings)
    detection_id = _seed(settings)

    result = runner.invoke(
        app, ["detections", "reconcile-plausibility", "--apply", "--yes"]
    )

    assert result.exit_code == 0, result.output
    with session_scope() as session:
        row = session.get(orm.Detection, detection_id)
        assert row is not None  # never deleted
        assert row.common_name == "Flammulated Owl"  # original claim untouched
        assert row.native_result["occurrence_probability"] == 8e-06  # preserved
        assert row.native_result["plausibility_review"]["implausible"] is True


def test_no_coordinates_refuses_rather_than_guessing(settings) -> None:
    set_settings(settings)  # base fixture has no latitude/longitude
    init_engine(settings)
    ensure_schema_at_head()

    result = runner.invoke(app, ["detections", "reconcile-plausibility"])

    assert result.exit_code != 0
    # stderr, not stdout: a refusal must not land in a --json caller's document,
    # so the failure paths print to stderr (see `console_err` in cli.py).
    assert "coordinates" in result.stderr
    assert result.stdout == ""


def test_no_implausible_detections_reports_clean(settings) -> None:
    settings = _with_coordinates(settings)
    set_settings(settings)
    init_engine(settings)
    ensure_schema_at_head()

    result = runner.invoke(app, ["detections", "reconcile-plausibility"])

    assert result.exit_code == 0, result.output
    assert "No implausible detections found" in result.output


class TestKeep:
    """``oo detections keep`` (ADR-061) -- the CLI half of the operator surface
    for the keep flag Tasks 1-4 built the retention exemption for."""

    def test_keep_marks_the_detection_and_emits_json(self, settings) -> None:
        set_settings(settings)
        detection_id = _seed(settings)

        result = runner.invoke(app, ["detections", "keep", str(detection_id), "--json"])

        assert result.exit_code == 0, result.output
        body = json.loads(result.stdout)
        assert body["kept_at"]
        assert body["kept_by"] == "operator"

        with session_scope() as session:
            row = session.get(orm.Detection, detection_id)
            assert row.kept_at is not None
            assert row.kept_by == "operator"

    def test_unkeep_clears_both(self, settings) -> None:
        set_settings(settings)
        detection_id = _seed(settings)

        runner.invoke(app, ["detections", "keep", str(detection_id)])
        result = runner.invoke(
            app, ["detections", "keep", str(detection_id), "--unkeep", "--json"]
        )

        assert result.exit_code == 0, result.output
        body = json.loads(result.stdout)
        assert body["kept_at"] is None
        assert body["kept_by"] is None

        with session_scope() as session:
            row = session.get(orm.Detection, detection_id)
            assert row.kept_at is None
            assert row.kept_by is None

    def test_keeping_an_unknown_detection_fails_clearly(self, settings) -> None:
        set_settings(settings)
        init_engine(settings)
        ensure_schema_at_head()

        result = runner.invoke(app, ["detections", "keep", str(uuid.uuid4())])

        assert result.exit_code != 0
        assert result.stdout == ""  # a refusal must never land in a --json document


def _seed_jackdaw_admitted_at_035(settings, *, name: str, record_threshold: bool) -> uuid.UUID:
    """One ordinary garden bird, stored exactly as the live station stores them.

    Score 0.42, band `in_range`, admitted while the station was running
    `OO_BIRDNET_THRESHOLD_IN_RANGE=0.35` (since 2026-08-09). Nothing about this
    row is wrong.

    `record_threshold` controls whether the row carries the bar it was admitted
    under. With it, the row-level exemption in `plausibility_repair` protects
    the row on its own. Without it, nothing protects the row except the CLI
    passing the station's configured threshold through -- which is exactly the
    defect ADR-070 fixes, so both rows are seeded and the second is the one
    that fails against the pre-ADR-070 CLI.
    """
    detection_id = uuid.uuid4()
    with session_scope() as session:
        template = session.execute(select(orm.Detection).limit(1)).scalar_one()
        session.add(
            orm.Detection(
                id=detection_id,
                station_id=template.station_id,
                detector_id=template.detector_id,
                stream_id=template.stream_id,
                window_id=uuid.uuid4(),
                event_start_utc=BASE,
                event_end_utc=BASE + timedelta(seconds=3),
                source_start_frame=0,
                source_end_frame=1,
                detector_label=LABELS[1],
                common_name=name,
                scientific_name="Coloeus monedula",
                rank="species",
                taxonomic_group="bird",
                score=0.42,
                native_result={
                    "detector": "birdnet-v2.4",
                    "week": 20,
                    "occurrence_probability": 0.772293,
                    "plausibility_band": "in_range",
                    **({"threshold_applied": 0.35} if record_threshold else {}),
                },
            )
        )
    return detection_id


def test_the_configured_band_thresholds_reach_the_repair_pass(settings) -> None:
    """ADR-070. The CLI must judge stored rows by the bar the detector admitted
    them under, not by `find_implausible_detections`'s own defaults.

    Before ADR-070 this command passed only `plausibility_floor` and `limit`,
    so the five band thresholds silently fell back to 0.55/0.75/0.90 however
    the station was configured. Measured on the live station on 2026-08-23,
    running `OO_BIRDNET_THRESHOLD_IN_RANGE=0.35`: a full-depth dry run returned
    32,660 findings -- Common Woodpigeon 9,168, European Robin 7,434, Collared
    Dove 2,477, highest flagged score 0.549992 -- every one of them a row sitting
    in the 0.35-0.55 gap, and not one a genuinely implausible species. Applying
    it would have withdrawn about a third of the bird record, irreversibly:
    `plausibility_repair` skips rows that already carry a `plausibility_review`,
    so a second run cannot undo the first.
    """
    settings = _with_coordinates(settings).model_copy(update={"birdnet_threshold_in_range": 0.35})
    set_settings(settings)
    _seed(settings)
    jackdaw_id = _seed_jackdaw_admitted_at_035(settings, name="Eurasian Jackdaw", record_threshold=True)
    bare_jackdaw_id = _seed_jackdaw_admitted_at_035(
        settings, name="Jackdaw with no recorded bar", record_threshold=False
    )

    result = runner.invoke(app, ["detections", "reconcile-plausibility", "--json"])

    assert result.exit_code == 0, result.output
    findings = json.loads(result.stdout)
    names = {item["common_name"] for item in findings}
    # The plausible garden bird is left alone -- both the row that records the
    # bar it was admitted under, and the row that does not and so depends
    # entirely on the CLI passing the station's configured 0.35 through.
    assert "Eurasian Jackdaw" not in names
    assert "Jackdaw with no recorded bar" not in names
    # ...and the genuinely implausible species is still caught by the same run,
    # so this is not simply a quieter command.
    assert names == {"Flammulated Owl"}

    with session_scope() as session:
        for row_id in (jackdaw_id, bare_jackdaw_id):
            row = session.get(orm.Detection, row_id)
            assert "plausibility_review" not in (row.native_result or {})
