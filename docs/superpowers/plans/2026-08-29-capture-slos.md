# Capture SLOs (ADR-073) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single `continuity_ratio` with five separately measured and
separately reported SLOs, so that drift is never again counted as lost audio.

**Architecture:** A new dependency-free module `slo.py` holds every calculation
as a pure function (the `plausibility.py` / `firmware_store.py` precedent: no
SQLAlchemy, no FastAPI, no Pydantic). `station.py` and `api/app.py` call into it;
neither gains arithmetic of its own. Nothing in the capture hot path changes —
this plan adds measurement, not behaviour.

**Tech Stack:** Python 3.12, pytest, FastAPI, Prometheus text format.

**Spec:** [[ADR-073]]

## Global Constraints

- **Never change capture behaviour.** This plan is measurement only. No task
  touches the ALSA read path, the ring, or the clock.
- **`continuity_ratio` is kept, not removed.** It is referenced from
  `api/metrics.py`, from operations docs and from three prior ADRs. It gains a
  docstring saying what it actually mixes; it does not change value.
- **Drift is never counted as loss.** Enforced by test, not by convention.
- Run tests with `.venv/bin/python -m pytest`, always with
  `--deselect tests/test_api.py::TestLiveChannels` (those three hang forever on
  starlette 0.41.3 — see [[SETUP]]).
- Ruff and mypy must be clean on every file touched:
  `.venv/bin/python -m ruff check <files> && .venv/bin/python -m ruff format --check <files> && .venv/bin/python -m mypy <files>`
- UTC internally, always. Local time only for presentation.

---

### Task 1: Split the frame deficit into loss and drift

The whole plan rests on this arithmetic. `continuity_ratio` today is
`frames / expected_frames`, which sums lost audio and crystal drift. Measured on
the 2026-08-25 soak: 16.0 s of "shortfall", **0.0 s of it lost audio**.

**Files:**
- Create: `src/open_observatory/slo.py`
- Test: `tests/test_slo.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `DeficitSplit` dataclass with fields `deficit_frames: int`,
  `lost_frames: int`, `drift_frames: int`, `lost_seconds: float`,
  `drift_seconds: float`, `integrity_ratio: float`; and
  `split_deficit(*, expected_frames: int, frames: int, missing_frames: int, sample_rate: int) -> DeficitSplit`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slo.py
"""The arithmetic ADR-073 exists to enforce: drift is not loss."""
from __future__ import annotations

import pytest

from open_observatory import slo

RATE = 384_000


def test_a_perfect_stream_has_no_loss_and_no_drift() -> None:
    s = slo.split_deficit(expected_frames=RATE * 100, frames=RATE * 100, missing_frames=0, sample_rate=RATE)
    assert s.lost_frames == 0
    assert s.drift_frames == 0
    assert s.integrity_ratio == 1.0


def test_the_2026_08_25_soak_lost_no_audio_at_all() -> None:
    """The regression this module exists for.

    78.69 h, continuity 0.999943, and *zero* confirmed loss. The old ratio
    called that a 16-second shortfall. It was the crystal, and every second of
    audio was present and correct.
    """
    expected = 108_785_512_945
    frames = 108_779_366_400
    s = slo.split_deficit(expected_frames=expected, frames=frames, missing_frames=0, sample_rate=RATE)

    assert s.deficit_frames == expected - frames
    assert s.lost_frames == 0
    assert s.lost_seconds == 0.0
    assert s.integrity_ratio == 1.0                    # <- the point
    assert s.drift_seconds == pytest.approx(16.0, abs=0.1)


def test_real_loss_is_charged_to_integrity_and_not_to_drift() -> None:
    """The 2026-08-22 soak: 0.597 s genuinely lost, the rest drift."""
    missing = 229_245
    expected = RATE * 259_585
    frames = expected - missing - 4_970_000          # loss plus ~12.9 s of drift
    s = slo.split_deficit(expected_frames=expected, frames=frames, missing_frames=missing, sample_rate=RATE)

    assert s.lost_seconds == pytest.approx(0.597, abs=0.001)
    assert s.drift_seconds == pytest.approx(12.9, abs=0.2)
    assert s.integrity_ratio < 1.0
    assert s.integrity_ratio > 0.999999


def test_drift_can_never_reduce_the_integrity_ratio() -> None:
    """A station with a terrible crystal and perfect capture scores 100%.

    At ~1150 ppm drift alone consumes the whole 0.1% budget the old criterion
    allowed, so a flawless station would have failed it. Integrity must be
    blind to that.
    """
    expected = RATE * 86_400
    for ppm in (50, 500, 5_000):
        frames = int(expected * (1 - ppm * 1e-6))
        s = slo.split_deficit(expected_frames=expected, frames=frames, missing_frames=0, sample_rate=RATE)
        assert s.integrity_ratio == 1.0, f"{ppm} ppm of drift reduced integrity"
        assert s.lost_frames == 0


def test_a_negative_deficit_does_not_invent_negative_drift() -> None:
    """The device can run marginally fast, or a sample can land early.

    Clamped, because a negative "drift" reads as the clock running backwards
    and a negative loss is meaningless.
    """
    s = slo.split_deficit(expected_frames=RATE * 10, frames=RATE * 10 + 500, missing_frames=0, sample_rate=RATE)
    assert s.drift_frames == 0
    assert s.lost_frames == 0
    assert s.integrity_ratio == 1.0


def test_missing_frames_exceeding_the_deficit_is_reported_not_hidden() -> None:
    """Defensive: the two counters come from different code paths.

    If confirmed loss somehow exceeds the total deficit, drift clamps to zero
    rather than going negative and silently offsetting the loss.
    """
    s = slo.split_deficit(expected_frames=RATE * 10, frames=RATE * 10 - 100, missing_frames=1000, sample_rate=RATE)
    assert s.lost_frames == 1000
    assert s.drift_frames == 0


def test_a_zero_length_stream_does_not_divide_by_zero() -> None:
    s = slo.split_deficit(expected_frames=0, frames=0, missing_frames=0, sample_rate=RATE)
    assert s.integrity_ratio == 1.0
    assert s.drift_seconds == 0.0
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_slo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'open_observatory.slo'`

