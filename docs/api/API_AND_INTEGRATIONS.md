# API and Integration Specification

This document is a seed specification, written before implementation began. The
system has since been partly built, and this revision annotates every item as
either **Implemented** (there is working code behind it today) or **Planned**
(design intent only, no code yet). Nothing has been deleted on the strength of
being unimplemented — the operating brief requires unimplemented but specified
components to remain recorded, not silently dropped.

Source of truth for what is implemented: `src/open_observatory/api/app.py`.
The implemented list below was re-checked route by route against that file on
**2026-08-09, after the [[ADR-043]]/045/049/050/052 work merged to `main`** — an
earlier "regenerated on 2026-08-09" claim was made against a branch that predated
those merges and was missing seven routes.

## REST base

`/api/v1`

### Endpoints — implemented

| Endpoint | Notes |
|---|---|
| `GET /station` | station identity and the full pipeline snapshot |
| `GET /health` | always credential-free, even with auth on |
| `GET /system` | host facts |
| `GET /settings` | the whole operator-editable catalogue, with tier, bounds, units and shipped defaults ([[ADR-047]]/048) |
| `PUT /settings` | partial update; validates, persists to `config/runtime.env`, applies live where safe |
| `GET /setup` | guided first-run state: what a new station still needs ([[ADR-048]]) |
| `GET /retention/status` | tiered clip retention state ([[ADR-026]]/029) |
| `GET /pause` | the privacy pause: current state **and** the durations offered, in one response ([[ADR-055]]) |
| `POST /pause` | body `{"preset": "15m"\|"1h"\|"3h"\|"6h"\|"until-midnight"}`. Stops detection, evidence, publishing and live listening until the deadline; capture keeps running. Pressing it while paused *replaces* the deadline. 422 on an unknown key |
| `DELETE /pause` | resume now. Idempotent — resuming a station that is not paused is a 200 |
| `GET /audio/devices` | |
| `POST /audio/probe` | |
| `GET /audiomoth` | |
| `GET /streams` | |
| `GET /gaps` | |
| `GET /detectors` | includes schedule state and deferred-queue lag |
| `GET /detectors/near-misses` | what BirdNET proposed and refused, in a bounded in-memory ring with per-band score histograms ([[ADR-052]]). `limit` 0–500 (default 50), `species_limit` 0–500 (default 40). **Persists nothing** — it is a live ring, empty after a restart |
| `GET /models` | installed model assets and their licences |
| `GET /detections` | credential-free by default even with auth on, for the ESP32 display ([[ADR-034]]) |
| `GET /detections/export` | CSV/JSON; registered *before* `/detections/{id}` so the path parameter does not swallow it |
| `GET /detections/{id}` | |
| `POST /detections/{id}/review` | append-only; body `{status: confirmed\|rejected\|corrected\|held, note?, corrected_taxon_id?}` ([[ADR-029]], closed by [[ADR-043]]). `corrected_taxon_id` is required iff `status == "corrected"` and 400s on an unknown id; `held` also exempts the detection's evidence from retention's age tiers |
| `GET /detections/{id}/review` | latest review, or `null` |
| `GET /taxa/search` | resolves a taxon the station has itself identified before, for the correction picker ([[ADR-043]]). `q` required, 1–120 chars; `limit` 1–100 (default 20); returns `{taxa, source: "station_history"}` |
| `GET /taxa/activity` | |
| `GET /history` | |
| `GET /history/windows` | `last-hour`, `last-night`, `dawn-chorus`, `today`, `yesterday`, `last-24h`, `last-7d`. This is what the dashboard puts on screen, **not** the whole grammar `window` accepts — see below. |
| `GET /media/{asset_id}` | `410 Gone` when the file has been reclaimed by retention |
| `GET /debug/pipeline` | |
| `GET /debug/levels` | |
| `GET /debug/events` | |
| `WS /live` | |
| `WS /live/audio` | |
| `WS /display` | the counter-top display's push channel — detections only, ~49 B a detection ([[ADR-038]]), plus [[ADR-050]]'s OTA offer frame. Wire format in [`DEBUG_UI_TRANSPORT.md`](DEBUG_UI_TRANSPORT.md) |
| `GET /live/audio.wav` | the debug UI's default listen path ([[ADR-019]]) |
| `POST /live/tune` | retunes the shared ultrasonic oscillator in place ([[ADR-022]]) |

