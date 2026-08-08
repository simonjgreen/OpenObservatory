"""Alembic environment.

Wired to the project's own configuration and declarative metadata rather than
a hardcoded DSN, so the same migrations run against the SQLite developer
profile and the PostgreSQL production profile from ADR-007 without editing
this file.

URL resolution order:

1. ``-x url=<dsn>`` on the command line (used by tests to point at a
   throwaway database without touching the environment);
2. ``open_observatory.config.Settings().resolved_database_dsn`` -- the same
   ``OO_DATABASE_DSN`` / ``config/runtime.env`` resolution the application
   uses, so "what DSN does this migration run against" always has one answer.

Batch mode (``render_as_batch=True``) is always on. SQLite cannot ALTER or
DROP most columns in place; Alembic's batch mode works around that by
rebuilding the table. The same migrations run unbatched-equivalent on
PostgreSQL -- batch mode is a no-op wrapper there, not a SQLite-only branch --
so one migration file is honest on both dialects. See
``docs/data/DATA_MODEL.md`` for what differs in practice between the two.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from open_observatory.config import Settings
from open_observatory.db.models import Base

# Alembic Config object, providing access to values within alembic.ini.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate compares against this metadata.
target_metadata = Base.metadata


def get_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    if "url" in x_args:
        return x_args["url"]
    # A fresh Settings() (not the process-wide get_settings() singleton) so
    # this always reflects the current environment / config/runtime.env,
    # exactly like a fresh application process would resolve it.
    return Settings().resolved_database_dsn


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live database connection (``--sql``)."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
