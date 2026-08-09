"""Over-the-air firmware for the counter-top display: the station's half.

ADR-050. The device is not on a cable while this is written, so nothing here has
been observed on real hardware. What these tests *can* do is pin the two things
the station is solely responsible for: refusing to publish something that would
not boot, and never offering an update to a display that should not have one.

The display's own rules -- version ordering, the digest check, the "not while
someone is looking at it" gate and the rollback deadline -- are host-tested in
`firmware/inside-observer/test/test_ota/`, and `compare_versions` is deliberately
implemented twice, once in each language, with the same cases asserted on both
sides. A station that accepts a version the display refuses to parse is a
rollout that silently never lands.
"""

from __future__ import annotations

import json
import struct
import time

import pytest
from fastapi.testclient import TestClient

from open_observatory import firmware_store
from open_observatory.api.app import create_app
from open_observatory.config import set_settings
from open_observatory.display_channel import DisplayClient, encode, update_frame
from open_observatory.firmware_store import (
    APP_SLOT_BYTES,
    FirmwareError,
    FirmwareStore,
    compare_versions,
    is_plausible_version,
    should_offer,
    validate_image,
)


def esp32_image(size: int = 200_000) -> bytes:
    """A byte string shaped like an ESP32 application image.

    Not a real firmware -- it would not run -- but it carries the three things
    `validate_image` looks at: the 0xE9 magic, chip id 0, and an
    `esp_app_desc_t` magic word at 0x20. Those offsets were read off the real
    `.pio/build/cyd/firmware.bin`, not from documentation.
    """
    image = bytearray(b"\x00" * size)
    image[0] = firmware_store.ESP_IMAGE_MAGIC
    image[firmware_store.CHIP_ID_OFFSET] = firmware_store.CHIP_ID_ESP32
    struct.pack_into("<I", image, firmware_store.APP_DESC_OFFSET, firmware_store.APP_DESC_MAGIC)
    return bytes(image)


class TestVersionOrdering:
    """The same cases the firmware asserts in test_ota.cpp. Two implementations,
    one contract; if they disagree, a rollout silently does nothing."""

    def test_versions_order_numerically_not_lexically(self) -> None:
        assert compare_versions("0.10.0", "0.9.0") > 0
        assert compare_versions("0.9.0", "0.10.0") < 0
        assert compare_versions("1.0.0", "0.99.99") > 0
        assert compare_versions("2.0.0", "10.0.0") < 0

    def test_missing_components_read_as_zero(self) -> None:
        assert compare_versions("0.2", "0.2.0") == 0
        assert compare_versions("1", "1.0.0.0") == 0
        assert compare_versions("0.2.1", "0.2") > 0

    @pytest.mark.parametrize(
        "value", ["0.2.0-rc1", "v0.2.0", "", "0..2", ".2", "2.", "1.2.3.4.5", "100000.0.0"]
    )
    def test_a_version_neither_side_can_order_is_refused(self, value: str) -> None:
        assert not is_plausible_version(value)

    @pytest.mark.parametrize("value", ["0.2.0", "12", "0.2", "1.2.3.4"])
    def test_the_versions_this_project_actually_uses_are_accepted(self, value: str) -> None:
        assert is_plausible_version(value)

    def test_an_unorderable_version_compares_equal_so_it_is_never_newer(self) -> None:
        assert compare_versions("0.3.0-rc1", "0.2.0") == 0
        assert compare_versions("0.2.0", "banana") == 0

    def test_a_non_ascii_digit_is_not_a_digit(self) -> None:
        # `str.isdigit()` is true for '٣' and '²'. int() would then either
        # succeed with a surprising value or raise, and either way the two
        # implementations would disagree, since the C++ side only ever accepts
        # '0'..'9'.
        assert not is_plausible_version("٣.0.0")
        assert not is_plausible_version("².0")


