"""End-to-end tests through the real FastAPI app and a real pipeline.

Nothing is mocked. The app runs the whole station against the synthetic capture
source, so these exercise capture → resample → segment → detect → normalise →
persist → serve, which is the path the debug UI actually depends on.
"""

from __future__ import annotations

import asyncio
import struct
import time
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from open_observatory.api.app import create_app
from open_observatory.audio.spectrogram import decode_header_size
from open_observatory.db import models as orm
from open_observatory.db.session import session_scope


@pytest.fixture
def client(settings):
    settings = settings.model_copy(
        update={
            "source": "synthetic",
            "synthetic_scene": "mixed",
            # 192 kHz so the ultrasonic channel and detector both come alive.
            "synthetic_sample_rate": 192000,
            "clip_plugins": ("activity-v1", "ultrasonic-pass-v1"),
            "clip_min_score": 0.0,
            "native_ring_seconds": 20,
            "audible_ring_seconds": 20,
        }
    )
    from open_observatory.config import set_settings

    set_settings(settings)
    app = create_app(settings)
    with TestClient(app) as test_client:
        # Let the pipeline actually run for a moment.
        for _ in range(40):
            if test_client.get("/api/v1/station").json()["capture"]["blocks"] > 12:
                break
            import time

            time.sleep(0.25)
        yield test_client


class TestStationEndpoints:
    def test_health_reports_clip_storage_that_is_not_its_own_device(self, settings) -> None:
        """The SSD going missing must be loud, not silent.

        Evidence clips live on a USB SSD mounted over the clips directory
        (ADR-021). If that mount is absent the station keeps capturing -- capture
        always wins -- but it would be writing multi-megabyte clips to the SD card
        alongside capture, which is exactly what caused ALSA overruns on
        2026-08-08. Degrading quietly would reintroduce that with nobody looking.
        """
        configured = settings.model_copy(update={"source": "synthetic", "clips_require_mount": True})
        from open_observatory.config import set_settings

        set_settings(configured)
        app = create_app(configured)
        with TestClient(app) as local_client:
            body = local_client.get("/api/v1/health").json()

        # Not asserting the exact status: it is "degraded" while capturing and
        # "critical" before the first block arrives, which is a startup race and
        # not the point. The point is that the problem is named.
        assert body["status"] != "ok"
        assert any("not a mount point" in problem for problem in body["problems"]), body["problems"]

    def test_health_reports_synthetic_source_honestly(self, client) -> None:
        payload = client.get("/api/v1/health").json()
        assert payload["capture"]["state"] == "capturing"
        assert payload["capture"]["is_live_hardware"] is False
        # A synthetic stream must never be silently presented as a microphone.
        assert any("synthetic" in problem for problem in payload["problems"])

    def test_station_snapshot_has_every_panel_the_ui_needs(self, client) -> None:
        payload = client.get("/api/v1/station").json()
        for key in (
            "station",
            "capture",
            "resampler",
            "rings",
            "levels",
            "spectrograms",
            "segmenters",
            "leases",
            "detectors",
            "normaliser",
            "clips",
            "storage",
            "live_audio",
            "bus",
            "persistence",
        ):
            assert key in payload, f"status snapshot is missing {key!r}"

    def test_capture_advances_and_stays_continuous(self, client) -> None:
        first = client.get("/api/v1/station").json()["capture"]
        import time

        time.sleep(1.0)
        second = client.get("/api/v1/station").json()["capture"]
        assert second["frames"] > first["frames"]
        assert second["discontinuities"] == 0
        assert second["continuity_ratio"] is None or second["continuity_ratio"] > 0.9

    def test_levels_are_labelled_as_uncalibrated(self, client) -> None:
        payload = client.get("/api/v1/station").json()["levels"]
        assert "not calibrated" in payload["note"].lower()

    def test_both_spectrogram_channels_exist_at_high_rate(self, client) -> None:
        specs = client.get("/api/v1/station").json()["spectrograms"]
        names = {spec["name"] for spec in specs}
        assert names == {"audible", "ultrasonic"}
        ultrasonic = next(spec for spec in specs if spec["name"] == "ultrasonic")
        assert ultrasonic["min_hz"] >= 15000
        # `columns_emitted > 0` used to stand here, and stopped being true of an
        # idle station when ADR-040 made encoding conditional on a viewer. It was
        # testing that the channel *runs*, which is now a different question from
        # whether it *exists*; the running is covered by
        # `test_live_gating_endpoint.py`. Connect a viewer and it emits.
        assert ultrasonic["viewer_gated"] is True
        with client.websocket_connect("/api/v1/live") as socket:
            socket.receive_text()  # hello
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                specs = client.get("/api/v1/station").json()["spectrograms"]
                emitted = next(s for s in specs if s["name"] == "ultrasonic")["columns_emitted"]
                if emitted > 0:
                    break
                time.sleep(0.1)
            assert emitted > 0

    def test_detectors_declare_their_claims_and_licences(self, client) -> None:
        detectors = client.get("/api/v1/detectors").json()["detectors"]
        by_id = {entry["plugin_id"]: entry for entry in detectors}
        assert "activity-v1" in by_id
        for entry in detectors:
            assert entry["claim"], f"{entry['plugin_id']} has no stated claim"
            assert entry["licence_name"]
            assert isinstance(entry["calibrated"], bool)

    def test_near_misses_endpoint_names_what_was_refused(self, client) -> None:
        """ADR-052. The endpoint an operator tunes from.

        BirdNET's model assets are deliberately unbundled (ADR-006), so this
        test stands a ledger-bearing plugin in front of a real running worker
        rather than skipping: what is under test is the API contract -- that
        a detector's near misses reach a client with the species, the score,
        the prior, the band and the bar it missed -- not BirdNET's inference.
        """
        from open_observatory.detectors.near_miss import NearMissLedger

        station = client.app.state.station
        worker = station.workers[0]

        class _WithLedger:
            def __init__(self, inner) -> None:
                self._inner = inner
                self._ledger = NearMissLedger(capacity=8)

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def near_miss_snapshot(self, *, limit=50, species_limit=40):
                snapshot = self._ledger.snapshot(
                    thresholds={"in_range": 0.55}, limit=limit, species_limit=species_limit
                )
                snapshot["note"] = "candidates only"
                snapshot["min_confidence"] = 0.12
                snapshot["windows_analysed"] = 998
                snapshot["range_model_loaded"] = True
                snapshot["week"] = 30
                snapshot["plausibility_floor"] = 0.0005
                return snapshot

        wrapper = _WithLedger(worker.plugin)
        wrapper._ledger.record_rejected(
            at_ns=1_700_000_000_000_000_000,
            label_index=42,
            common_name="Eurasian Blackbird",
            scientific_name="Turdus merula",
            score=0.538,
            occurrence=0.9312,
            band="in_range",
            threshold=0.55,
        )
        original = worker.plugin
        worker.plugin = wrapper
        try:
            payload = client.get("/api/v1/detectors/near-misses").json()
        finally:
            worker.plugin = original

        entry = next(
            item for item in payload["detectors"] if item["plugin_id"] == worker.plugin_id
        )
        assert entry["rejected_total"] == 1
        row = entry["recent"][0]
        assert row["common_name"] == "Eurasian Blackbird"
        assert row["score"] == 0.538
        assert row["occurrence_probability"] == 0.9312
        assert row["band"] == "in_range"
        assert row["threshold"] == 0.55
        assert row["shortfall"] == 0.012
        # The histogram is the part that chooses a threshold, so it must be
        # on the payload whether or not the ring is holding anything.
        in_range = next(band for band in entry["bands"] if band["band"] == "in_range")
        assert sum(in_range["histogram"]["counts"]) == 1
        # And a detector with no plausibility bands is absent rather than
        # carrying an empty stub.
        assert len(payload["detectors"]) == 1

    def test_resampler_reports_zero_group_delay(self, client) -> None:
        resampler = client.get("/api/v1/station").json()["resampler"]
        assert resampler["group_delay_frames"] == 0
        assert resampler["source_rate"] == 192000
        assert resampler["target_rate"] == 48000

    def test_metrics_endpoint_exposes_the_spec_metrics(self, client) -> None:
        body = client.get("/metrics").text
        for metric in (
            "oo_capture_frames_total",
            "oo_capture_discontinuities_total",
            "oo_capture_live_hardware",
            "oo_ring_fill_ratio",
            "oo_detector_queue_depth",
            "oo_audio_rms_dbfs",
            "oo_storage_free_bytes",
        ):
            assert metric in body, f"missing metric {metric}"

    def test_birdnet_plausibility_metric_is_exposed_per_reason(self, settings) -> None:
        """ADR-032: `/metrics` must carry the suppression counters even before

        any window has been analysed -- the worker registers at startup
        regardless of whether the (unbundled, ADR-006) model assets are
        present, and `plausibility_snapshot()` starts at zero rather than
        being absent.
        """
        configured = settings.model_copy(update={"source": "synthetic", "birdnet_enabled": True})
        from open_observatory.config import set_settings

        set_settings(configured)
        app = create_app(configured)
        with TestClient(app) as local_client:
            body = local_client.get("/metrics").text
        assert "oo_birdnet_suppressed_total" in body
        for reason in (
            "suppressed_implausible_prior",
            "suppressed_no_prior",
            "suppressed_out_of_range",
            "suppressed_uncommon",
        ):
            assert f'reason="{reason}"' in body, f"missing reason label {reason}"

    def test_audio_devices_endpoint_lists_without_crashing(self, client) -> None:
        payload = client.get("/api/v1/audio/devices").json()
        assert "devices" in payload
        assert "active" in payload

    def test_model_licences_are_exposed(self, client) -> None:
        payload = client.get("/api/v1/models").json()
        assert "not bundled" in payload["note"]
        for asset in payload["assets"]:
            assert asset["licence"]
            assert asset["source_url"].startswith("http")

    def test_system_report(self, client) -> None:
        payload = client.get("/api/v1/system").json()
        assert "host" in payload
        assert payload["process"]["memory_rss_bytes"] > 0


