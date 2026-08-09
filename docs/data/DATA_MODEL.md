# Data Model

This document is a seed specification, written before implementation began.
The system has since been partly built, and this revision marks each table as
either **Implemented** (a real SQLAlchemy model exists in
`src/open_observatory/db/models.py`) or **Planned** (design intent only, no
table exists). Unimplemented tables are kept, not deleted, per the project's
operating brief.

## Core entities

### station — Implemented, with a corrected privacy claim

The seed spec originally said latitude/longitude were "encrypted or
access-controlled" and documented an "external location precision" field.
Neither is true of the current model: `latitude` and `longitude` are plain
nullable `Float` columns, stored in the clear, with no separate
lower-precision field for external disclosure and no access control on them.

**Open gap:** this overstates a privacy protection that does not exist. It is
relevant because ADR-015 records that there is currently no authentication at
all, so anyone who can reach the API can read exact station coordinates via
`GET /api/v1/station`.

Columns as implemented:

- id UUID
- name
- timezone IANA string
- latitude nullable float, plain
- longitude nullable float, plain
- created_at
- software_version

### audio_device — Implemented

- id
- station_id
- stable_device_key
- USB identifiers (vendor/product/serial)
- ALSA identifiers
- negotiated format/rate/channels
- first_seen/last_seen
- configuration JSON

### audio_stream — Implemented

- id UUID
- audio_device_id
- start/end UTC
- start/end monotonic ns
- sample rate/format/channels
- end reason
- frame count
- discontinuity count
- last_frame_at_utc — nullable; a heartbeat written every ~10 s while the
  stream is open, added by ADR-024. NULL for rows written before it existed
  and for streams too short-lived to see a housekeeping tick. `frame_count`
  and `discontinuity_count` are also written at that same cadence now, not
  only at close, so a crashed process's row carries real numbers rather than
  the zeros a freshly-created row starts with.
- detail — free-form JSON; also now carries an `orphan_recovery` or
  `reconciliation` sub-object when `Station._close_orphaned_streams` or
  `oo history reconcile-streams` has corrected the row's `end_utc`, recording
  the original claim and the method used (ADR-024).

**Correctness note (ADR-024):** `start_utc`/`end_utc` is a *claim*, not
ground truth — a stream row was found in the live database claiming a 32 hour
span while its own `frame_count` implied 2.79 hours of actual audio, with the
gap explained by a capture-side hang rather than a crash. `history.coverage()`
now bounds each row's contribution by `frame_count`/`sample_rate` and by
`last_frame_at_utc`, whichever is tighter, and flags a row `suspect` when the
two disagree by more than 10%. Do not read `end_utc - start_utc` anywhere as
"how long this stream captured for" without going through that reconciliation.

### capture_gap — Implemented

- id
- stream_id
- start/end monotonic and UTC
- estimated missing frames
- reason
- detail JSON

### detector — Implemented

- id
- plugin ID/version
- model ID/version/hash
- taxonomy version
- licence metadata JSON
- active configuration JSON
- installed_at

### analysis_window — Planned, not implemented

No `analysis_window` table exists. Retained as design intent:

- id UUID
- stream_id
- stream kind
- source start/end frame
- UTC start/end
- sample rate
- encoding
- transient asset URI
- checksum
- lease expiry

### detector_run — Deliberately not persisted (not an omission)

No `detector_run` table exists, and this is a considered design decision, not
a gap. The module docstring in `db/models.py` records the reasoning: the
activity detector alone produces roughly two runs a second — about 172,000
rows a day — which would dominate the database for no benefit the Prometheus
counters do not already provide. Runs are counted and exposed as metrics
only; their *results* (i.e. `detection` rows) are what gets persisted.

Fields below are retained as a record of the shape a persisted run would have
had, in case the decision is revisited:

- id UUID
- detector_id
- window_id
- queued/started/completed timestamps
- status
- error category/message
- runtime ms
- worker metadata JSON

### detection — Implemented, corrected column list

The seed spec's column list does not match the implemented model: there is no
`detector_run_id` (consistent with `detector_run` not being persisted above),
and several implemented columns were missing from the original list.

Columns as implemented:

- id UUID
- station_id
- detector_id
- stream_id
- window_id
- event_start_utc / event_end_utc
- source_start_frame / source_end_frame — frame bounds in the *native*
  stream, so evidence is exactly reproducible
- detector_label nullable
- common_name / scientific_name nullable
- canonical_taxon_id nullable
- rank nullable
- taxonomic_group (indexed, defaults to `unknown`)
- score
- calibrated_probability nullable
- peak_frequency_hz nullable
- native_result JSON
- created_at

