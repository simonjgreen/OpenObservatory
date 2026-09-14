"""Long-term analytics: a rebuildable roll-up of the detection table, one local
calendar day at a time, and the questions that are asked of it (ADR-079).

Why a roll-up at all: the detection table costs about 16 µs per row on the
station to aggregate (ADR-056), which at 40,000 rows a day makes a season a
minute and a year longer than anyone waits. The tables here hold a day's
detections as a few hundred summary rows -- per hour, per group, per label --
so a year of them is milliseconds. They are a cache with a schema, never a
record: every row carries the version that built it, and any day can be thrown
away and rebuilt from the detections, which is what happens when a review or a
withdrawal changes what a day should say.

Three rules this module keeps, all inherited:

- **Local calendar days, in the station's timezone.** UTC-aligned day buckets
  filed 15% of this station's bats on the wrong date (ADR-056 finding 4). A
  "night" is noon to noon, keyed by the evening's date.
- **Only the microphone counts.** Synthetic and replay rows are excluded
  everywhere and counted (ADR-020), through the same ``is_live`` predicate the
  history views use.
- **Naming a species is a claim.** ``analytics_taxon_hour`` is the one table
  that names things, so it excludes withdrawn rows (ADR-044) and rows a human
  has rejected, honours a human correction (ADR-043), and counts what it
  excluded. The group-level tables count detections and name nothing, so a
  withdrawn row -- which did occur -- stays in them, exactly as it stays in
  ``history.timeline``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any
from typing import cast as typing_cast
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import Integer, and_, cast, delete, func, or_, select
from sqlalchemy.orm import Session

from . import history, review
from .db import models as orm
from .db.session import session_scope
from .evidence_value import BAND_WIDTH_HZ
from .schedule import SolarDay, solar_day

log = structlog.get_logger(__name__)

#: Bumped whenever the meaning of a roll-up row changes; every stored day
#: carrying an older version is rebuilt by the next run.
BUILDER_VERSION = "1"

#: A day older than this is rebuilt on a rolling basis, a few per run, so a
#: withdrawal or a refinement that touched an old day reaches the roll-up
#: within a week without anyone tracking which rows changed.
STALE_AFTER_DAYS = 7
STALE_DAYS_PER_RUN = 6
#: Days never built (a fresh install, or a long gap) are caught up newest first,
#: this many per run, so the recent past appears first and a backfill of months
#: cannot hold the hourly timer for hours.
CATCHUP_DAYS_PER_RUN = 40

#: Grains a series can be asked in. ``night`` is noon to noon.
GRAINS = ("day", "night", "week", "month")
#: Groups that name an organism, in the order charts stack them.
GROUP_ORDER = ("bird", "bat", "acoustic_event", "noise", "unknown")

BAT_GROUP = "bat"
REJECTED = review.REJECTED_STATUS


# ---------------------------------------------------------------------------
# Local days


def _zone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except Exception:  # a bad zone name degrades to UTC, as history does
        return UTC


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def local_midnight(d: date, zone: tzinfo) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=zone)


def day_bounds(d: date, zone: tzinfo) -> tuple[datetime, datetime]:
    """UTC bounds of local calendar day ``d``: 23, 24 or 25 hours long."""
    start = local_midnight(d, zone).astimezone(UTC)
    end = local_midnight(d + timedelta(days=1), zone).astimezone(UTC)
    return start, end


def hour_windows(d: date, zone: tzinfo) -> list[tuple[datetime, datetime]]:
    """The 24 local wall-clock hours of ``d`` as UTC intervals.

    On the spring-forward day one of them is empty and on the fall-back day
    one is two hours long; both are the truth about that wall-clock hour.
    """
    # Each boundary is built from its own local wall-clock timestamp rather
    # than by adding hours to midnight, so a mid-day offset change (the
    # clocks going forward or back) lands in the right hour.
    fixed: list[tuple[datetime, datetime]] = []
    for hour in range(24):
        start_local = datetime(d.year, d.month, d.day, hour, tzinfo=zone)
        end_local = (
            datetime(d.year, d.month, d.day, hour + 1, tzinfo=zone)
            if hour < 23
            else local_midnight(d + timedelta(days=1), zone)
        )
        start = start_local.astimezone(UTC)
        end = end_local.astimezone(UTC)
        fixed.append((start, max(start, end)))
    return fixed


def local_hour(value: datetime | None, d: date, zone: tzinfo) -> float | None:
    """Hours since local midnight of ``d`` for an instant, e.g. 6.42."""
    if value is None:
        return None
    return round((_aware(value) - local_midnight(d, zone)).total_seconds() / 3600.0, 3)


def _merge(intervals: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _overlap_seconds(
    intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime
) -> float:
    total = 0.0
    for a, b in intervals:
        lo = max(a, start)
        hi = min(b, end)
        if hi > lo:
            total += (hi - lo).total_seconds()
    return total


# ---------------------------------------------------------------------------
# Building one day


@dataclass(slots=True)
class DayBuild:
    local_date: date
    detections: int = 0
    excluded_synthetic: int = 0
    excluded_withdrawn: int = 0
    excluded_rejected: int = 0
    seconds: float = 0.0
    complete: bool = False
    #: Every fact the day row will carry, for the report.
    row: dict[str, Any] = field(default_factory=dict)


def _latest_review_subquery() -> Any:
    """The current review per detection, ``review.latest_reviews_by_detection``'s
    shape without the id list: the roll-up wants every reviewed row in a day."""
    latest_ts = (
        select(orm.Review.detection_id, func.max(orm.Review.created_at).label("ts"))
        .group_by(orm.Review.detection_id)
        .subquery("latest_ts")
    )
    return (
        select(
            orm.Review.detection_id.label("detection_id"),
            orm.Review.status.label("status"),
            orm.Review.corrected_common_name.label("corrected_common_name"),
            orm.Review.corrected_scientific_name.label("corrected_scientific_name"),
        )
        .join(
            latest_ts,
            and_(
                orm.Review.detection_id == latest_ts.c.detection_id,
                orm.Review.created_at == latest_ts.c.ts,
            ),
        )
        .subquery("latest_review")
    )


def _live_detections(start: datetime, end: datetime) -> Any:
    """The base select for a day: live-source detections inside ``[start, end)``."""
    return (
        select(orm.Detection)
        .join(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
        .where(
            orm.Detection.event_start_utc >= start,
            orm.Detection.event_start_utc < end,
            history.is_live(orm.AudioStream.source_kind),
        )
    )


def _bat_label(band: int | None) -> str:
    if band is None:
        return "bat pass (no peak)"
    low = band * (BAND_WIDTH_HZ // 1000)
    return f"{low}\u2013{low + BAND_WIDTH_HZ // 1000} kHz"


def build_day(
    session: Session,
    d: date,
    *,
    timezone: str,
    latitude: float | None,
    longitude: float | None,
    now: datetime | None = None,
) -> DayBuild:
    """Replace every roll-up row for local day ``d`` from the detection table.

    One transaction: the caller's session commits or nothing changes. A day
    is written even when it holds no detections, so "nothing was heard" and
    "nothing was built" stay distinguishable.
    """
    started = time.monotonic()
    moment = now or datetime.now(UTC)
    zone = _zone(timezone)
    day_start, day_end = day_bounds(d, zone)
    build = DayBuild(local_date=d)

    # ---- coverage, by hour and by solar window ---------------------------
    window = history.Range(day_start, day_end, d.isoformat())
    cov = history.coverage(session, window)
    streams = typing_cast(list[dict[str, Any]], cov["streams"])
    pauses = typing_cast(list[dict[str, Any]], cov["pauses"])
    live_intervals = _merge(
        (datetime.fromisoformat(str(s["start_utc"])), datetime.fromisoformat(str(s["end_utc"])))
        for s in streams
        if s["source_kind"] == history.LIVE_SOURCE_KIND
    )
    all_intervals = _merge(
        (datetime.fromisoformat(str(s["start_utc"])), datetime.fromisoformat(str(s["end_utc"])))
        for s in streams
    )
    pause_intervals = _merge(
        (datetime.fromisoformat(str(p["start_utc"])), datetime.fromisoformat(str(p["end_utc"])))
        for p in pauses
    )

    solar: SolarDay | None = None
    if latitude is not None and longitude is not None:
        solar = solar_day(d, latitude, longitude)

    def captured_in(start: datetime | None, end: datetime | None) -> float:
        if start is None or end is None or end <= start:
            return 0.0
        return _overlap_seconds(live_intervals, max(start, day_start), min(end, day_end))

    daylight_captured = captured_in(solar.sunrise_utc if solar else None, solar.sunset_utc if solar else None)
    dusk_captured = captured_in(solar.civil_dusk_utc if solar else None, day_end)
    dawn_captured = captured_in(day_start, solar.civil_dawn_utc if solar else None)

    # ---- detections per hour, per group and per label -------------------
    hours = hour_windows(d, zone)
    latest = _latest_review_subquery()

    group_hour: dict[tuple[int, str], tuple[int, float]] = {}
    taxon_hour: dict[tuple[int, str, str], list[Any]] = {}
    for index, (h_start, h_end) in enumerate(hours):
        if h_end <= h_start:
            continue
        base = (
            select(
                orm.Detection.taxonomic_group,
                func.count().label("n"),
                func.max(orm.Detection.score).label("best"),
            )
            .join(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
            .where(
                orm.Detection.event_start_utc >= h_start,
                orm.Detection.event_start_utc < h_end,
                history.is_live(orm.AudioStream.source_kind),
            )
            .group_by(orm.Detection.taxonomic_group)
        )
        for hrow in session.execute(base).all():
            group_hour[(index, hrow.taxonomic_group)] = (int(hrow.n), float(hrow.best or 0.0))

        name = func.coalesce(latest.c.corrected_common_name, orm.Detection.common_name)
        sci = func.coalesce(latest.c.corrected_scientific_name, orm.Detection.scientific_name)
        band = cast(orm.Detection.peak_frequency_hz / BAND_WIDTH_HZ, Integer)
        named = (
            select(
                orm.Detection.taxonomic_group,
                name.label("name"),
                sci.label("sci"),
                orm.Detection.detector_label,
                band.label("band"),
                func.count().label("n"),
                func.max(orm.Detection.score).label("best"),
            )
            .join(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
            .outerjoin(latest, latest.c.detection_id == orm.Detection.id)
            .where(
                orm.Detection.event_start_utc >= h_start,
                orm.Detection.event_start_utc < h_end,
                history.is_live(orm.AudioStream.source_kind),
                history.is_not_withdrawn(),
                or_(latest.c.status.is_(None), latest.c.status != REJECTED),
            )
            .group_by(
                orm.Detection.taxonomic_group, name, sci, orm.Detection.detector_label, band
            )
        )
        for nrow in session.execute(named).all():
            group = nrow.taxonomic_group
            if group == BAT_GROUP:
                label = _bat_label(int(nrow.band) if nrow.band is not None else None)
                sci_name = None
            else:
                label = nrow.name or nrow.sci or nrow.detector_label or group
                sci_name = nrow.sci
            taxon_key = (index, group, label[:240])
            entry = taxon_hour.setdefault(taxon_key, [0, 0.0, sci_name])
            entry[0] += int(nrow.n)
            entry[1] = max(entry[1], float(nrow.best or 0.0))
            if entry[2] is None:
                entry[2] = sci_name
    # ---- per-group day facts ---------------------------------------------
    group_rows: list[orm.AnalyticsGroupDay] = []
    totals = (
        select(
            orm.Detection.taxonomic_group,
            func.count().label("n"),
            func.max(orm.Detection.score).label("best"),
            func.min(orm.Detection.event_start_utc).label("first"),
            func.max(orm.Detection.event_start_utc).label("last"),
        )
        .join(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
        .where(
            orm.Detection.event_start_utc >= day_start,
            orm.Detection.event_start_utc < day_end,
            history.is_live(orm.AudioStream.source_kind),
        )
        .group_by(orm.Detection.taxonomic_group)
    )

    def nth_moment(group: str, offset: int) -> datetime | None:
        row = session.execute(
            select(orm.Detection.event_start_utc)
            .join(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
            .where(
                orm.Detection.taxonomic_group == group,
                orm.Detection.event_start_utc >= day_start,
                orm.Detection.event_start_utc < day_end,
                history.is_live(orm.AudioStream.source_kind),
            )
            .order_by(orm.Detection.event_start_utc)
            .offset(offset)
            .limit(1)
        ).scalar_one_or_none()
        return _aware(row) if row else None

    def count_in(group: str, start: datetime | None, end: datetime | None) -> int:
        if start is None or end is None or end <= start:
            return 0
        return int(
            session.execute(
                select(func.count())
                .select_from(orm.Detection)
                .join(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
                .where(
                    orm.Detection.taxonomic_group == group,
                    orm.Detection.event_start_utc >= max(start, day_start),
                    orm.Detection.event_start_utc < min(end, day_end),
                    history.is_live(orm.AudioStream.source_kind),
                )
            ).scalar_one()
        )

    total_detections = 0
    for trow in session.execute(totals).all():
        n = int(trow.n)
        total_detections += n
        group = trow.taxonomic_group
        group_rows.append(
            orm.AnalyticsGroupDay(
                local_date=d.isoformat(),
                taxonomic_group=group,
                detections=n,
                best_score=round(float(trow.best or 0.0), 4),
                first_utc=_aware(trow.first),
                last_utc=_aware(trow.last),
                p05_utc=nth_moment(group, int(0.05 * (n - 1))),
                p95_utc=nth_moment(group, int(0.95 * (n - 1))),
                daylight_detections=count_in(
                    group, solar.sunrise_utc if solar else None, solar.sunset_utc if solar else None
                ),
                dusk_to_midnight_detections=count_in(
                    group, solar.civil_dusk_utc if solar else None, day_end
                ),
                midnight_to_dawn_detections=count_in(
                    group, day_start, solar.civil_dawn_utc if solar else None
                ),
            )
        )

    # ---- what was excluded, and why --------------------------------------
    excluded_synthetic = history.excluded_synthetic_count(session, window)
    excluded_withdrawn = history.excluded_withdrawn_count(session, window)
    excluded_rejected = int(
        session.execute(
            select(func.count())
            .select_from(orm.Detection)
            .join(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id)
            .join(latest, latest.c.detection_id == orm.Detection.id)
            .where(
                orm.Detection.event_start_utc >= day_start,
                orm.Detection.event_start_utc < day_end,
                history.is_live(orm.AudioStream.source_kind),
                latest.c.status == REJECTED,
            )
        ).scalar_one()
    )

    # ---- replace the day's rows ------------------------------------------
    key = d.isoformat()
    for table in (
        orm.AnalyticsDay,
        orm.AnalyticsCoverageHour,
        orm.AnalyticsHour,
        orm.AnalyticsGroupDay,
        orm.AnalyticsTaxonHour,
    ):
        session.execute(delete(table).where(table.local_date == key))

    complete = day_end <= moment
    seconds = time.monotonic() - started
    day_row = orm.AnalyticsDay(
        local_date=key,
        timezone=timezone,
        day_start_utc=day_start,
        day_end_utc=day_end,
        seconds_in_day=int((day_end - day_start).total_seconds()),
        seconds_captured=round(_overlap_seconds(all_intervals, day_start, day_end), 1),
        seconds_from_microphone=round(_overlap_seconds(live_intervals, day_start, day_end), 1),
        seconds_paused=round(_overlap_seconds(pause_intervals, day_start, day_end), 1),
        detections=total_detections,
        excluded_synthetic=int(excluded_synthetic),
        excluded_withdrawn=int(excluded_withdrawn),
        excluded_rejected=excluded_rejected,
        sunrise_utc=solar.sunrise_utc if solar else None,
        sunset_utc=solar.sunset_utc if solar else None,
        civil_dawn_utc=solar.civil_dawn_utc if solar else None,
        civil_dusk_utc=solar.civil_dusk_utc if solar else None,
        daylight_captured_seconds=round(daylight_captured, 1),
        dusk_to_midnight_captured_seconds=round(dusk_captured, 1),
        midnight_to_dawn_captured_seconds=round(dawn_captured, 1),
        complete=complete,
        built_at=moment,
        builder_version=BUILDER_VERSION,
        build_seconds=round(seconds, 3),
    )
    session.add(day_row)
    for index, (h_start, h_end) in enumerate(hours):
        session.add(
            orm.AnalyticsCoverageHour(
                local_date=key,
                hour=index,
                seconds_captured=round(_overlap_seconds(all_intervals, h_start, h_end), 1),
                seconds_from_microphone=round(_overlap_seconds(live_intervals, h_start, h_end), 1),
                seconds_paused=round(_overlap_seconds(pause_intervals, h_start, h_end), 1),
            )
        )
    for (hour, group), (n, best) in group_hour.items():
        session.add(
            orm.AnalyticsHour(
                local_date=key, hour=hour, taxonomic_group=group, detections=n, best_score=round(best, 4)
            )
        )
    session.add_all(group_rows)
    for (hour, group, label), (n, best, sci_name) in taxon_hour.items():
        session.add(
            orm.AnalyticsTaxonHour(
                local_date=key,
                hour=hour,
                taxonomic_group=group,
                label=label,
                scientific_name=sci_name,
                detections=n,
                best_score=round(best, 4),
            )
        )
    session.flush()

    build.detections = total_detections
    build.excluded_synthetic = int(excluded_synthetic)
    build.excluded_withdrawn = int(excluded_withdrawn)
    build.excluded_rejected = excluded_rejected
    build.seconds = round(time.monotonic() - started, 3)
    build.complete = complete
    build.row = {
        "local_date": key,
        "detections": total_detections,
        "seconds_from_microphone": day_row.seconds_from_microphone,
        "complete": complete,
        "build_seconds": build.seconds,
    }
    return build


# ---------------------------------------------------------------------------
# Which days to build


def first_detection_date(session: Session, zone: tzinfo) -> date | None:
    first = session.execute(select(func.min(orm.Detection.event_start_utc))).scalar_one()
    if first is None:
        return None
    return _aware(first).astimezone(zone).date()


def days_to_build(
    session: Session,
    *,
    timezone: str,
    now: datetime | None = None,
    stale_after_days: int = STALE_AFTER_DAYS,
    stale_limit: int = STALE_DAYS_PER_RUN,
    catchup_limit: int = CATCHUP_DAYS_PER_RUN,
) -> list[date]:
    """Today and yesterday, days a new review has touched, days never built
    (newest first, capped), then the stalest built days (capped)."""
    moment = now or datetime.now(UTC)
    zone = _zone(timezone)
    today = moment.astimezone(zone).date()
    wanted: list[date] = [today, today - timedelta(days=1)]

    built = {
        row.local_date: row
        for row in session.execute(
            select(
                orm.AnalyticsDay.local_date,
                orm.AnalyticsDay.built_at,
                orm.AnalyticsDay.builder_version,
                orm.AnalyticsDay.timezone,
            )
        ).all()
    }

    # A review written since a day was built changes what that day names.
    recent_reviews = session.execute(
        select(orm.Detection.event_start_utc, orm.Review.created_at)
        .join(orm.Review, orm.Review.detection_id == orm.Detection.id)
        .where(orm.Review.created_at >= moment - timedelta(days=stale_after_days + 1))
    ).all()
    for event_start, created in recent_reviews:
        day = _aware(event_start).astimezone(zone).date()
        row = built.get(day.isoformat())
        if row is None or _aware(row.built_at) < _aware(created):
            wanted.append(day)

    first = first_detection_date(session, zone)
    if first is not None:
        missing: list[date] = []
        day = today
        while day >= first:
            if day.isoformat() not in built:
                missing.append(day)
            day -= timedelta(days=1)
        wanted.extend(missing[:catchup_limit])

    cutoff = moment - timedelta(days=stale_after_days)
    stale = sorted(
        (
            date.fromisoformat(key)
            for key, row in built.items()
            if _aware(row.built_at) < cutoff
            or row.builder_version != BUILDER_VERSION
            or row.timezone != timezone
        ),
        key=lambda day: _aware(built[day.isoformat()].built_at),
    )
    wanted.extend(stale[:stale_limit])

    ordered: list[date] = []
    seen: set[date] = set()
    for day in wanted:
        if day in seen or day > today:
            continue
        seen.add(day)
        ordered.append(day)
    return ordered


def rebuild(
    *,
    timezone: str,
    latitude: float | None,
    longitude: float | None,
    now: datetime | None = None,
    all_days: bool = False,
    since: date | None = None,
    until: date | None = None,
    limit: int | None = None,
    progress: Callable[[DayBuild], None] | None = None,
) -> dict[str, Any]:
    """Build the days that need it, one short transaction each.

    ``all_days`` rebuilds every day from the first detection to today;
    ``since``/``until`` bound that. Otherwise :func:`days_to_build` decides.
    """
    moment = now or datetime.now(UTC)
    zone = _zone(timezone)
    with session_scope() as session:
        if all_days or since is not None:
            first = since or first_detection_date(session, zone)
            last = until or moment.astimezone(zone).date()
            days: list[date] = []
            if first is not None:
                day = last
                while day >= first:
                    days.append(day)
                    day -= timedelta(days=1)
        else:
            days = days_to_build(session, timezone=timezone, now=moment)
    if limit is not None:
        days = days[:limit]

    started = time.monotonic()
    built: list[dict[str, Any]] = []
    for day in days:
        with session_scope() as session:
            result = build_day(
                session,
                day,
                timezone=timezone,
                latitude=latitude,
                longitude=longitude,
                now=moment,
            )
        built.append(result.row)
        log.info("analytics.day_built", **result.row)
        if progress is not None:
            progress(result)
    return {
        "days": built,
        "count": len(built),
        "seconds": round(time.monotonic() - started, 3),
        "builder_version": BUILDER_VERSION,
    }


# ---------------------------------------------------------------------------
# Reading the roll-up


@dataclass(frozen=True, slots=True)
class DateRange:
    first: date
    last: date
    label: str
    name: str

    def days(self) -> Iterator[date]:
        day = self.first
        while day <= self.last:
            yield day
            day += timedelta(days=1)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "first_date": self.first.isoformat(),
            "last_date": self.last.isoformat(),
            "label": self.label,
            "days": (self.last - self.first).days + 1,
        }


def resolve_dates(
    session: Session, name: str, timezone: str, *, now: datetime | None = None
) -> DateRange:
    """A window name as a closed range of local dates, never past today.

    ``all`` is the whole roll-up; everything else is the history grammar
    (``last-30d``, ``this-year``, ``2026-08``, ...), with an unknown name
    falling back to the last 30 days rather than a 500.
    """
    moment = now or datetime.now(UTC)
    zone = _zone(timezone)
    today = moment.astimezone(zone).date()
    # Nothing before the first detection is a hole in the roll-up; it is
    # before the record. "The last 90 days" on a station six weeks old is six
    # weeks, so the days-built figure means what its label says.
    record_start = first_detection_date(session, zone) or today
    if name == "all":
        return DateRange(record_start, today, "everything recorded", "all")
    resolved = history.resolve_range(name, timezone, now=moment)
    if resolved is None:
        resolved = history.resolve_range("last-30d", timezone, now=moment)
        assert resolved is not None
        name = "last-30d"
    first = max(resolved.start.astimezone(zone).date(), record_start)
    last_moment = resolved.end - timedelta(microseconds=1)
    last = min(last_moment.astimezone(zone).date(), today)
    if last < first:
        last = first
    return DateRange(first, last, resolved.label, name)


def _iso_week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def bucket_key(d: date, grain: str) -> tuple[str, date, date]:
    """``(key, start_date, end_date)`` of the bucket local day ``d`` falls in."""
    if grain == "week":
        start = _iso_week_start(d)
        iso = start.isocalendar()
        return f"{iso.year}-W{iso.week:02d}", start, start + timedelta(days=6)
    if grain == "month":
        start = d.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return start.strftime("%Y-%m"), start, nxt - timedelta(days=1)
    return d.isoformat(), d, d


def _day_rows(session: Session, dates: DateRange) -> dict[str, orm.AnalyticsDay]:
    rows = session.execute(
        select(orm.AnalyticsDay).where(
            orm.AnalyticsDay.local_date >= dates.first.isoformat(),
            orm.AnalyticsDay.local_date <= dates.last.isoformat(),
        )
    ).scalars()
    return {row.local_date: row for row in rows}


def _exclusions(day_rows: Iterable[orm.AnalyticsDay]) -> dict[str, int]:
    rows = list(day_rows)
    return {
        "excluded_synthetic_count": sum(r.excluded_synthetic for r in rows),
        "excluded_withdrawn_count": sum(r.excluded_withdrawn for r in rows),
        "excluded_rejected_count": sum(r.excluded_rejected for r in rows),
        "days_built": len(rows),
    }


NOTE_COUNTS = (
    "Counts are of detections, not of animals: one bird calling repeatedly "
    "produces many detections, and a bat pass is one detection. Compare a "
    "series with itself over time, not one species against another."
)


def series(
    session: Session,
    dates: DateRange,
    *,
    grain: str = "day",
    group: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Detections per bucket, with the captured seconds each bucket had.

    With ``label`` the counts come from the named table (withdrawn and
    rejected rows excluded); without it, from the group table (they are
    included, and named nothing). A ``night`` bucket is noon to noon, keyed
    by the evening's date, and its coverage is composed from the hour table.
    """
    grain = grain if grain in GRAINS else "day"
    day_rows = _day_rows(session, dates)
    first_key, last_key = dates.first.isoformat(), dates.last.isoformat()

    # Detections per (date, hour, group), needed for night composition and cheap
    # enough for any range the UI offers (24 rows per day per group).
    if label:
        stmt = select(
            orm.AnalyticsTaxonHour.local_date,
            orm.AnalyticsTaxonHour.hour,
            orm.AnalyticsTaxonHour.taxonomic_group,
            orm.AnalyticsTaxonHour.detections,
        ).where(
            orm.AnalyticsTaxonHour.label == label,
            orm.AnalyticsTaxonHour.local_date >= first_key,
            orm.AnalyticsTaxonHour.local_date <= last_key,
        )
        if group:
            stmt = stmt.where(orm.AnalyticsTaxonHour.taxonomic_group == group)
    else:
        stmt = select(
            orm.AnalyticsHour.local_date,
            orm.AnalyticsHour.hour,
            orm.AnalyticsHour.taxonomic_group,
            orm.AnalyticsHour.detections,
        ).where(
            orm.AnalyticsHour.local_date >= first_key,
            orm.AnalyticsHour.local_date <= last_key,
        )
        if group:
            stmt = stmt.where(orm.AnalyticsHour.taxonomic_group == group)
    hour_rows = session.execute(stmt).all()

    coverage_rows = session.execute(
        select(
            orm.AnalyticsCoverageHour.local_date,
            orm.AnalyticsCoverageHour.hour,
            orm.AnalyticsCoverageHour.seconds_from_microphone,
            orm.AnalyticsCoverageHour.seconds_paused,
        ).where(
            orm.AnalyticsCoverageHour.local_date >= first_key,
            orm.AnalyticsCoverageHour.local_date <= last_key,
        )
    ).all()

    def bucket_of(local_date: str, hour: int) -> tuple[str, date, date] | None:
        d = date.fromisoformat(local_date)
        if grain == "night":
            evening = d if hour >= 12 else d - timedelta(days=1)
            if evening < dates.first or evening > dates.last:
                return None
            return evening.isoformat(), evening, evening + timedelta(days=1)
        return bucket_key(d, grain)

    buckets: dict[str, dict[str, Any]] = {}

    def bucket(local_date: str, hour: int) -> dict[str, Any] | None:
        found = bucket_of(local_date, hour)
        if found is None:
            return None
        key, start, end = found
        return buckets.setdefault(
            key,
            {
                "key": key,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "detections": 0,
                "groups": {},
                "seconds_from_microphone": 0.0,
                "seconds_paused": 0.0,
                "days": set(),
                "partial": False,
            },
        )

    for local_date, hour, group_name, n in hour_rows:
        entry = bucket(local_date, hour)
        if entry is None:
            continue
        entry["detections"] += n
        entry["groups"][group_name] = entry["groups"].get(group_name, 0) + n
    for local_date, hour, live_seconds, paused in coverage_rows:
        entry = bucket(local_date, hour)
        if entry is None:
            continue
        entry["seconds_from_microphone"] += live_seconds
        entry["seconds_paused"] += paused
        entry["days"].add(local_date)
        row = day_rows.get(local_date)
        if row is not None and not row.complete:
            entry["partial"] = True

    # Every day in the range that has no roll-up row is an honest hole, not a
    # quiet day: represent it as a bucket with `built: false` so the chart can
    # hatch it rather than draw zero.
    for d in dates.days():
        if d.isoformat() in day_rows:
            continue
        found = bucket_of(d.isoformat(), 12)
        if found is None:
            continue
        key, start, end = found
        entry = buckets.setdefault(
            key,
            {
                "key": key,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "detections": 0,
                "groups": {},
                "seconds_from_microphone": 0.0,
                "seconds_paused": 0.0,
                "days": set(),
                "partial": False,
            },
        )
        entry["unbuilt_days"] = entry.get("unbuilt_days", 0) + 1

    ordered = []
    for key in sorted(buckets):
        entry = buckets[key]
        hours = entry["seconds_from_microphone"] / 3600.0
        ordered.append(
            {
                "key": entry["key"],
                "start_date": entry["start_date"],
                "end_date": entry["end_date"],
                "detections": entry["detections"],
                "groups": entry["groups"],
                "seconds_from_microphone": round(entry["seconds_from_microphone"], 1),
                "seconds_paused": round(entry["seconds_paused"], 1),
                "per_captured_hour": round(entry["detections"] / hours, 3) if hours > 0 else None,
                "days_built": len(entry["days"]),
                "days_unbuilt": entry.get("unbuilt_days", 0),
                "partial": entry["partial"],
            }
        )
    return {
        "range": dates.to_dict(),
        "grain": grain,
        "group": group,
        "label": label,
        "buckets": ordered,
        "note": NOTE_COUNTS,
        **_exclusions(day_rows.values()),
    }


