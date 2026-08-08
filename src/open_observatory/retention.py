"""NVR-style tiered clip retention.

The operator's decision (recorded in ADR-026) is precise, so this module
implements exactly it and nothing more:

* **Detection metadata is kept forever.** Species, timestamps, scores, peak
  frequency, capture coverage -- nothing here ever deletes a ``detection`` row
  or mutates its columns. It is the valuable, and cheap, part.
* **Clip bytes degrade by age**, in tiers:

  - 0-7 days: native (full-rate) clip and its audible rendering both survive.
  - 7-30 days: native clip deleted; audible rendering survives.
  - 30-90 days: only the first-ever and best-of-species clips survive; every
    other detection in this age band loses its remaining clip(s) too.
  - 90+ days: deleted, including the exemplars.
  - **Always**, independent of the above: once the clip filesystem exceeds a
    configurable watermark, the oldest surviving clips are reclaimed first,
    regardless of tier or exemplar status. This is the safety valve -- disk
    space always wins over any retention preference.

Deleting a clip never deletes its ``media_asset`` row: the row is marked
``reclaimed_at``/``reclaim_reason`` instead, so `/api/v1/media/{id}` can keep
answering (with 410, already handled) and a history view can keep showing
that the detection happened, just without audio.

Every deletion is bounded and yields to the caller: this runs in the
capture-isolated single-thread executor (see `station.py`), and disk I/O
sustained long enough to matter is exactly the class of bug that has twice
caused ALSA overruns on this project (`docs/architecture/ADRS.md` ADR-021,
`docs/delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md`). A `sweep()` call never
walks the clip directory tree; it only ever touches rows a batch query
returned and files those rows name.
"""

from __future__ import annotations

import shutil
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import models as orm

log = structlog.get_logger(__name__)

#: Media kinds that are the authoritative, full-rate recording. Everything
#: else (``playback``, ``audible_ultrasonic``) is a human/browser-audible
#: derivative and is what the 7-30 day tier is trying to keep.
NATIVE_KINDS = frozenset({"evidence_native"})

#: Detections in this taxonomic group have no species identity to rank by
#: score -- `ultrasonic-pass-v1` detects passes, not species (see the
#: honesty rules in `normaliser.py` and CLAUDE.md) -- so "best" for this
#: group is defined on the detector's own physical measurements instead.
BAT_GROUP = "bat"


@dataclass(frozen=True, slots=True)
class RetentionDecision:
    """One deletion (or would-delete) decision, for logs and dry-run output."""

    asset_id: uuid.UUID
    detection_id: uuid.UUID | None
    path: str
    kind: str
    tier: str
    reason: str
    bytes: int
    existed_on_disk: bool


@dataclass(slots=True)
class RetentionReport:
    """The result of one `RetentionSweeper.sweep()` call."""

    dry_run: bool = False
    started_at: datetime | None = None
    duration_s: float = 0.0
    disk_used_ratio_before: float | None = None
    disk_used_ratio_after: float | None = None
    exemplar_detections: int = 0
    already_missing: int = 0
    #: Per-tier counts/bytes: keys are "native", "exemplar_only", "expired", "watermark".
    tier_counts: dict[str, int] = field(default_factory=dict)
    tier_bytes: dict[str, int] = field(default_factory=dict)
    decisions: list[RetentionDecision] = field(default_factory=list)
    #: False when the batch budget (count or wall-clock) was exhausted with
    #: candidate work still outstanding -- the next sweep will pick it up.
    complete: bool = True

    @property
    def total_deleted(self) -> int:
        return sum(self.tier_counts.values())

    @property
    def total_bytes(self) -> int:
        return sum(self.tier_bytes.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "duration_s": self.duration_s,
            "disk_used_ratio_before": self.disk_used_ratio_before,
            "disk_used_ratio_after": self.disk_used_ratio_after,
            "exemplar_detections": self.exemplar_detections,
            "already_missing": self.already_missing,
            "tier_counts": dict(self.tier_counts),
            "tier_bytes": dict(self.tier_bytes),
            "total_deleted": self.total_deleted,
            "total_bytes": self.total_bytes,
            "complete": self.complete,
        }


