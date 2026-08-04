"""End-to-end tests through the real FastAPI app and a real pipeline.

Nothing is mocked. The app runs the whole station against the synthetic capture
source, so these exercise capture → resample → segment → detect → normalise →
persist → serve, which is the path the debug UI actually depends on.
"""

from __future__ import annotations

import asyncio
import struct

import pytest
from fastapi.testclient import TestClient

from open_observatory.api.app import create_app
from open_observatory.audio.spectrogram import decode_header_size


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
        assert ultrasonic["columns_emitted"] > 0

    def test_detectors_declare_their_claims_and_licences(self, client) -> None:
        detectors = client.get("/api/v1/detectors").json()["detectors"]
        by_id = {entry["plugin_id"]: entry for entry in detectors}
        assert "activity-v1" in by_id
        for entry in detectors:
            assert entry["claim"], f"{entry['plugin_id']} has no stated claim"
            assert entry["licence_name"]
            assert isinstance(entry["calibrated"], bool)

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
        """The whole vertical slice, end to end."""
        import time

        detections: list = []
        for _ in range(60):
            detections = client.get("/api/v1/detections?limit=50").json()["detections"]
            if detections:
                break
            time.sleep(0.5)
        assert detections, "the synthetic dawn chorus produced no detections at all"

        first = detections[0]
        assert first["event_start_utc"].endswith("Z")
        assert first["source_end_frame"] > first["source_start_frame"]
        assert 0.0 <= first["score"] <= 1.0
        assert first["detector"]["plugin_id"]

        detail = client.get(f"/api/v1/detections/{first['id']}").json()
        assert detail["id"] == first["id"]
        # The detector's own output is preserved verbatim.
        assert detail["native_result"]

    def test_activity_detections_never_carry_taxonomy(self, client) -> None:
        import time

        for _ in range(40):
            rows = client.get(
                "/api/v1/detections?limit=200&plugin_id=activity-v1"
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
            for row in client.get("/api/v1/detections?limit=100").json()["detections"]:
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
        payload = client.get("/api/v1/taxa/activity?hours=24").json()
        assert "entries" in payload
        for entry in payload["entries"]:
            assert entry["detections"] >= 1
            assert entry["display_name"]

    def test_streams_and_gaps_endpoints(self, client) -> None:
        streams = client.get("/api/v1/streams").json()["streams"]
        assert streams
        assert streams[0]["sample_rate"] == 192000
        assert "gaps" in client.get("/api/v1/gaps").json()


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
            assert event["schema_version"] == "1.0"
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
