"""The stream clock must survive an NTP step (ADR-063).

`StreamClock` anchors frame zero to UTC once and then counts frames. That is
what makes it drift-free against the monotonic clock, and it is also why a
*step* to the system wall clock afterwards is baked in for the life of the
stream.

On 2026-08-17 the station booted, opened capture at 10:07:30 BST, anchored at
10:07:32, and systemd-timesyncd made its first NTP contact at 10:09:17. The Pi
has no battery-backed RTC, so the pre-sync clock was ~106 seconds slow. Every
UTC timestamp the station produced for the next 49 hours -- every detection's
`event_start_utc`, every clip filename, every spectrogram column -- was 106
seconds early, and nothing in the system said so. It was found by eye, from a
live-listen banner in the browser reading "hearing 111.3 s ahead of the newest
column", and confirmed against the station's own arithmetic: over 178,749 s of
wall time it had delivered 178,634 s of audio.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from open_observatory.audio.contracts import NS_PER_S, ClockCorrelation, StreamClock

STEP_S = 106.0


def _clock_at(utc_s: float, monotonic_s: float) -> StreamClock:
    return StreamClock(
        utc_ns_at_frame_zero=int(utc_s * NS_PER_S),
        monotonic_ns_at_frame_zero=int(monotonic_s * NS_PER_S),
    )


class TestStepDetection:
    def test_an_unstepped_clock_reports_no_step(self) -> None:
        clock = _clock_at(utc_s=1_000_000.0, monotonic_s=500.0)
        later = ClockCorrelation(
            monotonic_ns=int(4_000 * NS_PER_S),
            utc_ns=int((1_000_000.0 + 3_500.0) * NS_PER_S),
        )
        assert clock.stepped_by(later) == 0

    def test_it_measures_the_real_step_from_the_incident(self) -> None:
        clock = _clock_at(utc_s=1_000_000.0, monotonic_s=500.0)
        # An hour later by the monotonic clock, but the wall clock has also been
        # stepped forward by 106 s in the meantime.
        stepped = ClockCorrelation(
            monotonic_ns=int(4_100 * NS_PER_S),
            utc_ns=int((1_000_000.0 + 3_600.0 + STEP_S) * NS_PER_S),
        )
        assert clock.stepped_by(stepped) == pytest.approx(STEP_S * NS_PER_S)

    def test_a_backwards_step_is_reported_negative(self) -> None:
        clock = _clock_at(utc_s=1_000_000.0, monotonic_s=500.0)
        stepped = ClockCorrelation(
            monotonic_ns=int(1_500 * NS_PER_S),
            utc_ns=int((1_000_000.0 + 1_000.0 - 30.0) * NS_PER_S),
        )
        assert clock.stepped_by(stepped) == pytest.approx(-30.0 * NS_PER_S)


class TestReanchoring:
    def test_it_moves_utc_and_leaves_monotonic_alone(self) -> None:
        """The monotonic anchor is load-bearing for ordering, gaps and duration.

        Those measurements are all keyed to a clock that cannot step, and they
        must stay valid across a re-anchor -- only the UTC *name* of an instant
        changes.
        """
        clock = _clock_at(utc_s=1_000_000.0, monotonic_s=500.0)
        stepped = ClockCorrelation(
            monotonic_ns=int(4_100 * NS_PER_S),
            utc_ns=int((1_000_000.0 + 3_600.0 + STEP_S) * NS_PER_S),
        )
        fixed = clock.reanchored(stepped)

        assert fixed.monotonic_ns_at_frame_zero == clock.monotonic_ns_at_frame_zero
        assert fixed.utc_ns_at_frame_zero == clock.utc_ns_at_frame_zero + int(
            STEP_S * NS_PER_S
        )

    def test_frame_timestamps_land_on_the_corrected_timeline(self) -> None:
        """The point of the whole exercise, stated as the user-visible fact."""
        clock = _clock_at(utc_s=1_000_000.0, monotonic_s=500.0)
        rate = 48_000
        frame = rate * 3_600  # one hour of audio in

        stepped = ClockCorrelation(
            monotonic_ns=int(4_100 * NS_PER_S),
            utc_ns=int((1_000_000.0 + 3_600.0 + STEP_S) * NS_PER_S),
        )
        before = clock.utc_ns(frame, rate) / NS_PER_S
        after = clock.reanchored(stepped).utc_ns(frame, rate) / NS_PER_S

        assert after - before == pytest.approx(STEP_S)
        # And it now agrees with the wall clock, which is what "correct" means.
        assert after == pytest.approx(1_000_000.0 + 3_600.0 + STEP_S)

    def test_reanchoring_is_idempotent(self) -> None:
        clock = _clock_at(utc_s=1_000_000.0, monotonic_s=500.0)
        stepped = ClockCorrelation(
            monotonic_ns=int(4_100 * NS_PER_S),
            utc_ns=int((1_000_000.0 + 3_600.0 + STEP_S) * NS_PER_S),
        )
        once = clock.reanchored(stepped)
        assert once.reanchored(stepped) == once
        assert once.stepped_by(stepped) == 0


class TestStationReanchorsOnItsHousekeepingTick:
    def _station(self):
        from open_observatory.config import Settings
        from open_observatory.station import Station

        station = Station(Settings())
        station.stream = SimpleNamespace(stream_id="test-stream")
        return station

    def test_a_step_past_the_threshold_reanchors_and_is_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        station = self._station()
        now_mono = time.monotonic_ns()
        station.clock = StreamClock(
            utc_ns_at_frame_zero=now_mono + 1_000 * NS_PER_S,
            monotonic_ns_at_frame_zero=now_mono,
        )
        before = station.clock

        stepped = ClockCorrelation(
            monotonic_ns=now_mono + 60 * NS_PER_S,
            utc_ns=now_mono + (1_000 + 60) * NS_PER_S + int(STEP_S * NS_PER_S),
        )
        monkeypatch.setattr(ClockCorrelation, "sample", classmethod(lambda cls: stepped))

        station._reanchor_clock_if_stepped()

        assert station.clock is not before
        assert station.clock.utc_ns_at_frame_zero == before.utc_ns_at_frame_zero + int(
            STEP_S * NS_PER_S
        )
        assert station.counters.clock_reanchors == 1
        assert station.counters.clock_last_step_s == pytest.approx(STEP_S)

    def test_ordinary_ntp_slew_does_not_reanchor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NTP slews at up to 500 ppm -- ~5 ms across a 10 s tick.

        Chasing that would reintroduce exactly the per-block timestamp jitter
        `StreamClock` exists to remove, so the threshold sits three orders of
        magnitude above it.
        """
        station = self._station()
        now_mono = time.monotonic_ns()
        station.clock = StreamClock(
            utc_ns_at_frame_zero=now_mono + 1_000 * NS_PER_S,
            monotonic_ns_at_frame_zero=now_mono,
        )
        before = station.clock

        slewed = ClockCorrelation(
            monotonic_ns=now_mono + 10 * NS_PER_S,
            utc_ns=now_mono + (1_000 + 10) * NS_PER_S + 5_000_000,  # +5 ms
        )
        monkeypatch.setattr(ClockCorrelation, "sample", classmethod(lambda cls: slewed))

        station._reanchor_clock_if_stepped()

        assert station.clock is before
        assert station.counters.clock_reanchors == 0

    def test_it_is_a_no_op_before_the_first_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        station = self._station()
        station.clock = None
        station._reanchor_clock_if_stepped()
        assert station.clock is None
        assert station.counters.clock_reanchors == 0

    def test_the_housekeeping_tick_actually_calls_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wiring, asserted separately from behaviour.

        The behaviour above is exercised by calling the method directly, which
        proves nothing about whether anything ever calls it -- and a correct fix
        nobody invokes is how the original defect survived in the first place.
        """
        station = self._station()
        called = 0

        def _count() -> None:
            nonlocal called
            called += 1

        monkeypatch.setattr(station, "_reanchor_clock_if_stepped", _count)
        monkeypatch.setattr(station.leases, "sweep", lambda: None)
        monkeypatch.setattr(station, "status_snapshot", lambda: {})

        ticks = 0

        async def _fake_sleep(_seconds: float) -> None:
            nonlocal ticks
            ticks += 1
            if ticks >= 3:
                station._running = False

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
        station._running = True
        asyncio.run(station._housekeeping_loop())

        assert called >= 1
