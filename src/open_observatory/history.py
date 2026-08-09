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

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, Select, cast, func, select
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.sql.elements import ColumnElement

from . import plausibility
from .db import models as orm
from .display import display_title

#: Groups that name an organism, as opposed to describing an unattributed sound.
IDENTIFIED_GROUPS = ("bird", "bat")

#: The only ``audio_stream.source_kind`` that represents a real microphone.
#: ``synthetic`` and ``replay`` streams are genuine records of what a detector did
#: -- useful for testing, never deleted -- but they are not evidence of an animal.
#: The incident that prompted this: an AudioMoth left in USB/OFF made `OO_SOURCE=auto`
#: fall back to the synthetic dawn-chorus scene, and five "Grey-winged Inca-Finch"
#: detections (a South American species) sat indistinguishable from real ones in
#: every view. coverage() above already draws this line for *seconds*
#: (``seconds_from_microphone``); the rest of this module draws the same line for
#: *rows*, so a query result cannot make the same mistake a summary statistic did.
LIVE_SOURCE_KIND = "alsa"


#: Every caller passes an ORM attribute (`orm.AudioStream.source_kind`), which is
#: an `InstrumentedAttribute`, not a `ColumnElement` -- so the narrower annotation
#: these two functions carried was wrong at every single call site, and mypy said
#: so eight times. Widened rather than silenced: both are usable as either.
SourceKindColumn = ColumnElement[Any] | InstrumentedAttribute[Any]


def is_live(column: SourceKindColumn) -> ColumnElement[Any]:
    """True for a genuine microphone stream, false for synthetic/replay/unknown."""
    return column == LIVE_SOURCE_KIND


def is_not_live(column: SourceKindColumn) -> ColumnElement[Any]:
    """The complement of :func:`is_live`, including a missing/NULL source_kind.

    Plain ``!=`` would silently drop NULLs (SQL three-valued logic), which here
    would mean a detection whose stream row cannot be found is shown by default
    instead of hidden -- the wrong way round for a filter whose job is to keep
    unproven rows out of the wildlife-facing views.
    """
    return (column != LIVE_SOURCE_KIND) | column.is_(None)


def _withdrawn_flag() -> ColumnElement[Any]:
    """The stored withdrawal boolean, as a SQL expression (ADR-044).

    ``native_result`` is a ``JSON`` column, so this compiles to
    ``json_extract`` on SQLite and to ``->>``/``CAST`` on PostgreSQL without
    either dialect being named here (ADR-007 keeps one schema for both).
    """
    return orm.Detection.native_result[plausibility.REVIEW_KEY][
        plausibility.WITHDRAWN_KEY
    ].as_boolean()


def is_withdrawn() -> ColumnElement[Any]:
    """True for a detection whose claim has been withdrawn by review (ADR-044).

    Takes no column argument, unlike :func:`is_live`: the withdrawal lives in
    ``detection.native_result`` and nowhere else, so there is nothing for a
    caller to choose.
    """
    return _withdrawn_flag().is_(True)


def is_not_withdrawn() -> ColumnElement[Any]:
    """The complement, and NULL-safe on purpose.

    The overwhelming majority of rows have no review block at all, so the
    extracted value is SQL ``NULL`` for them; a plain ``= false`` would drop
    every single one. Same three-valued-logic trap :func:`is_not_live` documents,
    with the opposite consequence if you get it wrong -- there it hid too little,
    here it would hide almost everything.
    """
    flag = _withdrawn_flag()
    return flag.is_(None) | flag.is_(False)


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


def excluded_synthetic_count(session: Session, window: Range, *, min_score: float = 0.0) -> int:
    """How many detections in this window a default (``include_synthetic=False``)
    call to timeline()/species_summary() would hide.

    Filtering the wildlife views by source is only honest if the exclusion is
    reported somewhere -- an operator wondering why a detection they watched
    happen is missing from history must be able to find out why, rather than
    just notice a gap and assume the detector missed it.
    """
    query = _in_range(
        select(func.count(orm.Detection.id)).outerjoin(
            orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id
        ),
        window,
    ).where(is_not_live(orm.AudioStream.source_kind))
    if min_score > 0:
        query = query.where(orm.Detection.score >= min_score)
    return session.execute(query).scalar_one()


