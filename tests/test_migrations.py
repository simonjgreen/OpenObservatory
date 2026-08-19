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

ADR-042 adds a fifth thing: ``ensure_schema_at_head`` -- what application and
CLI startup now call instead of ``create_all()``/the ALTER TABLE patcher --
must bootstrap a genuinely empty database, be a safe idempotent no-op against
one already at head (this is what runs on every ``deploy/deploy.sh`` and
every process startup against the live station), and refuse rather than
silently proceed against one that is unstamped or stale.

These use the real ``alembic/`` directory in the repository root (via
``alembic.ini`` + ``alembic/env.py``), not a synthetic copy, so a change to
either breaks these tests the same way it would break a real migration run.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from open_observatory.config import REPO_ROOT
from open_observatory.db.models import Base, Detection, MediaAsset, Station
from open_observatory.db.session import create_all, ensure_schema_at_head

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


@pytest.fixture
def migrated_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    """A throwaway SQLite database migrated to head, with an open ORM session."""
    db_path = tmp_path / "migrated_session.sqlite"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    with Session(engine) as session:
        yield session
    engine.dispose()


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
    """The adoption sequence -- stamp at the schema-matching baseline, then
    upgrade normally -- must reach head and preserve data while doing so.

    Originally this asserted that revision 0002 *created*
    ``ix_media_asset_reclaimed_at``, closing a real gap on the live station
    where the old ``ALTER TABLE ... ADD COLUMN`` patcher had added the column
    without its index. Revision 0011 (ADR-062) then deliberately dropped that
    index -- it was displacing the partial indexes retention actually needs --
    so head no longer has it and the original assertion would now be asserting
    a bug. What still matters, and is what this test kept, is that a database
    adopted at the baseline arrives at head with its rows intact and with the
    indexes head is supposed to have. 0002 still runs on the way through; 0011
    undoes it, on purpose, in that order.
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

    cfg = _alembic_config(monkeypatch, db_path)
    command.stamp(cfg, "0001_initial")
    command.upgrade(cfg, "head")

    engine.dispose()
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    asset_indexes = _index_names(engine, "media_asset")
    assert "ix_media_asset_reclaimed_at" not in asset_indexes
    assert "ix_media_asset_live_kind_created" in asset_indexes
    with Session(engine) as session:
        assert session.get(MediaAsset, asset_id) is not None
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
    Upgrading to head must remove exactly the first four; downgrading *to the
    revision before 0004* must restore all four.

    Named explicitly rather than as ``-1``: ``-1`` meant "undo 0004" only while
    0004 was head, and silently became "undo 0005" when the refinement revision
    landed (ADR-045). A relative step in a test is a claim about what head is,
    which is exactly the kind of quietly-wrong assertion this project keeps
    finding after the fact.
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
    # was written (ADR-043 added 0005, ADR-045 added 0006), and "-1" is
    # relative to whatever head currently is, not to this revision.
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


# --- ensure_schema_at_head (ADR-042): the production bootstrap path ---------


def _seed_detections(engine: sa.Engine, count: int) -> tuple[uuid.UUID, uuid.UUID]:
    """A station, a detector, and ``count`` detection rows -- the shape of a
    real station database, not just an empty schema."""
    from open_observatory.db.models import Detector

    station_id = uuid.uuid4()
    detector_id = uuid.uuid4()
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add(Station(id=station_id, name="Test Station", timezone="UTC"))
        session.add(
            Detector(
                id=detector_id,
                plugin_id="birdnet-v2.4",
                plugin_version="1",
                model_id="birdnet",
                model_version="2.4",
            )
        )
        session.flush()
        for i in range(count):
            session.add(
                Detection(
                    id=uuid.uuid4(),
                    station_id=station_id,
                    detector_id=detector_id,
                    stream_id=uuid.uuid4(),
                    window_id=uuid.uuid4(),
                    event_start_utc=now,
                    event_end_utc=now,
                    source_start_frame=i,
                    source_end_frame=i + 1,
                    common_name=f"Species {i}",
                    score=0.9,
                )
            )
        session.commit()
    return station_id, detector_id


def test_ensure_schema_at_head_bootstraps_a_genuinely_empty_database(
    tmp_path: Path,
) -> None:
    """A fresh developer checkout or a brand-new station: no file at all yet.
    ``ensure_schema_at_head`` must build the full schema itself, exactly like
    ``alembic upgrade head`` would (it *is* that call for this case)."""
    db_path = tmp_path / "fresh.sqlite"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)

    ensure_schema_at_head(engine)

    assert _table_names(engine) - {"alembic_version"} == ALL_TABLE_NAMES
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "select version_num from alembic_version"
        ).scalar() is not None


def test_ensure_schema_at_head_is_an_idempotent_noop_at_head(tmp_path: Path) -> None:
    """The exact shape of every real deploy and every process startup against
    the live station: a database already at head. Running it twice must be
    safe, change nothing, and preserve every row -- this is the idempotency
    and safety-against-real-data check the task calls for, run here against a
    database seeded with several thousand detection rows standing in for the
    live station's ~65,000."""
    db_path = tmp_path / "at_head.sqlite"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.attributes["configure_logger"] = False
    cfg.cmd_opts = argparse.Namespace(x=[f"url=sqlite+pysqlite:///{db_path}"])
    command.upgrade(cfg, "head")

    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    _seed_detections(engine, 2_000)
    engine.dispose()

    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    with engine.connect() as connection:
        before_count = connection.exec_driver_sql("select count(*) from detection").scalar()
        before_rev = connection.exec_driver_sql(
            "select version_num from alembic_version"
        ).scalar()

    # Run it twice -- the second call is the idempotency check.
    ensure_schema_at_head(engine)
    ensure_schema_at_head(engine)

    with engine.connect() as connection:
        after_count = connection.exec_driver_sql("select count(*) from detection").scalar()
        after_rev = connection.exec_driver_sql("select version_num from alembic_version").scalar()

    assert after_count == before_count == 2_000
    assert after_rev == before_rev


