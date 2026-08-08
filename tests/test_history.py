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
                # Frame count matches the claimed span exactly -- a healthy stream,
                # not the ADR-024 case under test elsewhere in this file.
                frame_count=384000 * 2 * 3600,
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
                frame_count=384000 * 2 * 3600,
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

        # A stream the AudioMoth fell back to because it presented no ALSA card --
        # the incident this module's filtering exists for. Same window, same
        # detector, high score: nothing about the row itself looks wrong, which is
        # exactly why the source has to be checked rather than the content.
        synthetic_stream_id = uuid.uuid4()
        session.add(
            orm.AudioStream(
                id=synthetic_stream_id,
                source_kind="synthetic",
                start_utc=base,
                end_utc=base + timedelta(hours=2),
                start_monotonic_ns=0,
                sample_rate=48000,
                sample_format="S16_LE",
                frame_count=48000 * 2 * 3600,
            )
        )
        session.add(
            orm.Detection(
                id=uuid.uuid4(),
                station_id=station_id,
                detector_id=detector_id,
                stream_id=synthetic_stream_id,
                window_id=uuid.uuid4(),
                event_start_utc=base + timedelta(minutes=5),
                event_end_utc=base + timedelta(minutes=5, seconds=3),
                source_start_frame=0,
                source_end_frame=1,
                detector_label="Grey-winged Inca-Finch",
                common_name="Grey-winged Inca-Finch",
                scientific_name="Incaspiza ortizi",
                rank="species",
                taxonomic_group="bird",
                score=0.99,
            )
        )

    return {"station_id": station_id, "base": base, "synthetic_stream_id": synthetic_stream_id}


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
        # display_name/title_hint come from the shared display_title() helper now,
        # not a locally re-derived fallback chain.
        assert "title_hint" in by_name["European Robin"]
        assert by_name["European Robin"]["title_hint"] is None

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


class TestFrameDerivedCoverage:
    """ADR-024: the live database's worst case -- a stream row that claimed
    `start_utc` 2026-08-07 03:38:54 to `end_utc` 2026-08-08 11:36:36 (32 hours),
    ending in `AlsaCaptureError: ALSA read failed: File descriptor in bad state`,
    but whose `frame_count` (3,852,212,352 at 384 kHz) was only 2.79 hours of
    actual audio -- with zero capture-gap rows and zero detections anywhere in
    the other ~29 hours. Coverage must not believe the claimed span over the
    frames actually delivered.
    """

    def _stale_row(self, session, station_id, base, *, last_frame_at_utc=None):
        stream_id = uuid.uuid4()
        session.add(
            orm.AudioStream(
                id=stream_id,
                source_kind="alsa",
                start_utc=base,
                end_utc=base + timedelta(hours=32),
                start_monotonic_ns=0,
                sample_rate=384000,
                sample_format="S16_LE",
                # 2.79 hours' worth of frames inside a 32 hour claim.
                frame_count=3_852_212_352,
                end_reason="AlsaCaptureError: ALSA read failed: File descriptor in bad state",
                last_frame_at_utc=last_frame_at_utc,
            )
        )
        return stream_id

    def test_coverage_is_capped_by_frame_count_not_the_claimed_end(self, seeded) -> None:
        with session_scope() as session:
            self._stale_row(session, seeded["station_id"], seeded["base"])
        window = history.Range(seeded["base"], seeded["base"] + timedelta(hours=32), "t")
        with session_scope() as session:
            result = history.coverage(session, window)
        # 2 healthy hours (the seeded alsa streams) plus ~2.79 stale-row hours,
        # nowhere near the 32 the row claims.
        assert result["seconds_captured"] < 6 * 3600
        assert result["fraction_captured"] < (6 * 3600) / (32 * 3600)

    def test_stale_row_is_flagged_suspect(self, seeded) -> None:
        with session_scope() as session:
            self._stale_row(session, seeded["station_id"], seeded["base"])
        window = history.Range(seeded["base"], seeded["base"] + timedelta(hours=32), "t")
        with session_scope() as session:
            result = history.coverage(session, window)
        assert result["suspect_stream_count"] == 1
        suspects = [span for span in result["streams"] if span["suspect"]]
        assert len(suspects) == 1
        assert suspects[0]["frame_derived_seconds"] == pytest.approx(
            3_852_212_352 / 384000, abs=1
        )

    def test_healthy_rows_are_not_flagged(self, seeded) -> None:
        window = history.Range(seeded["base"], seeded["base"] + timedelta(hours=2), "t")
        with session_scope() as session:
            result = history.coverage(session, window)
        assert result["suspect_stream_count"] == 0
        assert all(not span["suspect"] for span in result["streams"])

    def test_heartbeat_gives_a_tighter_cap_than_frame_count_alone(self, seeded) -> None:
        """A row with a heartbeat gets capped to it directly, not just proportionally."""
        heartbeat = seeded["base"] + timedelta(hours=1)
        with session_scope() as session:
            stream_id = self._stale_row(
                session, seeded["station_id"], seeded["base"], last_frame_at_utc=heartbeat
            )
        window = history.Range(seeded["base"], seeded["base"] + timedelta(hours=32), "t")
        with session_scope() as session:
            result = history.coverage(session, window)
        span = next(s for s in result["streams"] if s["stream_id"] == str(stream_id))
        # Capped at the heartbeat (1h in), tighter than the frame-derived bound
        # (~2.79h), because the heartbeat is a direct timestamp.
        assert span["seconds"] == pytest.approx(3600, abs=1)


