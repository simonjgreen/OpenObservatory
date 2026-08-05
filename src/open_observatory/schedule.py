"""Night scheduler: gates a detector to civil dusk through civil dawn.

Why this exists (Milestone 5, IMPLEMENTATION_PLAN.md item 2): a detector that runs
at two in the afternoon reports bat passes from wind, machinery and handling
noise, and no threshold tuning can tell those apart from a real pulse train,
because a broadband transient genuinely resembles one. The clock carries
information the signal does not.

Failure mode, chosen deliberately (also specified in the plan): if the station's
coordinates are unset there is no schedule to compute, so the detector runs
continuously and :meth:`NightSchedule.state` says why. It must never silently gate
to nothing overnight, because a station that records nothing looks identical to a
quiet night, and that confusion is exactly what this module exists to prevent.

The solar geometry is the low-precision NOAA algorithm (the same family of
formulas behind NOAA's published solar calculator spreadsheet), accurate to a few
minutes for civil twilight at UK latitudes -- adequate for gating a detector, not
an astronomical almanac. It is standard-library plus numpy only, no third-party
solar package. All internal arithmetic and all public timestamps are tz-aware UTC;
this module never touches local wall-clock time or the host's timezone, so
transitions like British Summer Time have no effect on it whatsoever.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import numpy as np

#: Civil twilight is defined as the sun's centre at -6 degrees elevation, i.e. a
#: zenith angle of 96 degrees (90 + 6).
_CIVIL_ZENITH_DEG = 96.0

ScheduleMode = Literal["always", "night"]


@dataclass(frozen=True, slots=True)
class SolarWindow:
    """A single night: civil dusk through to the *following* civil dawn."""

    dusk_utc: datetime
    dawn_utc: datetime


def _day_of_year(d: date) -> int:
    return d.timetuple().tm_yday


def _dawn_dusk_for_date(
    d: date, latitude: float, longitude: float
) -> tuple[datetime | None, datetime | None]:
    """Civil dawn and dusk occurring on calendar date ``d`` (UTC), or ``None``
    for each if the sun does not cross -6 degrees elevation that day (polar day
    or polar night at high latitude).

    Longitude is degrees, positive east (so western Europe is negative), matching
    this project's ``Settings.longitude``.
    """
    # Fractional-year angle gamma, evaluated at local solar noon (hour = 12), per
    # the NOAA General Solar Position Calculations formulas.
    n = _day_of_year(d)
    days_in_year = 366.0 if date(d.year, 12, 31).timetuple().tm_yday == 366 else 365.0
    gamma = 2.0 * np.pi / days_in_year * (n - 1)

    # Equation of time, in minutes.
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * np.cos(gamma)
        - 0.032077 * np.sin(gamma)
        - 0.014615 * np.cos(2 * gamma)
        - 0.040849 * np.sin(2 * gamma)
    )

    # Solar declination, in radians.
    decl = (
        0.006918
        - 0.399912 * np.cos(gamma)
        + 0.070257 * np.sin(gamma)
        - 0.006758 * np.cos(2 * gamma)
        + 0.000907 * np.sin(2 * gamma)
        - 0.002697 * np.cos(3 * gamma)
        + 0.00148 * np.sin(3 * gamma)
    )

    lat_rad = np.radians(latitude)
    zenith_rad = np.radians(_CIVIL_ZENITH_DEG)

    denom = np.cos(lat_rad) * np.cos(decl)
    if denom == 0:
        return None, None
    cos_ha = (np.cos(zenith_rad) - np.sin(lat_rad) * np.sin(decl)) / denom
    if cos_ha < -1.0 or cos_ha > 1.0:
        # Sun never reaches -6 degrees elevation on this date at this latitude:
        # either it never sets that low (polar/midsummer day) or never rises that
        # high (polar night). Either way there is no civil-twilight crossing.
        return None, None
    ha_deg = np.degrees(np.arccos(cos_ha))

    solar_noon_min = 720.0 - 4.0 * longitude - eqtime
    dawn_min = solar_noon_min - 4.0 * ha_deg
    dusk_min = solar_noon_min + 4.0 * ha_deg

    midnight = datetime(d.year, d.month, d.day, tzinfo=UTC)
    dawn_utc = midnight + timedelta(minutes=float(dawn_min))
    dusk_utc = midnight + timedelta(minutes=float(dusk_min))
    return dawn_utc, dusk_utc


class NightSchedule:
    """Gates a detector to civil dusk through civil dawn (plus margins).

    ``mode="always"`` never gates. ``mode="night"`` gates, except that missing
    coordinates or a date with no civil twilight both fall back to "always
    active" rather than silently gating everything off -- see module docstring.
    """

    def __init__(
        self,
        *,
        mode: str = "always",
        latitude: float | None = None,
        longitude: float | None = None,
        dusk_margin_min: float = 30.0,
        dawn_margin_min: float = 30.0,
    ) -> None:
        self.mode = mode
        self.latitude = latitude
        self.longitude = longitude
        self.dusk_margin_min = dusk_margin_min
        self.dawn_margin_min = dawn_margin_min

    @property
    def _has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def _window_starting(self, start_date: date) -> SolarWindow | None:
        """The night that begins with dusk on ``start_date`` and ends with the
        following calendar date's dawn, or ``None`` if either leg has no civil
        twilight."""
        assert self.latitude is not None and self.longitude is not None
        _, dusk = _dawn_dusk_for_date(start_date, self.latitude, self.longitude)
        dawn, _ = _dawn_dusk_for_date(start_date + timedelta(days=1), self.latitude, self.longitude)
        if dusk is None or dawn is None:
            return None
        return SolarWindow(dusk_utc=dusk, dawn_utc=dawn)

    def _resolve_window(self, now: datetime) -> SolarWindow | None:
        """The night relevant to ``now``: the one already under way (if any),
        otherwise the upcoming one. ``None`` means no civil twilight could be
        computed for either candidate night."""
        now_utc = now.astimezone(UTC)
        today = now_utc.date()

        # Containment is checked with margins applied, not just the raw civil
        # twilight instants, so a time inside the margin -- but past the bare
        # dawn -- still resolves to last night's window rather than jumping
        # ahead to the following night.
        previous_night = self._window_starting(today - timedelta(days=1))
        if previous_night is not None:
            lo = previous_night.dusk_utc - timedelta(minutes=self.dusk_margin_min)
            hi = previous_night.dawn_utc + timedelta(minutes=self.dawn_margin_min)
            if lo <= now_utc <= hi:
                return previous_night

        tonight = self._window_starting(today)
        if tonight is not None:
            return tonight
        return previous_night

    def window_for(self, now: datetime) -> SolarWindow | None:
        """The civil-dusk-to-civil-dawn window relevant to ``now``.

        Returns ``None`` when ``mode`` is not ``"night"``, when coordinates are
        unset, or when no civil twilight crossing could be found (high latitude).
        """
        if self.mode != "night" or not self._has_coordinates:
            return None
        return self._resolve_window(now)

    def is_active(self, now: datetime) -> bool:
        if self.mode != "night":
            return True
        if not self._has_coordinates:
            return True
        window = self._resolve_window(now)
        if window is None:
            # No civil twilight computable for this date/latitude: fail open.
            return True
        now_utc = now.astimezone(UTC)
        lo = window.dusk_utc - timedelta(minutes=self.dusk_margin_min)
        hi = window.dawn_utc + timedelta(minutes=self.dawn_margin_min)
        return lo <= now_utc <= hi

    def state(self, now: datetime) -> dict:
        """JSON-serialisable schedule state: current activity plus why."""
        if self.mode != "night":
            return {
                "mode": self.mode,
                "active": True,
                "reason": "always",
                "dusk_utc": None,
                "dawn_utc": None,
            }

        if not self._has_coordinates:
            return {
                "mode": self.mode,
                "active": True,
                "reason": "coordinates-unset",
                "dusk_utc": None,
                "dawn_utc": None,
            }

        window = self._resolve_window(now)
        if window is None:
            return {
                "mode": self.mode,
                "active": True,
                "reason": "no-civil-twilight",
                "dusk_utc": None,
                "dawn_utc": None,
            }

        active = self.is_active(now)
        return {
            "mode": self.mode,
            "active": active,
            "reason": "within-night-window" if active else "outside-night-window",
            "dusk_utc": window.dusk_utc.isoformat(),
            "dawn_utc": window.dawn_utc.isoformat(),
        }
