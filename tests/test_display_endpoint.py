"""`/api/v1/display` end to end, through the real app and a real pipeline.

The unit tests in `test_display_channel.py` prove the wire format. These prove
the socket: that a display gets a usable screen on connect, that the station's
honesty rules survive the transport, and that the bytes on the wire are what the
budget in ADR-038 claims.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from open_observatory import display_channel
from open_observatory.api.app import create_app


@pytest.fixture
def display_client(settings):
    configured = settings.model_copy(
        update={
            "source": "synthetic",
            "synthetic_scene": "mixed",
            "synthetic_sample_rate": 192000,
            "clip_plugins": ("activity-v1", "ultrasonic-pass-v1"),
            "clip_min_score": 0.0,
            "native_ring_seconds": 20,
            "audible_ring_seconds": 20,
            # Fast enough that a test can watch two beats without sleeping long.
            "display_channel_heartbeat_s": 1.0,
        }
    )
    from open_observatory.config import set_settings

    set_settings(configured)
    app = create_app(configured)
    with TestClient(app) as test_client:
        for _ in range(40):
            if test_client.get("/api/v1/station").json()["capture"]["blocks"] > 12:
                break
            time.sleep(0.25)
        yield test_client


def _receive(socket, wanted: str, tries: int = 40):
    """Next frame of type `wanted`, discarding others. Returns (frame, raw_text)."""
    for _ in range(tries):
        raw = socket.receive_text()
        frame = json.loads(raw)
        if frame["t"] == wanted:
            return frame, raw
    raise AssertionError(f"no {wanted!r} frame arrived")


class TestConnect:
    def test_hello_arrives_first_and_fits_a_single_packet(self, display_client) -> None:
        with display_client.websocket_connect("/api/v1/display") as socket:
            raw = socket.receive_text()
            frame = json.loads(raw)
            assert frame["t"] == "h"
            assert frame["v"] == display_channel.WIRE_VERSION
            assert frame["hb"] == 1
            assert frame["now"] > 1_600_000_000
            assert isinstance(frame["f"], list)
            assert len(raw.encode()) < display_channel.MAX_FRAME_BYTES, len(raw)

    def test_a_synthetic_source_is_reported_degraded_and_named(self, display_client) -> None:
        # ADR-020's incident, on the channel that matters most for it: a counter-top
        # display is the "browsing view" that must never present a test scene as
        # an observation of the garden.
        with display_client.websocket_connect("/api/v1/display") as socket:
            frame, _ = _receive(socket, "h")
            assert frame["st"] == "D"
            assert frame["d"] == "NO MICROPHONE - SYNTHETIC SOURCE"

    def test_no_detections_are_pushed_while_the_source_is_not_the_microphone(
        self, display_client
    ) -> None:
        with display_client.websocket_connect("/api/v1/display") as socket:
            _receive(socket, "h")
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                frame = json.loads(socket.receive_text())
                assert frame["t"] != "d", frame

    def test_the_heartbeat_keeps_the_feed_provably_alive(self, display_client) -> None:
        with display_client.websocket_connect("/api/v1/display") as socket:
            _receive(socket, "h")
            first, raw = _receive(socket, "s")
            second, _ = _receive(socket, "s")
            assert second["now"] >= first["now"]
            assert first["st"] in ("L", "D")
            # A heartbeat that cost real bytes would defeat the point of leaving
            # the poll behind.
            assert len(raw.encode()) < 120, raw

    def test_the_station_snapshot_reports_the_channel(self, display_client) -> None:
        with display_client.websocket_connect("/api/v1/display") as socket:
            _receive(socket, "h")
            snapshot = display_client.get("/api/v1/station").json()["display_channel"]
            assert snapshot["clients"] == 1
            stats = snapshot["per_client"][0]
            assert stats["sent"] >= 1
            assert stats["dropped"] == 0

    def test_a_disconnected_display_is_forgotten(self, display_client) -> None:
        with display_client.websocket_connect("/api/v1/display") as socket:
            _receive(socket, "h")
        for _ in range(20):
            if display_client.get("/api/v1/station").json()["display_channel"]["clients"] == 0:
                break
            time.sleep(0.1)
        assert display_client.get("/api/v1/station").json()["display_channel"]["clients"] == 0


class TestDegradedForSomethingElse:
    """Issue #30. A station can be degraded for a reason that has nothing to do
    with the microphone, and the feed must keep flowing while it says so.

    What happened: disk crossed the 85% watermark, `health_state` returned "D"
    with the watermark's own wording, and the pump -- gating on that letter --
    dropped every detection for six hours. The heartbeat kept arriving carrying
    the same "D", so the display's own staleness clock never fired and the glass
    showed six-hour-old rows under a disk banner with nothing to say it had
    stopped listening. A power cycle repopulated it instantly, because the
    connect snapshot reads the database and has never consulted health at all.

    The source really is synthetic in this fixture, so `detections_are_observations`
    is the one function pretended at -- the same narrowing `TestLiveDeltas` makes,
    and for the same reason.
    """

    @pytest.fixture(autouse=True)
    def _degraded_but_listening(self, monkeypatch):
        monkeypatch.setattr(
            display_channel,
            "health_state",
            lambda _health: ("D", "disk usage 86% exceeds the 85% watermark"),
        )
        monkeypatch.setattr(display_channel, "detections_are_observations", lambda _health: True)

    def test_detections_still_reach_a_degraded_station_s_display(self, display_client) -> None:
        with display_client.websocket_connect("/api/v1/display") as socket:
            _receive(socket, "h")
            frame, _ = _receive(socket, "d", tries=200)
            assert frame["at"] > 1_600_000_000

    def test_the_banner_still_says_what_is_wrong(self, display_client) -> None:
        with display_client.websocket_connect("/api/v1/display") as socket:
            frame, _ = _receive(socket, "h")
            assert frame["st"] == "D"
            assert frame["d"] == "disk usage 86% exceeds the 85% watermark"


class TestSuppressionIsCounted:
    """A detection dropped for ADR-020 must leave a trace.

    The six hours in issue #30 were invisible everywhere, not just on the glass:
    the pump dropped each detection with no counter and no log line, so nothing
    on the station could be asked what had happened either. The fixture's source
    is genuinely synthetic, so suppression here is the correct behaviour and the
    count is the evidence of it.
    """

    def test_suppressed_detections_are_reported_on_the_station_snapshot(
        self, display_client
    ) -> None:
        with display_client.websocket_connect("/api/v1/display") as socket:
            _receive(socket, "h")
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                socket.receive_text()  # heartbeats; the deltas are being suppressed
                stats = display_client.get("/api/v1/station").json()["display_channel"]
                if stats["per_client"] and stats["per_client"][0]["suppressed"] > 0:
                    return
            raise AssertionError("nothing recorded that detections were being dropped")


class TestLiveDeltas:
    """With the station on the microphone, detections must actually flow.

    `detections_are_observations` is patched rather than the hardware faked: the
    source really is synthetic here, and pretending otherwise anywhere else in
    the app would be exactly the dishonesty ADR-020 forbids. This narrows the
    pretence to the one function whose output the pump's gate consults -- which
    is the source predicate, not the state letter, since issue #30.
    """

    @pytest.fixture(autouse=True)
    def _on_the_microphone(self, monkeypatch):
        monkeypatch.setattr(display_channel, "detections_are_observations", lambda _health: True)

    def test_a_bat_pass_arrives_as_a_delta_with_no_name_and_no_score(
        self, display_client
    ) -> None:
        with display_client.websocket_connect("/api/v1/display") as socket:
            _receive(socket, "h")
            frame, raw = _receive(socket, "d", tries=200)
            # ultrasonic-pass-v1 is the only detector in this fixture that can
            # produce a wire item: activity-v1 is non-taxonomic and BirdNET has
            # no model here.
            assert frame["b"] == 1
            assert "n" not in frame
            assert "score" not in raw
            assert frame["at"] > 1_600_000_000

    def test_a_delta_is_a_few_dozen_bytes(self, display_client) -> None:
        with display_client.websocket_connect("/api/v1/display") as socket:
            _receive(socket, "h")
            _, raw = _receive(socket, "d", tries=200)
            assert len(raw.encode()) < 100, raw

    def test_bats_can_be_turned_off_server_side(self, display_client) -> None:
        with display_client.websocket_connect("/api/v1/display?bats=false") as socket:
            _receive(socket, "h")
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                frame = json.loads(socket.receive_text())
                assert frame["t"] != "d", frame

    def test_an_impossible_threshold_silences_named_detections_but_not_passes(
        self, display_client
    ) -> None:
        with display_client.websocket_connect("/api/v1/display?min_score=1.0") as socket:
            _receive(socket, "h")
            frame, _ = _receive(socket, "d", tries=200)
            assert frame["b"] == 1  # a pass is never scored, so it still arrives