def excluded_withdrawn_count(session: Session, window: Range, *, min_score: float = 0.0) -> int:
    """How many detections in this window ``species_summary`` hides as withdrawn.

    Exactly the same argument as :func:`excluded_synthetic_count`, for exactly
    the same reason: a species that vanished from the summary because its only
    detections were withdrawn must leave a trace an operator can find, or the
    correction is indistinguishable from the detector having missed it. Counted
    over live-source rows only, since a synthetic row is already excluded and
    would otherwise be reported twice.
    """
    query = (
        _in_range(
            select(func.count(orm.Detection.id)).outerjoin(
                orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id
            ),
            window,
        )
        .where(is_live(orm.AudioStream.source_kind))
        .where(is_withdrawn())
    )
    if min_score > 0:
        query = query.where(orm.Detection.score >= min_score)
    return session.execute(query).scalar_one()


def timeline(
    session: Session,
    window: Range,
    *,
    bucket_seconds: int | None = None,
    min_score: float = 0.0,
    include_unidentified: bool = True,
    include_synthetic: bool = False,
) -> dict[str, object]:
    """Detection counts per time bucket, split by taxonomic group.

    This is the shape of a night: when activity started, when it peaked, whether
    anything was happening at 03:00. By default that shape must be made of real
    detections only -- see LIVE_SOURCE_KIND.
    """
    seconds = bucket_seconds or choose_bucket_seconds(window.seconds)
    bucket = bucket_expression(session.get_bind().dialect, orm.Detection.event_start_utc, seconds)

    query = _in_range(
        select(
            bucket.label("bucket"),
            orm.Detection.taxonomic_group.label("group"),
            func.count().label("detections"),
            func.max(orm.Detection.score).label("best_score"),
        ).outerjoin(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id),
        window,
    )
    if min_score > 0:
        query = query.where(orm.Detection.score >= min_score)
    if not include_unidentified:
        query = query.where(orm.Detection.taxonomic_group.in_(IDENTIFIED_GROUPS))
    if not include_synthetic:
        query = query.where(is_live(orm.AudioStream.source_kind))
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
        "excluded_synthetic_count": (
            0
            if include_synthetic
            else excluded_synthetic_count(session, window, min_score=min_score)
        ),
    }


def species_summary(
    session: Session,
    window: Range,
    *,
    limit: int = 40,
    min_score: float = 0.0,
    include_unidentified: bool = False,
    include_synthetic: bool = False,
    include_withdrawn: bool = False,
) -> list[dict[str, object]]:
    """What was detected in the window, once per distinct label, with its extent.

    Excludes synthetic/replay detections by default (see LIVE_SOURCE_KIND): this is
    presented as a list of things heard, and a detector running against a test
    scene has not heard anything.

    Excludes withdrawn detections by default for the same reason (ADR-044). This
    is the one shape in this module that *names a species*, and it is an
    aggregate -- there is no row here to hang a "withdrawn" marker on, so a
    withdrawn *Western Screech-Owl* would appear in a list of things heard as a
    plain factual claim. The individual rows remain reachable and marked through
    ``GET /api/v1/detections``; ``excluded_withdrawn_count`` reports the size of
    what this hid. ``include_withdrawn=True`` is the diagnostic escape hatch,
    matching ``include_synthetic``.
    """
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
        )
        .join(orm.Detector, orm.Detection.detector_id == orm.Detector.id)
        .outerjoin(orm.AudioStream, orm.AudioStream.id == orm.Detection.stream_id),
        window,
    )
    if min_score > 0:
        query = query.where(orm.Detection.score >= min_score)
    if not include_unidentified:
        query = query.where(orm.Detection.taxonomic_group.in_(IDENTIFIED_GROUPS))
    if not include_synthetic:
        query = query.where(is_live(orm.AudioStream.source_kind))
    if not include_withdrawn:
        query = query.where(is_not_withdrawn())

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


#: A stream is only flagged "suspect" if its claimed wall-clock span is long
#: enough that a frame/clock mismatch cannot be an artefact of rounding or a
#: process starting and stopping within the same second.
SUSPECT_MIN_CLAIMED_SECONDS = 60.0