Counter-top display firmware over the air ([[ADR-050]]). All five verified on
hardware on 2026-08-09, including a deliberate rollback drill:

| Endpoint | Notes |
|---|---|
| `GET /firmware` | what is published and who is behind: `published`, `image_path`, `offer_on_connect`, `app_slot_bytes`, and `displays[]` with `firmware_version` / `up_to_date` / `frames_sent` |
| `POST /firmware` | raw `.bin` body; `version` and `notes` query params. Validates 0xE9 magic, chip id and `esp_app_desc_t`, and 422s with `{"errors": {"image": …}}` |
| `DELETE /firmware` | removes the published image; nothing else reads `data/firmware/` |
| `GET /firmware/image` | the bytes themselves; 404 when nothing is published. Public-read by default, since the display carries no credential |
| `POST /firmware/rollout` | offers the image to connected displays; 409 when nothing is published. **`offered` is how many displays were *told*, not how many installed** — only the display can say that, via `GET /station`'s `display_channel.per_client[].firmware_version` |

Authentication ([[ADR-034]]; the whole gate is inert while `auth_enabled` is `false`,
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
[[DEBUG_UI_TRANSPORT]]; see also [[ADR-012]] in [[ADRS]] for
the decision to use WebSocket rather than SSE for the live channel, and
[[ADR-019]] for why `GET /live/audio.wav` was added and made the debug UI's
default listen path — Web Audio produced no audible output at all on a real
laptop, for reasons unrelated to the transport, and a plain `<audio>` element
against a chunked WAV stream did work on the same machine. `WS /live/audio` is
unchanged and still used by clients (a phone) that never had that problem.
`GET /live/audio.wav` takes the same `channel` (`audible` default,
`ultrasonic`) and `tune_hz` query parameters as the WebSocket path, streams a
44-byte WAV header with both size fields set to `0xFFFFFFFF` (the endless-
stream convention) followed by continuous 16-bit little-endian mono PCM,
answers `503` if the ultrasonic channel is unavailable for this station's
native rate **or if the operator has paused the station ([[ADR-055]])** — in the
pause case the `detail` is the pause banner, which `web/src/audio.ts` surfaces
on the listen control — and carries `Cache-Control: no-store` plus `X-Live-Sample-Rate`
(and, on the ultrasonic channel, `X-Live-Tune-Hz`/`X-Live-Bandwidth-Hz`)
response headers in place of the WebSocket's JSON hello frame.

### Endpoints — planned, not implemented

- `POST /audio/test-capture` — the functionality exists as the CLI command
  `oo audio test-capture`; only the HTTP route is unimplemented.
- `POST /detectors/{id}/enable`
- `POST /detectors/{id}/disable`
- ~~`POST /detections/{id}/reviews`~~ — **implemented, at the singular path
  `POST /api/v1/detections/{id}/review`** ([[ADR-029]]). Append-only; every call
  inserts, setting `supersedes_review_id` to the prior row. ~~`corrected_taxon_id`
  is always written `None`.~~ **Closed by [[ADR-043]], 2026-08-09:** a correction is
  resolved through `GET /api/v1/taxa/search` and denormalised onto the review row
  as `corrected_taxon_id` / `corrected_common_name` / `corrected_scientific_name`.
  The detection's own claim columns are never touched, so the original stays
  visible and attributable.
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
| `window` | string | A window name, resolved in the station's timezone. Ignored when `since` is given explicitly. `GET /history/windows` lists what the dashboard offers; the full grammar is in "Window names" below. An unrecognised name falls back to `last-hour`, and the response's `range.label` always says which window you actually got. |
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

### Window names — ADR-056

`window` is one opaque string on `GET /history`, `GET /detections` and
`GET /detections/export` alike, so anything here works on all three. Every form
is resolved in the station's configured timezone and returned in UTC, and
**no window ever ends in the future** — an unfinished calendar period is
truncated at *now*, so `this-month` on the tenth is ten days long rather than
thirty-one (otherwise `coverage` would divide by a month that has not happened
and report a working station as one-third captured).

| Form | Examples | Meaning |
|---|---|---|
| Named | `last-hour`, `last-24h`, `today`, `yesterday`, `last-night`, `dawn-chorus` | unchanged; `last-night` is 20:00–08:00 local, `dawn-chorus` 03:00–10:00 local |
| Rolling relative | `last-7d`, `last-30d`, `last-36h` | ends *now*, not at midnight. 1–168 hours or 1–3660 days |
| Calendar period | `this-week`, `last-week`, `this-month`, `last-month`, `this-year`, `last-year` | local calendar; ISO weeks start Monday |
| Calendar literal | `2026-08-05`, `2026-W32`, `2026-07`, `2026` | a day, an ISO week, a month, a year |

So "every bat pass in July, as a spreadsheet" is:

```bash
curl -sG 'http://<station-host>:8080/api/v1/detections/export' \
  --data-urlencode 'window=2026-07' --data-urlencode 'group=bat' \
  --data-urlencode 'format=csv' --data-urlencode 'limit=20000' -o bats-july.csv
```

**A caution about the long ones.** `GET /history` aggregates the detection
table directly, at a measured ~16 µs per detection row on the Pi 5, so a window
costs roughly what it contains: about 2 s for seven days, 10 s for thirty and
30 s for ninety at this station's mid-2026 detection rate. `GET /detections`
and the export are bounded by their `limit` and do not have this problem.
[[ADR-056]] measures all of it and proposes the roll-up that fixes it; until then,
prefer the export for wide ranges.

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
document previously said 2026-08-05, disagreeing with [[ADR-020]] and
[[TARGET_DIAGNOSTICS]]; the audio itself stopped at about 2026-08-07 06:26 UTC
and nothing noticed for 29 hours, derived from frame counts in
[[OPEN_INVESTIGATION_CAPTURE_GAPS]]). The station correctly fell back
to a synthetic source and correctly reported itself degraded, but detectors
kept running against synthetic audio and persisted 5 bird detections
(*Grey-winged Inca-Finch*, implausible at this station) plus 515 acoustic
events indistinguishable from genuine records in every browsing view that
existed at the time.

