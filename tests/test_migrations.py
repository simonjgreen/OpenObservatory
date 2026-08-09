"""Alembic migration environment tests.

Covers the four things the migration environment must prove honest, per
``docs/data/DATA_MODEL.md`` and ADR-035:

1. the initial revision (``0001_initial``) matches what
   ``open_observatory.db.session.create_all()`` actually builds -- no drift;
2. upgrade/downgrade round-trips cleanly on a throwaway SQLite database;
3. stamping a pre-existing (``create_all()``-built) database at head, rather
   than upgrading it, does not touch its data and leaves no detected drift;
4. the concrete gap this environment was built to close -- a column added by
   the old ``ALTER TABLE`` patcher without its index -- is repaired by
   stamping at ``0001_initial`` and then running an ordinary ``upgrade head``.

These use the real ``alembic/`` directory in the repository root (via
``alembic.ini`` + ``alembic/env.py``), not a synthetic copy, so a change to
either breaks these tests the same way it would break a real migration run.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from open_observatory.config import REPO_ROOT
from open_observatory.db.models import Base, MediaAsset, Station
from open_observatory.db.session import create_all

ALL_TABLE_NAMES = set(Base.metadata.tables)


def _alembic_config(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> Config:
    """An Alembic ``Config`` pointed at ``db_path`` via the same
    ``OO_DATABASE_DSN`` resolution the application and ``alembic/env.py`` use.
    """
    monkeypatch.setenv("OO_DATABASE_DSN", f"sqlite+pysqlite:///{db_path}")
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.attributes["configure_logger"] = False
    return cfg


def _table_names(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def _index_names(engine: sa.Engine, table: str) -> set[str]:
    return {ix["name"] for ix in sa.inspect(engine).get_indexes(table)}


def test_initial_revision_matches_create_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`create_all()` and `alembic upgrade head` must build the identical schema.

    This is the honesty check the brief calls for: stamp a `create_all()`
    database at head and ask Alembic whether it sees anything left to do. It
    must say no.
    """
    db_path = tmp_path / "create_all.sqlite"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    create_all(engine)

    cfg = _alembic_config(monkeypatch, db_path)
    command.stamp(cfg, "head")
    # Raises CommandError if autogenerate would still detect operations.
    command.check(cfg)


def test_upgrade_head_from_empty_matches_create_all_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A brand-new database built purely by migrations has the same tables
    (and the same indexes) as one built by `create_all()`.
    """
    migrated_path = tmp_path / "migrated.sqlite"
    cfg = _alembic_config(monkeypatch, migrated_path)
    command.upgrade(cfg, "head")
    migrated_engine = sa.create_engine(f"sqlite+pysqlite:///{migrated_path}", future=True)

    created_path = tmp_path / "created.sqlite"
    created_engine = sa.create_engine(f"sqlite+pysqlite:///{created_path}", future=True)
    create_all(created_engine)

    assert _table_names(migrated_engine) - {"alembic_version"} == ALL_TABLE_NAMES
    assert _table_names(migrated_engine) - {"alembic_version"} == _table_names(created_engine)
    for table in ALL_TABLE_NAMES:
        assert _index_names(migrated_engine, table) == _index_names(created_engine, table), table


def test_upgrade_downgrade_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "roundtrip.sqlite"
    cfg = _alembic_config(monkeypatch, db_path)

    command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    assert _table_names(engine) >= ALL_TABLE_NAMES

    command.downgrade(cfg, "base")
    engine.dispose()
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    remaining = _table_names(engine) - {"alembic_version"}
    assert remaining == set(), f"downgrade to base left tables behind: {remaining}"

    # And it upgrades cleanly again -- downgrade did not leave stray state
    # (e.g. a half-dropped batch-mode shadow table) that trips a second pass.
    command.upgrade(cfg, "head")
    engine.dispose()
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    assert _table_names(engine) >= ALL_TABLE_NAMES


def test_stamping_existing_database_is_a_data_preserving_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adoption path for a database that already has the current schema
    (the live station's case): stamp, don't upgrade. No DDL runs, no data
    moves.
    """
    db_path = tmp_path / "existing.sqlite"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    create_all(engine)

    station_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(Station(id=station_id, name="Test Station", timezone="UTC"))
        session.commit()

    cfg = _alembic_config(monkeypatch, db_path)
    command.stamp(cfg, "head")
    command.check(cfg)  # no drift detected -- confirms stamp did not need to alter anything

    with Session(engine) as session:
        stations = session.query(Station).all()
    assert [s.id for s in stations] == [station_id]


