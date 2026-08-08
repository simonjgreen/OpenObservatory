# Home Assistant integration

Milestone 6. The station can publish its state and detections to an MQTT broker
and register itself in Home Assistant automatically via [MQTT
Discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery) —
no YAML required on the Home Assistant side. This is off by default
(`OO_MQTT_ENABLED=false`); nothing changes for an existing station until you
opt in.

See ADR-025 in `docs/architecture/ADRS.md` for why it is built this way
(bounded queue reused from the event bus, `aiomqtt`, no shared thread pool,
graceful degradation). This document is the operator-facing half: what
appears in Home Assistant, what it means, what it explicitly does not mean,
how to point the station at a broker, and how to verify it.

## What you need to provide

The station has no broker address or credentials built in, and never will —
they are operator-owned configuration, not something baked into this
repository. To go live you need:

- **Broker host and port.** Your Home Assistant box's MQTT broker (commonly
  the Mosquitto add-on, default port `1883`, or `8883` for TLS).
- **Whether it needs a username/password.** The Mosquitto add-on usually does
  once you've set up a login for it; check Home Assistant's MQTT integration
  settings (Settings → Devices & Services → MQTT) for what it's configured
  with.
- **Whether it needs TLS.** Only relevant if the broker is reachable off your
  LAN or you've deliberately enabled TLS on it. A LAN-only Mosquitto add-on
  typically does not.

Nothing else. Discovery prefix, topic prefix, QoS and client ID all have
working defaults.

## Configuration

