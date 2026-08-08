"""Home Assistant MQTT Discovery payloads.

Pure functions: given settings and a station identity, return the discovery
topic/payload pairs to publish. No I/O, no aiomqtt import, so this is trivially
unit-testable and the wire format can be checked against Home Assistant's own
documented shape without a broker.

Format verified against the current Home Assistant MQTT integration docs
(``home-assistant.io/integrations/mqtt``, fetched via context7 while writing
this module): a discovery topic is
``<discovery_prefix>/<component>/[<node_id>/]<object_id>/config``; the payload
carries a ``device`` block so every entity groups under one HA device, and an
``origin`` block identifying this project as the thing that published it.
Abbreviated keys (``dev``, ``avty_t``, ...) exist to save bytes on constrained
links; this station is on a LAN, so full key names are used throughout for
readability in ``mosquitto_sub -v`` output and in this file.

HONESTY CONSTRAINT (see CLAUDE.md, HANDOVER.md): BirdNET's score is not a
calibrated probability, so no entity here uses ``device_class: probability``
and no attribute is named "confidence" or "probability". Bat passes are
passes, not species identifications: no bat-related entity ever carries a
species name. The discovery config for bat entities below has no field that
could carry one at all (state_topic is a bare count or ON/OFF); the actual
per-message payloads (built in ``publisher.py: _handle_detection``) go
further and defensively strip any species-shaped field that somehow arrived
on a bat detection, because the wire format is a worse place to discover a
normaliser regression than a test — see ``tests/test_mqtt_publisher.py``'s
``TestHonestyConstraintsOnTheWire``.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from ..config import Settings

#: taxonomic_group values the ultrasonic pass detector can emit (normaliser.py);
#: kept separate from NON_TAXONOMIC_GROUPS there because "bat" *is* meaningful
#: taxonomy, just not species-level taxonomy this detector is allowed to claim.
BAT_GROUP = "bat"
BAT_PLUGIN_ID = "ultrasonic-pass-v1"


def _software_version() -> str:
    try:
        return version("open-observatory")
    except PackageNotFoundError:
        return "0.0.0+dev"


def _slug(name: str) -> str:
    """Turn a free-text station name into an HA-safe object_id fragment."""
    chars = [c.lower() if c.isalnum() else "_" for c in name.strip()]
    slug = "".join(chars)
    while "__" in slug:
        slug = slug.replace("__", "_")
    slug = slug.strip("_")
    return slug or "station"


@dataclass(frozen=True, slots=True)
class DiscoveryEntity:
    """One `<config topic>, <retained JSON payload>` pair to publish."""

    component: str
    object_id: str
    topic: str
    payload: dict[str, Any]


class TopicLayout:
    """Every topic the publisher writes to, derived once from settings + station_id."""

    def __init__(self, settings: Settings, station_id: str) -> None:
        self.settings = settings
        self.station_id = station_id
        self.slug = _slug(settings.station_name)
        self.base = f"{settings.mqtt_topic_prefix}/{station_id}"

    @property
    def availability(self) -> str:
        return f"{self.base}/status/availability"

    @property
    def capture(self) -> str:
        return f"{self.base}/status/capture"

    @property
    def health(self) -> str:
        return f"{self.base}/status/health"

    @property
    def detection(self) -> str:
        return f"{self.base}/detection"

    @property
    def detection_event(self) -> str:
        return f"{self.base}/events/detection"

    @property
    def last_detection(self) -> str:
        return f"{self.base}/metrics/last_detection"

    @property
    def species_today(self) -> str:
        return f"{self.base}/metrics/species_today"

    @property
    def bat_passes_tonight(self) -> str:
        return f"{self.base}/metrics/bat_passes_tonight"

    @property
    def bat_activity(self) -> str:
        return f"{self.base}/metrics/bat_activity"

    def discovery_topic(self, component: str, object_id: str) -> str:
        node_id = self.station_id.replace("-", "")
        return f"{self.settings.mqtt_discovery_prefix}/{component}/{node_id}/{object_id}/config"


def device_block(topics: TopicLayout) -> dict[str, Any]:
    return {
        "identifiers": [f"openobservatory-{topics.station_id}"],
        "name": topics.settings.station_name,
        "manufacturer": "Open Observatory",
        "model": "Open Observatory station (AudioMoth + Raspberry Pi 5)",
        "sw_version": _software_version(),
    }


def origin_block() -> dict[str, Any]:
    return {"name": "Open Observatory", "sw_version": _software_version()}


def _base_payload(topics: TopicLayout, *, name: str, unique_suffix: str) -> dict[str, Any]:
    return {
        "name": name,
        "unique_id": f"openobservatory_{topics.station_id}_{unique_suffix}",
        "device": device_block(topics),
        "origin": origin_block(),
        "availability_topic": topics.availability,
        "payload_available": "online",
        "payload_not_available": "offline",
    }


def build_entities(settings: Settings, station_id: str) -> list[DiscoveryEntity]:
    """The full discovery entity set for one station.

    Deliberately does NOT include a per-species entity: the operator's own
    design note (originally in API_AND_INTEGRATIONS.md) says one permanent
    entity per detected species becomes unwieldy, and an open-world species
    list cannot be declared as static HA discovery config anyway. Per-species
    notification instead goes through the `event` entity below, using
    attributes an automation can match on rather than one entity per species.
    """
    topics = TopicLayout(settings, station_id)
    entities: list[DiscoveryEntity] = []

    last_detection = _base_payload(topics, name="Last detection", unique_suffix="last_detection")
    last_detection.update(
        {
            "state_topic": topics.last_detection,
            "value_template": "{{ value_json.display_name }}",
            "json_attributes_topic": topics.last_detection,
            "icon": "mdi:paw",
        }
    )
    entities.append(
        DiscoveryEntity(
            "sensor",
            f"{topics.slug}_last_detection",
            topics.discovery_topic("sensor", "last_detection"),
            last_detection,
        )
    )

    species_today = _base_payload(topics, name="Species today", unique_suffix="species_today")
    species_today.update(
        {
            "state_topic": topics.species_today,
            "unit_of_measurement": "species",
            "state_class": "measurement",
            "icon": "mdi:bird",
        }
    )
    entities.append(
        DiscoveryEntity(
            "sensor",
            f"{topics.slug}_species_today",
            topics.discovery_topic("sensor", "species_today"),
            species_today,
        )
    )

    bat_passes = _base_payload(topics, name="Bat passes tonight", unique_suffix="bat_passes_tonight")
    bat_passes.update(
        {
            "state_topic": topics.bat_passes_tonight,
            "unit_of_measurement": "passes",
            "state_class": "total_increasing",
            "icon": "mdi:bat",
        }
    )
    entities.append(
        DiscoveryEntity(
            "sensor",
            f"{topics.slug}_bat_passes_tonight",
            topics.discovery_topic("sensor", "bat_passes_tonight"),
            bat_passes,
        )
    )

    bat_activity = _base_payload(topics, name="Bat activity", unique_suffix="bat_activity")
    bat_activity.update(
        {
            "state_topic": topics.bat_activity,
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:bat",
        }
    )
    entities.append(
        DiscoveryEntity(
            "binary_sensor",
            f"{topics.slug}_bat_activity",
            topics.discovery_topic("binary_sensor", "bat_activity"),
            bat_activity,
        )
    )

    station_healthy = _base_payload(topics, name="Station healthy", unique_suffix="station_healthy")
    station_healthy.update(
        {
            "state_topic": topics.health,
            "value_template": "{{ 'ON' if value_json.status == 'ok' else 'OFF' }}",
            "json_attributes_topic": topics.health,
            "icon": "mdi:radio-tower",
        }
    )
    entities.append(
        DiscoveryEntity(
            "binary_sensor",
            f"{topics.slug}_station_healthy",
            topics.discovery_topic("binary_sensor", "station_healthy"),
            station_healthy,
        )
    )

    detection_event = _base_payload(topics, name="Detection", unique_suffix="detection_event")
    detection_event.update(
        {
            "state_topic": topics.detection_event,
            # Coarse categories, not species: HA's `event` platform requires a
            # static event_types list, and the species set is open-world. The
            # species/score/detector ride as JSON attributes instead, which an
            # automation trigger condition can match on (see
            # docs/operations/HOME_ASSISTANT.md for a worked "tawny owl" example).
            "event_types": ["bird_detection", "bat_pass_detection", "other_detection"],
            "icon": "mdi:ear-hearing",
        }
    )
    entities.append(
        DiscoveryEntity(
            "event",
            f"{topics.slug}_detection",
            topics.discovery_topic("event", "detection"),
            detection_event,
        )
    )

    return entities