**`native_result` since 2026-08-09 (ADR-037 option C)**: `normaliser.py`
drops a small, fixed set of keys before persisting a *new* `native_result`,
each only when its value (not merely its name) is proven to duplicate
something already persisted elsewhere on the same row —
`_strip_redundant_native_result` in `normaliser.py` is the exact logic.
**Rows written before 2026-08-09 are untouched** and still carry every key
the detector originally emitted; there is no migration for old data and none
is planned.

| Dropped key | Recover it from |
|---|---|
| `detector` | `detector.plugin_id`, via `detection.detector_id` |
| `model_id` | `detector.model_id`, via `detection.detector_id` |
| `label` | `detection.detector_label` (exact) |
| `confidence` | `detection.score` (exact — the dropped copy was itself a rounded display value) |
| `peak_frequency_hz` | `detection.peak_frequency_hz` (exact — the dropped copy had *less* precision than the typed column) |

`occurrence_probability` and `plausibility_band` (ADR-032's plausibility
audit trail) are never dropped — they are not duplicates of anything, they
are evidence. Likewise kept, deliberately, are `band_hz`, `score_definition`
and `confidence_definition`: these look like per-detector-version constants
but are not — the live database shows `activity-v1`'s `score_definition`
changed (a config value, `activity_band_hz`/`ultrasonic_band_hz`-adjacent,
changed without a `plugin_version`/`model_version` bump) under the *same*
`detector_id`, so "the detector version recorded on the row" cannot reliably
reconstruct them. See ADR-037's "B and C: what was implemented" section for
the measured before/after byte counts and the full reasoning.

### detection_cluster / detection_cluster_member — Planned, not implemented

Neither table exists. Retained as design intent:

`detection_cluster`:

- id UUID
- canonical taxon ID nullable
- event start/end
- status (`candidate`, `corroborated`, `conflicted`)
- derived summary JSON

`detection_cluster_member`:

- cluster_id
- detection_id
- temporal overlap
- taxonomy mapping confidence

### media_asset — Implemented, with a corrected `kind` description

The seed spec documented `kind` as a closed enum of
(`evidence_native`, `playback`, `spectrogram`, `export`). In the implemented
model, `kind` is a plain indexed `String(32)` column with no enum constraint
at the database or ORM level — any string is accepted. The set of values
actually in use has also grown: `audible_ultrasonic` (see ADR-014 and
`DEBUG_UI_TRANSPORT.md`) is a real, produced-by-default value that was missing
from the original documented set.

Columns as implemented:

- id UUID
- kind (string, unconstrained; observed values include `evidence_native`,
  `playback`, `spectrogram`, `export`, `audible_ultrasonic`)
- storage URI
- MIME type
- stream_id nullable
- source start/end frames nullable
- sample rate nullable
- byte length
- SHA-256
- created/expires timestamps
- reclaimed_at nullable — set by the retention sweeper (ADR-026) when it
  deletes the underlying file; the row itself is never deleted
- reclaim_reason nullable — which tier reclaimed it (`native`,
  `exemplar_only`, `expired`, `watermark`)
- detail JSON

### detection_media — Implemented

- detection_id
- media_asset_id
- role

### review — Table exists; nothing writes to it

The `review` table is implemented in `db/models.py` exactly as specified
below, but no code path currently creates a row in it: there is no
`POST /detections/{id}/reviews` endpoint (see `API_AND_INTEGRATIONS.md`) and
nothing else in the codebase inserts into this table. It is schema-only at
present.

- id
- detection_id
- actor
- status (`confirmed`, `rejected`, `uncertain`)
- corrected taxon ID nullable
- note
- created_at
- supersedes_review_id nullable

Reviews are append-only. Current status is derived from the latest valid
review.

### telemetry_series / telemetry_sample — Planned, not implemented

Neither table exists. Retained as design intent:

`telemetry_series`:

- id
- station_id
- source
- metric name
- unit
- metadata JSON

`telemetry_sample`:

- series_id
- timestamp UTC
- numeric/text value
- quality

May be moved to a TimescaleDB hypertable when volume warrants it, if built.

### health_event — Implemented

- id
- service/component
- severity
- event type
- start/end UTC
- detail JSON
- acknowledged_at

Note: the implemented model does not carry a separate `station_id` column or
an `acknowledged_by` field, only `acknowledged_at`.

### alert_rule / alert_event — Planned, not implemented

