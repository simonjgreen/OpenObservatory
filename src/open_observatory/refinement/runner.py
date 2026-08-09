"""The batch loop: one bounded pass over unrefined events, in its own process.

Three things this deliberately does not do, each because of a bug this project
has already paid for:

* **It does not run inside the station.** ADR-033: 0.30 s of retention work in a
  dedicated thread starved the capture event loop for 55-150 ms and produced
  ~1.9 false capture gaps a minute, because an executor partitions queueing and
  nothing partitions the GIL. BatDetect2 is 2.1 s of inference per pass. The
  only fence that actually holds is a process boundary plus ``AllowedCPUs=2-3``
  (verified on the target, systemd 255 -- see ADR-045).
* **It does not open the microphone, the ring, or the event bus.** It reads the
  database and files on disk. Charter item 1 says one process owns the
  microphone, and this is not it.
* **It does not run whenever it feels like it.** :meth:`RefinementRunner.run`
  refuses outside the configured quiet window unless explicitly forced, so a
  mis-set timer cannot put the classifier on the CPU at dusk. The window is a
  property of the runner, not only of the ``.timer`` unit, because a unit file
  is one ``systemctl start`` away from being bypassed.

Everything is bounded: a wall-clock budget, an item budget, and a per-item
try/except that records ``failed`` rather than aborting the pass. A refinement
run that ends early is honest about it (``complete=False``) in exactly the way
``RetentionReport`` already is.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from ..db import models as orm
from .contracts import (
    EXAMINED_OUTCOMES,
    RefinementBasis,
    RefinementCandidate,
    RefinementOutcome,
    RefinementProposal,
    RefinementViolation,
    Refiner,
    RefinerUnavailable,
)
from .store import find_candidates, record_refinement

log = structlog.get_logger(__name__)

SessionFactory = Callable[[], Any]


@dataclass(slots=True)
class RefinementReport:
    """The result of one :meth:`RefinementRunner.run`."""

    refiner_id: str = ""
    refiner_version_label: str = ""
    dry_run: bool = False
    started_at: datetime | None = None
    duration_s: float = 0.0
    candidates_considered: int = 0
    #: Keyed by ``RefinementOutcome``.
    outcomes: dict[str, int] = field(default_factory=dict)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    #: False when the item or wall-clock budget was spent with work outstanding.
    complete: bool = True
    #: Set when the run refused to start, e.g. outside the quiet window or with
    #: the model assets absent. ``skipped`` is not a failure.
    skipped_reason: str | None = None
    inference_s: float = 0.0
    audio_s: float = 0.0

    @property
    def examined(self) -> int:
        """Items the refiner genuinely saw. Excludes ``unavailable`` and ``failed``."""
        return sum(self.outcomes.get(str(outcome), 0) for outcome in EXAMINED_OUTCOMES)

    @property
    def realtime_factor(self) -> float | None:
        if self.inference_s <= 0.0:
            return None
        return round(self.audio_s / self.inference_s, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "refiner_id": self.refiner_id,
            "refiner_version": self.refiner_version_label,
            "dry_run": self.dry_run,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "duration_s": round(self.duration_s, 3),
            "candidates_considered": self.candidates_considered,
            "examined": self.examined,
            "outcomes": dict(self.outcomes),
            "complete": self.complete,
            "skipped_reason": self.skipped_reason,
            "inference_s": round(self.inference_s, 3),
            "audio_s": round(self.audio_s, 3),
            "realtime_factor": self.realtime_factor,
        }


def in_quiet_window(now: datetime, start_hour: int, end_hour: int) -> bool:
    """Is ``now`` (UTC) inside ``[start_hour, end_hour)``?

    Handles a window that wraps midnight, since a quiet window naturally might.
    The station's measured quiet window is 01:00-03:00 UTC, which does not wrap,
    but a station further west would.
    """
    hour = now.astimezone(UTC).hour
    if start_hour == end_hour:
        return True  # a zero-width window is read as "no restriction"
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


class RefinementRunner:
    """Drives one :class:`~.contracts.Refiner` over stored evidence."""

    def __init__(
        self,
        refiner: Refiner,
        *,
        session_factory: SessionFactory,
        max_items: int = 500,
        max_seconds: float = 3600.0,
        quiet_window_start_hour: int = 1,
        quiet_window_end_hour: int = 3,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.refiner = refiner
        self._session_factory = session_factory
        self.max_items = max_items
        self.max_seconds = max_seconds
        self.quiet_window_start_hour = quiet_window_start_hour
        self.quiet_window_end_hour = quiet_window_end_hour
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self, *, dry_run: bool = False, force: bool = False) -> RefinementReport:
        started_perf = time.monotonic()
        now = self._clock()
        report = RefinementReport(
            refiner_id=self.refiner.identity.refiner_id,
            refiner_version_label=self.refiner.identity.version_label,
            dry_run=dry_run,
            started_at=now,
        )

        if not force and not in_quiet_window(
            now, self.quiet_window_start_hour, self.quiet_window_end_hour
        ):
            report.skipped_reason = (
                f"outside the configured quiet window "
                f"{self.quiet_window_start_hour:02d}:00-{self.quiet_window_end_hour:02d}:00 UTC "
                f"(now {now.astimezone(UTC).strftime('%H:%M')} UTC); pass force to override"
            )
            log.info("refinement.skipped", reason=report.skipped_reason)
            report.duration_s = time.monotonic() - started_perf
            return report

        try:
            self.refiner.prepare()
        except RefinerUnavailable as exc:
            # Not a failure. ADR-006 forbids bundling model assets, so "the
            # model is not installed" is a legitimate state of a working
            # station -- and it must be visible, not silently indistinguishable
            # from "nothing needed refining".
            report.skipped_reason = f"refiner unavailable: {exc}"
            log.warning("refinement.unavailable", refiner=report.refiner_id, detail=str(exc))
            report.duration_s = time.monotonic() - started_perf
            return report

        deadline = started_perf + self.max_seconds
        try:
            self._drain(report, deadline=deadline, dry_run=dry_run)
        finally:
            try:
                self.refiner.close()
            except Exception:
                log.exception("refinement.close_failed", refiner=report.refiner_id)

        report.duration_s = time.monotonic() - started_perf
        log.info("refinement.run", **report.to_dict())
        return report

    def _drain(self, report: RefinementReport, *, deadline: float, dry_run: bool) -> None:
        with self._session_factory() as session:
            candidates = find_candidates(
                session,
                identity=self.refiner.identity,
                groups=sorted(self.refiner.handles_groups),
                limit=self.max_items,
            )
            report.candidates_considered = len(candidates)
            for handled, candidate in enumerate(candidates):
                if time.monotonic() >= deadline:
                    report.complete = False
                    log.info(
                        "refinement.budget_spent",
                        refiner=report.refiner_id,
                        # `handled`, not `report.examined`: an item the refiner
                        # could not read still came off this list, and reporting
                        # it as still outstanding would make the next run's
                        # backlog look larger than it is.
                        handled=handled,
                        remaining=len(candidates) - handled,
                        examined=report.examined,
                    )
                    break
                self._refine_one(session, report, candidate, dry_run=dry_run)
            else:
                # Every candidate this pass fetched was handled; there may still
                # be more than `max_items` outstanding.
                report.complete = len(candidates) < self.max_items
            if not dry_run:
                session.commit()

    def _refine_one(
        self,
        session: Session,
        report: RefinementReport,
        candidate: RefinementCandidate,
        *,
        dry_run: bool,
    ) -> None:
        began = time.monotonic()
        try:
            proposal = self.refiner.refine(candidate)
        except Exception as exc:
            log.exception(
                "refinement.item_failed",
                refiner=report.refiner_id,
                detection_id=str(candidate.detection_id),
            )
            proposal = RefinementProposal(
                outcome=RefinementOutcome.FAILED,
                basis=RefinementBasis.NEW_MODEL,
                reason=f"{type(exc).__name__}: {exc}",
            )
        elapsed = time.monotonic() - began
        report.inference_s += elapsed
        report.audio_s += float(proposal.evidence.get("classified_audio_s") or 0.0)

        key = str(proposal.outcome)
        report.outcomes[key] = report.outcomes.get(key, 0) + 1
        if proposal.carries_a_claim:
            report.proposals.append(
                {
                    "detection_id": str(candidate.detection_id),
                    "event_start_utc": candidate.event_start_utc.isoformat(),
                    "original_common_name": candidate.common_name,
                    "original_score": round(candidate.score, 4),
                    "our_peak_frequency_hz": candidate.peak_frequency_hz,
                    "proposed_scientific_name": proposal.proposed_scientific_name,
                    "proposed_common_name": proposal.proposed_common_name,
                    "proposed_score": proposal.proposed_score,
                    "reason": proposal.reason,
                }
            )

        if dry_run:
            return
        try:
            record_refinement(
                session,
                candidate=candidate,
                identity=self.refiner.identity,
                proposal=proposal,
                authority=self.refiner.authority,
                now=self._clock(),
            )
        except RefinementViolation:
            # Loud, and fatal for this item only: a violation means a refiner is
            # trying to do something the charter forbids, which is worth a
            # stack trace in the journal rather than a counter.
            log.exception(
                "refinement.violation",
                refiner=report.refiner_id,
                detection_id=str(candidate.detection_id),
            )
            raise


def write_health_event(session: Session, report: RefinementReport) -> orm.HealthEvent:
    """Record the run where the existing health surfaces can already see it.

    Uses ``health_event`` rather than inventing a second reporting mechanism:
    the row is what makes "the refiner has not run for three nights" an
    answerable question, which is precisely the failure the charter's retention
    safeguard is guarding against.
    """
    severity = "info"
    if report.skipped_reason or report.outcomes.get(str(RefinementOutcome.FAILED)):
        severity = "warning"
    row = orm.HealthEvent(
        service="refinement",
        component=report.refiner_id or "refinement",
        severity=severity,
        event_type="refinement.run",
        start_utc=report.started_at or datetime.now(UTC),
        end_utc=datetime.now(UTC),
        detail=report.to_dict(),
    )
    session.add(row)
    return row
