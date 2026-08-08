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
- `GET /live/audio.wav`

Also implemented, outside the `/api/v1` prefix: `GET /metrics` (Prometheus
exposition format, disabled via `metrics_enabled=false`).

The two WebSocket endpoints and the WAV endpoint are documented in detail in
`DEBUG_UI_TRANSPORT.md`; see also ADR-012 in `docs/architecture/ADRS.md` for
the decision to use WebSocket rather than SSE for the live channel, and
ADR-019 for why `GET /live/audio.wav` was added and made the debug UI's
default listen path — Web Audio produced no audible output at all on a real
laptop, for reasons unrelated to the transport, and a plain `<audio>` element
against a chunked WAV stream did work on the same machine. `WS /live/audio` is
unchanged and still used by clients (a phone) that never had that problem.
`GET /live/audio.wav` takes the same `channel` (`audible` default,
`ultrasonic`) and `tune_hz` query parameters as the WebSocket path, streams a
44-byte WAV header with both size fields set to `0xFFFFFFFF` (the endless-
stream convention) followed by continuous 16-bit little-endian mono PCM,
answers `503` if the ultrasonic channel is unavailable for this station's
native rate, and carries `Cache-Control: no-store` plus `X-Live-Sample-Rate`
(and, on the ultrasonic channel, `X-Live-Tune-Hz`/`X-Live-Bandwidth-Hz`)
response headers in place of the WebSocket's JSON hello frame.

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
| `include_synthetic` | bool, default `false` | See below. |

Also present on `GET /detections/{id}` and `GET /taxa/activity`
(`include_synthetic`, same default and meaning), and on `GET /history`
(`include_synthetic`, applied to the `timeline`, `species` and `unidentified`
sections only — `coverage` is unaffected, see below).

### `include_synthetic` and `excluded_synthetic_count` — implemented, ADR-020

Every endpoint above that presents detections as observations excludes rows
whose stream's `source_kind` is not `alsa` (i.e. not from the physical
microphone — this covers both the `synthetic` fallback source and `replay` of
a fixture file) unless the caller passes `include_synthetic=true`. This is a
default, not a delete: the rows are stored regardless, because they are an
honest record of detector behaviour and useful for testing, but a browsing
view must not present a test scene as an observation. `GET /detections` and
`GET /taxa/activity` report `include_synthetic` and
`excluded_synthetic_count` alongside their results, so an empty result is
distinguishable from a genuinely quiet night. `GET /detections/{id}` on an
excluded row returns `404` with a detail explaining why and how to retrieve it
(`include_synthetic=true`), rather than `200` with data the caller didn't ask
to see. Detection payloads also carry `source_kind` and a derived
`is_live_source` boolean regardless of which mode was requested.
`GET /history`'s `coverage` block is deliberately exempt from this filter: it
already separates `seconds_from_microphone` from total coverage and exists to
answer "was the microphone listening", which synthetic-exclusion would
obscure rather than clarify. Motivating incident: the AudioMoth's USB mode
switch was moved to `USB/OFF` on 2026-08-05, the station correctly fell back
to a synthetic source and correctly reported itself degraded, but detectors
kept running against synthetic audio and persisted 5 bird detections
(*Grey-winged Inca-Finch*, implausible at this station) plus 515 acoustic
events indistinguishable from genuine records in every browsing view that
existed at the time.

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

**Fixed in schema_version 1.1 (Milestone 6).** The gap recorded here through
1.0 — `additionalProperties: false` with `rank` and `taxonomic_group` missing,
even though every internal detection record carries them (see `detection` in
`DATA_MODEL.md`) — is closed. The schema now describes the full envelope
(`schema_version`, `event_id`, `event_type`, `occurred_at`, `station_id`,
`data`) rather than just the inner record, because the MQTT publisher made
that envelope the first shape something outside this repository depends on.
`data` includes `rank`, `taxonomic_group` and the `media` array the WebSocket
and MQTT paths both already send. `tests/test_mqtt_schema.py` validates a real
emitted `detection.created` event against the schema so this cannot drift
again unnoticed. See ADR-022.

## MQTT — implemented, disabled by default

`src/open_observatory/mqtt/` subscribes to the existing `EventBus`
(`station.bus`) and republishes `detection.created` (and a periodic health
snapshot, capture/detector state, and per-station metrics) over MQTT with
Home Assistant discovery. Off by default (`OO_MQTT_ENABLED=false`); every
setting is in `config/runtime.env` (see `config.py`'s MQTT section) and never
hardcoded. See `docs/operations/HOME_ASSISTANT.md` for topics, entities, setup
and verification with `mosquitto_sub`, and ADR-022 for the design.

Topic layout (`{prefix}` defaults to `openobservatory/{station_id}`):

- `{prefix}/status/availability` retained, LWT `offline`
- `{prefix}/status/capture` retained JSON
- `{prefix}/status/health` retained JSON
- `{prefix}/detection` non-retained JSON (the envelope above)
- `{prefix}/metrics/species_today` retained
- `{prefix}/metrics/bat_passes_tonight` retained
- `homeassistant/.../config` retained MQTT Discovery payloads

Publishing never blocks capture: the publisher consumes from a bounded
per-subscriber queue on the bus (the same drop-oldest, drop-counted policy
every other consumer uses) and reconnects with bounded exponential backoff
when the broker is unreachable. Broker/credential state is surfaced at
`GET /api/v1/health` (`mqtt` block) and as `oo_mqtt_*` Prometheus metrics.

Not implemented: environmental telemetry ingestion, the alert rule engine,
and HMAC webhooks (still planned, see below and the original Milestone 6
scope in `IMPLEMENTATION_PLAN.md`).

## Home Assistant discovery — implemented

Discovery entities are published retained under `{discovery_prefix}/.../config`
(default `homeassistant`, matching HA's default) the first time the publisher
connects, grouped under one HA device via the `device` block. Entities:
`sensor.<slug>_last_detection`, `sensor.<slug>_species_today`,
`sensor.<slug>_bat_passes_tonight`, `binary_sensor.<slug>_bat_activity`,
`binary_sensor.<slug>_station_healthy`, and an `event` platform entity per
station for detection notifications in automations. No numeric score is ever
published as `device_class: probability`, and bat detections never carry a
species name — see the full entity table and the calibration caveat in
`docs/operations/HOME_ASSISTANT.md`. One entity per detected species is
deliberately not created, per the original design intent below.

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