class TestDetections:
    def test_detections_are_produced_persisted_and_served(self, client) -> None:
        """The whole vertical slice, end to end.

        The `client` fixture runs the real pipeline against the synthetic source
        (no ALSA hardware in CI), so every detection it produces is exactly the
        kind the default view now hides -- include_synthetic=true is what makes
        this an end-to-end test of persistence rather than of the new filter.
        """
        import time

        detections: list = []
        for _ in range(60):
            detections = client.get(
                "/api/v1/detections?limit=50&include_synthetic=true"
            ).json()["detections"]
            if detections:
                break
            time.sleep(0.5)
        assert detections, "the synthetic dawn chorus produced no detections at all"

        first = detections[0]
        assert first["event_start_utc"].endswith("Z")
        assert first["source_end_frame"] > first["source_start_frame"]
        assert 0.0 <= first["score"] <= 1.0
        assert first["detector"]["plugin_id"]
        # Honestly labelled, even though the caller opted in to seeing it.
        assert first["source_kind"] == "synthetic"
        assert first["is_live_source"] is False

        detail = client.get(f"/api/v1/detections/{first['id']}?include_synthetic=true").json()
        assert detail["id"] == first["id"]
        # The detector's own output is preserved verbatim.
        assert detail["native_result"]

        # display_title's fields are on both the list row and the detail row, so
        # the same detection cannot display differently depending on how it was
        # fetched (the bug this work fixes).
        for row in (first, detail):
            assert "title_hint" in row
            assert "flags" in row
            assert "feeding_buzz" in row["flags"]

    def test_bat_pass_gets_a_frequency_title_hint(self, client) -> None:
        """A bat pass must show a presentational title_hint with the mandatory
        '?' on any candidate name, but must never carry a species name in its
        taxonomic fields (that guard belongs to the normaliser, not to the UI)."""
        import time

        bat_rows: list = []
        for _ in range(60):
            rows = client.get(
                "/api/v1/detections?limit=200&plugin_id=ultrasonic-pass-v1&include_synthetic=true"
            ).json()["detections"]
            bat_rows = [row for row in rows if row["taxonomic_group"] == "bat"]
            if bat_rows:
                break
            time.sleep(0.5)
        assert bat_rows, "the synthetic bat scene produced no ultrasonic pass"

        for row in bat_rows:
            assert row["common_name"] is None
            assert row["scientific_name"] is None
            assert row["canonical_taxon_id"] is None
            if row["title_hint"] is not None:
                assert "kHz" in row["title_hint"]
                # Any candidate name in the hint must carry its mandatory '?'.
                if "·" in row["title_hint"].split("kHz", 1)[-1]:
                    assert "?" in row["title_hint"]

    def test_activity_detections_never_carry_taxonomy(self, client) -> None:
        import time

        for _ in range(40):
            rows = client.get(
                "/api/v1/detections?limit=200&plugin_id=activity-v1&include_synthetic=true"
            ).json()["detections"]
            if rows:
                break
            time.sleep(0.5)
        for row in rows:
            assert row["common_name"] is None
            assert row["scientific_name"] is None
            assert row["canonical_taxon_id"] is None
            assert row["taxonomic_group"] == "acoustic_event"

    def test_claim_violations_stay_at_zero(self, client) -> None:
        assert client.get("/api/v1/station").json()["normaliser"]["claim_violations"] == 0

    def test_evidence_clip_is_written_and_downloadable(self, client) -> None:
        import time

        found = None
        for _ in range(60):
            rows = client.get(
                "/api/v1/detections?limit=100&include_synthetic=true"
            ).json()["detections"]
            for row in rows:
                if row["media"]:
                    found = row
                    break
            if found:
                break
            time.sleep(0.5)
        assert found, "no detection produced an evidence clip"
        asset = found["media"][0]
        response = client.get(asset["url"])
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.content[:4] == b"RIFF"
        assert len(response.content) == asset["byte_length"]

    def test_missing_detection_is_404_and_bad_id_is_400(self, client) -> None:
        import uuid as uuid_module

        assert client.get(f"/api/v1/detections/{uuid_module.uuid4()}").status_code == 404
        assert client.get("/api/v1/detections/not-a-uuid").status_code == 400

    def test_taxa_activity_summarises(self, client) -> None:
        payload = client.get("/api/v1/taxa/activity?hours=24&include_synthetic=true").json()
        assert "entries" in payload
        for entry in payload["entries"]:
            assert entry["detections"] >= 1
            assert entry["display_name"]
            assert "title_hint" in entry

    def test_streams_and_gaps_endpoints(self, client) -> None:
        streams = client.get("/api/v1/streams").json()["streams"]
        assert streams
        assert streams[0]["sample_rate"] == 192000
        assert "gaps" in client.get("/api/v1/gaps").json()


