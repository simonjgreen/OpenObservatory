"""Reconcile ``media_asset`` rows that claim a clip the filesystem does not have (ADR-057).

``oo clips reconcile-missing`` (``cli.py``) finds rows with ``reclaimed_at IS
NULL`` -- the database's way of saying "this evidence exists" -- whose
``storage_uri`` names a file that is not there, and marks them
``reclaimed_at`` / ``reclaim_reason="missing"``.

**What went wrong on the live station.** Measured 2026-08-10: 8,067 of 48,941
live rows (16.5%), 20.59 GB, every one of them created before
``2026-08-05T18:44:35Z`` and none after. The cause is
``ClipManager.enforce_retention()`` (``clips.py``), a pre-ADR-026 filesystem
sweep that unlinks the oldest clips by mtime until the tree fits
``max_total_bytes`` and **never touches the database**. Its own logs account
for the whole loss: 8,166 files and 20.84 GB deleted as ``over_budget``
between 2026-08-05 and 2026-08-08, against 8,067 rows and 20.59 GB missing
here (the ~99-file difference is untracked ``.partial`` files and clips with
no row). ADR-021 is the reason the loss *stopped* -- moving to the SSD raised
the budget from 20 GB to 300 GB -- not the reason it started.

**Why ``"missing"`` is a distinct reason, not just "reclaimed".** Setting
``reclaim_reason`` to a tier name would record that a policy decided to give
this clip up, which is not what happened. The charter's "withdraw, not delete"
instinct applies to the system's account of itself: an operator, and the
refinement runner (ADR-045), should be able to tell evidence deliberately aged
out from evidence that vanished. `MISSING_REASON` is that distinction, and the
``detail.missing_reconciliation`` block records what the row claimed before it
was reconciled -- the byte count it was contributing to the storage panel, and
when the file was observed absent.

**This module never deletes anything.** Not a file, not a row, not a
``detection``. It only ever sets two columns and adds one JSON key, on rows
whose file it has just confirmed is already gone.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import models as orm

#: ``media_asset.reclaim_reason`` for a row whose file went missing without a
#: retention decision. Deliberately not one of `retention.py`'s tier names.
#: ``reclaim_reason`` is ``String(32)``; this fits, so no migration is needed.
MISSING_REASON = "missing"

#: Key under ``media_asset.detail`` holding the audit record, in the same
#: shape as ``detail.reconciliation`` (ADR-024) and
#: ``native_result.plausibility_review`` (ADR-032). Its presence is also the
#: idempotency check: a reconciled row is never a finding again.
DETAIL_KEY = "missing_reconciliation"


@dataclass(frozen=True, slots=True)
class MissingAssetFinding:
    """One live ``media_asset`` row whose file is not on disk."""

    asset_id: uuid.UUID
    detection_id: uuid.UUID | None
    kind: str
    storage_uri: str
    byte_length: int
    created_at: datetime
    common_name: str | None
    taxonomic_group: str | None
    event_start_utc: datetime | None
    held_for_review: bool

    @property
    def reason(self) -> str:
        return (
            f"{self.kind} row claims {self.byte_length:,} bytes at "
            f"{self.storage_uri}, which does not exist"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "asset_id": str(self.asset_id),
            "detection_id": str(self.detection_id) if self.detection_id else None,
            "kind": self.kind,
            "storage_uri": self.storage_uri,
            "byte_length": self.byte_length,
            "created_at": self.created_at.isoformat(),
            "common_name": self.common_name,
            "taxonomic_group": self.taxonomic_group,
            "event_start_utc": (
                self.event_start_utc.isoformat() if self.event_start_utc else None
            ),
            "held_for_review": self.held_for_review,
            "reclaim_reason": MISSING_REASON,
            "reason": self.reason,
        }


@dataclass(slots=True)
class MissingAssetReport:
    """Aggregate of one ``find_missing_assets`` pass, for `--json` and the CLI table."""

    scanned: int = 0
    missing: int = 0
    missing_bytes: int = 0
    truncated: bool = False
    findings: list[MissingAssetFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        findings = self.findings
        by_kind: dict[str, int] = {}
        by_day: dict[str, int] = {}
        held = 0
        for item in findings:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
            by_day[item.created_at.date().isoformat()] = (
                by_day.get(item.created_at.date().isoformat(), 0) + 1
            )
            if item.held_for_review:
                held += 1
        return {
            "scanned": self.scanned,
            "missing": self.missing,
            "missing_bytes": self.missing_bytes,
            "truncated": self.truncated,
            "held_for_review": held,
            "by_kind": by_kind,
            "by_created_day": dict(sorted(by_day.items())),
            "findings": [item.to_dict() for item in findings],
        }


def find_missing_assets(
    session: Session,
    *,
    limit: int = 200_000,
    exists: Callable[[str], bool] | None = None,
) -> MissingAssetReport:
    """Read-only: live ``media_asset`` rows whose file is not on disk.

    Writes nothing. One ``stat`` per live row, oldest first -- deliberately a
    manual command and never part of the housekeeping sweep, for exactly the
    reason `RetentionSweeper.find_orphans` is (ADR-021, ADR-033: sustained
    disk I/O next to the ALSA read is the documented cause of two capture
    incidents on this station). Measured on the live station, 48,989 rows cost
    0.27 s of ``os.path.exists`` -- cheap for a command an operator runs,
    still far too much to do on a 300 s loop, which is why the recurring check
    is `RetentionSweeper.audit_missing_files`'s bounded rolling sample instead.

    Rows already carrying a ``detail.missing_reconciliation`` block are never
    findings, so a second run after ``--apply`` reports nothing and cannot
    overwrite the first run's record of what the row claimed.

    ``exists`` is injectable purely so tests can drive this without a
    filesystem; production always uses ``Path.exists``.
    """
    from . import review as review_queries

    check: Callable[[str], bool] = exists or (lambda path: Path(path).exists())
    held_ids = review_queries.held_detection_ids(session)

    rows = session.execute(
        select(orm.MediaAsset, orm.Detection)
        .outerjoin(orm.DetectionMedia, orm.DetectionMedia.media_asset_id == orm.MediaAsset.id)
        .outerjoin(orm.Detection, orm.Detection.id == orm.DetectionMedia.detection_id)
        .where(orm.MediaAsset.reclaimed_at.is_(None))
        .order_by(orm.MediaAsset.created_at.asc())
        .limit(limit)
    ).all()

    report = MissingAssetReport()
    report.truncated = len(rows) >= limit
    seen: set[uuid.UUID] = set()
    for asset, detection in rows:
        if asset.id in seen:
            continue
        seen.add(asset.id)
        report.scanned += 1
        if (asset.detail or {}).get(DETAIL_KEY):
            continue
        if check(asset.storage_uri):
            continue
        report.missing += 1
        report.missing_bytes += int(asset.byte_length or 0)
        report.findings.append(
            MissingAssetFinding(
                asset_id=asset.id,
                detection_id=detection.id if detection is not None else None,
                kind=asset.kind,
                storage_uri=asset.storage_uri,
                byte_length=int(asset.byte_length or 0),
                created_at=asset.created_at,
                common_name=detection.common_name if detection is not None else None,
                taxonomic_group=detection.taxonomic_group if detection is not None else None,
                event_start_utc=detection.event_start_utc if detection is not None else None,
                held_for_review=detection is not None and detection.id in held_ids,
            )
        )
    return report


def apply_missing_reconciliation(
    session: Session,
    item: MissingAssetFinding,
    *,
    now: datetime | None = None,
) -> None:
    """Mark one row's evidence as missing, preserving what it used to claim.

    Sets ``reclaimed_at`` and ``reclaim_reason=MISSING_REASON`` so every
    consumer that filters on ``reclaimed_at IS NULL`` -- the storage panel's
    tier sums, the retention sweeper's candidate queries, the refinement
    runner's ``find_candidates`` -- stops counting bytes that are not there.
    The row itself, its detection, and the association between them all
    survive: "this happened, and we no longer have the audio" is a true
    statement and the one the record should be making.

    Never deletes a file (there is nothing to delete) and never deletes a row.
    Running it twice is a no-op: the ``detail`` block's presence is the skip
    condition in `find_missing_assets`, and it is not overwritten here.
    """
    row = session.get(orm.MediaAsset, item.asset_id)
    if row is None:
        return
    detail = dict(row.detail or {})
    if detail.get(DETAIL_KEY):
        return
    stamp = now or datetime.now(UTC)
    detail[DETAIL_KEY] = {
        "reconciled": True,
        "reconciled_utc": stamp.isoformat(),
        # What the row was asserting before this ran, so the storage panel's
        # historical over-report stays explicable rather than just disappearing.
        "claimed_storage_uri": item.storage_uri,
        "claimed_byte_length": item.byte_length,
        "file_present": False,
        "reason": item.reason,
        # Said in the row, not only in an ADR: this is not a retention decision.
        "note": (
            "file absent without a retention decision; marked reclaimed for "
            "accounting only, reason 'missing' rather than a tier name (ADR-057)"
        ),
    }
    row.detail = detail
    row.reclaimed_at = stamp
    row.reclaim_reason = MISSING_REASON
