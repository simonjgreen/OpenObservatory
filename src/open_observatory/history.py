"""Aggregated history queries, for browsing what happened rather than what is.

The debug UI's live channel only ever knows about the session it is connected for,
so a page opened at breakfast has no idea a nightjar called at 03:40. Detections are
persisted, so the history is there; this module is what makes it queryable at a scale
the browser can hold.

The important constraint is that aggregation happens in SQL. The activity detector
alone produces roughly 172,000 rows a day, so answering "what happened last night"
by fetching rows and counting them in Python would ship tens of megabytes to count
to a few hundred. Every function here returns something already reduced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, Select, cast, func, select
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from .db import models as orm
from .display import display_title

#: Groups that name an organism, as opposed to describing an unattributed sound.
IDENTIFIED_GROUPS = ("bird", "bat")


@dataclass(frozen=True, slots=True)
class Range:
    """A closed-open time window, always in UTC internally."""

    start: datetime
    end: datetime
    label: str

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def to_dict(self) -> dict[str, object]:
        return {
            "start_utc": _iso(self.start),
            "end_utc": _iso(self.end),
            "label": self.label,
            "seconds": round(self.seconds, 3),
        }


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def resolve_named_range(
    name: str, timezone: str, *, now: datetime | None = None
) -> Range:
    """Turn a name like ``last-night`` into an explicit UTC window.

    Resolved against the station's configured timezone rather than UTC, because
    "last night" is a local idea: a window computed in UTC would drift an hour with
    British Summer Time and quietly clip the interesting end of the night.
    """
    try:
        zone = ZoneInfo(timezone)
    except Exception:
        zone = UTC
    moment = (now or datetime.now(UTC)).astimezone(zone)

    def local(day: datetime, hour: int) -> datetime:
        return day.replace(hour=hour, minute=0, second=0, microsecond=0)

    def window(start: datetime, end: datetime, label: str) -> Range:
        # Boundaries are chosen in local time, because "last night" is a local idea,
        # but stored in UTC to honour the project's rule that UTC is the internal
        # representation and local time is only for presentation.
        return Range(start.astimezone(UTC), end.astimezone(UTC), label)

    if name == "last-hour":
        return window(moment - timedelta(hours=1), moment, "last hour")
    if name == "last-24h":
        return window(moment - timedelta(hours=24), moment, "last 24 hours")
    if name == "today":
        return window(local(moment, 0), moment, "today")
    if name == "yesterday":
        start = local(moment - timedelta(days=1), 0)
        return window(start, start + timedelta(days=1), "yesterday")
    if name == "last-night":
        # Dusk-to-dawn, wide enough to hold a British summer night at either end.
        # Before 12:00 local, "last night" means the one that just ended.
        anchor = moment if moment.hour >= 12 else moment - timedelta(days=1)
        start = local(anchor, 20)
        return window(start, start + timedelta(hours=12), "last night")
    if name == "dawn-chorus":
        anchor = moment if moment.hour >= 10 else moment
        start = local(anchor, 3)
        return window(start, start + timedelta(hours=7), "dawn chorus")
    return window(moment - timedelta(hours=1), moment, "last hour")


def bucket_expression(dialect: Dialect, column: ColumnElement, seconds: int) -> ColumnElement:
    """Truncate a timestamp column to a bucket, in whichever SQL dialect is in use.

    ADR-007 keeps SQLite for the developer profile and PostgreSQL for production, so
    a dialect branch here is honest rather than a leak: there is no portable epoch
    expression, and pushing the arithmetic into Python would defeat the point of
    aggregating in SQL at all.
    """
    if dialect.name == "sqlite":
        epoch = cast(func.strftime("%s", column), Integer)
    else:
        epoch = cast(func.extract("epoch", column), Integer)
    # Truncate with modulo rather than integer division. SQLAlchemy 2 renders `/`
    # as *true* division — it casts to NUMERIC to guarantee float semantics — so
    # `(epoch / seconds) * seconds` truncates nothing and yields one bucket per
    # distinct second: a twelve hour window came back with 1899 ten-minute buckets.
    # Modulo needs no FLOOR and behaves identically on SQLite and PostgreSQL.
    return epoch - (epoch % seconds)


def choose_bucket_seconds(window_seconds: float, target_buckets: int = 120) -> int:
    """Pick a bucket size giving roughly ``target_buckets`` columns.

    Rounded to a familiar interval so axis labels land on recognisable times rather
    than on arbitrary multiples of the window length.
    """
    friendly = (10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 43200, 86400)
    ideal = max(1.0, window_seconds / max(1, target_buckets))
    for candidate in friendly:
        if candidate >= ideal:
            return candidate
    return friendly[-1]


def _in_range(query: Select, window: Range) -> Select:
    return query.where(
        orm.Detection.event_start_utc >= window.start,
        orm.Detection.event_start_utc < window.end,
    )


def timeline(
    session: Session,
    window: Range,
    *,
    bucket_seconds: int | None = None,
    min_score: float = 0.0,
    include_unidentified: bool = True,
) -> dict[str, object]:
    """Detection counts per time bucket, split by taxonomic group.

    This is the shape of a night: when activity started, when it peaked, whether
    anything was happening at 03:00.
    """
    seconds = bucket_seconds or choose_bucket_seconds(window.seconds)
    bucket = bucket_expression(session.get_bind().dialect, orm.Detection.event_start_utc, seconds)

    query = _in_range(
        select(
            bucket.label("bucket"),
            orm.Detection.taxonomic_group.label("group"),
            func.count().label("detections"),
            func.max(orm.Detection.score).label("best_score"),
        ),
        window,
    )
    if min_score > 0:
        query = query.where(orm.Detection.score >= min_score)
    if not include_unidentified:
        query = query.where(orm.Detection.taxonomic_group.in_(IDENTIFIED_GROUPS))
    rows = session.execute(query.group_by("bucket", "group").order_by("bucket")).all()

    buckets: dict[int, dict[str, object]] = {}
    for row in rows:
        entry = buckets.setdefault(
            int(row.bucket),
            {"start_utc": _iso(datetime.fromtimestamp(int(row.bucket), tz=UTC)), "groups": {}},
        )
        groups = entry["groups"]
        assert isinstance(groups, dict)
        groups[row.group] = {"detections": row.detections, "best_score": round(row.best_score, 4)}

    return {
        "range": window.to_dict(),
        "bucket_seconds": seconds,
        "buckets": [buckets[key] for key in sorted(buckets)],
        "note": (
            "Counts of detections, not of animals. One bird calling repeatedly "
            "produces many detections."
        ),
    }


def species_summary(
    session: Session,
    window: Range,
    *,
    limit: int = 40,
    min_score: float = 0.0,
    include_unidentified: bool = False,
) -> list[dict[str, object]]:
    """What was detected in the window, once per distinct label, with its extent."""
    query = _in_range(
        select(
            orm.Detection.taxonomic_group.label("group"),
            orm.Detection.common_name,
            orm.Detection.scientific_name,
            orm.Detection.detector_label,
            orm.Detector.plugin_id,
            func.count().label("detections"),
            func.max(orm.Detection.score).label("best_score"),
            func.min(orm.Detection.event_start_utc).label("first_seen"),
            func.max(orm.Detection.event_start_utc).label("last_seen"),
        ).join(orm.Detector, orm.Detection.detector_id == orm.Detector.id),
        window,
    )
    if min_score > 0:
        query = query.where(orm.Detection.score >= min_score)
    if not include_unidentified:
        query = query.where(orm.Detection.taxonomic_group.in_(IDENTIFIED_GROUPS))

    rows = session.execute(
        query.group_by(
            orm.Detection.taxonomic_group,
            orm.Detection.common_name,
            orm.Detection.scientific_name,
            orm.Detection.detector_label,
            orm.Detector.plugin_id,
        )
        .order_by(func.count().desc())
        .limit(limit)
    ).all()

    results = []
    for row in rows:
        # Aggregated across many detections (once per distinct label), so there is
        # no single peak_frequency_hz/native_result to derive a frequency hint or
        # buzz flag from; display_title still gives the uniform name-fallback chain.
        display_name, title_hint = display_title(
            common_name=row.common_name,
            scientific_name=row.scientific_name,
            label=row.detector_label,
            plugin_id=row.plugin_id,
            taxonomic_group=row.group,
            peak_frequency_hz=None,
            native_result=None,
        )
        results.append(
            {
                "taxonomic_group": row.group,
                "common_name": row.common_name,
                "scientific_name": row.scientific_name,
                "label": row.detector_label,
                "display_name": display_name,
                "title_hint": title_hint,
                "plugin_id": row.plugin_id,
                "detections": row.detections,
                "best_score": round(row.best_score, 4),
                "first_seen_utc": _iso(row.first_seen),
                "last_seen_utc": _iso(row.last_seen),
            }
        )
    return results


def coverage(session: Session, window: Range) -> dict[str, object]:
    """How much of the window the station was actually capturing for.

    Without this, an empty night is ambiguous: nothing called, or nothing was
    listening. The answer changes what the absence means, so it is reported
    alongside the detections rather than left for someone to wonder about.
    """
    streams = session.execute(
        select(
            orm.AudioStream.id,
            orm.AudioStream.source_kind,
            orm.AudioStream.start_utc,
            orm.AudioStream.end_utc,
            orm.AudioStream.sample_rate,
            orm.AudioStream.discontinuity_count,
        ).where(
            orm.AudioStream.start_utc < window.end,
            (orm.AudioStream.end_utc.is_(None)) | (orm.AudioStream.end_utc > window.start),
        )
    ).all()

    now = datetime.now(UTC)
    spans: list[dict[str, object]] = []
    intervals: list[tuple[datetime, datetime, str]] = []
    for row in streams:
        start = max(_aware(row.start_utc), window.start)
        end = min(_aware(row.end_utc) if row.end_utc else now, window.end)
        seconds = max(0.0, (end - start).total_seconds())
        if seconds > 0:
            intervals.append((start, end, row.source_kind))
        spans.append(
            {
                "stream_id": str(row.id),
                "source_kind": row.source_kind,
                "start_utc": _iso(start),
                "end_utc": _iso(end),
                "seconds": round(seconds, 1),
                "sample_rate": row.sample_rate,
                "discontinuity_count": row.discontinuity_count,
            }
        )

    # Merge before summing. Restarts and any stream left unclosed by a killed
    # process produce overlapping rows, and adding those up reported 13x coverage
    # of a twelve hour night — a number that cannot be true and so is worse than
    # no number at all.
    covered = _merged_seconds([(a, b) for a, b, _ in intervals])
    live = _merged_seconds([(a, b) for a, b, kind in intervals if kind == "alsa"])

    gap_rows = session.execute(
        select(
            func.count().label("gaps"),
            func.coalesce(func.sum(orm.CaptureGap.estimated_missing_frames), 0).label("frames"),
        ).where(
            orm.CaptureGap.start_utc >= window.start,
            orm.CaptureGap.start_utc < window.end,
        )
    ).one()

    return {
        "seconds_in_range": round(window.seconds, 1),
        "seconds_captured": round(covered, 1),
        "seconds_from_microphone": round(live, 1),
        # Cannot exceed 1 now that intervals are merged, but clamped anyway: a
        # coverage figure above 100% would discredit every other number here.
        "fraction_captured": round(min(1.0, covered / window.seconds), 5)
        if window.seconds
        else None,
        "gaps": gap_rows.gaps,
        "estimated_missing_frames": int(gap_rows.frames or 0),
        "streams": spans,
    }


def _merged_seconds(intervals: list[tuple[datetime, datetime]]) -> float:
    """Total seconds covered by a set of possibly-overlapping intervals."""
    if not intervals:
        return 0.0
    ordered = sorted(intervals)
    total = 0.0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start > current_end:
            total += (current_end - current_start).total_seconds()
            current_start, current_end = start, end
        elif end > current_end:
            current_end = end
    total += (current_end - current_start).total_seconds()
    return total


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