#: How far the frame-derived duration may fall short of the claimed wall-clock
#: span before a row is flagged suspect. 0.9 gives headroom for legitimate
#: clock-rate offset (measured on this station at a few hundred ppm, nowhere
#: near enough to explain a 10% shortfall) while still catching the case that
#: prompted this: 2.79 hours of frames inside a 32 hour claim is a ratio of 0.09.
SUSPECT_FRAME_RATIO = 0.9


def _frame_count_is_trustworthy(end_reason: str | None, detail: object) -> bool:
    """False for a row whose `frame_count` cannot be told apart from "never recorded".

    Every row closed by `Station._close_stream_row` (a graceful exit or a read
    error such as the `AlsaCaptureError` in ADR-024) carries a real, per-stream
    frame count -- that is exactly the number this module's frame-derived cap
    exists to trust over the row's own claimed `end_utc`.

    Rows closed by `Station._close_orphaned_streams` are different. Before
    ADR-024 added the `last_frame_at_utc` heartbeat, that path never wrote
    `frame_count` at all, so every historical row it touched shows
    ``frame_count == 0`` -- indistinguishable from "definitely captured
    nothing". Treating that zero as ground truth would zero out coverage for
    what may well have been hours of genuine, working capture, which is the
    opposite failure to the one this module exists to prevent. Only a row the
    *current* orphan-recovery path closed via the heartbeat
    (``detail.orphan_recovery.method == "heartbeat"``) carries a frame count
    earned the same way a normal close does, so only that case is trusted.
    """
    if end_reason != "process_exited":
        return True
    recovery = detail.get("orphan_recovery") if isinstance(detail, dict) else None
    return isinstance(recovery, dict) and recovery.get("method") == "heartbeat"


def _honest_stream_end(
    *,
    start: datetime,
    claimed_end: datetime,
    sample_rate: int,
    frame_count: int,
    last_frame_at_utc: datetime | None,
    frame_count_trusted: bool = True,
) -> datetime:
    """The latest moment there is actual evidence this stream was still capturing.

    `end_utc` is a claim -- what the row says happened -- and claims are exactly
    what this module exists to stop trusting blindly (see the module docstring's
    account of the 1302% coverage incident, and ADR-024 for the sequel: a single
    stream row claimed a 32 hour span but delivered 2.79 hours of frames, with no
    detections and no capture-gap rows anywhere in the other 29). Two
    independent, cheaper-to-fake-badly signals cap it instead:

    - the frame count actually delivered, converted to seconds at the stream's
      own sample rate and laid down starting at `start` (audio is known to have
      begun exactly there -- `StreamClock` anchors to the first block read).
      Skipped when `frame_count_trusted` is false -- see
      :func:`_frame_count_is_trustworthy` for why a legacy orphan-closed row's
      zero must not be read as "definitely captured nothing".
    - `last_frame_at_utc`, the heartbeat written every ~10 s while the stream
      was open (ADR-024) -- a direct timestamp, not an assumption that frames
      arrived at a constant rate. Trusted whenever present, regardless of
      `frame_count_trusted`, because it is only ever written from a real block.

    The tighter of whichever bounds are available wins. Never returns a value
    later than `claimed_end`, so this can only pull coverage down, never invent
    extra.
    """
    honest = claimed_end
    if sample_rate > 0 and frame_count_trusted:
        frame_bound = start + timedelta(seconds=frame_count / sample_rate)
        honest = min(honest, frame_bound)
    if last_frame_at_utc is not None:
        honest = min(honest, max(start, _aware(last_frame_at_utc)))
    return max(start, honest)