- [ ] **Step 3: Write the implementation**

```python
# src/open_observatory/slo.py
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

from dataclasses import dataclass

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
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_slo.py -v`
Expected: 7 passed.

- [ ] **Step 5: Lint, type-check, commit**

```bash
.venv/bin/python -m ruff check src/open_observatory/slo.py tests/test_slo.py
.venv/bin/python -m ruff format src/open_observatory/slo.py tests/test_slo.py
.venv/bin/python -m mypy src/open_observatory/slo.py
git add src/open_observatory/slo.py tests/test_slo.py
git commit -m "ADR-073: split the frame deficit into confirmed loss and crystal drift"
```

---

### Task 2: Coverage over a window (SLO A)

Coverage is the SLO the old criterion was silent about, and it is the dominant
loss: over 9.8 days the station lost **0.60 s** in-stream and **114 s** to being
switched off between streams.

**Files:**
- Modify: `src/open_observatory/slo.py`
- Test: `tests/test_slo.py`

**Interfaces:**
- Consumes: `DeficitSplit` from Task 1 (unused here; same module).
- Produces: `Coverage` dataclass with `span_seconds: float`,
  `covered_seconds: float`, `ratio: float`, `outages: list[tuple[datetime, float]]`;
  and `coverage(intervals: Sequence[tuple[datetime, datetime]], *, start: datetime, end: datetime) -> Coverage`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_slo.py
from datetime import UTC, datetime, timedelta


def _dt(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=UTC)


def test_full_coverage_when_one_stream_spans_the_window() -> None:
    c = slo.coverage([(_dt(1), _dt(4))], start=_dt(2), end=_dt(3))
    assert c.ratio == 1.0
    assert c.outages == []


def test_a_stream_that_straddles_the_window_edge_is_clipped_not_excluded() -> None:
    """The bug I made by hand on 2026-08-29.

    Filtering streams by `start >= cutoff` drops a stream that *spans* the
    cutoff and reports 72.857% uptime for a station that never stopped.
    Intervals must be clipped to the window, never filtered by their start.
    """
    c = slo.coverage([(_dt(1), _dt(10))], start=_dt(5), end=_dt(6))
    assert c.ratio == 1.0
    assert c.covered_seconds == 86_400.0


