"""Tests for tiered clip retention (ADR-026).

This code deletes the operator's evidence irreversibly, so these tests are
written to be paranoid: every tier boundary, the watermark reclaim, exemplar
preservation, dry-run-matches-real-run, a clip missing from disk but present
in the database (and the reverse), and -- above everything else -- that a
sweep never deletes a `detection` row or any of its columns, whatever tier
fires.

A fake clock (`clock=lambda: FIXED_NOW`) makes every age deterministic:
`event_start_utc` is set relative to `FIXED_NOW`, never to real wall time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.orm import Session

from open_observatory.config import REPO_ROOT
from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope
from open_observatory.retention import BAT_GROUP, RetentionSweeper

FIXED_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(settings):
    init_engine(settings)
    create_all()
    return settings


@pytest.fixture
def station_and_detector(db):
    station_id = uuid.uuid4()
    detector_id = uuid.uuid4()
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
    return station_id, detector_id


def _seed_detection(
    session,
    *,
    station_id: uuid.UUID,
    detector_id: uuid.UUID,
    clip_dir: Path,
    age_days: float,
    kinds: tuple[str, ...] = ("evidence_native", "playback"),
    taxonomic_group: str = "bird",
    common_name: str | None = "European Robin",
    canonical_taxon_id: str | None = None,
    score: float = 0.5,
    native_result: dict | None = None,
    write_files: bool = True,
    byte_length: int = 1024,
) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """Insert one detection plus one media asset per `kind`, with real files."""
    detection_id = uuid.uuid4()
    event_start = FIXED_NOW - timedelta(days=age_days)
    session.add(
        orm.Detection(
            id=detection_id,
            station_id=station_id,
            detector_id=detector_id,
            stream_id=uuid.uuid4(),
            window_id=uuid.uuid4(),
            event_start_utc=event_start,
            event_end_utc=event_start + timedelta(seconds=3),
            source_start_frame=0,
            source_end_frame=1,
            detector_label=common_name or "event",
            common_name=common_name,
            scientific_name=f"Sci {common_name}" if common_name else None,
            canonical_taxon_id=canonical_taxon_id,
            rank="species" if canonical_taxon_id else None,
            taxonomic_group=taxonomic_group,
            score=score,
            native_result=native_result or {},
        )
    )
    asset_ids: dict[str, uuid.UUID] = {}
    day_dir = clip_dir / event_start.strftime("%Y-%m-%d")
    for kind in kinds:
        asset_id = uuid.uuid4()
        path = day_dir / f"{detection_id}_{kind}.wav"
        if write_files:
            day_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x00" * byte_length)
        session.add(
            orm.MediaAsset(
                id=asset_id,
                kind=kind,
                storage_uri=str(path),
                mime_type="audio/wav",
                byte_length=byte_length,
                sha256="0" * 64,
            )
        )
        session.add(
            orm.DetectionMedia(detection_id=detection_id, media_asset_id=asset_id, role="evidence")
        )
        asset_ids[kind] = asset_id
    return detection_id, asset_ids


def _sweeper(settings, **overrides) -> RetentionSweeper:
    kwargs = dict(
        clip_dir=settings.clip_dir,
        session_factory=session_scope,
        native_days=7,
        audible_only_days=30,
        exemplar_only_days=90,
        watermark_ratio=0.85,
        batch_size=1000,
        batch_budget_s=30.0,
        clock=lambda: FIXED_NOW,
    )
    kwargs.update(overrides)
    return RetentionSweeper(**kwargs)


def _asset(session, asset_id: uuid.UUID) -> orm.MediaAsset:
    return session.get(orm.MediaAsset, asset_id)


class TestTierBoundaries:
    def test_fresh_clip_keeps_native_and_audible(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _, assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=1,
            )
        sweeper = _sweeper(db)
        report = sweeper.sweep()
        assert report.total_deleted == 0
        with session_scope() as session:
            assert _asset(session, assets["evidence_native"]).reclaimed_at is None
            assert _asset(session, assets["playback"]).reclaimed_at is None
        assert Path(db.clip_dir).exists()

    def test_native_deleted_exactly_at_boundary(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _, just_under = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=6.99,
                common_name="Just Under",
            )
            _, at_boundary = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=7.0,
                common_name="At Boundary",
            )
        sweeper = _sweeper(db)
        sweeper.sweep()
        with session_scope() as session:
            assert _asset(session, just_under["evidence_native"]).reclaimed_at is None
            assert _asset(session, at_boundary["evidence_native"]).reclaimed_at is not None
            # Audible rendering survives the native-tier boundary either way.
            assert _asset(session, just_under["playback"]).reclaimed_at is None
            assert _asset(session, at_boundary["playback"]).reclaimed_at is None

    def test_native_file_actually_removed_from_disk(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _, assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=8,
            )
            native_path = Path(_asset(session, assets["evidence_native"]).storage_uri)
            playback_path = Path(_asset(session, assets["playback"]).storage_uri)
        assert native_path.exists()
        _sweeper(db).sweep()
        assert not native_path.exists()
        assert playback_path.exists()

    def test_non_exemplar_loses_everything_at_30_days(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            # An older, better exemplar of the same species so this one is not it.
            _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=200,
                common_name="Common Woodpigeon",
                score=0.99,
                kinds=("playback",),
            )
            _, assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=31,
                common_name="Common Woodpigeon",
                score=0.5,
                kinds=("playback",),
            )
        _sweeper(db).sweep()
        with session_scope() as session:
            assert _asset(session, assets["playback"]).reclaimed_at is not None

    def test_non_exemplar_survives_just_under_30_days(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=200,
                common_name="Common Woodpigeon",
                score=0.99,
                kinds=("playback",),
            )
            _, assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=29,
                common_name="Common Woodpigeon",
                score=0.5,
                kinds=("playback",),
            )
        _sweeper(db).sweep()
        with session_scope() as session:
            assert _asset(session, assets["playback"]).reclaimed_at is None

    def test_everything_including_exemplars_deleted_at_90_days(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            detection_id, best_ever = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=200,
                common_name="Common Woodpigeon",
                score=0.99,
                kinds=("playback",),
            )
        _sweeper(db).sweep()
        with session_scope() as session:
            assert _asset(session, best_ever["playback"]).reclaimed_at is not None
            # The clip is gone; the detection row -- the "log entry" -- is not.
            assert session.get(orm.Detection, detection_id) is not None


class TestHumanHold:
    """ADR-043: an explicit human hold exempts a detection's evidence from
    the three age-based tiers, but not from the watermark safety valve."""

    def test_held_detection_survives_native_boundary(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            detection_id, assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=8,
                kinds=("evidence_native",),
            )
            session.add(orm.Review(detection_id=detection_id, actor="op", status="held"))
        _sweeper(db).sweep()
        with session_scope() as session:
            assert _asset(session, assets["evidence_native"]).reclaimed_at is None

    def test_held_detection_survives_final_expiry(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            detection_id, assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=200,
                kinds=("playback",),
            )
            session.add(orm.Review(detection_id=detection_id, actor="op", status="held"))
        _sweeper(db).sweep()
        with session_scope() as session:
            assert _asset(session, assets["playback"]).reclaimed_at is None

    def test_hold_released_by_a_later_review(self, db, station_and_detector) -> None:
        """`Review` is append-only and "current" means "latest" -- a `held`
        followed by a `confirmed` is no longer held, exactly as `review.py`
        documents."""
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            detection_id, assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=200,
                kinds=("playback",),
            )
            session.add(
                orm.Review(
                    detection_id=detection_id,
                    actor="op",
                    status="held",
                    created_at=FIXED_NOW,
                )
            )
            session.add(
                orm.Review(
                    detection_id=detection_id,
                    actor="op",
                    status="confirmed",
                    created_at=FIXED_NOW + timedelta(seconds=1),
                )
            )
        _sweeper(db).sweep()
        with session_scope() as session:
            assert _asset(session, assets["playback"]).reclaimed_at is not None

    def test_watermark_reclaim_ignores_hold(self, db, station_and_detector, monkeypatch) -> None:
        """The one hard safety valve in this module: disk space always wins,
        even over an explicit human hold -- see the module docstring."""
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            detection_id, assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=1,
                kinds=("evidence_native",),
            )
            session.add(orm.Review(detection_id=detection_id, actor="op", status="held"))

        class FakeUsage:
            total = 1000
            free = 100  # 90% used > 85% watermark

        import shutil as shutil_module

        monkeypatch.setattr(shutil_module, "disk_usage", lambda _path: FakeUsage())

        report = _sweeper(db, watermark_ratio=0.85).sweep()
        assert report.tier_counts.get("watermark", 0) >= 1
        with session_scope() as session:
            assert _asset(session, assets["evidence_native"]).reclaimed_at is not None


class TestExemplarPreservation:
    def test_first_of_species_survives_30_90_day_cull(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            first_id, first_assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=60,
                common_name="European Robin",
                score=0.4,
                kinds=("playback",),
            )
            # Lower score than the first-ever detection, so this one is
            # neither first-of-species nor best-of-species -- isolating the
            # "first" exemption from the "best" one, which has its own test.
            _, later_assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=40,
                common_name="European Robin",
                score=0.1,
                kinds=("playback",),
            )
        _sweeper(db).sweep()
        with session_scope() as session:
            assert _asset(session, first_assets["playback"]).reclaimed_at is None
            assert _asset(session, later_assets["playback"]).reclaimed_at is not None

    def test_best_of_species_survives_30_90_day_cull(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            # `best` is also the first-ever of this species (older / earlier
            # event_start_utc) so that this test genuinely isolates "best":
            # `mediocre` is neither first nor best and must be reclaimed
            # purely because its score loses, not because it also lost "first".
            _, best = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=45,
                common_name="European Robin",
                score=0.95,
                kinds=("playback",),
            )
            _, mediocre = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=40,
                common_name="European Robin",
                score=0.3,
                kinds=("playback",),
            )
        _sweeper(db).sweep()
        with session_scope() as session:
            assert _asset(session, mediocre["playback"]).reclaimed_at is not None
            assert _asset(session, best["playback"]).reclaimed_at is None

    def test_bat_exemplar_ranked_by_snr_not_composite_score(self, db, station_and_detector) -> None:
        """A bat pass with a lower composite `score` but higher SNR must win.

        `ultrasonic-pass-v1` never claims species, so all bat passes collapse
        into one exemplar group (see ADR-026); "best" for that group is
        `peak_snr_db`, not the pulses/SNR composite `score`.
        """
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            # `low_score_high_snr` is also the first-ever bat pass (older),
            # so the test isolates SNR-based ranking: `high_score_low_snr` is
            # reclaimed purely because it loses on SNR, not because it also
            # loses "first".
            _, low_score_high_snr = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=45,
                taxonomic_group=BAT_GROUP,
                common_name=None,
                score=0.2,
                native_result={"peak_snr_db": 30.0, "pulse_count": 20},
                kinds=("playback",),
            )
            _, high_score_low_snr = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=40,
                taxonomic_group=BAT_GROUP,
                common_name=None,
                score=0.9,
                native_result={"peak_snr_db": 13.0, "pulse_count": 3},
                kinds=("playback",),
            )
        _sweeper(db).sweep()
        with session_scope() as session:
            assert _asset(session, high_score_low_snr["playback"]).reclaimed_at is not None
            assert _asset(session, low_score_high_snr["playback"]).reclaimed_at is None


def _seed_pre_migration_detection(
    conn,
    *,
    station_id: uuid.UUID,
    detector_id: uuid.UUID,
    common_name: str,
    age_days: float,
    score: float,
    reclaimed: bool = False,
) -> uuid.UUID:
    """Insert one detection plus one un-reclaimed (unless `reclaimed`) media
    asset directly via SQL, against the schema as it exists *before*
    `0008_detection_kept` -- i.e. with no `kept_at` / `kept_by` columns.

    Deliberately not the ORM: `orm.Detection` now has `kept_at`/`kept_by`
    mapped, so an ORM insert against a pre-0008 table would fail with
    "table detection has no column named kept_at" -- which is accurate, and
    exactly why this test cannot use `_seed_detection` like the rest of the
    file. This reproduces what a station's *existing* rows genuinely look
    like the moment before this migration runs.
    """
    detection_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    event_start = FIXED_NOW - timedelta(days=age_days)

    def fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")

    conn.execute(
        sa.text(
            """
            INSERT INTO detection (
                id, station_id, detector_id, stream_id, window_id,
                event_start_utc, event_end_utc, source_start_frame, source_end_frame,
                detector_label, common_name, scientific_name, canonical_taxon_id, rank,
                taxonomic_group, score, native_result, created_at
            ) VALUES (
                :id, :station_id, :detector_id, :stream_id, :window_id,
                :event_start_utc, :event_end_utc, 0, 1,
                :label, :common_name, :sci_name, NULL, NULL,
                'bird', :score, '{}', :created_at
            )
            """
        ),
        {
            "id": detection_id.hex,
            "station_id": station_id.hex,
            "detector_id": detector_id.hex,
            "stream_id": uuid.uuid4().hex,
            "window_id": uuid.uuid4().hex,
            "event_start_utc": fmt(event_start),
            "event_end_utc": fmt(event_start + timedelta(seconds=3)),
            "label": common_name,
            "common_name": common_name,
            "sci_name": f"Sci {common_name}",
            "score": score,
            "created_at": fmt(datetime.now(UTC)),
        },
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO media_asset (
                id, kind, storage_uri, mime_type, byte_length, sha256,
                created_at, detail, reclaimed_at
            ) VALUES (
                :id, 'playback', :uri, 'audio/wav', 1024, :sha256,
                :created_at, '{}', :reclaimed_at
            )
            """
        ),
        {
            "id": asset_id.hex,
            "uri": f"/tmp/{asset_id}.wav",
            "sha256": "0" * 64,
            "created_at": fmt(datetime.now(UTC)),
            "reclaimed_at": fmt(FIXED_NOW) if reclaimed else None,
        },
    )
    conn.execute(
        sa.text(
            "INSERT INTO detection_media (detection_id, media_asset_id, role)"
            " VALUES (:d, :m, 'evidence')"
        ),
        {"d": detection_id.hex, "m": asset_id.hex},
    )
    return detection_id


