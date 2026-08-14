"""Shared helpers for human review of detections (ADR-043).

``Review`` (``db/models.py``) is append-only: the docstring on the class says
plainly that "current status is derived from the latest valid review." Every
consumer that needs "the current review state of N detections" -- the
detection API, the retention sweeper, the plausibility repair pass -- goes
through this module rather than each re-deriving its own "latest row per
detection_id" query, so there is exactly one definition of "latest" to get
right, and one place to fix it.

Three rules from the charter (priority 5) are load-bearing here:

* **Preserve the original claim.** Nothing in this module ever updates or
  deletes a ``Review`` row, or a ``Detection`` row's own taxonomy columns.
  A correction is a new row that references what it corrects.
* **A corrected record must be distinguishable from an original one.**
  ``latest_reviews_by_detection`` and ``latest_review`` return the ``Review``
  row itself -- status, actor, timestamp, and (for a correction)
  ``corrected_taxon_id``/``corrected_common_name``/``corrected_scientific_name``
  -- so a caller always has "what changed it, and when, and who" available
  alongside the original ``Detection`` row it never touched.
* **A human's ear outranks a later machine refinement.** ``reviewed_detection_ids``
  exists specifically so ``plausibility_repair.py`` (a machine repair pass)
  can skip any detection a human has already looked at, in any capacity --
  see that module's docstring for how it is used.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import models as orm

#: A positive act of "keep this, it needs a human ear" -- distinct from
#: confirmed/rejected/corrected, and the mechanism ``retention.py`` uses to
#: exempt a detection's evidence from the age-based sweep tiers. See
#: ``retention.held_detection_ids`` below and ADR-043.
HELD_STATUS = "held"
CORRECTED_STATUS = "corrected"
CONFIRMED_STATUS = "confirmed"
REJECTED_STATUS = "rejected"

#: Every legal value of ``Review.status``. Kept as the single source of truth
#: for the API's Pydantic pattern (``api/app.py: ReviewIn``) so the two never
#: drift.
STATUSES = frozenset({CONFIRMED_STATUS, REJECTED_STATUS, CORRECTED_STATUS, HELD_STATUS})


def latest_reviews_by_detection(
    session: Session, detection_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, orm.Review]:
    """The current (latest-by-``created_at``) review for each detection in
    ``detection_ids`` that has ever been reviewed.

    A detection with no review is simply absent from the returned mapping --
    callers should treat a missing key the same as an explicit ``None``.

    Two reviews on the same detection landing at the exact same timestamp is
    the only case this cannot order; it is not disambiguated further (no
    review row currently carries a strictly-increasing sequence number), so
    one of the two is returned arbitrarily. This mirrors the single-detection
    "order by created_at desc limit 1" pattern used throughout this codebase
    before this module existed.
    """
    ids = list(detection_ids)
    if not ids:
        return {}
    latest_ts = (
        select(orm.Review.detection_id, func.max(orm.Review.created_at).label("ts"))
        .where(orm.Review.detection_id.in_(ids))
        .group_by(orm.Review.detection_id)
        .subquery()
    )
    rows = (
        session.execute(
            select(orm.Review).join(
                latest_ts,
                (orm.Review.detection_id == latest_ts.c.detection_id)
                & (orm.Review.created_at == latest_ts.c.ts),
            )
        )
        .scalars()
        .all()
    )
    return {row.detection_id: row for row in rows}


def latest_review(session: Session, detection_id: uuid.UUID) -> orm.Review | None:
    """The current review for a single detection, or ``None``."""
    return session.execute(
        select(orm.Review)
        .where(orm.Review.detection_id == detection_id)
        .order_by(orm.Review.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def held_detection_ids(session: Session) -> set[uuid.UUID]:
    """Every detection whose *current* review status is ``held``.

    Scoped to the whole review table rather than a candidate id list: the
    retention sweeper needs this before it knows which detections it is about
    to consider for deletion.
    """
    latest_ts = (
        select(orm.Review.detection_id, func.max(orm.Review.created_at).label("ts"))
        .group_by(orm.Review.detection_id)
        .subquery()
    )
    rows = session.execute(
        select(orm.Review.detection_id)
        .join(
            latest_ts,
            (orm.Review.detection_id == latest_ts.c.detection_id)
            & (orm.Review.created_at == latest_ts.c.ts),
        )
        .where(orm.Review.status == HELD_STATUS)
    ).all()
    return {row[0] for row in rows}


def reviewed_detection_ids(
    session: Session, detection_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of ``detection_ids`` has *any* human review at all (any status).

    Used by ``plausibility_repair.find_implausible_detections`` to enforce
    the charter's precedence rule: a detection a human has already looked at
    -- confirmed, rejected, corrected or held -- is never re-flagged by an
    automated repair pass. The repair pass is for the unreviewed backlog.
    """
    ids = list(detection_ids)
    if not ids:
        return set()
    rows = session.execute(
        select(orm.Review.detection_id.distinct()).where(orm.Review.detection_id.in_(ids))
    ).all()
    return {row[0] for row in rows}