class TestWhatMayBePublished:
    def test_a_plausible_esp32_image_is_accepted(self) -> None:
        validate_image(esp32_image())

    def test_an_empty_or_tiny_file_is_refused(self) -> None:
        with pytest.raises(FirmwareError, match="too small"):
            validate_image(b"")

    def test_something_that_is_not_an_esp32_image_is_refused(self) -> None:
        # The realistic mistake: uploading firmware.elf, which starts with
        # \x7fELF and would download and verify perfectly before not booting.
        elf = bytearray(esp32_image())
        elf[0:4] = b"\x7fELF"
        with pytest.raises(FirmwareError, match="0xE9"):
            validate_image(bytes(elf))

    def test_an_image_for_a_different_chip_is_refused(self) -> None:
        other = bytearray(esp32_image())
        other[firmware_store.CHIP_ID_OFFSET] = 9  # ESP32-S3
        with pytest.raises(FirmwareError, match="chip id"):
            validate_image(bytes(other))

    def test_an_image_without_an_application_descriptor_is_refused(self) -> None:
        # A whole-flash backup starts at the bootloader, so 0x20 is not an
        # app descriptor. It is also 4 MB, but the size check would not catch
        # a truncated one.
        blob = bytearray(esp32_image())
        struct.pack_into("<I", blob, firmware_store.APP_DESC_OFFSET, 0)
        with pytest.raises(FirmwareError, match="descriptor"):
            validate_image(bytes(blob))

    def test_an_image_too_large_for_an_app_slot_is_refused(self) -> None:
        with pytest.raises(FirmwareError, match="app slot"):
            validate_image(esp32_image(APP_SLOT_BYTES + 1))

    def test_the_slot_size_matches_the_partition_table(self) -> None:
        # firmware/inside-observer/partitions/inside-observer.csv. If these
        # drift, an image the station accepts is one the display cannot write.
        assert APP_SLOT_BYTES == 0x1F0000 == 2_031_616


class TestTheStore:
    def test_publish_then_read_back(self, tmp_path) -> None:
        store = FirmwareStore(tmp_path / "firmware")
        payload = esp32_image()
        release = store.publish(payload, version="0.2.1", notes="adds OTA")

        assert release.version == "0.2.1"
        assert release.size_bytes == len(payload)
        assert release.sha256 == firmware_store.digest(payload)
        assert store.current() == release
        assert store.image_path.read_bytes() == payload

    def test_publishing_replaces_rather_than_accumulates(self, tmp_path) -> None:
        store = FirmwareStore(tmp_path / "firmware")
        store.publish(esp32_image(200_000), version="0.2.1")
        store.publish(esp32_image(210_000), version="0.2.2")

        current = store.current()
        assert current is not None
        assert current.version == "0.2.2"
        assert current.size_bytes == 210_000
        assert store.image_path.stat().st_size == 210_000

    def test_a_version_the_display_cannot_order_is_refused_at_publish(self, tmp_path) -> None:
        store = FirmwareStore(tmp_path / "firmware")
        with pytest.raises(FirmwareError, match="order against"):
            store.publish(esp32_image(), version="0.2.1-rc1")
        assert store.current() is None

    def test_nothing_published_reads_as_nothing(self, tmp_path) -> None:
        assert FirmwareStore(tmp_path / "firmware").current() is None

    def test_a_manifest_without_its_image_offers_nothing(self, tmp_path) -> None:
        # The failure this guards: a manifest advertising a digest for bytes
        # that are not there, and the first display to connect being offered an
        # image it cannot fetch. Publishing writes the image first for exactly
        # this reason; this asserts the read side agrees.
        store = FirmwareStore(tmp_path / "firmware")
        store.publish(esp32_image(), version="0.2.1")
        store.image_path.unlink()
        assert store.current() is None

    def test_a_truncated_image_offers_nothing(self, tmp_path) -> None:
        store = FirmwareStore(tmp_path / "firmware")
        store.publish(esp32_image(), version="0.2.1")
        store.image_path.write_bytes(b"short")
        assert store.current() is None

    def test_a_corrupt_manifest_is_not_an_exception(self, tmp_path) -> None:
        # A station whose firmware directory has been half-deleted must keep
        # capturing. A firmware offer is the least important thing it does.
        store = FirmwareStore(tmp_path / "firmware")
        store.publish(esp32_image(), version="0.2.1")
        store.manifest_path.write_text("{not json", encoding="utf-8")
        assert store.current() is None

    def test_withdraw(self, tmp_path) -> None:
        store = FirmwareStore(tmp_path / "firmware")
        store.publish(esp32_image(), version="0.2.1")
        assert store.withdraw() is True
        assert store.current() is None
        assert store.withdraw() is False


