"""Historical repair for BirdNET's plausibility bands (ADR-032).

``oo detections reconcile-plausibility`` (``cli.py``) re-evaluates already-stored
BirdNET detections against the *current* range model, plausibility floor and band
thresholds, and finds species that would not be admitted under today's logic --
principally the two defects fixed in ``detectors/birdnet.py``: a near-zero prior
that used to be overruled by an uncalibrated score, and a missing prior that used
to get the easiest bar instead of the strictest one. See
``docs/detectors/DETECTOR_STRATEGY.md``'s "Known limitation" section and
``HANDOVER.md`` section 6.3 item 0.

It never deletes a row or overwrites ``native_result`` in place -- the original
detector output is kept verbatim, and the finding is recorded under a new
``native_result.plausibility_review`` key, exactly as
``history.apply_stream_reconciliation`` preserves the original stream claim under
``detail.reconciliation``. Dry-run by default, like every other repair command in
this codebase.

Flagging is not the same as hiding, and this module still only flags. What a flag
*means* to everything downstream is defined in ``plausibility.py`` and was
implemented in ADR-044: the API keeps a flagged row and marks it ``withdrawn``,
species tallies exclude it and report how many they excluded, and the MQTT
publisher and the ESP32 counter-top display do not present it at all. So running this
command with ``--apply`` now has visible consequences on every surface, which it
did not when ADR-032 shipped it.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import review as review_queries
from .db import models as orm
from .detectors import birdnet_classes
from .detectors.birdnet import band_for, load_range_model_for_repair

BIRDNET_PLUGIN_ID = "birdnet-v2.4"


def _iso(value: datetime) -> str:
    return value.isoformat()


@dataclass(frozen=True, slots=True)
class PlausibilityFinding:
    """One stored detection that would not be admitted under current logic."""

    detection_id: uuid.UUID
    common_name: str | None
    scientific_name: str | None
    event_start_utc: datetime
    score: float
    stored_occurrence_probability: float | None
    stored_band: str | None
    recomputed_occurrence_probability: float | None
    recomputed_band: str
    recomputed_threshold: float
    #: The bar this row was actually admitted under, as recorded on the row at
    #: capture time (``native_result.threshold_applied``), or ``None`` when the
    #: row does not say. ``None`` is the row class this command cannot judge
    #: safely -- see the "Threshold changes" note on
    #: :func:`find_implausible_detections` -- so it is surfaced rather than
    #: hidden, and an operator reviewing a dry run can see at a glance which
    #: findings rest on an assumption.
    admitting_threshold: float | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "detection_id": str(self.detection_id),
            "common_name": self.common_name,
            "scientific_name": self.scientific_name,
            "event_start_utc": _iso(self.event_start_utc),
            "score": round(self.score, 6),
            "stored_occurrence_probability": self.stored_occurrence_probability,
            "stored_band": self.stored_band,
            "recomputed_occurrence_probability": self.recomputed_occurrence_probability,
            "recomputed_band": self.recomputed_band,
            "recomputed_threshold": (
                None if math.isinf(self.recomputed_threshold) else self.recomputed_threshold
            ),
            "admitting_threshold": self.admitting_threshold,
            "reason": self.reason,
        }


#: Where ``detectors/birdnet.py`` records, on every detection it writes, the
#: exact confidence bar that row had to clear to be stored at all. Written
#: since the first BirdNET implementation (2026-08-04, commit db30257) and,
#: until ADR-070, never read by anything. It is the only thing that makes this
#: repair pass safe across a threshold change.
ADMITTING_THRESHOLD_KEY = "threshold_applied"


def _admitting_threshold(native_result: dict[str, object]) -> float | None:
    """The bar this row was admitted under, or ``None`` if the row does not say.

    ``None`` covers three real cases and they are deliberately not
    distinguished, because none of them is a bar this function can trust: the
    key is absent (a row from a detector or a version that never wrote it), it
    is not a number, or it is not finite. All three mean "unknown", and the
    caller must treat unknown as "cannot judge a threshold change here".
    """
    raw = native_result.get(ADMITTING_THRESHOLD_KEY)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    return None if math.isinf(value) or math.isnan(value) else value


def find_implausible_detections(
    session: Session,
    *,
    model_dir: Path,
    latitude: float,
    longitude: float,
    plausibility_floor: float = 0.0005,
    common_prior: float = 0.15,
    range_threshold: float = 0.03,
    threshold_in_range: float = 0.55,
    threshold_uncommon: float = 0.75,
    threshold_out_of_range: float = 0.90,
    limit: int = 5000,
) -> list[PlausibilityFinding]:
    """Read-only: recomputes bands, writes nothing. See `apply_plausibility_flag`.

    Loads the range model fresh (`load_range_model_for_repair`) rather than
    trusting `native_result.occurrence_probability`, specifically so a row
    stored with `occurrence=None` -- because the range model was off, or
    silent about that species, at capture time -- gets a real recomputed
    value now rather than being permanently unreviewable.

    Exempts BirdNET's eleven non-taxonomic classes from the *occurrence
    prior* (ADR-049): the range model has no meaningful prior for "Engine" or
    "Dog", so applying the floor to them withdraws correct detections rather
    than wrong ones. They are not skipped -- `band_for` sorts them into the
    `non_biological` band and judges them on score alone, at the ordinary
    in-range bar, so they can still appear in the findings if that bar has
    been raised since they were stored. (Earlier revisions of this docstring
    said "skips ... entirely", which was never what the code did and made a
    dry run showing thousands of `non_biological` findings look like a
    contradiction. It is not one: exempt from the prior, still subject to a
    score bar.)

    Judges each row by the bar it was *admitted* under wherever the record
    says what that was -- see the "Threshold changes" note below.

    Skips any row already carrying a `plausibility_review`: a repeat run must
    not silently re-flag, or overwrite the first review of, a row an operator
    has already seen.

    Also skips any row with a *human* review of any kind -- confirmed,
    rejected, corrected or held (`review.reviewed_detection_ids`) -- per the
    charter's priority-5 precedence rule: a human's ear is the highest-quality
    information this system ever holds about an event, and a machine
    refinement must never second-guess or overwrite it (ADR-043). This repair
    pass exists for the unreviewed backlog; once a human has looked at a
    detection, this function has nothing useful left to say about it.

    Threshold changes (ADR-070)
    ---------------------------
    The band thresholds are operator-tunable settings, so the table can hold
    rows written under several different bars, and *no single current
    threshold is correct for the whole table*. Re-judging every stored row
    against today's numbers is therefore wrong in both directions: it
    withdraws rows that were correctly admitted under the bar in force when
    they were written, and it is silent about rows admitted under a bar
    looser than any this command was told about.

    The record already contains the answer. ``detectors/birdnet.py`` stamps
    ``native_result.threshold_applied`` on every row it writes, so each row
    carries the bar it personally had to clear. This function uses it: a row
    is **not** flagged when its recomputed band is the same band it was stored
    in and its score cleared that row's own recorded bar. What is left is what
    this repair actually exists for -- a row whose *band* is now different
    (ADR-032 defect (b): a missing prior that used to get the easiest bar), or
    one the plausibility floor now rules out at any score (defect (a)), which
    is a correctness verdict rather than a tuning preference and is not
    subject to this exemption.

    The limitation that remains, stated plainly: a row that does not carry
    ``threshold_applied`` -- or carries it as something other than a finite
    number -- cannot be judged this way, and is still measured against the
    *currently configured* threshold for its band. If that bar has been raised
    since such a row was written, the row will be reported as implausible when
    the only thing that changed is the operator's preference. Those rows are
    identifiable in the output: their ``admitting_threshold`` is ``null``.
    On this station the key has been written since the first BirdNET commit,
    which predates the oldest detection in the database, so the class is
    expected to be empty -- but "expected to be empty" is not "impossible",
    and the honest position is that for such a row this command cannot tell a
    defect from a retune.

    The floor (``plausibility_floor``) is deliberately *not* given the same
    exemption. ADR-032's whole claim is that a near-zero prior is not a higher
    bar but a statement that no score is admissible; applying today's floor to
    history is the intended operation of this command, not an accident of
    configuration drift.
    """
    labels, _parsed, range_model = load_range_model_for_repair(model_dir, latitude, longitude)
    label_index = {label: index for index, label in enumerate(labels)}

    rows = (
        session.execute(
            select(orm.Detection)
            .join(orm.Detector, orm.Detection.detector_id == orm.Detector.id)
            .where(orm.Detector.plugin_id == BIRDNET_PLUGIN_ID)
            .where(orm.Detection.common_name.is_not(None))
            .order_by(orm.Detection.event_start_utc.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    human_reviewed = review_queries.reviewed_detection_ids(session, (row.id for row in rows))

    findings: list[PlausibilityFinding] = []
    for row in rows:
        if row.id in human_reviewed:
            continue
        native_result = row.native_result or {}
        if native_result.get("plausibility_review"):
            continue
        stored_band = native_result.get("plausibility_band")
        index = label_index.get(row.detector_label or "")
        if index is None:
            # Label list changed since capture (model version bump); nothing
            # honest to recompute against, so leave the row alone.
            continue
        week = native_result.get("week")
        if week is None:
            continue
        # ADR-049. A sound category has no meaningful occurrence prior, so the
        # floor must not be applied to it. Without this, the first dry run
        # against the live station's 67,679 rows proposed to withdraw 62
        # "Engine", 24 "Human vocal" and 5 "Dog" detections -- 91 of 114
        # findings -- every one of which was very probably correct. The prior
        # is still recomputed below for the species rows; for these it is not
        # even consulted.
        non_taxonomic = birdnet_classes.kind_of_detector_label(row.detector_label) is not None
        occurrence: float | None = None
        if not non_taxonomic:
            prior = range_model.probabilities(int(week))
            raw = float(prior[index])
            occurrence = None if math.isnan(raw) else raw
        band, threshold = band_for(
            occurrence,
            range_model_loaded=True,
            non_taxonomic=non_taxonomic,
            plausibility_floor=plausibility_floor,
            common_prior=common_prior,
            range_threshold=range_threshold,
            threshold_in_range=threshold_in_range,
            threshold_uncommon=threshold_uncommon,
            threshold_out_of_range=threshold_out_of_range,
        )
        if row.score >= threshold:
            continue  # still admissible under current logic
        admitting_threshold = _admitting_threshold(native_result)
        if (
            not math.isinf(threshold)
            and stored_band is not None
            and stored_band == band
            and admitting_threshold is not None
            and row.score >= admitting_threshold
        ):
            # Same band, and the row cleared the bar that band actually had
            # when the row was written. Nothing about this row's *plausibility*
            # has been found wrong; the operator has since retuned that band's
            # score bar, and a tuning change is a statement about what to admit
            # next, not a discovery that the past was mis-decided. Withdrawing
            # here would rewrite the record to match a preference. See the
            # "Threshold changes" note in this function's docstring.
            continue
        if math.isinf(threshold):
            reason = (
                f"occurrence {occurrence!r} is at or below the plausibility floor "
                f"({plausibility_floor}); no score is admissible"
            )
        else:
            reason = (
                f"recomputed band {band!r} needs score >= {threshold:.3f} under the "
                f"current range model and floor; stored score is {row.score:.3f}"
            )
        findings.append(
            PlausibilityFinding(
                detection_id=row.id,
                common_name=row.common_name,
                scientific_name=row.scientific_name,
                event_start_utc=row.event_start_utc,
                score=row.score,
                stored_occurrence_probability=native_result.get("occurrence_probability"),
                stored_band=stored_band if isinstance(stored_band, str) else None,
                recomputed_occurrence_probability=occurrence,
                recomputed_band=band,
                recomputed_threshold=threshold,
                admitting_threshold=admitting_threshold,
                reason=reason,
            )
        )
    return findings


def apply_plausibility_flag(session: Session, item: PlausibilityFinding) -> None:
    """Flag one detection row as implausible under current logic.

    Never call this without having shown the operator `PlausibilityFinding.to_dict()`
    first and had it confirmed -- this rewrites the operator's historical record,
    and the whole point of this repair path is that such a rewrite is visible and
    consented to, not silent. Mirrors `history.apply_stream_reconciliation`'s
    audit-preserving shape: the original `native_result` is copied, not mutated in
    place, and nothing under its existing keys is overwritten.

    Since ADR-044 the consumers do read this. `GET /api/v1/detections` keeps the
    row and marks it `withdrawn`; `/api/v1/history`'s species list and
    `/api/v1/taxa/activity` drop it and report `excluded_withdrawn_count`; the
    MQTT publisher and the `/api/v1/display` counter-top display channel do not present
    it at all. `plausibility.is_withdrawn` is the single definition they share.
    A row is withdrawn the moment this function commits, with no restart and no
    further step, which is exactly why the confirmation above is not a formality.

    Defensive re-check of the same human-review precedence rule
    `find_implausible_detections` already applies: if a human reviewed this
    detection between the finding being computed and this call being made
    (the CLI's confirm step is interactive, so real time passes), do nothing
    rather than flag over a human's ear -- see ADR-043.
    """
    row = session.get(orm.Detection, item.detection_id)
    if row is None:
        return
    if review_queries.latest_review(session, row.id) is not None:
        return
    native_result = dict(row.native_result or {})
    native_result["plausibility_review"] = {
        "implausible": True,
        "recomputed_band": item.recomputed_band,
        "recomputed_occurrence_probability": item.recomputed_occurrence_probability,
        "recomputed_threshold": (
            None if math.isinf(item.recomputed_threshold) else item.recomputed_threshold
        ),
        "reason": item.reason,
        "reviewed_utc": _iso(datetime.now(UTC)),
    }
    row.native_result = native_result
