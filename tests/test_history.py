"""Tests for the history queries that back overnight browsing.

Two things are being protected here. First, that the aggregation is actually
aggregation: a bug that silently stops truncating timestamps turns a tidy chart into
one bucket per second, which is how the first version behaved. Second, that coverage
cannot report an impossible figure — an empty night means something completely
different depending on whether the station was listening, so that number has to be
trustworthy or it is worse than absent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from open_observatory import history
from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope

LONDON = "Europe/London"


@pytest.fixture
def seeded(settings):
    """A station with a night's worth of detections and two capture streams."""
    init_engine(settings)
    create_all()
    station_id = uuid.uuid4()
    stream_id = uuid.uuid4()
    detector_id = uuid.uuid4()
    # 2026-08-04 21:00 UTC, comfortably inside a "last night" window resolved at
    # 09:00 the next morning.
    base = datetime(2026, 8, 4, 21, 0, tzinfo=UTC)

    with session_scope() as session:
        session.add(orm.Station(id=station_id, name="test", timezone=LONDON))
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
                start_utc=base,
                end_utc=base + timedelta(hours=2),
                start_monotonic_ns=0,
                sample_rate=384000,
                sample_format="S16_LE",
            )
        )
        # A second stream overlapping the first, as a restart produces.
        session.add(
            orm.AudioStream(
                id=uuid.uuid4(),
                source_kind="alsa",
                start_utc=base + timedelta(hours=1),
                end_utc=base + timedelta(hours=3),
                start_monotonic_ns=0,
                sample_rate=384000,
                sample_format="S16_LE",
            )
        )

        def detection(minutes: int, group: str, name: str | None, score: float) -> None:
            at = base + timedelta(minutes=minutes)
            session.add(
                orm.Detection(
                    id=uuid.uuid4(),
                    station_id=station_id,
                    detector_id=detector_id,
                    stream_id=stream_id,
                    window_id=uuid.uuid4(),
                    event_start_utc=at,
                    event_end_utc=at + timedelta(seconds=3),
                    source_start_frame=0,
                    source_end_frame=1,
                    detector_label=name or "acoustic event",
                    common_name=name,
                    scientific_name=f"Sci {name}" if name else None,
                    rank="species" if name else None,
                    taxonomic_group=group,
                    score=score,
                )
            )

        # Two species in the first ten minutes, one later, plus unidentified noise.
        for index in range(5):
            detection(index, "bird", "European Robin", 0.5 + index * 0.05)
        for index in range(3):
            detection(index + 2, "bird", "Common Woodpigeon", 0.9)
        detection(75, "bat", "bat pass", 0.8)
        for index in range(40):
            detection(index, "acoustic_event", None, 0.3)

    return {"station_id": station_id, "base": base}


class TestNamedRanges:
    def _at(self, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 8, 5, hour, minute, tzinfo=UTC)

    def test_last_night_before_noon_is_the_night_just_ended(self) -> None:
        window = history.resolve_named_range("last-night", LONDON, now=self._at(6))
        # 20:00 local on the 4th is 19:00 UTC in British Summer Time.
        assert window.start == datetime(2026, 8, 4, 19, 0, tzinfo=UTC)
        assert window.seconds == 12 * 3600

    def test_last_night_after_noon_is_tonight(self) -> None:
        window = history.resolve_named_range("last-night", LONDON, now=self._at(21))
        assert window.start == datetime(2026, 8, 5, 19, 0, tzinfo=UTC)

    def test_local_timezone_is_respected_not_utc(self) -> None:
        """A window computed in UTC would drift an hour with summer time."""
        summer = history.resolve_named_range("last-night", LONDON, now=self._at(6))
        winter = history.resolve_named_range(
            "last-night", LONDON, now=datetime(2026, 1, 5, 6, tzinfo=UTC)
        )
        # 20:00 local is 19:00 UTC in summer and 20:00 UTC in winter.
        assert summer.start.hour == 19
        assert winter.start.hour == 20

    def test_yesterday_is_a_whole_local_day(self) -> None:
        window = history.resolve_named_range("yesterday", LONDON, now=self._at(15))
        assert window.seconds == 24 * 3600

    def test_unknown_name_falls_back_rather_than_raising(self) -> None:
        window = history.resolve_named_range("nonsense", LONDON, now=self._at(15))
        assert window.seconds == 3600

    def test_bad_timezone_falls_back_to_utc(self) -> None:
        window = history.resolve_named_range("today", "Not/AZone", now=self._at(15))
        assert window.start.tzinfo is not None