def test_the_gap_between_two_streams_is_an_outage() -> None:
    c = slo.coverage(
        [(_dt(1), _dt(2, 0)), (_dt(2, 1), _dt(3))],
        start=_dt(1),
        end=_dt(3),
    )
    assert len(c.outages) == 1
    when, seconds = c.outages[0]
    assert when == _dt(2, 0)
    assert seconds == 3600.0
    assert c.ratio == pytest.approx(1 - 3600 / 172_800)


def test_overlapping_streams_are_not_double_counted() -> None:
    """Coverage is of the window, not the sum of stream lengths.

    A reopen can write overlapping rows; summing them would report >100%.
    """
    c = slo.coverage(
        [(_dt(1), _dt(3)), (_dt(2), _dt(4))],
        start=_dt(1),
        end=_dt(4),
    )
    assert c.ratio == 1.0
    assert c.covered_seconds == 3 * 86_400.0


def test_an_outage_at_the_end_of_the_window_still_counts() -> None:
    c = slo.coverage([(_dt(1), _dt(2))], start=_dt(1), end=_dt(3))
    assert c.ratio == pytest.approx(0.5)
    assert len(c.outages) == 1


def test_no_streams_at_all_is_zero_coverage_not_a_crash() -> None:
    c = slo.coverage([], start=_dt(1), end=_dt(2))
    assert c.ratio == 0.0
    assert c.covered_seconds == 0.0


def test_the_measured_steady_state_meets_the_slo() -> None:
    """The real figure from 2026-08-19 to 29: three outages, 1.9 minutes."""
    start = _dt(19)
    end = start + timedelta(days=9.8)
    intervals = [
        (start, start + timedelta(days=2.66)),
        (start + timedelta(days=2.66, seconds=105), start + timedelta(days=5.74)),
        (start + timedelta(days=5.74, seconds=4), start + timedelta(days=9.07)),
        (start + timedelta(days=9.07, seconds=6), end),
    ]
    c = slo.coverage(intervals, start=start, end=end)
    assert c.ratio > 0.995            # SLO A
    assert len(c.outages) == 3
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_slo.py -k coverage -v`
Expected: FAIL — `AttributeError: module 'open_observatory.slo' has no attribute 'coverage'`

- [ ] **Step 3: Write the implementation**

```python
# append to src/open_observatory/slo.py
from collections.abc import Sequence
from datetime import datetime

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

    clipped = sorted(
        (max(s, start), min(e, end)) for s, e in intervals if min(e, end) > max(s, start)
    )
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
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_slo.py -v`
Expected: 14 passed.

- [ ] **Step 5: Lint, type-check, commit**

```bash
.venv/bin/python -m ruff check src/open_observatory/slo.py tests/test_slo.py
.venv/bin/python -m ruff format src/open_observatory/slo.py tests/test_slo.py
.venv/bin/python -m mypy src/open_observatory/slo.py
git add src/open_observatory/slo.py tests/test_slo.py
git commit -m "ADR-073: coverage over a window, clipping intervals rather than filtering them"
```

---

### Task 3: Prime-hours coverage (SLO A2)

A two-minute outage at dawn costs more than two hours at 14:00 in December.
A2 restricts coverage to civil twilight ±2 h and the ultrasonic night window.

**Files:**
- Modify: `src/open_observatory/slo.py`
- Test: `tests/test_slo.py`

**Interfaces:**
- Consumes: `coverage()` from Task 2; `_dawn_dusk_for_date(d, latitude, longitude)`
  from `src/open_observatory/schedule.py:51`, which returns
  `tuple[datetime | None, datetime | None]` (civil dawn, civil dusk) in UTC, and
  `None` for either leg at polar latitudes.
- Produces: `prime_intervals(start: datetime, end: datetime, *, latitude: float, longitude: float, margin_hours: float = 2.0) -> list[tuple[datetime, datetime]]`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_slo.py
SURREY = {"latitude": 51.24, "longitude": -0.59}


def test_prime_intervals_bracket_dawn_and_dusk() -> None:
    ivs = slo.prime_intervals(_dt(15), _dt(16), **SURREY)
    assert ivs, "August in Surrey has both a dawn and a dusk"
    for s, e in ivs:
        assert e > s
        assert (e - s).total_seconds() <= 4 * 3600 + 1     # 2 h either side


def test_prime_intervals_do_not_overlap_and_are_ordered() -> None:
    ivs = slo.prime_intervals(_dt(15), _dt(20), **SURREY)
    for a, b in zip(ivs, ivs[1:], strict=False):
        assert a[1] <= b[0], "intervals must be merged and ordered"


def test_prime_coverage_is_perfect_when_capture_never_stops() -> None:
    ivs = slo.prime_intervals(_dt(15), _dt(18), **SURREY)
    c = slo.coverage([(_dt(14), _dt(19))], start=_dt(15), end=_dt(18))
    assert c.ratio == 1.0
    total_prime = sum((e - s).total_seconds() for s, e in ivs)
    assert total_prime > 0


def test_an_outage_at_noon_does_not_touch_prime_coverage() -> None:
    """The whole point of A2. Down for four hours in the middle of the day."""
    ivs = slo.prime_intervals(_dt(15), _dt(16), **SURREY)
    running = [(_dt(15), _dt(15, 11)), (_dt(15, 15), _dt(16))]
    prime_covered = 0.0
    prime_total = 0.0
    for ps, pe in ivs:
        c = slo.coverage(running, start=ps, end=pe)
        prime_covered += c.covered_seconds
        prime_total += c.span_seconds
    assert prime_total > 0
    assert prime_covered / prime_total == 1.0, "a midday outage must not count against A2"


def test_polar_latitudes_yield_no_prime_window_rather_than_crashing() -> None:
    """June above the Arctic circle has no civil twilight at all."""
    ivs = slo.prime_intervals(
        datetime(2026, 6, 20, tzinfo=UTC),
        datetime(2026, 6, 22, tzinfo=UTC),
        latitude=78.9,
        longitude=11.9,
    )
    assert ivs == []
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_slo.py -k prime -v`
Expected: FAIL — `AttributeError: module 'open_observatory.slo' has no attribute 'prime_intervals'`

