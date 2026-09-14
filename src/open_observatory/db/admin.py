"""Operator-facing facts and operations on the SQLite file (ADR-078).

Everything here opens the database file directly with :mod:`sqlite3` rather
than through the engine, because two of the three callers -- a copy and a
check -- are run against a file that is *not* the configured database, and the
third (`status`) must never trigger the schema bootstrap that
:func:`~open_observatory.db.session.ensure_schema_at_head` performs on an
empty file. Nothing in this module writes to the source database.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import TypedDict

#: Tables whose row counts an operator compares before and after a copy. Each
#: is counted only if it exists, so the same command works on an old database.
COUNTED_TABLES: tuple[str, ...] = (
    "detection",
    "media_asset",
    "audio_stream",
    "capture_gap",
    "review",
    "refinement",
)


class SqliteFacts(TypedDict):
    page_size: int
    page_count: int
    freelist_count: int
    journal_mode: str
    alembic_revision: str | None
    row_counts: dict[str, int]


class CopyResult(TypedDict):
    source: str
    destination: str
    bytes: int
    elapsed_s: float


class CheckResult(SqliteFacts):
    path: str
    check: str
    ok: bool
    problems: list[str]
    elapsed_s: float


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def sqlite_facts(path: Path) -> SqliteFacts:
    """Page geometry, journal mode, Alembic revision and row counts of `path`.

    Read-only by construction: every statement is a PRAGMA or a SELECT.
    """
    with _connect(path) as connection:
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        revision = None
        if "alembic_version" in tables:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            revision = row[0] if row else None
        counts: dict[str, int] = {}
        for table in COUNTED_TABLES:
            if table in tables:
                counts[table] = int(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                )
    return {
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist,
        "journal_mode": journal_mode,
        "alembic_revision": revision,
        "row_counts": counts,
    }


def copy_database(source: Path, destination: Path, *, force: bool = False) -> CopyResult:
    """Write a consistent copy of `source` at `destination` with ``VACUUM INTO``.

    ``VACUUM INTO`` reads the whole database inside one read transaction, so a
    station that keeps writing throughout still yields a copy that is exactly
    the database as of the moment the transaction opened. Unlike
    :meth:`sqlite3.Connection.backup` -- measured never converging on the live
    station on 2026-08-29, because it restarts whenever the source changes --
    it never restarts. The copy is compact (no free pages) and in
    rollback-journal mode; the station switches it to WAL on first open.

    Refuses to overwrite unless `force`, and refuses to copy a file onto itself.
    """
    source = source.resolve()
    destination = destination.resolve()
    if destination == source:
        raise ValueError("destination is the source database")
    if destination.exists():
        if not force:
            raise FileExistsError(f"{destination} already exists; pass --force to replace it")
        destination.unlink()
    for suffix in ("-wal", "-shm"):
        stale = destination.with_name(destination.name + suffix)
        if stale.exists():
            stale.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with _connect(source) as connection:
        connection.execute("VACUUM INTO ?", (str(destination),))
    elapsed = time.monotonic() - started
    return {
        "source": str(source),
        "destination": str(destination),
        "bytes": destination.stat().st_size,
        "elapsed_s": round(elapsed, 1),
    }


def check_database(path: Path, *, full: bool = False) -> CheckResult:
    """``PRAGMA quick_check`` (or ``integrity_check`` with `full`) plus the facts.

    ``ok`` is true only when the check returns the single row ``ok``. The
    first few problem rows are returned verbatim so a failure names itself.
    """
    pragma = "integrity_check" if full else "quick_check"
    started = time.monotonic()
    with _connect(path) as connection:
        rows = [str(row[0]) for row in connection.execute(f"PRAGMA {pragma}").fetchall()]
    facts = sqlite_facts(path)
    return {
        "path": str(path),
        "check": pragma,
        "ok": rows == ["ok"],
        "problems": rows[:10] if rows != ["ok"] else [],
        "elapsed_s": round(time.monotonic() - started, 1),
        **facts,
    }