class TestReviewWorkflow:
    """End-to-end human review (ADR-043), through the real pipeline and
    database -- confirm/reject/hold, taxon correction, and that the
    correction is visible everywhere it must be: the review endpoints
    themselves, the detection detail/list payloads, and the CSV/JSON export.

    Species-rank detections are seeded directly into the database rather
    than waited for from the pipeline: the `client` fixture runs with
    `birdnet_enabled=False` (`conftest.py`) because BirdNET's model assets
    are never bundled (ADR-006) and are not present in CI, so the synthetic
    scene's `activity-v1`/`ultrasonic-pass-v1` detectors never produce a
    species-rank, `canonical_taxon_id`-bearing detection on their own --
    exactly the honesty rule ADR-010/ADR-013 enforce. Seeding a row directly
    is the same shape `test_retention.py` and `test_review.py` already use.
    """

    def _seed_species_detection(
        self,
        client,
        *,
        common_name: str = "European Robin",
        scientific_name: str = "Erithacus rubecula",
        taxon_id: str = "sci:erithacus_rubecula",
    ) -> dict:
        with session_scope() as session:
            station_id = session.execute(select(orm.Station.id).limit(1)).scalar_one()
            detector_id = session.execute(select(orm.Detector.id).limit(1)).scalar_one()
            detection_id = uuid.uuid4()
            now = datetime.now(UTC)
            session.add(
                orm.Detection(
                    id=detection_id,
                    station_id=station_id,
                    detector_id=detector_id,
                    stream_id=uuid.uuid4(),
                    window_id=uuid.uuid4(),
                    event_start_utc=now,
                    event_end_utc=now,
                    source_start_frame=0,
                    source_end_frame=1,
                    detector_label=common_name,
                    common_name=common_name,
                    scientific_name=scientific_name,
                    canonical_taxon_id=taxon_id,
                    rank="species",
                    taxonomic_group="bird",
                    score=0.9,
                )
            )
        return client.get(
            f"/api/v1/detections/{detection_id}?include_synthetic=true"
        ).json()

    def test_confirm_then_reject_supersedes(self, client) -> None:
        target = self._seed_species_detection(client)
        confirm = client.post(f"/api/v1/detections/{target['id']}/review", json={"status": "confirmed"})
        assert confirm.status_code == 200
        assert confirm.json()["status"] == "confirmed"

        reject = client.post(f"/api/v1/detections/{target['id']}/review", json={"status": "rejected"})
        assert reject.status_code == 200
        assert reject.json()["status"] == "rejected"

        latest = client.get(f"/api/v1/detections/{target['id']}/review").json()["review"]
        assert latest["status"] == "rejected"

    def test_taxa_search_finds_a_station_identified_species(self, client) -> None:
        target = self._seed_species_detection(client)
        needle = (target["common_name"] or target["scientific_name"])[:4]
        results = client.get(f"/api/v1/taxa/search?q={needle}").json()["taxa"]
        assert any(t["taxon_id"] == target["canonical_taxon_id"] for t in results)

    def test_correcting_a_detection_preserves_the_original_and_shows_the_correction(
        self, client
    ) -> None:
        target = self._seed_species_detection(client)
        taxon_id = target["canonical_taxon_id"]

        correction = client.post(
            f"/api/v1/detections/{target['id']}/review",
            json={"status": "corrected", "corrected_taxon_id": taxon_id, "note": "heard it myself"},
        )
        assert correction.status_code == 200
        body = correction.json()
        assert body["status"] == "corrected"
        assert body["corrected_taxon_id"] == taxon_id
        assert body["corrected_common_name"] == target["common_name"]
        assert body["actor"] == "local"  # anonymous debug-slice default (ADR-034)

        detail = client.get(f"/api/v1/detections/{target['id']}?include_synthetic=true").json()
        # The original claim is never edited.
        assert detail["common_name"] == target["common_name"]
        assert detail["scientific_name"] == target["scientific_name"]
        # The correction is visible alongside it.
        assert detail["review"]["status"] == "corrected"
        assert detail["review"]["corrected_taxon_id"] == taxon_id
        assert detail["identification_source"] == "human"
        assert detail["effective_common_name"] == target["common_name"]

        listed = client.get(
            "/api/v1/detections?limit=200&include_synthetic=true"
        ).json()["detections"]
        row = next(r for r in listed if r["id"] == target["id"])
        assert row["review"]["status"] == "corrected"
        assert row["identification_source"] == "human"

        export = client.get(
            "/api/v1/detections/export?format=json&include_synthetic=true&limit=200"
        ).json()
        exported = next(r for r in export["detections"] if r["id"] == target["id"])
        assert exported["review"]["status"] == "corrected"
        assert exported["identification_source"] == "human"

        csv_response = client.get(
            "/api/v1/detections/export?format=csv&include_synthetic=true&limit=200"
        )
        assert csv_response.status_code == 200
        assert "review_status" in csv_response.text
        assert "corrected" in csv_response.text

    def test_correction_requires_taxon_id(self, client) -> None:
        target = self._seed_species_detection(client)
        response = client.post(
            f"/api/v1/detections/{target['id']}/review", json={"status": "corrected"}
        )
        assert response.status_code == 422

    def test_taxon_id_only_valid_with_corrected_status(self, client) -> None:
        target = self._seed_species_detection(client)
        response = client.post(
            f"/api/v1/detections/{target['id']}/review",
            json={"status": "confirmed", "corrected_taxon_id": target["canonical_taxon_id"]},
        )
        assert response.status_code == 422

    def test_correcting_to_an_unknown_taxon_is_rejected(self, client) -> None:
        target = self._seed_species_detection(client)
        response = client.post(
            f"/api/v1/detections/{target['id']}/review",
            json={"status": "corrected", "corrected_taxon_id": "sci:nonexistent_species"},
        )
        assert response.status_code == 400

    def test_held_status_is_accepted_and_visible(self, client) -> None:
        target = self._seed_species_detection(client)
        response = client.post(f"/api/v1/detections/{target['id']}/review", json={"status": "held"})
        assert response.status_code == 200
        assert response.json()["status"] == "held"
        assert client.get(f"/api/v1/detections/{target['id']}/review").json()["review"]["status"] == "held"

    def test_unreviewed_detection_has_no_review_but_is_never_none_shaped_wrong(
        self, client
    ) -> None:
        target = self._seed_species_detection(client)
        detail = client.get(f"/api/v1/detections/{target['id']}?include_synthetic=true").json()
        assert detail["review"] is None
        assert detail["identification_source"] == "model"
        assert detail["effective_common_name"] == detail["common_name"]

    def test_review_of_missing_detection_is_404(self, client) -> None:
        import uuid as uuid_module

        response = client.post(
            f"/api/v1/detections/{uuid_module.uuid4()}/review", json={"status": "confirmed"}
        )
        assert response.status_code == 404


