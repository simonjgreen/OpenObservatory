"""Validates a real emitted detection.created event against the wire schema.

HANDOVER.md section 6.3 item 9: the schema had drifted from what
CanonicalDetection.to_dict() actually emits (missing `rank`, `taxonomic_group`,
`additionalProperties: false`). This test builds a detection the same way the
real pipeline does -- through Normaliser, not a hand-written fixture dict --
wraps it in the real bus envelope via events.make_event, and validates that
exact payload against schemas/detection-event.schema.json, so the schema
cannot silently drift from reality again without a test failure.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import jsonschema
import numpy as np
import pytest

from open_observatory.audio.contracts import (
    NS_PER_S,
    AudioWindow,
    DetectorMetadata,
    NativeDetection,
    WindowSpec,
)
from open_observatory.events import make_event
from open_observatory.normaliser import Normaliser
from open_observatory.station import DetectionRecord

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "detection-event.schema.json"
AUDIBLE = WindowSpec(stream_kind="audible48", sample_rate=48000, duration_s=1.0, stride_s=0.5)


@pytest.fixture
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _birdnet_metadata() -> DetectorMetadata:
    return DetectorMetadata(
        plugin_id="birdnet-v2.4",
        plugin_version="2.4.0",
        model_id="birdnet-v2.4-uk",
        model_version="1",
        model_sha256="a" * 64,
        taxonomy_version="2024.1",
        licence_name="CC-BY-NC-SA-4.0",
        licence_url="https://example.invalid/licence",
        claim="bird species identification",
        calibrated=False,
    )


def _window() -> AudioWindow:
    utc0 = 1_700_000_000 * NS_PER_S
    return AudioWindow(
        window_id=uuid.uuid4(),
        stream_id=uuid.uuid4(),
        stream_kind="audible48",
        sample_rate=48000,
        start_frame=48000,
        end_frame=96000,
        native_start_frame=48000 * 8,
        native_end_frame=96000 * 8,
        utc_start_ns=utc0,
        utc_end_ns=utc0 + NS_PER_S,
        monotonic_start_ns=0,
        pcm=np.zeros(48000, dtype=np.float32),
        spec=AUDIBLE,
        created_monotonic_ns=0,
    )


def _build_real_detection_event(*, with_media: bool = False) -> dict:
    """Round-trip a detection through the real Normaliser, exactly as station.py does."""
    normaliser = Normaliser()
    detection = NativeDetection(
        offset_start_s=0.1,
        offset_end_s=0.9,
        score=0.62,
        label="Tawny Owl",
        common_name="Tawny Owl",
        scientific_name="Strix aluco",
        rank="species",
        taxonomic_group="bird",
    )
    canonical = normaliser.normalise(_birdnet_metadata(), _window(), detection, native_sample_rate=384000)
    assert canonical is not None
    record = DetectionRecord(detection=canonical)
    if with_media:
        record.media.append(
            {
                "id": str(uuid.uuid4()),
                "kind": "audio/wav",
                "role": "evidence",
                "description": "evidence clip",
                "sample_rate": 384000,
                "duration_s": 6.0,
                "byte_length": 1234,
                "sha256": "b" * 64,
                "url": "/api/v1/media/00000000-0000-0000-0000-000000000000",
            }
        )
    return make_event(
        "detection.created", record.to_dict(), station_id=uuid.uuid4(), occurred_at=None
    )


class TestSchemaShape:
    def test_schema_version_is_1_1(self, schema: dict) -> None:
        assert schema["properties"]["schema_version"]["enum"] == ["1.0", "1.1"]

    def test_data_declares_rank_and_taxonomic_group(self, schema: dict) -> None:
        data_props = schema["properties"]["data"]["properties"]
        assert "rank" in data_props
        assert "taxonomic_group" in data_props
        assert schema["properties"]["data"]["required"] == [
            "id",
            "detector",
            "stream_id",
            "window_id",
            "event_start_utc",
            "event_end_utc",
            "source_start_frame",
            "source_end_frame",
            "taxonomic_group",
            "score",
            "native_result",
        ]


class TestRealEventValidatesAgainstSchema:
    def test_species_detection_without_media(self, schema: dict) -> None:
        event = _build_real_detection_event(with_media=False)
        assert event["data"]["rank"] == "species"
        assert event["data"]["taxonomic_group"] == "bird"
        jsonschema.validate(event, schema)

    def test_species_detection_with_media(self, schema: dict) -> None:
        event = _build_real_detection_event(with_media=True)
        jsonschema.validate(event, schema)

    def test_a_field_the_1_0_schema_would_have_rejected_now_validates(self, schema: dict) -> None:
        """Direct regression check for HANDOVER.md 6.3 item 9."""
        event = _build_real_detection_event(with_media=False)
        assert "rank" in event["data"]
        assert "taxonomic_group" in event["data"]
        jsonschema.validate(event, schema)  # would previously fail additionalProperties

    def test_non_taxonomic_activity_detection_validates(self, schema: dict) -> None:
        normaliser = Normaliser()
        detection = NativeDetection(
            offset_start_s=0.0,
            offset_end_s=0.3,
            score=21.4,
            label="acoustic-transient",
            taxonomic_group="acoustic_event",
        )
        metadata = DetectorMetadata(
            plugin_id="activity-v1",
            plugin_version="1.0.0",
            model_id="activity-v1",
            model_version="1",
            model_sha256=None,
            taxonomy_version=None,
            licence_name="Apache-2.0",
            licence_url=None,
            claim="broadband acoustic activity, non-taxonomic",
            calibrated=False,
        )
        canonical = normaliser.normalise(metadata, _window(), detection, native_sample_rate=48000)
        assert canonical is not None
        record = DetectionRecord(detection=canonical)
        event = make_event("detection.created", record.to_dict(), station_id=uuid.uuid4())
        jsonschema.validate(event, schema)
