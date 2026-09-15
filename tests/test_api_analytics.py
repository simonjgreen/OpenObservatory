"""The analytics endpoints (ADR-079) over HTTP: thin smoke on top of
`tests/test_analytics.py`, which tests the numbers. Here the questions are
that each route answers, in the shape the web UI reads, and that a station
with no roll-up yet answers honestly rather than with a 500."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from open_observatory import analytics
from open_observatory.api.app import create_app
from open_observatory.config import Settings, set_settings
from open_observatory.db import models as orm
from open_observatory.db.session import session_scope

NOW = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)


@pytest.fixture
def client(settings: Settings):
    configured = settings.model_copy(
        update={"timezone": "Europe/London", "latitude": 51.3, "longitude": -0.5}
    )
    set_settings(configured)
    app = create_app(configured)
    with TestClient(app) as test_client:
        yield test_client


def _seed_one_day() -> None:
    with session_scope() as session:
        station = orm.Station(name="t", timezone="Europe/London")
        detector = orm.Detector(plugin_id="birdnet-v2.4", plugin_version="1", model_id="m", model_version="1")
        session.add_all([station, detector])
        session.flush()
        stream = orm.AudioStream(
            id=uuid.uuid4(),
            source_kind="alsa",
            start_utc=datetime(2026, 8, 5, 0, 0, tzinfo=UTC),
            end_utc=datetime(2026, 8, 5, 23, 0, tzinfo=UTC),
            start_monotonic_ns=0,
            sample_rate=384000,
            sample_format="S16_LE",
            frame_count=384000 * 23 * 3600,
        )
        session.add(stream)
        session.flush()
        for i in range(3):
            session.add(
                orm.Detection(
                    station_id=station.id,
                    detector_id=detector.id,
                    stream_id=stream.id,
                    window_id=uuid.uuid4(),
                    event_start_utc=datetime(2026, 8, 5, 6, i, tzinfo=UTC),
                    event_end_utc=datetime(2026, 8, 5, 6, i, 3, tzinfo=UTC),
                    source_start_frame=0,
                    source_end_frame=1,
                    detector_label="European Robin",
                    common_name="European Robin",
                    scientific_name="Erithacus rubecula",
                    rank="species",
                    taxonomic_group="bird",
                    score=0.7,
                )
            )
        session.add(
            orm.Detection(
                station_id=station.id,
                detector_id=detector.id,
                stream_id=stream.id,
                window_id=uuid.uuid4(),
                event_start_utc=datetime(2026, 8, 5, 20, 30, tzinfo=UTC),
                event_end_utc=datetime(2026, 8, 5, 20, 30, 3, tzinfo=UTC),
                source_start_frame=0,
                source_end_frame=1,
                detector_label="bat pass",
                taxonomic_group="bat",
                score=0.8,
                peak_frequency_hz=45500,
            )
        )
    analytics.rebuild(timezone="Europe/London", latitude=51.3, longitude=-0.5, now=NOW, all_days=True)


def test_status_and_questions_before_any_rollup(client: TestClient) -> None:
    status = client.get("/api/v1/analytics/status")
    assert status.status_code == 200
    body = status.json()
    assert body["days_built"] == 0 and body["coordinates_set"] is True
    questions = client.get("/api/v1/analytics/questions").json()["questions"]
    assert any(q["id"] == "green-woodpecker-hour" for q in questions)
    # An empty roll-up answers with empty shapes, never a 500.
    for path in ("series?range=all", "hours?range=all", "taxa?range=all", "span?range=all"):
        response = client.get(f"/api/v1/analytics/{path}")
        assert response.status_code == 200, path


def test_every_view_answers_in_shape(client: TestClient) -> None:
    _seed_one_day()
    series = client.get("/api/v1/analytics/series?range=all&grain=day&group=bird").json()
    assert series["range"]["first_date"] == "2026-08-05"
    assert next(b["detections"] for b in series["buckets"]) == 3
    assert series["buckets"][0]["per_captured_hour"] is not None
    assert "excluded_withdrawn_count" in series

    hours = client.get("/api/v1/analytics/hours?range=all&columns=day&label=European%20Robin").json()
    assert hours["profile"][7] == 3  # 06:00 UTC is 07:00 BST
    assert hours["columns"][0]["solar"]["sunrise"] is not None

    taxa = client.get("/api/v1/analytics/taxa?range=all&grain=week&group=bat").json()
    assert taxa["rows"][0]["label"] == "45\u201350 kHz"
    assert taxa["rows"][0]["scientific_name"] is None

    span = client.get("/api/v1/analytics/span?range=2026-08-05&group=bird").json()
    day = span["days"][0]
    assert day["built"] is True and day["daylight_detections"] == 3
    assert span["coordinates_set"] is True

    # An unknown window name falls back rather than failing.
    assert client.get("/api/v1/analytics/series?range=nonsense").status_code == 200
    # A bad grain is coerced, per the 422-or-coerce rule at the top of app.py.
    assert client.get("/api/v1/analytics/series?range=all&grain=fortnight").json()["grain"] == "day"


def test_saved_reports_round_trip_over_http(client: TestClient) -> None:
    created = client.post(
        "/api/v1/analytics/reports",
        json={
            "name": "Robins by week",
            "question": "When are there most robins?",
            "view": "series",
            "params": {"range": "this-year", "grain": "week", "label": "European Robin"},
        },
    )
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]
    listed = client.get("/api/v1/analytics/reports").json()["reports"]
    assert [r["name"] for r in listed] == ["Robins by week"]
    assert listed[0]["params"]["label"] == "European Robin"
    bad = client.post("/api/v1/analytics/reports", json={"name": "x", "view": "pie", "params": {}})
    assert bad.status_code == 422
    assert client.delete(f"/api/v1/analytics/reports/{report_id}").status_code == 204
    assert client.delete(f"/api/v1/analytics/reports/{uuid.uuid4()}").status_code == 404
    assert client.get("/api/v1/analytics/reports").json()["reports"] == []


def test_rollup_status_reports_missing_days(client: TestClient) -> None:
    _seed_one_day()
    facts = client.get("/api/v1/analytics/status").json()
    assert facts["days_built"] >= 1
    assert facts["first_detection_date"] == "2026-08-05"
    assert facts["last_built_at"] is not None
