"""NVR-style tiered clip retention.

The operator's decision (recorded in ADR-026, revised by ADR-061) is precise,
so this module implements exactly it and nothing more:

* **Detection metadata is kept forever.** Species, timestamps, scores, peak
  frequency, capture coverage -- nothing here ever deletes a ``detection`` row
  or mutates its columns. It is the valuable, and cheap, part.
* **Clip bytes degrade by age**, in tiers:

  - 0-7 days: native (full-rate) clip and its audible rendering both survive.
  - 7-30 days: native clip deleted; audible rendering survives.
  - 30+ days: only clips an operator has explicitly kept survive; every
    other detection in this age band loses its remaining clip(s) too.
  - **Always**, independent of the above: once the clip filesystem exceeds a
    configurable watermark, the oldest surviving *unkept* clips are reclaimed
    first, regardless of tier. This is the safety valve -- disk space always
    wins over any retention preference, except an explicit keep, which this
    tier also honours (see below).

* **A human `kept` flag** (ADR-061, `detection.kept_at`/`kept_by`) means
  "keep forever, until a human removes it". It is set and cleared only by a
  human -- never by age or disk pressure -- and every tier's candidate query
  excludes it, including the watermark reclaim. It replaces a computed
  first-of-species/best-of-species rule that used to cost an unbounded
  per-sweep table scan; see ADR-061 for the incident that forced the change.

* **A human hold is exempt from the two age-based tiers** (ADR-043). A
  detection whose latest human review (`review.py`) has status `"held"` --
  an explicit "keep this, it needs my ear" -- is skipped by `_strip_native`
  and `_strip_unkept`, the same way a kept detection is. Deliberately
  narrower than `kept`: the watermark reclaim tier does **not**
  check it, on purpose, because it is this module's one hard safety valve
  (see the bullet above) and a held-but-not-kept detection is still only
  evidence, not something the station can let disk exhaustion turn into an
  outage over. An operator who needs a genuinely permanent hold should
  either mark it `kept` or export the clip. See ADR-043's "known limitation"
  note. `held` and `kept` are independent: a detection may be both, either,
  or neither.

Deleting a clip never deletes its ``media_asset`` row: the row is marked
``reclaimed_at``/``reclaim_reason`` instead, so `/api/v1/media/{id}` can keep
answering (with 410, already handled) and a history view can keep showing
that the detection happened, just without audio.

**Tier age is measured on the asset, not the detection** (ADR-062). Each
age-based tier bounds and orders by ``media_asset.created_at`` rather than
``detection.event_start_utc``. That is not the same number, and the difference
is deliberate:

* ``created_at`` is when the clip file was written, which is always *after* the
  event it contains -- verified across all 86,377 native assets on the station:
  minimum lag 0.45 s, mean 823 s, maximum 29 h, and **zero** rows where the
  asset predates its detection. So ``created_at <= cutoff`` implies
  ``event_start_utc <= cutoff``. The substitution can only ever make a tier
  *later* to act, never earlier -- it cannot delete a clip before its policy
  age, which is the direction that would matter.
* What it costs is that a clip written unusually long after its event (a
  refinement backfill, say) survives its tier by up to that lag. Bounded, and
  it resolves itself on a subsequent sweep as the cutoff advances.
* What it buys is that the walk can use a partial index keyed on the asset,
  which is the only way this sweep stops re-examining work it has already
  done. See ADR-062 for the measurements.

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
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import Integer, cast, func, select, tuple_
from sqlalchemy import text as sa_text
from sqlalchemy import update as sa_update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from . import evidence_value
from . import review as review_queries
from .db import models as orm

log = structlog.get_logger(__name__)

#: Media kinds that are the authoritative, full-rate recording. Everything
#: else (``playback``, ``audible_ultrasonic``) is a human/browser-audible
#: derivative and is what the 7-30 day tier is trying to keep.
NATIVE_KINDS = frozenset({"evidence_native"})

#: Order the four tiers run in `sweep()`, used to expand "interrupted here"
#: into "skipped from here on", the same shape a batch/deadline backlog
#: drain already produces (see `RetentionReport.tiers_skipped`).
_TIER_ORDER = ("native", "unkept", "watermark")

#: SQLite VM instructions between `sqlite3.Connection.set_progress_handler`
#: callbacks (ADR-061's statement-timeout addendum). Chosen small enough to
#: be responsive -- SQLite executes on the order of tens of millions of VM
#: instructions per second for ordinary row scans, so 1000 instructions is
#: comfortably sub-millisecond of work between checks, meaning a statement
#: is aborted within about a millisecond of its deadline passing even deep
#: inside a large scan -- while staying coarse enough that the callback
#: itself (one `time.monotonic()` call) is invoked at a rate that cannot be
#: measured against the cost of the query it is bounding. Not exposed as a
#: constructor argument: it tunes responsiveness of the abort, not sweep
#: policy, and `batch_budget_s`/`batch_size` remain the only two knobs an
#: operator needs.
_PROGRESS_HANDLER_INSTRUCTIONS = 1000

#: ``detection.taxonomic_group`` for an ultrasonic pass (``detectors/
#: ultrasonic.py``). Spelled out here rather than imported because the modules
#: that already name it (``display.py``, ``mqtt/discovery.py``) are not ones
#: the retention sweep should be dragging in.
_BAT_GROUP = "bat"

#: Trailing window over which a bat band's share of passes is measured
#: (ADR-074: "a band holding < 1% of the trailing-90-day passes is sparse").
#: A window rather than all history because sparseness is a claim about what
#: is flying now, and because it is what lets the count be an indexed range
#: scan on `ix_detection_group_start` instead of a table scan.
_BAND_WINDOW_DAYS = 90

#: Share of the trailing window, in parts per thousand, below which a bat band
#: is sparse and is banked whole (ADR-074: "a band holding < 1% of the
#: trailing-90-day passes").
#:
#: Deliberately **not** `evidence_sample_permille`, which this used to reuse
#: because both happened to be "one per thousand" on the day they were
#: written. They are not the same quantity. `evidence_sample_permille` is the
#: settings-page knob labelled "blind sample (per 1000)" -- how often an
#: unbiased draw keeps a clip the policy would otherwise let age out -- while
#: this one decides which bat bands are exempt from age expiry *for ever*.
#: Sharing the number meant an operator raising the blind sample to 10%
#: silently and permanently banked the 45-50 kHz band along with it, which is
#: not a trade anyone was offered on that page. A constant rather than a
#: second setting because this is ADR-074's definition of "sparse", not a knob.
_BAND_SPARSE_PERMILLE = 10

#: How far back one sweep looks for detections to promote (ADR-076).
#:
#: Not "the whole archive": that question cannot be asked affordably from
#: inside the sweep -- see `_promotion_candidates`. Historical material is the
#: backfill's job (`oo retention bank-backfill`), which walks the archive
#: oldest-first under no time budget and is idempotent, so a station that was
#: down for a week catches up by running it rather than by widening this.
#:
#: Twenty-four hours against a 300 s sweep cadence is about 288 overlapping
#: passes over the same window, which is deliberate: promotion is idempotent
#: (a banked row is not a candidate), so overlap costs one indexed scan and
#: buys tolerance of a sweep that got interrupted or a budget that ran out.
_PROMOTION_LOOKBACK = timedelta(hours=24)


def _band_expression() -> Any:
    """``detection.peak_frequency_hz`` bucketed into 5 kHz bands, in SQL.

    Deliberately the same arithmetic as `evidence_value.frequency_band`, done
    in the database rather than in Python: bucketing in Python would mean
    either grouping by raw frequency (thousands of distinct values, one row
    each) or reading every row out, and this sweep reads rows out only in
    `LIMIT`-bounded batches. ``CAST(x AS INTEGER)`` truncates toward zero,
    which equals ``//`` for the positive frequencies this is applied to --
    every caller pairs it with ``peak_frequency_hz > 0``, exactly as
    `frequency_band` returns ``None`` for anything else.
    """
    return cast(orm.Detection.peak_frequency_hz / evidence_value.BAND_WIDTH_HZ, Integer) * (
        evidence_value.BAND_WIDTH_HZ // 1000
    )


@dataclass(frozen=True, slots=True)
class _EvidenceBank:
    """What each species has banked right now (ADR-076).

    ADR-074 carried the *members* of the bank -- a set of species names, and a
    set of bat bands, recomputed from a census measured at 18.3219 s against a
    1.5 s budget. This carries only the **counts**, read from an index over the
    banked rows in 0.0023 s, because membership is now a column and no longer
    has to be re-derived to be applied.
    """

    #: species name -> detections it has banked. Bats appear under `_BAT_GROUP`
    #: keyed by band edge in `bands`, not here.
    banked: Mapping[str, int]
    #: band edge (kHz) -> detections that band has banked.
    bands: Mapping[int, int]

    def exclusion(self) -> Any:
        """SQL for "the bank is not keeping this detection".

        One predicate over an ordinary nullable column, and none of ADR-074's
        three-valued-logic hazard: `banked_at IS NULL` is true or false for
        every row, including the bat passes whose `common_name` is NULL by
        design and the birds whose `peak_frequency_hz` is. The `NOT (x OR y)`
        spelling this replaces evaluated to NULL for exactly those rows, which
        would have dropped every bat clip out of both tiers' candidate queries
        for ever.
        """
        return orm.Detection.banked_at.is_(None)

    def total(self) -> int:
        return sum(self.banked.values()) + sum(self.bands.values())


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
class _TierTally:
    """One tier's staged-but-not-yet-reported tallies (C1, 2026-08-14).

    `_stage_delete` writes here, never straight to `RetentionReport`: a
    tier's count/bytes/`already_missing`/decisions must only become visible
    on the report -- and thence `RetentionSweeper.totals`, which feeds the
    Prometheus deletion counters -- once that tier's own transaction is
    durably committed (or, for a dry run, once its staging pass finishes
    without being aborted). An aborted tier's `_TierTally` is simply
    discarded by the caller, so it contributes nothing.
    """

    count: int = 0
    bytes: int = 0
    already_missing: int = 0
    decisions: list[RetentionDecision] = field(default_factory=list)
    #: Per-`evidence_value.Verdict` counts/bytes for this tier's own staged
    #: decisions (ADR-074/Task 6). Empty unless `evidence_value_enabled`.
    value_counts: dict[str, int] = field(default_factory=dict)
    value_bytes: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class RetentionReport:
    """The result of one `RetentionSweeper.sweep()` call."""

    dry_run: bool = False
    started_at: datetime | None = None
    duration_s: float = 0.0
    disk_used_ratio_before: float | None = None
    disk_used_ratio_after: float | None = None
    #: Count of detections currently marked `kept` (ADR-061) -- exempt from
    #: every tier, including the watermark reclaim.
    kept_detections: int = 0
    #: Count of detections exempted from this sweep by an explicit human
    #: hold (ADR-043) -- see `RetentionSweeper._strip_native` et al.
    held_detections: int = 0
    #: Bytes held by kept, un-reclaimed media assets that `_watermark_reclaim`
    #: declined to reclaim (ADR-061). Only ever non-zero when the watermark
    #: was actually exceeded this sweep -- see `_watermark_reclaim` -- so a
    #: healthy station never pays for the query that produces it. This is
    #: the number that makes "the sweep is not deleting kept evidence" an
    #: observable fact rather than an assumption: see `_health_payload`.
    watermark_blocked_by_kept: int = 0
    already_missing: int = 0
    #: Per-tier counts/bytes: keys are "native", "unkept", "watermark".
    tier_counts: dict[str, int] = field(default_factory=dict)
    tier_bytes: dict[str, int] = field(default_factory=dict)
    #: Per-`evidence_value.Verdict` counts/bytes (ADR-074, Task 6): what each
    #: category of clip would cost, for a human to read before anything is
    #: unlinked. Keys are `Verdict` values ("bank", "quota", "sample",
    #: "expire") -- though `"bank"` never appears here, because a banked
    #: candidate is excluded from the tier's own `SELECT` (see
    #: `_EvidenceBank.exclusion`) and never reaches staging to be tallied.
    #: Only the native/unkept age tiers populate these -- the watermark tier
    #: is deliberately blind to value (ADR-074 rule: watermark outranks
    #: value) -- and both stay empty while `evidence_value_enabled` is
    #: `False`, so the flag's inertness extends to this report too.
    value_counts: dict[str, int] = field(default_factory=dict)
    value_bytes: dict[str, int] = field(default_factory=dict)
    #: Detections promoted into the bank this sweep (ADR-076). Always 0 while
    #: `evidence_value_enabled` is off -- `sweep()` never calls
    #: `_promote_to_bank` in that case.
    promoted: int = 0
    decisions: list[RetentionDecision] = field(default_factory=list)
    #: False when the batch budget (count or wall-clock) was exhausted with
    #: candidate work still outstanding -- the next sweep will pick it up.
    complete: bool = True
    #: Monotonic seconds from sweep start to just before the first tier
    #: guard. ADR-061: the incident this exists to catch was a preamble
    #: query alone (2.978 s) eating the entire 1.5 s budget before any tier
    #: guard was reached, for nine days, with no symptom besides a zero
    #: deletion count that looked identical to "nothing to delete". A
    #: healthy sweep's preamble is a couple of indexed queries and should
    #: read as a small fraction of `batch_budget_s`; a preamble that is
    #: itself close to (or past) the budget is the nine-day failure
    #: recurring.
    preamble_s: float = 0.0
    #: Name of every tier ("native", "unkept", "watermark") whose guard
    #: evaluated False, in evaluation order -- appended whether the cause
    #: was the wall-clock deadline, an exhausted batch, or the tier being
    #: disabled by configuration (a `*_days` of 0). Distinguishing a broken
    #: sweep from a merely partial one is not "was anything skipped" but
    #: "was *everything* skipped": a backlog drain only ever skips a
    #: trailing suffix of tiers because the ones before it consumed real
    #: budget or time, whereas all three skipped together -- with the
    #: batch budget still full -- means the sweep never did any work at
    #: all, which a healthy station in steady state never produces (see
    #: `station.py`'s `housekeeping.retention_never_reached_a_tier`).
    tiers_skipped: list[str] = field(default_factory=list)
    #: Name of the tier whose *statement* (not merely its row-by-row budget)
    #: was aborted this sweep by the per-statement deadline guard -- `None`
    #: on every ordinary sweep, including a normal batch/deadline backlog
    #: drain. `"preamble"` if the abort happened before any tier guard ran.
    #: This is the ADR-061 second-addendum fix: `batch_budget_s` used to be
    #: checked only between rows of a result set already fully returned by
    #: `session.execute(query).all()`, so one slow statement ran to
    #: completion however long that took (over five minutes, measured on
    #: the station) instead of degrading to "fewer deletions this pass".
    interrupted_tier: str | None = None
    #: Monotonic seconds from sweep start to the moment the aborted
    #: statement's exception was caught, alongside `interrupted_tier`.
    interrupted_after_s: float | None = None

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
            "kept_detections": self.kept_detections,
            "held_detections": self.held_detections,
            "watermark_blocked_by_kept": self.watermark_blocked_by_kept,
            "already_missing": self.already_missing,
            "tier_counts": dict(self.tier_counts),
            "tier_bytes": dict(self.tier_bytes),
            "value_counts": dict(self.value_counts),
            "value_bytes": dict(self.value_bytes),
            "total_deleted": self.total_deleted,
            "total_bytes": self.total_bytes,
            "complete": self.complete,
            "preamble_s": self.preamble_s,
            "tiers_skipped": list(self.tiers_skipped),
            "interrupted_tier": self.interrupted_tier,
            "interrupted_after_s": self.interrupted_after_s,
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
        watermark_ratio: float = 0.85,
        batch_size: int = 200,
        batch_budget_s: float = 1.5,
        clock: Callable[[], datetime] | None = None,
        evidence_value_enabled: bool = False,
        evidence_common_species: Sequence[str] = (),
        evidence_bank_size: int = 200,
        evidence_sample_permille: int = 10,
        evidence_implausible_species: Sequence[str] = (),
        evidence_implausible_cap: int = 3,
        promotion_lookback: timedelta = _PROMOTION_LOOKBACK,
    ) -> None:
        self.clip_dir = Path(clip_dir)
        self._session_factory = session_factory
        self.native_days = native_days
        self.audible_only_days = audible_only_days
        self.watermark_ratio = watermark_ratio
        self.batch_size = batch_size
        self.batch_budget_s = batch_budget_s
        self._clock = clock or (lambda: datetime.now(UTC))

        # -- value-based retention (ADR-074) --------------------------------
        #: All four are plain attributes because that is how a live-tier
        #: setting reaches this object: `tuning.LIVE_TARGETS` maps each one
        #: here and `Station.apply_tuning` assigns it, exactly as it does for
        #: `native_days` and its siblings. Defaults are the shipped ones, and
        #: the flag ships **off**: this policy deletes clips, it has never run
        #: against real data, and its first run must be a dry-run a human
        #: reads (ADR-074 rule 3).
        self.evidence_value_enabled = evidence_value_enabled
        self.evidence_common_species = evidence_common_species
        self.evidence_bank_size = evidence_bank_size
        self.evidence_sample_permille = evidence_sample_permille
        #: Species a range model says cannot plausibly be here (ADR-076).
        #: `cap_for` checks this before `evidence_common_species`: a species on
        #: both lists is a systematic misidentification, and the implausible
        #: cap (three examples) is what makes it judgeable rather than merely
        #: boring.
        self.evidence_implausible_species = evidence_implausible_species
        self.evidence_implausible_cap = evidence_implausible_cap
        #: How far back `_promotion_candidates` looks, not a setting -- see
        #: `_PROMOTION_LOOKBACK`. A constructor parameter only so tests can
        #: reach material older than 24 hours without waiting for it.
        self.promotion_lookback = promotion_lookback
        #: Total banked detections as of the last sweep (`_EvidenceBank.total`),
        #: 0 while the flag is off. Reported by `snapshot()` in place of
        #: ADR-074's census-timing figures (ADR-076: there is no census left to
        #: time -- membership is a column, read by one indexed query every
        #: sweep, not derived rarely and reused).
        self.last_bank_size: int = 0
        #: Detections promoted into the bank by the last sweep. 0 while
        #: `evidence_value_enabled` is off, since `sweep()` never calls
        #: `_promote_to_bank` in that case.
        self.promoted_last_sweep: int = 0

        #: Cumulative across the process lifetime, for Prometheus counters.
        self.totals: dict[str, int] = {}
        self.last_sweep_at: datetime | None = None
        self.last_sweep_duration_s: float = 0.0
        self.last_sweep_complete: bool = True
        #: Consecutive sweeps that neither completed nor deleted anything.
        #: Zero whenever the last sweep did either. See `sweep`.
        self.consecutive_barren_sweeps: int = 0
        self.last_preamble_s: float = 0.0
        self.last_tiers_skipped: list[str] = []
        self.last_interrupted_tier: str | None = None
        self.last_interrupted_after_s: float | None = None
        self.last_disk_used_ratio: float | None = None
        self.last_kept_detections: int = 0
        self.last_held_detections: int = 0
        self.last_watermark_blocked_by_kept: int = 0

        # -- rolling missing-file audit (ADR-057) ---------------------------
        #: Cursor into ``(media_asset.created_at, id)``, advanced by
        #: `audit_missing` and wrapped to the beginning when it runs off the
        #: end. A cursor rather than an OFFSET because OFFSET is O(offset) and
        #: this table is tens of thousands of rows and growing; the ``id`` half
        #: is there because ``created_at`` is not unique -- three or four
        #: assets are written per detection within microseconds of each other
        #: -- and a bare ``>`` would silently skip every row that shares a
        #: timestamp with the last one read, which is exactly the class of
        #: quiet omission this whole audit exists to catch.
        self.audit_cursor: tuple[datetime, uuid.UUID] | None = None
        #: Running tally for the pass currently in progress.
        self.audit_scanned: int = 0
        self.audit_missing: int = 0
        self.audit_missing_bytes: int = 0
        #: The last *completed* pass over every live row. These are the only
        #: two numbers that answer "how many rows claim a file that is gone"
        #: exactly rather than as a partial sample, which is why they are
        #: reported separately instead of being folded into a running total.
        self.last_pass_scanned: int = 0
        self.last_pass_missing: int = 0
        self.last_pass_missing_bytes: int = 0
        self.audit_passes: int = 0
        self.last_audit_at: datetime | None = None
        self.last_audit_duration_s: float = 0.0

    # ------------------------------------------------------------------

    @contextmanager
    def _bounded_statements(self, session: Session, deadline: float) -> Iterator[None]:
        """Arm a per-statement abort for this sweep's own queries, scoped narrowly.

        ADR-061's second addendum: `batch_budget_s`/`batch_size` are only
        checked *between* rows of a result set `session.execute(query).all()`
        has already fully returned, so a single slow statement runs to
        completion however long that takes -- once, on the station, for over
        five minutes, wedging the housekeeping loop behind it (retention
        runs in the evidence executor the housekeeping loop awaits, so
        stream heartbeats, the ADR-057 media audit and the ADR-059 disk-usage
        refresh all stopped for the duration; capture was unaffected, since
        it owns its own thread, ADR-030).

        `sqlite3.Connection.set_progress_handler(handler, n)` calls `handler`
        every `n` VM instructions while a statement is executing and aborts
        that statement with `sqlite3.OperationalError: interrupted` if it
        returns truthy -- exactly the mechanism this ADR named as the open
        fix. Installed on the session's own DBAPI connection (reached via
        `session.connection().connection`, which SQLAlchemy's pool proxies
        straight through to the real `sqlite3.Connection`) and always
        removed in `finally`, before this connection can be checked back
        into the pool: a handler left armed there would abort the next,
        unrelated caller's query the moment its own already-expired deadline
        was next evaluated, on a connection it never agreed to share a
        budget with.

        A no-op on any non-SQLite dialect. PostgreSQL (the eventual
        production DSN, ADR-007) has no equivalent DBAPI hook; a statement
        timeout there is a `SET LOCAL statement_timeout` decision, out of
        scope here.
        """
        if session.get_bind().dialect.name != "sqlite":
            yield
            return

        # SQLAlchemy's pool proxy (`PoolProxiedConnection`) forwards unknown
        # attributes straight through to the real driver connection, but its
        # type stub does not know about `sqlite3.Connection`-specific
        # methods -- hence `Any` here rather than fighting the stub.
        dbapi_connection: Any = session.connection().connection

        def _past_deadline() -> int:
            return 1 if time.monotonic() >= deadline else 0

        dbapi_connection.set_progress_handler(_past_deadline, _PROGRESS_HANDLER_INSTRUCTIONS)
        try:
            yield
        finally:
            dbapi_connection.set_progress_handler(None, 0)

    def sweep(self, *, dry_run: bool = False) -> RetentionReport:
        """Run one bounded retention pass. Safe to call on a schedule."""
        start_perf = time.monotonic()
        now = self._clock()
        report = RetentionReport(dry_run=dry_run, started_at=now)
        deadline = start_perf + self.batch_budget_s
        budget = self.batch_size

        report.disk_used_ratio_before = self._disk_used_ratio()

        with self._session_factory() as session:
            current_tier = "preamble"
            aborted = False
            try:
                with self._bounded_statements(session, deadline):
                    # A plain indexed count, not the materialised-into-Python
                    # scan this replaces (ADR-061): `ix_detection_kept_at_partial`
                    # (migration 0010; it replaced the plain `ix_detection_kept_at`
                    # dropped by migration 0009) makes this an index-only
                    # count, not a table scan, and nothing here reads
                    # `native_result` or any other wide column.
                    report.kept_detections = session.execute(
                        select(func.count()).select_from(orm.Detection).where(
                            orm.Detection.kept_at.is_not(None)
                        )
                    ).scalar_one()
                    held_ids = review_queries.held_detection_ids(session)
                    report.held_detections = len(held_ids)

                    # Everything above this line is the preamble that,
                    # unbounded, caused the nine-day incident (ADR-061):
                    # recorded *before* the first tier guard so a preamble
                    # that alone ate the budget shows up as a large
                    # `preamble_s` next to an empty `tiers_skipped`-causing
                    # deadline, rather than being indistinguishable from a
                    # fast one.
                    report.preamble_s = round(time.monotonic() - start_perf, 4)

                # C1 (2026-08-14): each tier below arms its own
                # `_bounded_statements` (inside `_run_tier` for native/unkept,
                # inline for watermark) and commits its own real work before
                # returning, rather than sharing one continuous arming across
                # all three the way the preamble's query above still can
                # safely (it never mutates anything). Committing per tier is
                # what removes the cross-tier-autoflush hazard C1 found: a
                # completed tier has nothing left pending for the *next*
                # tier's `session.execute()` to autoflush.
                # ADR-064. The watermark tier goes **first** whenever disk is
                # already over the line, and only then. It is the one tier that
                # is an emergency rather than a policy, and running it last
                # behind two age tiers that share one `batch_size` budget meant
                # it was skipped exactly when it was most needed: the sweep
                # immediately after ADR-062's fix reclaimed its full 200-file
                # batch in the native tier and reported
                # `tiers_skipped=['unkept', 'watermark']`. A safety valve
                # downstream of the thing it protects against is not a safety
                # valve. Below the watermark this costs one `shutil.disk_usage`
                # call and the ordering is unchanged.
                if self._disk_over_watermark():
                    current_tier = "watermark"
                    budget = self._watermark_reclaim(
                        session, report, deadline=deadline, budget=budget, dry_run=dry_run
                    )
                    watermark_ran = True
                else:
                    watermark_ran = False
                current_tier = "native"
                # ADR-074, and deliberately below the watermark tier rather
                # than in the preamble above: value decides what is *worth*
                # keeping, the watermark decides what the disk can *hold*, and
                # the cheapest way to guarantee the second never waits on the
                # first is to do the first afterwards. Inert while the flag is
                # off -- no query is issued and the tiers below get a `None`
                # bank, which leaves their candidate queries exactly as they
                # were.
                #
                # Wrapped here, at the call site, rather than inside
                # `_evidence_bank` itself (ADR-076): the method's contract is
                # deliberately deadline-free -- it is one indexed query, not a
                # census with its own abort/retry machinery to bound -- but
                # every statement this sweep issues still runs under the same
                # per-statement guard, so an unexpectedly slow bank read
                # degrades this sweep to "native tier interrupted" (the
                # ordinary backlog-drain path below) rather than escaping
                # unbounded.
                with self._bounded_statements(session, deadline):
                    bank = self._evidence_bank(session)
                self.last_bank_size = bank.total() if bank is not None else 0
                # ADR-076: promotion runs after the watermark tier -- the
                # watermark is an emergency valve and must never queue behind
                # a write -- and before the two age tiers, which must see the
                # result. Re-reading the bank below is what makes that last
                # part true: without it, a detection promoted this sweep
                # would be deleted by the same sweep that just decided to
                # keep it, because the age tiers' candidate queries were
                # built from the bank read before promotion ran.
                if bank is not None:
                    with self._bounded_statements(session, deadline):
                        report.promoted = self._promote_to_bank(
                            session, bank, now=now, deadline=deadline
                        )
                    # Outside the arm, same reason `_run_tier` commits
                    # outside its own: committing inside would check the
                    # armed connection back into the pool before this
                    # context manager's `finally` gets to disarm it.
                    if report.promoted and not dry_run:
                        session.commit()
                    # Re-read, guarded the same as every other statement this
                    # sweep issues: the age tiers below must see what
                    # promotion just banked, or a detection promoted this
                    # sweep is deleted by the same sweep that decided to keep
                    # it. If this read is the one that gets interrupted,
                    # `bank` is left holding the pre-promotion snapshot --
                    # stale, and unsafe to hand to the age tiers, because
                    # "stale" here specifically means missing the rows just
                    # promoted, which is exactly what would get deleted by
                    # the tiers' `bank.exclusion()` filter failing to exempt
                    # them. So this is deliberately left unguarded by a local
                    # `except`: the `OperationalError` propagates to
                    # `sweep()`'s own handler, which records `current_tier`
                    # ("native" by this point) as interrupted and skips the
                    # native/unkept/watermark tiers for this pass entirely --
                    # the same "fewer deletions this pass, try again next
                    # sweep" degrade every other interrupted statement here
                    # gets, and the only one of the two possible responses
                    # that cannot unbank-by-omission what was just promoted.
                    with self._bounded_statements(session, deadline):
                        bank = self._evidence_bank(session)
                if self.native_days > 0 and budget > 0 and time.monotonic() < deadline:
                    budget = self._strip_native(
                        session,
                        report,
                        now=now,
                        deadline=deadline,
                        budget=budget,
                        dry_run=dry_run,
                        held_ids=held_ids,
                        bank=bank,
                    )
                else:
                    report.tiers_skipped.append("native")
                current_tier = "unkept"
                if self.audible_only_days > 0 and budget > 0 and time.monotonic() < deadline:
                    budget = self._strip_unkept(
                        session,
                        report,
                        now=now,
                        deadline=deadline,
                        budget=budget,
                        dry_run=dry_run,
                        held_ids=held_ids,
                        bank=bank,
                    )
                else:
                    report.tiers_skipped.append("unkept")
                current_tier = "watermark"
                if watermark_ran:
                    pass
                elif budget > 0 and time.monotonic() < deadline:
                    budget = self._watermark_reclaim(
                        session, report, deadline=deadline, budget=budget, dry_run=dry_run
                    )
                else:
                    report.tiers_skipped.append("watermark")
            except OperationalError as exc:
                if "interrupted" not in str(getattr(exc, "orig", exc)):
                    # A genuine database error, not our deadline guard --
                    # never swallow it as if it were a bounded timeout.
                    raise
                report.interrupted_tier = current_tier
                report.interrupted_after_s = round(time.monotonic() - start_perf, 4)
                report.complete = False
                aborted = True
                # Every tier at or after the one whose statement was aborted
                # did not run (or did not finish) this sweep -- record them
                # the same way an ordinary batch/deadline backlog drain
                # already does, so the report reads as "fewer deletions this
                # pass", the entire point of `batch_budget_s`, not as a
                # crash.
                start_index = (
                    _TIER_ORDER.index(current_tier) if current_tier in _TIER_ORDER else 0
                )
                for tier in _TIER_ORDER[start_index:]:
                    if tier not in report.tiers_skipped:
                        report.tiers_skipped.append(tier)
                log.error(
                    "retention.statement_interrupted",
                    tier=current_tier,
                    after_s=report.interrupted_after_s,
                    batch_budget_s=self.batch_budget_s,
                )

            if aborted:
                # C1: the interrupted tier's own flush/commit (inside
                # `_run_tier`) may have left this session's transaction
                # marked rollback-only -- `session.commit()` on that state
                # raises `PendingRollbackError`, an `InvalidRequestError`
                # `except OperationalError` above cannot catch, which is
                # exactly how this used to escape `sweep()` after files were
                # already gone from disk. `rollback()` is the documented
                # recovery (SQLAlchemy's own `PendingRollbackError` message
                # names it) and is always safe here: every earlier tier that
                # finished already committed its own work in its own
                # transaction, so this only ever discards the interrupted
                # tier's own not-yet-committed staging -- and `_run_tier`
                # guarantees nothing was unlinked for that tier before its
                # commit succeeded, so nothing durable is lost by discarding
                # it.
                session.rollback()
            elif dry_run:
                # Nothing above committed anything real (`_run_tier` and
                # `_watermark_reclaim` both gate their `session.commit()` on
                # `not dry_run`), but every processed tier's decisions are
                # still staged in-memory so a later tier's candidate query
                # would not re-offer one within this same pass. Discard all
                # of it here, so "dry run" still means nothing persisted.
                session.rollback()
            else:
                # Ordinarily a no-op: every tier that ran committed its own
                # work already. Kept as a safety net, not the load-bearing
                # commit it used to be.
                session.commit()

        # "Complete" means neither bound was the reason a candidate went
        # unprocessed: the batch budget wasn't exhausted, the wall-clock
        # deadline wasn't hit, and no statement was aborted outright. Any of
        # those false means there may be more candidates than this call
        # looked at, and the next sweep should pick up where this one left
        # off.
        report.complete = report.complete and budget > 0 and time.monotonic() < deadline
        report.duration_s = round(time.monotonic() - start_perf, 4)
        report.disk_used_ratio_after = self._disk_used_ratio()

        self.last_sweep_at = now
        self.last_sweep_duration_s = report.duration_s
        self.last_sweep_complete = report.complete
        self.last_preamble_s = report.preamble_s
        self.last_tiers_skipped = list(report.tiers_skipped)
        self.last_interrupted_tier = report.interrupted_tier
        self.last_interrupted_after_s = report.interrupted_after_s
        self.last_disk_used_ratio = report.disk_used_ratio_after
        self.last_kept_detections = report.kept_detections
        self.last_held_detections = report.held_detections
        self.last_watermark_blocked_by_kept = report.watermark_blocked_by_kept
        self.promoted_last_sweep = report.promoted
        # ADR-062. A single incomplete sweep is ordinary -- it means the budget
        # ran out with work still queued, and the next one continues. A *run*
        # of sweeps that each reclaim nothing is the failure this counter
        # exists to make visible: it is what the station did for two days
        # while `/api/v1/health` reported `ok`, because the only thing said
        # out loud was a `retention_sweep_keeping_up: false` buried in the
        # storage block with nothing escalating it.
        if report.complete or report.total_deleted:
            self.consecutive_barren_sweeps = 0
        else:
            self.consecutive_barren_sweeps += 1
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

    def _run_tier(
        self,
        session: Session,
        report: RetentionReport,
        *,
        tier: str,
        query: Any,
        reason: str,
        deadline: float,
        budget: int,
        dry_run: bool,
        bank: _EvidenceBank | None = None,
    ) -> int:
        """Fetch one tier's candidates, stage every decision, and commit the
        whole tier as one transaction *before* unlinking a single file.

        C1 (2026-08-14): this is what replaces the old per-row
        unlink-then-mark-then-commit-much-later order (see
        `_stage_delete`'s docstring for the measured failure it caused). The
        `SELECT`, every row's staging, and the explicit `flush()` all happen
        inside one `_bounded_statements` arming, so an abort anywhere in
        this tier's own work -- including its own flush -- propagates out of
        this call with **no file for this tier unlinked yet**: `to_unlink`
        is a local list, never consulted by the caller when an exception
        escapes this method. `session.commit()` deliberately runs *after*
        that arming exits, not inside it: `_bounded_statements` disarms by
        calling `set_progress_handler(None, 0)` on the DBAPI connection it
        captured at entry, and `Session.commit()` checks that connection
        back into the pool, which turns SQLAlchemy's proxy for it into a
        dead reference -- disarming against it afterwards raised
        `AttributeError: 'NoneType' object has no attribute
        'set_progress_handler'` (caught while writing this fix's own test).
        `flush()` alone does not release the connection, so committing
        outside the arm loses nothing: the interruptible part -- the
        autoflush of every staged `UPDATE` -- already happened above, and a
        commit with nothing left to flush is an ordinary fast `COMMIT`. A
        tier's commit succeeding here means its work is durable before the
        *next* tier's `session.execute()` runs, which is also what removes
        the cross-tier-autoflush hazard this bug depended on: there is
        nothing of a completed tier's left pending for a later tier to
        autoflush.

        C1 follow-up (2026-08-14): every decision staged above is written
        into a local `_TierTally`, not `report`, for exactly the same
        reason `to_unlink` is local -- an abort during the flush (or, still
        inside the arm, during the `SELECT` itself) must propagate out of
        this method with the tally simply discarded, before it ever reaches
        `report.tier_counts`/`tier_bytes`/`already_missing`/`decisions` (and
        thence `RetentionSweeper.totals`, which feeds the Prometheus
        deletion counters). The merge below only runs once `session.commit()`
        has returned (or, dry run, once staging finished without being
        interrupted) -- see `_TierTally`.
        """
        to_unlink: list[tuple[uuid.UUID, Path]] = []
        tally = _TierTally()
        with self._bounded_statements(session, deadline):
            for asset, detection_id in session.execute(query).all():
                if time.monotonic() >= deadline or budget <= 0:
                    break
                # ADR-074/Task 6: cheap in-memory classification, no extra
                # query. `bank`'s exclusion already kept every BANK verdict
                # out of `query` entirely, so a candidate that reaches this
                # point can only be QUOTA or SAMPLE -- exactly the two
                # branches `evidence_value.classify` falls through to once
                # the BANK checks fail, which is why re-deriving `band` and
                # `is_common` here would answer a question already settled
                # upstream. `None` (no verdict tallied at all) whenever the
                # flag is off, keeping the report's `value_counts`/
                # `value_bytes` empty and the flag inert.
                verdict = (
                    evidence_value.Verdict.SAMPLE
                    if evidence_value.sampled(
                        str(detection_id), self.evidence_sample_permille
                    )
                    else evidence_value.Verdict.QUOTA
                ).value if bank is not None else None
                staged = self._stage_delete(
                    tally,
                    asset,
                    detection_id=detection_id,
                    tier=tier,
                    reason=reason,
                    dry_run=dry_run,
                    verdict=verdict,
                )
                if staged is not None:
                    to_unlink.append(staged)
                budget -= 1
            # Explicit, and still inside the armed block: this is the flush
            # that used to happen implicitly, unarmed, at the start of the
            # *next* tier's query (or not at all until the final commit).
            # Doing it here, ourselves, is what makes it this tier's own
            # concern instead of a later, unrelated caller's.
            session.flush()
        # Outside the arm on purpose -- see the docstring above.
        if not dry_run:
            session.commit()
        # Only reached if nothing above raised -- every row staged above is
        # now durably committed (or, dry run, safely never going to be).
        # This is the merge point: the tally becomes visible on `report`
        # only here, never earlier.
        report.tier_counts[tier] = report.tier_counts.get(tier, 0) + tally.count
        report.tier_bytes[tier] = report.tier_bytes.get(tier, 0) + tally.bytes
        for verdict, count in tally.value_counts.items():
            report.value_counts[verdict] = report.value_counts.get(verdict, 0) + count
        for verdict, nbytes in tally.value_bytes.items():
            report.value_bytes[verdict] = report.value_bytes.get(verdict, 0) + nbytes
        report.already_missing += tally.already_missing
        report.decisions.extend(tally.decisions)
        # Unlinking is pure filesystem I/O, not a database statement, so it
        # has nothing to do with the deadline guard armed above.
        if not dry_run:
            for asset_id, path in to_unlink:
                self._unlink_staged(asset_id, path)
        return budget

    def _strip_native(
        self,
        session: Session,
        report: RetentionReport,
        *,
        now: datetime,
        deadline: float,
        budget: int,
        dry_run: bool,
        held_ids: set[uuid.UUID] | None = None,
        bank: _EvidenceBank | None = None,
    ) -> int:
        cutoff = now - timedelta(days=self.native_days)
        query = (
            select(orm.MediaAsset, orm.Detection.id)
            .join(orm.DetectionMedia, orm.DetectionMedia.media_asset_id == orm.MediaAsset.id)
            .join(orm.Detection, orm.Detection.id == orm.DetectionMedia.detection_id)
            .where(orm.MediaAsset.reclaimed_at.is_(None))
            .where(orm.MediaAsset.kind.in_(NATIVE_KINDS))
            # Bounded and ordered on the *asset's* age, not the detection's
            # (ADR-062). This is what lets `ix_media_asset_live_kind_created`
            # serve the range predicate and the `ORDER BY` together, so the
            # scan stops at `LIMIT` and -- because the index is partial on
            # `reclaimed_at IS NULL` -- never walks a row this sweeper has
            # already reclaimed. See this module's docstring ("Tier age is
            # measured on the asset") for why that substitution is safe.
            .where(orm.MediaAsset.created_at <= cutoff)
            .where(orm.Detection.kept_at.is_(None))
        )
        if held_ids:
            query = query.where(orm.Detection.id.notin_(held_ids))
        # ADR-076: one indexed predicate, in the candidate query where every
        # other exemption lives. `None` when the flag is off, and then this
        # query is byte-identical to the age-only one.
        if bank is not None:
            query = query.where(bank.exclusion())
        query = query.order_by(orm.MediaAsset.created_at.asc()).limit(budget)
        return self._run_tier(
            session,
            report,
            tier="native",
            query=query,
            reason=(
                f"age >= {self.native_days}d: native clip superseded by "
                "audible-only tier"
            ),
            deadline=deadline,
            budget=budget,
            dry_run=dry_run,
            bank=bank,
        )

    def _strip_unkept(
        self,
        session: Session,
        report: RetentionReport,
        *,
        now: datetime,
        deadline: float,
        budget: int,
        dry_run: bool,
        held_ids: set[uuid.UUID] | None = None,
        bank: _EvidenceBank | None = None,
    ) -> int:
        cutoff = now - timedelta(days=self.audible_only_days)
        query = (
            select(orm.MediaAsset, orm.Detection.id)
            .join(orm.DetectionMedia, orm.DetectionMedia.media_asset_id == orm.MediaAsset.id)
            .join(orm.Detection, orm.Detection.id == orm.DetectionMedia.detection_id)
            .where(orm.MediaAsset.reclaimed_at.is_(None))
            # Asset age, not detection age -- see `_strip_native` and ADR-062.
            # Served by `ix_media_asset_live_created` (kind-agnostic, so the
            # narrower `(kind, created_at)` index does not apply here).
            .where(orm.MediaAsset.created_at <= cutoff)
            .where(orm.Detection.kept_at.is_(None))
        )
        if held_ids:
            query = query.where(orm.Detection.id.notin_(held_ids))
        # ADR-076 -- see `_strip_native`.
        if bank is not None:
            query = query.where(bank.exclusion())
        query = query.order_by(orm.MediaAsset.created_at.asc()).limit(budget)
        return self._run_tier(
            session,
            report,
            tier="unkept",
            query=query,
            reason=f"age >= {self.audible_only_days}d and not kept",
            deadline=deadline,
            budget=budget,
            dry_run=dry_run,
            bank=bank,
        )

    # -- what has been banked (ADR-076) -------------------------------------

    def _evidence_bank(self, session: Session) -> _EvidenceBank | None:
        """Per-species and per-band banked counts: **one** pass over the bank.

        No TTL, no cached census, no abort handling -- all three existed to
        survive an 18.32 s query that no longer exists. `detection.banked_at`
        (migration 0012) is a column, not a derived fact, so membership no
        longer has to be re-derived from a census of the whole archive every
        sweep -- it is read straight off the rows the bank actually holds.

        One query rather than two aggregates, and the reason is which number
        the cost scales with. The obvious band aggregate --
        ``WHERE banked_at IS NOT NULL AND taxonomic_group = 'bat'`` -- looks
        cheaper and is not: ``ix_detection_group_start`` makes
        ``taxonomic_group`` an equality SQLite can seek, so the planner takes
        that index and then needs a row lookup per **bat detection** to test
        ``banked_at``. Measured: it touches all 66,902 bat rows whether the
        bank holds seven thousand or seven, and it would grow with the archive
        for ever. That is the exact property this ADR exists to remove from
        the sweep.

        Selecting only on ``banked_at IS NOT NULL`` puts the planner on
        ``ix_detection_banked_partial`` (confirmed: ``SCAN detection USING
        INDEX ix_detection_banked_partial``), so the work is bounded by the
        **bank** -- 7,302 rows, itself bounded by ``bank_size`` times the
        species count -- and aggregating two dicts from those rows in Python
        costs nothing worth measuring.
        """
        if not self.evidence_value_enabled:
            return None
        banked: dict[str, int] = {}
        bands: dict[int, int] = {}
        rows = session.execute(
            select(
                orm.Detection.common_name,
                orm.Detection.taxonomic_group,
                orm.Detection.peak_frequency_hz,
            ).where(orm.Detection.banked_at.is_not(None))
        ).all()
        for common_name, group, peak_hz in rows:
            if common_name is not None:
                banked[common_name] = banked.get(common_name, 0) + 1
            if group == _BAT_GROUP:
                # Same bucketing as the SQL `_band_expression`, and the same
                # `None` for a missing or non-positive frequency.
                edge = evidence_value.frequency_band(peak_hz)
                if edge is not None:
                    bands[edge] = bands.get(edge, 0) + 1
        return _EvidenceBank(banked=banked, bands=bands)

    #: Detections promoted into the bank in one sweep. Bounded so promotion can
    #: never be the thing that eats a sweep's budget: the bank fills over a few
    #: sweeps instead of one, and `oo retention bank-backfill` exists for the
    #: one-off case where filling it now actually matters.
    _PROMOTE_PER_SWEEP = 200

    def _promote_to_bank(
        self,
        session: Session,
        bank: _EvidenceBank,
        *,
        now: datetime,
        deadline: float,
        limit: int = _PROMOTE_PER_SWEEP,
    ) -> int:
        """Bank the oldest live, unbanked detections of every under-cap species.

        **Oldest first, and that is the whole point.** ADR-074's bank deleted
        the first-ever recording of a species the moment that species reached
        its cap, because membership was a boolean on the *species* and the age
        tiers order `created_at ASC`. Promoting oldest-first and never demoting
        inverts that: the earliest recording is the first thing protected and
        the last thing at risk.

        A species at its cap is excluded **in the candidate query itself**,
        not filtered out afterwards. That is what keeps this affordable: the
        candidate set is `LIMIT`ed, and the commonest species are exactly the
        ones that would otherwise fill it with rows the policy will never bank.

        What this deliberately does **not** do is find the oldest unbanked
        detection in the whole archive -- that question cannot be asked inside
        the budget (see `_promotion_candidates`). It promotes from a trailing
        window; `oo retention bank-backfill` does the archive, oldest-first,
        with no budget at all.
        """
        promoted = 0
        common = {n.casefold() for n in self.evidence_common_species}
        implausible = {n.casefold() for n in self.evidence_implausible_species}
        policy = evidence_value.Policy(
            bank_size=self.evidence_bank_size,
            sample_permille=self.evidence_sample_permille,
            implausible_cap=self.evidence_implausible_cap,
        )

        # Species that cannot take another, computed once and pushed into the
        # candidate query. `cap_for` returns 0 for a common species, so the
        # common list needs no separate handling here.
        skip = {
            species
            for species, banked in bank.banked.items()
            if banked >= evidence_value.cap_for(
                species,
                is_common=species.casefold() in common,
                is_implausible=species.casefold() in implausible,
                policy=policy,
            )
        }
        skip |= set(self.evidence_common_species)

        room_by_species: dict[str, int] = {}
        for detection_id, species in self._promotion_candidates(
            session, now=now, skip=skip, limit=limit * 2
        ):
            if promoted >= limit or time.monotonic() >= deadline:
                break
            if species not in room_by_species:
                cap = evidence_value.cap_for(
                    species,
                    is_common=species.casefold() in common,
                    is_implausible=species.casefold() in implausible,
                    policy=policy,
                )
                room_by_species[species] = cap - bank.banked.get(species, 0)
            if room_by_species[species] <= 0:
                continue
            if not self._has_live_evidence(session, detection_id):
                continue
            session.execute(
                sa_update(orm.Detection)
                .where(orm.Detection.id == detection_id)
                .values(banked_at=now)
            )
            room_by_species[species] -= 1
            promoted += 1

        # No commit in this method, on purpose: the whole call is made from
        # inside `sweep()`'s own `_bounded_statements` arming, and
        # `Session.commit()` checks the armed DBAPI connection back into the
        # pool -- turning SQLAlchemy's proxy for it into a dead reference, so
        # that arming's `finally` then calls `set_progress_handler` on
        # nothing and raises `AttributeError: 'NoneType' object has no
        # attribute 'set_progress_handler'`. Same failure `_run_tier`
        # documents for its own tier loop. `sweep()` commits once this call
        # returns and its arming has exited, exactly as it does for every
        # other tier.
        return promoted

    def _promotion_candidates(
        self, session: Session, *, now: datetime, skip: set[str], limit: int
    ) -> list[tuple[uuid.UUID, str]]:
        """Recent unbanked detections whose species could still take one.

        **No join to the media tables, and that is the entire reason this is
        affordable.** The obvious spelling -- group live assets by species --
        is the 18.32 s census again in a different shape: SQLite scans
        `detection_media` whole regardless of any time bound on `detection`,
        because the bound is on the wrong side of the join. Measured on the
        station's own cardinalities: 5.9754 s unbounded, and still **2.6672 s
        bounded to two hours**. Bounding the window does not help.

        This asks a cheaper question instead. A detection created moments ago
        always has live evidence -- its clips were just written -- so the
        incremental case does not need to ask whether it does. Liveness is
        checked in `_promote_to_bank` on the handful actually about to be
        promoted, one PK-prefix seek each.

        `skip` carries the species that cannot take another detection: the
        common list, and every species already at its cap. Excluded **in SQL**
        rather than in Python, because the candidate set is `LIMIT`ed and the
        commonest species are exactly the ones that would otherwise fill it --
        a window full of robins the policy will never bank, re-scanned every
        300 s, while a heron three rows further along is never seen.

        `common_name NOT IN (...)` is safe here only because of the
        `IS NOT NULL` guard above it: SQL three-valued logic makes
        `NULL NOT IN (...)` evaluate to NULL, not true, and a `WHERE` keeps
        only rows that are true. Without that guard every bat pass would
        silently vanish from this query. Same trap, same place, as
        `_EvidenceBank.exclusion` under ADR-074.

        Measured: **0.0149 s** for a 24-hour window at a 500-row limit,
        `SEARCH detection USING INDEX ix_detection_event_start_utc`.
        """
        query = (
            select(orm.Detection.id, orm.Detection.common_name)
            .where(orm.Detection.event_start_utc >= now - self.promotion_lookback)
            .where(orm.Detection.banked_at.is_(None))
            .where(orm.Detection.common_name.is_not(None))
            .order_by(orm.Detection.event_start_utc.asc())
            .limit(limit)
        )
        if skip:
            query = query.where(orm.Detection.common_name.notin_(sorted(skip)))
        return [(i, n) for i, n in session.execute(query).all() if n is not None]

    def _has_live_evidence(self, session: Session, detection_id: uuid.UUID) -> bool:
        """One PK-prefix seek plus one PK lookup. Measured at 0.46 ms.

        Banking a detection whose clips are already gone protects nothing and
        consumes a species' slot **permanently** -- promotion is monotone and
        never reconsiders. So the check is worth its cost, and its cost is only
        paid for detections that have already survived the cap filter.
        """
        return session.execute(
            select(orm.DetectionMedia.detection_id)
            .join(orm.MediaAsset, orm.MediaAsset.id == orm.DetectionMedia.media_asset_id)
            .where(orm.DetectionMedia.detection_id == detection_id)
            .where(orm.MediaAsset.reclaimed_at.is_(None))
            .limit(1)
        ).first() is not None

    def _band_counts(self, session: Session, *, since: datetime) -> dict[int, int]:
        """Bat passes per 5 kHz band since `since`: one indexed aggregate.

        Bat passes are never given a species, by design, so rarity cannot
        come from a name; it comes from peak frequency (ADR-074). The
        distribution is as skewed as the birds' -- 20-25 kHz held 36,180 of
        66,485 passes on 2026-08-29 and 60-65 kHz held four -- so a band's
        share of the trailing window is what decides whether it is kept
        whole.

        Index-served end to end, unlike the old per-species census this
        module used to also run: ``ix_detection_group_start`` is exactly
        ``(taxonomic_group, event_start_utc)``, so the equality and the range
        are one seek plus a walk of the window, and nothing outside it is
        touched however long the station has been running. The grouping is a
        computed expression, but only over the rows the window already
        narrowed to, into at most a dozen or so buckets.

        Counted on detections rather than on clips on purpose: this measures
        what flew, and detection metadata is kept forever (ADR-026), so the
        answer does not move when a clip is reclaimed. It is the *shape* of
        the distribution, not an inventory of disk.
        """
        band = _band_expression().label("band")
        rows = session.execute(
            select(band, func.count())
            .where(orm.Detection.taxonomic_group == _BAT_GROUP)
            .where(orm.Detection.event_start_utc >= since)
            # Matches `frequency_band`, which is None for a missing or
            # non-positive frequency: no band, so no bank.
            .where(orm.Detection.peak_frequency_hz.is_not(None))
            .where(orm.Detection.peak_frequency_hz > 0)
            .group_by(band)
        ).all()
        return {int(edge): count for edge, count in rows if edge is not None}

    def _disk_over_watermark(self) -> bool:
        """Whether the clip filesystem is already past the watermark.

        Cheap enough to ask every sweep -- one `statvfs` -- and asking it up
        front is what lets `sweep` promote the watermark tier ahead of the age
        tiers when it matters. `_watermark_reclaim` re-reads usage itself and
        returns immediately if this was a false alarm, so a stale answer here
        costs nothing but the second call.
        """
        ratio = self._disk_used_ratio()
        return ratio is not None and ratio > self.watermark_ratio

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
            # An upper bound of "now" excludes nothing -- an asset stamped in
            # the future is not something this tier should be reclaiming
            # anyway -- but it is not decoration. Without a range predicate on
            # `created_at` the planner has no reason to enter
            # `ix_media_asset_live_created` and instead scanned
            # `detection_media` whole and sorted the result: 1.8048 s against
            # 0.0032 s with this line. ADR-062.
            .where(orm.MediaAsset.created_at <= self._clock())
            .where(orm.Detection.kept_at.is_(None))
            .order_by(orm.MediaAsset.created_at.asc())
            .limit(budget)
        )
        reason = (
            f"disk usage {ratio:.1%} exceeds watermark "
            f"{self.watermark_ratio:.0%}: oldest-first reclaim, tier "
            "ignored, but kept recordings are never reclaimed"
        )
        to_unlink: list[tuple[uuid.UUID, Path]] = []
        tally = _TierTally()
        with self._bounded_statements(session, deadline):
            for asset, detection_id in session.execute(query).all():
                if time.monotonic() >= deadline or budget <= 0 or freed >= bytes_over:
                    break
                staged = self._stage_delete(
                    tally,
                    asset,
                    detection_id=detection_id,
                    tier="watermark",
                    reason=reason,
                    dry_run=dry_run,
                )
                if staged is not None:
                    to_unlink.append(staged)
                freed += asset.byte_length
                budget -= 1
            session.flush()
        # `commit()` runs outside the arm, same reason as `_run_tier` (see
        # its docstring): committing inside would check the captured DBAPI
        # connection back into the pool before `_bounded_statements`'
        # `finally` tries to disarm the (by then stale) reference to it.
        if not dry_run:
            session.commit()
        # Merge point (C1 follow-up, see `_run_tier`'s docstring): only
        # reached once the commit above has returned (or, dry run, once
        # staging finished without an abort), so an interrupted watermark
        # pass contributes nothing to `report`.
        report.tier_counts["watermark"] = report.tier_counts.get("watermark", 0) + tally.count
        report.tier_bytes["watermark"] = report.tier_bytes.get("watermark", 0) + tally.bytes
        report.already_missing += tally.already_missing
        report.decisions.extend(tally.decisions)
        if not dry_run:
            for asset_id, path in to_unlink:
                self._unlink_staged(asset_id, path)

        # I3 (final pre-merge review, 2026-08-14): this used to run *before*
        # the reclaim loop above -- the observability query ahead of the
        # work it observes, the same structural mistake this whole branch
        # exists to correct (ADR-061's own preamble/statement-timeout
        # lesson), and it only ever runs on the one tick that least wants an
        # extra unbounded-feeling query: disk already over the watermark.
        # Now run after the reclaim above, so an abort here costs only the
        # reporting figure, never the reclaim itself.
        #
        # Measured (`EXPLAIN QUERY PLAN`, a 119,476-media_asset /
        # 46,000-detection / 112-kept synthetic fixture matching the
        # station's own counts, no `ANALYZE` run -- SQLite has no stats
        # table without one, which is the state a station actually runs in):
        # rewriting which table the ORM `select()` names first
        # (`select_from(Detection)` vs. `select_from(MediaAsset)`) changes
        # nothing -- SQLite's cost-based planner reorders joins on its own
        # estimates regardless of `FROM`-clause order, and both shapes
        # compiled to the identical plan, `SCAN detection_media`, **0.20 s**,
        # scaling with every live asset. `Session.with_hint()` compiles to
        # nothing on SQLAlchemy's SQLite dialect (confirmed: no
        # `get_select_hint_text` override exists), so the only way to force
        # the planner onto `ix_detection_kept_at_partial` is SQLite's own
        # `INDEXED BY` syntax, which SQLAlchemy has no portable spelling
        # for -- hence the raw `text()` below, gated to the SQLite dialect,
        # with the portable ORM query kept for anything else (PostgreSQL,
        # ADR-007, has no `INDEXED BY` equivalent and its planner is
        # cost-based enough not to need one). Measured with the hint:
        # `SEARCH detection USING INDEX ix_detection_kept_at_partial
        # (kept_at>?)`, **0.0004 s** -- about 500x, and scaling with the 112
        # kept rows the query actually cares about, not the 119,476 live
        # ones. See `docs/architecture/ADRS.md` ADR-061 fourth correction.
        if time.monotonic() < deadline:
            try:
                with self._bounded_statements(session, deadline):
                    if session.get_bind().dialect.name == "sqlite":
                        kept_bytes_query: Any = sa_text(
                            "SELECT coalesce(sum(media_asset.byte_length), 0) "
                            "FROM detection INDEXED BY ix_detection_kept_at_partial "
                            "JOIN detection_media "
                            "ON detection_media.detection_id = detection.id "
                            "JOIN media_asset "
                            "ON media_asset.id = detection_media.media_asset_id "
                            "WHERE detection.kept_at IS NOT NULL "
                            "AND media_asset.reclaimed_at IS NULL"
                        )
                    else:
                        kept_bytes_query = (
                            select(func.coalesce(func.sum(orm.MediaAsset.byte_length), 0))
                            .select_from(orm.Detection)
                            .join(
                                orm.DetectionMedia,
                                orm.DetectionMedia.detection_id == orm.Detection.id,
                            )
                            .join(
                                orm.MediaAsset,
                                orm.MediaAsset.id == orm.DetectionMedia.media_asset_id,
                            )
                            .where(orm.Detection.kept_at.is_not(None))
                            .where(orm.MediaAsset.reclaimed_at.is_(None))
                        )
                    report.watermark_blocked_by_kept = int(
                        session.execute(kept_bytes_query).scalar_one()
                    )
            except OperationalError as exc:
                if "interrupted" not in str(getattr(exc, "orig", exc)):
                    raise
                # The reclaim above already ran and already committed --
                # only this reporting figure is stale. Left at its previous
                # value (0 on a sweep that never got this far before) rather
                # than guessed at.
                log.warning("retention.watermark_blocked_by_kept_query_interrupted")
        return budget

    # -- mechanics ------------------------------------------------------

    def _stage_delete(
        self,
        tally: _TierTally,
        asset: orm.MediaAsset,
        *,
        detection_id: uuid.UUID | None,
        tier: str,
        reason: str,
        dry_run: bool,
        verdict: str | None = None,
    ) -> tuple[uuid.UUID, Path] | None:
        """Record a deletion decision and mark the row reclaimed -- but never
        touch the filesystem. Returns the asset id and path still needing an
        `_unlink_staged()` call once this tier's transaction is durably
        committed, or `None` when there is nothing to unlink (dry run, or the
        file was already gone).

        Writes into the caller's local `tally`, never into the sweep's
        `RetentionReport` directly (C1, 2026-08-14): the report must only
        learn about a tier's deletions after that tier's own commit has
        returned (see `_TierTally`), and this method has no way to know,
        from inside the loop, whether the statement it is part of is about
        to be aborted.

        **C1, final pre-merge review (2026-08-14): row-committed-before-
        unlinked, on purpose, replacing unlink-then-mark-then-commit-much-
        later.** The previous order unlinked the file first and left
        `reclaimed_at` staged in the ORM, to be persisted either by the next
        tier's autoflush or by `sweep()`'s single commit at the very end.
        Measured, with real files, over 8,000 candidates: whenever the
        per-statement deadline guard (`_bounded_statements`) happened to
        abort mid-*flush* rather than mid-*read* --

            budget=0.467s -> PendingRollbackError; rows marked reclaimed=0; files gone=8000
            budget=0.518s -> ok deleted=7400;      rows marked reclaimed=7400; files gone=7400
            budget=0.529s -> PendingRollbackError; rows marked reclaimed=0; files gone from disk=8000

        `PendingRollbackError` is an `InvalidRequestError`, not an
        `OperationalError`, raised by the *later*, unrelated
        `session.commit()` -- by which point the files named by the failed
        flush were already unlinked. That is ADR-057's defect reintroduced
        verbatim: a live row (`reclaimed_at IS NULL`) naming a file that is
        not on disk, undetectable except by the reconciliation command that
        incident needed. A comment used to sit here asserting the opposite
        as settled fact ("there is nothing of its own to roll back"); it was
        wrong, and this replaces it with the measurement.

        Committing the row *before* unlinking means an abort here can only
        ever cost a **deletion** -- the file stays on disk, retried next
        sweep -- never a **record of one**. ADR-057 already answered which
        of those two failures this project prefers: a live row whose file
        happens to still be present is a few wasted kilobytes, discoverable
        by `find_orphans`; a live row whose file is gone is undetectable
        evidence loss, which is what actually happened and took a dedicated
        reconciliation command to fix. See `_unlink_staged` for the other
        half -- what happens if the unlink itself fails *after* the commit
        that authorised it.
        """
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
        tally.decisions.append(decision)
        log.info(
            "retention.would_delete" if dry_run else "retention.staged_delete",
            asset_id=str(asset.id),
            detection_id=str(detection_id) if detection_id else None,
            kind=asset.kind,
            tier=tier,
            reason=reason,
            bytes=size,
            path=str(path),
            existed_on_disk=existed,
        )

        tally.count += 1
        tally.bytes += size if existed else 0
        if not existed:
            tally.already_missing += 1
        if verdict is not None:
            tally.value_counts[verdict] = tally.value_counts.get(verdict, 0) + 1
            tally.value_bytes[verdict] = tally.value_bytes.get(verdict, 0) + (
                size if existed else 0
            )

        # Staged in-memory either way -- dry run or real -- so a later
        # tier's candidate query (`reclaimed_at IS NULL`) never re-offers,
        # and double-counts, an asset this sweep already decided on.
        asset.reclaimed_at = self._clock()
        asset.reclaim_reason = tier

        if dry_run or not existed:
            return None
        return asset.id, path

    def _unlink_staged(self, asset_id: uuid.UUID, path: Path) -> None:
        """Remove a file whose row is already durably committed `reclaimed`.

        Called only after the tier's transaction has committed (see
        `_run_tier`), so a failure here -- the file already gone, a
        permissions error -- leaves an **orphan**: a reclaimed row whose file
        still happens to exist. That is the failure direction `_stage_delete`
        chooses on purpose (see its docstring); the row is not, and cannot
        be, un-committed from here to "retry next sweep" the way the old
        unlink-first order could. An orphan wastes disk space; it does not
        lie about evidence being present. It is **not** discoverable by
        `find_orphans`: that scan's known-set (`select(MediaAsset.storage_uri)`)
        has no `reclaimed_at` filter, so a reclaimed-but-still-present row
        counts as "known" and its file is never yielded. Nothing in this
        codebase currently detects this direction -- the ADR-057 rolling
        audit and the storage endpoints only ever scan `reclaimed_at IS
        NULL` rows, which this row, by definition, is not.
        """
        try:
            path.unlink()
        except OSError as exc:
            log.warning(
                "retention.unlink_failed",
                asset_id=str(asset_id),
                path=str(path),
                error=str(exc),
            )
            return
        parent = path.parent
        if parent != self.clip_dir and parent.is_dir():
            try:
                if not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass

    def _disk_used_ratio(self) -> float | None:
        try:
            usage = shutil.disk_usage(self.clip_dir)
        except OSError:
            return None
        if usage.total == 0:
            return None
        return round(1.0 - usage.free / usage.total, 4)

    # -- rolling missing-file audit (ADR-057) -----------------------------

    def audit_missing_files(self, *, batch: int | None = None) -> dict[str, Any]:
        """Stat a small, bounded slice of live rows; report how many files are gone.

        **The problem this exists for.** A ``media_asset`` row with
        ``reclaimed_at IS NULL`` is the database asserting that evidence
        exists. On the live station 8,067 such rows (16.5%, 20.59 GB) named
        files that had been unlinked by `clips.ClipManager.enforce_retention`
        without any row being marked, and nobody found out for five days --
        there was no way to find out short of an operator running a command
        (ADR-057). This makes the answer arrive on its own.

        **Why a rolling sample and not a census.** Statting every live row is
        cheap in isolation -- 48,989 rows measured at 0.27 s on the target --
        but 0.27 s is the same order as the ORM sweep ADR-033 had to pace to
        300 s after it starved the event loop and cost ~1.9 capture gaps per
        minute. Capture always wins, so this walks a fixed ``batch`` (default:
        the sweeper's own ``batch_size``, 200) per call, cursored on the
        indexed ``created_at`` column, wrapping to the start when it runs off
        the end. 200 stats is ~1 ms of the same measurement, on a call that
        already happens every 300 s; a full pass over ~50k live rows completes
        in about 20 hours.

        The exact answer is therefore `last_pass_missing` out of
        `last_pass_scanned`, refreshed once per pass, with `audit_missing` /
        `audit_scanned` as the partial tally in between. Reporting both is
        deliberate: a sample of 3% finding zero is not the same claim as a
        completed pass finding zero, and the honesty constraint is that a
        number means what its label says.

        Never deletes anything and never marks a row -- reconciliation is
        `media_repair.apply_missing_reconciliation`, run by an operator with
        ``oo clips reconcile-missing --apply``. This only counts.
        """
        size = batch or self.batch_size
        started = time.monotonic()
        with self._session_factory() as session:
            query = select(
                orm.MediaAsset.created_at,
                orm.MediaAsset.id,
                orm.MediaAsset.storage_uri,
                orm.MediaAsset.byte_length,
            ).where(orm.MediaAsset.reclaimed_at.is_(None))
            if self.audit_cursor is not None:
                query = query.where(
                    tuple_(orm.MediaAsset.created_at, orm.MediaAsset.id) > self.audit_cursor
                )
            rows = session.execute(
                query.order_by(
                    orm.MediaAsset.created_at.asc(), orm.MediaAsset.id.asc()
                ).limit(size)
            ).all()

        for created_at, asset_id, storage_uri, byte_length in rows:
            self.audit_cursor = (created_at, asset_id)
            self.audit_scanned += 1
            if not Path(storage_uri).exists():
                self.audit_missing += 1
                self.audit_missing_bytes += int(byte_length or 0)

        if len(rows) < size:
            # Ran off the end: this pass has seen every live row, so its
            # totals are exact. Start the next one from the beginning.
            self.audit_passes += 1
            self.last_pass_scanned = self.audit_scanned
            self.last_pass_missing = self.audit_missing
            self.last_pass_missing_bytes = self.audit_missing_bytes
            self.audit_cursor = None
            self.audit_scanned = 0
            self.audit_missing = 0
            self.audit_missing_bytes = 0

        self.last_audit_at = self._clock()
        self.last_audit_duration_s = round(time.monotonic() - started, 4)
        if self.audit_missing or self.last_pass_missing:
            log.warning(
                "retention.audit_missing_files",
                pass_missing=self.last_pass_missing,
                pass_scanned=self.last_pass_scanned,
                partial_missing=self.audit_missing,
                partial_scanned=self.audit_scanned,
                passes=self.audit_passes,
            )
        return self.audit_snapshot()

    def audit_snapshot(self) -> dict[str, Any]:
        """The missing-file audit's state, for `/health`, `/metrics` and the CLI."""
        return {
            "passes_completed": self.audit_passes,
            "last_pass_scanned": self.last_pass_scanned,
            "last_pass_missing": self.last_pass_missing,
            "last_pass_missing_bytes": self.last_pass_missing_bytes,
            "in_progress_scanned": self.audit_scanned,
            "in_progress_missing": self.audit_missing,
            "in_progress_missing_bytes": self.audit_missing_bytes,
            "last_audit_at": self.last_audit_at.isoformat() if self.last_audit_at else None,
            "last_audit_duration_s": self.last_audit_duration_s,
        }

    @property
    def known_missing(self) -> int:
        """Rows currently known to claim evidence that is not on disk.

        The last completed pass's exact figure once there has been one, and
        the running tally of the first (incomplete) pass before that. Never a
        figure extrapolated from a partial sample onto the whole table: an
        estimate presented as a count is the failure mode the charter's
        honesty constraint names directly.
        """
        return self.last_pass_missing if self.audit_passes else self.audit_missing

    @property
    def known_missing_bytes(self) -> int:
        """Bytes those rows claim, on the same basis as `known_missing`.

        This is what the storage panel and the retention budget were counting
        as reclaimable and could never have recovered, because there is
        nothing there to unlink.
        """
        return (
            self.last_pass_missing_bytes if self.audit_passes else self.audit_missing_bytes
        )

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
            "watermark_ratio": self.watermark_ratio,
            "batch_size": self.batch_size,
            "batch_budget_s": self.batch_budget_s,
            "last_sweep_at": self.last_sweep_at.isoformat() if self.last_sweep_at else None,
            "last_sweep_duration_s": self.last_sweep_duration_s,
            "last_sweep_complete": self.last_sweep_complete,
            "consecutive_barren_sweeps": self.consecutive_barren_sweeps,
            "last_preamble_s": self.last_preamble_s,
            "last_tiers_skipped": list(self.last_tiers_skipped),
            "last_interrupted_tier": self.last_interrupted_tier,
            "last_interrupted_after_s": self.last_interrupted_after_s,
            "last_disk_used_ratio": self.last_disk_used_ratio,
            "kept_detections": self.last_kept_detections,
            "held_detections": self.last_held_detections,
            "watermark_blocked_by_kept": self.last_watermark_blocked_by_kept,
            # ADR-076. Replaces ADR-074's census-timing figures
            # (`last_census_duration_s`, `census_age_s`, `census_aborts`):
            # there is no census left to time or abort, so the operator-facing
            # question moves from "is the census keeping up" to "what is the
            # bank actually holding". `bank_size_now` is `_EvidenceBank.total()`
            # from the last sweep, 0 while the flag is off; `promoted_last_sweep`
            # is `RetentionReport.promoted` from the last sweep, also 0 while
            # the flag is off.
            "evidence_value_enabled": self.evidence_value_enabled,
            "bank_size_now": self.last_bank_size,
            "promoted_last_sweep": self.promoted_last_sweep,
            "totals": dict(self.totals),
            # ADR-057. Named "missing_audit", not folded into `totals`: these
            # count rows whose file vanished *without* a retention decision,
            # which is a different fact from anything this sweeper deleted.
            "missing_audit": self.audit_snapshot(),
            "known_missing": self.known_missing,
            "known_missing_bytes": self.known_missing_bytes,
        }
