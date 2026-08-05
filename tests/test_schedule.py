"""Tests for the night scheduler (civil-twilight gating).

Reference dusk/dawn values are for the development station (51.4769 N, -0.0005 W)
and were fetched from the sunrise-sunset.org API on 2026-08-05:

    https://api.sunrise-sunset.org/json?lat=51.4769&lng=-0.0005&date=<date>&formatted=0

which reports ``civil_twilight_begin``/``civil_twilight_end`` in UTC, computed from
the standard NOAA solar-position formulas this module also implements. Times below
are copied verbatim from that response (see comments beside each). A tolerance of a
few minutes is used because the two implementations round intermediate steps
slightly differently.

The 2026 British Summer Time boundary (clocks change on the last Sunday of March
and October in the UK) falls on 29 March 2026. 28 March and 30 March straddle it;
both dates are exercised below. Since this module works entirely in UTC and never
consults the local civil calendar, the BST transition itself must have zero effect
on the computed times -- which is exactly what these two dates confirm.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from open_observatory.schedule import NightSchedule, SolarWindow

CHARTER_ALLEY_LAT = 51.4769
CHARTER_ALLEY_LON = -0.0005

TOLERANCE = timedelta(minutes=5)


def _assert_close(actual: datetime, expected: datetime, tolerance: timedelta = TOLERANCE) -> None:
    diff = abs(actual - expected)
    assert diff <= tolerance, f"{actual.isoformat()} not within {tolerance} of {expected.isoformat()}"


def test_summer_dusk_and_dawn() -> None:
    # 2026-06-21: civil_twilight_end 21:11:49Z; 2026-06-22: civil_twilight_begin 03:01:15Z.
    schedule = NightSchedule(mode="night", latitude=CHARTER_ALLEY_LAT, longitude=CHARTER_ALLEY_LON)
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    window = schedule.window_for(now)
    assert window is not None
    _assert_close(window.dusk_utc, datetime(2026, 6, 21, 21, 11, 49, tzinfo=UTC))
    _assert_close(window.dawn_utc, datetime(2026, 6, 22, 3, 1, 15, tzinfo=UTC))


def test_winter_dusk_and_dawn() -> None:
    # 2026-12-21: civil_twilight_end 16:38:29Z; 2026-12-22: civil_twilight_begin 07:27:18Z.
    schedule = NightSchedule(mode="night", latitude=CHARTER_ALLEY_LAT, longitude=CHARTER_ALLEY_LON)
    now = datetime(2026, 12, 21, 12, 0, tzinfo=UTC)
    window = schedule.window_for(now)
    assert window is not None
    _assert_close(window.dusk_utc, datetime(2026, 12, 21, 16, 38, 29, tzinfo=UTC))
    _assert_close(window.dawn_utc, datetime(2026, 12, 22, 7, 27, 18, tzinfo=UTC))


def test_dates_either_side_of_bst_boundary() -> None:
    # UK BST 2026 starts 29 March 2026. 28 and 30 March straddle it. Both are pure
    # UTC computations, so there must be no discontinuity across the boundary.
    # 2026-03-28: civil_twilight_end 19:04:03Z; 2026-03-29: civil_twilight_begin 05:12:53Z.
    schedule = NightSchedule(mode="night", latitude=CHARTER_ALLEY_LAT, longitude=CHARTER_ALLEY_LON)

    before = schedule.window_for(datetime(2026, 3, 28, 12, 0, tzinfo=UTC))
    assert before is not None
    _assert_close(before.dusk_utc, datetime(2026, 3, 28, 19, 4, 3, tzinfo=UTC))
    _assert_close(before.dawn_utc, datetime(2026, 3, 29, 5, 12, 53, tzinfo=UTC))

    # 2026-03-30: civil_twilight_end 19:07:31Z; 2026-03-31: civil_twilight_begin 05:08:13Z.
    after = schedule.window_for(datetime(2026, 3, 30, 12, 0, tzinfo=UTC))
    assert after is not None
    _assert_close(after.dusk_utc, datetime(2026, 3, 30, 19, 7, 31, tzinfo=UTC))
    _assert_close(after.dawn_utc, datetime(2026, 3, 31, 5, 8, 13, tzinfo=UTC))


def test_window_spans_midnight() -> None:
    schedule = NightSchedule(mode="night", latitude=CHARTER_ALLEY_LAT, longitude=CHARTER_ALLEY_LON)
    # Mid-winter night: well after dusk, well after local midnight, well before dawn.
    two_am = datetime(2026, 12, 22, 2, 0, tzinfo=UTC)
    assert schedule.is_active(two_am) is True

    # Mid-afternoon, same "night" reference point: must not be active.
    two_pm = datetime(2026, 12, 21, 14, 0, tzinfo=UTC)
    assert schedule.is_active(two_pm) is False


def test_margins_widen_the_window() -> None:
    no_margin = NightSchedule(
        mode="night",
        latitude=CHARTER_ALLEY_LAT,
        longitude=CHARTER_ALLEY_LON,
        dusk_margin_min=0.0,
        dawn_margin_min=0.0,
    )
    wide_margin = NightSchedule(
        mode="night",
        latitude=CHARTER_ALLEY_LAT,
        longitude=CHARTER_ALLEY_LON,
        dusk_margin_min=45.0,
        dawn_margin_min=45.0,
    )

    # 20 minutes before civil dusk on 2026-12-21 (dusk ~16:38:29Z): inactive with no
    # margin, active once a 45-minute dusk margin is applied.
    just_before_dusk = datetime(2026, 12, 21, 16, 18, tzinfo=UTC)
    assert no_margin.is_active(just_before_dusk) is False
    assert wide_margin.is_active(just_before_dusk) is True

    # 20 minutes after civil dawn on 2026-12-22 (dawn ~07:27:18Z): inactive with no
    # margin, active once a 45-minute dawn margin is applied.
    just_after_dawn = datetime(2026, 12, 22, 7, 47, tzinfo=UTC)
    assert no_margin.is_active(just_after_dawn) is False
    assert wide_margin.is_active(just_after_dawn) is True


def test_mode_always_is_always_active() -> None:
    schedule = NightSchedule(mode="always")
    assert schedule.is_active(datetime(2026, 1, 1, 12, 0, tzinfo=UTC)) is True
    assert schedule.is_active(datetime(2026, 7, 15, 3, 0, tzinfo=UTC)) is True
    state = schedule.state(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    assert state["mode"] == "always"
    assert state["active"] is True
    assert state["reason"] == "always"


def test_mode_night_without_coordinates_is_always_active() -> None:
    schedule = NightSchedule(mode="night", latitude=None, longitude=None)
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert schedule.is_active(now) is True
    state = schedule.state(now)
    assert state["active"] is True
    assert state["reason"] == "coordinates-unset"
    assert state["dusk_utc"] is None
    assert state["dawn_utc"] is None

    # Also true with only one coordinate set.
    partial = NightSchedule(mode="night", latitude=CHARTER_ALLEY_LAT, longitude=None)
    assert partial.is_active(now) is True
    assert partial.state(now)["reason"] == "coordinates-unset"


def test_high_latitude_no_civil_twilight_stays_active() -> None:
    # 70 N, 25 E at the June solstice: midnight sun, sun never reaches -6 degrees
    # elevation. Confirmed via sunrise-sunset.org returning the library's
    # "no twilight" sentinel (1970-01-01T00:00:01Z) for civil_twilight_begin/end at
    # these coordinates on this date.
    schedule = NightSchedule(mode="night", latitude=70.0, longitude=25.0)
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)

    assert schedule.is_active(now) is True

    state = schedule.state(now)
    assert state["active"] is True
    assert state["reason"] == "no-civil-twilight"


def test_state_reports_within_and_outside_night_window() -> None:
    schedule = NightSchedule(mode="night", latitude=CHARTER_ALLEY_LAT, longitude=CHARTER_ALLEY_LON)

    night = schedule.state(datetime(2026, 12, 22, 2, 0, tzinfo=UTC))
    assert night["mode"] == "night"
    assert night["active"] is True
    assert night["reason"] == "within-night-window"
    assert night["dusk_utc"] is not None
    assert night["dawn_utc"] is not None

    day = schedule.state(datetime(2026, 12, 21, 14, 0, tzinfo=UTC))
    assert day["active"] is False
    assert day["reason"] == "outside-night-window"


def test_solar_window_is_frozen_dataclass() -> None:
    window = SolarWindow(
        dusk_utc=datetime(2026, 1, 1, 18, 0, tzinfo=UTC),
        dawn_utc=datetime(2026, 1, 2, 7, 0, tzinfo=UTC),
    )
    assert window.dusk_utc.tzinfo is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        window.dusk_utc = datetime(2026, 1, 1, tzinfo=UTC)  # type: ignore[misc]
