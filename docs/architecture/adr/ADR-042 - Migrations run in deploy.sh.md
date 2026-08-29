---
aliases:
  - ADR-042
tags:
  - adr
---
# ADR-042: `alembic upgrade head` runs in `deploy/deploy.sh`, not application startup; `create_all()`/the ALTER TABLE patcher are retired from production use
**Status:** active; closes [[ADR-035 - Alembic environment|ADR-035]]'s remaining follow-up. One claim below has since
become false — `create_all()` is reachable from production code again; see
**Reviewed 2026-08-29**.

**Decision:** `deploy/deploy.sh` runs `alembic upgrade head` against the
station as an explicit step — after syncing source and installing Python
dependencies, before installing/restarting the systemd unit. `api/app.py:
create_app()` and the three `cli.py` maintenance commands that touch the
database (`history reconcile-streams`, `detections reconcile-plausibility`,
`clips retention`) no longer call `db/session.py: create_all()`; they call a
new `db/session.py: ensure_schema_at_head()` instead. The old
`_patch_sqlite_columns` ALTER TABLE patcher is deleted outright, not just
unused. `create_all()` itself is kept, but only as a test helper (see
below) — it is no longer reachable from any production code path.

**Reason:** [[ADR-035 - Alembic environment|ADR-035]] built a real Alembic migration environment but left it
decorative — nothing called `alembic upgrade head`, so `create_all()` and the
ALTER TABLE patcher in `db/session.py` remained the schema's actual authors
in practice. That is exactly the situation that let `media_asset.reclaimed_at`
ship on the live station with no index ([[ADR-035 - Alembic environment|ADR-035]]'s own motivating gap): two
things describing the schema, free to disagree, with nothing checking that
they didn't. Wiring Alembic into the path that actually runs, and then
deleting the alternate path, is what closes that off for good rather than
adding a third description on top of the first two.

### Startup or deploy — the actual decision, and why

Two places could plausibly run `alembic upgrade head`: application/CLI
startup, or the deploy script. **This ADR puts it in `deploy/deploy.sh`, not
startup**, for a reason specific to this project rather than a general
preference:

- `CLAUDE.md` and the charter are explicit that **audio capture correctness
  outranks UI progress** and capture must have exactly one owning process.
  `oo serve` (`api/app.py: create_app()`) is the process that starts both the
  API and, through `Station`, the capture pipeline — there is no separate
  "control plane" process to isolate a slow migration from. A `CREATE TABLE`
  or batch-mode index rebuild embedded in that same startup path is squarely
  on capture's critical path: a slow migration (SQLite batch mode rebuilds
  the whole table on affected schema changes — [[DATA_MODEL]]'s "SQLite vs.
  PostgreSQL" section already flags this as measurably slower on a large
  table) or a failing one would delay or crash the very process that is
  supposed to start listening to the microphone.
- Deploying is already a distinct, operator-initiated step
  (`deploy/deploy.sh`) that syncs code, installs dependencies, and only then
  restarts the service. Running the migration there means a slow or failing
  migration is caught **before** the working service is touched at all:
  `set -euo pipefail` makes a failing `alembic upgrade head` abort the script
  before the systemd unit is reinstalled or restarted, so the previous,
  working version keeps running capture uninterrupted. A migration failure
  becomes a failed deploy an operator sees immediately, not a crash-looping
  service discovered later.
- This is also more predictable in the sense the task asks for: the DSN,
  the code version, and the database are all in a known, stationary state
  during a deploy (nothing else is racing to use the database), which is not
  true of "whatever moment the service happens to start" — a systemd restart
  after a crash, a reboot, or a manual `systemctl restart` for an unrelated
  reason would otherwise re-run a migration check against a running system
  for no reason connected to a code change.

The trade-off accepted: a developer or operator who starts the service
directly (`oo serve`) without having gone through `deploy/deploy.sh` first
does not get migrations run for them against an existing, out-of-date
database — they get a clear, actionable refusal instead (see
`ensure_schema_at_head()` below). That is judged better than the
alternative of quietly running unreviewed DDL on a process whose job is to
start capturing audio.

### What `ensure_schema_at_head()` actually does