### `withdrawn` and `excluded_withdrawn_count` — implemented, ADR-044

A detection whose identification a plausibility review has retracted
(`oo detections reconcile-plausibility --apply`, [[ADR-032]]) is treated differently
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
caveat — a Home Assistant entity state is a bare name, and [[ADR-023]] forbids the
display from showing a score — so marking is not an option there and silence is
the honest answer.

### Human review on the detection payload — implemented, ADR-043

A correction outranks the machine, and the payload says so rather than quietly
substituting a name. Every detection returned by `GET /detections` and
`GET /detections/{id}` carries:

| Field | Meaning |
|---|---|
| `review` | the latest review row in full, or null — `status`, `note`, `reviewed_by`, `reviewed_at`, `supersedes_review_id`, and the correction triple |
| `identification_source` | `"human"` when a correction is in force, `"model"` otherwise |
| `effective_common_name` | the corrected name if corrected, else the model's |
| `effective_scientific_name` | likewise |

The detection's own `common_name` / `scientific_name` / `canonical_taxon_id` are
**never** overwritten, which is what keeps the original attributable. The CSV
export carries `identification_source`, `effective_common_name`,
`effective_scientific_name`, `review_status`, `reviewed_by` and `reviewed_at`
alongside `withdrawn`.

**Two stated limits.** A correction can only name a taxon the station has itself
identified before (that is what `GET /taxa/search` searches), and
`GET /api/v1/history` still aggregates on the *original* taxonomy — corrections
are not yet folded into its `GROUP BY`.

### Refinement has no HTTP surface — deliberate, ADR-045

The refinement runner writes `refinement` rows and stamps `detection.refined_at`
/ `refinement_version` / `refinement_outcome`, and **none of it is exposed
through the API at all** — not on the detection payload, not as an endpoint.
That is the point: a BatDetect2 proposal is not a station claim, and nothing it
writes may reach the API, MQTT, the web UI or the display. `oo refine status` is
the only way to read one. Wiring a proposal to the review workflow is open work,
not an oversight.