class TestFindSuspectStreams:
    def test_finds_the_stale_row_by_its_own_frame_count(self, seeded) -> None:
        base = seeded["base"]
        with session_scope() as session:
            stream_id = uuid.uuid4()
            session.add(
                orm.AudioStream(
                    id=stream_id,
                    source_kind="alsa",
                    start_utc=base,
                    end_utc=base + timedelta(hours=32),
                    start_monotonic_ns=0,
                    sample_rate=384000,
                    sample_format="S16_LE",
                    frame_count=3_852_212_352,
                    end_reason="AlsaCaptureError: ALSA read failed: File descriptor in bad state",
                )
            )
        with session_scope() as session:
            suspects = history.find_suspect_streams(session)
        assert len(suspects) == 1
        assert suspects[0].stream_id == stream_id
        assert suspects[0].proposed_end_utc < suspects[0].claimed_end_utc
        assert suspects[0].proposed_end_utc == base + timedelta(seconds=3_852_212_352 / 384000)

    def test_healthy_rows_are_not_suspect(self, seeded) -> None:
        with session_scope() as session:
            suspects = history.find_suspect_streams(session)
        assert suspects == []

    def test_open_rows_are_never_touched(self, seeded) -> None:
        """A NULL `end_utc` might belong to a station running right now."""
        base = seeded["base"]
        with session_scope() as session:
            session.add(
                orm.AudioStream(
                    id=uuid.uuid4(),
                    source_kind="alsa",
                    start_utc=base,
                    end_utc=None,
                    start_monotonic_ns=0,
                    sample_rate=384000,
                    sample_format="S16_LE",
                    frame_count=0,
                )
            )
        with session_scope() as session:
            assert history.find_suspect_streams(session) == []

    def test_apply_preserves_the_original_claim_for_audit(self, seeded) -> None:
        base = seeded["base"]
        with session_scope() as session:
            stream_id = uuid.uuid4()
            session.add(
                orm.AudioStream(
                    id=stream_id,
                    source_kind="alsa",
                    start_utc=base,
                    end_utc=base + timedelta(hours=32),
                    start_monotonic_ns=0,
                    sample_rate=384000,
                    sample_format="S16_LE",
                    frame_count=3_852_212_352,
                    end_reason="AlsaCaptureError: ALSA read failed: File descriptor in bad state",
                )
            )

        with session_scope() as session:
            [suspect] = history.find_suspect_streams(session)
            claimed_before = suspect.claimed_end_utc
            history.apply_stream_reconciliation(session, suspect)

        with session_scope() as session:
            row = session.get(orm.AudioStream, stream_id)
            assert history._aware(row.end_utc) < claimed_before
            assert row.detail["reconciliation"]["claimed_end_utc"] is not None
            assert "reconciled" in row.end_reason
            # And it no longer shows up as suspect.
            assert history.find_suspect_streams(session) == []