- [ ] **Step 3: Write the implementation**

```python
# append to src/open_observatory/slo.py
from datetime import timedelta

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
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_slo.py -v`
Expected: 19 passed.

- [ ] **Step 5: Lint, type-check, commit**

```bash
.venv/bin/python -m ruff check src/open_observatory/slo.py tests/test_slo.py
.venv/bin/python -m ruff format src/open_observatory/slo.py tests/test_slo.py
.venv/bin/python -m mypy src/open_observatory/slo.py
git add src/open_observatory/slo.py tests/test_slo.py
git commit -m "ADR-073: prime-hours coverage, so a dawn outage is not averaged against December"
```

---

### Task 4: Detection coverage (SLO D)

ADR-073 records D as "not currently measurable". **That is wrong and this task
corrects it.** `detectors/base.py:120-122` already counts `windows_analysed`,
`windows_dropped_queue_full` and `windows_dropped_stale`. The ratio has simply
never been computed or surfaced.

**Files:**
- Modify: `src/open_observatory/slo.py`
- Modify: [[ADR-073]]
- Test: `tests/test_slo.py`

**Interfaces:**
- Consumes: per-detector counters `windows_analysed: int`,
  `windows_dropped_queue_full: int`, `windows_dropped_stale: int`.
- Produces: `detection_coverage(detectors: Sequence[Mapping[str, object]]) -> float | None`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_slo.py
def test_detection_coverage_is_analysed_over_offered() -> None:
    d = [{"windows_analysed": 99, "windows_dropped_queue_full": 1, "windows_dropped_stale": 0}]
    assert slo.detection_coverage(d) == pytest.approx(0.99)