## Event stream

**Planned wording superseded.** The original text below is stale and is kept
only as a record of the original intent:

> ~~SSE is sufficient for v1 dashboard updates; WebSocket may be added for
> richer controls.~~

In fact WebSocket is the only live transport implemented, and no SSE endpoint
exists or is planned. See [[DEBUG_UI_TRANSPORT]] for the full wire format of
`GET /api/v1/live` and `GET /api/v1/live/audio`, and [[ADR-012]] in
[[ADRS]] for why WebSocket was chosen over SSE.

### Event envelope

The wire contract is `schemas/detection-event.schema.json`; treat that file as
authoritative rather than any example reproduced here, because a previous
inline example in this document had drifted from it.

**Fixed in schema_version 1.1 (Milestone 6).** The gap recorded here through
1.0 — `additionalProperties: false` with `rank` and `taxonomic_group` missing,
even though every internal detection record carries them (see `detection` in
[[DATA_MODEL]]) — is closed. The schema now describes the full envelope
(`schema_version`, `event_id`, `event_type`, `occurred_at`, `station_id`,
`data`) rather than just the inner record, because the MQTT publisher made
that envelope the first shape something outside this repository depends on.
`data` includes `rank`, `taxonomic_group` and the `media` array the WebSocket
and MQTT paths both already send. `tests/test_mqtt_schema.py` validates a real
emitted `detection.created` event against the schema so this cannot drift
again unnoticed. See [[ADR-025]].

## MQTT — implemented, disabled by default

`src/open_observatory/mqtt/` subscribes to the existing `EventBus`
(`station.bus`) and republishes `detection.created` (and a periodic health
snapshot, capture/detector state, and per-station metrics) over MQTT with
Home Assistant discovery. Off by default (`OO_MQTT_ENABLED=false`); every
setting is in `config/runtime.env` (see `config.py`'s MQTT section) and never
hardcoded. See [[HOME_ASSISTANT]] for topics, entities, setup
and verification with `mosquitto_sub`, and [[ADR-025]] for the design.

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
scope in [[IMPLEMENTATION_PLAN]]).

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
[[HOME_ASSISTANT]]. One entity per detected species is
deliberately not created, per the original design intent below.

## Settings — implemented (ADR-047, widened by ADR-048)

A station is configured from the browser. `config/runtime.env` on the device is
the only store; the UI is a second *writer* of that file, not a second
configuration, so a hand edit and a UI edit are indistinguishable at the next
startup. Writes are atomic, preserve comments and keys outside the catalogue,
and are mode 0600.

### `GET /api/v1/settings`

```json
{
  "fields": [
    {
      "name": "ultrasonic_min_snr_db",
      "category": "detect-ultrasonic",
      "tier": "live",
      "kind": "float",
      "label": "pulse SNR threshold",
      "help": "How far above the tracked band noise floor a pulse must sit…",
      "unit": "dB",
      "minimum": 0.0, "maximum": 90.0,
      "choices": [],
      "danger": null,
      "secret": false,
      "restart_required": false,
      "note": null,
      "default": 12.0,
      "value": 20.0
    }
  ],
  "categories": [{ "id": "station", "title": "Station", "description": "…", "hidden": false }],
  "non_editable": [{ "name": "bind_port", "reason": "…lockout…" }],
  "pending_restart": ["latitude", "longitude"],
  "location_configured": false
}
```

- `tier` is `"live"` (in force now) or `"restart"` (saved, applied at next
  start). `restart_required` is the same fact under the name the field has
  carried since [[ADR-047]].
- `default` is the value shipped in `config.py` — the operator's documented way
  back to a known state, including [[ADR-041]]'s measured spectrogram floors.
- An individual field also carries `"pending_restart": true` when it is one of
  the fields named in the top-level `pending_restart` list, so a UI need not
  cross-reference.
- `pending_restart` names every field whose **saved** value differs from what
  the running components are actually using, live-tier fields included: a live
  setting whose target object does not exist (no ultrasonic encoder at 48 kHz)
  is reported as pending, never as applied. The same list appears in
  `GET /station` as `station.site_pending_restart` and in `GET /health` under
  `notes`.