class TestSourceFiltering:
    """The station in this fixture only ever runs the synthetic source (there is
    no ALSA hardware in CI), so every detection it produces is exactly the kind
    that must not be presented as an observation by default -- this is the
    regression suite for the USB/OFF incident, exercised through the real API.
    """

    def _wait_for_a_detection(self, client) -> dict:
        import time

        for _ in range(60):
            rows = client.get(
                "/api/v1/detections?limit=50&include_synthetic=true"
            ).json()["detections"]
            if rows:
                return rows[0]
            time.sleep(0.5)
        raise AssertionError("the synthetic dawn chorus produced no detections at all")

    def test_detections_list_hides_synthetic_by_default(self, client) -> None:
        self._wait_for_a_detection(client)
        payload = client.get("/api/v1/detections?limit=50").json()
        assert payload["detections"] == []
        assert payload["include_synthetic"] is False
        # Silently empty would look identical to a quiet night; the count is what
        # lets an operator tell the two apart.
        assert payload["excluded_synthetic_count"] > 0

    def test_detections_list_shows_synthetic_when_asked(self, client) -> None:
        self._wait_for_a_detection(client)
        payload = client.get("/api/v1/detections?limit=50&include_synthetic=true").json()
        assert payload["detections"]
        assert payload["include_synthetic"] is True
        assert payload["excluded_synthetic_count"] == 0
        assert all(row["source_kind"] == "synthetic" for row in payload["detections"])

    def test_detection_detail_404s_for_synthetic_by_default(self, client) -> None:
        found = self._wait_for_a_detection(client)
        response = client.get(f"/api/v1/detections/{found['id']}")
        assert response.status_code == 404
        # An operator hitting this from a bookmarked/shared link must be able to
        # tell "hidden because synthetic" apart from "never existed".
        assert "include_synthetic" in response.json()["detail"]

    def test_detection_detail_visible_with_override(self, client) -> None:
        found = self._wait_for_a_detection(client)
        response = client.get(f"/api/v1/detections/{found['id']}?include_synthetic=true")
        assert response.status_code == 200
        assert response.json()["id"] == found["id"]

    def test_taxa_activity_hides_synthetic_and_reports_the_count(self, client) -> None:
        self._wait_for_a_detection(client)
        payload = client.get("/api/v1/taxa/activity?hours=24").json()
        assert payload["entries"] == []
        assert payload["excluded_synthetic_count"] > 0

    def test_history_hides_synthetic_and_reports_the_count(self, client) -> None:
        self._wait_for_a_detection(client)
        payload = client.get("/api/v1/history?window=last-24h").json()
        assert payload["species"] == []
        assert payload["timeline"]["excluded_synthetic_count"] > 0

    def test_history_shows_synthetic_when_asked(self, client) -> None:
        self._wait_for_a_detection(client)
        payload = client.get("/api/v1/history?window=last-24h&include_synthetic=true").json()
        assert payload["timeline"]["excluded_synthetic_count"] == 0
        total = sum(
            group["detections"]
            for bucket in payload["timeline"]["buckets"]
            for group in bucket["groups"].values()
        )
        assert total > 0


