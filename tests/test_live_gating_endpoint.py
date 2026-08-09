"""Gating through the real app, and the display's independence from it (ADR-040).

`test_live_gating.py` proves the station's arithmetic. These prove the wiring:
that connecting a browser is what actually starts the encoders, that a viewer
who arrives during an idle period is told the canvas is filling rather than
shown a stale one, and -- the operator's first-class requirement -- that a
browser arriving and leaving does not disturb the wall display.

`TestClient` runs the socket over ASGI in-process, so these are not a
measurement of the network path: ADR-012's constraint that live-channel changes
be re-measured from a real browser over real Wi-Fi still binds, and was
separately satisfied on the station. What they *can* prove is behaviour that
does not depend on the transport, which is all of the above.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from open_observatory.api.app import create_app
from open_observatory.audio.spectrogram import FRAME_SPECTROGRAM


@pytest.fixture
def gated_client(settings):
    configured = settings.model_copy(
        update={
            "source": "synthetic",
            "synthetic_scene": "mixed",
            "synthetic_sample_rate": 192000,
            "native_ring_seconds": 20,
            "audible_ring_seconds": 20,
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


def _columns_emitted(client: TestClient) -> int:
    specs = client.get("/api/v1/station").json()["spectrograms"]
    return sum(spec["columns_emitted"] for spec in specs)


class TestTheBrowserIsWhatStartsTheEncoders:
    def test_an_unwatched_station_emits_no_columns(self, gated_client) -> None:
        before = _columns_emitted(gated_client)
        time.sleep(1.0)
        assert _columns_emitted(gated_client) == before

    def test_connecting_starts_them_and_disconnecting_stops_them(self, gated_client) -> None:
        with gated_client.websocket_connect("/api/v1/live") as socket:
            hello = json.loads(socket.receive_text())
            assert hello["type"] == "hello"
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and _columns_emitted(gated_client) == 0:
                time.sleep(0.1)
            watched = _columns_emitted(gated_client)
            assert watched > 0, "a connected viewer must start the encoders"

        # The socket is closed; the next block or two closes the gate.
        time.sleep(1.0)
        settled = _columns_emitted(gated_client)
        time.sleep(1.0)
        assert _columns_emitted(gated_client) == settled

    def test_the_hello_says_the_canvas_is_deliberately_empty(self, gated_client) -> None:
        """The exact confusion the original snapshot-on-connect avoided.

        A blank canvas that the client cannot distinguish from a dead pipeline is
        the failure `LiveHub`'s docstring names. Gating is allowed to produce a
        blank canvas; it is not allowed to produce an unexplained one.
        """
        with gated_client.websocket_connect("/api/v1/live") as socket:
            hello = json.loads(socket.receive_text())
            specs = hello["spectrograms"]
            assert specs, "hello must describe the channels"
            assert all(spec["viewer_gated"] is True for spec in specs)
            assert all(spec["history_seconds"] == 0.0 for spec in specs)

    def test_a_second_viewer_still_gets_the_backfill(self, gated_client) -> None:
        """Gating must not cost the snapshot when there is one to give."""
        with gated_client.websocket_connect("/api/v1/live") as first:
            json.loads(first.receive_text())
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and _columns_emitted(gated_client) == 0:
                time.sleep(0.1)

            with gated_client.websocket_connect("/api/v1/live") as second:
                hello = json.loads(second.receive_text())
                assert any(spec["history_seconds"] > 0.0 for spec in hello["spectrograms"])
                # ...and the backfill frame itself, ahead of any live column.
                payload = second.receive_bytes()
                assert payload[0] == FRAME_SPECTROGRAM


class TestTheDisplayIsNotCollateralDamage:
    """The wall display is the first-class surface; a browser must not cost it.

    Both channels live in one process on one event loop, which is exactly why
    this needs asserting rather than assuming.
    """

    def test_a_browser_connecting_does_not_drop_display_frames(self, gated_client) -> None:
        with gated_client.websocket_connect("/api/v1/display") as display:
            first = json.loads(display.receive_text())
            assert first["t"] == "h"

            def display_stats():
                channel = gated_client.get("/api/v1/station").json()["display_channel"]
                assert channel["clients"] == 1
                return channel["per_client"][0]

            before = display_stats()
            with gated_client.websocket_connect("/api/v1/live") as browser:
                json.loads(browser.receive_text())
                # A heartbeat must still arrive, on time, with a browser attached.
                for _ in range(6):
                    frame = json.loads(display.receive_text())
                    if frame["t"] == "s":
                        break
                else:
                    raise AssertionError("no heartbeat reached the display")
            after = display_stats()

            assert after["dropped"] == before["dropped"] == 0
            assert after["sent"] > before["sent"]

    def test_the_display_alone_does_not_start_the_encoders(self, gated_client) -> None:
        """A display is not a viewer: it has no canvas and no use for columns."""
        with gated_client.websocket_connect("/api/v1/display") as display:
            assert json.loads(display.receive_text())["t"] == "h"
            before = _columns_emitted(gated_client)
            time.sleep(1.0)
            assert _columns_emitted(gated_client) == before
