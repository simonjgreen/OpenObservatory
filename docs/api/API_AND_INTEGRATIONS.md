# API and Integration Specification

This document is a seed specification, written before implementation began. The
system has since been partly built, and this revision annotates every item as
either **Implemented** (there is working code behind it today) or **Planned**
(design intent only, no code yet). Nothing has been deleted on the strength of
being unimplemented — the operating brief requires unimplemented but specified
components to remain recorded, not silently dropped.

Source of truth for what is implemented: `src/open_observatory/api/app.py`.
The implemented list below was regenerated from that file on **2026-08-09**.

## REST base

`/api/v1`

### Endpoints — implemented

| Endpoint | Notes |
|---|---|
| `GET /station` | station identity and the full pipeline snapshot |
| `GET /health` | always credential-free, even with auth on |
| `GET /system` | host facts |
| `GET /retention/status` | tiered clip retention state (ADR-026/029) |
| `GET /audio/devices` | |
| `POST /audio/probe` | |
| `GET /audiomoth` | |
| `GET /streams` | |
| `GET /gaps` | |
| `GET /detectors` | includes schedule state and deferred-queue lag |
| `GET /models` | installed model assets and their licences |
| `GET /detections` | credential-free by default even with auth on, for the ESP32 display (ADR-034) |
| `GET /detections/export` | CSV/JSON; registered *before* `/detections/{id}` so the path parameter does not swallow it |
| `GET /detections/{id}` | |
| `POST /detections/{id}/review` | append-only; body `{status: confirmed\|rejected, note?}` (ADR-029) |
| `GET /detections/{id}/review` | latest review, or `null` |
| `GET /taxa/activity` | |
| `GET /history` | |
| `GET /history/windows` | `last-hour`, `last-night`, `dawn-chorus`, `today`, `yesterday`, `last-24h` |
| `GET /media/{asset_id}` | `410 Gone` when the file has been reclaimed by retention |
| `GET /debug/pipeline` | |
| `GET /debug/levels` | |
| `GET /debug/events` | |
| `WS /live` | |
| `WS /live/audio` | |
| `GET /live/audio.wav` | the debug UI's default listen path (ADR-019) |
| `POST /live/tune` | retunes the shared ultrasonic oscillator in place (ADR-022) |

Authentication (ADR-034; the whole gate is inert while `auth_enabled` is `false`,
which is the default):

| Endpoint | Notes |
|---|---|
| `POST /auth/login` | rate-limited; returns `must_change_password` |
| `POST /auth/logout` | |
| `GET /auth/me` | |
| `POST /auth/password` | |
| `GET /auth/tokens` | |
| `POST /auth/tokens` | the token is shown in full exactly once; only a SHA-256 hash is stored |
| `DELETE /auth/tokens/{token_id}` | revokes immediately |

Also implemented, outside the `/api/v1` prefix: `GET /metrics` (Prometheus
exposition format, disabled via `metrics_enabled=false`; never gated by auth,
because the gate only matches `/api/v1/*` at all).

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

- `POST /audio/test-capture` — the functionality exists as the CLI command
  `oo audio test-capture`; only the HTTP route is unimplemented.
- `POST /detectors/{id}/enable`
- `POST /detectors/{id}/disable`
- ~~`POST /detections/{id}/reviews`~~ — **implemented, at the singular path
  `POST /api/v1/detections/{id}/review`** (ADR-029). Append-only; every call
  inserts, setting `supersedes_review_id` to the prior row. `corrected_taxon_id`
  is always written `None` — correcting a misidentified taxon implies a
  re-labelling pipeline and is deliberately left for a future ADR.
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

`GET /detections/export` takes **exactly the same filters**, so "export what I am
looking at" is true rather than approximate, plus `format` (`csv`|`json`, default
`csv`) and its own higher `limit` (1–20000, default 5000 — an export is a
deliberate one-off request, not a page a UI repaints).

Also present on `GET /detections/{id}` and `GET /taxa/activity`
(`include_synthetic`, same default and meaning; `taxa/activity` additionally takes
`hours`, 1–168, default 24), and on `GET /history`
(`include_synthetic`, applied to the `timeline`, `species` and `unidentified`
sections only — `coverage` is unaffected, see below).

