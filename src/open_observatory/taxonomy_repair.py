"""Historical repair for BirdNET's non-taxonomic classes (ADR-049).

``oo detections reconcile-taxonomy`` (``cli.py``) finds stored BirdNET rows
that record a sound category — an engine, a dog, a human voice — as though it
were a bird identified to species rank, and corrects the fields that make that
claim.

**What was wrong.** ``detectors/birdnet.py`` stamped ``rank="species"`` and
``taxonomic_group="bird"`` onto every output class, including the eleven that
are not species (``detectors/birdnet_classes.py``). ``normaliser`` then minted
``canonical_taxon_id="sci:engine"`` from the scientific field, which for these
classes is a repeat of the common name rather than a binomial. Measured on the
live station on 2026-08-09: 247 such rows — 203 ``Engine``, 25 ``Human vocal``,
18 ``Dog``, 1 ``Gray Wolf`` — every one of them asserting a bird at species
rank. The charter's honesty constraint is not a preference here; the system
was stating something it had no evidence for and did not believe.

**What this command changes, and what it deliberately does not.**

============================  ==========================================
``rank``                      ``"species"`` -> ``NULL``
``taxonomic_group``           ``"bird"`` -> ``"acoustic_event"``
``scientific_name``           ``"Engine"`` -> ``NULL``
``canonical_taxon_id``        ``"sci:engine"`` -> ``NULL``
``common_name``               **untouched** — "Engine" is what was heard
``score``, times, evidence    **untouched**
the row itself                **never deleted**
============================  ==========================================

``common_name`` staying is the point of the exercise rather than an oversight.
The operator's history view saying "Engine, 18:07" is honest and useful — it
is the "that was traffic, not a bird" signal — and removing it would trade one
inaccuracy for a different one. What changes is that the row stops claiming to
be a *taxon*.

**Why the typed columns are rewritten here when ADR-043 says the original claim
is never edited, and ADR-044 marks rather than rewrites.** Both of those
precedents are about a *claim*: which species this was. This is not a
disagreement about the identification — nobody is proposing that "Engine" was
really a wren. ``rank`` and ``taxonomic_group`` are metadata saying what *kind*
of statement the row is, they were set by this pipeline rather than by the
detector, and they are false. Leaving them false has ongoing consequences that
a marker alone cannot fix: ``/api/v1/history``'s species list and
``/api/v1/taxa/activity`` ``GROUP BY`` those columns and would keep reporting
engines among the garden's birds, and ``GET /api/v1/taxa/search`` (ADR-043)
offers ``sci:engine`` as a taxon a reviewer can *correct a real bird into*.

So this follows ADR-044's rule where it binds — nothing is deleted, and the
original values are preserved verbatim and attributably under
``native_result.taxonomy_review``, so the record of what the system used to
believe survives — while declining to leave a knowingly false category
assertion in a column that four consumers aggregate over.

**Human reviews are not skipped here**, unlike
``plausibility_repair.find_implausible_detections``. That precedence rule
(ADR-043: a human's ear outranks a machine) protects a human's *verdict on an
identification*, and the review workflow has no field in which a human could
have expressed an opinion about ``rank`` or ``taxonomic_group``. Skipping
reviewed rows would therefore leave "this engine is a bird" standing on
precisely the rows somebody cared enough to look at. Nothing this command
writes touches ``Review`` or the effective name a correction produces.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import models as orm
from .detectors import birdnet_classes

#: Where the audit block is written inside ``detection.native_result``.
#: Deliberately a different key from ADR-044's ``plausibility_review``: the two
#: repairs answer different questions and a row can legitimately carry both.
REVIEW_KEY = "taxonomy_review"


@dataclass(frozen=True, slots=True)
class TaxonomyFinding:
    """One stored detection recording a sound category as a species."""

    detection_id: uuid.UUID
    common_name: str | None
    detector_label: str | None
    sound_kind: str
    event_start_utc: datetime
    score: float
    original_rank: str | None
    original_taxonomic_group: str
    original_scientific_name: str | None
    original_canonical_taxon_id: str | None

    @property
    def reason(self) -> str:
        return (
            f"{self.common_name!r} is a BirdNET sound category of kind "
            f"{self.sound_kind!r}, not a species; stored as rank="
            f"{self.original_rank!r}, taxonomic_group="
            f"{self.original_taxonomic_group!r}"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "detection_id": str(self.detection_id),
            "common_name": self.common_name,
            "detector_label": self.detector_label,
            "sound_kind": self.sound_kind,
            "event_start_utc": self.event_start_utc.isoformat(),
            "score": round(self.score, 6),
            "original_rank": self.original_rank,
            "original_taxonomic_group": self.original_taxonomic_group,
            "original_scientific_name": self.original_scientific_name,
            "original_canonical_taxon_id": self.original_canonical_taxon_id,
            "corrected_rank": None,
            "corrected_taxonomic_group": birdnet_classes.NON_TAXONOMIC_GROUP,
            "reason": self.reason,
        }


def find_mislabelled_taxonomy(
    session: Session, *, limit: int = 100000
) -> list[TaxonomyFinding]:
    """Read-only: BirdNET rows recording a sound category as a taxon.

    Writes nothing. A row is a finding only if it is one of the eleven
    non-taxonomic classes *and* at least one of the four taxonomic fields is
    still set the wrong way — so a second run after ``--apply`` finds nothing,
    and a row written by the post-ADR-049 detector is never a finding.

    Rows already carrying a ``taxonomy_review`` block are skipped regardless,
    so a repeat run cannot overwrite the first review's record of what the
    original values were.
    """
    rows = (
        session.execute(
            select(orm.Detection)
            .join(orm.Detector, orm.Detection.detector_id == orm.Detector.id)
            .where(orm.Detector.plugin_id == "birdnet-v2.4")
            .order_by(orm.Detection.event_start_utc.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

    findings: list[TaxonomyFinding] = []
    for row in rows:
        kind = birdnet_classes.kind_of_detector_label(row.detector_label)
        if kind is None:
            continue
        native_result = row.native_result or {}
        if native_result.get(REVIEW_KEY):
            continue
        already_correct = (
            row.rank is None
            and row.taxonomic_group == birdnet_classes.NON_TAXONOMIC_GROUP
            and row.scientific_name is None
            and row.canonical_taxon_id is None
        )
        if already_correct:
            continue
        findings.append(
            TaxonomyFinding(
                detection_id=row.id,
                common_name=row.common_name,
                detector_label=row.detector_label,
                sound_kind=kind,
                event_start_utc=row.event_start_utc,
                score=row.score,
                original_rank=row.rank,
                original_taxonomic_group=row.taxonomic_group,
                original_scientific_name=row.scientific_name,
                original_canonical_taxon_id=row.canonical_taxon_id,
            )
        )
    return findings


def apply_taxonomy_correction(session: Session, item: TaxonomyFinding) -> None:
    """Correct one row's taxonomic fields, preserving the originals verbatim.

    Never call this without having shown the operator ``to_dict()`` first: it
    changes stored columns, and the whole point of this repair path is that
    such a change is visible and consented to rather than silent. The row is
    not deleted, its ``common_name``, score, timestamps and evidence are not
    touched, and the previous values are recoverable from
    ``native_result.taxonomy_review`` — running this twice is a no-op because
    that block's presence is itself the skip condition upstream.
    """
    row = session.get(orm.Detection, item.detection_id)
    if row is None:
        return
    native_result = dict(row.native_result or {})
    if native_result.get(REVIEW_KEY):
        return
    native_result[REVIEW_KEY] = {
        "corrected": True,
        "sound_kind": item.sound_kind,
        "original_rank": item.original_rank,
        "original_taxonomic_group": item.original_taxonomic_group,
        "original_scientific_name": item.original_scientific_name,
        "original_canonical_taxon_id": item.original_canonical_taxon_id,
        "corrected_rank": None,
        "corrected_taxonomic_group": birdnet_classes.NON_TAXONOMIC_GROUP,
        "reason": item.reason,
        "reviewed_utc": datetime.now(UTC).isoformat(),
    }
    row.native_result = native_result
    row.rank = None
    row.taxonomic_group = birdnet_classes.NON_TAXONOMIC_GROUP
    row.scientific_name = None
    row.canonical_taxon_id = None
