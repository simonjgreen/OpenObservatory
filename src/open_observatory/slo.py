"""The five capture SLOs of ADR-073, as pure arithmetic.

PURE. No SQLAlchemy, no FastAPI, no Pydantic -- the same rule
``plausibility.py`` and ``firmware_store.py`` follow, and for the same reason:
these numbers appear in the health payload, in ``/metrics``, in the acceptance
record and in the operations guide, and every one of those callers must get
the identical figure from the identical function.

The reason this module exists at all is that ``continuity_ratio`` --
``frames / expected_frames`` -- silently adds together three unrelated things:

* **coverage**  the capture process was not running
* **integrity** frames were dropped while it *was* running
* **drift**     the device's crystal is not the host's (ADR-072)

Only the first two are missing audio. Drift is audio that exists, is correct,
and is merely labelled a few seconds off -- and it dominates the ratio. On the
2026-08-25 soak drift was **100%** of the reported shortfall: nothing was lost
and the station still read 99.9943% "complete".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = ["DeficitSplit", "split_deficit"]


@dataclass(frozen=True, slots=True)
class DeficitSplit:
    """One stream's frame deficit, separated into what it actually means."""

    deficit_frames: int
    #: Audio that is genuinely gone. The station's own confirmed-loss counter
    #: (ADR-039), never inferred from the deficit.
    lost_frames: int
    #: The remainder: the crystal, and accepted (ADR-072).
    drift_frames: int
    lost_seconds: float
    drift_seconds: float
    #: SLO B. Of the audio that should have been captured while running, the
    #: fraction that was. **Blind to drift by construction.**
    integrity_ratio: float


def split_deficit(
    *,
    expected_frames: int,
    frames: int,
    missing_frames: int,
    sample_rate: int,
) -> DeficitSplit:
    """Separate a frame deficit into confirmed loss and crystal drift.

    ``missing_frames`` is authoritative for loss: it is what ``AlsaSource``
    confirmed as gone after watching a deficit step fail to come back down
    (ADR-039). Everything else in the deficit is drift by definition, which is
    why drift is a residual here rather than a measurement -- there is no
    third thing it could be.
    """
    deficit = max(0, expected_frames - frames)
    lost = max(0, missing_frames)
    # Clamped rather than allowed negative. The two inputs come from different
    # code paths, and a negative "drift" would silently offset real loss.
    drift = max(0, deficit - lost)

    rate = sample_rate or 1
    integrity = 1.0 if expected_frames <= 0 else max(0.0, 1.0 - lost / expected_frames)

    return DeficitSplit(
        deficit_frames=deficit,
        lost_frames=lost,
        drift_frames=drift,
        lost_seconds=round(lost / rate, 4),
        drift_seconds=round(drift / rate, 4),
        integrity_ratio=round(integrity, 9),
    )


__all__ += ["Coverage", "coverage"]


@dataclass(frozen=True, slots=True)
class Coverage:
    """SLO A. What fraction of a window the microphone was actually recording."""

    span_seconds: float
    covered_seconds: float
    ratio: float
    #: (when the outage began, how long it lasted). Reported, because "99.5%"
    #: says nothing about whether that was one bad hour or a thousand blips.
    outages: list[tuple[datetime, float]]


def coverage(
    intervals: Sequence[tuple[datetime, datetime]],
    *,
    start: datetime,
    end: datetime,
) -> Coverage:
    """Coverage of ``[start, end]`` given the intervals capture was running.

    Every interval is **clipped** to the window rather than filtered by whether
    it starts inside it. That is not a detail: filtering by ``start >= window``
    drops a stream that spans the window edge, and on 2026-08-29 that mistake
    reported 72.857% for a station that had not stopped once. A stream running
    since last week still covers this morning.

    Overlapping intervals are merged, so a reopen that writes two overlapping
    rows cannot produce more than 100%.
    """
    span = max(0.0, (end - start).total_seconds())
    if span <= 0:
        return Coverage(0.0, 0.0, 0.0, [])

    clipped = sorted((max(s, start), min(e, end)) for s, e in intervals if min(e, end) > max(s, start))
    merged: list[tuple[datetime, datetime]] = []
    for s, e in clipped:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    covered = sum((e - s).total_seconds() for s, e in merged)

    outages: list[tuple[datetime, float]] = []
    cursor = start
    for s, e in merged:
        if (s - cursor).total_seconds() > 1.0:
            outages.append((cursor, (s - cursor).total_seconds()))
        cursor = max(cursor, e)
    if (end - cursor).total_seconds() > 1.0:
        outages.append((cursor, (end - cursor).total_seconds()))

    return Coverage(
        span_seconds=round(span, 3),
        covered_seconds=round(covered, 3),
        ratio=round(covered / span, 6),
        outages=[(w, round(d, 3)) for w, d in outages],
    )


__all__ += ["prime_intervals"]


def prime_intervals(
    start: datetime,
    end: datetime,
    *,
    latitude: float,
    longitude: float,
    margin_hours: float = 2.0,
) -> list[tuple[datetime, datetime]]:
    """The hours worth measuring separately: civil twilight, plus a margin.

    SLO A2 exists because wall-clock seconds are the wrong unit for a bird
    monitor. Two minutes lost at dawn costs a chorus; two hours lost at 14:00
    in December costs almost nothing. Both are the same number to SLO A, which
    is why A2 is measured apart from it rather than instead of it.

    Imported lazily so this module stays free of the scheduler's own imports
    and can be reasoned about (and tested) on its own.

    Returns merged, ordered, half-open intervals clipped to ``[start, end]``.
    Empty at polar latitudes where the sun does not cross -6 degrees, which is
    honest: there is no twilight to measure, not zero coverage of it.
    """
    from .schedule import _dawn_dusk_for_date

    margin = timedelta(hours=margin_hours)
    raw: list[tuple[datetime, datetime]] = []

    day = (start - timedelta(days=1)).date()
    last = (end + timedelta(days=1)).date()
    while day <= last:
        dawn, dusk = _dawn_dusk_for_date(day, latitude, longitude)
        for moment in (dawn, dusk):
            if moment is None:
                continue
            s = max(moment - margin, start)
            e = min(moment + margin, end)
            if e > s:
                raw.append((s, e))
        day += timedelta(days=1)

    raw.sort()
    merged: list[tuple[datetime, datetime]] = []
    for s, e in raw:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


__all__ += ["detection_coverage"]


def _as_int(value: object) -> int:
    """Coerce a counter pulled from a loosely-typed mapping. Falsy is zero."""
    if not value:
        return 0
    assert isinstance(value, (int, float))
    return int(value)


def detection_coverage(detectors: Sequence[Mapping[str, object]]) -> float | None:
    """SLO D. The fraction of offered windows a detector actually analysed.

    **The worst detector, not the mean.** Averaging would let a healthy
    detector paper over a starving one, and a detector that analysed half the
    windows is not "partly covered" -- it is a hole in the record for whatever
    it listens for. ADR-073 records this SLO as unmeasurable; it was not, the
    counters were simply never divided.

    ``None`` when no detector has been offered a window yet: before the first
    window there is no ratio, and reporting 0% would read as total failure at
    every startup.
    """
    worst: float | None = None
    for d in detectors:
        analysed = _as_int(d.get("windows_analysed", 0))
        dropped = _as_int(d.get("windows_dropped_queue_full", 0)) + _as_int(d.get("windows_dropped_stale", 0))
        offered = analysed + dropped
        if offered <= 0:
            continue
        ratio = analysed / offered
        worst = ratio if worst is None else min(worst, ratio)
    return None if worst is None else round(worst, 6)
