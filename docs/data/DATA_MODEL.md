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

**Open gap, still open on 2026-08-09:** this overstates a privacy protection that
does not exist. There is no encryption, no access control on these two columns,
and no lower-precision field for external disclosure — which `PRD.md` §10 asks
for ("location precision exposed through APIs must be configurable; external
integrations should default to coarse location") and which the acceptance
criteria list.

Authentication now exists (ADR-034, closing ADR-015) but is **off by default**,
and `GET /api/v1/station` is not in the credential-free allow-list — so with auth
enabled, exact coordinates are gated; with auth off, which is the default and is
the live station's state, anyone who can reach the port can read them. Enabling
auth is therefore the mitigation available today; a coarse-location field is not
implemented.

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
- model_id / model_version / model_sha256
- taxonomy version
- licence_name, licence_url — two string columns, **not** a JSON blob
- claim — the detector's own statement of what it does and does not assert
- calibrated — surfaced in every detection payload
- configuration JSON (the active configuration; the only JSON column here)
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
- refined_at (indexed) / refinement_version / refinement_outcome — added by
  revision `0006_refinement` (ADR-045); described in full in the refinement
  section below

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
  `unkept`, `watermark`)
- detail JSON

### detection_media — Implemented

- detection_id
- media_asset_id
- role

### review — Implemented, and written to since 2026-08-08

The `review` table is implemented in `db/models.py` exactly as specified below.
**This entry previously said nothing wrote to it; that is no longer true.**
`POST /api/v1/detections/{id}/review` (ADR-029) inserts a row on every call —
append-only, setting `supersedes_review_id` to the prior row for that detection —
and `GET /api/v1/detections/{id}/review` returns the latest. The debug UI's
detection drawer has confirm/reject controls wired to it.

**The correction columns are now written (ADR-043, 2026-08-09).** This section
previously said `corrected_taxon_id` "is always written `None`" and that
correction was deferred to a future ADR. That is no longer true: revision
`0005_review_correction_names` added the denormalised name columns, and
`POST /api/v1/detections/{id}/review` writes all three when `status` is
`corrected`.

- id
- detection_id
- actor
- status — one of `confirmed`, `rejected`, `corrected`, `held`. (An earlier
  version of this list said `uncertain`; **no code path has ever written that
  value**.)
- corrected_taxon_id nullable
- corrected_common_name nullable — denormalised, revision `0005`
- corrected_scientific_name nullable — denormalised, revision `0005`
- note
- created_at
- supersedes_review_id nullable

Reviews are append-only. Current status is derived from the latest valid
review. **A correction never edits the detection**: the detection's own
`common_name` / `scientific_name` / `canonical_taxon_id` are untouched, so the
original claim stays visible and attributable, and the API derives
`effective_common_name` / `identification_source` at read time. A `held` review
also exempts the detection's evidence from retention's age tiers — though not
from the disk watermark.

On the live station on 2026-08-09 this table held **65 rows**, so the review
workflow is in real use, not merely built.

### refinement — Implemented (ADR-045), written by the refinement runner

Append-only, like `review`. Charter item 5 in table form: **only from new
information**, **the original claim preserved**, **a refined record
distinguishable from an original one**. Written only by `oo refine run`
(`src/open_observatory/refinement/store.py`), which runs in its own systemd unit,
never in the capture process.

- id
- detection_id
- refiner_id, refiner_version, model_id, model_version, model_sha256
- **evidence_fingerprint** — SHA-256 over refiner + model + weights + *the
  configuration the refiner ran under*
- basis (`new_model`, `corrected_prior`, `human_ear`)
- outcome (`proposed`, `no_change`, `confirmed`, `unavailable`, `failed`,
  `applied`)
- reason
- original_common_name, original_scientific_name, original_taxonomic_group,
  original_score — **the prior verdict, snapshotted verbatim at write time**
- proposed_common_name, proposed_scientific_name, proposed_rank,
  proposed_taxonomic_group, proposed_score
