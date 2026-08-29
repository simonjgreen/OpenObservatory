---
aliases:
  - ADR-007
tags:
  - adr
---
# ADR-007: SQLAlchemy-mediated storage with SQLite in developer mode
**Decision:** Persist through SQLAlchemy 2 + Alembic against a DSN from configuration.
Developer/debug mode on the Pi defaults to SQLite at `data/openobservatory.sqlite`.
PostgreSQL 16 remains the production DSN and the only supported target for multi-process
deployment.

**Reason:** The target has no Docker and no PostgreSQL. Requiring a database server to
observe an audio pipeline would break the "repository runnable at every milestone" rule.
No raw SQL and no dialect-specific types are used, so the DSN swap is configuration-only.

**Constraint:** Any feature that requires concurrent writers, `LISTEN/NOTIFY`, or JSON
indexing must not be built on the SQLite profile. Retention and review workflows are
gated on the PostgreSQL profile. Query code must also stay dialect-portable: the history
aggregation layer (`history.py`) is the working example, and it is why bucket truncation
is written as `x - (x % n)` rather than with `FLOOR` or integer division — `/` on an
Integer column in SQLAlchemy 2 is *true* division and casts to NUMERIC.

**Status of Alembic (2026-08-08, superseded by [[ADR-035]]):** a real `alembic/` migration
environment now exists (`alembic/env.py`, `alembic/versions/`), wired to `Settings` and
`Base.metadata`. `create_all()` in `db/session.py` still runs at every application/CLI
startup and still builds a correct fresh schema, but new columns should go through an
Alembic revision from here on, not the `create_all()` + `ALTER TABLE`-patcher path. See
[[ADR-035]] for the initial baseline, the stamp path for existing databases (the live
station's `openobservatory.sqlite` included), and what still has to change (`api/app.py`
and `cli.py` startup calling Alembic instead of `create_all()`) before the patcher can be
retired.

**Rollback:** setting `OO_DATABASE_DSN` to a PostgreSQL URL is the intended one-line
switch. The migration environment ([[ADR-035]]) is a prerequisite for exercising that switch
honestly and now exists; it has not yet been run against a real PostgreSQL 16 instance in
this repository, so "the DSN swap is configuration-only" remains unverified beyond SQLite
until that happens.

---
Part of the [[ADRS|Architecture Decision Record index]].
