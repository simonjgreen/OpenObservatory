"""What a refiner is, and the three charter rules it cannot get around.

The rules are ``docs/CHARTER.md`` item 5. This module makes each one a thing
the type system and a raised exception enforce, rather than a paragraph a
future refiner's author is trusted to have read.

Rule 1, "only from new information", is the one that needs the most care,
because it is the easiest to violate sincerely. Re-running a model over the same
clip and writing down whichever answer looks better this time is not refinement,
it is laundering — the record gains confidence without anything having been
learned. It is enforced two ways, deliberately overlapping:

* **A refiner may not be the instrument that made the original claim.**
  :meth:`EvidenceIdentity.check_is_new_information` refuses when the refiner's
  ``(model_id, model_version)`` is the pair already on the detection's own
  detector row. A second opinion from the same model at the same version is not
  a second opinion.
* **The same evidence may not be banked twice.** Every refinement records the
  :attr:`EvidenceIdentity.fingerprint` that produced it — refiner, model,
  weights hash and the *configuration* the refiner ran under. ``store.py``
  refuses to write a second refinement carrying a fingerprint a detection has
  already seen. Change the model, the weights or the settings and the
  fingerprint changes and a new refinement is admissible; change nothing and
  re-running is idempotent.

Rule 2, "preserve the original claim", is enforced in ``store.py``: the original
claim is snapshotted verbatim onto the refinement row, and the detection's own
claim columns are compared before and after every write, with a
:class:`RefinementViolation` raised if any of them moved.

Rule 3, "distinguishable from an original", is the schema: a ``refinement`` row
exists, and ``detection.refined_at`` / ``refinement_version`` /
``refinement_outcome`` say on the event itself that refinement ran, at what
version, with what result. That last part is also what the charter's retention
decision needs — "delete on 'refinement has run', not on age alone".

A fourth constraint is not from item 5 but from the overriding **honesty**
constraint, and it is why :attr:`Refiner.authority` exists: a refiner is only
allowed to change a record automatically if its accuracy has been established on
*this station's own audio*. BatDetect2's has not (see :mod:`.batdetect2`), so it
proposes and a human disposes.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class RefinementViolation(ValueError):
    """A refinement was attempted that charter item 5 does not permit.

    Deliberately the same shape as :class:`~open_observatory.normaliser.ClaimViolation`:
    a loud, immediate failure at the boundary rather than a log line and a
    slightly wrong permanent record.
    """


class RefinementBasis(StrEnum):
    """What new information justified looking at this record again.

    There is no ``same_model_again`` member, and that is the point: the enum is
    a closed set of things that can legitimately count as new, so "I re-read the
    score" has no name to travel under.
    """

    #: A classifier the original record never saw. The BatDetect2 cascade.
    NEW_MODEL = "new_model"
    #: The same model, but a prior it is conditioned on has been corrected —
    #: e.g. a range model reloaded after the coordinates were fixed (ADR-032's
    #: territory).
    CORRECTED_PRIOR = "corrected_prior"
    #: A person listened. Ranks above every model here, and is the only basis
    #: that may ever carry ``apply`` authority.
    HUMAN_EAR = "human_ear"


class RefinementOutcome(StrEnum):
    """What the refiner concluded. Persisted on the detection so retention can read it."""

    #: The refiner ran and offers a different identification, for a human to
    #: accept or reject. The only outcome a ``propose``-authority refiner may
    #: produce that carries a claim.
    PROPOSED = "proposed"
    #: The refiner ran, found nothing it could improve, and said so. This is a
    #: successful refinement pass, not a failure: it is exactly the state the
    #: charter's retention decision wants to be able to see before deleting.
    NO_CHANGE = "no_change"
    #: The refiner ran and agrees with the original claim, having derived it
    #: independently. Strictly better evidence than ``no_change``.
    CONFIRMED = "confirmed"
    #: The refiner could not run on this item — clip already reclaimed, file
    #: missing, unreadable audio. Never conflated with ``no_change``: the whole
    #: risk the charter names is data the refiner never actually saw.
    UNAVAILABLE = "unavailable"
    #: The refiner raised. Recorded rather than swallowed.
    FAILED = "failed"
    #: Reserved. No shipped refiner has ``apply`` authority, so nothing can
    #: currently produce this; ``store.record_refinement`` raises if a
    #: ``propose``-authority refiner tries.
    APPLIED = "applied"


#: Outcomes that mean "the refiner genuinely examined this item". Retention's
#: future "delete on refinement has run" guard should require one of these —
#: ``unavailable`` and ``failed`` explicitly do not qualify, because they are
#: the failure the charter's safeguard exists to catch.
EXAMINED_OUTCOMES = frozenset(
    {
        RefinementOutcome.PROPOSED,
        RefinementOutcome.NO_CHANGE,
        RefinementOutcome.CONFIRMED,
        RefinementOutcome.APPLIED,
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceIdentity:
    """Exactly what produced a refinement, hashed so it can be compared.

    The configuration is part of the identity on purpose. A refiner re-run with
    a lower confidence floor is running with different information about what
    counts as a call, and its answer is admissible; a refiner re-run with
    nothing changed is not. Without ``config`` in the hash, the only way to get
    a second answer out of the same model would be to bump a version string,
    which is precisely the kind of quiet, sincere workaround this project keeps
    finding after the fact.
    """

    refiner_id: str
    refiner_version: str
    model_id: str
    model_version: str
    model_sha256: str | None = None
    #: Everything that changes what the refiner would conclude. Must be
    #: JSON-serialisable and stable in ordering (it is sorted before hashing).
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """SHA-256 over the whole identity. Stable across processes and runs."""
        payload = json.dumps(
            {
                "refiner_id": self.refiner_id,
                "refiner_version": self.refiner_version,
                "model_id": self.model_id,
                "model_version": self.model_version,
                "model_sha256": self.model_sha256,
                "config": self.config,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def version_label(self) -> str:
        """Human-readable "at what version" for ``detection.refinement_version``."""
        return f"{self.refiner_id}@{self.refiner_version}/{self.model_id}@{self.model_version}"

    def check_is_new_information(
        self, *, original_model_id: str, original_model_version: str
    ) -> None:
        """Charter item 5, rule 1, first half. Raises rather than returning a bool.

        A refiner that *is* the original instrument, at the original version,
        has nothing new to say about the record — anything it produces is a
        re-reading of the same score. Note that this compares the model, not the
        plugin: two plugin wrappers around one set of weights are one opinion.
        """
        if (self.model_id, self.model_version) == (original_model_id, original_model_version):
            raise RefinementViolation(
                f"refiner {self.refiner_id!r} runs {original_model_id}@{original_model_version}, "
                "which is the model that made the original claim: a second reading of the same "
                "model at the same version is not new information (charter item 5, rule 1)"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "refiner_id": self.refiner_id,
            "refiner_version": self.refiner_version,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_sha256": self.model_sha256,
            "config": dict(self.config),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class RefinementCandidate:
    """One stored event, with the evidence still on disk, offered to a refiner.

    Everything a refiner needs to do its job and nothing it could use to reach
    back into the live station: this is a read of the database and a path on
    disk, in a process that has never opened the microphone.
    """

    detection_id: uuid.UUID
    event_start_utc: datetime
    taxonomic_group: str
    common_name: str | None
    scientific_name: str | None
    score: float
    peak_frequency_hz: float | None
    #: The detector that made the original claim — the other half of rule 1.
    detector_plugin_id: str
    detector_model_id: str
    detector_model_version: str
    native_result: dict[str, Any]
    #: The full-rate evidence clip. ``None`` when retention has already
    #: reclaimed it, which is an ``unavailable`` outcome, never a ``no_change``.
    clip_path: Path | None
    clip_sample_rate: int | None
    media_asset_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class RefinementProposal:
    """What a refiner concluded about one candidate.

    A proposal carries no authority of its own. ``store.record_refinement``
    decides what may be written, from the refiner's :attr:`Refiner.authority`
    and this outcome.
    """

    outcome: RefinementOutcome
    basis: RefinementBasis
    #: Free text an operator will actually read, in the manner of
    #: ``plausibility_repair``'s ``reason``. Says what was found *and* what is
    #: uncertain about it — never just the winning label.
    reason: str
    proposed_common_name: str | None = None
    proposed_scientific_name: str | None = None
    proposed_rank: str | None = None
    proposed_taxonomic_group: str | None = None
    #: The refiner's own number. **Not** a probability and never presented as
    #: one — the honesty constraint applies to a refiner exactly as it applies
    #: to a detector.
    proposed_score: float | None = None
    #: Everything the refiner saw, kept verbatim, the way ``native_result`` is
    #: kept for a detection. Includes the measurements a human needs to check
    #: the proposal against physics — see :mod:`.batdetect2`.
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def carries_a_claim(self) -> bool:
        return self.proposed_common_name is not None or self.proposed_scientific_name is not None


@runtime_checkable
class Refiner(Protocol):
    """A source of new information about already-stored events.

    Deliberately *not* :class:`~open_observatory.detectors.base.DetectorPlugin`.
    A detector consumes an :class:`~open_observatory.audio.contracts.AudioWindow`
    cut from a live stream and is driven by a worker that drops anything older
    than ``max_delivery_latency_s``; a refiner consumes a clip written hours ago,
    which that worker would correctly reject as stale. Reusing
    ``DeferredDetectorWorker`` here would have meant disabling the one safety
    property it exists to provide. See ADR-045.
    """

    identity: EvidenceIdentity
    #: ``"propose"`` — may only offer a change for human review.
    #: ``"apply"`` — may change the record automatically. Reserved: no shipped
    #: refiner has it, and ``store.py`` enforces the difference.
    authority: str
    #: Only candidates whose ``taxonomic_group`` is in here are offered.
    handles_groups: frozenset[str]

    def prepare(self) -> None:
        """Load models and assets. Raises :class:`RefinerUnavailable` if absent."""

    def refine(self, candidate: RefinementCandidate) -> RefinementProposal:
        """Examine one candidate. Must not raise for ordinary "found nothing"."""

    def close(self) -> None: ...


class RefinerUnavailable(RuntimeError):
    """Assets or libraries a refiner needs are not installed.

    Expected, not a bug: ADR-006 forbids bundling third-party model binaries and
    BatDetect2's whole repository is CC-BY-NC-4.0, so a fresh checkout
    legitimately cannot run this. Mirrors
    :class:`~open_observatory.detectors.base.DetectorUnavailable`.
    """