- applied (bool), resolved_at nullable, resolved_review_id nullable → `review.id`
- evidence (JSON, the refiner's own output verbatim)
- created_at

`ix_refinement_evidence` is **unique on (detection_id, evidence_fingerprint)**.
That is the charter's first rule as a constraint rather than a convention: the
same instrument, at the same version, under the same settings cannot bank a
second, more optimistic answer about the same event. Change the model, the
weights or the settings and the fingerprint changes and a new refinement is
admissible.

`outcome` distinguishes "examined and could not improve" (`no_change`,
`confirmed`) from "never actually seen" (`unavailable`, `failed`). Conflating
them would defeat the charter's retention safeguard, whose stated risk is "not
old data, it is data the refiner never actually saw".

No shipped refiner may set `applied`. See ADR-045 for the measured accuracy
evidence behind that ceiling.

### detection.refined_at / refinement_version / refinement_outcome

Three columns on `detection`, denormalised from the newest `refinement` row.
They exist because the charter's retention decision asks that "each event should
carry the fact that refinement ran, at what version, with what outcome, and
deletion should require it" — and the retention sweeper runs *inside the capture
process* on a paced 1.5 s budget (ADR-026, ADR-033), so that question has to be
answerable from an index (`ix_detection_refined_at`), not from a correlated
subquery against a second table.

NULL means no refiner has ever examined the event. **Retention does not yet
consult these columns** — see ADR-045's "What this does not do" for the exact
predicate that would close the gap, and why applying it is the operator's call.

On the live station on 2026-08-09 every row was still NULL: the runner has not
been run against it.

### BirdNET near-miss ledger — deliberately not persisted (not an omission)

ADR-052 records what BirdNET proposed and refused, with per-band score
histograms, and serves it at `GET /api/v1/detectors/near-misses`. **There is no
table for it and there must not be one.** It is a bounded in-memory ring
(`detectors/near_miss.py`, `birdnet_near_miss_ring`, default 200) that touches no
session and issues no commit, and it is empty after a restart by design: the
station rejects thousands of candidates an hour and persisting them would move
the detection table's growth problem (ADR-037) into a table nobody browses,
against a charter cross-cutting constraint on SD-card write amplification. If
you find yourself adding a migration for it, read ADR-052 first.

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

### user / auth_session / api_token — Implemented (ADR-034), added after this seed spec

Three tables the seed spec did not anticipate at all, added by the authentication
foundation on 2026-08-08 and migrated by revision `0003_auth_tables`. They are
only consulted when `auth_enabled` is true; with it false (the default) they exist
and are simply unused, which is what makes disabling auth a safe rollback.

`user` — a local operator account. Deliberately no roles, groups or org model:
this is authentication, not authorisation, for a single-operator LAN appliance.

- id UUID
- username — unique, indexed
- password_hash — always an Argon2id PHC string; the plaintext is never stored or logged
- must_change_password — set on the bootstrap account; login still succeeds so an
  operator can never be simply locked out
- disabled_at nullable
- created_at
- last_login_at nullable

`auth_session` — a browser session backing an `HttpOnly` cookie.

- id UUID
- user_id → `user.id`, indexed
- token_hash — SHA-256 of the opaque cookie token only, unique and indexed. Fast
  and unsalted on purpose: the token already carries ~256 bits of entropy, and
  this lookup runs on every authenticated request, where an Argon2id cost would
  be a real per-request tax
- created_at / expires_at (indexed) / revoked_at nullable
- user_agent — free text, never parsed, never trusted

`api_token` — a long-lived, revocable credential for a machine client.

- id UUID
- user_id → `user.id`, indexed
- name — operator-supplied, so "which client is this" has an answer
- token_prefix — first 8 characters, in the clear, indexed, so a token can be
  located without a full-table scan
- token_hash — SHA-256 of the whole token, unique; shown in full exactly once at
  creation and never again
- created_at / last_used_at nullable / revoked_at nullable

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
- `media_asset.reclaimed_at` — added by Alembic revision `0002`, because
  `ALTER TABLE ADD COLUMN` (how the column reached the live station) creates a
  column but never its index
- `detection.refined_at` — added by Alembic revision `0006` (ADR-045), created
  explicitly and unconditionally there for the same reason `media_asset.reclaimed_at`
  needed revision `0002`: `ALTER TABLE ADD COLUMN` cannot create an index
- `review.detection_id`
- `refinement.detection_id`
- `refinement.refiner_id`
- `refinement.outcome`
- `refinement.created_at`
- `refinement (detection_id, evidence_fingerprint)` — **unique**; this is charter
  item 5's "only from new information" rule as a database constraint (ADR-045)
- `health_event.service`
- `health_event.severity`
- `health_event.start_utc`
- `user.username` — unique
- `auth_session.user_id`
- `auth_session.token_hash` — unique
- `auth_session.expires_at`
- `api_token.user_id`
- `api_token.token_prefix`
- `api_token.token_hash` — unique

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
- detections/reviews/refinements: **indefinite, unconditionally** — no tier or
  watermark in `retention.py` ever deletes a `detection`, `review` or
  `refinement` row, or mutates a detection's claim columns;
- **an open gap, recorded rather than closed (ADR-045):** the clip tiers above
  delete on *age alone*. Nothing reads `detection.refined_at`, so a clip can be
  reclaimed at 7, 30 or 90 days having never been examined by a refiner once —
  which is exactly the failure `docs/CHARTER.md`'s retention decision names
  ("delete on 'refinement has run', not on age alone"). The schema supports the
  fix and `oo refine status` reports how many events have never been examined;
  applying it is a live deletion-policy change and is the operator's call;
- detailed metrics: 30–90 days depending store;
- logs: 14 days default.

## Migrations (Alembic, ADR-035, wired up by ADR-042)

**Revisions as of 2026-08-09**, in `alembic/versions/`:

| Revision | What it does |
|---|---|
| `0001_initial` | the baseline, autogenerated from an empty database against `Base.metadata` and verified by stamping a `create_all()`-built database and running `alembic check` |
| `0002_media_asset_reclaimed_at_index` | a real fix, not a demonstration: `ALTER TABLE ADD COLUMN` creates a column but never an index, so the live station's `media_asset.reclaimed_at` had none despite the model declaring one. Written `IF NOT EXISTS`, so it is a no-op on a database that reached the baseline by a normal upgrade |
| `0003_auth_tables` | `user`, `auth_session`, `api_token` (ADR-034) |
| `0004_drop_dead_detection_indexes` | drops four unused `detection` indexes found dead by re-verification against the live database (ADR-037 option B) |
| `0005_review_correction_names` | `review.corrected_common_name` / `corrected_scientific_name`, so a human correction carries the name it asserts (ADR-043) |
| `0006_refinement` | the `refinement` table plus `detection.refined_at` / `refinement_version` / `refinement_outcome` and `ix_detection_refined_at` (ADR-045) |

**The live station is at `0006_refinement`, which is `head`** — read directly
from its `alembic_version` table, read-only, on 2026-08-09 at 21:46Z. ADR-042's
deploy step has therefore now carried a real station across three revisions
(`0004` → `0005` → `0006`) unattended, which is the thing that was untested when
that ADR was written.

Two earlier snapshots of this same paragraph are kept because they show the
mechanism working rather than being asserted: at `0004` the station held 65,515
`detection` and 28,183 `media_asset` rows, and `alembic upgrade head` was
confirmed idempotent against a read-only copy (a second run does nothing,
`alembic check` reports no drift, row counts unchanged). At `0006` it held
**74,969 `detection`, 35,285 `media_asset`, 65 `review` and 0 `refinement`
rows.** These are snapshots of one reading each, not standing facts — the
detection count grows continuously.

`alembic/` (`env.py`, `script.py.mako`, `versions/`) and `alembic.ini` at the
repository root are a real migration environment, wired to
`Settings.resolved_database_dsn` (the same `OO_DATABASE_DSN` resolution the
application uses) and to `Base.metadata` from `db/models.py`.

**As of ADR-042, Alembic is the only thing that creates or changes schema.**
`db/session.py: create_all()` still exists, but only for tests that want a
non-Alembic way to build a comparison schema (`tests/test_migrations.py`) or
a fast disposable one; it is no longer called by `api/app.py` or `cli.py` on
startup. The old `_patch_sqlite_columns` ALTER TABLE patcher is deleted
outright. Application and CLI startup now call
`db/session.py: ensure_schema_at_head()`, and `deploy/deploy.sh` runs
`alembic upgrade head` as an explicit step before every restart — see
ADR-042 for why the migration itself runs in the deploy script and not in
application startup.

### Which case are you in?

- **Fresh database (nothing at `data/openobservatory.sqlite`, or a fresh
  PostgreSQL database with no `open_observatory` tables in it):** nothing to
  do by hand. `ensure_schema_at_head()` detects a database with no tables at
  all and runs `alembic upgrade head` itself — this is exactly revision
  `0001_initial`'s `CREATE TABLE`, so it is fast and always correct. Running
  `alembic upgrade head` yourself first also works and is equivalent.
- **Existing database built by `create_all()` before Alembic existed** (a
  developer database that predates this environment; the live station was in
  this position until it was adopted while ADR-035 was written): **do not
  run `alembic upgrade head` first** — the initial revision's `upgrade()`
  issues `CREATE TABLE` for every table and will fail (or, worse on some
  backends, silently diverge) against tables that already exist. Instead:
  `alembic stamp 0001_initial`, then `alembic upgrade head` to pick up
  anything that shipped after the baseline. `ensure_schema_at_head()`
  deliberately does **not** attempt this automatically — it has no reliable
  way to tell "unstamped because pre-Alembic" apart from "unstamped because
  something is badly wrong," so it raises with this exact instruction rather
  than guessing.
- **Existing database already under Alembic but behind `head`:** run
  `alembic upgrade head` (`deploy/deploy.sh` does this for the station on
  every deploy). `ensure_schema_at_head()` raises rather than starting the
  service against a schema older than the code expects.

If you are not sure which case you are in: `alembic current` prints nothing
for a database Alembic has never touched (fresh-or-unstamped); compare its
table list (`sqlite3 data/openobservatory.sqlite .tables`, or `\dt` on
PostgreSQL) against `db/models.py` — if the tables already exist, you are in
the stamp case. Starting the service (`oo serve`, or any of the `oo
history`/`oo detections`/`oo clips` maintenance commands) also tells you
immediately: `ensure_schema_at_head()` raises a specific, actionable
`RuntimeError` for each of these cases rather than a generic SQLAlchemy
"no such table" or "no such column".

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
   `0001_initial` / `0002_media_asset_reclaimed_at_index` /
   `0003_auth_tables` for the pattern.
4. Verify: `alembic upgrade head` then `alembic downgrade -1` then
   `alembic upgrade head` again against a throwaway database. Add a case to
   `tests/test_migrations.py` if the change is non-trivial (a rename, a
   backfill, a new table with a foreign key into existing data).
5. Run `alembic check` against a `create_all()`-built database stamped at
   your new revision — it must report no drift. This is the same honesty
   check the baseline itself is held to.

### Applying a migration

- Developer SQLite: `alembic upgrade head` (or let `ensure_schema_at_head()`
  do it for you the next time you run `oo serve` or any `oo` command against
  a database with no tables at all — equivalent for a database with no prior
  state; prefer `alembic upgrade head` by hand if you specifically want to
  exercise the real migration path outside the application).
- Station SQLite: `deploy/deploy.sh` runs `alembic upgrade head` itself, as
  an explicit step before the systemd unit is restarted (ADR-042) — a normal
  deploy needs no manual migration step. See "Which case are you in?" above
  for the one-time adoption sequence a pre-Alembic database needs first. Back
  up `data/openobservatory.sqlite` (and its `-wal`/`-shm` siblings, if
  present) before running anything by hand — there is still no automated
  backup tooling in this repository
  (`docs/operations/DEPLOYMENT_AND_OPERATIONS.md`).
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

**If `deploy/deploy.sh`'s migration step itself fails (ADR-042):** the script
runs `alembic upgrade head` before touching the systemd unit and uses `set
-euo pipefail`, so a failing migration stops the deploy there — the
previous, working version keeps running under the old schema, nothing is
restarted, and no half-migrated code is installed. The operator sequence is:
read the `alembic` error (usually a batch-mode rebuild failing partway
through on SQLite, or a constraint violation the migration didn't expect on
real data); if the database was left mid-migration, restore
`data/openobservatory.sqlite` from a pre-migration backup (back one up
manually before a deploy you are unsure about — there is still no automated
tool); fix the migration or the data it doesn't like; re-run
`deploy/deploy.sh`. The still-running old process is never restarted until
after the migration step succeeds, so a failed migration never leaves a
running service pointed at a schema its own code doesn't recognise. There is
a brief window, only on a successful deploy, between the migration
completing and the restart happening, where the database is at the new
schema but the old process is still serving requests against it — the
migrations in this repository are additive (new tables, new nullable
columns, dropped-but-unused indexes) specifically so that old code tolerates
this window; a migration that removes or renames something a running
version of the code still reads would need to ship across two deploys
(add-and-dual-write, then remove) rather than one, the same rule any rolling
migration needs.