def resolve_taxon(session: Session, taxon_id: str) -> orm.Detection | None:
    """Look up a ``canonical_taxon_id`` against the station's *own* detection
    history.

    ADR-043: a correction target is drawn from a species the station has
    itself already identified at least once (by any detector), not from a
    bundled or network-fetched taxonomy database -- see ``search_taxa``
    below for the same constraint applied to lookup/search. Returns an
    arbitrary matching ``Detection`` row (only its ``common_name`` /
    ``scientific_name`` / ``taxonomic_group`` are used by callers), or
    ``None`` if the station has never produced this taxon id.
    """
    return (
        session.execute(
            select(orm.Detection).where(orm.Detection.canonical_taxon_id == taxon_id).limit(1)
        )
        .scalars()
        .first()
    )


def search_taxa(session: Session, query: str, *, limit: int = 20) -> list[dict[str, object]]:
    """Species the station has itself already identified, matching ``query``
    against common or scientific name (case-insensitive substring).

    Backs the review drawer's taxon-correction lookup (ADR-043). Deliberately
    not backed by a bundled or fetched taxonomy database: BirdNET's own label
    list (``birdnet_labels.txt``) is model data under a separate,
    non-commercial licence (ADR-006) that may not even be present on disk
    (``oo models fetch`` is optional), so it cannot be relied on as a lookup
    source. The station's own detection history is data it already
    unconditionally holds, requires no new dependency, and -- because
    ``canonical_taxon_id`` is only ever set for a species-rank identification
    (``normaliser._canonical_taxon_id``) -- is never itself a fabricated
    claim (CLAUDE.md's "do not fabricate classifier support" rule extends
    naturally to "do not fabricate a taxonomy").

    Known limitation, recorded in ADR-043: a reviewer can only correct *to* a
    taxon this station has produced before. A first-ever, correct-by-ear
    identification of a species the station has never itself detected has no
    match here and cannot currently be entered as a structured correction.
    """
    needle = query.strip().lower()
    if not needle:
        return []
    pattern = f"%{needle}%"
    rows = session.execute(
        select(
            orm.Detection.canonical_taxon_id,
            orm.Detection.common_name,
            orm.Detection.scientific_name,
            orm.Detection.taxonomic_group,
            func.count(orm.Detection.id).label("detections"),
        )
        .where(orm.Detection.canonical_taxon_id.is_not(None))
        .where(
            func.lower(orm.Detection.common_name).like(pattern)
            | func.lower(orm.Detection.scientific_name).like(pattern)
        )
        .group_by(
            orm.Detection.canonical_taxon_id,
            orm.Detection.common_name,
            orm.Detection.scientific_name,
            orm.Detection.taxonomic_group,
        )
        .order_by(func.count(orm.Detection.id).desc())
        .limit(limit)
    ).all()
    return [
        {
            "taxon_id": row.canonical_taxon_id,
            "common_name": row.common_name,
            "scientific_name": row.scientific_name,
            "taxonomic_group": row.taxonomic_group,
            "detections": row.detections,
        }
        for row in rows
    ]