def coverage(session: Session, window: Range) -> dict[str, object]:
    """How much of the window the station was actually capturing for.

    Without this, an empty night is ambiguous: nothing called, or nothing was
    listening. The answer changes what the absence means, so it is reported
    alongside the detections rather than left for someone to wonder about.

    Coverage above 100% must be impossible *by construction*, not by hoping
    every row is well-formed (ADR-024). Two defences, applied in order:

    1. Each stream's contribution is capped by what it actually delivered --
       see :func:`_honest_stream_end` -- before it ever reaches an interval.
       A row whose claimed end time outlived its own capture, whether from a
       process that hung instead of crashing or one a previous session's
       start-up patch closed with a guessed timestamp, cannot inflate the
       total.
    2. Intervals are still merged before summing (the original 1302% fix):
       overlapping restarts must not be double-counted either.
    """
    streams = session.execute(
        select(
            orm.AudioStream.id,
            orm.AudioStream.source_kind,
            orm.AudioStream.start_utc,
            orm.AudioStream.end_utc,
            orm.AudioStream.sample_rate,
            orm.AudioStream.frame_count,
            orm.AudioStream.discontinuity_count,
            orm.AudioStream.last_frame_at_utc,
            orm.AudioStream.end_reason,
            orm.AudioStream.detail,
        ).where(
            orm.AudioStream.start_utc < window.end,
            (orm.AudioStream.end_utc.is_(None)) | (orm.AudioStream.end_utc > window.start),
        )
    ).all()

    now = datetime.now(UTC)
    spans: list[dict[str, object]] = []
    intervals: list[tuple[datetime, datetime, str]] = []
    suspect_count = 0
    for row in streams:
        full_start = _aware(row.start_utc)
        claimed_end = _aware(row.end_utc) if row.end_utc else now
        trusted = _frame_count_is_trustworthy(row.end_reason, row.detail)
        honest_end = _honest_stream_end(
            start=full_start,
            claimed_end=claimed_end,
            sample_rate=row.sample_rate or 0,
            frame_count=row.frame_count or 0,
            last_frame_at_utc=row.last_frame_at_utc,
            frame_count_trusted=trusted,
        )

        claimed_seconds_full = max(0.0, (claimed_end - full_start).total_seconds())
        frame_seconds_full = (
            (row.frame_count / row.sample_rate) if row.sample_rate and trusted else None
        )
        suspect = (
            frame_seconds_full is not None
            and claimed_seconds_full >= SUSPECT_MIN_CLAIMED_SECONDS
            and frame_seconds_full < claimed_seconds_full * SUSPECT_FRAME_RATIO
        )
        if suspect:
            suspect_count += 1

        start = max(full_start, window.start)
        end = min(honest_end, window.end)
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
                "frame_count": row.frame_count,
                # Unclamped by the window, for the honesty check itself: how long
                # the row claims to span versus how long its own frame count
                # says it actually captured.
                "claimed_seconds": round(claimed_seconds_full, 1),
                "frame_derived_seconds": (
                    round(frame_seconds_full, 1) if frame_seconds_full is not None else None
                ),
                "suspect": suspect,
            }
        )

    # Merge before summing. Restarts and any stream left unclosed by a killed
    # process produce overlapping rows, and adding those up reported 13x coverage
    # of a twelve hour night — a number that cannot be true and so is worse than
    # no number at all.
    covered = _merged_seconds([(a, b) for a, b, _ in intervals])
    live = _merged_seconds([(a, b) for a, b, kind in intervals if kind == LIVE_SOURCE_KIND])

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
        # Cannot exceed 1 now that intervals are both frame-capped and merged,
        # but clamped anyway: a coverage figure above 100% would discredit every
        # other number here.
        "fraction_captured": round(min(1.0, covered / window.seconds), 5)
        if window.seconds
        else None,
        "gaps": gap_rows.gaps,
        "estimated_missing_frames": int(gap_rows.frames or 0),
        "suspect_stream_count": suspect_count,
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


@dataclass(frozen=True, slots=True)
class StreamReconciliation:
    """One `audio_stream` row whose claimed span disagrees with its own frame count.

    Produced by :func:`find_suspect_streams` for the ``oo history reconcile-streams``
    CLI command. Never applied automatically -- see that command's docstring.
    """

    stream_id: uuid.UUID
    source_kind: str
    start_utc: datetime
    claimed_end_utc: datetime
    proposed_end_utc: datetime
    sample_rate: int
    frame_count: int
    claimed_seconds: float
    frame_derived_seconds: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "stream_id": str(self.stream_id),
            "source_kind": self.source_kind,
            "start_utc": _iso(self.start_utc),
            "claimed_end_utc": _iso(self.claimed_end_utc),
            "proposed_end_utc": _iso(self.proposed_end_utc),
            "sample_rate": self.sample_rate,
            "frame_count": self.frame_count,
            "claimed_hours": round(self.claimed_seconds / 3600, 2),
            "frame_derived_hours": round(self.frame_derived_seconds / 3600, 2),
            "reason": self.reason,
        }