class TestSourceFiltering:
    """The AudioMoth USB/OFF incident: a synthetic fallback stream's detections
    (five "Grey-winged Inca-Finch" rows in the real database) must never be
    presented as observed wildlife, but must remain reachable on request.
    """

    def _window(self, seeded) -> history.Range:
        return history.Range(seeded["base"], seeded["base"] + timedelta(hours=2), "t")

    def test_timeline_excludes_synthetic_by_default(self, seeded) -> None:
        window = self._window(seeded)
        with session_scope() as session:
            result = history.timeline(session, window, bucket_seconds=3600)
        # Without the synthetic row, the first hour's bird count is the 5 Robins
        # and 3 Woodpigeons from the "alsa" stream only.
        assert result["buckets"][0]["groups"]["bird"]["detections"] == 8

    def test_timeline_includes_synthetic_on_request(self, seeded) -> None:
        window = self._window(seeded)
        with session_scope() as session:
            result = history.timeline(
                session, window, bucket_seconds=3600, include_synthetic=True
            )
        assert result["buckets"][0]["groups"]["bird"]["detections"] == 9

    def test_timeline_reports_the_excluded_count(self, seeded) -> None:
        window = self._window(seeded)
        with session_scope() as session:
            excluded = history.timeline(session, window)["excluded_synthetic_count"]
        assert excluded == 1

    def test_timeline_reports_zero_excluded_when_included(self, seeded) -> None:
        window = self._window(seeded)
        with session_scope() as session:
            excluded = history.timeline(session, window, include_synthetic=True)[
                "excluded_synthetic_count"
            ]
        assert excluded == 0

    def test_species_summary_excludes_synthetic_species_by_default(self, seeded) -> None:
        window = self._window(seeded)
        with session_scope() as session:
            rows = history.species_summary(session, window)
        assert "Grey-winged Inca-Finch" not in {row["display_name"] for row in rows}

    def test_species_summary_includes_synthetic_species_on_request(self, seeded) -> None:
        window = self._window(seeded)
        with session_scope() as session:
            rows = history.species_summary(session, window, include_synthetic=True)
        by_name = {row["display_name"]: row for row in rows}
        assert by_name["Grey-winged Inca-Finch"]["detections"] == 1
        assert by_name["Grey-winged Inca-Finch"]["best_score"] == pytest.approx(0.99, abs=1e-6)

    def test_live_stream_detections_are_unaffected_either_way(self, seeded) -> None:
        """The point of this fix is that genuine detections never move."""
        window = self._window(seeded)
        with session_scope() as session:
            excluding = {
                r["display_name"]: r["detections"]
                for r in history.species_summary(session, window)
            }
            including = {
                r["display_name"]: r["detections"]
                for r in history.species_summary(session, window, include_synthetic=True)
            }
        assert excluding["European Robin"] == including["European Robin"] == 5
        assert excluding["Common Woodpigeon"] == including["Common Woodpigeon"] == 3

    def test_excluded_synthetic_count_respects_the_score_filter(self, seeded) -> None:
        """The synthetic row scores 0.99, so a high min_score must still find it."""
        window = self._window(seeded)
        with session_scope() as session:
            assert history.excluded_synthetic_count(session, window, min_score=0.95) == 1
            assert history.excluded_synthetic_count(session, window, min_score=0.999) == 0