class TestWhoGetsOffered:
    def test_only_a_display_behind_the_published_version(self, tmp_path) -> None:
        store = FirmwareStore(tmp_path / "firmware")
        release = store.publish(esp32_image(), version="0.2.1")

        assert should_offer(release, "0.2.0") is True
        assert should_offer(release, "0.1.9") is True
        assert should_offer(release, "0.2.1") is False
        assert should_offer(release, "0.3.0") is False

    def test_a_display_that_did_not_say_is_not_offered_anything(self, tmp_path) -> None:
        # A build older than ADR-050 has no update path and would ignore the
        # frame. Sending it costs bytes and achieves nothing.
        store = FirmwareStore(tmp_path / "firmware")
        release = store.publish(esp32_image(), version="0.2.1")
        assert should_offer(release, None) is False
        assert should_offer(release, "") is False

    def test_nothing_published_offers_nothing(self) -> None:
        assert should_offer(None, "0.1.0") is False

    def test_a_version_string_that_cannot_be_ordered_is_never_offered_to(
        self, tmp_path
    ) -> None:
        store = FirmwareStore(tmp_path / "firmware")
        release = store.publish(esp32_image(), version="0.2.1")
        assert should_offer(release, "0.2.0-dev") is False


class TestTheUpdateFrame:
    def test_shape_and_size(self) -> None:
        frame = update_frame(
            version="0.2.1",
            sha256="9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
            size_bytes=1_127_649,
            path="/api/v1/firmware/image",
        )
        assert frame["t"] == "u"
        assert frame["fv"] == "0.2.1"
        assert frame["p"].startswith("/")
        raw = encode(frame)
        # Well inside one MTU, like every other frame on this channel, and sent
        # at most twice in a display's lifetime.
        assert len(raw.encode()) < 200
        assert '"score"' not in raw

    def test_it_never_carries_a_host(self) -> None:
        # The firmware refuses a `p` that does not start with "/" -- this is the
        # station-side half of that agreement.
        frame = update_frame(version="0.2.1", sha256="a" * 64, size_bytes=1, path="/x")
        assert "://" not in json.dumps(frame)

    def test_a_queued_offer_is_shed_after_detections_not_before(self) -> None:
        # DisplayClient sheds the oldest *detection* first. An update frame is
        # not a detection, so a burst of woodpigeons must not be able to lose
        # it -- the same reasoning that protects the status frame.
        client = DisplayClient(socket=None, maxsize=3)
        client.offer(update_frame(version="0.2.1", sha256="a" * 64, size_bytes=1, path="/x"))
        for _ in range(5):
            client.offer({"t": "d", "n": "Common Woodpigeon", "at": 1})
        assert any(frame["t"] == "u" for frame in client.pending())


# ---------------------------------------------------------------------------
# Through the real app
# ---------------------------------------------------------------------------


@pytest.fixture
def client(settings):
    configured = settings.model_copy(
        update={"display_channel_heartbeat_s": 1.0, "birdnet_enabled": False}
    )
    set_settings(configured)
    app = create_app(configured)
    with TestClient(app) as test_client:
        yield test_client