def test_ensure_schema_at_head_refuses_an_unstamped_pre_alembic_database(
    tmp_path: Path,
) -> None:
    """A database with tables (built by the retired ``create_all()``/ALTER
    TABLE bootstrap, or a ``create_all()``-built developer/test database that
    was never adopted) but no Alembic version row. Silently proceeding here
    is exactly the drift this task exists to close off -- it must refuse."""
    db_path = tmp_path / "unstamped.sqlite"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    create_all(engine)
    _seed_detections(engine, 5)

    with pytest.raises(RuntimeError, match="no Alembic version stamp"):
        ensure_schema_at_head(engine)

    # Refusing to proceed must not have touched the data.
    with engine.connect() as connection:
        assert connection.exec_driver_sql("select count(*) from detection").scalar() == 5


def test_ensure_schema_at_head_refuses_a_stale_database(tmp_path: Path) -> None:
    """A database stamped at an old revision (a deploy that synced new code
    but whose migration step failed or was skipped) must refuse rather than
    start the service against a schema older than the code expects."""
    db_path = tmp_path / "stale.sqlite"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.attributes["configure_logger"] = False
    cfg.cmd_opts = argparse.Namespace(x=[f"url=sqlite+pysqlite:///{db_path}"])
    command.upgrade(cfg, "0001_initial")

    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    with pytest.raises(RuntimeError, match="expects"):
        ensure_schema_at_head(engine)


# --- 0008_detection_kept (ADR-061) -------------------------------------------


def test_kept_columns_exist_at_head(migrated_session: Session) -> None:
    """ADR-061: retention filters on these in SQL, so they must be real columns."""
    columns = {c["name"] for c in sa.inspect(migrated_session.bind).get_columns("detection")}
    assert "kept_at" in columns
    assert "kept_by" in columns


def test_kept_at_is_deliberately_not_indexed(migrated_session: Session) -> None:
    """`ix_detection_kept_at` was a pessimisation and revision 0009 drops it.

    It was added on the reasoning that every tier's candidate query filters on
    `kept_at`, so the filter should be indexed. That reasoning was wrong, and
    wrong in a way that took the station down: `kept_at IS NULL` matches ~99.8%
    of rows (112 non-null out of ~46,000 on the live station), so the index is
    useless as a filter -- but SQLite *preferred* it anyway, which cost it
    `ix_detection_event_start_utc`, the index serving both the range predicate
    and the `ORDER BY`. The plan fell back to materialising the join into a
    temp B-tree and sorting it.

    Measured on the live station's own database, `_strip_native`'s candidate
    query, best of three:

        with the index:     SEARCH d USING ix_detection_kept_at
                            + USE TEMP B-TREE FOR ORDER BY      0.555 s
        without the index:  SEARCH d USING ix_detection_event_start_utc
                            (no sort)                           0.117 s

    Under live WAL contention the indexed plan blocked a real sweep for over
    five minutes inside one statement, which wedged the whole housekeeping loop
    behind it -- stream heartbeats, the media audit and the disk-usage refresh
    all stopped (ADR-061, and the incident note in HANDOVER).

    So this asserts the *absence* of an index on purpose. If a future change
    adds one back, the query plan must be re-measured on a database of the
    station's size first: no test fixture here is large enough for SQLite to
    make the bad choice.
    """
    indexes = {i["name"] for i in sa.inspect(migrated_session.bind).get_indexes("detection")}
    assert "ix_detection_kept_at" not in indexes
    # The index that must survive, because it serves the filter *and* the order.
    assert "ix_detection_event_start_utc" in indexes


