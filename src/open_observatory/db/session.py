"""Engine and session management.

Synchronous SQLAlchemy, deliberately. FastAPI runs ``def`` endpoints in a thread
pool, and the pipeline writes through :func:`asyncio.to_thread`, so blocking
drivers never touch the event loop. That keeps Alembic, the ORM and the debug
shell all working the ordinary way.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import structlog
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from .models import Base

log = structlog.get_logger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _configure_sqlite(engine: Engine) -> None:
    """WAL and a busy timeout, so a reader never blocks the capture writer."""

    @event.listens_for(engine, "connect")
    def _on_connect(connection, _record) -> None:  # type: ignore[no-untyped-def]
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def init_engine(settings: Settings, *, echo: bool = False) -> Engine:
    global _engine, _session_factory
    dsn = settings.resolved_database_dsn
    if dsn.startswith("sqlite"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        # Sessions are used from FastAPI's thread pool and from to_thread calls.
        _engine = create_engine(
            dsn, echo=echo, future=True, connect_args={"check_same_thread": False}
        )
        _configure_sqlite(_engine)
    else:
        _engine = create_engine(dsn, echo=echo, future=True, pool_pre_ping=True)
    _session_factory = sessionmaker(_engine, expire_on_commit=False, future=True)
    log.info("db.engine_ready", dsn=_redact(dsn))
    return _engine


def _redact(dsn: str) -> str:
    if "@" in dsn and "//" in dsn:
        scheme, _, rest = dsn.partition("//")
        _, _, host = rest.partition("@")
        return f"{scheme}//***@{host}"
    return dsn


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database engine not initialised; call init_engine() first")
    return _engine


def create_all(engine: Engine | None = None) -> None:
    """Create the schema directly, then patch in any columns added since.

    Used by tests and by first-run bootstrap on the SQLite developer profile.
    The PostgreSQL profile is expected to go through Alembic.

    ``create_all`` only creates *missing tables* -- it never alters an existing
    one, so a column added to a model after a SQLite database already exists
    (as ``audio_stream.last_frame_at_utc`` was, ADR-024) would silently never
    appear on a developer's or the station's existing file. SQLite's own
    ``ALTER TABLE ADD COLUMN`` is cheap and safe for a nullable column, so
    :func:`_patch_sqlite_columns` applies it defensively on every startup.
    """
    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        _patch_sqlite_columns(engine)


def _patch_sqlite_columns(engine: Engine) -> None:
    """Add any model column missing from an existing SQLite table.

    A stop-gap for the developer/on-device SQLite profile, which has no Alembic
    migrations (ADR-007). Only additive, nullable columns are handled -- exactly
    the shape a heartbeat or similar diagnostic column takes -- because that is
    the only kind ``ALTER TABLE ADD COLUMN`` can do without a table rebuild.
    """
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            existing = {
                row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info("{table.name}")')
            }
            if not existing:
                continue  # table itself doesn't exist yet; create_all() would have made it
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl_type = column.type.compile(dialect=engine.dialect)
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}'
                )
                log.info("db.column_patched", table=table.name, column=column.name)


@contextmanager
def session_scope() -> Iterator[Session]:
    if _session_factory is None:
        raise RuntimeError("database engine not initialised; call init_engine() first")
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as session:
        yield session


def database_file(settings: Settings) -> Path | None:
    dsn = settings.resolved_database_dsn
    if dsn.startswith("sqlite") and ":///" in dsn:
        return Path(dsn.split(":///", 1)[1])
    return None
