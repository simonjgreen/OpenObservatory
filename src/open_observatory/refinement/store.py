"""Reading candidates, and writing a refinement without rewriting history.

This module is where charter item 5's rules stop being prose. Everything that
could quietly turn refinement into "the record improved and nobody can say why"
happens through :func:`record_refinement`, and it refuses rather than warns.

The write is deliberately small. A refinement inserts one ``refinement`` row and
touches exactly three bookkeeping columns on the detection --
``refined_at`` / ``refinement_version`` / ``refinement_outcome``. It never
touches ``common_name``, ``scientific_name``, ``taxonomic_group``, ``score`` or
``native_result``, and it proves that rather than intending it: the claim
columns are read before the write and compared after, and a mismatch raises.
That guard exists for the refiner that has not been written yet, not for the one
that has.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import models as orm
from .contracts import (
    EvidenceIdentity,
    RefinementCandidate,
    RefinementOutcome,
    RefinementProposal,
    RefinementViolation,
)

log = structlog.get_logger(__name__)

#: The columns that carry the station's claim about what happened. Refinement
#: never writes any of them; :func:`record_refinement` asserts it.
CLAIM_COLUMNS = (
    "common_name",
    "scientific_name",
    "canonical_taxon_id",
    "rank",
    "taxonomic_group",
    "score",
    "calibrated_probability",
    "peak_frequency_hz",
    "native_result",
)

#: Media kinds a refiner can classify. The audible renderings are for human
#: ears: a heterodyne mix has discarded everything outside its tuned band and a
#: time-expanded clip is no longer at its original rate, so classifying either
#: would be classifying an artefact of the renderer.
REFINABLE_KINDS = ("evidence_native",)


def find_candidates(
    session: Session,
    *,
    identity: EvidenceIdentity,
    groups: Sequence[str],
    limit: int,
    include_refined: bool = False,
) -> list[RefinementCandidate]:
    """Oldest-unrefined-first events this refiner has not yet examined.

    Oldest first, not newest first, deliberately: the charter's retention
    decision assumes every event meets the refiner within about a day, and a
    newest-first runner with a backlog would starve exactly the events closest
    to losing their evidence. A backlog therefore drains from the end that is
    about to expire.

    Rows whose ``evidence_fingerprint`` this refiner has already written are
    excluded by default -- that is rule 1's second half made cheap, so a nightly
    run does not re-classify the whole archive and does not depend on the unique
    index firing to stay idempotent.
    """
    already = set()
    if not include_refined:
        already = {
            row[0]
            for row in session.execute(
                select(orm.Refinement.detection_id).where(
                    orm.Refinement.evidence_fingerprint == identity.fingerprint
                )
            ).all()
        }

    query = (
        select(orm.Detection, orm.Detector, orm.MediaAsset)
        .join(orm.Detector, orm.Detector.id == orm.Detection.detector_id)
        .join(orm.DetectionMedia, orm.DetectionMedia.detection_id == orm.Detection.id)
        .join(orm.MediaAsset, orm.MediaAsset.id == orm.DetectionMedia.media_asset_id)
        .where(orm.Detection.taxonomic_group.in_(list(groups)))
        .where(orm.MediaAsset.kind.in_(REFINABLE_KINDS))
        .where(orm.MediaAsset.reclaimed_at.is_(None))
        .order_by(orm.Detection.event_start_utc.asc())
        # Over-fetch: the already-refined filter is applied in Python (the id
        # set is small and cross-dialect NOT IN of thousands of UUIDs is not),
        # and one detection can carry more than one native asset.
        .limit(max(limit * 4, limit))
    )

    candidates: list[RefinementCandidate] = []
    seen: set[uuid.UUID] = set()
    for detection, detector, asset in session.execute(query).all():
        if detection.id in seen or detection.id in already:
            continue
        seen.add(detection.id)
        candidates.append(
            RefinementCandidate(
                detection_id=detection.id,
                event_start_utc=detection.event_start_utc,
                taxonomic_group=detection.taxonomic_group,
                common_name=detection.common_name,
                scientific_name=detection.scientific_name,
                score=float(detection.score),
                peak_frequency_hz=detection.peak_frequency_hz,
                detector_plugin_id=detector.plugin_id,
                detector_model_id=detector.model_id,
                detector_model_version=detector.model_version,
                native_result=dict(detection.native_result or {}),
                clip_path=Path(asset.storage_uri),
                clip_sample_rate=asset.sample_rate,
                media_asset_id=asset.id,
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def _claim_snapshot(detection: orm.Detection) -> tuple[object, ...]:
    """Deep-copied, because ``native_result`` is a mutable dict.

    A shallow snapshot would compare the same object with itself and pass
    happily while a refiner scribbled inside the detector's own output -- the
    exact silent-rewrite this guard exists to catch.
    """
    return tuple(copy.deepcopy(getattr(detection, name)) for name in CLAIM_COLUMNS)


def record_refinement(
    session: Session,
    *,
    candidate: RefinementCandidate,
    identity: EvidenceIdentity,
    proposal: RefinementProposal,
    authority: str,
    now: datetime | None = None,
) -> orm.Refinement | None:
    """Write one refinement, or refuse. Returns ``None`` if already recorded.

    Refuses, with :class:`RefinementViolation`, when:

    * the refiner is the instrument that made the original claim (rule 1,
      first half -- delegated to
      :meth:`EvidenceIdentity.check_is_new_information`);
    * a ``propose``-authority refiner reports an ``applied`` outcome, or a
      proposal that carries a claim is written as ``applied`` at all. No shipped
      refiner has ``apply`` authority; see ADR-045 for why the BatDetect2
      cascade in particular does not;
    * writing the row moved any column in :data:`CLAIM_COLUMNS` (rule 2).

    Returns ``None`` -- rather than raising -- when this exact evidence has
    already been recorded for this detection. Re-running a refiner unchanged is
    a legitimate, idempotent thing to do (a timer that fired twice, a resumed
    batch); what must not happen is a *second answer* being banked from it.
    """
    identity.check_is_new_information(
        original_model_id=candidate.detector_model_id,
        original_model_version=candidate.detector_model_version,
    )

    if authority not in ("propose", "apply"):
        raise RefinementViolation(f"unknown refiner authority {authority!r}")
    if proposal.outcome is RefinementOutcome.APPLIED and authority != "apply":
        raise RefinementViolation(
            f"refiner {identity.refiner_id!r} has 'propose' authority and may not apply a "
            "refinement to the record; a proposal is reviewed by a person (charter item 5 "
            "and the honesty constraint -- see ADR-045)"
        )

    existing = session.execute(
        select(orm.Refinement)
        .where(orm.Refinement.detection_id == candidate.detection_id)
        .where(orm.Refinement.evidence_fingerprint == identity.fingerprint)
    ).scalar_one_or_none()
    if existing is not None:
        log.debug(
            "refinement.already_recorded",
            detection_id=str(candidate.detection_id),
            refiner=identity.refiner_id,
            fingerprint=identity.fingerprint[:12],
        )
        return None

    detection = session.get(orm.Detection, candidate.detection_id)
    if detection is None:
        raise RefinementViolation(
            f"detection {candidate.detection_id} no longer exists; refusing to record a "
            "refinement against a row that is not there"
        )
    before = _claim_snapshot(detection)

    stamped = now or datetime.now(UTC)
    row = orm.Refinement(
        detection_id=candidate.detection_id,
        refiner_id=identity.refiner_id,
        refiner_version=identity.refiner_version,
        model_id=identity.model_id,
        model_version=identity.model_version,
        model_sha256=identity.model_sha256,
        evidence_fingerprint=identity.fingerprint,
        basis=str(proposal.basis),
        outcome=str(proposal.outcome),
        reason=proposal.reason,
        # Rule 2: the prior verdict, verbatim, on the refinement itself. Read
        # from the live row rather than from the candidate, so a candidate
        # assembled minutes ago cannot record a stale "original".
        original_common_name=detection.common_name,
        original_scientific_name=detection.scientific_name,
        original_taxonomic_group=detection.taxonomic_group,
        original_score=float(detection.score),
        proposed_common_name=proposal.proposed_common_name,
        proposed_scientific_name=proposal.proposed_scientific_name,
        proposed_rank=proposal.proposed_rank,
        proposed_taxonomic_group=proposal.proposed_taxonomic_group,
        proposed_score=proposal.proposed_score,
        applied=proposal.outcome is RefinementOutcome.APPLIED,
        evidence={
            **dict(proposal.evidence),
            "identity": identity.to_dict(),
            "clip": str(candidate.clip_path) if candidate.clip_path else None,
        },
        created_at=stamped,
    )
    session.add(row)

    # Rule 3, and the charter's retention safeguard: the event itself now says
    # that refinement ran, at what version, with what outcome.
    detection.refined_at = stamped
    detection.refinement_version = identity.version_label
    detection.refinement_outcome = str(proposal.outcome)

    after = _claim_snapshot(detection)
    if before != after:
        moved = [
            name
            for name, old, new in zip(CLAIM_COLUMNS, before, after, strict=True)
            if old != new
        ]
        raise RefinementViolation(
            "refinement moved the original claim on detection "
            f"{candidate.detection_id} ({', '.join(moved)}); charter item 5 requires the "
            "original claim to be preserved -- record a proposal, do not overwrite"
        )

    log.info(
        "refinement.recorded",
        detection_id=str(candidate.detection_id),
        refiner=identity.refiner_id,
        outcome=str(proposal.outcome),
        basis=str(proposal.basis),
        proposed=proposal.proposed_scientific_name,
        original=detection.common_name,
        applied=row.applied,
    )
    return row