class TestExemplarBackfill:
    """ADR-061: the `0008_detection_kept` migration's backfill.

    Unlike the rest of this file, these build a database with real Alembic
    migrations rather than `create_all()`, because the thing under test *is*
    the migration: it has to reproduce `_exemplar_detection_ids`'s "first"
    key exactly, from raw SQL, without the class above it to fall back on.
    """

    def _cfg(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> tuple[Config, Path]:
        db_path = tmp_path / name
        monkeypatch.setenv("OO_DATABASE_DSN", f"sqlite+pysqlite:///{db_path}")
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.attributes["configure_logger"] = False
        return cfg, db_path

    def test_backfill_keeps_the_first_of_each_species_and_nothing_else(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First-ever is irreplaceable; 'best' is not, and is not backfilled."""
        cfg, db_path = self._cfg(tmp_path, monkeypatch, "backfill.sqlite")
        # Pre-migration schema: the state every currently-deployed station is
        # in before this revision ships.
        command.upgrade(cfg, "0007_capture_pause")

        engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
        station_id = uuid.uuid4()
        detector_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO station (id, name, timezone, software_version, created_at)"
                    " VALUES (:id, 'test', 'UTC', '0.0.0', :now)"
                ),
                {"id": station_id.hex, "now": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO detector (id, plugin_id, plugin_version, model_id,"
                    " model_version, licence_name, claim, calibrated, configuration,"
                    " installed_at)"
                    " VALUES (:id, 'birdnet-v2.4', '1', 'm', '1', '', '', 0, '{}', :now)"
                ),
                {"id": detector_id.hex, "now": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")},
            )
            # Earlier robin: the one the backfill must keep.
            earlier_robin_id = _seed_pre_migration_detection(
                conn,
                station_id=station_id,
                detector_id=detector_id,
                common_name="European Robin",
                age_days=60,
                score=0.2,
            )
            # Later robin, higher score: what the old 'best' rule would have
            # kept. The backfill must leave it alone.
            later_robin_id = _seed_pre_migration_detection(
                conn,
                station_id=station_id,
                detector_id=detector_id,
                common_name="European Robin",
                age_days=40,
                score=0.9,
            )
            # A single wren: first-of-species trivially, and its own species
            # key, to prove the backfill is per-key and not global.
            wren_id = _seed_pre_migration_detection(
                conn,
                station_id=station_id,
                detector_id=detector_id,
                common_name="Eurasian Wren",
                age_days=10,
                score=0.5,
            )
        engine.dispose()

        command.upgrade(cfg, "head")

        engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
        with Session(engine) as session:
            earlier_robin = session.get(orm.Detection, earlier_robin_id)
            later_robin = session.get(orm.Detection, later_robin_id)
            wren = session.get(orm.Detection, wren_id)

        assert earlier_robin.kept_by == "exemplar-backfill"
        assert earlier_robin.kept_at is not None
        assert wren.kept_by == "exemplar-backfill"
        assert wren.kept_at is not None
        assert later_robin.kept_by is None
        assert later_robin.kept_at is None

    def test_backfill_skips_species_with_no_surviving_media(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A detection whose only media asset is already reclaimed has
        nothing left to protect, so the backfill must not touch it."""
        cfg, db_path = self._cfg(tmp_path, monkeypatch, "backfill_reclaimed.sqlite")
        command.upgrade(cfg, "0007_capture_pause")

        engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
        station_id = uuid.uuid4()
        detector_id = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO station (id, name, timezone, software_version, created_at)"
                    " VALUES (:id, 'test', 'UTC', '0.0.0', :now)"
                ),
                {"id": station_id.hex, "now": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO detector (id, plugin_id, plugin_version, model_id,"
                    " model_version, licence_name, claim, calibrated, configuration,"
                    " installed_at)"
                    " VALUES (:id, 'birdnet-v2.4', '1', 'm', '1', '', '', 0, '{}', :now)"
                ),
                {"id": detector_id.hex, "now": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")},
            )
            reclaimed_id = _seed_pre_migration_detection(
                conn,
                station_id=station_id,
                detector_id=detector_id,
                common_name="Blackbird",
                age_days=60,
                score=0.2,
                reclaimed=True,
            )
        engine.dispose()

        command.upgrade(cfg, "head")

        engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
        with Session(engine) as session:
            reclaimed = session.get(orm.Detection, reclaimed_id)
        assert reclaimed.kept_by is None
        assert reclaimed.kept_at is None


class TestWatermarkReclaim:
    def test_watermark_reclaims_oldest_first_regardless_of_tier(
        self, db, station_and_detector, monkeypatch
    ) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _, fresh_but_oldest = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=0.1,  # inside the 0-7 day tier: would normally survive
                common_name="Fresh",
                kinds=("evidence_native",),
            )
            _, newer = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=0.05,
                common_name="Newer",
                kinds=("evidence_native",),
            )

        class FakeUsage:
            total = 1000
            free = 100  # 90% used > 85% watermark

        import shutil as shutil_module

        monkeypatch.setattr(shutil_module, "disk_usage", lambda _path: FakeUsage())

        sweeper = _sweeper(db, watermark_ratio=0.85)
        report = sweeper.sweep()
        assert report.tier_counts.get("watermark", 0) >= 1
        with session_scope() as session:
            # created_at ordering is insertion order here; the older insert
            # (fresh_but_oldest) must be reclaimed before the newer one.
            oldest_reclaimed = _asset(session, fresh_but_oldest["evidence_native"]).reclaimed_at
            assert oldest_reclaimed is not None

    def test_below_watermark_does_not_touch_fresh_clips(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _, assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=0.1,
            )
        report = _sweeper(db).sweep()
        assert report.tier_counts.get("watermark", 0) == 0
        with session_scope() as session:
            assert _asset(session, assets["evidence_native"]).reclaimed_at is None


