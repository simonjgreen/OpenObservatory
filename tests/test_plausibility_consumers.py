"""Every consumer of a withdrawn detection, in one place (ADR-042).

ADR-032 fixed the detector so no *new* implausible identification is ever
written, and shipped `oo detections reconcile-plausibility` to flag the ones
already stored. Nothing read that flag: a Western Screech-Owl already in the
database still reached `GET /api/v1/detections`, Home Assistant over MQTT, and
the ESP32 on the operator's living-room wall as a plain factual claim.

These tests are written against the *old* behaviour on purpose -- every one of
them fails if its consumer stops checking the flag -- and they encode the split
ADR-042 argues for:

* a *record* keeps the row and marks it (the API list, detail and export);
* a *claim* refuses it (species tallies, MQTT, the wall display).

`tests/test_plausibility_repair.py` covers the other half: how the flag gets
written in the first place.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from open_observatory import display_channel, history, plausibility
from open_observatory.api.app import create_app
from open_observatory.config import set_settings
from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope
from open_observatory.display import detection_flags
from open_observatory.events import EventBus, EventType
from open_observatory.mqtt.publisher import MqttPublisher

#: What `plausibility_repair.apply_plausibility_flag` actually writes, copied
#: from a real dry-run finding for the measured Flammulated Owl so these tests
#: cannot pass against a shape the repair CLI does not produce.
WITHDRAWN_NATIVE_RESULT = {
    "detector": "birdnet-v2.4",
    "week": 30,
    "confidence": 0.959,
    "occurrence_probability": 8e-06,
    "plausibility_band": "out_of_range",
    "plausibility_review": {
        "implausible": True,
        "recomputed_band": "implausible",
        "recomputed_occurrence_probability": 8e-06,
        "recomputed_threshold": None,
        "reason": (
            "occurrence 8e-06 is at or below the plausibility floor (0.0005); "
            "no score is admissible"
        ),
        "reviewed_utc": "2026-08-09T10:00:00+00:00",
    },
}

STANDING_NATIVE_RESULT = {"detector": "birdnet-v2.4", "week": 30, "occurrence_probability": 0.019253}

#: A row that *was* reviewed and survived the review. Not withdrawn.
CLEARED_NATIVE_RESULT = {
    "detector": "birdnet-v2.4",
    "plausibility_review": {"implausible": False, "reason": "still admissible", "reviewed_utc": "x"},
}


class TestTheFlagItself:
    """`plausibility.is_withdrawn` is the single definition five surfaces share."""

    def test_a_flagged_row_is_withdrawn(self) -> None:
        assert plausibility.is_withdrawn(WITHDRAWN_NATIVE_RESULT) is True

    def test_an_ordinary_row_is_not(self) -> None:
        assert plausibility.is_withdrawn(STANDING_NATIVE_RESULT) is False

    def test_a_reviewed_but_cleared_row_is_not_withdrawn(self) -> None:
        """The block records that a review happened; the boolean records its verdict.

        Reading the block's presence alone would withdraw every row an operator
        looked at and confirmed -- the exact opposite of what a review is for.
        """
        assert plausibility.is_withdrawn(CLEARED_NATIVE_RESULT) is False

    @pytest.mark.parametrize("value", [None, {}, "not a dict", 7, {"plausibility_review": "yes"}])
    def test_junk_is_not_withdrawn_and_does_not_raise(self, value: object) -> None:
        # `native_result` is a JSON column and old rows carry all of these.
        assert plausibility.is_withdrawn(value) is False

    def test_the_withdrawal_is_returned_verbatim_and_attributable(self) -> None:
        block = plausibility.withdrawal(WITHDRAWN_NATIVE_RESULT)
        assert block is not None
        # Charter item 5: "with what changed it and when".
        assert block["reviewed_utc"] == "2026-08-09T10:00:00+00:00"
        assert "plausibility floor" in str(block["reason"])
        assert block["recomputed_band"] == "implausible"
        assert plausibility.withdrawal(STANDING_NATIVE_RESULT) is None

    def test_derived_flags_carry_it_for_rows_that_ship_no_native_result(self) -> None:
        assert detection_flags(WITHDRAWN_NATIVE_RESULT)["withdrawn"] is True
        assert detection_flags(STANDING_NATIVE_RESULT)["withdrawn"] is False


class TestWallDisplayWire:
    """`display_channel.wire_item`: the ESP32 gets nothing at all (ADR-042)."""

    filt = display_channel.DisplayFilter(min_score=0.75, show_bats=True, rows=6)

    def _rest_row(self, **overrides: object) -> dict:
        row = {
            "event_start_utc": "2026-08-08T22:00:00Z",
            "common_name": "Western Screech-Owl",
            "scientific_name": "Megascops kennicottii",
            "taxonomic_group": "bird",
            "score": 0.96,
        }
        row.update(overrides)
        return row

    def test_a_standing_detection_still_reaches_the_glass(self) -> None:
        assert display_channel.wire_item(self._rest_row(), self.filt) == {
            "n": "Western Screech-Owl",
            "at": 1786226400,
        }

    def test_the_top_level_withdrawn_boolean_suppresses_it(self) -> None:
        assert display_channel.wire_item(self._rest_row(withdrawn=True), self.filt) is None

    def test_the_flags_marker_suppresses_it(self) -> None:
        row = self._rest_row(flags={"feeding_buzz": False, "withdrawn": True})
        assert display_channel.wire_item(row, self.filt) is None

    def test_a_bus_events_raw_native_result_suppresses_it(self) -> None:
        """The live path carries `native_result`, not `withdrawn`.

        The connect snapshot and the live deltas must not be able to disagree
        about one row, so both shapes are understood.
        """
        row = self._rest_row(native_result=WITHDRAWN_NATIVE_RESULT)
        assert display_channel.wire_item(row, self.filt) is None

    def test_a_withdrawn_bat_pass_is_suppressed_too(self) -> None:
        # Bats bypass the score threshold entirely, so the withdrawal check has
        # to run before the bat branch or it would never apply to one.
        row = {
            "event_start_utc": "2026-08-08T22:00:00Z",
            "taxonomic_group": "bat",
            "peak_frequency_hz": 45000.0,
            "withdrawn": True,
        }
        assert display_channel.wire_item(row, self.filt) is None

    def test_the_species_footer_does_not_count_a_withdrawn_row(self) -> None:
        counter = display_channel.SpeciesToday(day_key="2026-08-08", names=set())
        counter.observe("2026-08-08", self._rest_row())
        assert counter.count == 1
        counter.observe(
            "2026-08-08",
            self._rest_row(
                common_name="Flammulated Owl",
                scientific_name="Psiloscops flammeolus",
                withdrawn=True,
            ),
        )
        # The footer has to agree with the feed, which never showed it.
        assert counter.count == 1


class TestMqtt:
    """A Home Assistant entity state is a bare claim with nowhere to put a caveat."""

    def _publisher(self, bus: EventBus, broker) -> MqttPublisher:  # type: ignore[no-untyped-def]
        from test_mqtt_publisher import make_publisher, make_settings

        return make_publisher(make_settings(), bus, broker)

    async def _run(self, native_result: dict) -> tuple[object, MqttPublisher]:
        from test_mqtt_publisher import FakeBroker, _settle, detection_event

        bus = EventBus()
        broker = FakeBroker()
        publisher = self._publisher(bus, broker)
        await publisher.start()
        await _settle()
        event = detection_event(
            bus, common_name="Western Screech-Owl", scientific_name="Megascops kennicottii"
        )
        event["data"]["native_result"] = native_result
        # The event was emitted before we edited it; re-emit the edited payload so
        # the publisher sees exactly what a repaired row would carry.
        bus.emit(EventType.DETECTION_CREATED, event["data"], station_id=uuid.uuid4())
        for _ in range(60):
            await asyncio.sleep(0)
        await publisher.stop()
        return broker, publisher

    async def test_a_withdrawn_detection_is_never_published(self) -> None:
        broker, publisher = await self._run(WITHDRAWN_NATIVE_RESULT)
        names = [
            str(message.payload) for message in broker.messages  # type: ignore[attr-defined]
        ]
        assert not any("Screech" in name for name in names), names
        assert publisher.stats.suppressed_withdrawn_total >= 1

    async def test_an_ordinary_detection_still_is(self) -> None:
        broker, publisher = await self._run(STANDING_NATIVE_RESULT)
        names = [
            str(message.payload) for message in broker.messages  # type: ignore[attr-defined]
        ]
        assert any("Screech" in name for name in names), names
        assert publisher.stats.suppressed_withdrawn_total == 0


# ----------------------------------------------------------------------
# The SQL side: aggregates that name a species


NIGHT = datetime(2026, 8, 4, 21, 0, tzinfo=UTC)


def _seed(settings, *, withdrawn_score: float = 0.96) -> uuid.UUID:
    """One standing species, one withdrawn species, on a live `alsa` stream."""
    init_engine(settings)
    create_all()
    station_id = uuid.uuid4()
    stream_id = uuid.uuid4()
    detector_id = uuid.uuid4()
    withdrawn_id = uuid.uuid4()
    with session_scope() as session:
        session.add(orm.Station(id=station_id, name="test", timezone="Europe/London"))
        session.add(
            orm.Detector(
                id=detector_id,
                plugin_id="birdnet-v2.4",
                plugin_version="1",
                model_id="m",
                model_version="1",
            )
        )
        session.add(
            orm.AudioStream(
                id=stream_id,
                source_kind="alsa",
                start_utc=NIGHT,
                end_utc=NIGHT + timedelta(hours=2),
                start_monotonic_ns=0,
                sample_rate=48000,
                sample_format="S16_LE",
                frame_count=48000 * 2 * 3600,
            )
        )

        def detection(det_id, minutes, name, sci, score, native):  # type: ignore[no-untyped-def]
            at = NIGHT + timedelta(minutes=minutes)
            session.add(
                orm.Detection(
                    id=det_id,
                    station_id=station_id,
                    detector_id=detector_id,
                    stream_id=stream_id,
                    window_id=uuid.uuid4(),
                    event_start_utc=at,
                    event_end_utc=at + timedelta(seconds=3),
                    source_start_frame=0,
                    source_end_frame=1,
                    detector_label=name,
                    common_name=name,
                    scientific_name=sci,
                    rank="species",
                    taxonomic_group="bird",
                    score=score,
                    native_result=native,
                )
            )

        detection(uuid.uuid4(), 1, "Tawny Owl", "Strix aluco", 0.82, STANDING_NATIVE_RESULT)
        detection(
            withdrawn_id,
            2,
            "Western Screech-Owl",
            "Megascops kennicottii",
            withdrawn_score,
            WITHDRAWN_NATIVE_RESULT,
        )
    return withdrawn_id


class TestSpeciesAggregates:
    """A species tally has no row to mark, so it must not contain a retracted one."""

    def test_species_summary_drops_the_withdrawn_species(self, settings) -> None:
        _seed(settings)
        window = history.Range(NIGHT, NIGHT + timedelta(hours=1), "test")
        with session_scope() as session:
            names = {
                row["common_name"] for row in history.species_summary(session, window)
            }
        assert "Tawny Owl" in names
        assert "Western Screech-Owl" not in names

    def test_the_exclusion_is_reported_rather_than_silent(self, settings) -> None:
        _seed(settings)
        window = history.Range(NIGHT, NIGHT + timedelta(hours=1), "test")
        with session_scope() as session:
            assert history.excluded_withdrawn_count(session, window) == 1

    def test_a_diagnostic_caller_can_still_see_it(self, settings) -> None:
        """Hidden by default is not the same as unreachable."""
        _seed(settings)
        window = history.Range(NIGHT, NIGHT + timedelta(hours=1), "test")
        with session_scope() as session:
            names = {
                row["common_name"]
                for row in history.species_summary(session, window, include_withdrawn=True)
            }
        assert "Western Screech-Owl" in names

    def test_the_predicate_keeps_rows_with_no_review_at_all(self, settings) -> None:
        """The NULL-safety that matters: almost every row has no review block.

        A plain `= false` on the extracted JSON value would return nothing at
        all, hiding the entire database rather than one owl.
        """
        _seed(settings)
        window = history.Range(NIGHT, NIGHT + timedelta(hours=1), "test")
        with session_scope() as session:
            rows = history.species_summary(session, window)
        assert rows, "the standing detection must survive the withdrawal filter"


class TestApiSurfaces:
    """Through the real FastAPI app, against a real database."""

    @pytest.fixture
    def client(self, settings):
        configured = settings.model_copy(
            update={"source": "synthetic", "synthetic_scene": "dawn-chorus"}
        )
        set_settings(configured)
        app = create_app(configured)
        with TestClient(app) as test_client:
            for _ in range(60):
                if test_client.get("/api/v1/station").json()["capture"]["blocks"] > 4:
                    break
                time.sleep(0.25)
            self.withdrawn_id = _seed(configured)
            yield test_client

    #: The seeded night, as query parameters. Passed as a dict rather than
    #: interpolated into the path: an ISO timestamp's "+00:00" offset becomes a
    #: space once a query string is decoded, and FastAPI then rejects it.
    WINDOW: ClassVar[dict[str, str]] = {
        "since": NIGHT.isoformat(),
        "until": (NIGHT + timedelta(hours=1)).isoformat(),
    }

    def _rows(self, client) -> list[dict]:  # type: ignore[no-untyped-def]
        payload = client.get(
            "/api/v1/detections", params={**self.WINDOW, "limit": 500}
        ).json()
        return payload["detections"]

    def test_the_record_is_kept_and_marked_not_hidden(self, client) -> None:
        """Charter item 5: withdraw, do not delete; the prior verdict stays visible."""
        rows = {row["common_name"]: row for row in self._rows(client)}
        owl = rows["Western Screech-Owl"]
        assert owl["withdrawn"] is True
        assert owl["flags"]["withdrawn"] is True
        # The original claim is untouched: same score, same name, still attributable.
        assert owl["score"] == pytest.approx(0.96)
        assert "plausibility floor" in owl["withdrawal"]["reason"]
        assert owl["withdrawal"]["reviewed_utc"] == "2026-08-09T10:00:00+00:00"

    def test_a_standing_row_says_so_explicitly(self, client) -> None:
        # Present and false, never absent: a client that has to distinguish
        # "not withdrawn" from "this station is too old to know" cannot do it
        # from a missing key.
        rows = {row["common_name"]: row for row in self._rows(client)}
        assert rows["Tawny Owl"]["withdrawn"] is False
        assert rows["Tawny Owl"]["withdrawal"] is None

    def test_the_detail_view_carries_the_withdrawal(self, client) -> None:
        row = client.get(f"/api/v1/detections/{self.withdrawn_id}").json()
        assert row["withdrawn"] is True
        assert row["withdrawal"]["recomputed_band"] == "implausible"

    def test_the_csv_export_carries_the_marker(self, client) -> None:
        body = client.get(
            "/api/v1/detections/export", params={**self.WINDOW, "format": "csv"}
        ).text
        header, *lines = [line for line in body.splitlines() if line]
        assert "withdrawn" in header.split(",")
        owl = next(line for line in lines if "Western Screech-Owl" in line)
        assert "True" in owl

    def test_the_history_species_list_excludes_it_and_says_how_many(self, client) -> None:
        payload = client.get("/api/v1/history", params=self.WINDOW).json()
        names = {row["common_name"] for row in payload["species"]}
        assert "Tawny Owl" in names
        assert "Western Screech-Owl" not in names
        assert payload["excluded_withdrawn_count"] == 1

    def test_taxa_activity_excludes_it_and_says_how_many(self, client) -> None:
        # `hours` is a look-back from now, and the seeded night is in the past,
        # so ask for a window wide enough to contain it.
        hours = int((datetime.now(UTC) - NIGHT).total_seconds() // 3600) + 2
        if hours > 168:
            pytest.skip("seeded fixture night has aged out of the endpoint's maximum window")
        payload = client.get(f"/api/v1/taxa/activity?hours={hours}").json()
        names = {row["common_name"] for row in payload["entries"]}
        assert "Tawny Owl" in names
        assert "Western Screech-Owl" not in names
        assert payload["excluded_withdrawn_count"] == 1
        assert client.get(
            f"/api/v1/taxa/activity?hours={hours}&include_withdrawn=true"
        ).json()["entries"], "the diagnostic escape hatch must still work"

    def test_the_wall_displays_connect_snapshot_never_contains_it(self, client) -> None:
        """The end of the chain this whole ADR exists for.

        Filtered in SQL, in a query whose narrow column list deliberately does
        not read `native_result` at all -- so this fails if the predicate is
        dropped, not merely if the Python check is.
        """
        with client.websocket_connect("/api/v1/display?min_score=0.5") as socket:
            import json as _json

            frame = _json.loads(socket.receive_text())
            assert frame["t"] == "h"
            names = [item.get("n") for item in frame["f"]]
            assert "Western Screech-Owl" not in names