def test_stamp_0001_then_upgrade_head_repairs_the_missing_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces the actual gap found on the live station while building this
    environment: ``media_asset.reclaimed_at`` was added by the old
    ``ALTER TABLE ... ADD COLUMN`` patcher, which cannot create an index, so
    its index never existed there. Revision 0002 exists to close exactly this
    gap, and the adoption sequence (stamp at the schema-matching baseline,
    then upgrade normally) must reach it and preserve data while doing so.
    """
    db_path = tmp_path / "patched.sqlite"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    create_all(engine)

    asset_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(
            MediaAsset(
                id=asset_id,
                kind="evidence_native",
                storage_uri="file:///tmp/example.wav",
                mime_type="audio/wav",
                sha256="0" * 64,
            )
        )
        session.commit()

    # Simulate "column exists, index never got created" -- what ADD COLUMN
    # alone produces.
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_media_asset_reclaimed_at")
    assert "ix_media_asset_reclaimed_at" not in _index_names(engine, "media_asset")

    cfg = _alembic_config(monkeypatch, db_path)
    command.stamp(cfg, "0001_initial")
    command.upgrade(cfg, "head")

    engine.dispose()
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    assert "ix_media_asset_reclaimed_at" in _index_names(engine, "media_asset")
    command.check(cfg)

    with Session(engine) as session:
        assets = session.query(MediaAsset).all()
    assert [a.id for a in assets] == [asset_id]


def test_0004_drops_and_restores_the_four_dead_detection_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-037 option B: revision 0004 drops
    ``ix_detection_station_start``, ``ix_detection_station_id``,
    ``ix_detection_taxonomic_group`` and ``ix_detection_canonical_taxon_id``
    -- confirmed dead by re-verification against the live database -- and
    keeps ``ix_detection_detector_id``, which a real query
    (``plausibility_repair.reconcile_plausibility``) was found to use.
    Upgrading to head must remove exactly the first four; downgrading one
    step must restore all four.
    """
    db_path = tmp_path / "drop_indexes.sqlite"
    cfg = _alembic_config(monkeypatch, db_path)

    command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    at_head = _index_names(engine, "detection")
    assert "ix_detection_station_start" not in at_head
    assert "ix_detection_station_id" not in at_head
    assert "ix_detection_taxonomic_group" not in at_head
    assert "ix_detection_canonical_taxon_id" not in at_head
    assert "ix_detection_detector_id" in at_head
    assert "ix_detection_group_start" in at_head
    assert "ix_detection_event_start_utc" in at_head
    assert "ix_detection_stream_id" in at_head

    # Explicit revision, not "-1": head has moved past 0004 since this test
    # was written (ADR-043, revision 0005), and "-1" is relative to whatever
    # head currently is, not to this revision specifically.
    command.downgrade(cfg, "0003_auth_tables")
    engine.dispose()
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    restored = _index_names(engine, "detection")
    assert "ix_detection_station_start" in restored
    assert "ix_detection_station_id" in restored
    assert "ix_detection_taxonomic_group" in restored
    assert "ix_detection_canonical_taxon_id" in restored

    # And upgrading again is clean -- no "index already exists" from the
    # restored indexes.
    command.upgrade(cfg, "head")
    engine.dispose()
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    command.check(cfg)


def test_alembic_cli_x_url_override(tmp_path: Path) -> None:
    """The ``-x url=`` override documented in ``alembic/env.py`` works from the
    command line, independent of ``OO_DATABASE_DSN`` (used by tooling that
    should not depend on process environment, e.g. a one-off inspection of an
    arbitrary database file).
    """
    db_path = tmp_path / "cli.sqlite"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-x",
            f"url=sqlite+pysqlite:///{db_path}",
            "upgrade",
            "head",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    assert _table_names(engine) >= ALL_TABLE_NAMES
