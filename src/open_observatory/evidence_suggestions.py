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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

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

    Two aggregate queries, both bounded to the trailing `WINDOW_DAYS` window
    and neither touching `native_result`:

    1. per-species detection count and byte total, `taxonomic_group == 'bird'`
       plus an `event_start_utc` range -- exactly the `(taxonomic_group,
       event_start_utc)` shape `ix_detection_group_start` covers, so this is
       an index range scan per group, not a table scan.
    2. the same window's total evidence bytes across every group, using the
       plain index on `event_start_utc` -- the denominator for "2% of
       evidence bytes" has to mean the whole archive's evidence, not just
       what birds produced, or a station with a large non-bird share (bats,
       acoustic events) would suggest species that are not actually 2% of
       anything.

    Both queries join `detection` -> `detection_media` -> `media_asset` and
    filter `media_asset.reclaimed_at IS NULL`, so a clip retention has
    already deleted does not count towards either the numerator or the
    denominator -- it no longer costs any disk.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=WINDOW_DAYS)

    per_species = session.execute(
        select(
            orm.Detection.common_name,
            func.count(func.distinct(orm.Detection.id)),
            func.coalesce(func.sum(orm.MediaAsset.byte_length), 0),
        )
        .select_from(orm.Detection)
        .join(orm.DetectionMedia, orm.DetectionMedia.detection_id == orm.Detection.id)
        .join(orm.MediaAsset, orm.MediaAsset.id == orm.DetectionMedia.media_asset_id)
        .where(orm.Detection.taxonomic_group == _BIRD_GROUP)
        .where(orm.Detection.event_start_utc >= cutoff)
        .where(orm.Detection.common_name.is_not(None))
        .where(orm.MediaAsset.reclaimed_at.is_(None))
        .group_by(orm.Detection.common_name)
    ).all()

    total_bytes = session.execute(
        select(func.coalesce(func.sum(orm.MediaAsset.byte_length), 0))
        .select_from(orm.Detection)
        .join(orm.DetectionMedia, orm.DetectionMedia.detection_id == orm.Detection.id)
        .join(orm.MediaAsset, orm.MediaAsset.id == orm.DetectionMedia.media_asset_id)
        .where(orm.Detection.event_start_utc >= cutoff)
        .where(orm.MediaAsset.reclaimed_at.is_(None))
    ).scalar_one()

    common = {n.casefold() for n in common_species}
    implausible = {n.casefold() for n in implausible_species}
    dismissed = {n.casefold() for n in dismissed_species}

    suggestions = []
    for common_name, detection_count, byte_total in per_species:
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