class TestDryRun:
    def test_dry_run_deletes_nothing(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _, assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=8,
            )
            native_path = Path(_asset(session, assets["evidence_native"]).storage_uri)
        report = _sweeper(db).sweep(dry_run=True)
        assert report.tier_counts.get("native", 0) == 1
        assert native_path.exists()
        with session_scope() as session:
            assert _asset(session, assets["evidence_native"]).reclaimed_at is None

    def test_dry_run_matches_real_run(self, db, station_and_detector) -> None:
        """Whatever the dry run says would happen must be exactly what does."""
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=8,
                common_name="A",
            )
            _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=40,
                common_name="B",
                kinds=("playback",),
            )
            _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=100,
                common_name="C",
                kinds=("playback",),
            )
        dry = _sweeper(db).sweep(dry_run=True)
        real = _sweeper(db).sweep(dry_run=False)
        assert dry.tier_counts == real.tier_counts
        assert dry.tier_bytes == real.tier_bytes
        assert dry.exemplar_detections == real.exemplar_detections


class TestDiskAndDbDisagreements:
    def test_row_present_file_already_gone(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _, assets = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=8,
            )
            native_path = Path(_asset(session, assets["evidence_native"]).storage_uri)
        native_path.unlink()  # simulate an operator or an earlier failure removing it
        report = _sweeper(db).sweep()
        assert report.already_missing == 1
        with session_scope() as session:
            # Still marked reclaimed: there is nothing left to reclaim, and
            # leaving it "pending" forever would make every future sweep
            # re-discover the same already-gone file.
            assert _asset(session, assets["evidence_native"]).reclaimed_at is not None

    def test_file_present_no_db_row_is_reported_not_deleted(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        orphan_dir = db.clip_dir / "2026-08-01"
        orphan_dir.mkdir(parents=True)
        orphan = orphan_dir / "orphan.wav"
        orphan.write_bytes(b"\x00" * 10)
        with session_scope() as session:
            _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=1,
            )
        # The automatic sweep must never touch a file it has no row for.
        _sweeper(db).sweep()
        assert orphan.exists()
        # The manual diagnostic finds it without deleting it.
        found = list(_sweeper(db).find_orphans())
        assert orphan.resolve() in {p.resolve() for p in found}
        assert orphan.exists()