SessionFactory = Callable[[], AbstractContextManager[Session]]


class RetentionSweeper:
    """Ages evidence clips off disk in tiers; detection metadata is never touched.

    Every query here is either a small bounded ``LIMIT`` batch or a read over
    the (small, "kilobytes per day" per the operator) detection metadata
    table -- never a filesystem walk. Call `sweep()` repeatedly (housekeeping
    calls it once per tick); a large backlog drains gradually rather than in
    one long stall.
    """

    def __init__(
        self,
        *,
        clip_dir: Path,
        session_factory: SessionFactory,
        native_days: int = 7,
        audible_only_days: int = 30,
        exemplar_only_days: int = 90,
        watermark_ratio: float = 0.85,
        batch_size: int = 200,
        batch_budget_s: float = 1.5,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.clip_dir = Path(clip_dir)
        self._session_factory = session_factory
        self.native_days = native_days
        self.audible_only_days = audible_only_days
        self.exemplar_only_days = exemplar_only_days
        self.watermark_ratio = watermark_ratio
        self.batch_size = batch_size
        self.batch_budget_s = batch_budget_s
        self._clock = clock or (lambda: datetime.now(UTC))

        #: Cumulative across the process lifetime, for Prometheus counters.
        self.totals: dict[str, int] = {}
        self.last_sweep_at: datetime | None = None
        self.last_sweep_duration_s: float = 0.0
        self.last_sweep_complete: bool = True
        self.last_disk_used_ratio: float | None = None
        self.last_exemplar_detections: int = 0

    # ------------------------------------------------------------------

    def sweep(self, *, dry_run: bool = False) -> RetentionReport:
        """Run one bounded retention pass. Safe to call on a schedule."""
        start_perf = time.monotonic()
        now = self._clock()
        report = RetentionReport(dry_run=dry_run, started_at=now)
        deadline = start_perf + self.batch_budget_s
        budget = self.batch_size

        report.disk_used_ratio_before = self._disk_used_ratio()

        with self._session_factory() as session:
            exemplar_ids = self._exemplar_detection_ids(session)
            report.exemplar_detections = len(exemplar_ids)

            if self.native_days > 0 and budget > 0 and time.monotonic() < deadline:
                budget = self._strip_native(
                    session, report, now=now, deadline=deadline, budget=budget, dry_run=dry_run
                )
            if self.audible_only_days > 0 and budget > 0 and time.monotonic() < deadline:
                budget = self._strip_non_exemplar(
                    session,
                    report,
                    now=now,
                    exemplar_ids=exemplar_ids,
                    deadline=deadline,
                    budget=budget,
                    dry_run=dry_run,
                )
            if self.exemplar_only_days > 0 and budget > 0 and time.monotonic() < deadline:
                budget = self._strip_expired(
                    session, report, now=now, deadline=deadline, budget=budget, dry_run=dry_run
                )
            if budget > 0 and time.monotonic() < deadline:
                budget = self._watermark_reclaim(
                    session, report, deadline=deadline, budget=budget, dry_run=dry_run
                )
            # dry_run never mutates ORM state above, so this commit is a no-op
            # for a dry run and the real deletions otherwise.
            session.commit()

        # "Complete" means neither bound was the reason a candidate went
        # unprocessed: the batch budget wasn't exhausted, and the wall-clock
        # deadline wasn't hit. Either false means there may be more
        # candidates than this call looked at, and the next sweep should
        # pick up where this one left off.
        report.complete = budget > 0 and time.monotonic() < deadline
        report.duration_s = round(time.monotonic() - start_perf, 4)
        report.disk_used_ratio_after = self._disk_used_ratio()

        self.last_sweep_at = now
        self.last_sweep_duration_s = report.duration_s
        self.last_sweep_complete = report.complete
        self.last_disk_used_ratio = report.disk_used_ratio_after
        self.last_exemplar_detections = report.exemplar_detections
        if not dry_run:
            for tier, count in report.tier_counts.items():
                self.totals[f"{tier}_deleted"] = self.totals.get(f"{tier}_deleted", 0) + count
            for tier, nbytes in report.tier_bytes.items():
                self.totals[f"{tier}_bytes"] = self.totals.get(f"{tier}_bytes", 0) + nbytes

        if report.total_deleted:
            log.info(
                "retention.swept" if not dry_run else "retention.dry_run_swept",
                **report.to_dict(),
            )
        return report

    # -- tiers ------------------------------------------------------------

    def _strip_native(
        self,
        session: Session,
        report: RetentionReport,
        *,
        now: datetime,
        deadline: float,
        budget: int,
        dry_run: bool,
    ) -> int:
        cutoff = now - timedelta(days=self.native_days)
        query = (
            select(orm.MediaAsset, orm.Detection.id)
            .join(orm.DetectionMedia, orm.DetectionMedia.media_asset_id == orm.MediaAsset.id)
            .join(orm.Detection, orm.Detection.id == orm.DetectionMedia.detection_id)
            .where(orm.MediaAsset.reclaimed_at.is_(None))
            .where(orm.MediaAsset.kind.in_(NATIVE_KINDS))
            .where(orm.Detection.event_start_utc <= cutoff)
            .order_by(orm.Detection.event_start_utc.asc())
            .limit(budget)
        )
        for asset, detection_id in session.execute(query).all():
            if time.monotonic() >= deadline or budget <= 0:
                break
            self._delete_asset(
                report,
                asset,
                detection_id=detection_id,
                tier="native",
                reason=(
                    f"age >= {self.native_days}d: native clip superseded by "
                    "audible-only tier"
                ),
                dry_run=dry_run,
            )
            budget -= 1
        return budget

    def _strip_non_exemplar(
        self,
        session: Session,
        report: RetentionReport,
        *,
        now: datetime,
        exemplar_ids: set[uuid.UUID],
        deadline: float,
        budget: int,
        dry_run: bool,
    ) -> int:
        cutoff = now - timedelta(days=self.audible_only_days)
        # Over-fetch: exemplar filtering happens in Python because "is this
        # detection an exemplar" isn't expressible as a join condition
        # without materialising `exemplar_ids` into the query. The multiplier
        # is a small constant, not an unbounded walk.
        query = (
            select(orm.MediaAsset, orm.Detection.id)
            .join(orm.DetectionMedia, orm.DetectionMedia.media_asset_id == orm.MediaAsset.id)
            .join(orm.Detection, orm.Detection.id == orm.DetectionMedia.detection_id)
            .where(orm.MediaAsset.reclaimed_at.is_(None))
            .where(orm.Detection.event_start_utc <= cutoff)
            .order_by(orm.Detection.event_start_utc.asc())
            .limit(budget * 4)
        )
        for asset, detection_id in session.execute(query).all():
            if detection_id in exemplar_ids:
                continue
            if time.monotonic() >= deadline or budget <= 0:
                break
            self._delete_asset(
                report,
                asset,
                detection_id=detection_id,
                tier="exemplar_only",
                reason=(
                    f"age >= {self.audible_only_days}d and not first-of-species "
                    "or best-of-species"
                ),
                dry_run=dry_run,
            )
            budget -= 1
        return budget

    def _strip_expired(
        self,
        session: Session,
        report: RetentionReport,
        *,
        now: datetime,
        deadline: float,
        budget: int,
        dry_run: bool,
    ) -> int:
        cutoff = now - timedelta(days=self.exemplar_only_days)
        query = (
            select(orm.MediaAsset, orm.Detection.id)
            .join(orm.DetectionMedia, orm.DetectionMedia.media_asset_id == orm.MediaAsset.id)
            .join(orm.Detection, orm.Detection.id == orm.DetectionMedia.detection_id)
            .where(orm.MediaAsset.reclaimed_at.is_(None))
            .where(orm.Detection.event_start_utc <= cutoff)
            .order_by(orm.Detection.event_start_utc.asc())
            .limit(budget)
        )
        for asset, detection_id in session.execute(query).all():
            if time.monotonic() >= deadline or budget <= 0:
                break
            self._delete_asset(
                report,
                asset,
                detection_id=detection_id,
                tier="expired",
                reason=f"age >= {self.exemplar_only_days}d: final expiry, including exemplars",
                dry_run=dry_run,
            )
            budget -= 1
        return budget

    def _watermark_reclaim(
        self,
        session: Session,
        report: RetentionReport,
        *,
        deadline: float,
        budget: int,
        dry_run: bool,
    ) -> int:
        usage = shutil.disk_usage(self.clip_dir)
        if usage.total == 0:
            return budget
        ratio = 1.0 - usage.free / usage.total
        if ratio <= self.watermark_ratio:
            return budget
        bytes_over = int((ratio - self.watermark_ratio) * usage.total)
        freed = 0
        query = (
            select(orm.MediaAsset, orm.Detection.id)
            .join(orm.DetectionMedia, orm.DetectionMedia.media_asset_id == orm.MediaAsset.id)
            .join(orm.Detection, orm.Detection.id == orm.DetectionMedia.detection_id)
            .where(orm.MediaAsset.reclaimed_at.is_(None))
            .order_by(orm.MediaAsset.created_at.asc())
            .limit(budget)
        )
        for asset, detection_id in session.execute(query).all():
            if time.monotonic() >= deadline or budget <= 0 or freed >= bytes_over:
                break
            self._delete_asset(
                report,
                asset,
                detection_id=detection_id,
                tier="watermark",
                reason=(
                    f"disk usage {ratio:.1%} exceeds watermark "
                    f"{self.watermark_ratio:.0%}: oldest-first reclaim, tier and "
                    "exemplar status ignored"
                ),
                dry_run=dry_run,
            )
            freed += asset.byte_length
            budget -= 1
        return budget

    # -- exemplar selection -------------------------------------------------

    def _exemplar_detection_ids(self, session: Session) -> set[uuid.UUID]:
        """First-ever and best-of-species detections, exempt through the 30-90d tier.

        "Species" is `canonical_taxon_id` when there is one (species-rank
        birds), else `common_name`, else the taxonomic group itself. That last
        fallback is deliberate, not an omission: it collapses every
        `ultrasonic-pass-v1` bat pass into a single group, because that
        detector identifies passes, not species (see the honesty rules this
        project enforces in `normaliser.py`) -- there is no finer species key
        to exempt by without inventing a claim the detector never made. In
        practice this keeps one best and one first bat-pass clip, not one per
        (non-existent) bat species.

        "Best" is highest `score` for anything with a real, comparable score.
        Bats are the deliberate exception: `ultrasonic-pass-v1`'s `score` is a
        composite ``0.4*min(1, pulses/8) + 0.6*min(1, (peak_snr_db-12)/24)``
        (see `detectors/ultrasonic.py`) that is not calibrated and not
        comparable across taxonomic groups, so bats are instead ranked by
        `peak_snr_db` from `native_result` -- the detector's own physical
        measurement -- with `pulse_count` as a tiebreaker.

        Only detections that still have at least one non-reclaimed media asset
        are considered: there is nothing to exempt for a detection that has
        already lost all its evidence. Detection metadata volume is the
        "kilobytes per day" the operator described, so this is a plain read
        into Python rather than a cross-dialect JSON aggregate query.
        """
        rows = session.execute(
            select(
                orm.Detection.id,
                orm.Detection.canonical_taxon_id,
                orm.Detection.common_name,
                orm.Detection.taxonomic_group,
                orm.Detection.event_start_utc,
                orm.Detection.score,
                orm.Detection.native_result,
            )
            .join(orm.DetectionMedia, orm.DetectionMedia.detection_id == orm.Detection.id)
            .join(orm.MediaAsset, orm.MediaAsset.id == orm.DetectionMedia.media_asset_id)
            .where(orm.MediaAsset.reclaimed_at.is_(None))
            .distinct()
        ).all()

        first: dict[str, tuple[datetime, uuid.UUID]] = {}
        best: dict[str, tuple[tuple[float, float], uuid.UUID]] = {}
        for det_id, taxon, common, group, start, score, native_result in rows:
            key = taxon or common or group or "unknown"

            existing_first = first.get(key)
            if existing_first is None or start < existing_first[0]:
                first[key] = (start, det_id)

            if group == BAT_GROUP:
                result = native_result or {}
                rank_value = (
                    float(result.get("peak_snr_db") or 0.0),
                    float(result.get("pulse_count") or 0.0),
                )
            else:
                rank_value = (float(score or 0.0), 0.0)
            existing_best = best.get(key)
            if existing_best is None or rank_value > existing_best[0]:
                best[key] = (rank_value, det_id)

        exemplars = {det_id for _, det_id in first.values()}
        exemplars.update(det_id for _, det_id in best.values())
        return exemplars

    # -- mechanics ------------------------------------------------------

    def _delete_asset(
        self,
        report: RetentionReport,
        asset: orm.MediaAsset,
        *,
        detection_id: uuid.UUID | None,
        tier: str,
        reason: str,
        dry_run: bool,
    ) -> None:
        path = Path(asset.storage_uri)
        existed = path.exists()
        size = int(asset.byte_length)

        decision = RetentionDecision(
            asset_id=asset.id,
            detection_id=detection_id,
            path=str(path),
            kind=asset.kind,
            tier=tier,
            reason=reason,
            bytes=size,
            existed_on_disk=existed,
        )
        report.decisions.append(decision)
        log.info(
            "retention.would_delete" if dry_run else "retention.delete",
            asset_id=str(asset.id),
            detection_id=str(detection_id) if detection_id else None,
            kind=asset.kind,
            tier=tier,
            reason=reason,
            bytes=size,
            path=str(path),
            existed_on_disk=existed,
        )

        report.tier_counts[tier] = report.tier_counts.get(tier, 0) + 1
        report.tier_bytes[tier] = report.tier_bytes.get(tier, 0) + (size if existed else 0)
        if not existed:
            report.already_missing += 1

        if dry_run:
            return

        if existed:
            try:
                path.unlink()
            except OSError as exc:
                log.warning(
                    "retention.unlink_failed",
                    asset_id=str(asset.id),
                    path=str(path),
                    error=str(exc),
                )
                # Leave reclaimed_at unset: an unlink failure is retried next
                # sweep rather than silently recorded as done.
                return
            parent = path.parent
            if parent != self.clip_dir and parent.is_dir():
                try:
                    if not any(parent.iterdir()):
                        parent.rmdir()
                except OSError:
                    pass

        asset.reclaimed_at = self._clock()
        asset.reclaim_reason = tier

    def _disk_used_ratio(self) -> float | None:
        try:
            usage = shutil.disk_usage(self.clip_dir)
        except OSError:
            return None
        if usage.total == 0:
            return None
        return round(1.0 - usage.free / usage.total, 4)

    # -- diagnostics ------------------------------------------------------

    def find_orphans(self, *, limit: int = 500) -> Iterator[Path]:
        """Files under `clip_dir` with no `media_asset` row at all.

        Deliberately **not** called from `sweep()` or any automatic loop: it
        walks the clip tree, which is exactly the sustained-I/O pattern this
        module otherwise avoids. It exists for the CLI's manual diagnostic
        use, and it never deletes anything -- only reports.
        """
        with self._session_factory() as session:
            known = {
                Path(row[0]).resolve()
                for row in session.execute(select(orm.MediaAsset.storage_uri)).all()
            }
        found = 0
        for path in self.clip_dir.rglob("*.wav"):
            if found >= limit:
                return
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved not in known:
                found += 1
                yield path

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "native_days": self.native_days,
            "audible_only_days": self.audible_only_days,
            "exemplar_only_days": self.exemplar_only_days,
            "watermark_ratio": self.watermark_ratio,
            "batch_size": self.batch_size,
            "batch_budget_s": self.batch_budget_s,
            "last_sweep_at": self.last_sweep_at.isoformat() if self.last_sweep_at else None,
            "last_sweep_duration_s": self.last_sweep_duration_s,
            "last_sweep_complete": self.last_sweep_complete,
            "last_disk_used_ratio": self.last_disk_used_ratio,
            "exemplar_detections": self.last_exemplar_detections,
            "totals": dict(self.totals),
        }
