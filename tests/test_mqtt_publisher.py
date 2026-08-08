"""MqttPublisher: connection lifecycle, backoff, bounded queue, honesty rules.

No network dependency: every test here uses FakeClient/FakeBroker, an in-memory
stand-in for aiomqtt.Client injected via MqttPublisher's client_factory. See
tests/test_mqtt_integration.py for the one test that talks to a real,
locally-run mosquitto broker.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import aiomqtt

from open_observatory.config import Settings
from open_observatory.events import EventBus, EventType
from open_observatory.mqtt.publisher import MqttPublisher

STATION_ID = "22222222-2222-2222-2222-222222222222"


class FakeClient:
    """Stands in for aiomqtt.Client as an async context manager."""

    def __init__(self, broker: FakeBroker, *, fail_connect: bool) -> None:
        self.broker = broker
        self._fail_connect = fail_connect

    async def __aenter__(self) -> FakeClient:
        if self._fail_connect:
            raise aiomqtt.MqttError("simulated connect failure")
        self.broker.connections += 1
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    async def publish(
        self, topic: str, payload: Any = None, qos: int = 0, retain: bool = False, **kwargs: Any
    ) -> None:
        if self.broker.fail_publish:
            raise aiomqtt.MqttError("simulated publish failure")
        self.broker.messages.append(SimpleNamespace(topic=topic, payload=payload, qos=qos, retain=retain))


class FakeBroker:
    def __init__(self, *, connect_failures: int = 0) -> None:
        self.messages: list[SimpleNamespace] = []
        self.connections = 0
        self.connect_failures_remaining = connect_failures
        self.fail_publish = False

    def client_factory(self) -> FakeClient:
        fail = self.connect_failures_remaining > 0
        if fail:
            self.connect_failures_remaining -= 1
        return FakeClient(self, fail_connect=fail)

    def by_topic(self, topic: str) -> list[SimpleNamespace]:
        return [m for m in self.messages if m.topic == topic]

    def json_by_topic(self, topic: str) -> list[dict]:
        return [json.loads(m.payload) for m in self.by_topic(topic)]


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = dict(
        mqtt_enabled=True,
        mqtt_reconnect_min_s=0.01,
        mqtt_reconnect_max_s=0.05,
        mqtt_health_publish_interval_s=3600.0,  # quiet unless a test wants it
        mqtt_queue_depth=8,
        station_name="Garden Observatory",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def make_publisher(
    settings: Settings, bus: EventBus, broker: FakeBroker, *, is_live: bool = True
) -> MqttPublisher:
    return MqttPublisher(
        settings,
        bus,
        station_id_provider=lambda: STATION_ID,
        health_provider=lambda: {"status": "ok", "problems": []},
        capture_status_provider=lambda: {"is_live_hardware": is_live, "state": "capturing"},
        client_factory=broker.client_factory,
    )


def detection_event(
    bus: EventBus,
    *,
    plugin_id: str = "birdnet-v2.4",
    taxonomic_group: str = "bird",
    display_name: str = "Tawny Owl",
    common_name: str | None = "Tawny Owl",
    scientific_name: str | None = "Strix aluco",
    score: float = 0.62,
    title_hint: str | None = None,
) -> dict:
    return bus.emit(
        EventType.DETECTION_CREATED,
        {
            "id": str(uuid.uuid4()),
            "detector": {
                "plugin_id": plugin_id,
                "plugin_version": "1.0.0",
                "model_id": "test",
                "model_version": "1",
                "model_sha256": None,
            },
            "stream_id": str(uuid.uuid4()),
            "window_id": str(uuid.uuid4()),
            "event_start_utc": "2026-08-08T22:00:00Z",
            "event_end_utc": "2026-08-08T22:00:01Z",
            "duration_s": 1.0,
            "source_start_frame": 0,
            "source_end_frame": 384000,
            "label": display_name,
            "display_name": display_name,
            "title_hint": title_hint,
            "common_name": common_name,
            "scientific_name": scientific_name,
            "canonical_taxon_id": None,
            "rank": "species" if common_name else None,
            "taxonomic_group": taxonomic_group,
            "score": score,
            "calibrated_probability": None,
            "peak_frequency_hz": None,
            "native_result": {},
            "media": [],
        },
        station_id=uuid.uuid4(),
    )


async def _settle() -> None:
    """Let queued coroutines/tasks progress without a real sleep."""
    for _ in range(20):
        await asyncio.sleep(0)


class TestDisabledIsANoop:
    async def test_start_does_not_connect(self) -> None:
        settings = make_settings(mqtt_enabled=False)
        bus = EventBus()
        broker = FakeBroker()
        publisher = make_publisher(settings, bus, broker)
        await publisher.start()
        await _settle()
        assert broker.connections == 0
        snap = publisher.snapshot()
        assert snap["enabled"] is False
        assert snap["connected"] is False
        await publisher.stop()


class TestConnectAndDiscovery:
    async def test_publishes_availability_online_and_discovery_retained(self) -> None:
        settings = make_settings()
        bus = EventBus()
        broker = FakeBroker()
        publisher = make_publisher(settings, bus, broker)
        await publisher.start()
        await asyncio.sleep(0.05)

        avail = broker.by_topic(f"openobservatory/{STATION_ID}/status/availability")
        assert avail and avail[0].payload == b"online" and avail[0].retain is True

        discovery = [m for m in broker.messages if m.topic.startswith("homeassistant/")]
        assert len(discovery) == 6
        assert all(m.retain for m in discovery)

        await publisher.stop()

    async def test_snapshot_reports_connected(self) -> None:
        settings = make_settings()
        bus = EventBus()
        broker = FakeBroker()
        publisher = make_publisher(settings, bus, broker)
        await publisher.start()
        await asyncio.sleep(0.05)
        assert publisher.snapshot()["connected"] is True
        await publisher.stop()
        assert publisher.snapshot()["connected"] is False


class TestReconnectBackoff:
    async def test_retries_and_eventually_connects(self) -> None:
        settings = make_settings(mqtt_reconnect_min_s=0.01, mqtt_reconnect_max_s=0.02)
        bus = EventBus()
        broker = FakeBroker(connect_failures=3)
        publisher = make_publisher(settings, bus, broker)
        await publisher.start()

        async def wait_for_success() -> None:
            while publisher.snapshot()["connect_successes"] == 0:  # noqa: ASYNC110
                await asyncio.sleep(0.01)  # bounded by outer asyncio.wait_for below

        await asyncio.wait_for(wait_for_success(), timeout=2.0)
        snap = publisher.snapshot()
        assert snap["connect_attempts"] >= 4
        assert snap["connect_successes"] == 1
        assert snap["last_error"] is None or "simulated connect failure" in "".join(
            [snap["last_error"] or ""]
        )
        await publisher.stop()

    async def test_capture_is_unaffected_by_a_permanently_absent_broker(self) -> None:
        """Degradation contract: the publisher task must never crash the process
        or raise out of start(); it just keeps retrying forever in the background."""
        settings = make_settings(mqtt_reconnect_min_s=0.01, mqtt_reconnect_max_s=0.02)
        bus = EventBus()
        broker = FakeBroker(connect_failures=10_000)
        publisher = make_publisher(settings, bus, broker)
        await publisher.start()  # must not raise
        await asyncio.sleep(0.1)
        snap = publisher.snapshot()
        assert snap["connected"] is False
        assert snap["connect_attempts"] > 0
        assert snap["last_error"] is not None
        assert publisher._task is not None and not publisher._task.done()
        await publisher.stop()


class TestBoundedQueueDropPolicy:
    async def test_events_drop_and_count_when_nothing_is_consuming(self) -> None:
        settings = make_settings(mqtt_queue_depth=3, mqtt_reconnect_min_s=10.0, mqtt_reconnect_max_s=10.0)
        bus = EventBus()
        # Broker that never succeeds: the subscription queue fills up because
        # nothing ever drains it via _consume_events.
        broker = FakeBroker(connect_failures=10_000)
        publisher = make_publisher(settings, bus, broker)
        await publisher.start()
        await _settle()

        for _ in range(10):
            detection_event(bus)

        snap = publisher.snapshot()
        assert snap["queued"] <= 3
        assert snap["dropped_total"] >= 7  # 10 offered - 3 that fit
        await publisher.stop()


class TestSyntheticSuppression:
    async def test_detection_from_non_live_capture_is_withheld(self) -> None:
        settings = make_settings()
        bus = EventBus()
        broker = FakeBroker()
        publisher = make_publisher(settings, bus, broker, is_live=False)
        await publisher.start()
        await asyncio.sleep(0.05)

        detection_event(bus)
        await asyncio.sleep(0.05)

        assert broker.by_topic(f"openobservatory/{STATION_ID}/detection") == []
        assert publisher.snapshot()["suppressed_synthetic_total"] == 1
        await publisher.stop()


class TestHonestyConstraintsOnTheWire:
    async def test_bat_pass_has_no_species_or_score(self) -> None:
        settings = make_settings()
        bus = EventBus()
        broker = FakeBroker()
        publisher = make_publisher(settings, bus, broker)
        await publisher.start()
        await asyncio.sleep(0.05)

        # Even if a future normaliser regression let a species field through,
        # the publisher must strip it -- this is the "defensive" half of the
        # honesty test; test_mqtt_discovery.py covers the discovery-config half.
        detection_event(
            bus,
            plugin_id="ultrasonic-pass-v1",
            taxonomic_group="bat",
            display_name="45 kHz pass",
            common_name=None,
            scientific_name="Pipistrellus pipistrellus",  # should never survive onto the wire
            score=12.5,
            title_hint="common pipistrelle?",
        )
        await asyncio.sleep(0.05)

        last = broker.json_by_topic(f"openobservatory/{STATION_ID}/metrics/last_detection")
        assert last, "expected a last_detection publish"
        payload = last[-1]
        assert payload["display_name"] == "Bat pass"
        assert "score" not in payload
        assert "scientific_name" not in payload
        assert "title_hint" not in payload

        event_payload = broker.json_by_topic(f"openobservatory/{STATION_ID}/events/detection")[-1]
        assert event_payload["event_type"] == "bat_pass_detection"
        assert "scientific_name" not in event_payload
        assert "score" not in event_payload

        await publisher.stop()

    async def test_bird_detection_carries_raw_score_not_probability(self) -> None:
        settings = make_settings()
        bus = EventBus()
        broker = FakeBroker()
        publisher = make_publisher(settings, bus, broker)
        await publisher.start()
        await asyncio.sleep(0.05)

        detection_event(bus, score=0.41)
        await asyncio.sleep(0.05)

        payload = broker.json_by_topic(f"openobservatory/{STATION_ID}/metrics/last_detection")[-1]
        assert payload["score"] == 0.41
        assert "probability" not in payload
        assert "confidence" not in payload
        await publisher.stop()


class TestBatPassesAndSpeciesCounters:
    async def test_species_today_counts_distinct_species(self) -> None:
        settings = make_settings()
        bus = EventBus()
        broker = FakeBroker()
        publisher = make_publisher(settings, bus, broker)
        await publisher.start()
        await asyncio.sleep(0.05)

        detection_event(bus, common_name="Tawny Owl", scientific_name="Strix aluco")
        await asyncio.sleep(0.02)
        detection_event(bus, common_name="Tawny Owl", scientific_name="Strix aluco")  # duplicate
        await asyncio.sleep(0.02)
        detection_event(bus, common_name="Robin", scientific_name="Erithacus rubecula")
        await asyncio.sleep(0.05)

        values = broker.by_topic(f"openobservatory/{STATION_ID}/metrics/species_today")
        assert values[-1].payload == b"2"
        await publisher.stop()

    async def test_bat_passes_tonight_increments(self) -> None:
        settings = make_settings()
        bus = EventBus()
        broker = FakeBroker()
        publisher = make_publisher(settings, bus, broker)
        await publisher.start()
        await asyncio.sleep(0.05)

        for _ in range(3):
            detection_event(bus, plugin_id="ultrasonic-pass-v1", taxonomic_group="bat")
            await asyncio.sleep(0.02)

        values = broker.by_topic(f"openobservatory/{STATION_ID}/metrics/bat_passes_tonight")
        assert values[-1].payload == b"3"

        activity = broker.by_topic(f"openobservatory/{STATION_ID}/metrics/bat_activity")
        assert activity[-1].payload == b"ON"
        await publisher.stop()


class TestDayRollover:
    def test_roll_day_counters_resets_species_and_bat_count(self) -> None:
        settings = make_settings()
        bus = EventBus()
        broker = FakeBroker()
        publisher = make_publisher(settings, bus, broker)
        publisher._species_today = {"Robin", "Tawny Owl"}
        publisher._species_day = "2020-01-01"
        publisher._bat_passes_tonight = 7
        publisher._bat_night_key = "2020-01-01"

        publisher._roll_day_counters_if_needed()

        assert publisher._species_today == set()
        assert publisher._bat_passes_tonight == 0
        assert publisher._species_day != "2020-01-01"


class TestBatActivityWindow:
    def test_activity_expires_after_window(self) -> None:
        settings = make_settings(mqtt_bat_activity_window_s=10.0)
        bus = EventBus()
        broker = FakeBroker()
        clock = {"t": 0.0}
        publisher = MqttPublisher(
            settings,
            bus,
            station_id_provider=lambda: STATION_ID,
            health_provider=lambda: {"status": "ok"},
            capture_status_provider=lambda: {"is_live_hardware": True},
            client_factory=broker.client_factory,
            clock=lambda: clock["t"],
        )
        assert publisher._bat_activity_active() is False
        publisher._last_bat_pass_monotonic = 0.0
        clock["t"] = 5.0
        assert publisher._bat_activity_active() is True
        clock["t"] = 11.0
        assert publisher._bat_activity_active() is False


class TestMetrics:
    async def test_render_metrics_reflects_state(self) -> None:
        settings = make_settings()
        bus = EventBus()
        broker = FakeBroker()
        publisher = make_publisher(settings, bus, broker)
        await publisher.start()
        await asyncio.sleep(0.05)

        body, content_type = publisher.render_metrics()
        text = body.decode()
        assert "oo_mqtt_enabled 1.0" in text
        assert "oo_mqtt_connected 1.0" in text
        assert "text/plain" in content_type
        await publisher.stop()

    async def test_health_and_capture_snapshots_are_published_on_capture_events(self) -> None:
        settings = make_settings()
        bus = EventBus()
        broker = FakeBroker()
        publisher = make_publisher(settings, bus, broker)
        await publisher.start()
        await asyncio.sleep(0.05)

        bus.emit(EventType.CAPTURE_STARTED, {"source_kind": "alsa"}, station_id=uuid.uuid4())
        await asyncio.sleep(0.05)

        capture_msgs = broker.json_by_topic(f"openobservatory/{STATION_ID}/status/capture")
        assert capture_msgs
        assert capture_msgs[-1] == {"is_live_hardware": True, "state": "capturing"}
        await publisher.stop()
