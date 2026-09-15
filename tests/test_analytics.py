"""ADR-079: the analytics roll-up and the questions asked of it.

Seeded like `test_history.py`: a real schema, real rows at fixed UTC instants
chosen to sit on either side of a local-midnight boundary in Europe/London,
so the one thing the roll-up must get right -- local calendar days -- is
exercised rather than assumed. Every value a test asserts on is one the
station could emit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from open_observatory import analytics, history, plausibility
from open_observatory.config import Settings
from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope

LONDON = "Europe/London"
ZONE = ZoneInfo(LONDON)
# Roughly Surrey. Sunrise on 2026-08-05 is about 05:35 BST, sunset about 20:40.
LAT, LON = 51.3, -0.5

DAY = date(2026, 8, 5)
#: 2026-08-04 23:30 UTC is 00:30 BST on the 5th: filed on the 5th, or the
#: roll-up has the bug ADR-056 measured at 15% of the station's bats.
JUST_AFTER_MIDNIGHT_LOCAL = datetime(2026, 8, 4, 23, 30, tzinfo=UTC)
NOW = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)


@pytest.fixture
def seeded(settings: Settings) -> Settings:
    init_engine(settings)
    create_all()
    station_id = uuid.uuid4()
    detector_id = uuid.uuid4()
    live_stream = uuid.uuid4()
    synthetic_stream = uuid.uuid4()
    with session_scope() as session:
        session.add(
            orm.Station(id=station_id, name="t", timezone=LONDON, latitude=LAT, longitude=LON)
        )
        session.add(
            orm.Detector(
                id=detector_id, plugin_id="birdnet-v2.4", plugin_version="1", model_id="m", model_version="1"
            )
        )
        # Flushed before anything that references them: the unit of work only
        # orders inserts along declared relationships, and these are plain keys.
        session.flush()
        # One live stream covering the whole of the 5th (local) and the
        # evening of the 4th; one synthetic stream on the 5th.
        start = datetime(2026, 8, 4, 18, 0, tzinfo=UTC)
        end = datetime(2026, 8, 5, 23, 0, tzinfo=UTC)
        seconds = int((end - start).total_seconds())
        session.add(
            orm.AudioStream(
                id=live_stream,
                source_kind="alsa",
                start_utc=start,
                end_utc=end,
                start_monotonic_ns=0,
                sample_rate=384000,
                sample_format="S16_LE",
                frame_count=384000 * seconds,
            )
        )
        session.add(
            orm.AudioStream(
                id=synthetic_stream,
                source_kind="synthetic",
                start_utc=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
                end_utc=datetime(2026, 8, 5, 11, 0, tzinfo=UTC),
                start_monotonic_ns=0,
                sample_rate=48000,
                sample_format="S16_LE",
                frame_count=48000 * 3600,
            )
        )
        # An operator pause of 30 minutes at 12:00 UTC on the 5th.
        session.add(
            orm.CapturePause(
                station_id=station_id,
                started_utc=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
                ends_utc=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
                ended_utc=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
                end_reason="expired",
            )
        )

        ids: dict[str, uuid.UUID] = {}

        def detection(
            key: str,
            at: datetime,
            group: str,
            name: str | None,
            score: float,
            *,
            stream: uuid.UUID = live_stream,
            peak: float | None = None,
            native: dict | None = None,
        ) -> None:
            row_id = uuid.uuid4()
            ids[key] = row_id
            session.add(
                orm.Detection(
                    id=row_id,
                    station_id=station_id,
                    detector_id=detector_id,
                    stream_id=stream,
                    window_id=uuid.uuid4(),
                    event_start_utc=at,
                    event_end_utc=at + timedelta(seconds=3),
                    source_start_frame=0,
                    source_end_frame=1,
                    detector_label=name or ("bat pass" if group == "bat" else "acoustic event"),
                    common_name=name,
                    scientific_name=f"Sci {name}" if name else None,
                    rank="species" if name else None,
                    taxonomic_group=group,
                    score=score,
                    peak_frequency_hz=peak,
                    native_result=native or {},
                )
            )

        morning = datetime(2026, 8, 5, 5, 0, tzinfo=UTC)  # 06:00 BST, after sunrise
        # Ten robins between 06:00 and 06:09 local, five woodpigeons at 07:00 local.
        for i in range(10):
            detection(f"robin{i}", morning + timedelta(minutes=i), "bird", "European Robin", 0.6 + i * 0.01)
        for i in range(5):
            detection(f"pigeon{i}", morning + timedelta(hours=1, minutes=i), "bird", "Common Woodpigeon", 0.9)
        # One bird at 20:00 local (19:00 UTC), before sunset; one at 03:00
        # local, before dawn, so first != p05.
        detection("evening", datetime(2026, 8, 5, 19, 0, tzinfo=UTC), "bird", "Eurasian Blackbird", 0.7)
        detection("stray", datetime(2026, 8, 5, 2, 0, tzinfo=UTC), "bird", "Eurasian Blackbird", 0.5)
        # A bird filed on the 5th only because the day is local: 00:30 BST.
        detection("midnight", JUST_AFTER_MIDNIGHT_LOCAL, "bird", "Tawny Owl", 0.8)
        # A withdrawn bird (ADR-044): counts as a detection, names nothing.
        detection(
            "withdrawn",
            morning + timedelta(minutes=30),
            "bird",
            "Western Screech-Owl",
            0.95,
            native={plausibility.REVIEW_KEY: {plausibility.WITHDRAWN_KEY: True}},
        )
        # A bird the operator corrected, and one they rejected.
        detection("corrected", morning + timedelta(minutes=40), "bird", "Willow Warbler", 0.55)
        detection("rejected", morning + timedelta(minutes=41), "bird", "Wood Warbler", 0.52)
        # A synthetic-source bird: excluded and counted.
        detection(
            "synthetic",
            datetime(2026, 8, 5, 10, 30, tzinfo=UTC),
            "bird",
            "Grey-winged Inca-Finch",
            0.9,
            stream=synthetic_stream,
        )
        # Bats: three passes after dusk on the 5th (21:30 BST = 20:30 UTC), one
        # in the small hours of the 5th (02:30 BST = 01:30 UTC) -- the morning
        # half of the night that started on the 4th.
        for i in range(3):
            detection(
                f"bat{i}", datetime(2026, 8, 5, 20, 30 + i, tzinfo=UTC), "bat", None, 0.8, peak=45000 + i
            )
        detection("bat-early", datetime(2026, 8, 5, 1, 30, tzinfo=UTC), "bat", None, 0.7, peak=52000)
        # Noise, 40 rows across the morning, and one on the evening of the 4th
        # (21:00 BST) so the record starts a day before any bird does.
        for i in range(40):
            detection(f"noise{i}", morning + timedelta(minutes=i), "acoustic_event", None, 0.3)
        detection("noise-4th", datetime(2026, 8, 4, 20, 0, tzinfo=UTC), "acoustic_event", None, 0.3)
        session.flush()
        session.add(
            orm.Review(
                detection_id=ids["corrected"],
                actor="operator",
                status="corrected",
                corrected_common_name="Common Chiffchaff",
                corrected_scientific_name="Phylloscopus collybita",
                created_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
            )
        )
        session.add(
            orm.Review(
                detection_id=ids["rejected"],
                actor="operator",
                status="rejected",
                created_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
            )
        )
    return settings


def _build(day: date = DAY, *, coordinates: bool = True, now: datetime = NOW) -> analytics.DayBuild:
    with session_scope() as session:
        return analytics.build_day(
            session,
            day,
            timezone=LONDON,
            latitude=LAT if coordinates else None,
            longitude=LON if coordinates else None,
            now=now,
        )


def test_local_days_are_the_stations_days(seeded: Settings) -> None:
    """00:30 BST on the 5th is the 5th; 23:30 UTC on the 4th is not the 4th."""
    _build(DAY)
    _build(DAY - timedelta(days=1))
    with session_scope() as session:
        owl_rows = session.execute(
            select(orm.AnalyticsTaxonHour).where(orm.AnalyticsTaxonHour.label == "Tawny Owl")
        ).scalars().all()
        assert [(r.local_date, r.hour) for r in owl_rows] == [("2026-08-05", 0)]
        fourth = session.get(orm.AnalyticsDay, "2026-08-04")
        assert fourth is not None and fourth.detections == 1
        assert fourth.seconds_from_microphone == pytest.approx(5 * 3600, abs=1)


def test_hours_are_local_and_groups_include_withdrawn(seeded: Settings) -> None:
    build = _build()
    # Every live row on the 5th, withdrawn included: 10+5+1+1+1+1+1+1 birds,
    # 4 bats, 40 noise. The synthetic finch is not among them.
    assert build.detections == 21 + 4 + 40
    assert build.excluded_synthetic == 1
    assert build.excluded_withdrawn == 1
    assert build.excluded_rejected == 1
    with session_scope() as session:
        hour_rows = {
            (r.hour, r.taxonomic_group): r.detections
            for r in session.execute(
                select(orm.AnalyticsHour).where(orm.AnalyticsHour.local_date == "2026-08-05")
            ).scalars()
        }
    # 05:00 UTC is 06:00 BST: the robins, the withdrawn owl, the two reviewed
    # warblers and the noise all land in local hour 6.
    assert hour_rows[(6, "bird")] == 10 + 1 + 1 + 1
    assert hour_rows[(6, "acoustic_event")] == 40
    assert hour_rows[(7, "bird")] == 5
    assert hour_rows[(21, "bat")] == 3
    assert hour_rows[(2, "bat")] == 1


def test_named_rows_honour_reviews_and_withdrawals(seeded: Settings) -> None:
    _build()
    with session_scope() as session:
        rows = session.execute(
            select(orm.AnalyticsTaxonHour).where(orm.AnalyticsTaxonHour.local_date == "2026-08-05")
        ).scalars().all()
    labels = {(r.taxonomic_group, r.label): r.detections for r in rows}
    assert labels[("bird", "European Robin")] == 10
    # The correction is what the roll-up names; the machine's name is gone.
    assert labels[("bird", "Common Chiffchaff")] == 1
    assert ("bird", "Willow Warbler") not in labels
    # Rejected and withdrawn claims are not named.
    assert ("bird", "Wood Warbler") not in labels
    assert ("bird", "Western Screech-Owl") not in labels
    # A bat pass is a frequency band, never a species.
    assert labels[("bat", "45\u201350 kHz")] == 3
    assert labels[("bat", "50\u201355 kHz")] == 1
    sci = {r.label: r.scientific_name for r in rows}
    assert sci["Common Chiffchaff"] == "Phylloscopus collybita"
    assert sci["45\u201350 kHz"] is None


def test_group_day_facts_and_solar_windows(seeded: Settings) -> None:
    _build()
    with session_scope() as session:
        birds = session.get(orm.AnalyticsGroupDay, {"local_date": "2026-08-05", "taxonomic_group": "bird"})
        bats = session.get(orm.AnalyticsGroupDay, {"local_date": "2026-08-05", "taxonomic_group": "bat"})
        day = session.get(orm.AnalyticsDay, "2026-08-05")
    assert birds is not None and bats is not None and day is not None
    assert birds.detections == 21
    # First is the owl at 00:30 local. With 21 birds the 5th percentile is
    # the second of them -- the stray 03:00 blackbird -- and the 95th is the
    # twentieth, the last woodpigeon at 07:04 local.
    aware = analytics._aware
    assert aware(birds.first_utc) == JUST_AFTER_MIDNIGHT_LOCAL
    assert birds.p05_utc is not None and aware(birds.p05_utc) == datetime(2026, 8, 5, 2, 0, tzinfo=UTC)
    assert birds.p95_utc is not None and aware(birds.p95_utc) == datetime(2026, 8, 5, 6, 4, tzinfo=UTC)
    assert aware(birds.last_utc) == datetime(2026, 8, 5, 19, 0, tzinfo=UTC)
    # Sunrise about 05:35 BST (04:35 UTC), sunset about 20:40 BST (19:40 UTC).
    assert day.sunrise_utc is not None and day.sunset_utc is not None
    assert datetime(2026, 8, 5, 4, 20, tzinfo=UTC) < aware(day.sunrise_utc)
    assert aware(day.sunrise_utc) < datetime(2026, 8, 5, 4, 50, tzinfo=UTC)
    assert datetime(2026, 8, 5, 19, 25, tzinfo=UTC) < aware(day.sunset_utc)
    assert aware(day.sunset_utc) < datetime(2026, 8, 5, 19, 55, tzinfo=UTC)
    # Everything but the owl and the stray happened in daylight.
    assert birds.daylight_detections == 19
    # The evening bats are after civil dusk; the early one is before civil dawn.
    assert bats.dusk_to_midnight_detections == 3
    assert bats.midnight_to_dawn_detections == 1
    assert day.dusk_to_midnight_captured_seconds > 0
    assert day.complete is True


def test_coverage_hours_include_the_pause_and_exclude_synthetic(seeded: Settings) -> None:
    _build()
    with session_scope() as session:
        rows = {
            r.hour: r
            for r in session.execute(
                select(orm.AnalyticsCoverageHour).where(
                    orm.AnalyticsCoverageHour.local_date == "2026-08-05"
                )
            ).scalars()
        }
        day = session.get(orm.AnalyticsDay, "2026-08-05")
    assert len(rows) == 24
    # 12:00-12:30 UTC is 13:00-13:30 BST.
    assert rows[13].seconds_paused == pytest.approx(1800, abs=1)
    assert rows[13].seconds_from_microphone == pytest.approx(3600, abs=1)
    # The live stream ends at 23:00 UTC = 00:00 BST on the 6th: the whole
    # local day is covered; the synthetic hour adds nothing to the microphone.
    assert day is not None
    assert day.seconds_from_microphone == pytest.approx(24 * 3600, abs=1)
    assert day.seconds_captured == pytest.approx(24 * 3600, abs=1)
    assert day.seconds_paused == pytest.approx(1800, abs=1)


def test_no_coordinates_means_no_solar_facts_not_zero(seeded: Settings) -> None:
    _build(coordinates=False)
    with session_scope() as session:
        day = session.get(orm.AnalyticsDay, "2026-08-05")
        birds = session.get(orm.AnalyticsGroupDay, {"local_date": "2026-08-05", "taxonomic_group": "bird"})
    assert day is not None and day.sunrise_utc is None and day.civil_dusk_utc is None
    assert day.daylight_captured_seconds == 0.0
    assert birds is not None and birds.daylight_detections == 0
    assert birds.detections == 21


def test_the_detection_table_always_wins(seeded: Settings) -> None:
    """ADR-056's first honesty rule: the roll-up must agree with a direct
    aggregation of the same day. Both paths, one number."""
    _build()
    start, end = analytics.day_bounds(DAY, ZONE)
    with session_scope() as session:
        direct = history.timeline(session, history.Range(start, end, "day"), bucket_seconds=86400)
        by_group_direct: dict[str, int] = {}
        for bucket in direct["buckets"]:  # type: ignore[union-attr]
            for group, facts in bucket["groups"].items():  # type: ignore[union-attr]
                by_group_direct[group] = by_group_direct.get(group, 0) + facts["detections"]
        rolled = {
            r.taxonomic_group: r.detections
            for r in session.execute(
                select(orm.AnalyticsGroupDay).where(orm.AnalyticsGroupDay.local_date == "2026-08-05")
            ).scalars()
        }
    assert rolled == by_group_direct


def test_rebuild_replaces_rather_than_duplicates(seeded: Settings) -> None:
    _build()
    _build()
    with session_scope() as session:
        assert session.execute(select(orm.AnalyticsDay)).scalars().all().__len__() == 1
        robin = session.execute(
            select(orm.AnalyticsTaxonHour).where(orm.AnalyticsTaxonHour.label == "European Robin")
        ).scalars().all()
    assert len(robin) == 1 and robin[0].detections == 10


def test_days_to_build_orders_the_work(seeded: Settings) -> None:
    with session_scope() as session:
        # Nothing built yet: today, yesterday, then every missing day newest
        # first back to the first detection (the 4th, local).
        days = analytics.days_to_build(session, timezone=LONDON, now=NOW)
    assert days[:2] == [date(2026, 8, 6), date(2026, 8, 5)]
    assert days[-1] == date(2026, 8, 4)
    assert len(days) == 3

    _build(date(2026, 8, 4), now=NOW)
    _build(date(2026, 8, 5), now=NOW)
    _build(date(2026, 8, 6), now=NOW)
    with session_scope() as session:
        days = analytics.days_to_build(session, timezone=LONDON, now=NOW)
    # Reviews written on the 5th at 12:00 are older than the builds at NOW.
    assert days == [date(2026, 8, 6), date(2026, 8, 5)]

    # A week later everything built is stale, oldest first, a few per run.
    later = NOW + timedelta(days=8)
    with session_scope() as session:
        days = analytics.days_to_build(session, timezone=LONDON, now=later, catchup_limit=0)
    assert days[:2] == [date(2026, 8, 14), date(2026, 8, 13)]
    assert date(2026, 8, 4) in days and date(2026, 8, 5) in days

    # A new review on an old day marks that day.
    with session_scope() as session:
        robin = session.execute(
            select(orm.Detection).where(orm.Detection.common_name == "European Robin")
        ).scalars().first()
        assert robin is not None
        session.add(
            orm.Review(
                detection_id=robin.id,
                actor="operator",
                status="confirmed",
                created_at=NOW + timedelta(hours=1),
            )
        )
    with session_scope() as session:
        days = analytics.days_to_build(
            session, timezone=LONDON, now=NOW + timedelta(hours=2), stale_limit=0, catchup_limit=0
        )
    assert days == [date(2026, 8, 6), date(2026, 8, 5)]


def test_rebuild_all_and_status(seeded: Settings) -> None:
    report = analytics.rebuild(timezone=LONDON, latitude=LAT, longitude=LON, now=NOW, all_days=True)
    assert report["count"] == 3
    assert [d["local_date"] for d in report["days"]] == ["2026-08-06", "2026-08-05", "2026-08-04"]
    with session_scope() as session:
        facts = analytics.status(session, timezone=LONDON, now=NOW)
    assert facts["days_built"] == 3
    assert facts["days_missing"] == 0
    assert facts["first_date"] == "2026-08-04"
    assert facts["last_date"] == "2026-08-06"
    assert facts["detections_rolled_up"] == 66
    assert facts["age_seconds"] == 0.0


@pytest.fixture
def built(seeded: Settings) -> Settings:
    analytics.rebuild(timezone=LONDON, latitude=LAT, longitude=LON, now=NOW, all_days=True)
    return seeded


def test_series_by_day_night_and_week(built: Settings) -> None:
    with session_scope() as session:
        dates = analytics.resolve_dates(session, "all", LONDON, now=NOW)
        assert (dates.first, dates.last) == (date(2026, 8, 4), date(2026, 8, 6))
        by_day = analytics.series(session, dates, grain="day", group="bird")
        by_night = analytics.series(session, dates, grain="night", group="bat")
        by_week = analytics.series(session, dates, grain="week")
        robins = analytics.series(session, dates, grain="day", label="European Robin")
    days = {b["key"]: b for b in by_day["buckets"]}
    assert days["2026-08-05"]["detections"] == 21
    assert days["2026-08-05"]["per_captured_hour"] == pytest.approx(21 / 24, abs=0.01)
    assert days["2026-08-04"]["detections"] == 0
    assert days["2026-08-06"]["partial"] is True
    assert by_day["excluded_withdrawn_count"] == 1
    # The night of the 4th holds the 02:30 BST pass; the night of the 5th the
    # three evening passes.
    nights = {b["key"]: b["detections"] for b in by_night["buckets"]}
    assert nights["2026-08-04"] == 1
    assert nights["2026-08-05"] == 3
    weeks = by_week["buckets"]
    assert len(weeks) == 1 and weeks[0]["key"] == "2026-W32"
    assert weeks[0]["groups"] == {"bird": 21, "bat": 4, "acoustic_event": 41}
    assert {b["key"]: b["detections"] for b in robins["buckets"]}["2026-08-05"] == 10


def test_hours_matrix_carries_solar_lines(built: Settings) -> None:
    with session_scope() as session:
        dates = analytics.resolve_dates(session, "all", LONDON, now=NOW)
        matrix = analytics.hours(session, dates, columns="day", group="bird", timezone=LONDON)
        woodpecker = analytics.hours(
            session, dates, columns="week", label="Common Woodpigeon", timezone=LONDON
        )
    fifth = next(c for c in matrix["columns"] if c["key"] == "2026-08-05")
    assert fifth["hours"][6] == 13 and fifth["hours"][7] == 5 and fifth["hours"][0] == 1
    assert fifth["captured"][6] == pytest.approx(3600, abs=1)
    assert fifth["solar"] is not None
    assert 5.2 < fifth["solar"]["sunrise"] < 5.8  # 05:35 BST, as a local hour
    assert 20.4 < fifth["solar"]["sunset"] < 21.0
    assert matrix["profile"][6] == 13
    assert woodpecker["columns"][0]["hours"][7] == 5
    assert sum(woodpecker["profile"]) == 5


def test_taxa_grid_names_corrections_and_counts_cells(built: Settings) -> None:
    with session_scope() as session:
        dates = analytics.resolve_dates(session, "all", LONDON, now=NOW)
        grid = analytics.taxa(session, dates, group="bird", grain="week")
        bats = analytics.taxa(session, dates, group="bat", grain="day")
    labels = [r["label"] for r in grid["rows"]]
    assert labels[0] == "European Robin"
    assert "Common Chiffchaff" in labels and "Willow Warbler" not in labels
    assert "Wood Warbler" not in labels and "Western Screech-Owl" not in labels
    robin = grid["rows"][0]
    assert robin["cells"] == [10] and robin["first_date"] == "2026-08-05" and robin["days_heard"] == 1
    assert grid["columns"][0]["key"] == "2026-W32"
    assert grid["excluded_rejected_count"] == 1
    band = next(r for r in bats["rows"] if r["label"] == "45\u201350 kHz")
    assert band["cells"] == [0, 3, 0]


def test_span_reports_daylight_and_the_night_that_starts_each_evening(built: Settings) -> None:
    with session_scope() as session:
        dates = analytics.resolve_dates(session, "2026-08-05", LONDON, now=NOW)
        birds = analytics.span(session, dates, group="bird", timezone=LONDON, latitude=LAT, longitude=LON)
        bats = analytics.span(session, dates, group="bat", timezone=LONDON, latitude=LAT, longitude=LON)
    day = birds["days"][0]
    assert day["date"] == "2026-08-05" and day["built"] is True
    assert day["first"] == pytest.approx(0.5, abs=0.01)  # the owl at 00:30 local
    assert day["p05"] == pytest.approx(3.0, abs=0.01)  # the stray blackbird
    assert day["p95"] == pytest.approx(7.067, abs=0.01)  # the last woodpigeon
    assert day["last"] == pytest.approx(20.0, abs=0.01)
    assert 15.0 < day["daylight_hours"] < 16.0
    assert day["daylight_detections"] == 19
    assert day["daylight_per_captured_hour"] is not None
    night = bats["days"][0]
    # The night starting on the 5th: three passes after dusk plus whatever the
    # 6th's morning half holds (nothing), over roughly 7-8 hours of night.
    assert night["night_known"] is True
    assert night["night_detections"] == 3
    assert 6.0 < night["night_hours"] < 9.0
    assert night["night_per_captured_hour"] is not None


def test_resolve_dates_falls_back_rather_than_failing(built: Settings) -> None:
    with session_scope() as session:
        unknown = analytics.resolve_dates(session, "not-a-window", LONDON, now=NOW)
        month = analytics.resolve_dates(session, "2026-08", LONDON, now=NOW)
        ninety = analytics.resolve_dates(session, "last-90d", LONDON, now=NOW)
    assert unknown.name == "last-30d"
    # The record began on the 4th: a month or ninety days that starts before
    # it starts there instead, so the days-built figure is honest.
    assert (month.first, month.last) == (date(2026, 8, 4), date(2026, 8, 6))
    assert (ninety.first, ninety.last) == (date(2026, 8, 4), date(2026, 8, 6))
    assert ninety.label == "last 90 days"


def test_questions_are_well_formed() -> None:
    listed = analytics.questions()
    assert len(listed) >= 6
    ids = [q["id"] for q in listed]
    assert len(ids) == len(set(ids))
    for q in listed:
        assert q["view"] in analytics.VIEWS
        assert q["expect"] and q["question"] and q["title"]
        assert "range" in q["params"]


def test_saved_reports_round_trip(seeded: Settings) -> None:
    with session_scope() as session:
        saved = analytics.save_report(
            session,
            name="Robins by week",
            question="When are there most robins?",
            view="series",
            params={"range": "this-year", "grain": "week", "label": "European Robin"},
            actor="operator",
        )
        assert saved["id"] and saved["created_by"] == "operator"
        with pytest.raises(ValueError):
            analytics.save_report(session, name="x", question="", view="pie", params={}, actor="o")
    with session_scope() as session:
        listed = analytics.list_reports(session)
        assert [r["name"] for r in listed] == ["Robins by week"]
        assert analytics.delete_report(session, uuid.UUID(saved["id"])) is True
        assert analytics.delete_report(session, uuid.uuid4()) is False
    with session_scope() as session:
        assert analytics.list_reports(session) == []


def test_hour_windows_survive_the_clocks_changing() -> None:
    # 2026-03-29: 01:00 GMT becomes 02:00 BST; local hour 1 does not exist.
    forward = analytics.hour_windows(date(2026, 3, 29), ZONE)
    assert forward[1][0] == forward[1][1]
    assert sum((e - s).total_seconds() for s, e in forward) == 23 * 3600
    # 2026-10-25: 02:00 BST becomes 01:00 GMT; local hour 1 happens twice.
    back = analytics.hour_windows(date(2026, 10, 25), ZONE)
    assert (back[1][1] - back[1][0]).total_seconds() == 7200
    assert sum((e - s).total_seconds() for s, e in back) == 25 * 3600
