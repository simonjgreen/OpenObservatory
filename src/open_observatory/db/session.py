"""Engine and session management.

Synchronous SQLAlchemy, deliberately. FastAPI runs ``def`` endpoints in a thread
pool, and the pipeline writes through :func:`asyncio.to_thread`, so blocking
drivers never touch the event loop. That keeps Alembic, the ORM and the debug
shell all working the ordinary way.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
import structlog
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..config import REPO_ROOT, Settings
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
    """Create the schema directly from ``Base.metadata``, bypassing Alembic.

    **Not the production bootstrap path any more (ADR-041).** Application and
    CLI startup call :func:`ensure_schema_at_head` instead, which goes through
    Alembic so there is exactly one thing that decides what the schema looks
    like. This function still exists for two legitimate, test-only uses:

    1. ``tests/test_migrations.py`` builds a ``create_all()`` database and
       asks Alembic (``alembic check``) whether it sees any drift from
       ``head`` -- that comparison is what keeps the migrations honest, and
       it needs a non-Alembic way to build the "expected" schema to compare
       against.
    2. Other tests that want a fast, disposable schema for a scenario
       unrelated to migrations themselves.

    It never alters an existing table -- only ``CREATE TABLE`` for tables
    that do not exist yet -- so it must not be pointed at a database that
    already has some but not all of the current tables; that is exactly the
    situation :func:`ensure_schema_at_head` refuses to paper over.
    """
    engine = engine or get_engine()
    Base.metadata.create_all(engine)


def _alembic_config() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.attributes["configure_logger"] = False
    return cfg


def ensure_schema_at_head(engine: Engine | None = None) -> None:
    """Verify the schema is under Alembic's control and at ``head``.

    Replaces the old ``create_all()`` + ``ALTER TABLE`` patcher as the
    production bootstrap (ADR-041): Alembic is now the only thing that
    creates or changes schema, so there is one description of the schema
    (the migrations) instead of two (the migrations *and* whatever
    ``create_all()``/the patcher happened to build) that could silently
    drift apart -- which is exactly what happened before ADR-035.

    Deliberately does **not** run a migration against a database that
    already has tables, even an out-of-date one. ``deploy/deploy.sh`` runs
    ``alembic upgrade head`` as an explicit step before the service is
    (re)started (ADR-041), specifically so that a slow or failing migration
    is caught by the deploy script -- which can fail loudly and leave the
    previous, working version running -- rather than by this function
    blocking or failing on capture's own startup path. Capture wins: this
    check is a single fast read (``PRAGMA``/catalog query, no DDL), never a
    write.

    Two cases:

    - **A completely empty database** (no tables at all -- a fresh developer
      checkout, a brand-new station, or a test's throwaway SQLite file) is
      bootstrapped by running ``alembic upgrade head`` directly. Starting
      from nothing, this *is* revision ``0001_initial``'s ``CREATE TABLE``
      -- fast, and by construction identical to what ``create_all()`` would
      have built, because ``tests/test_migrations.py`` asserts exactly that.
    - **A database that already has tables** must already be under
      Alembic's control and at ``head``. If it has no ``alembic_version``
      row at all, it is a pre-Alembic database that was never adopted (see
      docs/data/DATA_MODEL.md "Which case are you in?"). If it has one but
      it does not match ``head``, a migration is owed. Either way this
      raises rather than starting the service against a schema the code
      does not expect.
    """
    engine = engine or get_engine()
    table_names = set(sa.inspect(engine).get_table_names())

    cfg = _alembic_config()
    script = ScriptDirectory.from_config(cfg)
    head_rev = script.get_current_head()

    if not table_names:
        log.info("db.schema_bootstrap", detail="empty database; running alembic upgrade head")
        cfg.cmd_opts = argparse.Namespace(x=[f"url={engine.url.render_as_string(hide_password=False)}"])
        command.upgrade(cfg, "head")
        return

    with engine.connect() as connection:
        current_rev = MigrationContext.configure(connection).get_current_revision()

    if current_rev is None:
        raise RuntimeError(
            "Database has tables but no Alembic version stamp -- this looks like a "
            "pre-Alembic database (built by create_all() before the Alembic "
            "environment existed, or the ALTER TABLE patcher retired in ADR-041). "
            "Adopt it first: `alembic stamp 0001_initial && alembic upgrade head` "
            "(see docs/data/DATA_MODEL.md 'Which case are you in?'). Refusing to "
            "start against an unstamped schema."
        )
    if current_rev != head_rev:
        raise RuntimeError(
            f"Database is at Alembic revision {current_rev!r}, but the code expects "
            f"{head_rev!r}. Run `alembic upgrade head` (deploy/deploy.sh does this "
            "automatically before every restart) before starting the service."
        )
    log.info("db.schema_at_head", revision=current_rev)


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