def find_suspect_streams(
    session: Session,
    *,
    min_claimed_seconds: float = SUSPECT_MIN_CLAIMED_SECONDS,
    ratio_threshold: float = SUSPECT_FRAME_RATIO,
) -> list[StreamReconciliation]:
    """Scan every closed stream row for one whose frame count contradicts its claim.

    Deliberately restricted to rows that already have an `end_utc` -- i.e. a
    process closed them, gracefully or via `AlsaCaptureError` -- rather than rows
    still open (`end_utc IS NULL`). An open row might belong to a station that is
    running right now; a CLI process with no view of that has no business
    guessing whether it is stale. Open orphans are `Station._close_orphaned_streams`'s
    job, at the next startup of whichever process owns the database, not this
    command's (see ADR-024).

    Also skips any row whose `frame_count` cannot be trusted -- see
    :func:`_frame_count_is_trustworthy` -- rather than reporting every row
    `Station._close_orphaned_streams` closed before the `last_frame_at_utc`
    heartbeat existed (all of them showing `frame_count == 0`) as if that zero
    were measured rather than simply never recorded.
    """
    now = datetime.now(UTC)
    rows = (
        session.execute(select(orm.AudioStream).where(orm.AudioStream.end_utc.is_not(None)))
        .scalars()
        .all()
    )
    out: list[StreamReconciliation] = []
    for row in rows:
        if not _frame_count_is_trustworthy(row.end_reason, row.detail):
            continue
        full_start = _aware(row.start_utc)
        claimed_end = _aware(row.end_utc) if row.end_utc else now
        sample_rate = row.sample_rate or 0
        if sample_rate <= 0:
            continue
        claimed_seconds = max(0.0, (claimed_end - full_start).total_seconds())
        if claimed_seconds < min_claimed_seconds:
            continue
        frame_seconds = row.frame_count / sample_rate
        if frame_seconds >= claimed_seconds * ratio_threshold:
            continue
        honest_end = _honest_stream_end(
            start=full_start,
            claimed_end=claimed_end,
            sample_rate=sample_rate,
            frame_count=row.frame_count or 0,
            last_frame_at_utc=row.last_frame_at_utc,
        )
        out.append(
            StreamReconciliation(
                stream_id=row.id,
                source_kind=row.source_kind,
                start_utc=full_start,
                claimed_end_utc=claimed_end,
                proposed_end_utc=honest_end,
                sample_rate=sample_rate,
                frame_count=row.frame_count or 0,
                claimed_seconds=claimed_seconds,
                frame_derived_seconds=frame_seconds,
                reason=(
                    f"claimed {claimed_seconds / 3600:.2f}h but frame_count implies only "
                    f"{frame_seconds / 3600:.2f}h of audio actually arrived "
                    f"(ratio {frame_seconds / claimed_seconds:.3f} < {ratio_threshold})"
                ),
            )
        )
    return out


def apply_stream_reconciliation(session: Session, item: StreamReconciliation) -> None:
    """Correct one stream row's `end_utc`, preserving the original claim for audit.

    Never call this without having shown the operator :meth:`StreamReconciliation.to_dict`
    first and had it confirmed -- this rewrites the operator's historical record,
    and the whole point of this repair path is that such a rewrite is visible and
    consented to, not silent.
    """
    row = session.get(orm.AudioStream, item.stream_id)
    if row is None:
        return
    detail = dict(row.detail or {})
    detail["reconciliation"] = {
        "claimed_end_utc": _iso(item.claimed_end_utc),
        "corrected_end_utc": _iso(item.proposed_end_utc),
        "reason": item.reason,
        "applied_utc": _iso(datetime.now(UTC)),
    }
    row.detail = detail
    row.end_utc = item.proposed_end_utc
    if row.end_reason and "reconciled" not in row.end_reason:
        row.end_reason = (row.end_reason[:40] + " (reconciled)")[:64]
