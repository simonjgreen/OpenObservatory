"""Operator pause: the charter's privacy constraint, made operable (ADR-055).

The charter says a microphone in a garden records "neighbours, visitors and
passers-by who never consented". Until now that was honoured by what the
station *keeps* -- no continuous speech, no clip for a human sound class
(ADR-049), bounded evidence retention (ADR-026). None of it helps an operator
who knows, in advance, that the garden is about to be full of other people's
children for an afternoon. This module is the control for that case: a pause,
with a duration, that the operator can set in one click and forget.

Four properties, and every one of them is a failure mode this module exists to
prevent rather than a feature:

**It expires by itself.** The operator will forget. A pause that outlives the
party costs a night of bat data, which is a worse outcome than the problem it
solved. Expiry is therefore *read-side and unconditional*: :attr:`active` is a
comparison against a stored deadline, so a pause ends at its deadline even if
the housekeeping loop, the database and the API are all broken. Nothing has to
fire for a pause to end.

**It survives a restart.** The Pi may reboot mid-party. What is persisted is
the **deadline**, never a countdown -- a remaining-seconds figure written to
disk is wrong the moment the process stops, and would silently extend every
pause by the length of the outage.

**It is recorded, not merely absent.** Charter item 2 makes "a quiet night
versus a dead microphone" a first-class distinction, and an operator pause is a
third thing that must not be mistaken for either. Each pause is a row, opened
when it starts and closed when it ends, so coverage and history can draw the
window as deliberate rather than as an unexplained hole.

**Capture is not stopped.** Deliberately, and this is the trade this module
makes: the ALSA device stays open. Closing it risks it not coming back -- that
is what cost this station 29 hours of recording (HANDOVER §3a) -- and a privacy
control that occasionally bricks the station is worse than the exposure it
prevents. The ring buffer is transient process memory that is overwritten
continuously and never leaves the process, so keeping capture alive retains
nothing. What the pause stops is every path by which audio or a claim about it
*escapes*: detection rows, evidence clips, the event bus (and so MQTT and the
counter-top display), and live listening.

Deliberately free of FastAPI and SQLAlchemy sessions beyond the callable it is
handed, so all of it is exercised without a server.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

log = structlog.get_logger(__name__)

#: Longest pause the API will accept. A day is already far beyond the case this
#: exists for; anything longer is better expressed by stopping the service,
#: where it cannot be forgotten about silently.
MAX_PAUSE_SECONDS = 24 * 3600

#: Reasons a pause row is closed. Recorded so the history view can tell an
#: operator who came back early from one that simply ran out.
ENDED_EXPIRED = "expired"
ENDED_RESUMED = "resumed"
ENDED_SUPERSEDED = "superseded"
ENDED_UNKNOWN = "unknown"


@dataclass(frozen=True)
class PausePreset:
    """One entry in the split button's drop-down.

    ``seconds is None`` means "until local midnight", which cannot be a fixed
    number of seconds: it depends on the station's configured zone and on the
    time of day the operator presses it. Resolving it on the station rather
    than in the browser is deliberate -- the browser's zone is whichever
    laptop happens to be open, and the station's zone is the one the whole
    system already presents times in.
    """

    key: str
    label: str
    seconds: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "seconds": self.seconds}


#: The durations offered, in the order the drop-down lists them. The set an
#: operator can choose from is a setting (``pause_presets``, ADR-048); this is
#: the catalogue those keys are resolved against, because a duration the
#: station does not know how to compute is not a value a form may invent.
PRESETS: tuple[PausePreset, ...] = (
    PausePreset("15m", "15 minutes", 15 * 60),
    PausePreset("1h", "1 hour", 3600),
    PausePreset("3h", "3 hours", 3 * 3600),
    PausePreset("6h", "6 hours", 6 * 3600),
    PausePreset("until-midnight", "until midnight", None),
)

PRESETS_BY_KEY: dict[str, PausePreset] = {preset.key: preset for preset in PRESETS}


class PauseError(ValueError):
    """A pause request that cannot be honoured, with a message for the operator."""


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        # A misconfigured zone must not stop a privacy control from working.
        # UTC is wrong for "midnight" but it is never *unavailable*, and the
        # settings page already reports an invalid zone.
        log.warning("pause.timezone_unusable", timezone=timezone)
        return ZoneInfo("UTC")


def next_local_midnight(now: datetime, timezone: str) -> datetime:
    """The next local midnight after ``now``, as a UTC instant.

    "Until midnight" means the end of the operator's day, not 24 hours. Pressed
    at 23:58 it is two minutes, which is correct and is why the API also
    enforces a floor -- see :func:`resolve`.
    """
    local = now.astimezone(_zone(timezone))
    tomorrow = (local + timedelta(days=1)).date()
    midnight = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=local.tzinfo)
    return midnight.astimezone(UTC)


def resolve(
    preset_key: str, *, timezone: str, now: datetime | None = None
) -> tuple[datetime, PausePreset]:
    """Turn a preset key into the UTC instant the pause ends at.

    Raises :class:`PauseError` for a key this station does not offer, rather
    than falling back to a default: a pause is a privacy action, and silently
    substituting a *different* duration for the one that was asked for is
    exactly the kind of quiet disagreement this control cannot afford.
    """
    preset = PRESETS_BY_KEY.get(preset_key)
    if preset is None:
        raise PauseError(
            f"unknown pause duration {preset_key!r}; "
            f"expected one of {', '.join(PRESETS_BY_KEY)}"
        )
    moment = now or datetime.now(UTC)
    if preset.seconds is None:
        ends = next_local_midnight(moment, timezone)
    else:
        ends = moment + timedelta(seconds=preset.seconds)
    if ends <= moment:
        raise PauseError(f"{preset.label} resolves to a time in the past")
    if (ends - moment).total_seconds() > MAX_PAUSE_SECONDS:
        raise PauseError(f"{preset.label} exceeds the {MAX_PAUSE_SECONDS}s maximum pause")
    return ends, preset


def available_presets(configured: tuple[str, ...] | list[str]) -> list[PausePreset]:
    """The presets this station offers, in the configured order.

    Unknown keys are dropped rather than raising: a typo in ``pause_presets``
    must not take the whole control away, and the settings API validates the
    field on the way in anyway.
    """
    offered = [PRESETS_BY_KEY[key] for key in configured if key in PRESETS_BY_KEY]
    return offered or list(PRESETS)


#: Type of the ``session_scope``-shaped callable this module is handed. Kept
#: structural so tests can pass a throwaway factory.
SessionFactory = Callable[[], AbstractContextManager[Any]]


class PauseController:
    """The live pause state, and the only thing allowed to change it.

    Reads are the hot half. :attr:`active` is checked once per capture block
    (to decide whether live audio may leave the process) and once per detection
    (to decide whether anything is recorded), so it is a float comparison
    against a cached deadline and touches neither the database nor a lock.
    Writes are rare -- an operator pressing a button -- and are the only place
    the database is involved.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        timezone_provider: Callable[[], str] = lambda: "UTC",
        station_id_provider: Callable[[], uuid.UUID | None] = lambda: None,
    ) -> None:
        self._session_factory = session_factory
        self._timezone = timezone_provider
        self._station_id = station_id_provider
        #: The deadline, as a Unix timestamp. Zero means "not paused". This is
        #: the single value every hot-path read consults.
        self._ends_epoch: float = 0.0
        self._ends_utc: datetime | None = None
        self._started_utc: datetime | None = None
        self._preset_key: str = ""
        self._label: str = ""
        self._actor: str = ""
        self._row_id: uuid.UUID | None = None
        #: Process-lifetime counters, surfaced in the station snapshot so a
        #: privacy control's effect is visible rather than merely promised
        #: (the same reasoning as ADR-049's ``clips.skipped_human_audio``).
        self.detections_suppressed = 0
        self.pauses_started = 0

    # -- the hot read ---------------------------------------------------

    @property
    def active(self) -> bool:
        """Whether the station is currently paused.

        Expiry lives here and nowhere else. There is no timer to miss, no task
        to crash and no row to update before a pause ends -- past the deadline
        this is False, and every gate that consults it reopens in the same
        instant.
        """
        return self._ends_epoch > time.time()

    def note_suppressed(self, count: int = 1) -> None:
        self.detections_suppressed += count

    @property
    def ends_utc(self) -> datetime | None:
        return self._ends_utc if self.active else None

    def remaining_seconds(self) -> float:
        return max(0.0, self._ends_epoch - time.time())

    # -- transitions ----------------------------------------------------

    def start(self, preset_key: str, *, actor: str = "operator") -> dict[str, Any]:
        """Begin (or replace) a pause. Returns the new :meth:`snapshot`.

        Pressing pause while already paused replaces the deadline rather than
        extending or refusing it. That is what the control on the glass says it
        does -- it names an absolute duration, not an increment -- and an
        operator who picks "1 hour" during a 15-minute pause means the party is
        going longer, not seventy-five minutes.
        """
        now = datetime.now(UTC)
        ends, preset = resolve(preset_key, timezone=self._timezone(), now=now)
        if self._row_id is not None:
            self._close_row(self._row_id, ended_utc=now, reason=ENDED_SUPERSEDED)
        self._started_utc = now
        self._ends_utc = ends
        self._ends_epoch = ends.timestamp()
        self._preset_key = preset.key
        self._label = preset.label
        self._actor = actor
        self._row_id = self._open_row(started_utc=now, ends_utc=ends, preset=preset, actor=actor)
        self.pauses_started += 1
        log.warning(
            "pause.started",
            preset=preset.key,
            ends_utc=ends.isoformat().replace("+00:00", "Z"),
            seconds=round((ends - now).total_seconds(), 1),
            actor=actor,
            note="detection, evidence, publishing and live listening are suppressed; "
            "capture continues so the device is never closed",
        )
        return self.snapshot()

    def resume(self, *, actor: str = "operator") -> dict[str, Any]:
        """End a pause early. Idempotent: resuming when not paused is a no-op."""
        if self._row_id is None and self._ends_epoch == 0.0:
            return self.snapshot()
        now = datetime.now(UTC)
        was_active = self.active
        self._clear(now, ENDED_RESUMED if was_active else ENDED_EXPIRED)
        if was_active:
            log.warning("pause.resumed", actor=actor)
        return self.snapshot()

    def sync(self) -> None:
        """Close the row of a pause that has expired. Safe to call at any time.

        Called from the housekeeping loop. It does **not** end the pause -- the
        pause already ended, at its deadline, in :attr:`active`. All this does
        is make the durable record agree with that, promptly, so the history
        view does not show a pause as still running.
        """
        if self._row_id is None or self.active:
            return
        self._clear(self._ends_utc or datetime.now(UTC), ENDED_EXPIRED)
        log.info("pause.expired")

    def _clear(self, ended_utc: datetime, reason: str) -> None:
        row_id, self._row_id = self._row_id, None
        self._ends_epoch = 0.0
        self._ends_utc = None
        self._started_utc = None
        self._preset_key = ""
        self._label = ""
        self._actor = ""
        if row_id is not None:
            self._close_row(row_id, ended_utc=ended_utc, reason=reason)

    # -- restart survival -----------------------------------------------

    def restore(self) -> None:
        """Re-adopt an unfinished pause after a restart.

        The one thing this must never do is treat the restart itself as the end
        of the pause. A station that reboots at 15:30 during a pause until 18:00
        has to come back paused; a station whose pause expired while it was down
        has to come back recording, with the row closed honestly at the deadline
        rather than at the moment anyone noticed.
        """
        row = self._load_open_row()
        if row is None:
            return
        ends_utc = _aware(row["ends_utc"])
        now = datetime.now(UTC)
        if ends_utc <= now:
            self._close_row(row["id"], ended_utc=ends_utc, reason=ENDED_EXPIRED)
            log.info(
                "pause.expired_while_down",
                ends_utc=ends_utc.isoformat().replace("+00:00", "Z"),
            )
            return
        self._row_id = row["id"]
        self._started_utc = _aware(row["started_utc"])
        self._ends_utc = ends_utc
        self._ends_epoch = ends_utc.timestamp()
        self._preset_key = row["preset"] or ""
        self._label = row["label"] or ""
        self._actor = row["actor"] or ""
        log.warning(
            "pause.restored",
            ends_utc=ends_utc.isoformat().replace("+00:00", "Z"),
            remaining_s=round((ends_utc - now).total_seconds(), 1),
        )

    # -- reporting ------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """What every surface reads: the API, the live socket, the display.

        ``ends_utc`` is the authoritative field and ``remaining_s`` is derived
        from it for convenience. A client should render the countdown from the
        deadline, not from the number -- a page left open for an hour has a
        stale ``remaining_s`` and a still-correct ``ends_utc``.
        """
        active = self.active
        return {
            "active": active,
            "ends_utc": _iso(self._ends_utc) if active else None,
            "started_utc": _iso(self._started_utc) if active else None,
            "remaining_s": round(self.remaining_seconds(), 1) if active else 0.0,
            "preset": self._preset_key if active else None,
            "label": self._label if active else None,
            "actor": self._actor if active else None,
            "detections_suppressed": self.detections_suppressed,
            "pauses_started": self.pauses_started,
        }

    def banner(self, timezone: str | None = None) -> str:
        """One line for a small screen. Empty when not paused.

        Upper case and absolute: the counter-top display renders elapsed times
        for detections (ADR-038) because "is this happening now" is the question
        an ambient object answers, but the question a paused station has to
        answer is "when does it come back", and that is a clock time.
        """
        if not self.active or self._ends_utc is None:
            return ""
        local = self._ends_utc.astimezone(_zone(timezone or self._timezone()))
        return f"PAUSED BY OPERATOR - RECORDING RESUMES {local:%H:%M}"

    # -- persistence ----------------------------------------------------
    #
    # Imported lazily and kept behind these four methods so the controller
    # itself stays testable without a database, and so a persistence failure
    # can never stop a pause from taking effect: the in-memory deadline is set
    # first and every one of these swallows and logs rather than raising. A
    # pause that works but is not recorded is a documentation loss; a pause
    # that fails to engage because a disk was full is a privacy failure.

    def _open_row(
        self, *, started_utc: datetime, ends_utc: datetime, preset: PausePreset, actor: str
    ) -> uuid.UUID | None:
        if self._session_factory is None:
            return None
        from .db import models as orm

        row_id = uuid.uuid4()
        try:
            with self._session_factory() as session:
                session.add(
                    orm.CapturePause(
                        id=row_id,
                        station_id=self._station_id(),
                        started_utc=started_utc,
                        ends_utc=ends_utc,
                        preset=preset.key,
                        label=preset.label,
                        actor=actor,
                    )
                )
        except Exception:
            log.exception("pause.row_open_failed")
            return None
        return row_id

    def _close_row(self, row_id: uuid.UUID | None, *, ended_utc: datetime, reason: str) -> None:
        if row_id is None or self._session_factory is None:
            return
        from .db import models as orm

        try:
            with self._session_factory() as session:
                row = session.get(orm.CapturePause, row_id)
                if row is not None and row.ended_utc is None:
                    row.ended_utc = ended_utc
                    row.end_reason = reason
        except Exception:
            log.exception("pause.row_close_failed", reason=reason)

    def _load_open_row(self) -> dict[str, Any] | None:
        if self._session_factory is None:
            return None
        from sqlalchemy import select

        from .db import models as orm

        try:
            with self._session_factory() as session:
                row = session.execute(
                    select(orm.CapturePause)
                    .where(orm.CapturePause.ended_utc.is_(None))
                    .order_by(orm.CapturePause.started_utc.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if row is None:
                    return None
                return {
                    "id": row.id,
                    "started_utc": row.started_utc,
                    "ends_utc": row.ends_utc,
                    "preset": row.preset,
                    "label": row.label,
                    "actor": row.actor,
                }
        except Exception:
            log.exception("pause.restore_failed")
            return None

    def close_stale_rows(self) -> None:
        """Close pause rows left open by a process that died mid-pause and
        whose deadline has since passed, other than the one we just adopted.

        Same shape as ``Station._close_orphaned_streams``: an unfinished row is
        an honest record of a crash, but leaving several open would make
        "is this station paused" ambiguous in the durable record.
        """
        if self._session_factory is None:
            return
        from sqlalchemy import select

        from .db import models as orm

        try:
            with self._session_factory() as session:
                rows = (
                    session.execute(
                        select(orm.CapturePause).where(orm.CapturePause.ended_utc.is_(None))
                    )
                    .scalars()
                    .all()
                )
                for row in rows:
                    if row.id == self._row_id:
                        continue
                    row.ended_utc = _aware(row.ends_utc)
                    row.end_reason = ENDED_UNKNOWN
        except Exception:
            log.exception("pause.stale_close_failed")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _aware(value).isoformat().replace("+00:00", "Z")


def merged_intervals(
    intervals: Iterator[tuple[datetime, datetime]] | list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Overlapping pause windows folded into disjoint ones.

    Restarts and superseded pauses can produce rows that overlap, and summing
    those would report more paused time than the window contains -- the same
    arithmetic that once produced a 1302% coverage figure (ADR-024). Merging
    before summing is the fix there and here.
    """
    ordered = sorted(intervals)
    if not ordered:
        return []
    merged: list[tuple[datetime, datetime]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start > last_end:
            merged.append((start, end))
        elif end > last_end:
            merged[-1] = (last_start, end)
    return merged
