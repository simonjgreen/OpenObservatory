"""One end-to-end test against a real, locally-run mosquitto broker.

Everything else in tests/test_mqtt_publisher.py uses a fake client and has no
network dependency, per the milestone brief. This file is the "verify against
a locally-run mosquitto (install one in a container...)" half of that same
instruction: it spins up `eclipse-mosquitto:2` via Docker, points a real
MqttPublisher (real aiomqtt.Client, no fakes) at it, and checks messages
arrive over an actual TCP MQTT connection. It never talks to the operator's
Home Assistant broker -- no address or credentials for that exist anywhere in
this repository, deliberately (see docs/operations/HOME_ASSISTANT.md).

Skipped automatically if Docker is unavailable, so the rest of the suite is
unaffected in an environment without it.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
import uuid

import aiomqtt
import pytest

from open_observatory.config import Settings
from open_observatory.events import EventBus, EventType
from open_observatory.mqtt.publisher import MqttPublisher

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")

STATION_ID = "33333333-3333-3333-3333-333333333333"
CONTAINER = "oo-test-mosquitto-pytest"
PORT = 18831


@pytest.fixture(scope="module")
def mosquitto() -> None:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
    started = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            CONTAINER,
            "-p",
            f"{PORT}:1883",
            "eclipse-mosquitto:2",
            "mosquitto",
            "-c",
            "/mosquitto-no-auth.conf",
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start mosquitto container: {started.stderr}")
    try:
        deadline = time.monotonic() + 10
        last_error = None
        while time.monotonic() < deadline:
            try:
                probe_cmd = [
                    "docker", "exec", CONTAINER,
                    "mosquitto_pub", "-h", "localhost", "-t", "probe", "-m", "x",
                ]
                subprocess.run(probe_cmd, capture_output=True, check=True, timeout=2)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                time.sleep(0.3)
        if last_error is not None:
            pytest.skip(f"mosquitto did not become ready: {last_error}")
        yield None
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)


def make_settings() -> Settings:
    return Settings(
        mqtt_enabled=True,
        mqtt_host="127.0.0.1",
        mqtt_port=PORT,
        mqtt_reconnect_min_s=0.05,
        mqtt_reconnect_max_s=0.5,
        mqtt_health_publish_interval_s=3600.0,
        station_name="Garden Observatory",
    )


async def test_real_broker_receives_availability_discovery_and_detection(mosquitto: None) -> None:
    settings = make_settings()
    bus = EventBus()
    publisher = MqttPublisher(
        settings,
        bus,
        station_id_provider=lambda: STATION_ID,
        health_provider=lambda: {"status": "ok", "problems": []},
        capture_status_provider=lambda: {"is_live_hardware": True, "state": "capturing"},
    )

    received: list[aiomqtt.Message] = []

    async def subscriber() -> None:
        async with aiomqtt.Client(hostname="127.0.0.1", port=PORT) as client:
            await client.subscribe(f"openobservatory/{STATION_ID}/#")
            await client.subscribe("homeassistant/#")
            async for message in client.messages:
                received.append(message)
                if len(received) >= 8:
                    return

    sub_task = asyncio.create_task(subscriber())
    await publisher.start()
    try:
        await asyncio.sleep(0.5)  # let the publisher connect and publish discovery + availability

        bus.emit(
            EventType.DETECTION_CREATED,
            {
                "id": str(uuid.uuid4()),
                "detector": {
                    "plugin_id": "birdnet-v2.4",
                    "plugin_version": "1.0.0",
                    "model_id": "test",
                    "model_version": "1",
                    "model_sha256": None,
                },
                "stream_id": str(uuid.uuid4()),
                "window_id": str(uuid.uuid4()),
                "event_start_utc": "2026-08-08T22:00:00Z",
                "event_end_utc": "2026-08-08T22:00:01Z",
                "source_start_frame": 0,
                "source_end_frame": 1,
                "display_name": "Tawny Owl",
                "common_name": "Tawny Owl",
                "scientific_name": "Strix aluco",
                "rank": "species",
                "taxonomic_group": "bird",
                "score": 0.5,
                "calibrated_probability": None,
                "native_result": {},
                "media": [],
            },
            station_id=uuid.uuid4(),
        )

        await asyncio.wait_for(sub_task, timeout=10.0)
    finally:
        await publisher.stop()
        if not sub_task.done():
            sub_task.cancel()

    topics = {m.topic.value for m in received}
    assert f"openobservatory/{STATION_ID}/status/availability" in topics
    assert any(t.startswith("homeassistant/") for t in topics)
    assert f"openobservatory/{STATION_ID}/detection" in topics

    availability_topic = f"openobservatory/{STATION_ID}/status/availability"
    availability = next(m for m in received if m.topic.value == availability_topic)
    assert availability.payload == b"online"
    # Whether the *delivery* to an already-subscribed client echoes the RETAIN
    # bit is a subtle, broker/protocol-version-dependent wire nuance (see the
    # MQTT 3.1.1/5 spec on "new subscription" vs normal forwarding) that isn't
    # what this test is checking. That the publish call itself was made with
    # retain=True is asserted directly in tests/test_mqtt_publisher.py against
    # the fake client, which records the argument unambiguously.

    detection = next(m for m in received if m.topic.value == f"openobservatory/{STATION_ID}/detection")
    body = json.loads(detection.payload)
    assert body["data"]["common_name"] == "Tawny Owl"
    assert body["data"]["taxonomic_group"] == "bird"