def test_detection_coverage_is_the_worst_detector_not_the_average() -> None:
    """One starving detector is a hole in the record.

    Averaging hides it: a detector analysing 50% alongside one analysing 100%
    is not "75% covered", it is a detector missing half the garden.
    """
    d = [
        {"windows_analysed": 100, "windows_dropped_queue_full": 0, "windows_dropped_stale": 0},
        {"windows_analysed": 50, "windows_dropped_queue_full": 50, "windows_dropped_stale": 0},
    ]
    assert slo.detection_coverage(d) == pytest.approx(0.5)


def test_stale_drops_count_against_coverage_too() -> None:
    """A window dropped for being stale was still never analysed."""
    d = [{"windows_analysed": 90, "windows_dropped_queue_full": 5, "windows_dropped_stale": 5}]
    assert slo.detection_coverage(d) == pytest.approx(0.90)


def test_a_detector_that_has_seen_nothing_yet_is_not_zero_percent() -> None:
    """Before the first window there is no ratio, and None says so."""
    assert slo.detection_coverage([{"windows_analysed": 0, "windows_dropped_queue_full": 0, "windows_dropped_stale": 0}]) is None
    assert slo.detection_coverage([]) is None
```

- [ ] **Step 2: Run the test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_slo.py -k detection_coverage -v`
Expected: FAIL — no attribute `detection_coverage`

- [ ] **Step 3: Write the implementation**

```python
# append to src/open_observatory/slo.py
from collections.abc import Mapping

__all__ += ["detection_coverage"]


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
        analysed = int(d.get("windows_analysed", 0) or 0)
        dropped = int(d.get("windows_dropped_queue_full", 0) or 0) + int(
            d.get("windows_dropped_stale", 0) or 0
        )
        offered = analysed + dropped
        if offered <= 0:
            continue
        ratio = analysed / offered
        worst = ratio if worst is None else min(worst, ratio)
    return None if worst is None else round(worst, 6)
```

- [ ] **Step 4: Run the test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_slo.py -v`
Expected: 23 passed.

- [ ] **Step 5: Correct ADR-073, which says this is unmeasurable**

In [[ADR-073]],
replace the SLO D row's `measured` cell `not yet measured` with
`worst detector, from counters that already exist`, and replace this bullet:

```
- **D is not currently measurable.** Nothing reports the fraction of captured
  audio the detectors actually consumed. Recording an SLO that cannot be
  measured is honest only if the gap is stated: it is stated here, and closing
  it is work, not a formality.
```

with:

```
- **D was recorded here as unmeasurable, and that was wrong.** The counters
  already existed -- `windows_analysed`, `windows_dropped_queue_full` and
  `windows_dropped_stale` in `detectors/base.py` -- and had simply never been
  divided or surfaced. Corrected 2026-08-29 during implementation. It is
  reported as the *worst* detector rather than the mean, so a healthy detector
  cannot paper over a starving one.
```

- [ ] **Step 6: Lint, type-check, commit**

```bash
.venv/bin/python -m ruff check src/open_observatory/slo.py tests/test_slo.py
.venv/bin/python -m ruff format src/open_observatory/slo.py tests/test_slo.py
.venv/bin/python -m mypy src/open_observatory/slo.py
git add src/open_observatory/slo.py tests/test_slo.py "docs/architecture/adr/ADR-073 - What missing audio means, and five SLOs instead of one continuity number.md"
git commit -m "ADR-073: detection coverage was measurable all along; report the worst detector"
```

---

### Task 5: Surface the SLOs in the health payload

**Files:**
- Modify: `src/open_observatory/station.py` — the capture snapshot dict, around
  line 2176 where `continuity_ratio` is set
- Test: `tests/test_slo_health.py`

**Interfaces:**
- Consumes: `slo.split_deficit`, `slo.detection_coverage`.
- Produces: exactly three new keys inside the existing `capture` block:
  `audio_lost_seconds: float`, `drift_seconds: float`,
  `capture_integrity_ratio: float`. Nothing else. Surfacing coverage (A/A2) and
  detection coverage (D) needs a cross-process query and is deferred — see the
  end of this plan.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_slo_health.py
"""The health payload must report loss and drift as different things."""
from __future__ import annotations

from open_observatory import slo


def test_the_health_keys_decompose_the_deficit_exactly() -> None:
    """Whatever the payload says, lost + drift must equal the deficit.

    This is the invariant that stops the two ever drifting apart in the
    reporting layer -- the place ADR-073 says the category error lived.
    """
    s = slo.split_deficit(
        expected_frames=384_000 * 3600,
        frames=384_000 * 3600 - 100_000,
        missing_frames=20_000,
        sample_rate=384_000,
    )
    assert s.lost_frames + s.drift_frames == s.deficit_frames


def test_integrity_ratio_and_continuity_ratio_disagree_and_that_is_correct() -> None:
    """The old number and the new one are not interchangeable.

    continuity_ratio charges drift to the station. integrity_ratio does not.
    A test asserting they match would be asserting the bug.
    """
    expected = 384_000 * 3600
    frames = expected - 100_000            # pure drift, nothing lost
    s = slo.split_deficit(expected_frames=expected, frames=frames, missing_frames=0, sample_rate=384_000)
    continuity = frames / expected
    assert continuity < 1.0
    assert s.integrity_ratio == 1.0
```

