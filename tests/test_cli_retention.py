"""``oo retention bank-backfill`` (ADR-076 Task 9).

The one-off, unbudgeted counterpart to the sweep's own promotion step, which
only looks back 24 hours because the archive-wide version of this query was
measured at 5.9754 s (2.6672 s even bounded to a two-hour window) --
unaffordable inside the sweep's 1.5 s batch budget. This command has no
budget, so the seeded detection here is well outside that 24-hour window: if
this were reusing the sweep's bounded query by mistake, it would see nothing.

Dry-run is the default and must roll back rather than commit (ADR-074 rule
3): the one assertion that matters most is that ``banked_at`` is still NULL
after a dry run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from open_observatory.cli import app
from open_observatory.config import set_settings
from open_observatory.db import models as orm
from open_observatory.db.session import ensure_schema_at_head, init_engine, session_scope

runner = CliRunner()

# Ten days back: well outside the sweep's 24h `promotion_lookback`, so a pass
# here proves the backfill reaches the whole archive rather than reusing the
# sweep's bounded window by accident.
BASE = datetime(2026, 8, 5, 6, 0, tzinfo=UTC) - timedelta(days=10)


def _seed(settings, *, common_name: str = "Eurasian Wren") -> uuid.UUID:
    """One old detection with a live (unreclaimed) evidence asset."""
    init_engine(settings)
    ensure_schema_at_head()
    clip_dir = settings.clip_dir
    clip_dir.mkdir(parents=True, exist_ok=True)
    clip_path = clip_dir / "wren.wav"
    clip_path.write_bytes(b"\x00" * 4096)

    station_id, detector_id, detection_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    asset_id = uuid.uuid4()
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
                detector_label=common_name,
                common_name=common_name,
                scientific_name="Troglodytes troglodytes",
                taxonomic_group="bird",
                score=0.8,
                native_result={},
            )
        )
        session.add(
            orm.MediaAsset(
                id=asset_id,
                kind="evidence_native",
                storage_uri=str(clip_path),
                mime_type="audio/wav",
                byte_length=4096,
                sha256="0" * 64,
                created_at=BASE,
            )
        )
        session.add(
            orm.DetectionMedia(detection_id=detection_id, media_asset_id=asset_id, role="evidence")
        )
    return detection_id


def test_bank_backfill_dry_run_promotes_nothing(settings) -> None:
    detection_id = _seed(settings)
    set_settings(settings)

    result = runner.invoke(app, ["retention", "bank-backfill", "--dry-run"])

    assert result.exit_code == 0
    assert "would bank" in result.stdout.lower()
    with session_scope() as session:
        assert session.get(orm.Detection, detection_id).banked_at is None


def test_bank_backfill_dry_run_is_the_default(settings) -> None:
    """No flag at all must behave exactly like ``--dry-run`` (ADR-074 rule 3)."""
    detection_id = _seed(settings)
    set_settings(settings)

    result = runner.invoke(app, ["retention", "bank-backfill"])

    assert result.exit_code == 0
    assert "dry run" in result.stdout.lower()
    with session_scope() as session:
        assert session.get(orm.Detection, detection_id).banked_at is None


def test_bank_backfill_no_dry_run_writes_banked_at(settings) -> None:
    detection_id = _seed(settings)
    set_settings(settings)

    result = runner.invoke(app, ["retention", "bank-backfill", "--no-dry-run"])

    assert result.exit_code == 0
    assert "eurasian wren" in result.stdout.lower()
    with session_scope() as session:
        assert session.get(orm.Detection, detection_id).banked_at is not None


def test_bank_backfill_is_idempotent(settings) -> None:
    """A banked row is not a candidate: a second run banks nothing further."""
    _seed(settings)
    set_settings(settings)

    first = runner.invoke(app, ["retention", "bank-backfill", "--no-dry-run"])
    second = runner.invoke(app, ["retention", "bank-backfill", "--no-dry-run"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "banked 1 detection" in first.stdout.lower()
    assert "banked 0 detection" in second.stdout.lower()
