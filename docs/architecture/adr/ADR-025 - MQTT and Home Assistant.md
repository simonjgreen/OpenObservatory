---
aliases:
  - ADR-025
tags:
  - adr
---
# ADR-025: MQTT publisher and Home Assistant Discovery, off by default, on its own bus subscription
**Status:** active.

**Decision:** Milestone 6 adds `src/open_observatory/mqtt/` — `publisher.py` (the
runtime) and `discovery.py` (pure Home Assistant Discovery payload builders) — as a
consumer of the existing `EventBus` ([[ADR-009 - In-process event bus|ADR-009]]), the same seam the debug UI's
WebSocket already uses. `Settings.mqtt_enabled` defaults to `false`; an operator who
upgrades an existing station gets no new network traffic and no new failure mode
until they opt in. Alongside it, `schemas/detection-event.schema.json` moves from
`schema_version` `1.0` to `1.1`, fixing a gap recorded but not closed since
Milestone 3 (HANDOVER.md section 6.3 item 9): the schema had `additionalProperties:
false` and omitted `rank` and `taxonomic_group`, fields every internal detection
record actually carries. MQTT is what the handover note predicted would force this:
it is the first thing in this project that publishes the wire envelope to a
consumer outside this repository (Home Assistant), so a schema that quietly
disagreed with reality stops being an internal inconsistency and starts being a
contract break nobody would notice until an external validator rejected a message.

**Why `aiomqtt` and not raw `paho-mqtt`.** This codebase is asyncio throughout —
FastAPI, the capture loop, every worker in `station.py`. `aiomqtt` (pinned
`==2.3.0`) wraps paho's connection handling in `async with`/`async for`, matching
every other I/O boundary in the project. Raw paho runs its network loop on a
callback/thread model, which would mean either giving it its own thread (a second
thing, besides ALSA, needing deliberate isolation from the default executor) or
hand-rolling an asyncio bridge `aiomqtt` already provides. Version 3 of `aiomqtt`
adds built-in `reconnect=True`, but is not released to PyPI at the time of writing
(latest is `2.5.1`); this module implements manual reconnect with bounded
exponential backoff instead, the same pattern `aiomqtt`'s own migration guide shows
for v2.

**Why a bus subscription instead of a new dedicated queue.** `EventBus.subscribe`
already returns a bounded `asyncio.Queue` with drop-oldest-and-count semantics
(`events.py: Subscription.offer`) — this is precisely "a bounded queue with an
explicit drop policy" the brief asks for, already implemented and exercised by
every other bus consumer. `MqttPublisher` subscribes with
`maxsize=settings.mqtt_queue_depth` and reads the subscription's own `.dropped`
counter for its `dropped_total` metric rather than inventing a second bounded
queue and a second drop-counting mechanism that could disagree with the first.

**Why publishing can never block capture.** Nothing in `station.py`, the capture
loop, or any detector worker calls into `mqtt/`. The only coupling is one direction:
`EventBus.publish()` is synchronous and non-blocking (`events.py`), and offering an
event to a full subscriber queue drops the oldest queued item rather than waiting.
A broker that is down, slow, or rejects credentials therefore cannot propagate any
back-pressure to capture — there is no path for it to do so. All MQTT network I/O
happens through `aiomqtt`'s native asyncio driver, never through
`asyncio.to_thread`/`run_in_executor`, so it never contends with the ALSA blocking
read for the default thread pool the way a naive synchronous client would (see the
module docstring in `mqtt/publisher.py`, and [[ADR-021 - Clips on their own device|ADR-021]]'s own incident for what
sharing that pool with sustained I/O actually costs).

**Why [[ADR-020 - Non-live sources excluded|ADR-020]]'s synthetic-exclusion rule is re-implemented here rather than
inherited.** The bus event for `detection.created` carries the full detection
payload but not `source_kind` — that field lives on the `AudioStream` row and is
joined in at the API/DB layer, not carried on the in-process event. MQTT is exactly
the kind of "browsing/notification surface" [[ADR-020 - Non-live sources excluded|ADR-020]] was written for, so
`MqttPublisher._handle_detection` reads `capture_status_provider()["is_live_hardware"]`
(sourced from `station.status_snapshot()`, the same field `GET /api/v1/health`
already uses) and withholds publication for a synthetic or replay detection,
counting it in `suppressed_synthetic_total` rather than silently dropping it. The
row is still written to the database regardless, unaffected by this — only the
Home Assistant notification is withheld.

