"""Suggesting additions to the common-species list, without nagging (ADR-074).

`evidence_common_species` is deliberately an operator list, not a computed
threshold -- "which birds are boring is a matter of taste and place, not
statistics." But an operator cannot maintain a list from nothing: they do not
know that European Greenfinch quietly became 3.1 GB last month. This module
is the station noticing that on the operator's behalf.

A species is suggested when, over the trailing 30 days, it produced more than
500 detections *and* more than 2% of evidence bytes, is not already on the
common list, has not been dismissed, and is a plausible bird here.

Plausibility is checked the cheap way ADR-076's second amendment settled on
for the identical problem: `evidence_implausible_species` is an operator
list, not a read of the `plausibility_review` block buried in the wide
`native_result` JSON column. ADR-062's budget is why that column stays
untouched by any query in this module.

The route in `api/app.py` is a thin adapter: it calls `compute_suggestions`
and shapes the result into JSON. Everything that decides *whether* a species
qualifies lives here, tested directly against a database session, the same
split `slo.py`, `evidence_value.py` and `plausibility.py` use to keep
threshold logic out of FastAPI.

This module used to join `detection -> detection_media -> media_asset` for
both of its aggregates, on the theory that the `(taxonomic_group,
event_start_utc)` bound on `detection` would give SQLite "an index range scan
per group, not a table scan". Measured against the production schema, that
was wrong in the identical way ADR-076 already found and fixed once:
`EXPLAIN QUERY PLAN` showed `SCAN detection_media` on the whole table,
every call, regardless of the bound -- because that bound sits on
`detection`, the far side of the join, and SQLite has no way to push it
through. `WINDOW_DAYS` bounded nothing either on a station whose archive is
younger than the window: "the trailing 30 days" was the entire
`detection_media` table, ~362,000 rows, scanned twice on every page load of
the settings screen this feeds. Two such calls measured at ~35 s combined
against the live station and cost it 41 s of dropped audio. Not joining is
the fix, not bounding the window harder -- see ADR-076 and the commit that
split this module's two aggregates and added a third, narrower one:

1. per-species detection counts, straight off `detection` alone --
   `taxonomic_group == 'bird'` plus an `event_start_utc` range is exactly the
   `(taxonomic_group, event_start_utc)` shape `ix_detection_group_start`
   covers, so this is an index range scan, no join at all.
2. the denominator, straight off `media_asset` alone -- live (unreclaimed)
   bytes created since the cutoff, served by `ix_media_asset_live_created`.
   This is bytes of live evidence written in the window across every group,
   not just birds: the denominator for "2% of evidence bytes" has to mean
   the whole archive's evidence, or a station with a large non-bird share
   (bats, acoustic events) would suggest species that are not actually 2% of
   anything. It is also more honest than the old join-based version, which
   silently dropped any asset whose detection fell outside the window.
3. byte totals, joined, but only for the handful of species that already
   passed the `> 500 detections` threshold in step 1 *and* survive the
   common/implausible/dismissed exclusions -- driven from
   `ix_detection_group_start` via an `IN (...)` list, not a scan of anything.
   Skipped entirely when no species survives those exclusions either, which
   is the common case: two cheap queries and stop.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from sqlalchemy import func, select

from .db import models as orm

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = ["MIN_BYTE_FRACTION", "MIN_DETECTIONS", "WINDOW_DAYS", "Suggestion", "compute_suggestions"]

#: Spelled out here rather than imported from `retention.py`, for the same
#: reason that module gives its own `_BIRD_GROUP`/`_BAT_GROUP` constants:
#: this is a cross-module literal, not a shared object, and importing it
#: would couple two independently-testable modules for no behaviour.
_BIRD_GROUP = "bird"

#: ADR-074: "over the trailing 30 days".
WINDOW_DAYS = 30
#: ADR-074: "more than 500 detections".
MIN_DETECTIONS = 500
#: ADR-074: "more than 2% of evidence bytes".
MIN_BYTE_FRACTION = 0.02


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One species the station thinks belongs on the common list."""

    common_name: str
    detection_count: int
    byte_total: int
    window_days: int