class TestLiveChannels:
    def test_live_socket_sends_hello_then_binary_columns(self, client) -> None:
        with client.websocket_connect("/api/v1/live") as socket:
            hello = socket.receive_json()
            assert hello["type"] == "hello"
            assert hello["station"]["capture"]["state"] == "capturing"
            assert hello["spectrograms"]
            assert "server_utc" in hello

            # Backfill plus live columns are binary frames with our header.
            payload = socket.receive_bytes()
            frame_type, channel, bins, columns, _reserved, first_utc = struct.unpack(
                "<BBHHHd", payload[: decode_header_size()]
            )
            assert frame_type == 1
            assert channel in (0, 1)
            assert bins > 0
            assert columns > 0
            assert len(payload) == decode_header_size() + bins * columns
            assert first_utc > 1_600_000_000

    def test_live_audio_socket_streams_int16_pcm(self, client) -> None:
        with client.websocket_connect("/api/v1/live/audio") as socket:
            hello = socket.receive_json()
            assert hello["type"] == "audio-hello"
            assert hello["encoding"] == "pcm_s16le_mono"
            assert hello["sample_rate"] == 48000
            chunk = socket.receive_bytes()
            # Whole number of int16 frames, matching the advertised chunk size.
            assert len(chunk) % 2 == 0
            assert len(chunk) == hello["chunk_frames"] * 2

    def test_listener_count_is_reported(self, client) -> None:
        with client.websocket_connect("/api/v1/live/audio") as socket:
            socket.receive_json()
            socket.receive_bytes()
            assert client.get("/api/v1/station").json()["live_audio"]["listeners"] >= 1

    def test_audible_channel_is_the_default_and_costs_nothing_idle(self, client) -> None:
        # No one has ever connected to the ultrasonic channel in this test, so
        # the heterodyne must not have processed a single native sample, even
        # though capture has been running for a while (the `client` fixture
        # waits for >12 blocks before yielding).
        import time

        time.sleep(0.5)
        snapshot = client.get("/api/v1/station").json()["live_audio_ultrasonic"]
        assert snapshot["listeners"] == 0
        heterodyne = snapshot["heterodyne"]
        assert heterodyne is not None
        assert heterodyne["native_samples_processed"] == 0

    def test_ultrasonic_channel_streams_pcm_at_the_tuning_frequency(self, client) -> None:
        with client.websocket_connect("/api/v1/live/audio?channel=ultrasonic&tune_hz=42000") as socket:
            hello = socket.receive_json()
            assert hello["type"] == "audio-hello"
            assert hello["channel"] == "ultrasonic"
            assert hello["available"] is True
            assert hello["sample_rate"] == 48000
            assert hello["tune_hz"] == pytest.approx(42000.0)
            assert hello["bandwidth_hz"] > 0
            chunk = socket.receive_bytes()
            assert len(chunk) % 2 == 0
            assert len(chunk) == hello["chunk_frames"] * 2

    def test_ultrasonic_listener_count_and_only_ultrasonic_broadcaster_is_used(self, client) -> None:
        with client.websocket_connect("/api/v1/live/audio?channel=ultrasonic") as socket:
            socket.receive_json()
            socket.receive_bytes()
            station = client.get("/api/v1/station").json()
            assert station["live_audio_ultrasonic"]["listeners"] >= 1
            # The audible channel's own listener count is untouched.
            assert station["live_audio"]["listeners"] == 0

    def test_ultrasonic_retune_via_socket_message_is_applied(self, client) -> None:
        with client.websocket_connect("/api/v1/live/audio?channel=ultrasonic&tune_hz=30000") as socket:
            socket.receive_json()
            socket.send_json({"type": "tune", "tune_hz": 60000})
            # Give the reader task a moment to process it, then drain a chunk
            # so the connection stays alive long enough to matter.
            socket.receive_bytes()
            import time

            time.sleep(0.2)
            heterodyne = client.get("/api/v1/station").json()["live_audio_ultrasonic"]["heterodyne"]
            assert heterodyne["tune_hz"] == pytest.approx(60000.0, abs=1.0)

    @pytest.mark.skip(
        reason=(
            "starlette 0.41.3's synchronous TestClient blocks in "
            "_TestClientTransport.handle_request until the ASGI app coroutine "
            "returns, so client.stream(...) cannot represent a still-open "
            "connection to live_audio_wav's genuinely infinite generator -- "
            "this hangs forever rather than failing. Pre-existing starlette "
            "limitation, not a regression. Exercise this path for real with "
            "the WebSocket TestClient (genuinely concurrent) or "
            "httpx.AsyncClient with ASGITransport (real async streaming) "
            "instead; see docs/development/SETUP.md trap 3."
        )
    )
    def test_audio_wav_streams_a_valid_riff_header_then_pcm(self, client) -> None:
        with client.stream("GET", "/api/v1/live/audio.wav") as response:
            assert response.status_code == 200
            assert response.headers["content-type"] == "audio/wav"
            assert response.headers["cache-control"] == "no-store"
            assert int(response.headers["x-live-sample-rate"]) == 48000

            chunks = response.iter_bytes()
            body = b""
            while len(body) < 144:
                body += next(chunks)

            (
                riff_id,
                riff_size,
                wave_id,
                fmt_id,
                fmt_size,
                audio_format,
                num_channels,
                sample_rate,
                byte_rate,
                block_align,
                bits_per_sample,
                data_id,
                data_size,
            ) = struct.unpack("<4sI4s4sIHHIIHH4sI", body[:44])
            assert riff_id == b"RIFF"
            assert wave_id == b"WAVE"
            assert fmt_id == b"fmt "
            assert fmt_size == 16
            assert audio_format == 1  # PCM
            assert num_channels == 1
            assert sample_rate == 48000
            assert bits_per_sample == 16
            assert byte_rate == sample_rate * block_align
            assert data_id == b"data"
            # Endless stream: the conventional "unknown length" placeholder.
            assert riff_size == 0xFFFFFFFF
            assert data_size == 0xFFFFFFFF

            # Some real PCM follows the header, and it is a whole number of
            # 16-bit frames.
            pcm = body[44:]
            assert len(pcm) >= 100
            assert len(pcm) % 2 == 0

    @pytest.mark.skip(
        reason=(
            "starlette 0.41.3's synchronous TestClient blocks in "
            "_TestClientTransport.handle_request until the ASGI app coroutine "
            "returns, so client.stream(...) cannot represent a still-open "
            "connection to live_audio_wav's genuinely infinite generator -- "
            "this hangs forever rather than failing. Pre-existing starlette "
            "limitation, not a regression. Exercise this path for real with "
            "the WebSocket TestClient (genuinely concurrent) or "
            "httpx.AsyncClient with ASGITransport (real async streaming) "
            "instead; see docs/development/SETUP.md trap 3."
        )
    )
    def test_audio_wav_ultrasonic_channel_honours_tune_hz(self, client) -> None:
        with client.stream(
            "GET", "/api/v1/live/audio.wav?channel=ultrasonic&tune_hz=42000"
        ) as response:
            assert response.status_code == 200
            assert float(response.headers["x-live-tune-hz"]) == pytest.approx(42000.0, abs=1.0)
            assert float(response.headers["x-live-bandwidth-hz"]) > 0
            next(response.iter_bytes())  # header
            next(response.iter_bytes())  # at least one PCM chunk

    @pytest.mark.skip(
        reason=(
            "starlette 0.41.3's synchronous TestClient blocks in "
            "_TestClientTransport.handle_request until the ASGI app coroutine "
            "returns, so client.stream(...) cannot represent a still-open "
            "connection to live_audio_wav's genuinely infinite generator -- "
            "this hangs forever rather than failing. Pre-existing starlette "
            "limitation, not a regression. Exercise this path for real with "
            "the WebSocket TestClient (genuinely concurrent) or "
            "httpx.AsyncClient with ASGITransport (real async streaming) "
            "instead; see docs/development/SETUP.md trap 3."
        )
    )
    def test_audio_wav_disconnect_releases_the_broadcaster_listener(self, client) -> None:
        import time

        with client.stream("GET", "/api/v1/live/audio.wav") as response:
            chunks = response.iter_bytes()
            next(chunks)  # header
            next(chunks)  # a PCM chunk, so the listener has definitely attached
            assert client.get("/api/v1/station").json()["live_audio"]["listeners"] >= 1

        # The `with` block above closed the response; the server should notice
        # the disconnect and release the listener rather than leaking it (for
        # the ultrasonic channel, a leaked listener would keep the heterodyne
        # running for nobody).
        deadline = time.monotonic() + 5.0
        released = False
        while time.monotonic() < deadline:
            if client.get("/api/v1/station").json()["live_audio"]["listeners"] == 0:
                released = True
                break
            time.sleep(0.2)
        assert released, "listener was not released after the client disconnected"

    def test_live_tune_endpoint_retunes_the_shared_oscillator_without_disturbing_an_open_listener(
        self, client
    ) -> None:
        """`POST /api/v1/live/tune` is the retune control the chunked-WAV path is
        missing (ADR-022) -- but the oscillator it retunes is shared
        station-wide across every ultrasonic listener regardless of transport
        (ADR-018), so this is exercised here against an already-open
        WebSocket listener rather than the WAV response directly.

        This is a genuine substitution, not a weaker one: starlette's
        synchronous `TestClient` fully drains an HTTP endpoint's ASGI call
        before `client.stream(...)` returns at all (see
        `_TestClientTransport.handle_request` in `starlette/testclient.py`,
        which blocks on `portal.call(self.app, ...)` until the app coroutine
        itself returns), so it cannot represent a still-open connection to a
        never-ending generator like `live_audio_wav`'s body -- attempting it
        hangs the test process forever, confirmed against this repo's
        `test_audio_wav_streams_a_valid_riff_header_then_pcm` on an unmodified
        checkout too, so this is a pre-existing TestClient limitation, not a
        regression. The WebSocket TestClient genuinely supports a still-open,
        concurrently-driven connection (it runs the app on a background
        thread against real queues rather than draining to completion), so it
        can actually prove the thing that matters: a retune request lands on
        the shared oscillator and the open connection is never reconnected --
        exactly the code path (`Station.set_ultrasonic_tune_hz`) the WAV
        endpoint's own connect-time `tune_hz` handling already shares.
        """
        with client.websocket_connect("/api/v1/live/audio?channel=ultrasonic&tune_hz=30000") as socket:
            hello = socket.receive_json()
            assert hello["tune_hz"] == pytest.approx(30000.0, abs=1.0)
            socket.receive_bytes()  # already flowing before the retune

            tune_response = client.post("/api/v1/live/tune?tune_hz=60000")
            assert tune_response.status_code == 200
            payload = tune_response.json()
            assert payload["tune_hz"] == pytest.approx(60000.0, abs=1.0)
            assert payload["bandwidth_hz"] > 0
            assert payload["available"] is True

            # Still the same open socket -- no reconnect, no new hello frame --
            # and the station-wide oscillator it shares with the WAV path has
            # moved.
            socket.receive_bytes()
            heterodyne = client.get("/api/v1/station").json()["live_audio_ultrasonic"]["heterodyne"]
            assert heterodyne["tune_hz"] == pytest.approx(60000.0, abs=1.0)

    def test_live_tune_endpoint_clamps_out_of_range_requests(self, client) -> None:
        response = client.post("/api/v1/live/tune?tune_hz=999999")
        assert response.status_code == 200
        payload = response.json()
        assert payload["tune_hz"] < 999999
        assert payload["available"] is True


