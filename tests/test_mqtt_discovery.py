"""Home Assistant MQTT Discovery payload shape.

No broker involved: discovery.py is pure functions over Settings + a station
id. These tests check the wire format matches what Home Assistant's MQTT
integration documents (device block, origin block, availability, discovery
topic structure) and enforces the project's honesty rules end to end.
"""

from __future__ import annotations

from open_observatory.config import Settings
from open_observatory.mqtt.discovery import TopicLayout, _slug, build_entities

STATION_ID = "11111111-1111-1111-1111-111111111111"


def _settings(**overrides: object) -> Settings:
    return Settings(station_name="Garden Observatory", **overrides)  # type: ignore[arg-type]


class TestSlug:
    def test_lowercases_and_underscores(self) -> None:
        assert _slug("Garden Observatory") == "garden_observatory"

    def test_collapses_repeats_and_strips(self) -> None:
        assert _slug("  Back--Garden!! ") == "back_garden"

    def test_empty_falls_back(self) -> None:
        assert _slug("") == "station"


class TestTopicLayout:
    def test_topics_are_prefixed_by_station_id(self) -> None:
        topics = TopicLayout(_settings(mqtt_topic_prefix="openobservatory"), STATION_ID)
        assert topics.base == f"openobservatory/{STATION_ID}"
        assert topics.availability == f"openobservatory/{STATION_ID}/status/availability"
        assert topics.detection == f"openobservatory/{STATION_ID}/detection"

    def test_discovery_topic_structure(self) -> None:
        topics = TopicLayout(_settings(mqtt_discovery_prefix="homeassistant"), STATION_ID)
        topic = topics.discovery_topic("sensor", "last_detection")
        prefix, component, node_id, object_id, leaf = topic.split("/")
        assert prefix == "homeassistant"
        assert component == "sensor"
        assert node_id == STATION_ID.replace("-", "")
        assert object_id == "last_detection"
        assert leaf == "config"


class TestBuildEntities:
    def test_every_entity_shares_one_device(self) -> None:
        entities = build_entities(_settings(), STATION_ID)
        device_ids = {tuple(e.payload["device"]["identifiers"]) for e in entities}
        assert len(device_ids) == 1
        assert device_ids.pop() == (f"openobservatory-{STATION_ID}",)

    def test_every_entity_carries_origin_and_availability(self) -> None:
        for entity in build_entities(_settings(), STATION_ID):
            assert entity.payload["origin"]["name"] == "Open Observatory"
            assert entity.payload["availability_topic"].endswith("/status/availability")
            assert entity.payload["payload_available"] == "online"
            assert entity.payload["payload_not_available"] == "offline"

    def test_unique_ids_are_all_distinct(self) -> None:
        entities = build_entities(_settings(), STATION_ID)
        unique_ids = [e.payload["unique_id"] for e in entities]
        assert len(unique_ids) == len(set(unique_ids))

    def test_expected_entity_set(self) -> None:
        entities = build_entities(_settings(), STATION_ID)
        got = {(e.component, e.object_id) for e in entities}
        assert got == {
            ("sensor", "garden_observatory_last_detection"),
            ("sensor", "garden_observatory_species_today"),
            ("sensor", "garden_observatory_bat_passes_tonight"),
            ("binary_sensor", "garden_observatory_bat_activity"),
            ("binary_sensor", "garden_observatory_station_healthy"),
            ("event", "garden_observatory_detection"),
        }

    def test_no_species_only_entity_created(self) -> None:
        """Design note carried over from API_AND_INTEGRATIONS.md: no
        one-entity-per-species, and the open-world species set cannot be a
        static discovery config anyway."""
        entities = build_entities(_settings(), STATION_ID)
        assert all("species" not in e.object_id or "today" in e.object_id for e in entities)

    def test_discovery_topics_are_all_under_discovery_prefix(self) -> None:
        settings = _settings(mqtt_discovery_prefix="homeassistant")
        for entity in build_entities(settings, STATION_ID):
            assert entity.topic.startswith("homeassistant/")
            assert entity.topic.endswith("/config")


class TestHonestyConstraintsInDiscovery:
    def test_no_entity_declares_probability_device_class(self) -> None:
        for entity in build_entities(_settings(), STATION_ID):
            assert entity.payload.get("device_class") != "probability"

    def test_no_entity_name_or_key_mentions_confidence_or_probability(self) -> None:
        banned = ("confidence", "probability", "% likely")
        for entity in build_entities(_settings(), STATION_ID):
            haystack = " ".join(str(v) for v in entity.payload.values()).lower()
            for word in banned:
                assert word not in haystack, f"{entity.object_id} payload mentions {word!r}"

    def test_bat_entities_have_no_species_value_template(self) -> None:
        entities = {e.object_id: e for e in build_entities(_settings(), STATION_ID)}
        bat_passes = entities["garden_observatory_bat_passes_tonight"]
        bat_activity = entities["garden_observatory_bat_activity"]
        for entity in (bat_passes, bat_activity):
            haystack = " ".join(str(v) for v in entity.payload.values()).lower()
            assert "species" not in haystack
            assert "scientific_name" not in haystack

    def test_detection_event_uses_coarse_event_types_not_species(self) -> None:
        entities = {e.object_id: e for e in build_entities(_settings(), STATION_ID)}
        event_entity = entities["garden_observatory_detection"]
        assert event_entity.payload["event_types"] == [
            "bird_detection",
            "bat_pass_detection",
            "other_detection",
        ]
