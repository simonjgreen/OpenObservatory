"""``oo clips reconcile-missing`` (ADR-057).

Same shape as `oo history reconcile-streams` and `oo detections
reconcile-plausibility`: dry-run by default, ``--apply`` alone still asks,
``--apply --yes`` writes, and nothing about the system is destroyed whatever
happens.

The ``--json`` document is asserted to parse from stdout with nothing else in
it. That is not a formality: the dry-run notice on ``reconcile-plausibility``
once landed on stdout underneath a 1,485-line report and turned a well-formed
document into "Extra data", which is why `emit_json` and `notice` exist.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from open_observatory.cli import app
from open_observatory.config import set_settings
from open_observatory.db import models as orm
from open_observatory.db.session import ensure_schema_at_head, init_engine, session_scope
from open_observatory.media_repair import DETAIL_KEY, MISSING_REASON

runner = CliRunner()

BASE = datetime(2026, 8, 5, 18, 44, tzinfo=UTC)


def _seed(settings) -> tuple[uuid.UUID, uuid.UUID, Path]:
    """One detection with two assets: one file present, one file gone."""
    init_engine(settings)
    ensure_schema_at_head()
    clip_dir = Path(settings.clip_dir)
    clip_dir.mkdir(parents=True, exist_ok=True)
    station_id, detector_id, detection_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    present_id, missing_id = uuid.uuid4(), uuid.uuid4()
    present_path = clip_dir / "present.wav"
    present_path.write_bytes(b"\x00" * 4096)

    with session_scope() as session:
        session.add(orm.Station(id=station_id, name="test", timezone="Europe/London"))
        session.add(
            orm.Detector(
                id=detector_id,
                plugin_id="birdnet-v2.4",
                plugin_version="1",
                model_id="m",
                model_version="2.4",
            )
        )
        session.add(
            orm.Detection(
                id=detection_id,
                station_id=station_id,
                detector_id=detector_id,
                stream_id=uuid.uuid4(),
                window_id=uuid.uuid4(),
                event_start_utc=BASE,
                event_end_utc=BASE + timedelta(seconds=3),
                source_start_frame=0,
                source_end_frame=1,
                detector_label="Spotted Crake",
                common_name="Spotted Crake",
                scientific_name="Porzana porzana",
                taxonomic_group="bird",
                score=0.61,
                native_result={},
            )
        )
        for asset_id, path in (
            (present_id, present_path),
            (missing_id, clip_dir / "2026-08-04" / "vanished.wav"),
        ):
            session.add(
                orm.MediaAsset(
                    id=asset_id,
                    kind="evidence_native",
                    storage_uri=str(path),
                    mime_type="audio/wav",
                    byte_length=4096,
                    sha256="0" * 64,
                    created_at=BASE,
                )
            )
            session.add(
                orm.DetectionMedia(
                    detection_id=detection_id, media_asset_id=asset_id, role="evidence"
                )
            )
    return detection_id, missing_id, present_path


def test_dry_run_reports_and_changes_nothing(settings) -> None:
    _detection_id, missing_id, _present = _seed(settings)
    set_settings(settings)

    result = runner.invoke(app, ["clips", "reconcile-missing"])

    assert result.exit_code == 0
    assert "Spotted Crake" in result.stdout
    assert "Dry run only" in result.stdout
    with session_scope() as session:
        assert session.get(orm.MediaAsset, missing_id).reclaimed_at is None


def test_json_document_is_the_only_thing_on_stdout(settings) -> None:
    _detection_id, missing_id, _present = _seed(settings)
    set_settings(settings)

    result = runner.invoke(app, ["clips", "reconcile-missing", "--json"])

    payload = json.loads(result.stdout)
    assert payload["missing"] == 1
    assert payload["scanned"] == 2
    assert payload["missing_bytes"] == 4096
    assert payload["findings"][0]["asset_id"] == str(missing_id)
    assert payload["findings"][0]["reclaim_reason"] == MISSING_REASON
    # The advice belongs on stderr, where a `jq` pipe does not see it.
    assert "Dry run only" in result.stderr


def test_apply_marks_the_row_missing_and_leaves_everything_else_alone(settings) -> None:
    detection_id, missing_id, present_path = _seed(settings)
    set_settings(settings)

    result = runner.invoke(app, ["clips", "reconcile-missing", "--apply", "--yes"])

    assert result.exit_code == 0
    with session_scope() as session:
        row = session.get(orm.MediaAsset, missing_id)
        assert row.reclaimed_at is not None
        assert row.reclaim_reason == MISSING_REASON
        assert row.detail[DETAIL_KEY]["claimed_byte_length"] == 4096
        # Nothing about the system's own record of the event is destroyed.
        assert session.get(orm.Detection, detection_id) is not None
        assert session.query(orm.MediaAsset).count() == 2
        assert session.query(orm.DetectionMedia).count() == 2
    assert present_path.exists()


def test_apply_without_yes_asks_first_and_aborts_on_no(settings) -> None:
    _detection_id, missing_id, _present = _seed(settings)
    set_settings(settings)

    result = runner.invoke(app, ["clips", "reconcile-missing", "--apply"], input="n\n")

    assert result.exit_code == 1
    with session_scope() as session:
        assert session.get(orm.MediaAsset, missing_id).reclaimed_at is None


def test_second_run_finds_nothing(settings) -> None:
    _seed(settings)
    set_settings(settings)

    runner.invoke(app, ["clips", "reconcile-missing", "--apply", "--yes"])
    result = runner.invoke(app, ["clips", "reconcile-missing", "--json"])

    assert json.loads(result.stdout)["missing"] == 0