**Why the health sensor shares its logic with `GET /api/v1/health` instead of
recomputing it.** `api/app.py`'s `get_health` route body was extracted into
`_health_payload()`, called both by the route and passed as
`health_provider` to `MqttPublisher`. Two independently-maintained definitions of
"healthy" — one for the API, one for MQTT — would eventually disagree, most
plausibly exactly when it matters: the synthetic-source degradation case [[ADR-020 - Non-live sources excluded|ADR-020]]
and the 2026-08-08 incident (see [[HANDOVER]] section 3a) both turn
on. `binary_sensor.<station>_station_healthy` in Home Assistant means exactly what
`GET /api/v1/health`'s `status` field means, by construction, not by convention.

**Consequence — entity design.** See [[HOME_ASSISTANT]] for the
full entity table and setup instructions. In summary: one HA device per station,
`sensor.<slug>_last_detection` / `_species_today` / `_bat_passes_tonight`,
`binary_sensor.<slug>_bat_activity` / `_station_healthy`, and one `event` entity
(`event.<slug>_detection`) carrying coarse `event_types`
(`bird_detection`/`bat_pass_detection`/`other_detection`) with species and score as
attributes, so a per-species automation ("notify me when a tawny owl is heard") is
possible without HA discovery ever declaring one entity per species — deliberately
avoided, per the original design note this ADR's discovery code implements, because
the species set is open-world and cannot be a static discovery config, and a
permanent entity per species becomes unwieldy on a station with a busy bird list.
BirdNET's score is published as a plain `score` attribute, never
`device_class: probability` and never named "confidence" (CLAUDE.md's honesty
rule); a bat pass never carries a species field or a score at all, matching the
operator's decision that bat passes are always shown and never scored.

**What this explicitly does not implement.** Environmental telemetry ingestion, the
alert rule engine with repetition/cooldown, and HMAC webhooks remain unimplemented,
as scoped in [[IMPLEMENTATION_PLAN]]'s Milestone 6 description. `bat_passes_tonight`
resets at local midnight in the station's configured timezone, not at true civil
dawn — an approximation documented in [[HOME_ASSISTANT]] and in
`publisher.py`'s `_roll_day_counters_if_needed`, chosen because precise dawn
rollover would pull `schedule.py`'s solar geometry (and the station's optional
coordinates) into a module that otherwise has no dependency on them; the database
remains the authoritative record regardless of what the HA sensor shows.

**Reviewed 2026-08-29:** the decision holds. `mqtt/` is still nothing but an
`EventBus` consumer — `api/app.py:75` is the only import of it anywhere in `src/` —
`Settings.mqtt_enabled` still defaults to `False` (`src/open_observatory/config.py:601`),
the subscription is still taken at `mqtt_queue_depth` and still reports the bus
subscription's own `.dropped` counter rather than a second one
(`src/open_observatory/mqtt/publisher.py:177` and `:198`), and `SCHEMA_VERSION` is still
`"1.1"` (`src/open_observatory/events.py:34`). Two things a reader of the paragraphs
above would otherwise get wrong:

- The pin moved. `aiomqtt` is pinned `==2.5.1` (`pyproject.toml:40`) since the
  2026-08-19 dependency bump, not `==2.3.0` as written above. The reason for choosing it
  is unchanged, and so is the manual reconnect with bounded exponential backoff
  (`src/open_observatory/mqtt/publisher.py:288`) — 2.5.1 is still a v2 release.
- The synthetic-source rule is no longer the only thing that withholds a detection.
  `_handle_detection` also withholds a withdrawn detection ([[ADR-044 - Withdrawn detections|ADR-044]], counted as
  `suppressed_withdrawn_total`) and, unless `mqtt_publish_unidentified` is set
  (`src/open_observatory/config.py:653`, default false), a detection that names no
  species and is not a bat pass (`suppressed_unidentified_total`). Both follow the rule
  this ADR set for the synthetic case: the row is still written to the database, only
  the Home Assistant notification is withheld.

---
Part of the [[ADRS|Architecture Decision Record index]].