def hours(
    session: Session,
    dates: DateRange,
    *,
    columns: str = "day",
    group: str | None = None,
    label: str | None = None,
    timezone: str = "UTC",
) -> dict[str, Any]:
    """A time-of-day matrix: one column per day or week, 24 hour cells each,
    with the captured seconds behind every cell and the day's solar moments
    as local hours so a chart can draw sunrise and sunset over it."""
    columns = "week" if columns == "week" else "day"
    zone = _zone(timezone)
    day_rows = _day_rows(session, dates)
    first_key, last_key = dates.first.isoformat(), dates.last.isoformat()
    if label:
        stmt = select(
            orm.AnalyticsTaxonHour.local_date,
            orm.AnalyticsTaxonHour.hour,
            orm.AnalyticsTaxonHour.detections,
        ).where(
            orm.AnalyticsTaxonHour.label == label,
            orm.AnalyticsTaxonHour.local_date >= first_key,
            orm.AnalyticsTaxonHour.local_date <= last_key,
        )
        if group:
            stmt = stmt.where(orm.AnalyticsTaxonHour.taxonomic_group == group)
    else:
        stmt = select(
            orm.AnalyticsHour.local_date, orm.AnalyticsHour.hour, orm.AnalyticsHour.detections
        ).where(
            orm.AnalyticsHour.local_date >= first_key, orm.AnalyticsHour.local_date <= last_key
        )
        if group:
            stmt = stmt.where(orm.AnalyticsHour.taxonomic_group == group)
    hour_rows = session.execute(stmt).all()
    coverage_rows = session.execute(
        select(
            orm.AnalyticsCoverageHour.local_date,
            orm.AnalyticsCoverageHour.hour,
            orm.AnalyticsCoverageHour.seconds_from_microphone,
        ).where(
            orm.AnalyticsCoverageHour.local_date >= first_key,
            orm.AnalyticsCoverageHour.local_date <= last_key,
        )
    ).all()

    cols: dict[str, dict[str, Any]] = {}

    def column(local_date: str) -> dict[str, Any]:
        d = date.fromisoformat(local_date)
        key, start, end = bucket_key(d, columns)
        return cols.setdefault(
            key,
            {
                "key": key,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "hours": [0] * 24,
                "captured": [0.0] * 24,
                "days_built": set(),
                "partial": False,
                "solar": None,
            },
        )

    for local_date, hour, n in hour_rows:
        column(local_date)["hours"][hour] += n
    for local_date, hour, live_seconds in coverage_rows:
        entry = column(local_date)
        entry["captured"][hour] += live_seconds
        entry["days_built"].add(local_date)
        row = day_rows.get(local_date)
        if row is not None and not row.complete:
            entry["partial"] = True

    def solar_of(row: orm.AnalyticsDay) -> dict[str, float | None] | None:
        d = date.fromisoformat(row.local_date)
        if row.sunrise_utc is None and row.civil_dawn_utc is None:
            return None
        return {
            "sunrise": local_hour(row.sunrise_utc, d, zone),
            "sunset": local_hour(row.sunset_utc, d, zone),
            "civil_dawn": local_hour(row.civil_dawn_utc, d, zone),
            "civil_dusk": local_hour(row.civil_dusk_utc, d, zone),
        }

    ordered = []
    profile = [0] * 24
    captured_total = [0.0] * 24
    for key in sorted(cols):
        entry = cols[key]
        days_built = sorted(entry["days_built"])
        # The solar moments of the column's middle built day.
        middle = day_rows.get(days_built[len(days_built) // 2]) if days_built else None
        entry["solar"] = solar_of(middle) if middle is not None else None
        for h in range(24):
            profile[h] += entry["hours"][h]
            captured_total[h] += entry["captured"][h]
        ordered.append(
            {
                "key": entry["key"],
                "start_date": entry["start_date"],
                "end_date": entry["end_date"],
                "hours": entry["hours"],
                "captured": [round(v, 1) for v in entry["captured"]],
                "days_built": len(days_built),
                "partial": entry["partial"],
                "solar": entry["solar"],
            }
        )
    return {
        "range": dates.to_dict(),
        "columns_grain": columns,
        "group": group,
        "label": label,
        "timezone": timezone,
        "columns": ordered,
        "profile": profile,
        "profile_captured": [round(v, 1) for v in captured_total],
        "note": NOTE_COUNTS,
        **_exclusions(day_rows.values()),
    }


def taxa(
    session: Session,
    dates: DateRange,
    *,
    group: str | None = "bird",
    grain: str = "week",
    limit: int = 60,
) -> dict[str, Any]:
    """Every label's detections per bucket: the phenology grid.

    Rows are ordered by total, and each carries the first and last date it
    was heard, its total and its cells, so a cell can show a count as well as
    a colour -- a first-heard date resting on one detection must look
    different from one resting on forty (ADR-056)."""
    grain = grain if grain in ("day", "week", "month") else "week"
    day_rows = _day_rows(session, dates)
    first_key, last_key = dates.first.isoformat(), dates.last.isoformat()
    stmt = (
        select(
            orm.AnalyticsTaxonHour.taxonomic_group,
            orm.AnalyticsTaxonHour.label,
            orm.AnalyticsTaxonHour.scientific_name,
            orm.AnalyticsTaxonHour.local_date,
            func.sum(orm.AnalyticsTaxonHour.detections).label("n"),
            func.max(orm.AnalyticsTaxonHour.best_score).label("best"),
        )
        .where(
            orm.AnalyticsTaxonHour.local_date >= first_key,
            orm.AnalyticsTaxonHour.local_date <= last_key,
        )
        .group_by(
            orm.AnalyticsTaxonHour.taxonomic_group,
            orm.AnalyticsTaxonHour.label,
            orm.AnalyticsTaxonHour.scientific_name,
            orm.AnalyticsTaxonHour.local_date,
        )
    )
    if group:
        stmt = stmt.where(orm.AnalyticsTaxonHour.taxonomic_group == group)
    rows = session.execute(stmt).all()

    column_keys: dict[str, tuple[date, date]] = {}
    for d in dates.days():
        key, start, end = bucket_key(d, grain)
        column_keys.setdefault(key, (start, end))
    ordered_columns = sorted(column_keys)
    index_of = {key: i for i, key in enumerate(ordered_columns)}

    taxa_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for group_name, label, sci, local_date, n, best in rows:
        entry = taxa_rows.setdefault(
            (group_name, label),
            {
                "taxonomic_group": group_name,
                "label": label,
                "scientific_name": sci,
                "detections": 0,
                "best_score": 0.0,
                "first_date": local_date,
                "last_date": local_date,
                "days_heard": 0,
                "cells": [0] * len(ordered_columns),
            },
        )
        entry["detections"] += int(n)
        entry["best_score"] = max(entry["best_score"], float(best or 0.0))
        entry["first_date"] = min(entry["first_date"], local_date)
        entry["last_date"] = max(entry["last_date"], local_date)
        entry["days_heard"] += 1
        key, _, _ = bucket_key(date.fromisoformat(local_date), grain)
        entry["cells"][index_of[key]] += int(n)

    ranked = sorted(taxa_rows.values(), key=lambda r: (-r["detections"], r["label"]))
    columns = [
        {
            "key": key,
            "start_date": column_keys[key][0].isoformat(),
            "end_date": column_keys[key][1].isoformat(),
            "days_built": sum(
                1
                for d in day_rows
                if column_keys[key][0] <= date.fromisoformat(d) <= column_keys[key][1]
            ),
            "seconds_from_microphone": round(
                sum(
                    r.seconds_from_microphone
                    for k, r in day_rows.items()
                    if column_keys[key][0] <= date.fromisoformat(k) <= column_keys[key][1]
                ),
                1,
            ),
        }
        for key in ordered_columns
    ]
    return {
        "range": dates.to_dict(),
        "grain": grain,
        "group": group,
        "columns": columns,
        "rows": ranked[:limit],
        "total_rows": len(ranked),
        "note": NOTE_COUNTS,
        **_exclusions(day_rows.values()),
    }


def span(
    session: Session,
    dates: DateRange,
    *,
    group: str = "bird",
    timezone: str = "UTC",
    latitude: float | None = None,
    longitude: float | None = None,
) -> dict[str, Any]:
    """Per day: when the group's activity started and stopped (5th and 95th
    percentile moments, plus first and last), the day's sunrise, sunset and
    twilight, daylight and night lengths, and detections per captured hour
    inside daylight and inside the night that starts that evening."""
    zone = _zone(timezone)
    day_rows = _day_rows(session, dates)
    group_rows = {
        row.local_date: row
        for row in session.execute(
            select(orm.AnalyticsGroupDay).where(
                orm.AnalyticsGroupDay.taxonomic_group == group,
                orm.AnalyticsGroupDay.local_date >= dates.first.isoformat(),
                orm.AnalyticsGroupDay.local_date <= dates.last.isoformat(),
            )
        ).scalars()
    }
    # The night that starts on the last day ends on the day after it, which
    # may be outside the range: fetch one more day for the morning half.
    after_key = (dates.last + timedelta(days=1)).isoformat()
    after = session.execute(
        select(orm.AnalyticsDay).where(orm.AnalyticsDay.local_date == after_key)
    ).scalar_one_or_none()
    if after is not None:
        day_rows[after_key] = after
    after_group = session.execute(
        select(orm.AnalyticsGroupDay).where(
            orm.AnalyticsGroupDay.taxonomic_group == group,
            orm.AnalyticsGroupDay.local_date == after_key,
        )
    ).scalar_one_or_none()
    if after_group is not None:
        group_rows[after_key] = after_group

    days_out = []
    for d in dates.days():
        key = d.isoformat()
        row = day_rows.get(key)
        if row is None:
            days_out.append({"date": key, "built": False})
            continue
        g = group_rows.get(key)
        next_row = day_rows.get((d + timedelta(days=1)).isoformat())
        next_group = group_rows.get((d + timedelta(days=1)).isoformat())

        sunrise = local_hour(row.sunrise_utc, d, zone)
        sunset = local_hour(row.sunset_utc, d, zone)
        dawn = local_hour(row.civil_dawn_utc, d, zone)
        dusk = local_hour(row.civil_dusk_utc, d, zone)
        daylight_h = (
            round((_aware(row.sunset_utc) - _aware(row.sunrise_utc)).total_seconds() / 3600, 3)
            if row.sunrise_utc and row.sunset_utc
            else None
        )
        # The night that starts this evening: this day's dusk to tomorrow's dawn.
        next_dawn = next_row.civil_dawn_utc if next_row is not None else None
        if next_dawn is None and latitude is not None and longitude is not None:
            next_dawn = solar_day(d + timedelta(days=1), latitude, longitude).civil_dawn_utc
        night_h = (
            round((_aware(next_dawn) - _aware(row.civil_dusk_utc)).total_seconds() / 3600, 3)
            if row.civil_dusk_utc and next_dawn
            else None
        )
        night_detections = (g.dusk_to_midnight_detections if g else 0) + (
            next_group.midnight_to_dawn_detections if next_group else 0
        )
        night_captured = row.dusk_to_midnight_captured_seconds + (
            next_row.midnight_to_dawn_captured_seconds if next_row is not None else 0.0
        )
        night_known = next_row is not None
        captured_h = row.seconds_from_microphone / 3600.0
        daylight_captured_h = row.daylight_captured_seconds / 3600.0
        night_captured_h = night_captured / 3600.0
        days_out.append(
            {
                "date": key,
                "built": True,
                "complete": row.complete,
                "detections": g.detections if g else 0,
                "seconds_from_microphone": row.seconds_from_microphone,
                "per_captured_hour": round(g.detections / captured_h, 3) if g and captured_h > 0 else None,
                "first": local_hour(g.first_utc, d, zone) if g else None,
                "last": local_hour(g.last_utc, d, zone) if g else None,
                "p05": local_hour(g.p05_utc, d, zone) if g else None,
                "p95": local_hour(g.p95_utc, d, zone) if g else None,
                "sunrise": sunrise,
                "sunset": sunset,
                "civil_dawn": dawn,
                "civil_dusk": dusk,
                "daylight_hours": daylight_h,
                "daylight_detections": g.daylight_detections if g else 0,
                "daylight_per_captured_hour": (
                    round(g.daylight_detections / daylight_captured_h, 3)
                    if g and daylight_captured_h > 0
                    else None
                ),
                "night_hours": night_h,
                "night_known": night_known,
                "night_detections": night_detections if night_known else None,
                "night_per_captured_hour": (
                    round(night_detections / night_captured_h, 3)
                    if night_known and night_captured_h > 0
                    else None
                ),
            }
        )
    return {
        "range": dates.to_dict(),
        "group": group,
        "timezone": timezone,
        "coordinates_set": latitude is not None and longitude is not None,
        "days": days_out,
        "note": (
            "Active span is the local time between the 5th and 95th percentile "
            "detection of the day, so one stray call at 02:00 does not stretch it. "
            + NOTE_COUNTS
        ),
        **_exclusions(r for k, r in day_rows.items() if k != after_key),
    }


def status(session: Session, *, timezone: str, now: datetime | None = None) -> dict[str, Any]:
    """How much roll-up exists and how fresh it is."""
    moment = now or datetime.now(UTC)
    zone = _zone(timezone)
    today = moment.astimezone(zone).date()
    facts = session.execute(
        select(
            func.count(orm.AnalyticsDay.local_date),
            func.min(orm.AnalyticsDay.local_date),
            func.max(orm.AnalyticsDay.local_date),
            func.max(orm.AnalyticsDay.built_at),
            func.sum(orm.AnalyticsDay.detections),
        )
    ).one()
    days_built, first_key, last_key, last_built, detections = facts
    first = first_detection_date(session, zone)
    missing = 0
    if first is not None:
        built_keys = {
            r[0]
            for r in session.execute(select(orm.AnalyticsDay.local_date)).all()
        }
        day = first
        while day <= today:
            if day.isoformat() not in built_keys:
                missing += 1
            day += timedelta(days=1)
    age = (moment - _aware(last_built)).total_seconds() if last_built else None
    return {
        "timezone": timezone,
        "builder_version": BUILDER_VERSION,
        "days_built": int(days_built or 0),
        "first_date": first_key,
        "last_date": last_key,
        "first_detection_date": first.isoformat() if first else None,
        "days_missing": missing,
        "last_built_at": _iso(last_built) if last_built else None,
        "age_seconds": round(age, 1) if age is not None else None,
        "detections_rolled_up": int(detections or 0),
        "today": today.isoformat(),
    }


# ---------------------------------------------------------------------------
# The questions that ship as demonstrators


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    title: str
    question: str
    #: What a working instrument should show. If it does not, suspect the
    #: instrument before the garden.
    expect: str
    view: str
    params: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "question": self.question,
            "expect": self.expect,
            "view": self.view,
            "params": dict(self.params),
        }


QUESTIONS: tuple[Question, ...] = (
    Question(
        id="bird-day-length",
        title="Do the birds' hours shrink with the daylight?",
        question=(
            "As the days shorten into winter, does bird call activity compress "
            "to match, or do they keep the same hours regardless?"
        ),
        expect=(
            "The active span (first to last of the day's central 90%) should track "
            "the sunrise and sunset lines through the year, starting around civil "
            "dawn. A span that ignores the light, or starts before dawn every day, "
            "is a detector hearing something other than birds."
        ),
        view="span",
        params={"range": "all", "group": "bird"},
    ),
    Question(
        id="bat-night-length",
        title="Do bats thin out as the nights lengthen?",
        question=(
            "Bat passes per hour of night, against how long that night was. Does a "
            "longer autumn night spread the same activity thinner, or does it fall away?"
        ),
        expect=(
            "Passes should sit inside the civil-dusk-to-dawn window, densest in the "
            "first hours after dusk. Passes at midday are wind, machinery or a "
            "handling noise, not bats."
        ),
        view="span",
        params={"range": "all", "group": "bat"},
    ),
    Question(
        id="robin-year",
        title="When are there most robins?",
        question="Which weeks of the year hold the most European Robin detections?",
        expect=(
            "Robins sing almost year-round in a British garden, with a quieter "
            "spell during the late-summer moult and a resurgence of autumn song. "
            "A week of zero against full coverage is worth a look."
        ),
        view="series",
        params={"range": "this-year", "grain": "week", "group": "bird", "label": "European Robin"},
    ),
    Question(
        id="who-when",
        title="Which birds are here at which times of year?",
        question="Every species by week: who arrived when, who left, who stayed.",
        expect=(
            "Resident species run the whole way across; summer visitors stop in "
            "the autumn; winter visitors begin then. A species heard in one "
            "week only, on one detection, is a cell to click rather than a fact."
        ),
        view="taxa",
        params={"range": "all", "grain": "week", "group": "bird"},
    ),
    Question(
        id="green-woodpecker-hour",
        title="What time of day for a green woodpecker?",
        question="At which hour is a European Green Woodpecker most likely to be heard?",
        expect=(
            "A daytime bird: the profile should be empty overnight and peak in the "
            "morning. Night-time hits for a species that does not call at night "
            "are misidentifications to review."
        ),
        view="hours",
        params={"range": "all", "columns": "week", "group": "bird", "label": "European Green Woodpecker"},
    ),
    Question(
        id="bat-hours",
        title="When do the bats fly?",
        question="Bat passes by hour of the day, one column per night of the last three months.",
        expect=(
            "Activity should lie between the dusk and dawn curves and move with "
            "them as the nights lengthen -- the band of colour should bend."
        ),
        view="hours",
        params={"range": "last-90d", "columns": "day", "group": "bat"},
    ),
    Question(
        id="dawn-chorus",
        title="When does the day start?",
        question="Bird detections by hour, one column per week: how the dawn chorus moves through the year.",
        expect=(
            "The morning edge of the colour should follow the civil-dawn line, "
            "earlier in June, later in December. The evening edge should follow "
            "dusk less tightly -- birds stop before it gets dark."
        ),
        view="hours",
        params={"range": "all", "columns": "week", "group": "bird"},
    ),
    Question(
        id="was-it-listening",
        title="Was the station listening?",
        question="Captured hours per day against detections per day: was a quiet day quiet, or deaf?",
        expect=(
            "Coverage near 24 hours every day. A day with low coverage and low "
            "detections is not a quiet day and must not be read as one; a day "
            "with full coverage and nothing heard is."
        ),
        view="series",
        params={"range": "last-90d", "grain": "day"},
    ),
)


def questions() -> list[dict[str, Any]]:
    return [q.to_dict() for q in QUESTIONS]


# ---------------------------------------------------------------------------
# Saved reports


VIEWS = ("series", "hours", "taxa", "span")


def list_reports(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(orm.AnalyticsReport).order_by(orm.AnalyticsReport.created_at)
    ).scalars()
    return [_report_dict(row) for row in rows]


def save_report(
    session: Session, *, name: str, question: str, view: str, params: dict[str, Any], actor: str
) -> dict[str, Any]:
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}; one of {', '.join(VIEWS)}")
    row = orm.AnalyticsReport(
        name=name.strip()[:120], question=question.strip(), view=view, params=params, created_by=actor
    )
    session.add(row)
    session.flush()
    return _report_dict(row)


def delete_report(session: Session, report_id: Any) -> bool:
    row = session.get(orm.AnalyticsReport, report_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def _report_dict(row: orm.AnalyticsReport) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "question": row.question,
        "view": row.view,
        "params": dict(row.params or {}),
        "created_at": _iso(row.created_at),
        "created_by": row.created_by,
    }