class TestDebugSurface:
    def test_pipeline_debug_includes_recent_events(self, client) -> None:
        payload = client.get("/api/v1/debug/pipeline").json()
        assert "station" in payload
        assert "live_hub" in payload
        types = {event["event_type"] for event in payload["recent_events"]}
        assert "capture.started" in types

    def test_event_envelopes_match_the_published_schema(self, client) -> None:
        events = client.get("/api/v1/debug/events?limit=50").json()["events"]
        assert events
        for event in events:
            # 1.0 -> 1.1: MQTT (Milestone 6, ADR-025) fixed the schema's
            # additionalProperties:false gap that dropped `rank` and
            # `taxonomic_group`; see schemas/detection-event.schema.json.
            assert event["schema_version"] == "1.1"
            assert event["event_id"]
            assert event["event_type"]
            assert event["occurred_at"].endswith("Z")
            assert isinstance(event["data"], dict)

    def test_levels_history(self, client) -> None:
        payload = client.get("/api/v1/debug/levels?seconds=60").json()
        for sample in payload["samples"]:
            assert "rms_dbfs" in sample
            assert "clipping_ratio" in sample


class TestSecurity:
    def test_media_outside_the_clip_directory_is_refused(self, client, settings) -> None:
        """A database row must not be able to serve an arbitrary file."""
        import uuid as uuid_module

        from open_observatory.db import models as orm
        from open_observatory.db.session import session_scope

        asset_id = uuid_module.uuid4()
        with session_scope() as session:
            session.add(
                orm.MediaAsset(
                    id=asset_id,
                    kind="evidence_native",
                    storage_uri="/etc/passwd",
                    mime_type="audio/wav",
                    byte_length=1,
                    sha256="0" * 64,
                )
            )
        assert client.get(f"/api/v1/media/{asset_id}").status_code == 403


def test_synthetic_and_replay_agree_on_the_block_contract() -> None:
    """A replayed fixture and a live source must be indistinguishable downstream."""
    from open_observatory.audio.replay_source import SyntheticSource

    async def run() -> None:
        source = SyntheticSource(
            scene="dawn-chorus", sample_rate=48000, block_ms=100, mode="accelerated"
        )
        info = await source.open()
        assert info.fmt.sample_rate == 48000
        previous = None
        for _ in range(10):
            block = await source.read()
            assert block is not None
            assert block.stream_id == info.stream_id
            assert block.sample_rate == info.fmt.sample_rate
            if previous is not None:
                assert block.first_frame == previous.last_frame
            previous = block
        await source.close()

    asyncio.run(run())