def compute_suggestions(
    session: Session,
    *,
    common_species: Sequence[str],
    implausible_species: Sequence[str],
    dismissed_species: Sequence[str],
    now: datetime | None = None,
) -> list[Suggestion]:
    """Species that qualify for the common-list suggestion prompt right now.

    Three queries, none of them a table scan (see the module docstring for
    why the previous two-query, join-everywhere shape was not):

    1. per-species detection counts, off `detection` alone.
    2. the evidence-bytes denominator, off `media_asset` alone.
    3. byte totals, joined, for the species that already passed the
       detection-count threshold in step 1 *and* are not already common,
       implausible or dismissed -- skipped entirely when no species survives
       both.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=WINDOW_DAYS)

    per_species_rows = session.execute(
        select(
            orm.Detection.common_name,
            func.count(orm.Detection.id),
        )
        .where(orm.Detection.taxonomic_group == _BIRD_GROUP)
        .where(orm.Detection.event_start_utc >= cutoff)
        .where(orm.Detection.common_name.is_not(None))
        .group_by(orm.Detection.common_name)
    ).all()
    # The `is_not(None)` filter above guarantees `common_name` is never NULL
    # in this result set; the cast tells mypy what the `WHERE` already did.
    per_species: list[tuple[str, int]] = [(cast(str, name), count) for name, count in per_species_rows]

    common = {n.casefold() for n in common_species}
    implausible = {n.casefold() for n in implausible_species}
    dismissed = {n.casefold() for n in dismissed_species}

    # Exclude species that already can't qualify (common/implausible/dismissed)
    # before the expensive byte query, so it is never asked about a species
    # whose answer will be thrown away. This is an optimisation only:
    # `_qualifies` below still checks the same three sets and remains the
    # single place the rule lives, so a second caller (or a future refactor
    # of this pre-filter) cannot bypass it.
    candidates = [
        name
        for name, detection_count in per_species
        if detection_count > MIN_DETECTIONS and name.casefold() not in common | implausible | dismissed
    ]

    total_bytes = session.execute(
        select(func.coalesce(func.sum(orm.MediaAsset.byte_length), 0))
        .where(orm.MediaAsset.reclaimed_at.is_(None))
        .where(orm.MediaAsset.created_at >= cutoff)
    ).scalar_one()

    byte_totals: dict[str, int] = {}
    if candidates:
        byte_total_rows = session.execute(
            select(
                orm.Detection.common_name,
                func.coalesce(func.sum(orm.MediaAsset.byte_length), 0),
            )
            .select_from(orm.Detection)
            .join(orm.DetectionMedia, orm.DetectionMedia.detection_id == orm.Detection.id)
            .join(orm.MediaAsset, orm.MediaAsset.id == orm.DetectionMedia.media_asset_id)
            .where(orm.Detection.taxonomic_group == _BIRD_GROUP)
            .where(orm.Detection.event_start_utc >= cutoff)
            .where(orm.Detection.common_name.in_(candidates))
            .where(orm.MediaAsset.reclaimed_at.is_(None))
            .group_by(orm.Detection.common_name)
        ).all()
        byte_totals = {cast(str, name): total for name, total in byte_total_rows}

    detection_counts: dict[str, int] = dict(per_species)

    suggestions = []
    for common_name in candidates:
        detection_count = detection_counts[common_name]
        byte_total = byte_totals.get(common_name, 0)
        if _qualifies(
            common_name,
            detection_count=detection_count,
            byte_total=byte_total,
            total_bytes=total_bytes,
            common=common,
            implausible=implausible,
            dismissed=dismissed,
        ):
            suggestions.append(
                Suggestion(
                    common_name=common_name,
                    detection_count=detection_count,
                    byte_total=byte_total,
                    window_days=WINDOW_DAYS,
                )
            )
    suggestions.sort(key=lambda s: s.byte_total, reverse=True)
    return suggestions


def _qualifies(
    common_name: str,
    *,
    detection_count: int,
    byte_total: int,
    total_bytes: int,
    common: set[str],
    implausible: set[str],
    dismissed: set[str],
) -> bool:
    """The pure threshold rule from ADR-074, isolated so it is testable
    without a database: more than 500 detections *and* more than 2% of
    evidence bytes, not already common, not dismissed, not implausible.
    """
    name = common_name.casefold()
    if name in common or name in dismissed or name in implausible:
        return False
    if detection_count <= MIN_DETECTIONS:
        return False
    if total_bytes <= 0:
        return False
    return (byte_total / total_bytes) > MIN_BYTE_FRACTION