- [ ] **Step 2: Run the test and verify it passes already**

Run: `.venv/bin/python -m pytest tests/test_slo_health.py -v`
Expected: PASS — these pin Task 1's invariants before the wiring changes them.

- [ ] **Step 3: Wire it into the capture snapshot**

In `src/open_observatory/station.py`, add the import near the other
first-party imports:

```python
from . import slo as slo_module
```

Then compute the split **before** the dict is built, next to where
`continuity` is calculated (around line 2075, inside the same
`if stream is not None and self.clock is not None:` block):

```python
            # ADR-073. `continuity` above sums confirmed loss and crystal
            # drift; this separates them. Drift is audio that exists and is
            # merely mislabelled (ADR-072); charging it to the station is the
            # category error ADR-073 exists to end.
            deficit_split = slo_module.split_deficit(
                expected_frames=expected_frames,
                frames=self._stream_frames,
                missing_frames=self.counters.estimated_missing_frames,
                sample_rate=stream.fmt.sample_rate,
            )
```

Initialise it alongside the other optionals so the name always exists:

```python
        expected_frames = None
        continuity = None
        deficit_split = None          # <- add this line
```

Then, immediately after the line that sets `"continuity_ratio": continuity,`
in the capture dict (around line 2176), insert:

```python
                # ADR-073: loss and drift, reported apart. See slo.py.
                "audio_lost_seconds": deficit_split.lost_seconds if deficit_split else 0.0,
                "drift_seconds": deficit_split.drift_seconds if deficit_split else 0.0,
                "capture_integrity_ratio": (
                    deficit_split.integrity_ratio if deficit_split else None
                ),
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q --deselect tests/test_api.py::TestLiveChannels`
Expected: all pass, count one higher than before plus the new files.

- [ ] **Step 5: Verify against the live station**

```bash
curl -s http://192.168.1.195:8080/api/v1/health | python3 -c "
import json,sys; c=json.load(sys.stdin)['capture']
print('continuity      ', c['continuity_ratio'])
print('integrity       ', c['capture_integrity_ratio'])
print('audio lost   (s)', c['audio_lost_seconds'])
print('drift        (s)', c['drift_seconds'])"
```

Expected on a healthy stream: `integrity` is **1.0**, `audio lost` is **0.0**,
and `drift` accounts for the whole difference between `continuity` and 1.0.

- [ ] **Step 6: Lint, type-check, commit**

```bash
.venv/bin/python -m ruff check src/open_observatory/station.py tests/test_slo_health.py
.venv/bin/python -m ruff format src/open_observatory/station.py tests/test_slo_health.py
.venv/bin/python -m mypy src/open_observatory/station.py
git add src/open_observatory/station.py tests/test_slo_health.py
git commit -m "ADR-073: report loss, drift and integrity as three separate numbers in health"
```

---

### Task 6: Metrics, and the acceptance record

**Files:**
- Modify: `src/open_observatory/api/metrics.py` — beside the existing
  `oo_capture_continuity_ratio` gauge, around line 138
- Modify: [[ACCEPTANCE_CRITERIA]]
- Modify: [[DEPLOYMENT_AND_OPERATIONS]]