class TestRetentionStatus:
    """`GET /api/v1/retention/status` — the operator-facing storage panel.

    The UI for this (ADR-029) was written against an assumed contract while the
    retention backend (ADR-026) was built separately, and the two did not meet:
    the UI polled an endpoint that did not exist, so the panel would have shown
    "not available yet" forever. These tests pin the shape so that cannot
    silently regress again.
    """

    def test_reports_a_tier_for_each_stage_of_the_ageing_policy(self, client) -> None:
        payload = client.get("/api/v1/retention/status").json()

        assert [tier["name"] for tier in payload["tiers"]] == [
            "native + audible",
            "audible only",
            "first/best per species",
        ]
        # Ascending, and matching the configured policy rather than hardcoded
        # numbers -- the boundaries are settings, and a test that restated them
        # as literals would pass while the operator's configuration was ignored.
        settings = client.app.state.settings
        assert [tier["age_days_max"] for tier in payload["tiers"]] == [
            settings.retention_native_days,
            settings.retention_audible_only_days,
            settings.retention_exemplar_only_days,
        ]
        assert payload["disk_reclaim_threshold"] == settings.retention_watermark_ratio

    def test_every_tier_reports_counts_and_bytes_that_are_never_negative(self, client) -> None:
        payload = client.get("/api/v1/retention/status").json()

        for tier in payload["tiers"]:
            assert tier["clips"] >= 0
            assert tier["bytes"] >= 0
        assert payload["eligible_for_deletion"]["clips"] >= 0
        assert payload["eligible_for_deletion"]["bytes"] >= 0

    def test_freshly_written_clips_land_in_the_youngest_tier_only(self, client) -> None:
        """Anything this run produced is seconds old, so it must appear in the
        0-7 day tier and nowhere else. This is what catches an inverted or
        off-by-one age comparison, which would silently report live evidence as
        being due for deletion."""
        payload = client.get("/api/v1/retention/status").json()
        tiers = {tier["name"]: tier for tier in payload["tiers"]}

        assert tiers["audible only"]["clips"] == 0
        assert tiers["first/best per species"]["clips"] == 0
        assert payload["eligible_for_deletion"]["clips"] == 0

    def test_never_counts_bytes_that_have_already_been_reclaimed(self, client, settings) -> None:
        """A reclaimed asset keeps its detection row but its bytes are gone from
        disk. Counting it would overstate storage in use and make the panel
        disagree with df."""
        import uuid as _uuid
        from datetime import UTC, datetime

        from open_observatory.db import models as orm
        from open_observatory.db.session import session_scope

        before = client.get("/api/v1/retention/status").json()["tiers"][0]

        with session_scope() as session:
            session.add(
                orm.MediaAsset(
                    id=_uuid.uuid4(),
                    kind="clip",
                    storage_uri="/nonexistent/reclaimed.wav",
                    mime_type="audio/wav",
                    byte_length=123_456_789,
                    sha256="0" * 64,
                    created_at=datetime.now(UTC),
                    reclaimed_at=datetime.now(UTC),
                )
            )

        after = client.get("/api/v1/retention/status").json()["tiers"][0]
        assert after["clips"] == before["clips"]
        assert after["bytes"] == before["bytes"]

    def test_reports_rows_whose_file_is_missing_and_corrects_the_reclaimable_bytes(
        self, client, settings
    ) -> None:
        """ADR-057. `reclaimed_at IS NULL` is a claim, not a measurement: 8,067
        live rows on the station (20.59 GB) named files that were gone, and
        every figure here counted them. The panel must be able to say so, and
        `eligible_for_deletion` must not promise space no deletion can free."""
        import uuid as _uuid
        from datetime import UTC, datetime, timedelta

        from open_observatory.db import models as orm
        from open_observatory.db.session import session_scope

        old = datetime.now(UTC) - timedelta(days=400)
        with session_scope() as session:
            session.add(
                orm.MediaAsset(
                    id=_uuid.uuid4(),
                    kind="evidence_native",
                    storage_uri=str(settings.clip_dir / "gone" / "vanished.wav"),
                    mime_type="audio/wav",
                    byte_length=1_000_000,
                    sha256="0" * 64,
                    created_at=old,
                )
            )

        payload = client.get("/api/v1/retention/status").json()
        # Before any audit has run the station must not claim zero: "not yet
        # checked" and "checked, none missing" are different answers.
        assert payload["missing_files"]["exact"] is False
        assert payload["missing_files"]["clips"] == 0

        client.app.state.station.retention.audit_missing_files()
        payload = client.get("/api/v1/retention/status").json()

        assert payload["missing_files"]["exact"] is True
        assert payload["missing_files"]["clips"] == 1
        assert payload["missing_files"]["bytes"] == 1_000_000
        eligible = payload["eligible_for_deletion"]
        # The row is 400 days old, so it is counted as reclaimable -- but
        # reclaiming it would free nothing, and that is the number the disk
        # budget needs.
        assert eligible["bytes"] >= 1_000_000
        assert eligible["bytes_verified_present"] == eligible["bytes"] - 1_000_000

    def test_health_says_so_as_a_note_without_degrading_the_station(
        self, client, settings
    ) -> None:
        """The station is capturing correctly; what is wrong is its account of
        what it holds. Degrading for that would flip
        `binary_sensor.<station>_station_healthy` in Home Assistant and teach
        an operator to ignore it -- but staying silent is the honesty failure
        the charter names, so it is a note."""
        import uuid as _uuid
        from datetime import UTC, datetime

        from open_observatory.db import models as orm
        from open_observatory.db.session import session_scope

        with session_scope() as session:
            session.add(
                orm.MediaAsset(
                    id=_uuid.uuid4(),
                    kind="evidence_native",
                    storage_uri=str(settings.clip_dir / "gone" / "vanished.wav"),
                    mime_type="audio/wav",
                    byte_length=1_000_000,
                    sha256="0" * 64,
                    created_at=datetime.now(UTC),
                )
            )
        client.app.state.station.retention.audit_missing_files()

        payload = client.get("/api/v1/health").json()
        assert payload["storage"]["rows_claiming_missing_files"] == 1
        assert any("not on disk" in note for note in payload["notes"])
        assert not any("not on disk" in problem for problem in payload["problems"])
        assert all("missing" not in problem for problem in payload["problems"])


