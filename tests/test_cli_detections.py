"""Tests for ``oo detections reconcile-plausibility``, the ADR-032 repair path.

Mirrors ``test_cli_history.py``'s shape for ``oo history reconcile-streams``:
dry-run by default, ``--apply`` alone changes nothing without confirmation,
``--apply --yes`` writes, and the original claim is never overwritten.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from typer.testing import CliRunner

from open_observatory import plausibility_repair as repair
from open_observatory.cli import app
from open_observatory.config import set_settings
from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope

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
    create_all()
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


def _with_coordinates(settings):
    return settings.model_copy(update={"latitude": 51.4769, "longitude": -0.0005})


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
    create_all()

    result = runner.invoke(app, ["detections", "reconcile-plausibility"])

    assert result.exit_code != 0
    assert "coordinates" in result.output


def test_no_implausible_detections_reports_clean(settings) -> None:
    settings = _with_coordinates(settings)
    set_settings(settings)
    init_engine(settings)
    create_all()

    result = runner.invoke(app, ["detections", "reconcile-plausibility"])

    assert result.exit_code == 0, result.output
    assert "No implausible detections found" in result.output