Neither table exists. Retained as design intent: rules are versioned JSON
with explicit species/model/time/repetition thresholds. Alert events retain
matched detection IDs and delivery results.

## Indexes

Corrected against `db/models.py`. Indexes on tables that do not exist
(`detector_run`, `telemetry_sample`) have been removed from this list and
folded into the "planned" notes above; real indexes present in the
implemented schema have been added.

Implemented:

- `audio_device.stable_device_key` — unique
- `audio_stream.start_utc`
- `capture_gap.stream_id`
- `capture_gap (stream_id, start_monotonic_ns)`
- `detector.plugin_id`
- `detector (plugin_id, plugin_version, model_version)` — unique
- `detection.detector_id`
- `detection.stream_id`
- `detection.event_start_utc`
- `detection.taxonomic_group` — as part of the composite below, not on its own
- `detection (taxonomic_group, event_start_utc desc)`
- `media_asset.kind`
- `media_asset.created_at`
- `review.detection_id`
- `health_event.service`
- `health_event.severity`
- `health_event.start_utc`

**Dropped 2026-08-09 (ADR-037 option B, Alembic revision
`0004_drop_dead_detection_indexes`)**: `detection.station_id`,
`detection.canonical_taxon_id`, a standalone `detection.taxonomic_group`
index, and the composite `detection (station_id, event_start_utc desc)`. None
of them were used by any query in the codebase — `station_id` holds one
distinct value across the whole live database, `canonical_taxon_id` is only
ever read into Python (never filtered or ordered by), and the standalone
`taxonomic_group` index was a dead prefix of the composite above, which
survives. `detection.detector_id` was also proposed for removal by the
original research but was **kept**: re-verification found
`plausibility_repair.reconcile_plausibility` (ADR-032) joins from `detector`
(filtered by `plugin_id`) into `detection` and SQLite uses this exact index
to satisfy that join. Reversible with `alembic downgrade -1`.

Planned only (would apply if the corresponding table is built):

- `telemetry_sample (series_id, timestamp desc)`
- GIN indexes on selected JSON fields only after measured need

## Retention

- analysis windows: minutes/hours, removed after all leases complete (planned
  — no `analysis_window` table exists yet, see above);
- rolling raw PCM: memory-only by default;
- evidence clips: **tiered aging, not a flat 30 days** (ADR-026,
  `src/open_observatory/retention.py`) — native (full-rate) clip and its
  audible rendering both survive 0–7 days; 7–30 days keeps the audible
  rendering only; 30–90 days keeps only the first-ever and best-of-species
  clip; 90+ days deletes everything. Independently of all of that, disk usage
  above an 85%-default watermark reclaims the oldest surviving clips first,
  regardless of tier. Every threshold is a `Settings` field;
- detections/reviews: **indefinite, unconditionally** — no tier or watermark
  in `retention.py` ever deletes a `detection` row or mutates its columns;
- detailed metrics: 30–90 days depending store;
- logs: 14 days default.

## Migrations (Alembic, ADR-035)

`alembic/` (`env.py`, `script.py.mako`, `versions/`) and `alembic.ini` at the
repository root are a real migration environment, wired to
`Settings.resolved_database_dsn` (the same `OO_DATABASE_DSN` resolution the
application uses) and to `Base.metadata` from `db/models.py`, so this
document and `db/session.py` no longer say "there is no Alembic
environment." `db/session.py: create_all()` still exists and is still what
every application/CLI entry point calls on startup — see "Which case are you
in?" below for why that is deliberate, not an oversight.

### Which case are you in?

- **Fresh database (nothing at `data/openobservatory.sqlite`, or a fresh
  PostgreSQL database with no `open_observatory` tables in it):** run
  `alembic upgrade head`. Nothing else is needed; `create_all()` at
  application startup is then a no-op (all tables already exist).
- **Existing database built by `create_all()` before Alembic existed** (the
  live station's `data/openobservatory.sqlite`, or any developer database
  that predates this environment): **do not run `alembic upgrade head`
  first** — the initial revision's `upgrade()` issues `CREATE TABLE` for
  every table and will fail (or, worse on some backends, silently diverge)
  against tables that already exist. Instead: `alembic stamp 0001_initial`,
  then `alembic upgrade head` to pick up anything that shipped after the
  baseline. This is the exact sequence verified against a local read-only
  copy of the live station's database (48,067 `detection` rows, 19,205
  `media_asset` rows, both counts unchanged afterward, `alembic check`
  clean).