class TestBucketSizing:
    def test_scales_to_roughly_the_target_count(self) -> None:
        for window_seconds in (3600, 12 * 3600, 24 * 3600, 7 * 86400):
            seconds = history.choose_bucket_seconds(window_seconds, target_buckets=120)
            assert 1 <= window_seconds / seconds <= 240

    def test_prefers_recognisable_intervals(self) -> None:
        # A 12 hour night at ~120 buckets wants 360 s; 600 s is the friendly choice,
        # so labels land on the ten-minute marks people actually read.
        assert history.choose_bucket_seconds(12 * 3600) == 600
        assert history.choose_bucket_seconds(3600) == 30


class TestMergedSeconds:
    def test_overlapping_intervals_are_not_double_counted(self) -> None:
        base = datetime(2026, 8, 4, 20, tzinfo=UTC)
        merged = history._merged_seconds(
            [
                (base, base + timedelta(hours=2)),
                (base + timedelta(hours=1), base + timedelta(hours=3)),
            ]
        )
        assert merged == 3 * 3600

    def test_disjoint_intervals_add(self) -> None:
        base = datetime(2026, 8, 4, 20, tzinfo=UTC)
        merged = history._merged_seconds(
            [
                (base, base + timedelta(hours=1)),
                (base + timedelta(hours=2), base + timedelta(hours=3)),
            ]
        )
        assert merged == 2 * 3600

    def test_empty_is_zero(self) -> None:
        assert history._merged_seconds([]) == 0.0


class TestTimeline:
    def test_buckets_are_actually_truncated(self, seeded) -> None:
        """The regression that mattered: `/` in SQLAlchemy 2 is true division.

        Using it to truncate returned one bucket per distinct second — a twelve hour
        window produced 1899 ten-minute buckets.
        """
        window = history.Range(
            seeded["base"] - timedelta(minutes=10),
            seeded["base"] + timedelta(hours=2),
            "test",
        )
        with session_scope() as session:
            result = history.timeline(session, window, bucket_seconds=600)
        assert result["bucket_seconds"] == 600
        expected_max = int(window.seconds / 600) + 1
        assert len(result["buckets"]) <= expected_max
        # Every bucket start must land exactly on a bucket boundary.
        for bucket in result["buckets"]:
            stamp = datetime.fromisoformat(str(bucket["start_utc"]).replace("Z", "+00:00"))
            assert int(stamp.timestamp()) % 600 == 0

    def test_counts_split_by_group(self, seeded) -> None:
        window = history.Range(
            seeded["base"], seeded["base"] + timedelta(hours=2), "test"
        )
        with session_scope() as session:
            result = history.timeline(session, window, bucket_seconds=3600)
        first = result["buckets"][0]["groups"]
        assert first["bird"]["detections"] == 8
        assert first["acoustic_event"]["detections"] == 40
        # The bat pass is 75 minutes in, so it belongs to the second hour.
        assert "bat" not in first
        assert result["buckets"][1]["groups"]["bat"]["detections"] == 1

    def test_unidentified_can_be_excluded(self, seeded) -> None:
        window = history.Range(
            seeded["base"], seeded["base"] + timedelta(hours=2), "test"
        )
        with session_scope() as session:
            result = history.timeline(
                session, window, bucket_seconds=3600, include_unidentified=False
            )
        for bucket in result["buckets"]:
            assert "acoustic_event" not in bucket["groups"]

    def test_score_filter_applies(self, seeded) -> None:
        window = history.Range(
            seeded["base"], seeded["base"] + timedelta(hours=2), "test"
        )
        with session_scope() as session:
            result = history.timeline(session, window, bucket_seconds=3600, min_score=0.85)
        # Only the woodpigeons at 0.9 and the bat pass at 0.8... 0.8 < 0.85, so three.
        total = sum(
            entry["detections"]
            for bucket in result["buckets"]
            for entry in bucket["groups"].values()
        )
        assert total == 3

    def test_states_what_it_counts(self, seeded) -> None:
        window = history.Range(seeded["base"], seeded["base"] + timedelta(hours=1), "t")
        with session_scope() as session:
            result = history.timeline(session, window)
        # Counting detections is not counting animals, and the payload says so.
        assert "not of animals" in str(result["note"])

    def test_empty_window_is_empty_not_an_error(self, seeded) -> None:
        window = history.Range(
            seeded["base"] - timedelta(days=5), seeded["base"] - timedelta(days=4), "t"
        )
        with session_scope() as session:
            assert history.timeline(session, window)["buckets"] == []