Edit `config/runtime.env` on the station (copy from `config/example.env` if
you don't have one yet; it is gitignored and never checked in):

```env
OO_MQTT_ENABLED=true
OO_MQTT_HOST=192.0.2.50        # your Home Assistant / broker box
OO_MQTT_PORT=1883
OO_MQTT_TLS=false
OO_MQTT_USERNAME=openobservatory  # blank if the broker allows anonymous
OO_MQTT_PASSWORD=change-me
```

Restart the station (`systemctl restart open-observatory` on the target — see
`DEPLOYMENT_AND_OPERATIONS.md`; in development, just restart the process).
Home Assistant should show a new device named after `OO_STATION_NAME`
(default "Garden Observatory") within a few seconds, with no further action
on the HA side — MQTT Discovery is on by default in Home Assistant's MQTT
integration.

Full list of `OO_MQTT_*` settings: `src/open_observatory/config.py`, the "MQTT
/ Home Assistant" section. Everything is documented inline there, including
`OO_MQTT_QOS`, `OO_MQTT_TOPIC_PREFIX`, `OO_MQTT_DISCOVERY_PREFIX`, and the
reconnect backoff bounds.

## What appears in Home Assistant

One HA device, named after the station, grouping every entity below.

| Entity | Type | Meaning |
|---|---|---|
| `sensor.<slug>_last_detection` | sensor | The most recent live detection's display name (species name, or "Bat pass"). Attributes: `detected_at`, `detector` (plugin id), `scientific_name`, `taxonomic_group`, and `score` for non-bat detections. |
| `sensor.<slug>_species_today` | sensor | Count of distinct species detected since local midnight. |
| `sensor.<slug>_bat_passes_tonight` | sensor | Count of bat passes since local midnight (see caveat below). |
| `binary_sensor.<slug>_bat_activity` | binary_sensor | ON for `OO_MQTT_BAT_ACTIVITY_WINDOW_S` (default 900s / 15 min) after the most recent bat pass, then reverts to OFF automatically. |
| `binary_sensor.<slug>_station_healthy` | binary_sensor | ON exactly when `GET /api/v1/health` reports `status: ok` — the same check, not a second implementation of it. Attributes include the `problems` list and `capture` block from that endpoint. |
| `event.<slug>_detection` | event | Fires on every live detection. `event_type` is one of `bird_detection`, `bat_pass_detection`, `other_detection`. Attributes carry the species/score detail — see the automation example below for how to filter on them. |

`<slug>` is your station name, lowercased and underscored (`Garden
Observatory` → `garden_observatory`).

Availability: the station publishes `online`/`offline` with a broker-side
[Last Will and Testament](https://www.home-assistant.io/integrations/mqtt/#last-will-and-testament),
so if the process dies or the network drops without a clean shutdown, Home
Assistant marks every entity on this device "unavailable" itself — you do not
need a separate offline detector.

## What this does NOT mean — read before wiring an automation

- **`score` is not a probability or a confidence percentage.** BirdNET's
  score is not calibrated; a score of 0.8 does not mean "80% likely correct."
  No entity here uses `device_class: probability`, and the word
  "confidence"/"probability" never appears in an entity name or attribute,
  enforced by both `tests/test_mqtt_discovery.py` and
  `tests/test_mqtt_publisher.py`. Use the score to sort or threshold your own
  automations if you want, but do not present it to household members as a
  percentage chance of correctness.
- **A bat pass is not a species identification.** `binary_sensor.<slug>_bat_activity`
  and `sensor.<slug>_bat_passes_tonight` never carry a species name, and
  `sensor.<slug>_last_detection` shows literally "Bat pass" for one, with no
  `score` attribute at all — bat passes are always shown and never scored,
  by design. `ultrasonic-pass-v1` detects echolocation passes, not bat
  species; the detector and the normaliser it feeds are both structurally
  prevented from emitting one (see `normaliser.py`'s `ClaimViolation`), and
  the publisher strips any species-shaped field defensively even so.
- **`bat_passes_tonight` resets at local midnight, not at dawn.** "Tonight"
  is approximated as the calendar day in your configured timezone
  (`OO_TIMEZONE`), not true civil dawn-to-dawn. A pass just after midnight
  but before dawn will count toward the *new* day's total. This keeps the
  publisher independent of the station's optional latitude/longitude; the
  database (`GET /api/v1/detections`) is the authoritative record regardless
  of what this sensor shows at any moment.
- **`station_healthy` going OFF does not mean capture stopped.** It also goes
  OFF when the station is running on the synthetic fallback source (no real
  microphone signal) or a detector reports degraded/error state — check the
  `problems` attribute for which.
- **This integration never reads anything from Home Assistant.** It is
  publish-only. Environmental telemetry ingestion (temperature/humidity feeds
  *into* the station) and the alert rule engine are not implemented yet — see
  `IMPLEMENTATION_PLAN.md`'s Milestone 6 scope and ADR-025.

## Verifying without Home Assistant

With `mosquitto_sub` (or any MQTT client) pointed at your broker, confirm the
station is actually publishing before troubleshooting on the HA side:

```sh
# Availability (retained -- you'll see it immediately even with no new events)
mosquitto_sub -h <broker host> -t 'openobservatory/+/status/availability' -v

# Everything the station publishes for its own station_id
mosquitto_sub -h <broker host> -t 'openobservatory/<station-id>/#' -v

# Discovery configs Home Assistant would read
mosquitto_sub -h <broker host> -t 'homeassistant/+/+/+/config' -v
```

Find `<station-id>` from `GET /api/v1/station` (`id` field) or from the topic
names you see subscribing to the wildcard `openobservatory/+/status/availability`
above.

Expected first few seconds after enabling and restarting:

1. `openobservatory/<id>/status/availability` → `online` (retained).
2. Six `homeassistant/.../config` messages (retained) — one per entity in the
   table above.
3. `openobservatory/<id>/status/health` and `.../status/capture` (retained)
   within `OO_MQTT_HEALTH_PUBLISH_INTERVAL_S` (default 15s).
4. `openobservatory/<id>/detection` (not retained) the next time a live
   detection happens.

If you see nothing at all: check `GET /api/v1/health`'s `mqtt` block
(`connected`, `last_error`, `connect_attempts`) — the station never stops
capturing or detecting because the broker is unreachable, it just keeps
retrying with backoff and reports why in that block and in
`oo_mqtt_connected` / `oo_mqtt_dropped_total` on `/metrics`.

## Example automations

**Notify on any tawny owl**, using the `event` entity's attributes rather
than a per-species entity (there isn't one, deliberately — see ADR-025):

```yaml
automation:
  - alias: "Tawny owl heard"
    trigger:
      - platform: event
        event_type: mqtt_event_received  # or use the event entity's own trigger in the UI
    condition:
      - condition: template
        value_template: >
          {{ trigger.event.data.event_type == 'bird_detection' and
             trigger.event.data.display_name == 'Tawny Owl' }}
    action:
      - service: notify.mobile_app
        data:
          message: "Tawny owl heard in the garden"
```

(In the HA UI, simplest is: Settings → Automations → New → Trigger type
"Event" → pick `event.<slug>_detection`, then add a condition on
`trigger.event.data.display_name`.)

**Turn on a porch light briefly on bat activity:**

```yaml
automation:
  - alias: "Bats active"
    trigger:
      - platform: state
        entity_id: binary_sensor.garden_observatory_bat_activity
        to: "on"
    action:
      - service: light.turn_on
        target:
          entity_id: light.porch
      - delay: "00:05:00"
      - service: light.turn_off
        target:
          entity_id: light.porch
```

**Alert if the station goes unhealthy for more than 10 minutes** (catches
both a dead process, via availability/LWT, and a degraded-but-alive station,
via the health sensor):

```yaml
automation:
  - alias: "Observatory unhealthy"
    trigger:
      - platform: state
        entity_id: binary_sensor.garden_observatory_station_healthy
        to: "off"
        for: "00:10:00"
    action:
      - service: notify.mobile_app
        data:
          message: >
            Observatory station degraded:
            {{ state_attr('binary_sensor.garden_observatory_station_healthy', 'problems') }}
```

## Rollback

Set `OO_MQTT_ENABLED=false` and restart. The station stops connecting and
publishing; nothing else about capture, detection, or the local API changes.
Home Assistant will mark the device unavailable (LWT never fires on a clean
`mqtt_enabled=false` restart since the station never connects to set one up
in the first place — the previous retained `online` state on the broker will
simply go stale; delete the retained messages on the broker with
`mosquitto_pub -r -n -t 'openobservatory/<id>/status/availability'` if you
want Home Assistant to drop the device's entities immediately rather than
leaving them showing last-known state).

## Not yet implemented

Environmental telemetry ingestion (a temperature/humidity feed *into* the
station over MQTT), the alert rule engine with repetition and cooldown, and
outgoing HMAC-signed webhooks are all still scoped-but-not-built — see
`docs/delivery/IMPLEMENTATION_PLAN.md`'s Milestone 6 entry and
`docs/api/API_AND_INTEGRATIONS.md`.
