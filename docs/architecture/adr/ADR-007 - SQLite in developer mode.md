---
aliases:
  - ADR-007
tags:
  - adr
---
# ADR-007: SQLAlchemy-mediated storage with SQLite in developer mode
**Status:** active; the "no Alembic environment" position is superseded by [[ADR-035 - Alembic environment|ADR-035]], and
the "`create_all()` at startup" position by [[ADR-042 - Migrations run in deploy.sh|ADR-042]]. Three claims below have since
been overtaken by shipped work — see **Reviewed 2026-08-29**.

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

**Status of Alembic (2026-08-08, superseded by [[ADR-035 - Alembic environment|ADR-035]] and then by [[ADR-042 - Migrations run in deploy.sh|ADR-042]]):** a real `alembic/` migration
environment now exists (`alembic/env.py`, `alembic/versions/`), wired to `Settings` and
`Base.metadata`. `create_all()` in `db/session.py` still runs at every application/CLI
startup and still builds a correct fresh schema, but new columns should go through an
Alembic revision from here on, not the `create_all()` + `ALTER TABLE`-patcher path. See
[[ADR-035 - Alembic environment|ADR-035]] for the initial baseline, the stamp path for existing databases (the live
station's `openobservatory.sqlite` included), and what still has to change (`api/app.py`
and `cli.py` startup calling Alembic instead of `create_all()`) before the patcher can be
retired.

**Rollback:** setting `OO_DATABASE_DSN` to a PostgreSQL URL is the intended one-line
switch. The migration environment ([[ADR-035 - Alembic environment|ADR-035]]) is a prerequisite for exercising that switch
honestly and now exists; it has not yet been run against a real PostgreSQL 16 instance in
this repository, so "the DSN swap is configuration-only" remains unverified beyond SQLite
until that happens.

**Reviewed 2026-08-29:** the decision holds — SQLite is still the only database this
project has ever run on. `Settings.resolved_database_dsn` still falls back to
`sqlite+pysqlite:///{data_dir}/openobservatory.sqlite` (`src/open_observatory/config.py:721`),
`OO_DATABASE_DSN` is still the one-line override, and the dialect-portability discipline is
still being kept: `history.py:391` still writes bucket truncation as `epoch - (epoch % seconds)`
with the comment naming exactly the reason given above. `psycopg` remains an optional extra
(`pyproject.toml:57`) that nothing in the repository installs or exercises; there are no
workflows and no PostgreSQL tests, so the Rollback paragraph's "unverified beyond SQLite"
still stands as written. Three things in the text above are now out of date:

- **The Alembic paragraph is stale.** [[ADR-042 - Migrations run in deploy.sh|ADR-042]] landed the follow-up it describes as
  outstanding. `create_all()` no longer runs at application startup: `api/app.py:326` and
  the `cli.py` maintenance commands call `ensure_schema_at_head()`
  (`src/open_observatory/db/session.py:110`), which runs `alembic upgrade head` on an empty
  database and otherwise *refuses to start* against a schema that is unstamped or behind
  `head`. `deploy/deploy.sh:92` runs the migration as an explicit pre-restart step. The
  `_patch_sqlite_columns` ALTER TABLE patcher is deleted, not merely unused, so the
  "before the patcher can be retired" clause has no referent any more.
- **The retention/review gate never happened.** The Constraint's "Retention and review
  workflows are gated on the PostgreSQL profile" is not what shipped. Retention is on by
  default (`retention_enabled: bool = True`, `config.py:379`) and has been paced and
  re-ordered repeatedly against the live station ([[ADR-033 - Retention is paced|ADR-033]], [[ADR-062 - Retention walks live assets|ADR-062]],
  [[ADR-064 - Watermark tier first|ADR-064]]); the review workflow ships as
  `POST/GET /api/v1/detections/{id}/review` ([[ADR-043 - Taxon correction|ADR-043]], [[ADR-044 - Withdrawn detections|ADR-044]]).
  Both run on the station's SQLite file today. Read that sentence as a superseded intention.
- **There are two writing processes, not one.** The Constraint's "no concurrent writers on
  the SQLite profile" was overtaken by [[ADR-045 - Refinement runner|ADR-045]], which gave the refinement runner its own
  `systemd` unit writing `refinement` rows to the same file while the station writes
  detections. What makes that safe is not the constraint but WAL plus a busy timeout
  (`db/session.py:35-43`) and the runner's append-only, propose-only authority. The
  constraint's real content survives as a narrower one: no feature may depend on
  `LISTEN/NOTIFY` or JSON indexing, and no *unbounded* write concurrency.

Two entry points are the exception to the first bullet: `oo refine run` and `oo refine
status` (`cli.py:1661`, `cli.py:1744`) still call `create_all()` rather than
`ensure_schema_at_head()`, so they skip the head check the other eight call sites perform.
That defect belongs to [[ADR-042 - Migrations run in deploy.sh|ADR-042]], which records it in full; it is not fixed here.

---
Part of the [[ADRS|Architecture Decision Record index]].