class TestTheApi:
    def test_nothing_is_published_on_a_fresh_station(self, client) -> None:
        body = client.get("/api/v1/firmware").json()
        assert body["published"] is None
        assert body["image_path"] is None
        assert body["app_slot_bytes"] == APP_SLOT_BYTES

    def test_publish_serve_and_withdraw(self, client) -> None:
        payload = esp32_image()
        response = client.post(
            "/api/v1/firmware?version=0.2.1&notes=adds%20OTA", content=payload
        )
        assert response.status_code == 200
        published = response.json()["published"]
        assert published["version"] == "0.2.1"
        assert published["sha256"] == firmware_store.digest(payload)

        image = client.get("/api/v1/firmware/image")
        assert image.status_code == 200
        assert image.content == payload
        # The display compares this against the size in the offer and refuses a
        # mismatch, so it has to be exact.
        assert int(image.headers["content-length"]) == len(payload)

        assert client.delete("/api/v1/firmware").json()["published"] is None
        assert client.get("/api/v1/firmware/image").status_code == 404

    def test_a_bad_image_is_refused_by_name_and_nothing_is_stored(self, client) -> None:
        response = client.post("/api/v1/firmware?version=0.2.1", content=b"\x7fELF" + b"\x00" * 100_000)
        assert response.status_code == 422
        assert "0xE9" in response.json()["detail"]["errors"]["image"]
        assert client.get("/api/v1/firmware").json()["published"] is None

    def test_a_bad_version_is_refused(self, client) -> None:
        response = client.post("/api/v1/firmware?version=0.2.1-rc1", content=esp32_image())
        assert response.status_code == 422
        assert client.get("/api/v1/firmware").json()["published"] is None

    def test_a_rollout_with_nothing_published_is_a_conflict_not_a_crash(self, client) -> None:
        assert client.post("/api/v1/firmware/rollout").status_code == 409

    def test_a_display_that_is_behind_is_offered_the_image_on_connect(self, client) -> None:
        client.post("/api/v1/firmware?version=0.9.9", content=esp32_image())
        with client.websocket_connect("/api/v1/display?fw=0.2.0") as socket:
            frames = [json.loads(socket.receive_text()) for _ in range(2)]
        assert frames[0]["t"] == "h", "the screen is populated before anything asks it to go blank"
        offer = frames[1]
        assert offer["t"] == "u"
        assert offer["fv"] == "0.9.9"
        assert offer["p"] == "/api/v1/firmware/image"
        assert len(offer["sha"]) == 64

    def test_a_display_already_on_the_published_version_is_offered_nothing(
        self, client
    ) -> None:
        client.post("/api/v1/firmware?version=0.9.9", content=esp32_image())
        with client.websocket_connect("/api/v1/display?fw=0.9.9") as socket:
            assert json.loads(socket.receive_text())["t"] == "h"
            # The next frame is a heartbeat, not an offer. Nothing is sent at
            # all in the ordinary case.
            assert json.loads(socket.receive_text())["t"] == "s"

    def test_a_display_that_reports_no_version_is_offered_nothing(self, client) -> None:
        client.post("/api/v1/firmware?version=0.9.9", content=esp32_image())
        with client.websocket_connect("/api/v1/display") as socket:
            assert json.loads(socket.receive_text())["t"] == "h"
            assert json.loads(socket.receive_text())["t"] == "s"

    def test_the_connect_offer_can_be_switched_off(self, settings) -> None:
        configured = settings.model_copy(
            update={
                "display_channel_heartbeat_s": 1.0,
                "birdnet_enabled": False,
                "display_ota_offer_on_connect": False,
            }
        )
        set_settings(configured)
        with TestClient(create_app(configured)) as test_client:
            test_client.post("/api/v1/firmware?version=0.9.9", content=esp32_image())
            with test_client.websocket_connect("/api/v1/display?fw=0.2.0") as socket:
                assert json.loads(socket.receive_text())["t"] == "h"
                assert json.loads(socket.receive_text())["t"] == "s"

    def test_a_rollout_reaches_a_display_that_is_already_connected(self, client) -> None:
        with client.websocket_connect("/api/v1/display?fw=0.2.0") as socket:
            assert json.loads(socket.receive_text())["t"] == "h"
            client.post("/api/v1/firmware?version=0.9.9", content=esp32_image())
            result = client.post("/api/v1/firmware/rollout").json()
            assert result["offered"] == 1
            assert result["connected"] == 1
            for _ in range(10):
                frame = json.loads(socket.receive_text())
                if frame["t"] == "u":
                    assert frame["fv"] == "0.9.9"
                    break
            else:
                raise AssertionError("the rollout never reached the display")

    def test_the_station_snapshot_reports_which_build_each_display_runs(
        self, client
    ) -> None:
        with client.websocket_connect("/api/v1/display?fw=0.2.0"):
            time.sleep(0.1)
            per_client = client.get("/api/v1/station").json()["display_channel"][
                "per_client"
            ]
            assert per_client[0]["firmware_version"] == "0.2.0"

    def test_up_to_date_is_three_valued_not_two(self, client) -> None:
        client.post("/api/v1/firmware?version=0.9.9", content=esp32_image())
        with client.websocket_connect("/api/v1/display"):
            time.sleep(0.1)
            displays = client.get("/api/v1/firmware").json()["displays"]
            # A display that predates ADR-050 is unknown, not out of date.
            assert displays[0]["up_to_date"] is None