Replacing `create_all()` with nothing was not viable: every test that spins
up `create_app()` or invokes a CLI command against a fresh `tmp_path` SQLite
file needs *some* schema-bootstrap to happen, and a fresh developer checkout
or a brand-new station needs the same thing on its very first run, before
any deploy has ever touched it. `ensure_schema_at_head()`
(`src/open_observatory/db/session.py`) draws the same distinction
[[DATA_MODEL]]'s "Which case are you in?" already documented for
a human operator, and automates only the safe half of it:

- **A completely empty database** (no tables at all) is bootstrapped by
  running `alembic upgrade head` directly. Starting from nothing this *is*
  revision `0001_initial`'s `CREATE TABLE` — fast, and by construction
  identical to what `create_all()` used to build, which is exactly what
  `tests/test_migrations.py::test_upgrade_head_from_empty_matches_create_all_tables`
  asserts. This is a read (`PRAGMA`/catalog query to see there are zero
  tables) followed by DDL only in the case where there is nothing to lose —
  fine on both a test's throwaway file and a station's very first boot.
- **A database that already has tables** is never touched by DDL here. Its
  Alembic revision is read (a single fast query against `alembic_version`)
  and compared to the code's own migration head:
  - no `alembic_version` row at all → a pre-Alembic database that was never
    adopted (`create_all()`-built, or a database that predates [[ADR-035 - Alembic environment|ADR-035]]
    entirely) — raises, naming the exact adoption sequence
    (`alembic stamp 0001_initial && alembic upgrade head`).
  - `alembic_version` present but not equal to `head` → a migration is owed
    — raises, naming `alembic upgrade head` and noting `deploy/deploy.sh`
    normally does this automatically.
  - `alembic_version` present and equal to `head` → proceeds; this is the
    live station's case on every normal startup after a normal deploy, and
    it is a single read, not a migration run.

### Verification

- **Migrations vs. models agree at `head`, the most valuable check in this
  work:** `tests/test_migrations.py::test_initial_revision_matches_create_all`
  already existed from [[ADR-035 - Alembic environment|ADR-035]] and still passes with all four revisions
  applied — stamping a `create_all()`-built database at `head` and running
  `alembic check` reports no drift. This is re-run on every test invocation,
  not a one-time claim: models and migrations are not free to diverge again
  without a test failing first.
- **`ensure_schema_at_head()` itself** is covered by four new cases in
  `tests/test_migrations.py`: bootstrapping a genuinely empty database;
  idempotency at `head` against a database seeded with 2,000 detection rows
  (run twice, row count and revision unchanged both times); refusing an
  unstamped `create_all()`-built database without touching its data;
  refusing a database stamped at an old revision (`0001_initial` while
  `head` is `0004`).
- **Idempotency and safety against the live station's actual data:**
  verified against a fresh backup of the live station's real database
  (`sqlite3`-equivalent hot backup via Python's `sqlite3.Connection.backup`,
  taken over SSH, never opened for writing on the station itself — the
  original file was never touched). The copy holds 65,515 `detection` rows
  and 28,183 `media_asset` rows. `alembic current` on the copy already
  reports `0004_drop_dead_detection_indexes (head)` — the live station is
  current. Running `alembic upgrade head` against the copy is a true no-op
  (no DDL emitted, confirmed by `alembic check` reporting no drift
  afterward), and running it a second time changes nothing further; row
  counts for both tables are unchanged throughout. This was run against the
  copy only — the live database was never opened for writing during this
  work.
