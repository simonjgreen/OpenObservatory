"""The operator privacy pause (ADR-055), below the HTTP layer.

Four properties are load-bearing, and each of them is a way this control could
be quietly useless rather than obviously broken:

1. it ends by itself, without anything having to run;
2. it survives a restart, because a deadline is persisted rather than a
   countdown;
3. it leaves a record, so a paused afternoon is not an unexplained hole
   (charter item 2);
4. it never *stops* capture, because a privacy control that can leave the
   station unable to reopen its microphone is worse than the exposure it
   prevents.

The HTTP-level half of this -- live listening actually being refused, and a
pause outliving a real process restart -- is in `tests/test_api.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from open_observatory import history
from open_observatory import pause as pause_module
from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope
from open_observatory.pause import (
    ENDED_EXPIRED,
    ENDED_RESUMED,
    ENDED_SUPERSEDED,
    PauseController,
    PauseError,
    available_presets,
    next_local_midnight,
    resolve,
)

LONDON = "Europe/London"


@pytest.fixture
def db(settings):
    init_engine(settings)
    create_all()
    return settings


def _rows() -> list[orm.CapturePause]:
    with session_scope() as session:
        return list(
            session.execute(
                select(orm.CapturePause).order_by(orm.CapturePause.started_utc)
            ).scalars()
        )


class TestResolvingADuration:
    def test_each_fixed_preset_lands_where_its_label_says(self) -> None:
        now = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)
        for key, expected in (("15m", 900), ("1h", 3600), ("3h", 10800), ("6h", 21600)):
            ends, preset = resolve(key, timezone=LONDON, now=now)
            assert (ends - now).total_seconds() == expected, key
            assert preset.key == key

    def test_until_midnight_is_the_station_zone_not_utc(self) -> None:
        """The failure this catches is a station in BST resuming an hour late.

        14:00 UTC on 8 August is 15:00 in London, so the operator's midnight is
        23:00 UTC -- nine hours away, not ten. Computing it in UTC would give a
        pause that runs an hour past the end of the day it was meant to cover.
        """
        now = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)
        ends, _ = resolve("until-midnight", timezone=LONDON, now=now)
        assert ends == datetime(2026, 8, 8, 23, 0, tzinfo=UTC)
        assert (ends - now).total_seconds() == 9 * 3600

    def test_until_midnight_late_in_the_evening_is_minutes_not_a_day(self) -> None:
        """23:58 local means two minutes. "Until midnight" is the end of the
        operator's day, and a control that quietly turned that into 24 hours
        would cost a whole night of bats."""
        now = datetime(2026, 8, 8, 22, 58, tzinfo=UTC)  # 23:58 London
        ends, _ = resolve("until-midnight", timezone=LONDON, now=now)
        assert (ends - now).total_seconds() == 120

    def test_an_unusable_timezone_still_resolves_rather_than_raising(self) -> None:
        """A misconfigured zone must not take the privacy control away. It is
        the wrong midnight; it is not a broken button."""
        now = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)
        assert next_local_midnight(now, "Nowhere/Fictional") == datetime(
            2026, 8, 9, 0, 0, tzinfo=UTC
        )

    def test_an_unknown_key_is_refused_rather_than_defaulted(self) -> None:
        """Substituting a different duration for the one that was asked for is
        the one thing a privacy control may never do quietly."""
        with pytest.raises(PauseError, match="unknown pause duration"):
            resolve("forever", timezone=LONDON)

    def test_the_offered_menu_drops_typos_but_never_empties(self) -> None:
        assert [p.key for p in available_presets(("1h", "nonsense", "6h"))] == ["1h", "6h"]
        assert available_presets(()) == list(pause_module.PRESETS)


class TestItEndsByItself:
    def test_active_is_false_past_the_deadline_with_nothing_having_run(self) -> None:
        """No timer, no task, no row update. The operator will forget, so the
        expiry cannot depend on anything that could fail while they are not
        looking."""
        controller = PauseController(timezone_provider=lambda: LONDON)
        controller.start("15m")
        assert controller.active

        # Reach past the deadline without letting any of the controller's own
        # machinery run.
        controller._ends_epoch -= 901
        assert controller.active is False
        assert controller.snapshot()["active"] is False
        assert controller.snapshot()["ends_utc"] is None
        assert controller.remaining_seconds() == 0.0

    def test_sync_closes_the_row_of_a_pause_that_already_ended(self, db) -> None:
        controller = PauseController(
            session_factory=session_scope, timezone_provider=lambda: LONDON
        )
        controller.start("15m")
        controller._ends_epoch -= 901
        controller.sync()

        (row,) = _rows()
        assert row.ended_utc is not None
        assert row.end_reason == ENDED_EXPIRED

    def test_sync_is_a_noop_while_the_pause_is_still_running(self, db) -> None:
        controller = PauseController(
            session_factory=session_scope, timezone_provider=lambda: LONDON
        )
        controller.start("1h")
        controller.sync()
        (row,) = _rows()
        assert row.ended_utc is None
        assert controller.active


class TestItSurvivesARestart:
    def test_a_new_controller_readopts_a_running_pause(self, db) -> None:
        """The Pi rebooting mid-party must come back paused, not recording."""
        first = PauseController(session_factory=session_scope, timezone_provider=lambda: LONDON)
        first.start("6h")
        ends = first.ends_utc

        second = PauseController(session_factory=session_scope, timezone_provider=lambda: LONDON)
        assert second.active is False  # nothing adopted until it is asked to
        second.restore()

        assert second.active
        assert second.ends_utc == ends
        assert second.snapshot()["preset"] == "6h"
        # Still one row, still open: a restart is not a new pause.
        assert len(_rows()) == 1
        assert _rows()[0].ended_utc is None

    def test_a_pause_that_expired_while_the_station_was_down_stays_ended(self, db) -> None:
        """And is closed at its *deadline*, not at the moment anyone noticed --
        otherwise the recorded pause silently grows by the length of the
        outage."""
        deadline = datetime.now(UTC) - timedelta(hours=2)
        with session_scope() as session:
            session.add(
                orm.CapturePause(
                    id=uuid.uuid4(),
                    started_utc=deadline - timedelta(hours=1),
                    ends_utc=deadline,
                    preset="1h",
                    label="1 hour",
                    actor="operator",
                )
            )

        controller = PauseController(
            session_factory=session_scope, timezone_provider=lambda: LONDON
        )
        controller.restore()

        assert controller.active is False
        (row,) = _rows()
        assert row.end_reason == ENDED_EXPIRED
        assert row.ended_utc.replace(tzinfo=UTC) == deadline

    def test_what_is_persisted_is_a_deadline_and_never_a_countdown(self, db) -> None:
        """The column an implementation might reach for -- "seconds remaining"
        -- is wrong the instant the process stops. Asserting the schema has no
        such column is cheap and states the intent where a successor will
        meet it."""
        columns = set(orm.CapturePause.__table__.columns.keys())
        assert "ends_utc" in columns
        assert not any("remaining" in name or "duration" in name for name in columns)


class TestItLeavesARecord:
    def test_resuming_early_records_when_it_actually_stopped(self, db) -> None:
        controller = PauseController(
            session_factory=session_scope, timezone_provider=lambda: LONDON
        )
        controller.start("6h")
        controller.resume()

        (row,) = _rows()
        assert row.end_reason == ENDED_RESUMED
        assert row.ended_utc is not None
        # The deadline it was *set* to is preserved alongside when it really
        # ended: what the operator asked for is part of the record.
        assert row.ends_utc > row.ended_utc
        assert controller.active is False

    def test_pausing_again_replaces_rather_than_extends(self, db) -> None:
        """"1 hour" during a 15-minute pause means the party is going longer,
        not seventy-five minutes."""
        controller = PauseController(
            session_factory=session_scope, timezone_provider=lambda: LONDON
        )
        controller.start("15m")
        controller.start("1h")

        assert 3500 < controller.remaining_seconds() <= 3600
        rows = _rows()
        assert len(rows) == 2
        assert rows[0].end_reason == ENDED_SUPERSEDED
        assert rows[1].ended_utc is None

    def test_resuming_when_not_paused_is_harmless(self, db) -> None:
        controller = PauseController(
            session_factory=session_scope, timezone_provider=lambda: LONDON
        )
        assert controller.resume()["active"] is False
        assert _rows() == []

    def test_rows_left_open_by_a_crash_are_closed_and_the_live_one_is_not(self, db) -> None:
        """Two ways a row is left open, and they get different reasons.

        `restore` adopts (or expires) the most recent one, because that is the
        pause this station was actually in. Anything older is a row a crash
        orphaned some time ago: it is closed at its own deadline and marked
        `unknown`, because nobody can now say whether it ran to term. The pause
        currently running is never touched by the sweep -- which is the bug
        this asserts against, since closing it would leave the station paused
        with no open row to find after the next restart.
        """
        old = datetime.now(UTC) - timedelta(days=2)
        recent = datetime.now(UTC) - timedelta(days=1)
        with session_scope() as session:
            for started in (old, recent):
                session.add(
                    orm.CapturePause(
                        id=uuid.uuid4(),
                        started_utc=started,
                        ends_utc=started + timedelta(hours=1),
                        preset="1h",
                        label="1 hour",
                    )
                )
        controller = PauseController(
            session_factory=session_scope, timezone_provider=lambda: LONDON
        )
        controller.restore()
        controller.start("1h")
        controller.close_stale_rows()

        rows = _rows()
        assert rows[0].end_reason == "unknown"
        assert rows[1].end_reason == ENDED_EXPIRED
        assert rows[-1].ended_utc is None
        assert controller.active

    def test_persistence_failure_does_not_stop_the_pause_engaging(self) -> None:
        """A full disk must not leave the station recording a party.

        The in-memory deadline is set before anything is written, and the write
        is best-effort: losing the *record* of a pause is a documentation loss,
        losing the *pause* is a privacy failure.
        """

        def exploding_session():  # pragma: no cover - trivial
            raise RuntimeError("disk full")

        controller = PauseController(
            session_factory=exploding_session, timezone_provider=lambda: LONDON
        )
        state = controller.start("1h")
        assert controller.active
        assert state["active"] is True


class TestItIsVisible:
    def test_the_banner_names_a_clock_time_in_the_station_zone(self) -> None:
        controller = PauseController(timezone_provider=lambda: LONDON)
        controller.start("until-midnight")
        assert controller.banner(LONDON) == "PAUSED BY OPERATOR - RECORDING RESUMES 00:00"

    def test_the_banner_is_empty_when_not_paused(self) -> None:
        assert PauseController().banner(LONDON) == ""

    def test_the_display_shows_a_pause_ahead_of_every_other_state(self) -> None:
        """On the counter-top glass a pause outranks every fault line, because
        it is the only one of them the person standing in the kitchen can act
        on -- and it must not read as "listening"."""
        from open_observatory.display_channel import health_state

        state, detail = health_state(
            {
                "status": "ok",
                "capture": {"is_live_hardware": True, "state": "capturing"},
                "pause": {"active": True, "banner": "PAUSED BY OPERATOR - RESUMES 18:30"},
            }
        )
        assert state == "D"
        assert detail == "PAUSED BY OPERATOR - RESUMES 18:30"

    def test_an_inactive_pause_leaves_the_display_reading_listening(self) -> None:
        from open_observatory.display_channel import health_state

        assert health_state(
            {
                "status": "ok",
                "capture": {"is_live_hardware": True, "state": "capturing"},
                "pause": {"active": False},
            }
        ) == ("L", "")


class TestCoverageShowsItAsDeliberate:
    def test_a_pause_is_reported_beside_coverage_and_never_subtracted_from_it(
        self, db
    ) -> None:
        """Charter item 2. A silent afternoon has three possible explanations
        and coverage has to distinguish all three: nothing called, nothing was
        recording, or somebody paused it. Deducting the pause from
        `seconds_captured` would collapse the third into the second.
        """
        stream_id = uuid.uuid4()
        start = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
        end = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
        with session_scope() as session:
            session.add(
                orm.AudioStream(
                    id=stream_id,
                    source_kind="alsa",
                    start_utc=start,
                    end_utc=end,
                    start_monotonic_ns=0,
                    end_monotonic_ns=6 * 3600 * 1_000_000_000,
                    sample_rate=48000,
                    sample_format="S16_LE",
                    frame_count=48000 * 6 * 3600,
                    last_frame_at_utc=end,
                    end_reason="closed",
                )
            )
            session.add(
                orm.CapturePause(
                    id=uuid.uuid4(),
                    started_utc=datetime(2026, 8, 8, 14, 0, tzinfo=UTC),
                    ends_utc=datetime(2026, 8, 8, 17, 0, tzinfo=UTC),
                    ended_utc=datetime(2026, 8, 8, 16, 0, tzinfo=UTC),
                    end_reason=ENDED_RESUMED,
                    preset="3h",
                    label="3 hours",
                )
            )

        window = history.Range(start=start, end=end, label="test")
        with session_scope() as session:
            result = history.coverage(session, window)

        assert result["seconds_captured"] == pytest.approx(6 * 3600, abs=1)
        # Two hours: 14:00 to when it was actually resumed at 16:00, not to the
        # 17:00 it was set to.
        assert result["seconds_paused"] == pytest.approx(2 * 3600, abs=1)
        assert len(result["pauses"]) == 1
        assert result["pauses"][0]["end_reason"] == ENDED_RESUMED
        assert result["pauses"][0]["running"] is False

    def test_overlapping_pauses_are_merged_before_being_summed(self, db) -> None:
        """The 1302%-coverage arithmetic, in its new home. A restart mid-pause
        or a superseded pause produces overlapping rows, and adding those up
        would report more paused time than the window contains."""
        start = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
        end = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
        with session_scope() as session:
            for offset_h, length_h in ((1, 3), (2, 3)):
                session.add(
                    orm.CapturePause(
                        id=uuid.uuid4(),
                        started_utc=start + timedelta(hours=offset_h),
                        ends_utc=start + timedelta(hours=offset_h + length_h),
                        ended_utc=start + timedelta(hours=offset_h + length_h),
                        end_reason=ENDED_EXPIRED,
                        preset="3h",
                        label="3 hours",
                    )
                )

        window = history.Range(start=start, end=end, label="test")
        with session_scope() as session:
            result = history.coverage(session, window)

        # 13:00-16:00 and 14:00-17:00 is four hours, not six.
        assert result["seconds_paused"] == pytest.approx(4 * 3600, abs=1)

    def test_a_running_pause_never_claims_time_that_has_not_happened(self, db) -> None:
        """An open row runs to a deadline in the future. Counting up to that
        deadline would be a coverage figure about time that has not occurred."""
        now = datetime.now(UTC)
        with session_scope() as session:
            session.add(
                orm.CapturePause(
                    id=uuid.uuid4(),
                    started_utc=now - timedelta(minutes=30),
                    ends_utc=now + timedelta(hours=5),
                    preset="6h",
                    label="6 hours",
                )
            )
        window = history.Range(
            start=now - timedelta(hours=1), end=now + timedelta(hours=6), label="t"
        )
        with session_scope() as session:
            result = history.coverage(session, window)

        assert result["seconds_paused"] == pytest.approx(30 * 60, abs=5)
        assert result["pauses"][0]["running"] is True