class TestMetadataNeverDeleted:
    def test_sweep_never_deletes_a_detection_row(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            detection_id, _ = _seed_detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                clip_dir=db.clip_dir,
                age_days=200,  # well past every tier, including final expiry
            )
        with session_scope() as session:
            before = session.get(orm.Detection, detection_id)
            snapshot = {
                "common_name": before.common_name,
                "score": before.score,
                "event_start_utc": before.event_start_utc,
                "taxonomic_group": before.taxonomic_group,
            }
        _sweeper(db).sweep()
        with session_scope() as session:
            after = session.get(orm.Detection, detection_id)
            assert after is not None
            assert after.common_name == snapshot["common_name"]
            assert after.score == snapshot["score"]
            assert after.event_start_utc == snapshot["event_start_utc"]
            assert after.taxonomic_group == snapshot["taxonomic_group"]

    def test_detection_count_unchanged_by_a_sweep(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            for age in (1, 10, 40, 100):
                _seed_detection(
                    session,
                    station_id=station_id,
                    detector_id=detector_id,
                    clip_dir=db.clip_dir,
                    age_days=age,
                )
        with session_scope() as session:
            before_count = session.query(orm.Detection).count()
        _sweeper(db).sweep()
        with session_scope() as session:
            after_count = session.query(orm.Detection).count()
        assert before_count == after_count == 4


class TestBoundedWork:
    def test_batch_size_bounds_deletions_per_call(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            for index in range(10):
                _seed_detection(
                    session,
                    station_id=station_id,
                    detector_id=detector_id,
                    clip_dir=db.clip_dir,
                    age_days=8 + index,
                    common_name=f"species-{index}",
                    kinds=("evidence_native",),
                )
        sweeper = _sweeper(db, batch_size=3, batch_budget_s=30.0)
        report = sweeper.sweep()
        assert report.tier_counts.get("native", 0) == 3
        assert report.complete is False

    def test_repeated_bounded_sweeps_eventually_clear_the_backlog(
        self, db, station_and_detector
    ) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            for index in range(10):
                _seed_detection(
                    session,
                    station_id=station_id,
                    detector_id=detector_id,
                    clip_dir=db.clip_dir,
                    age_days=8 + index,
                    common_name=f"species-{index}",
                    kinds=("evidence_native",),
                )
        sweeper = _sweeper(db, batch_size=3, batch_budget_s=30.0)
        total = 0
        for _ in range(10):
            report = sweeper.sweep()
            total += report.tier_counts.get("native", 0)
            if report.complete:
                break
        assert total == 10


@pytest.mark.parametrize("age_days", [0.0, 3.5, 6.99])
def test_below_native_boundary_never_strips_native(db, station_and_detector, age_days) -> None:
    station_id, detector_id = station_and_detector
    with session_scope() as session:
        _, assets = _seed_detection(
            session,
            station_id=station_id,
            detector_id=detector_id,
            clip_dir=db.clip_dir,
            age_days=age_days,
        )
    _sweeper(db).sweep()
    with session_scope() as session:
        assert _asset(session, assets["evidence_native"]).reclaimed_at is None


class TestReportInvariants:
    """`RetentionReport`'s bookkeeping is a pure function of the decisions
    appended to it -- worth checking with generated data rather than only the
    hand-picked cases above, since the tier bookkeeping is exactly what an
    operator's "explain this deletion" request would rely on being correct.
    """

    @given(
        tiers_and_bytes=st.lists(
            st.tuples(
                st.sampled_from(["native", "exemplar_only", "expired", "watermark"]),
                st.integers(min_value=0, max_value=10_000_000),
                st.booleans(),  # existed_on_disk
            ),
            max_size=20,
        )
    )
    def test_totals_match_sum_of_recorded_tiers(self, tiers_and_bytes) -> None:
        from open_observatory.retention import RetentionDecision, RetentionReport

        report = RetentionReport()
        for tier, size, existed in tiers_and_bytes:
            report.decisions.append(
                RetentionDecision(
                    asset_id=uuid.uuid4(),
                    detection_id=uuid.uuid4(),
                    path="/x",
                    kind="playback",
                    tier=tier,
                    reason="test",
                    bytes=size,
                    existed_on_disk=existed,
                )
            )
            report.tier_counts[tier] = report.tier_counts.get(tier, 0) + 1
            report.tier_bytes[tier] = report.tier_bytes.get(tier, 0) + (size if existed else 0)

        assert report.total_deleted == len(tiers_and_bytes)
        assert report.total_bytes == sum(
            size for _tier, size, existed in tiers_and_bytes if existed
        )
        # Every recorded byte total is non-negative and traceable to a decision.
        assert all(v >= 0 for v in report.tier_bytes.values())
        payload = report.to_dict()
        assert payload["total_deleted"] == report.total_deleted
        assert payload["total_bytes"] == report.total_bytes
