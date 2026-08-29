---
aliases:
  - ADR-033
tags:
  - adr
---
# ADR-033: Retention is paced, because a dedicated thread is not the same as out of the way
**Status:** active; amends [[ADR-026 - Tiered clip retention|ADR-026]]'s sweep cadence, and the open item below
is closed by [[ADR-039 - Confirmed loss, not deficit|ADR-039]]. The pacing itself holds, but "pacing rather than making
the sweep cheaper" did not — see the review note at the foot.

**Decision:** `RetentionSweeper.sweep()` runs every `retention_interval_s`
(default **300 s**), rounded to the nearest 10 s housekeeping tick, not on every
tick as [[ADR-026 - Tiered clip retention|ADR-026]]'s implementation did. Nothing else about retention changes: the
sweep still runs in the evidence executor's dedicated thread, still bounds itself
by `retention_batch_size` and `retention_batch_budget_s`, and still resumes where
it left off. Two permanent instruments are added: an event-loop lag watchdog
(`loop.lag`) and a per-contributor cost breakdown of `status_snapshot()`
(`snapshot_phase_s`).

**Reason — measured on the live station, 2026-08-08.** After seven branches were
merged, `capture.gap` came back at ~1.6 records per minute against zero over the
preceding 73 minutes. The gaps were spaced at multiples of ~10.4 s, the
housekeeping period, so the tick was the suspect. Single-variable experiment,
same code, same settings, `OO_RETENTION_ENABLED` the only thing changed:

| Window | Retention cadence | `capture.gap` | `estimated_missing_frames` | `rate_offset_ppm` |
|---|---|---|---|---|
| 18:01–18:06Z (5 min) | every 10 s | **8** | 252,495 | +2,680 |
| 18:06–18:14Z (7 min) | disabled | **0** | **0** | **−51.75** |

The event-loop lag watchdog, deployed before the experiment, separates cause from
coincidence. With the sweep on a 10 s cadence the loop was starved for 55–150 ms
about five times a minute, on that same ~10.4 s beat. With the sweep disabled the
lag events fell to ~1.6/min on an unrelated 30 s beat and no gap was produced.

**Why a dedicated thread was not enough.** [[ADR-021 - Clips on their own device|ADR-021]] moved retention off the default
pool so it could not queue in front of the ALSA read, and [[ADR-030 - ALSA ring and capture thread|ADR-030]] gave the read its
own executor as well. Both were right, and neither addresses this: a ~0.30 s sweep
is SQLAlchemy ORM work in Python, so it holds the GIL, and CPython hands the GIL
back to a waiting I/O-bound thread reluctantly. The event loop is the I/O-bound
thread here, and it still has to *issue* each `run_in_executor` read and consume
its result. A read that is issued 130 ms late is a read that starts 130 ms late,
however private its thread. **The GIL is a shared resource that no executor
partitions**, and that is the general lesson: "give it its own thread" bounds
queueing delay, not scheduling delay.

**Why pacing rather than making the sweep cheaper.** Retention deletes clips whose
age crossed a boundary measured in *days*, plus a watermark reclaim that triggers
at 85% of a filesystem currently 6.3% full. Nothing about it is urgent at a
ten-second granularity; the 10 s cadence bought no property the operator can
observe. Five minutes is what the pre-merge code did (`ticks % 30`) and it restores
that behaviour behind a setting rather than a literal, so a station that genuinely
fills its disk fast can be tuned without a code change. The bounded-batch design
from [[ADR-026 - Tiered clip retention|ADR-026]] is what makes a longer interval safe: a backlog still drains
incrementally, just on a slower beat.

**What this did not fix, and left open — now CLOSED by [[ADR-039 - Confirmed loss, not deficit|ADR-039]] (2026-08-09).** The
paragraph below is kept as written because it is the diagnosis [[ADR-039 - Confirmed loss, not deficit|ADR-039]] acts on. It
is no longer an open item: the estimator now confirms a deficit step against the
following blocks before crediting it, and `reason=overrun` is no longer attached to
an event ALSA never reported. The stall did not lose
any audio, and the station said it did. Over the affected window the capture path
was 21,300 frames (0.055 s) behind what elapsed time implied, while
`estimated_missing_frames` claimed 252,495 (0.657 s) and `overruns` was **0** —
ALSA never reported a ring overflow, because the 500 ms ring from [[ADR-030 - ALSA ring and capture thread|ADR-030]] absorbed
the stall exactly as intended. The deficit-step estimator in `_read_blocking`
credits a timing step of more than one block as lost audio immediately, which was
correct against an 80 ms ring where such a step really did mean an overflow, and
is not correct against a ring deep enough to recover. Every one of those phantom
frames is then added to `presented` in the observed-rate calculation, which is the
whole of the nonsense `rate_offset_ppm` of +2,680 against a true device offset near
−43 ppm: 252,495/92,505,600 is 2,729 ppm. **`capture.gap lost_audio=True` currently
means "the read was late", not "recording was lost".** Confirming a deficit step
against the following blocks before crediting it is the fix, and it belongs to
whoever next owns the estimator; it is recorded in
[[OPEN_INVESTIGATION_CAPTURE_GAPS]] with the numbers above.

**Consequence:** `/api/v1/station` and `/api/v1/health` report `loop_lag_max_s`,
`loop_lag_events`, `housekeeping_blocking_s` and `snapshot_phase_s`. A future
"capture is stalling" report should start there — it distinguishes a blocked event
loop from a blocked device in one reading, which nothing the station published
before could do. `retention.enabled` in the snapshot now reflects the station's
setting rather than always claiming `true`.

**Reviewed 2026-08-29:** the pacing decision holds and is implemented as written.
`retention_interval_s` still defaults to 300.0 (`src/open_observatory/config.py:414`),
housekeeping still runs the sweep every `max(1, round(retention_interval_s / 10.0))`
ticks on the evidence executor (`src/open_observatory/station.py:1987`), and the
station still reports `retention.interval_s` and an `enabled` taken from the setting
rather than from the sweeper. Two things a reader should not carry forward:

- **"Why pacing rather than making the sweep cheaper" was overtaken.** Pacing cut the
  frequency of the stall, not its cost, and the cost then grew: on the same ~302 s beat
  the sweep later starved the loop long enough to empty the 500 ms ring, and that time
  audio was lost for real rather than only claimed (finding 2 of
  [[OPEN_INVESTIGATION_CAPTURE_GAPS]]). The sweep did have to be made cheaper in the
  end — [[ADR-061 - Operator keep flag|ADR-061]] removed the unbounded preamble query that ran before any deadline
  check, and [[ADR-062 - Retention walks live assets|ADR-062]] stopped the tiers walking the whole history. Pacing was
  necessary and not sufficient.
- **`snapshot_phase_s` is published by `/api/v1/station` only.** The Consequence above
  names both endpoints. `/api/v1/health` builds its payload from `status_snapshot()`
  and so does carry `loop_lag_max_s`, `loop_lag_events` and `housekeeping_blocking_s`
  under `capture`, but not the phase breakdown; `snapshot_phase_s` appears nowhere in
  `src/open_observatory/api/app.py`.

---
Part of the [[ADRS|Architecture Decision Record index]].