def test_kept_at_has_a_partial_index_that_cannot_steal_the_ordered_plan(
    migrated_session: Session,
) -> None:
    """Revision 0010: an index over only the *kept* rows, and only those.

    Dropping the full index in 0009 fixed `_strip_native` and broke something
    else: `RetentionReport.kept_detections` counts `kept_at IS NOT NULL`, which
    without an index is a full `SCAN detection` over 290,956 rows. On the live
    station that ran ~6 s under WAL contention -- and because it sits in the
    sweep's preamble, before any tier guard, it spent the entire 1.5 s budget
    and every tier was skipped again. Same silent-zero-deletions symptom, a
    different cause.

    A *partial* index is the shape that satisfies both. It contains only the
    non-null rows (112 on the station, against 290,956), so:

      * `kept_at IS NOT NULL` can use it -- the count becomes a covering index
        lookup, measured 0.151 s -> 0.000 s;
      * `kept_at IS NULL` **cannot** use it, so it cannot be preferred over
        `ix_detection_event_start_utc` in `_strip_native`, whose plan is
        measured unchanged at 0.113 s -> 0.115 s with no temp B-tree.

    That asymmetry is the whole point, so this test asserts the `WHERE` clause
    is really there. A plain index on the same column would pass a bare
    name check and reintroduce the outage.
    """
    sql = migrated_session.execute(
        sa.text(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='ix_detection_kept_at_partial'"
        )
    ).scalar_one_or_none()
    assert sql is not None, "the partial index is missing"
    assert "WHERE kept_at IS NOT NULL" in sql.replace("\n", " "), sql


def test_live_asset_indexes_are_partial_and_the_plain_one_is_gone(
    migrated_session: Session,
) -> None:
    """Revision 0011 (ADR-062): the third index incident on this project.

    Both new indexes must be *partial* on `reclaimed_at IS NULL`. That clause
    is the mechanism, not a detail: it is what makes a reclaimed row leave the
    index, so a sweep never walks past work it has already done. Without it the
    native tier re-examined ~210,000 already-reclaimed detections every pass to
    find the ~1,699 outstanding, taking 2.2 s against a 1.5 s budget -- so it
    reclaimed nothing at all, for two days, while the disk climbed.

    `ix_media_asset_reclaimed_at` (added by revision 0001) must be gone, and
    that is load-bearing too. `reclaimed_at` is NULL on 176,231 of 214,499
    rows, so SQLite treated a plain index on it as a cheap way in and took it
    over the partial ones, losing the `ORDER BY` and adding a temp B-tree:
    0.0004 s -> 0.1215 s. A name check alone would not catch a plain index
    being reintroduced under the new names, so the `WHERE` clauses are
    asserted directly.
    """
    rows = dict(
        migrated_session.execute(
            sa.text(
                "SELECT name, sql FROM sqlite_master WHERE type='index' "
                "AND tbl_name IN ('media_asset', 'detection_media')"
            )
        ).all()
    )

    assert "ix_media_asset_reclaimed_at" not in rows, (
        "the plain reclaimed_at index is back; it displaces the partial indexes"
    )

    for name in ("ix_media_asset_live_kind_created", "ix_media_asset_live_created"):
        assert name in rows, f"{name} is missing"
        assert "WHERE reclaimed_at IS NULL" in (rows[name] or "").replace("\n", " "), rows[name]

    assert "ix_detection_media_asset" in rows, (
        "the asset -> detection join has no index; retention scans the link table"
    )


def test_revision_0011_downgrade_restores_the_previous_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rollback note in `docs/delivery/MILESTONE_STATUS.md` has to be true.

    A downgrade that left the new indexes in place would silently keep the new
    query plans, so a rollback would not actually roll the behaviour back.
    """
    db_path = tmp_path / "rev0011.sqlite"
    cfg = _alembic_config(monkeypatch, db_path)
    command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}", future=True)

    assert "ix_media_asset_live_kind_created" in _index_names(engine, "media_asset")

    command.downgrade(cfg, "0010_kept_at_partial_index")
    asset_indexes = _index_names(engine, "media_asset")
    assert "ix_media_asset_reclaimed_at" in asset_indexes
    assert "ix_media_asset_live_kind_created" not in asset_indexes
    assert "ix_media_asset_live_created" not in asset_indexes
    assert "ix_detection_media_asset" not in _index_names(engine, "detection_media")

    command.upgrade(cfg, "head")
    asset_indexes = _index_names(engine, "media_asset")
    assert "ix_media_asset_reclaimed_at" not in asset_indexes
    assert "ix_media_asset_live_kind_created" in asset_indexes
    engine.dispose()
