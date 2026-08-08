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
- reclaimed_at nullable — set by the retention sweeper (ADR-022) when it
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
- `detection.station_id`
- `detection.detector_id`
- `detection.stream_id`
- `detection.event_start_utc`
- `detection.canonical_taxon_id`
- `detection.taxonomic_group`
- `detection (station_id, event_start_utc desc)`
- `detection (taxonomic_group, event_start_utc desc)`
- `media_asset.kind`
- `media_asset.created_at`
- `review.detection_id`
- `health_event.service`
- `health_event.severity`
- `health_event.start_utc`

Planned only (would apply if the corresponding table is built):

- `telemetry_sample (series_id, timestamp desc)`
- GIN indexes on selected JSON fields only after measured need

## Retention

- analysis windows: minutes/hours, removed after all leases complete (planned
  — no `analysis_window` table exists yet, see above);
- rolling raw PCM: memory-only by default;
- evidence clips: **tiered aging, not a flat 30 days** (ADR-022,
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
