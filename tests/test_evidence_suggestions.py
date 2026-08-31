"""Suggesting additions to the common-species list, without nagging (ADR-074).

A species is suggested when, over the trailing 30 days, it produced more than
500 detections *and* more than 2% of evidence bytes, is not already common,
not dismissed, and not on the implausible list.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy.event as sa_event

from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope
from open_observatory.evidence_suggestions import MIN_DETECTIONS, compute_suggestions

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(settings):
    init_engine(settings)
    create_all()
    return settings


@pytest.fixture
def station_and_detector(db):
    station_id = uuid.uuid4()
    detector_id = uuid.uuid4()
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
    return station_id, detector_id


def _seed(
    session,
    *,
    station_id: uuid.UUID,
    detector_id: uuid.UUID,
    common_name: str,
    count: int,
    byte_length: int,
    age_days: float = 1.0,
    taxonomic_group: str = "bird",
) -> None:
    """Insert `count` detections for one species, each with one media asset,
    with no files on disk -- these tests never touch the filesystem, only the
    byte-total and detection-count aggregates.
    """
    event_start = NOW - timedelta(days=age_days)
    for _ in range(count):
        detection_id = uuid.uuid4()
        session.add(
            orm.Detection(
                id=detection_id,
                station_id=station_id,
                detector_id=detector_id,
                stream_id=uuid.uuid4(),
                window_id=uuid.uuid4(),
                event_start_utc=event_start,
                event_end_utc=event_start + timedelta(seconds=3),
                source_start_frame=0,
                source_end_frame=1,
                detector_label=common_name,
                common_name=common_name,
                scientific_name=f"Sci {common_name}",
                taxonomic_group=taxonomic_group,
                score=0.8,
            )
        )
        asset_id = uuid.uuid4()
        session.add(
            orm.MediaAsset(
                id=asset_id,
                kind="evidence_native",
                storage_uri=f"/tmp/{detection_id}.wav",
                mime_type="audio/wav",
                byte_length=byte_length,
                sha256="0" * 64,
                created_at=event_start,
            )
        )
        session.add(
            orm.DetectionMedia(detection_id=detection_id, media_asset_id=asset_id, role="evidence")
        )


# A greenfinch-shaped burst: comfortably over both thresholds against a small
# total archive.
GREENFINCH_COUNT = MIN_DETECTIONS + 704
GREENFINCH_BYTES = 2_500_000
PADDING_BYTES = 40_000_000  # keeps the greenfinch share under 100% but over 2%


def test_a_qualifying_species_is_suggested(db, station_and_detector) -> None:
    station_id, detector_id = station_and_detector
    with session_scope() as session:
        _seed(
            session,
            station_id=station_id,
            detector_id=detector_id,
            common_name="European Greenfinch",
            count=GREENFINCH_COUNT,
            byte_length=GREENFINCH_BYTES // GREENFINCH_COUNT,
        )
        _seed(
            session,
            station_id=station_id,
            detector_id=detector_id,
            common_name="Padding Species",
            count=10,
            byte_length=PADDING_BYTES // 10,
        )

    with session_scope() as session:
        suggestions = compute_suggestions(
            session,
            common_species=(),
            implausible_species=(),
            dismissed_species=(),
            now=NOW,
        )

    names = {s.common_name for s in suggestions}
    assert "European Greenfinch" in names
    greenfinch = next(s for s in suggestions if s.common_name == "European Greenfinch")
    assert greenfinch.detection_count == GREENFINCH_COUNT
    assert greenfinch.window_days == 30


def test_a_species_under_the_detection_threshold_is_not_suggested(db, station_and_detector) -> None:
    station_id, detector_id = station_and_detector
    with session_scope() as session:
        _seed(
            session,
            station_id=station_id,
            detector_id=detector_id,
            common_name="European Greenfinch",
            count=MIN_DETECTIONS,  # exactly at the line: "more than 500" excludes it
            byte_length=100_000,
        )

    with session_scope() as session:
        suggestions = compute_suggestions(
            session, common_species=(), implausible_species=(), dismissed_species=(), now=NOW
        )
    assert suggestions == []


def test_a_species_under_the_byte_fraction_threshold_is_not_suggested(
    db, station_and_detector
) -> None:
    station_id, detector_id = station_and_detector
    with session_scope() as session:
        # Well over the count threshold, but a byte share far under 2% of a
        # much bigger archive.
        _seed(
            session,
            station_id=station_id,
            detector_id=detector_id,
            common_name="European Greenfinch",
            count=MIN_DETECTIONS + 1,
            byte_length=1,
        )
        _seed(
            session,
            station_id=station_id,
            detector_id=detector_id,
            common_name="Padding Species",
            count=10,
            byte_length=10_000_000,
        )

    with session_scope() as session:
        suggestions = compute_suggestions(
            session, common_species=(), implausible_species=(), dismissed_species=(), now=NOW
        )
    assert suggestions == []


def test_an_already_common_species_is_never_suggested(db, station_and_detector) -> None:
    station_id, detector_id = station_and_detector
    with session_scope() as session:
        _seed(
            session,
            station_id=station_id,
            detector_id=detector_id,
            common_name="Common Woodpigeon",
            count=GREENFINCH_COUNT,
            byte_length=GREENFINCH_BYTES // GREENFINCH_COUNT,
        )

    with session_scope() as session:
        suggestions = compute_suggestions(
            session,
            common_species=("Common Woodpigeon",),
            implausible_species=(),
            dismissed_species=(),
            now=NOW,
        )
    assert suggestions == []


def test_a_dismissed_species_is_never_suggested_again(db, station_and_detector) -> None:
    """ADR-074: 'a prompt that returns after being declined is a prompt that
    gets ignored along with everything else on the page.'"""
    station_id, detector_id = station_and_detector
    with session_scope() as session:
        _seed(
            session,
            station_id=station_id,
            detector_id=detector_id,
            common_name="European Greenfinch",
            count=GREENFINCH_COUNT,
            byte_length=GREENFINCH_BYTES // GREENFINCH_COUNT,
        )

    with session_scope() as session:
        suggestions = compute_suggestions(
            session,
            common_species=(),
            implausible_species=(),
            dismissed_species=("european greenfinch",),  # case-insensitive
            now=NOW,
        )
    assert suggestions == []