`GET /history` takes `window` (default `last-night`), `since`, `until`,
`bucket_seconds` (10–86400), `min_score` (0.0–1.0), `include_unidentified`
(default `true`) and `include_synthetic` (default `false`).

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
switch was moved to `USB/OFF` and the problem was found on 2026-08-08 (this
document previously said 2026-08-05, disagreeing with ADR-020 and
`TARGET_DIAGNOSTICS.md`; the audio itself stopped at about 2026-08-07 06:26 UTC
and nothing noticed for 29 hours, derived from frame counts in
`OPEN_INVESTIGATION_CAPTURE_GAPS.md`). The station correctly fell back
to a synthetic source and correctly reported itself degraded, but detectors
kept running against synthetic audio and persisted 5 bird detections
(*Grey-winged Inca-Finch*, implausible at this station) plus 515 acoustic
events indistinguishable from genuine records in every browsing view that
existed at the time.

### `withdrawn` and `excluded_withdrawn_count` — implemented, ADR-044

A detection whose identification a plausibility review has retracted
(`oo detections reconcile-plausibility --apply`, ADR-032) is treated differently
depending on whether the surface is a *record* or a *claim*.

Every detection payload carries a top-level **`withdrawn`** boolean — always
present, false on the overwhelming majority — and a **`withdrawal`** object
(null unless withdrawn) holding the reviewer's recomputed occurrence
probability, band, threshold, reason and `reviewed_utc`, verbatim. The same
boolean appears in the derived `flags` block and as a `withdrawn` column in the
CSV export. The row itself is **not** hidden from `GET /detections`,
`GET /detections/{id}` or the export: the charter's item 5 requires that the
prior verdict stay visible and attributable, and a record the system got wrong
is evidence about the system.

The endpoints that aggregate *by species* do exclude it, because a `GROUP BY`
row has nothing to attach a marker to: `GET /history`'s `species` and
`unidentified` lists and `GET /taxa/activity` drop withdrawn detections and
report **`excluded_withdrawn_count`**, exactly as they report
`excluded_synthetic_count`. `GET /taxa/activity` and `history.species_summary`
take `include_withdrawn=true` as the diagnostic escape hatch.
`GET /history`'s `timeline` is deliberately unfiltered: it counts detections
and names nothing.

The MQTT publisher and the `/api/v1/display` counter-top display channel present a
withdrawn detection not at all. Both are claim surfaces with no room for a
caveat — a Home Assistant entity state is a bare name, and ADR-023 forbids the
display from showing a score — so marking is not an option there and silence is
the honest answer.

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
again unnoticed. See ADR-025.

## MQTT — implemented, disabled by default

`src/open_observatory/mqtt/` subscribes to the existing `EventBus`
(`station.bus`) and republishes `detection.created` (and a periodic health
snapshot, capture/detector state, and per-station metrics) over MQTT with
Home Assistant discovery. Off by default (`OO_MQTT_ENABLED=false`); every
setting is in `config/runtime.env` (see `config.py`'s MQTT section) and never
hardcoded. See `docs/operations/HOME_ASSISTANT.md` for topics, entities, setup
and verification with `mosquitto_sub`, and ADR-025 for the design.

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

## Authentication — implemented, off by default (ADR-034)

`auth_enabled` (`OO_AUTH_ENABLED`) defaults to `false`, and while it is false the
whole gate is inert: every endpoint above is anonymous, exactly as it always was.
With it enabled, a blanket Starlette middleware requires either an `oo_session`
cookie or `Authorization: Bearer <token>` on every `/api/v1/*` path except:

- `GET /api/v1/health` — hardcoded public, because `deploy/deploy.sh` polls it
  after every restart with no credential;
- `GET /metrics` — never matches the `/api/v1/*` prefix at all;
- anything in `auth_public_read_paths` (GET only; default exactly
  `/api/v1/detections`, for the ESP32 counter-top display, which cannot carry a
  credential and cannot be reflashed as part of an ordinary station upgrade).

`WS /api/v1/live` closes with code **4401** when a credential is required and
absent. **There is no TLS anywhere in this codebase**, so a session cookie or
bearer token is readable by anything positioned on the LAN;
`auth_cookie_secure` therefore defaults to `false`, because a `Secure` cookie on
a plain-HTTP origin is silently never sent back and turns a working login into
one that appears to succeed and authenticates nothing. See
`docs/operations/DEPLOYMENT_AND_OPERATIONS.md` for the operator procedure and
ADR-034 for the full trade-off.

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
