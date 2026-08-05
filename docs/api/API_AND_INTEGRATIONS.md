# API and Integration Specification

This document is a seed specification, written before implementation began. The
system has since been partly built, and this revision annotates every item as
either **Implemented** (there is working code behind it today) or **Planned**
(design intent only, no code yet). Nothing has been deleted on the strength of
being unimplemented — the operating brief requires unimplemented but specified
components to remain recorded, not silently dropped.

Source of truth for what is implemented: `src/open_observatory/api/app.py`.

## REST base

`/api/v1`

### Endpoints — implemented

- `GET /station`
- `GET /health`
- `GET /system`
- `GET /audio/devices`
- `POST /audio/probe`
- `GET /audiomoth`
- `GET /streams`
- `GET /gaps`
- `GET /detectors`
- `GET /models`
- `GET /detections`
- `GET /detections/{id}`
- `GET /taxa/activity`
- `GET /history`
- `GET /history/windows`
- `GET /media/{asset_id}`
- `GET /debug/pipeline`
- `GET /debug/levels`
- `GET /debug/events`
- `WS /live`
- `WS /live/audio`

Also implemented, outside the `/api/v1` prefix: `GET /metrics` (Prometheus
exposition format, disabled via `metrics_enabled=false`).

The two WebSocket endpoints are documented in detail in
`DEBUG_UI_TRANSPORT.md`; see also ADR-012 in `docs/architecture/ADRS.md` for
the decision to use WebSocket rather than SSE for the live channel.

### Endpoints — planned, not implemented

- `POST /audio/test-capture`
- `POST /detectors/{id}/enable`
- `POST /detectors/{id}/disable`
- `POST /detections/{id}/reviews` — the `review` table exists in the schema
  (see `DATA_MODEL.md`) but nothing in the codebase writes to it; there is no
  endpoint and no other write path.
- `GET /detections/{id}/media` — superseded, not merely unimplemented. Media
  metadata is embedded directly in the detection payload returned by
  `GET /detections/{id}` (and by `GET /detections`), under a `media` array
  each entry of which carries a `url` pointing at the implemented
  `GET /api/v1/media/{asset_id}`, which serves the underlying file. A separate
  listing endpoint was never built because it would duplicate that array.
- `GET /telemetry`
- `GET /alerts/rules`
- `POST /alerts/rules`
- `POST /exports`
- `GET /exports/{id}`

Use cursor pagination for any of the above once built. Filters must be
explicit and documented. OpenAPI is generated and checked into release
artefacts.

### `GET /detections` query parameters — implemented

| Parameter | Type | Notes |
|---|---|---|
| `limit` | int, 1–500, default 100 | |
| `since` | datetime | |
| `until` | datetime | |
| `window` | string | Named window (e.g. `last-night`), resolved in the station's timezone. Ignored when `since` is given explicitly. See `GET /history/windows` for the available names. |
| `group` | string | Filter to one `taxonomic_group`. |
| `plugin_id` | string | Filter to detections from one detector plugin. |
| `identified_only` | bool, default `false` | Restrict to taxonomic groups considered identified. |
| `min_score` | float, 0.0–1.0, default 0.0 | |

## Event stream

**Planned wording superseded.** The original text below is stale and is kept
only as a record of the original intent:

> ~~SSE is sufficient for v1 dashboard updates; WebSocket may be added for
> richer controls.~~

In fact WebSocket is the only live transport implemented, and no SSE endpoint
exists or is planned. See `DEBUG_UI_TRANSPORT.md` for the full wire format of
`GET /api/v1/live` and `GET /api/v1/live/audio`, and ADR-012 in
`docs/architecture/ADRS.md` for why WebSocket was chosen over SSE.

### Event envelope

The wire contract is `schemas/detection-event.schema.json`; treat that file as
authoritative rather than any example reproduced here, because a previous
inline example in this document had drifted from it.

**Known open gap:** the schema sets `additionalProperties: false` and does not
include `rank` or `taxonomic_group`, both of which internal detection records
carry (see `detection` in `DATA_MODEL.md`). Events on the wire therefore drop
those two fields; this has not yet been reconciled.

## MQTT — planned, not implemented

No code in this repository publishes to MQTT. The design below is retained as
intent.

Default root: `openobservatory/{station_id}`

Topics:

- `status/availability` retained
- `status/capture` retained
- `status/detectors/{detector_id}` retained
- `metrics/species_today` retained
- `metrics/capture_continuity` retained
- `detection` non-retained JSON
- `alert` non-retained JSON
- `health/event` non-retained JSON

Telemetry input mappings are configured as topic + JSONPath/value/unit
mappings.

## Home Assistant discovery — planned, not implemented

No discovery-publishing code exists. Intent, once MQTT is implemented:
publish discovery entities for:

- station availability;
- capture state;
- uninterrupted capture duration;
- last species;
- last detection time;
- species count today;
- disk free;
- microphone level/clipping;
- each detector state and lag;
- high-severity health event count.

Do not create one permanent entity per detected species by default; this
becomes unwieldy.

## MCP server — planned, not implemented

No MCP server code exists in this repository. Intent: expose concise, bounded
tools:

- `get_station_status`
- `list_recent_detections`
- `summarise_activity`
- `compare_periods`
- `get_species_history`
- `get_detection_evidence_metadata`
- `list_capture_gaps`
- `list_health_events`
- optional `review_detection`

MCP tools return structured data and never embed raw audio. Media is
represented by an authenticated local URL or asset ID. Queries must enforce
maximum date ranges and result counts.

## Webhooks — planned, not implemented

No outgoing-webhook code exists. Intent: outgoing webhook payload uses the
common event envelope, signed with HMAC SHA-256. Retries are bounded with
exponential backoff and dead-letter reporting.