- Secrets (`mqtt_password`) report `"value": null` with `"is_set": true|false`
  and are never echoed.
- Sequence-valued fields (`preferred_sample_rates`, `activity_band_hz`,
  `clip_plugins`, …) are rendered and accepted as the comma-separated form
  `runtime.env` stores.
- `danger` is set on settings that are legitimate but can cost recordings
  (`source`, `audio_device`, `clip_plugins`, `mqtt_tls_insecure`). The UI
  requires an explicit acknowledgement; the API does not — it is advice, not a
  gate.
- `non_editable` lists what is deliberately not editable from a browser, with
  the hazard. See [[ADR-048]] for the reasoning on each.

### `PUT /api/v1/settings`

Body is a partial object of `field: value`. Strings are coerced; `null` or `""`
**restores the shipped default** (for an optional field that means unset).
Unknown or excluded fields are refused rather than ignored.

`200` returns the same shape as `GET`, plus `"saved": [names]`.

`422` returns `{"detail": {"errors": {"<field>": "<message>"}}}` naming **every**
failing field at once, so a form round-trips one correction pass. Validation
runs before anything is written: a rejected request changes neither the file
nor the process, and cross-field rules (floor below ceiling, ring at least two
capture blocks, retention ladder in order, at least one sample rate offered)
mean it cannot leave the station unable to start capture. A rule fires only
when one of its own fields is in the request, so a pre-existing inconsistency
never blocks an unrelated edit.

```bash
curl -s -X PUT http://<station-host>:8080/api/v1/settings \
  -H 'content-type: application/json' \
  -d '{"ultrasonic_min_snr_db": 20, "ultrasonic_min_pulses_per_pass": 5}'
```

### `GET /api/v1/setup`

```json
{
  "completed": false,
  "required_outstanding": ["location", "microphone"],
  "steps": [
    {
      "id": "microphone",
      "title": "Is the microphone working?",
      "detail": "Recording from AudioMoth USB at 384000 Hz.",
      "done": true,
      "optional": false,
      "fields": ["audio_device", "preferred_sample_rates", "source"]
    }
  ]
}
```

Four steps: `location`, `timezone` (optional), `microphone`, `mqtt` (optional).
The microphone step reads **live capture state**, not a stored flag, so a
station running on the synthetic fallback reports that rather than ticking a
box. Dismissal is the ordinary setting `setup_completed`, written through
`PUT /settings` — it is a fact about the station, not about one browser.

## Authentication — implemented, off by default (ADR-034)

`auth_enabled` (`OO_AUTH_ENABLED`) defaults to `false`, and while it is false the
whole gate is inert: every endpoint above is anonymous, exactly as it always was.
With it enabled, a blanket Starlette middleware requires either an `oo_session`
cookie or `Authorization: Bearer <token>` on every `/api/v1/*` path except:

- `GET /api/v1/health` — hardcoded public, because `deploy/deploy.sh` polls it
  after every restart with no credential;
- `GET /metrics` — never matches the `/api/v1/*` prefix at all;
- anything in `auth_public_read_paths` — **three paths by default**, all for the
  ESP32 counter-top display, which cannot carry a credential: `/api/v1/detections`
  (the HTTP fallback), `/api/v1/display` (the push channel — a WebSocket, checked
  before the 4401 close, not a GET) and `/api/v1/firmware/image` (so a display can
  fetch its own update). [[ADR-050]] removed the "cannot be reflashed" half of the
  original reasoning: it can now be updated over the air, but still without a
  credential.

`WS /api/v1/live` closes with code **4401** when a credential is required and
absent. **There is no TLS anywhere in this codebase**, so a session cookie or
bearer token is readable by anything positioned on the LAN;
`auth_cookie_secure` therefore defaults to `false`, because a `Secure` cookie on
a plain-HTTP origin is silently never sent back and turns a working login into
one that appears to succeed and authenticates nothing. See
[[DEPLOYMENT_AND_OPERATIONS]] for the operator procedure and
[[ADR-034]] for the full trade-off.

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