class TestHistoryHTTP:
    """`/api/v1/history` and `/api/v1/history/windows` through the real app.

    HANDOVER.md §6.3 item 8: everything above this class tests the aggregation
    functions directly, but nothing previously exercised these two endpoints
    through FastAPI at all -- exactly where the true-division bucket bug would
    have shown itself (SQLAlchemy 2's ``/`` on an Integer column is *true*
    division, not floor division; it once produced 1899 ten-minute buckets for a
    twelve hour window because ``(epoch / seconds) * seconds`` truncated
    nothing). Nothing here is mocked: a real `Station` runs a real synthetic
    capture through a real FastAPI app.
    """

    @pytest.fixture
    def http_client(self, settings):
        from fastapi.testclient import TestClient

        from open_observatory.api.app import create_app
        from open_observatory.config import set_settings

        configured = settings.model_copy(
            update={"source": "synthetic", "synthetic_scene": "dawn-chorus"}
        )
        set_settings(configured)
        app = create_app(configured)
        with TestClient(app) as test_client:
            import time

            for _ in range(60):
                if test_client.get("/api/v1/station").json()["capture"]["blocks"] > 12:
                    break
                time.sleep(0.25)
            yield test_client

    def test_history_windows_lists_the_named_ranges(self, http_client) -> None:
        payload = http_client.get("/api/v1/history/windows").json()
        names = {entry["name"] for entry in payload["windows"]}
        assert {"last-hour", "last-night", "dawn-chorus", "today", "yesterday", "last-24h"} <= names
        for entry in payload["windows"]:
            # Each resolved window must be a real, non-negative, closed-open span.
            assert entry["seconds"] >= 0
            assert entry["start_utc"] < entry["end_utc"] or entry["seconds"] == 0

    def test_history_endpoint_has_every_documented_section(self, http_client) -> None:
        payload = http_client.get("/api/v1/history?window=last-24h").json()
        for key in ("range", "timezone", "timeline", "species", "unidentified", "coverage"):
            assert key in payload, payload.keys()

    def test_history_timeline_buckets_stay_truncated_through_http(self, http_client) -> None:
        """The regression that mattered, exercised through the real endpoint.

        A naive `/` truncation over a 24 hour window at the default target of
        ~120 buckets would (as it once did) yield one bucket per second instead
        of one per interval -- tens of thousands of buckets rather than at most a
        couple of hundred.
        """
        payload = http_client.get("/api/v1/history?window=last-24h").json()
        buckets = payload["timeline"]["buckets"]
        assert len(buckets) <= 300
        seconds = payload["timeline"]["bucket_seconds"]
        for bucket in buckets:
            stamp = datetime.fromisoformat(str(bucket["start_utc"]).replace("Z", "+00:00"))
            assert int(stamp.timestamp()) % seconds == 0

    def test_history_coverage_cannot_exceed_the_window_through_http(self, http_client) -> None:
        payload = http_client.get("/api/v1/history?window=last-hour").json()
        coverage = payload["coverage"]
        assert coverage["fraction_captured"] is not None
        assert 0.0 <= coverage["fraction_captured"] <= 1.0
        assert coverage["seconds_captured"] <= coverage["seconds_in_range"] + 1
        assert "suspect_stream_count" in coverage
        # The fixture only ever runs the synthetic source, which is real
        # capture-of-a-kind but never "from the microphone" (ADR-020).
        assert coverage["seconds_from_microphone"] == 0

    def test_history_custom_since_until_window(self, http_client) -> None:
        now = datetime.now(UTC)
        since = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        until = now.isoformat().replace("+00:00", "Z")
        payload = http_client.get(f"/api/v1/history?since={since}&until={until}").json()
        assert payload["range"]["label"] == "custom"
        assert payload["coverage"]["seconds_in_range"] == pytest.approx(3600, abs=2)
