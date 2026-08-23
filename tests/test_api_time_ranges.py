"""`since`/`until` on the read endpoints: naive input must never be a 500.

`GET /api/v1/history?since=2026-08-04&until=2026-08-09` returned 500 on the
live station while the same window written as `since=2026-08-04T00:00:00Z`
returned 200. A bare `datetime` query parameter accepts a date-only or
offsetless value and parses it to a *naive* datetime; `history.coverage` then
compares it against stream timestamps it has just made aware, and Python
raises `TypeError: can't compare offset-naive and offset-aware datetimes`.

The failure needs a stream row overlapping the requested window to appear at
all -- an empty window never reaches the comparison -- which is why it hid from
the endpoint's existing tests and showed up only against a station with real
history.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from open_observatory.api.app import create_app
from open_observatory.config import Settings, set_settings
from open_observatory.db import models as orm
from open_observatory.db.session import session_scope

#: A window wholly in the past, so it is only non-empty because of the stream
#: seeded into it below.
WINDOW_START = datetime(2026, 8, 4, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 9, tzinfo=UTC)


@pytest.fixture
def client(settings: Settings):
    set_settings(settings)
    app = create_app(settings)
    with TestClient(app) as test_client:
        with session_scope() as session:
            session.add(
                orm.AudioStream(
                    id=uuid.uuid4(),
                    source_kind="alsa",
                    start_utc=WINDOW_START + timedelta(hours=1),
                    end_utc=WINDOW_START + timedelta(hours=3),
                    start_monotonic_ns=0,
                    sample_rate=48000,
                    sample_format="FLOAT_LE",
                    frame_count=48000 * 7200,
                )
            )
        yield test_client


@pytest.mark.parametrize(
    ("since", "until"),
    [
        # Date-only: the form a person types, and the form that 500ed.
        ("2026-08-04", "2026-08-09"),
        # Naive, fully specified: same defect, no `Z`.
        ("2026-08-04T00:00:00", "2026-08-09T00:00:00"),
        # Aware: the form the UI sends. Must keep working unchanged.
        ("2026-08-04T00:00:00Z", "2026-08-09T00:00:00Z"),
        # Aware in another offset: honoured and converted, not second-guessed.
        ("2026-08-04T01:00:00+01:00", "2026-08-09T01:00:00+01:00"),
    ],
)
@pytest.mark.parametrize("path", ["/api/v1/history", "/api/v1/detections"])
def test_naive_and_aware_ranges_agree_and_never_500(
    client: TestClient, path: str, since: str, until: str
) -> None:
    response = client.get(path, params={"since": since, "until": until})
    assert response.status_code == 200, response.text


def test_the_window_is_reported_back_as_utc_whatever_form_it_arrived_in(
    client: TestClient,
) -> None:
    """All four spellings of the same instant resolve to one UTC range, so a
    caller cannot get a different answer by writing the time differently."""
    resolved = {
        client.get("/api/v1/history", params={"since": since, "until": until}).json()["range"]["start_utc"]
        for since, until in (
            ("2026-08-04", "2026-08-09"),
            ("2026-08-04T00:00:00", "2026-08-09T00:00:00"),
            ("2026-08-04T00:00:00Z", "2026-08-09T00:00:00Z"),
            ("2026-08-04T01:00:00+01:00", "2026-08-09T01:00:00+01:00"),
        )
    }
    assert resolved == {"2026-08-04T00:00:00Z"}


def test_unparseable_input_is_still_a_422_not_a_500(client: TestClient) -> None:
    """Coercion is for input that has a meaning; nonsense must still be
    refused by name rather than crashing or being quietly reinterpreted."""
    assert client.get("/api/v1/history", params={"since": "banana"}).status_code == 422
    assert client.get("/api/v1/detections", params={"until": "banana"}).status_code == 422
