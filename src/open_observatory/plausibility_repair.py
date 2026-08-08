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

Flagging is not the same as hiding. This module only marks a row reviewed; making
any consumer (the API, the MQTT publisher, the ESP32 wall display) actually stop
presenting a flagged row as an observation is separate work, not done here -- see
the module docstring notes on ``apply_plausibility_flag`` below.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import models as orm
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
            "reason": self.reason,
        }


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

    Skips any row already carrying a `plausibility_review`: a repeat run must
    not silently re-flag, or overwrite the first review of, a row an operator
    has already seen.
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

    findings: list[PlausibilityFinding] = []
    for row in rows:
        native_result = row.native_result or {}
        if native_result.get("plausibility_review"):
            continue
        index = label_index.get(row.detector_label or "")
        if index is None:
            # Label list changed since capture (model version bump); nothing
            # honest to recompute against, so leave the row alone.
            continue
        week = native_result.get("week")
        if week is None:
            continue
        prior = range_model.probabilities(int(week))
        raw = float(prior[index])
        occurrence = None if math.isnan(raw) else raw
        band, threshold = band_for(
            occurrence,
            range_model_loaded=True,
            plausibility_floor=plausibility_floor,
            common_prior=common_prior,
            range_threshold=range_threshold,
            threshold_in_range=threshold_in_range,
            threshold_uncommon=threshold_uncommon,
            threshold_out_of_range=threshold_out_of_range,
        )
        if row.score >= threshold:
            continue  # still admissible under current logic
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
                stored_band=native_result.get("plausibility_band"),
                recomputed_occurrence_probability=occurrence,
                recomputed_band=band,
                recomputed_threshold=threshold,
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

    This flags only; it does not hide the row from any consumer. `GET
    /api/v1/detections`, the MQTT publisher and the ESP32 wall display firmware
    would each need to check `native_result.plausibility_review.implausible` and
    exclude or mark such rows to actually stop presenting a flagged historical
    record as an observation -- none of them do that yet. That follow-up is
    tracked in `HANDOVER.md` section 6.3 item 0.
    """
    row = session.get(orm.Detection, item.detection_id)
    if row is None:
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
