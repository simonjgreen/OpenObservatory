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
    """Create the schema directly.

    Used by tests and by first-run bootstrap on the SQLite developer profile.
    The PostgreSQL profile is expected to go through Alembic.
    """
    Base.metadata.create_all(engine or get_engine())


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