class TestOperatorPause:
    """The privacy pause over real HTTP, against the real pipeline (ADR-055).

    The unit-level properties are in `tests/test_pause.py`. What is only
    provable here is the part an operator would actually be betrayed by: that a
    paused station refuses to let anyone listen to the garden, that it writes
    nothing while paused, and that capture keeps running throughout.
    """

    def test_the_control_reports_its_menu_and_its_state_in_one_request(self, client) -> None:
        """One request, so the button cannot offer a duration whose state it
        read from somewhere else."""
        payload = client.get("/api/v1/pause").json()

        assert payload["active"] is False
        assert payload["ends_utc"] is None
        assert [preset["key"] for preset in payload["presets"]] == [
            "15m",
            "1h",
            "3h",
            "6h",
            "until-midnight",
        ]
        assert payload["default_preset"] == "1h"
        # "until midnight" is the only one the browser could not compute for
        # itself, and it is reported as such rather than as a number.
        assert payload["presets"][-1]["seconds"] is None

    def test_pausing_and_resuming_round_trips(self, client) -> None:
        paused = client.post("/api/v1/pause", json={"preset": "1h"}).json()
        assert paused["active"] is True
        assert paused["preset"] == "1h"
        assert 3500 < paused["remaining_s"] <= 3600
        assert "PAUSED" in paused["banner"]

        assert client.get("/api/v1/pause").json()["active"] is True

        resumed = client.delete("/api/v1/pause").json()
        assert resumed["active"] is False
        assert resumed["ends_utc"] is None
        assert client.get("/api/v1/pause").json()["active"] is False

    def test_resuming_when_not_paused_is_not_an_error(self, client) -> None:
        """One click to resume, by requirement, and therefore no confirmation
        step and no state in which the button can fail."""
        assert client.delete("/api/v1/pause").status_code == 200

    def test_an_unknown_duration_is_refused_and_changes_nothing(self, client) -> None:
        response = client.post("/api/v1/pause", json={"preset": "forever"})
        assert response.status_code == 422
        assert "unknown pause duration" in response.json()["detail"]
        assert client.get("/api/v1/pause").json()["active"] is False

    def test_live_listening_is_refused_while_paused(self, client) -> None:
        """The one that makes "paused" true rather than merely displayed.

        A pause anyone with the URL can listen straight through is not a pause:
        the garden is exactly as audible as it was and the operator has been
        told otherwise. The WAV channel is what the shipped UI actually uses,
        so it is the one that has to say no.

        Only the refusal is exercised here, never the success case. A
        *successful* `GET /live/audio.wav` returns a genuinely infinite
        generator, and Starlette's synchronous `TestClient` blocks until the
        ASGI app returns -- see SETUP.md trap 3, which is why three tests in
        `TestLiveChannels` are marked skip. That the endpoint streams when the
        station is recording is covered there; what is new and testable here is
        that it refuses when the station is paused, which returns immediately
        because the `HTTPException` is raised before any response body exists.
        """
        client.post("/api/v1/pause", json={"preset": "1h"})
        try:
            refused = client.get("/api/v1/live/audio.wav")
            assert refused.status_code == 503
            assert "PAUSED" in refused.json()["detail"]
            # The ultrasonic channel too -- a bat monitor is still a microphone
            # in a garden.
            assert client.get("/api/v1/live/audio.wav?channel=ultrasonic").status_code == 503
        finally:
            client.delete("/api/v1/pause")

        # And resuming genuinely reopens the gate. Asserted on the station's
        # own state rather than by re-requesting the stream, for the reason
        # above.
        assert client.app.state.station.pause.active is False

    def test_the_listen_socket_refuses_before_attaching_a_listener(self, client) -> None:
        """Refused with a reason rather than accepted and served silence: an
        open socket that plays nothing is indistinguishable from a broken
        station, which is the confusion this whole feature exists to avoid."""
        client.post("/api/v1/pause", json={"preset": "1h"})
        try:
            with client.websocket_connect("/api/v1/live/audio") as socket:
                hello = socket.receive_json()
            assert hello["available"] is False
            assert hello["paused"] is True
            assert "PAUSED" in hello["reason"]
            # No listener was attached, so the broadcaster is untouched.
            assert client.get("/api/v1/station").json()["live_audio"]["listeners"] == 0
        finally:
            client.delete("/api/v1/pause")

    def test_nothing_is_persisted_while_paused_and_capture_never_stops(self, client) -> None:
        """Capture keeps running -- frames keep arriving, the device is never
        closed -- and yet no detection row appears. That pair is the whole
        design: the microphone cannot fail to come back, because it never went
        away."""
        import time as _time

        from sqlalchemy import func as _func

        from open_observatory.db import models as orm
        from open_observatory.db.session import session_scope

        def _count() -> int:
            with session_scope() as session:
                return int(
                    session.execute(select(_func.count(orm.Detection.id))).scalar_one()
                )

        client.post("/api/v1/pause", json={"preset": "1h"})
        try:
            frames_before = client.get("/api/v1/station").json()["capture"]["frames"]
            _time.sleep(0.5)
            before = _count()
            _time.sleep(3.0)
            after = _count()
            snapshot = client.get("/api/v1/station").json()

            assert after == before, "a paused station wrote detection rows"
            assert snapshot["capture"]["frames"] > frames_before, (
                "capture stopped during a pause, which it must never do -- closing "
                "the device risks it not coming back (HANDOVER section 3a)"
            )
            assert snapshot["pause"]["active"] is True
        finally:
            client.delete("/api/v1/pause")

    def test_health_says_so_as_a_note_and_never_as_a_fault(self, client) -> None:
        """`problems` would flip `binary_sensor.<station>_station_healthy` and
        make every alerting rule in the house treat a birthday party as an
        outage. Saying nothing would be the quiet omission the honesty
        constraint forbids. So: a note."""
        client.post("/api/v1/pause", json={"preset": "1h"})
        try:
            payload = client.get("/api/v1/health").json()
            assert payload["pause"]["active"] is True
            assert any("paused by the operator" in note for note in payload["notes"])
            assert not any("paused" in problem.lower() for problem in payload["problems"])
        finally:
            client.delete("/api/v1/pause")

    def test_the_counter_top_display_is_told(self, client) -> None:
        """ADR-038's channel. The display is the everyday surface, and somebody
        glancing at it must not read a paused garden as a silent one."""
        from open_observatory.display_channel import health_state

        client.post("/api/v1/pause", json={"preset": "1h"})
        try:
            state, detail = health_state(client.get("/api/v1/health").json())
            assert state == "D"
            assert "PAUSED" in detail
        finally:
            client.delete("/api/v1/pause")

    def test_metrics_distinguish_a_paused_station_from_a_dead_one(self, client) -> None:
        client.post("/api/v1/pause", json={"preset": "1h"})
        try:
            body = client.get("/metrics").text
            assert "oo_paused 1.0" in body
            # Capture is still reported as running: an alert that cannot tell
            # these apart is charter item 2's failure in Prometheus form.
            assert "oo_capture_state 1.0" in body
        finally:
            client.delete("/api/v1/pause")

    def test_history_coverage_shows_the_pause_as_deliberate(self, client) -> None:
        client.post("/api/v1/pause", json={"preset": "1h"})
        try:
            coverage = client.get("/api/v1/history?window=today").json()["coverage"]
            assert len(coverage["pauses"]) == 1
            assert coverage["pauses"][0]["running"] is True
            # Reported as its own figure alongside capture, never folded into
            # it. (That it is not *subtracted* is proved against a seeded
            # window in `tests/test_pause.py`; a station a few seconds old has
            # no meaningful capture total to compare against yet.)
            assert "seconds_paused" in coverage
            assert coverage["seconds_paused"] >= 0
            assert "seconds_captured" in coverage
        finally:
            client.delete("/api/v1/pause")


class TestPauseSurvivesARestart:
    """A reboot mid-party must come back paused, not recording.

    Built as a genuine second process lifetime rather than by poking the
    controller: a whole new `create_app` against the same database, with the
    first one shut down. That is what the Pi does, and it is the only way to
    prove a deadline was persisted rather than held in memory.
    """

    def test_a_second_app_against_the_same_database_comes_back_paused(self, settings) -> None:
        from open_observatory.config import set_settings

        configured = settings.model_copy(
            update={"source": "synthetic", "timezone": "Europe/London"}
        )
        set_settings(configured)

        with TestClient(create_app(configured)) as first:
            paused = first.post("/api/v1/pause", json={"preset": "6h"}).json()
            assert paused["active"] is True
            deadline = paused["ends_utc"]

        # ... the station restarts ...
        set_settings(configured)
        with TestClient(create_app(configured)) as second:
            restored = second.get("/api/v1/pause").json()
            assert restored["active"] is True, "a restart resumed recording mid-pause"
            assert restored["ends_utc"] == deadline, "the deadline moved across the restart"
            # And the gates are genuinely closed in the new process, not merely
            # reported: this is what makes a restored pause a pause rather than
            # a status field.
            assert second.get("/api/v1/live/audio.wav").status_code == 503
            second.delete("/api/v1/pause")

    def test_a_pause_that_expired_while_the_station_was_down_does_not_come_back(
        self, settings
    ) -> None:
        """The other half, and the one an operator would be angrier about: a
        pause set at 3pm must not still be suppressing detections when the
        station is restarted the following week."""
        from datetime import timedelta

        from open_observatory.config import set_settings
        from open_observatory.db import models as orm
        from open_observatory.db.session import session_scope

        configured = settings.model_copy(update={"source": "synthetic"})
        set_settings(configured)

        with TestClient(create_app(configured)) as first:
            first.get("/api/v1/pause")
            past = datetime.now(UTC) - timedelta(hours=3)
            with session_scope() as session:
                session.add(
                    orm.CapturePause(
                        id=uuid.uuid4(),
                        started_utc=past,
                        ends_utc=past + timedelta(hours=1),
                        preset="1h",
                        label="1 hour",
                    )
                )

        set_settings(configured)
        with TestClient(create_app(configured)) as second:
            assert second.get("/api/v1/pause").json()["active"] is False
            # The gate is genuinely open, checked on the station rather than by
            # requesting the stream -- see `test_live_listening_is_refused_
            # while_paused` for why a successful WAV request cannot be made
            # from the synchronous `TestClient`.
            assert second.app.state.station.pause.active is False