- Full test suite, `ruff check .`, and `mypy src` all pass (the two `mypy`
  findings that remain — `tests/test_migrations.py`'s pre-existing set-
  comprehension typing note and `cli.py:211`'s unrelated `CaptureBlock |
  None` narrowing — both predate this change and are untouched by it;
  [[SETUP]]'s "NOT clean" trap already documents that
  `mypy src` has pre-existing findings).

### What changes for an operator or a developer

- **Deploying the station:** unchanged in practice — `./deploy/deploy.sh`
  now migrates automatically as part of the same command. Nothing new to
  run by hand for a normal deploy.
- **A fresh developer checkout:** unchanged — `oo serve` (or any `oo`
  command) against a database that does not exist yet still works with no
  extra step, because `ensure_schema_at_head()` bootstraps it.
- **A developer with an old, pre-Alembic SQLite file** (built before this
  work, or before [[ADR-035 - Alembic environment|ADR-035]]): the next `oo serve` now refuses to start,
  with the exact adoption command in the error message, instead of silently
  patching columns in with `ALTER TABLE`.
- **Adding a new column or table:** unchanged from [[ADR-035 - Alembic environment|ADR-035]]/[[DATA_MODEL]]
  — write an Alembic revision, not a model change relying on the patcher,
  which no longer exists to fall back on.

### Rollback

**If the migration step in `deploy/deploy.sh` fails:** `set -euo pipefail`
stops the script before the systemd unit is reinstalled or restarted, so the
previous, working version of the service keeps running under the old
schema — there is no window where a running process is pointed at a schema
its own code does not expect. Read the `alembic` error, restore
`data/openobservatory.sqlite` from a pre-deploy backup if the database was
left mid-migration (there is still no automated backup tool — take one
manually before a deploy you are unsure about), fix the migration or the
data it does not like, and re-run `deploy/deploy.sh`. Full detail and the
one window that *is* possible on a successful deploy (old process, new
schema, for the few seconds between the migration step and the restart —
tolerated because every migration here is additive) is in
[[DATA_MODEL]] under "Rollback".

**To revert this change entirely:** `git revert` the commit. `create_all()`
and `ensure_schema_at_head()` both remain in `db/session.py` regardless (the
former as a test helper, the latter as the reverted call sites' replacement
disappearing), so reverting is a plain code rollback with no data migration
of its own — the schema itself does not change, only what checks it on the
way in.

### Smoke test

```bash
# Confirms deploy.sh's new step runs and the station is already at head
# (idempotent no-op expected — this is not a migration, just a check).
ssh <user>@<station-host> "cd open-observatory && .venv/bin/python -m alembic current"
# -> 0004_drop_dead_detection_indexes (head)

./deploy/deploy.sh --no-web --no-deps
# -> "==> running database migrations" step prints, exits 0, and the rest of
#    the deploy proceeds; the station's /api/v1/health check at the end
#    confirms the service came back up.
```

**Reviewed 2026-08-29:** the decision holds everywhere it is wired. `deploy/deploy.sh:92`
runs `alembic upgrade head` before the systemd unit is reinstalled; `api/app.py:326` and
seven `cli.py` maintenance commands call `ensure_schema_at_head()`
(`src/open_observatory/db/session.py:110`) — the three named above plus `detections
reconcile-taxonomy`, `detections keep`, `clips purge-human-audio` and `clips
reconcile-missing`, all added later and all wired the same way; and
`_patch_sqlite_columns` is gone from the tree, not merely unused.

The sentence above that `create_all()` "is no longer reachable from any production code
path" is nonetheless **false today**, and stopped being true within the hour it was
written. `oo refine run` (`cli.py:1661`) and `oo refine status` (`cli.py:1744`) both call
`create_all()`. Both arrived with [[ADR-045 - Refinement runner|ADR-045]] in commit `2f8d78e` at 12:03 on 2026-08-09,
sixteen minutes after this ADR's own commit `ec286ab` at 11:47, which had removed the last
`create_all()` call from `cli.py` (`git show ec286ab:src/open_observatory/cli.py` matches
it nowhere). `oo refine run` is the `ExecStart` of `deploy/open-observatory-refine.service`
and fires nightly from `open-observatory-refine.timer`, so this is a shipped station path,
not a developer convenience.

What it costs: those two commands get none of the refusal the other eight call sites get.
`create_all()` is `Base.metadata.create_all(engine)` — it creates tables that do not exist
and never alters one that does — so against a database owed a migration it would add
whatever tables are missing and leave `alembic_version` untouched, reconstructing exactly
the unstamped, two-descriptions-of-the-schema state this ADR set out to close. Against a
database already at `head` it is a no-op, which is why nothing has gone wrong yet.
Replacing both calls with `ensure_schema_at_head()` is the fix, and it belongs in the code
rather than in this file.

Also moved on: `head` is now `0011_retention_live_asset_indexes`
(`alembic/versions/20260819_0010_000000000011_retention_live_asset_indexes.py:69`), so the
smoke test above expects a revision six migrations old, and the Verification section's
"all four revisions" counted the four that existed on 2026-08-09. Both are accurate records
of that day; only the smoke test's expected output has to be read with the current head
substituted.

---
Part of the [[ADRS|Architecture Decision Record index]].