**Interfaces:**
- Consumes: the `capture` keys added in Task 5.
- Produces: gauges `oo_capture_integrity_ratio`, `oo_capture_audio_lost_seconds`,
  `oo_capture_drift_seconds`.

- [ ] **Step 1: Add the gauges**

In `src/open_observatory/api/metrics.py`, immediately after the
`oo_capture_continuity_ratio` block:

```python
        self._set(
            "oo_capture_integrity_ratio",
            "SLO B: fraction of recorded audio not dropped. Blind to crystal drift (ADR-073)",
            capture.get("capture_integrity_ratio", 1.0),
        )
        self._set(
            "oo_capture_audio_lost_seconds",
            "Confirmed lost audio this stream. Drift is NOT counted here (ADR-073)",
            capture.get("audio_lost_seconds", 0.0),
        )
        self._set(
            "oo_capture_drift_seconds",
            "Timestamp error from crystal drift. Accepted, not loss (ADR-072, ADR-073)",
            capture.get("drift_seconds", 0.0),
        )
```

- [ ] **Step 2: Verify the metrics render**

```bash
.venv/bin/python -m pytest tests/ -q -k metrics --deselect tests/test_api.py::TestLiveChannels
curl -s http://192.168.1.195:8080/metrics | grep -E "oo_capture_(integrity|audio_lost|drift)"
```

Expected: three gauges present with sane values.

- [ ] **Step 3: Replace the continuity criterion in the acceptance record**

In [[ACCEPTANCE_CRITERIA]], replace the single ticked
continuity line with two lines:

```markdown
- [x] **SLO A — coverage ≥ 99.5%/month.** Measured 99.986% over 9.8 days to 2026-08-29 (1.9 min downtime, 3 outages).
- [x] **SLO B — capture integrity ≥ 99.99%.** Measured 100% over the 78.7 h stream ending 2026-08-28: **zero** audio lost. The 2026-08-22 soak lost 0.597 s of a 259.2 s budget.
- [ ] SLO A2 — prime-hours coverage ≥ 99.9%. Not yet measured over a full month.
- [ ] SLO C — timestamp error ≤ 60 s. Currently bounded by stream age (ADR-072); needs a month of evidence.
- [ ] SLO D — detection coverage ≥ 99%. Measurable as of 2026-08-29; not yet observed over a month.
- [ ] SLO E — evidence sufficiency ≥ 95%. Blocked on ADR-074.
```

- [ ] **Step 4: Document the split for operators**

In [[DEPLOYMENT_AND_OPERATIONS]], in the health-check section,
add:

```markdown
**Read `capture_integrity_ratio`, not `continuity_ratio`.** The older figure
sums lost audio and crystal drift; the newer one counts only audio that is
genuinely gone. On a healthy station integrity is 1.0 while continuity sits
near 0.99995, and the difference is the microphone's crystal (ADR-072), not a
fault. `audio_lost_seconds` and `drift_seconds` show the split directly.
```

- [ ] **Step 5: Full verification and commit**

```bash
.venv/bin/python -m pytest -q --deselect tests/test_api.py::TestLiveChannels
.venv/bin/python -m ruff check src/ tests/ && .venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/open_observatory/slo.py src/open_observatory/api/metrics.py
git add -A
git commit -m "ADR-073: SLO gauges, and replace the continuity box with A and B"
```

---

## Deferred, deliberately

**Coverage (A), prime-hours coverage (A2) and detection coverage (D) are
computed and tested but not yet served.** Tasks 2, 3 and 4 build and prove the
functions; wiring A and A2 to an endpoint needs a query over `audio_stream`
across process lifetimes, and D needs the per-detector counters lifted into the
health payload — both a different shape of work from the in-process arithmetic
Tasks 1 and 5 use. That is the natural first task of a follow-up plan.

This is a deliberate stopping point, not an oversight: **B is the SLO that
changes what the project believes about itself today**, because it is the one
that stops drift being reported as lost audio. A is already known to be met
(99.986% measured); serving it is reporting, not discovery.

**No alert on `rate_offset_ppm`.** Proposed in ADR-072, not decided. Out of
scope here.
