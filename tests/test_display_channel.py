"""The inside observer's push channel: wire format, filtering, and back-pressure.

Every assertion here is about something an ESP32 with 240 kB of usable heap and a
2.8" panel actually needs. The size assertions are the point of the feature (ADR-038):
the polled transport this replaces cost ~127 kB per 20 s cycle to render six rows.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from open_observatory.display_channel import (
    MAX_FRAME_BYTES,
    WIRE_VERSION,
    DisplayClient,
    DisplayFilter,
    SpeciesToday,
    detection_frame,
    encode,
    health_state,
    hello_frame,
    is_taxonomic_name,
    status_frame,
    wire_item,
)


def bird(**overrides):
    payload = {
        "event_start_utc": "2026-08-08T13:46:39.755370Z",
        "common_name": "Common Woodpigeon",
        "scientific_name": "Columba palumbus",
        "taxonomic_group": "bird",
        "score": 0.91,
        "peak_frequency_hz": 480.0,
        "detector": {"plugin_id": "birdnet-analyzer"},
    }
    payload.update(overrides)
    return payload


def bat(**overrides):
    payload = {
        "event_start_utc": "2026-08-08T22:04:11Z",
        "common_name": None,
        "scientific_name": None,
        "taxonomic_group": "bat",
        "score": 0.31,
        "peak_frequency_hz": 36240.0,
        "detector": {"plugin_id": "ultrasonic-pass-v1"},
    }
    payload.update(overrides)
    return payload


class TestWireItem:
    def test_a_bird_becomes_a_name_and_an_epoch_and_nothing_else(self):
        item = wire_item(bird(), DisplayFilter())
        assert item == {"n": "Common Woodpigeon", "at": 1786196799}

    def test_no_score_ever_reaches_the_wire(self):
        # ADR-023: the threshold decides what appears; the number never leaves
        # the station. A field the display cannot render is a field it can leak.
        assert "score" not in json.dumps(wire_item(bird(), DisplayFilter()))
        assert "s" not in wire_item(bird(), DisplayFilter())
        assert "s" not in wire_item(bat(), DisplayFilter())

    def test_a_bird_below_the_threshold_is_never_sent(self):
        assert wire_item(bird(score=0.4), DisplayFilter(min_score=0.75)) is None

    def test_the_threshold_boundary_is_inclusive(self):
        assert wire_item(bird(score=0.75), DisplayFilter(min_score=0.75)) is not None

    def test_a_non_taxonomic_birdnet_class_is_not_a_species(self):
        engine = bird(common_name="Engine", scientific_name="Engine", score=0.99)
        assert wire_item(engine, DisplayFilter()) is None

    def test_an_unattributed_acoustic_event_is_not_sent(self):
        activity = bird(
            common_name=None,
            scientific_name=None,
            taxonomic_group="unknown",
            detector={"plugin_id": "activity-v1"},
        )
        assert wire_item(activity, DisplayFilter()) is None

    def test_a_bat_pass_carries_no_name_and_no_score(self):
        item = wire_item(bat(), DisplayFilter())
        # `b` marks it a pass; the display supplies the words "Bat pass" itself,
        # so the station cannot accidentally ship a species name for one.
        assert item == {"b": 1, "at": 1786226651, "k": 36.2}

    def test_a_bat_pass_is_sent_however_low_its_score(self):
        # ADR-013: ultrasonic-pass-v1 claims a pass, not a species. Its score is
        # a pulse-train confidence about *whether*, not *what*, so filtering on
        # it would hide real observations on the strength of an irrelevant number.
        assert wire_item(bat(score=0.01), DisplayFilter(min_score=0.9)) is not None

    def test_a_bat_pass_is_dropped_when_the_operator_turned_bats_off(self):
        assert wire_item(bat(), DisplayFilter(show_bats=False)) is None

    def test_a_bat_is_recognised_by_its_plugin_even_if_the_group_is_missing(self):
        odd = bat(taxonomic_group="")
        assert wire_item(odd, DisplayFilter()) is not None

    def test_a_bat_without_a_usable_frequency_omits_the_measurement(self):
        item = wire_item(bat(peak_frequency_hz=None), DisplayFilter())
        assert item == {"b": 1, "at": 1786226651}

    def test_an_undateable_detection_is_dropped(self):
        assert wire_item(bird(event_start_utc=None), DisplayFilter()) is None
        assert wire_item(bird(event_start_utc="not a time"), DisplayFilter()) is None

    def test_a_datetime_start_is_accepted_as_well_as_an_iso_string(self):
        item = wire_item(
            bird(event_start_utc=datetime(2026, 8, 8, 13, 46, 39, tzinfo=UTC)),
            DisplayFilter(),
        )
        assert item is not None and item["at"] == 1786196799


class TestTaxonomicName:
    @pytest.mark.parametrize(
        ("scientific", "common", "expected"),
        [
            ("Columba palumbus", "Common Woodpigeon", True),
            ("Engine", "Engine", False),
            ("Human vocal", "Human vocal", False),
            ("", "Common Woodpigeon", False),
            ("Columba palumbus", "", False),
            (None, None, False),
            ("Columba", "Woodpigeon", False),  # no space: not a binomial
        ],
    )
    def test_matches_the_firmware_rule(self, scientific, common, expected):
        assert is_taxonomic_name(scientific, common) is expected


class TestFrameSizes:
    """The operator's headline requirement: one detection, one packet, minimally.

    A single Ethernet MTU is 1500 bytes; ~1400 is the safe payload budget once
    IP/TCP/WebSocket framing is paid for. These are hard assertions, not aspirations.
    """

    def test_a_detection_frame_is_a_couple_of_hundred_bytes_at_most(self):
        frame = encode(detection_frame(wire_item(bird(), DisplayFilter())))
        assert len(frame) < 200, frame
        assert len(frame) < MAX_FRAME_BYTES

    def test_a_bat_frame_is_smaller_still(self):
        frame = encode(detection_frame(wire_item(bat(), DisplayFilter())))
        assert len(frame) < 100, frame

    def test_a_pathological_species_name_still_fits_a_single_packet(self):
        # The longest common names BirdNET emits run to ~50 characters
        # ("Chestnut-backed Chickadee (Northwestern)"); allow far worse.
        monster = bird(common_name="X" * 200)
        frame = encode(detection_frame(wire_item(monster, DisplayFilter())))
        assert len(frame) < MAX_FRAME_BYTES

    def test_the_whole_connect_snapshot_fits_a_single_packet(self):
        items = [wire_item(bird(), DisplayFilter()) for _ in range(6)]
        frame = encode(
            hello_frame(
                now=1786470400,
                state="L",
                detail="",
                species_today=14,
                items=items,
                heartbeat_s=10,
            )
        )
        assert len(frame) < MAX_FRAME_BYTES, len(frame)

    def test_encoding_carries_no_whitespace(self):
        # json.dumps' default separators add ~2 bytes per field, which on this
        # channel is a double-digit percentage of the frame.
        assert " " not in encode({"t": "d", "n": "A", "at": 1})


class TestFrames:
    def test_hello_announces_the_wire_version_and_the_heartbeat_period(self):
        frame = hello_frame(
            now=1786470400, state="L", detail="", species_today=3, items=[], heartbeat_s=10
        )
        assert frame["t"] == "h"
        assert frame["v"] == WIRE_VERSION
        assert frame["hb"] == 10
        assert frame["now"] == 1786470400
        assert frame["sp"] == 3
        assert frame["f"] == []

    def test_a_healthy_hello_omits_the_detail_string(self):
        frame = hello_frame(
            now=1, state="L", detail="", species_today=0, items=[], heartbeat_s=10
        )
        assert "d" not in frame

    def test_a_degraded_hello_carries_the_stations_own_words(self):
        frame = hello_frame(
            now=1,
            state="D",
            detail="NO MICROPHONE - SYNTHETIC SOURCE",
            species_today=0,
            items=[],
            heartbeat_s=10,
        )
        assert frame["d"] == "NO MICROPHONE - SYNTHETIC SOURCE"

    def test_a_detection_frame_omits_the_species_count_when_it_did_not_move(self):
        item = wire_item(bird(), DisplayFilter())
        assert "sp" not in detection_frame(item)
        assert detection_frame(item, species_today=15)["sp"] == 15

    def test_status_carries_the_clock_so_a_display_can_resync(self):
        frame = status_frame(now=1786470400, state="L", detail="", species_today=9)
        assert frame == {"t": "s", "now": 1786470400, "st": "L", "sp": 9}


class TestHealthState:
    """Mirrors the firmware's parseHealth so the banner says the same thing on
    both transports. The HTTP fallback still does this mapping on the device."""

    def test_a_capturing_live_station_is_listening_with_no_banner(self):
        assert health_state(
            {
                "status": "ok",
                "problems": [],
                "capture": {"state": "capturing", "source_kind": "alsa", "is_live_hardware": True},
            }
        ) == ("L", "")

    def test_a_synthetic_source_is_named_as_such(self):
        assert health_state(
            {
                "status": "degraded",
                "problems": ["capturing from a synthetic/replay source, not the microphone"],
                "capture": {
                    "state": "capturing",
                    "source_kind": "synthetic",
                    "is_live_hardware": False,
                },
            }
        ) == ("D", "NO MICROPHONE - SYNTHETIC SOURCE")

    def test_a_replay_source_is_degraded_but_differently_worded(self):
        state, detail = health_state(
            {
                "status": "degraded",
                "problems": [],
                "capture": {
                    "state": "capturing",
                    "source_kind": "replay",
                    "is_live_hardware": False,
                },
            }
        )
        assert state == "D"
        assert detail == "NOT LISTENING TO THE MICROPHONE"

    def test_a_reported_problem_is_surfaced_verbatim(self):
        state, detail = health_state(
            {
                "status": "degraded",
                "problems": ["detector birdnet-analyzer: error"],
                "capture": {"state": "capturing", "source_kind": "alsa", "is_live_hardware": True},
            }
        )
        assert state == "D"
        assert detail == "detector birdnet-analyzer: error"

    def test_capture_not_running_is_degraded(self):
        state, detail = health_state(
            {
                "status": "ok",
                "problems": [],
                "capture": {"state": "stopped", "source_kind": "alsa", "is_live_hardware": True},
            }
        )
        assert state == "D"
        assert detail == "CAPTURE stopped"


class TestSpeciesToday:
    def test_counts_distinct_scientific_names(self):
        tracker = SpeciesToday(day_key="2026-08-08", names={"Columba palumbus"})
        assert tracker.count == 1
        assert tracker.observe("2026-08-08", bird()) is False  # already counted
        assert tracker.count == 1  # same species again: not a new one
        assert tracker.observe("2026-08-08", bird(scientific_name="Turdus merula")) is True
        assert tracker.count == 2

    def test_a_bat_pass_is_not_a_species(self):
        tracker = SpeciesToday(day_key="2026-08-08", names=set())
        tracker.observe("2026-08-08", bat())
        assert tracker.count == 0

    def test_the_set_resets_at_local_midnight(self):
        tracker = SpeciesToday(day_key="2026-08-08", names={"Columba palumbus"})
        tracker.observe("2026-08-09", bird(scientific_name="Turdus merula"))
        assert tracker.day_key == "2026-08-09"
        assert tracker.count == 1

    def test_an_unknown_count_is_never_reported_as_zero(self):
        # -1 is the display's "not known yet", which is not the same fact as a
        # genuinely silent day.
        tracker = SpeciesToday(day_key="2026-08-08", names=None)
        assert tracker.count == -1


class TestDisplayClient:
    """Bounded, with an explicit drop policy, like every other queue here.
    Capture always wins: this must never apply back-pressure to anything."""

    def test_offer_never_blocks_and_never_raises(self):
        client = DisplayClient(socket=None, maxsize=4)
        for _ in range(1000):
            client.offer({"t": "d", "n": "x", "at": 1})
        assert client.queue_depth <= 4

    def test_a_slow_display_loses_the_oldest_detection_not_the_newest(self):
        client = DisplayClient(socket=None, maxsize=3)
        for index in range(5):
            client.offer({"t": "d", "n": f"bird-{index}", "at": index})
        names = [frame["n"] for frame in client.pending()]
        assert names == ["bird-2", "bird-3", "bird-4"]
        assert client.dropped == 2

    def test_status_frames_outlive_detections_under_pressure(self):
        # The banner is what makes a broken station look broken. Losing it to a
        # burst of woodpigeons is the one drop this channel must not make.
        client = DisplayClient(socket=None, maxsize=3)
        client.offer({"t": "s", "st": "D", "d": "NO MICROPHONE - SYNTHETIC SOURCE"})
        for index in range(6):
            client.offer({"t": "d", "n": f"bird-{index}", "at": index})
        kinds = [frame["t"] for frame in client.pending()]
        assert "s" in kinds

    def test_a_closed_client_accepts_nothing(self):
        client = DisplayClient(socket=None, maxsize=4)
        client.close()
        client.offer({"t": "d"})
        assert client.queue_depth == 0

    def test_stats_report_what_was_sent_and_what_was_shed(self):
        client = DisplayClient(socket=None, maxsize=2)
        for index in range(5):
            client.offer({"t": "d", "at": index})
        stats = client.stats()
        assert stats["dropped"] == 3
        assert stats["queued"] == 2
        assert stats["sent"] == 0