If you are not sure which case you are in: `alembic current` prints nothing
for a database Alembic has never touched (fresh-or-unstamped); compare its
table list (`sqlite3 data/openobservatory.sqlite .tables`, or `\dt` on
PostgreSQL) against `db/models.py` — if the tables already exist, you are in
the stamp case.

### Creating a new migration

1. Change `db/models.py` as needed.
2. `alembic revision --autogenerate -m "describe the change"` against a
   database that reflects the *old* schema (e.g. a `create_all()`-built
   temporary SQLite file at the previous revision, or any database already
   at `head` before your model change) — autogenerate diffs the live
   database against the new metadata, so pointing it at a database that
   already has the new column produces an empty migration.
3. Read the generated file. Autogenerate does not detect: pure data
   backfills, column renames (it emits drop+add, which loses data — rewrite
   this by hand as `alter_column`/`execute` with a backfill), or check
   constraints. Give the revision a plain (not autogenerated) revision id
   matching the existing `NNNN_description` style once satisfied — see
   `0001_initial` / `0002_media_asset_reclaimed_at_index` for the pattern.
4. Verify: `alembic upgrade head` then `alembic downgrade -1` then
   `alembic upgrade head` again against a throwaway database. Add a case to
   `tests/test_migrations.py` if the change is non-trivial (a rename, a
   backfill, a new table with a foreign key into existing data).
5. Run `alembic check` against a `create_all()`-built database stamped at
   your new revision — it must report no drift. This is the same honesty
   check the baseline itself is held to.

### Applying a migration

- Developer SQLite: `alembic upgrade head` (or let `create_all()` build a
  fresh file, then `alembic stamp head` — equivalent for a database with no
  prior state; prefer `upgrade head` so you exercise the real migration
  path).
- Station SQLite: see "Which case are you in?" above. Back up
  `data/openobservatory.sqlite` (and its `-wal`/`-shm` siblings, if present)
  before running anything — there is still no automated backup tooling in
  this repository (`docs/operations/DEPLOYMENT_AND_OPERATIONS.md`).
- PostgreSQL 16: same commands, `OO_DATABASE_DSN` pointed at the PostgreSQL
  DSN. Not yet exercised against a real PostgreSQL instance in this
  repository — the environment is dialect-portable by construction (batch
  mode, no dialect-specific types in `models.py`) but that claim is
  unverified beyond SQLite until it runs there.
- One-off / scripting use without touching `OO_DATABASE_DSN`:
  `alembic -x url=<dsn> upgrade head` overrides the resolved DSN for a
  single invocation (`alembic/env.py: get_url()`).

### SQLite vs. PostgreSQL differences that matter for migrations

- SQLite cannot `ALTER`/`DROP` most columns or constraints in place;
  `alembic/env.py` sets `render_as_batch=True` unconditionally so every
  migration goes through Alembic's batch mode, which rebuilds the table
  under the hood on SQLite. On PostgreSQL, batch mode is a thin pass-through
  to the same `ALTER TABLE` Alembic would emit without it — one migration
  file behaves correctly on both, but a SQLite batch rebuild is measurably
  slower on a large table than the equivalent single-statement PostgreSQL
  `ALTER`. Prefer additive, nullable columns for anything performance
  sensitive on SQLite, same as before Alembic existed.
- Index/column existence checks: SQLite has no `CREATE INDEX IF NOT EXISTS`
  quirks worth noting; PostgreSQL 16 supports the same syntax, which is why
  revision `0002` uses it directly rather than branching on
  `op.get_bind().dialect.name`.
- `CHAR(32)` vs. a native UUID type: `models.py` uses SQLAlchemy's `Uuid`
  type uniformly, which renders as `CHAR(32)` on SQLite and PostgreSQL's
  native `UUID` on that dialect — no per-dialect migration code needed for
  the columns in this schema today.

### Rollback

`alembic downgrade base` round-trips cleanly on a throwaway database
(`tests/test_migrations.py::test_upgrade_downgrade_roundtrip`). There is
deliberately no supported `downgrade` path for a real, populated deployment
database — `0001_initial.downgrade()` drops every table, which is correct
for a scratch database and destructive for a real one. To roll back a bad
migration against a real database: stop the service, restore
`data/openobservatory.sqlite` from the backup taken before the migration ran
(there is no automated backup tool; this is an operator's own
responsibility per `DEPLOYMENT_AND_OPERATIONS.md`), check out the previous
commit, and restart. `alembic downgrade` is a tool for development, not a
substitute for that backup.