class TestSpeciesSummary:
    def test_one_row_per_label_with_its_extent(self, seeded) -> None:
        window = history.Range(seeded["base"], seeded["base"] + timedelta(hours=2), "t")
        with session_scope() as session:
            rows = history.species_summary(session, window)
        by_name = {row["display_name"]: row for row in rows}
        assert by_name["European Robin"]["detections"] == 5
        assert by_name["European Robin"]["best_score"] == pytest.approx(0.7, abs=1e-6)
        assert by_name["Common Woodpigeon"]["detections"] == 3
        # First and last are the extent of the calling, not of the window.
        first = by_name["European Robin"]["first_seen_utc"]
        last = by_name["European Robin"]["last_seen_utc"]
        assert first < last

    def test_excludes_unidentified_by_default(self, seeded) -> None:
        window = history.Range(seeded["base"], seeded["base"] + timedelta(hours=2), "t")
        with session_scope() as session:
            rows = history.species_summary(session, window)
        assert all(row["taxonomic_group"] in history.IDENTIFIED_GROUPS for row in rows)

    def test_ordered_by_how_much_was_heard(self, seeded) -> None:
        window = history.Range(seeded["base"], seeded["base"] + timedelta(hours=2), "t")
        with session_scope() as session:
            rows = history.species_summary(session, window)
        counts = [row["detections"] for row in rows]
        assert counts == sorted(counts, reverse=True)


class TestCoverage:
    def test_overlapping_streams_cannot_exceed_the_window(self, seeded) -> None:
        """Two overlapping streams summed naively reported 13x coverage."""
        window = history.Range(seeded["base"], seeded["base"] + timedelta(hours=2), "t")
        with session_scope() as session:
            result = history.coverage(session, window)
        assert result["fraction_captured"] <= 1.0
        # The two streams span 21:00-23:00 and 22:00-00:00; clipped to a two hour
        # window that is two hours of cover, not three.
        assert result["seconds_captured"] == pytest.approx(2 * 3600, abs=1)

    def test_reports_how_much_came_from_the_microphone(self, seeded) -> None:
        window = history.Range(seeded["base"], seeded["base"] + timedelta(hours=2), "t")
        with session_scope() as session:
            result = history.coverage(session, window)
        assert result["seconds_from_microphone"] == pytest.approx(2 * 3600, abs=1)

    def test_window_with_no_capture_reports_zero_not_null(self, seeded) -> None:
        window = history.Range(
            seeded["base"] - timedelta(days=3), seeded["base"] - timedelta(days=2), "t"
        )
        with session_scope() as session:
            result = history.coverage(session, window)
        # This is the distinction that makes an empty night interpretable.
        assert result["seconds_captured"] == 0
        assert result["fraction_captured"] == 0
        assert result["streams"] == []