def test_an_implausible_species_is_never_suggested(db, station_and_detector) -> None:
    """ADR-074/076: a false-positive burst must be investigated, not silenced
    by adding the species to the boring list."""
    station_id, detector_id = station_and_detector
    with session_scope() as session:
        _seed(
            session,
            station_id=station_id,
            detector_id=detector_id,
            common_name="California Quail",
            count=GREENFINCH_COUNT,
            byte_length=GREENFINCH_BYTES // GREENFINCH_COUNT,
        )

    with session_scope() as session:
        suggestions = compute_suggestions(
            session,
            common_species=(),
            implausible_species=("California Quail",),
            dismissed_species=(),
            now=NOW,
        )
    assert suggestions == []


def test_acoustic_events_are_never_suggested(db, station_and_detector) -> None:
    """ADR-077: acoustic events keep no clips at all, so suggesting one to
    the *common bird species* list would be meaningless."""
    station_id, detector_id = station_and_detector
    with session_scope() as session:
        _seed(
            session,
            station_id=station_id,
            detector_id=detector_id,
            common_name="Engine",
            count=GREENFINCH_COUNT,
            byte_length=GREENFINCH_BYTES // GREENFINCH_COUNT,
            taxonomic_group="acoustic_event",
        )

    with session_scope() as session:
        suggestions = compute_suggestions(
            session, common_species=(), implausible_species=(), dismissed_species=(), now=NOW
        )
    assert suggestions == []


def test_detections_outside_the_thirty_day_window_do_not_count(db, station_and_detector) -> None:
    station_id, detector_id = station_and_detector
    with session_scope() as session:
        _seed(
            session,
            station_id=station_id,
            detector_id=detector_id,
            common_name="European Greenfinch",
            count=GREENFINCH_COUNT,
            byte_length=GREENFINCH_BYTES // GREENFINCH_COUNT,
            age_days=31,
        )

    with session_scope() as session:
        suggestions = compute_suggestions(
            session, common_species=(), implausible_species=(), dismissed_species=(), now=NOW
        )
    assert suggestions == []


def test_a_reclaimed_asset_costs_nothing_either_side_of_the_fraction(
    db, station_and_detector
) -> None:
    """A clip retention already deleted must not inflate the numerator, and
    must not inflate the denominator either -- it no longer costs any disk."""
    station_id, detector_id = station_and_detector
    with session_scope() as session:
        _seed(
            session,
            station_id=station_id,
            detector_id=detector_id,
            common_name="European Greenfinch",
            count=GREENFINCH_COUNT,
            byte_length=GREENFINCH_BYTES // GREENFINCH_COUNT,
        )
        # Reclaim every greenfinch asset.
        for asset in session.query(orm.MediaAsset).all():
            asset.reclaimed_at = NOW
            asset.reclaim_reason = "native"

    with session_scope() as session:
        suggestions = compute_suggestions(
            session, common_species=(), implausible_species=(), dismissed_species=(), now=NOW
        )
    assert suggestions == []


class TestQueryPlans:
    """The regression this module exists to fix: on the production schema,
    both of the original two aggregates compiled to `SCAN detection_media`
    on the *whole* table, every call, because their only time bound sat on
    `detection`, the far side of the join -- `WINDOW_DAYS` bounded nothing on
    a station younger than the window. Two calls from a settings page load
    measured at ~35 s combined and cost the live station ~41 s of dropped
    audio. A functional test cannot catch this: a small fixture makes a table
    scan free, and the answers were correct throughout -- which is exactly
    why the original suite passed.

    This captures every statement `compute_suggestions` actually issues, on a
    zero-row (no `ANALYZE` statistics) schema database -- reproducing the
    station's own plans exactly -- and asserts none of them scans `detection`
    or `detection_media`.
    """

    def test_no_query_scans_detection_or_detection_media(
        self, db, station_and_detector
    ) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _seed(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name="European Greenfinch",
                count=GREENFINCH_COUNT,
                byte_length=GREENFINCH_BYTES // GREENFINCH_COUNT,
            )

        from open_observatory.db.session import _engine

        statements: list[tuple[str, object]] = []

        def capture(conn, cursor, statement, parameters, *_a):
            if statement.strip().upper().startswith("SELECT"):
                statements.append((statement, parameters))

        sa_event.listen(_engine, "before_cursor_execute", capture)
        try:
            with session_scope() as session:
                compute_suggestions(
                    session,
                    common_species=(),
                    implausible_species=(),
                    dismissed_species=(),
                    now=NOW,
                )
        finally:
            sa_event.remove(_engine, "before_cursor_execute", capture)

        assert statements, "compute_suggestions issued no SELECT to check"

        raw = _engine.raw_connection()
        try:
            for statement, parameters in statements:
                cursor = raw.cursor()
                cursor.execute(f"EXPLAIN QUERY PLAN {statement}", parameters)
                plan = [row[-1] for row in cursor.fetchall()]
                for detail in plan:
                    assert "SCAN detection_media" not in detail, (statement, plan)
                    assert not detail.startswith("SCAN detection"), (statement, plan)
        finally:
            raw.close()
