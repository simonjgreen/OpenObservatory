# Drift gate (c), 2026-08-29 — **aborted at 35.6 minutes, and it cost the live station 2.2 s of audio**

Gate (c) is Milestone 1's exit gate as literally written — "one-hour
generated/replayed stream shows no timestamp drift or unexplained gaps" — which
[[ADR-069 - Two drift gates|ADR-069]] found had never been run, because "the
one-hour drift run" had come to mean two other things. This was the first attempt.
It did not complete, and the reason it did not complete is the finding.

## What was run

A second station on the same Pi, alongside the live one:

```bash
OO_DATA_DIR=$HOME/gate-c-data OO_BIND_HOST=127.0.0.1 OO_BIND_PORT=8081 \
  taskset -c 2,3 nice -n 19 .venv/bin/oo serve --source synthetic
```

Started `2026-08-29T20:22:48Z`. Fenced to cores 2–3 at `nice 19` — deliberately the
same fence [[ADR-045 - Refinement runner|ADR-045]] gives the refinement runner,
chosen so the live capture would keep the other two cores. Own data directory, own
port, its own fresh SQLite database (which incidentally exercised
[[ADR-042 - Migrations run in deploy.sh|ADR-042]]'s empty-database bootstrap path).

## What it showed before it was stopped, at 35.6 minutes

| | measured | criterion (fixed in advance, [[MILESTONE_STATUS]]) |
|---|---|---|
| duration | 2,137 s (35.6 min) | ≥ 3,600 s — **not met, aborted** |
| `stream_restarts` | 0 | 0 ✅ |
| `estimated_missing_seconds` | 0.0 | 0 ✅ |
| `gaps_with_loss` | 0 | 0 ✅ |
| `discontinuities` | 0 | 0 ✅ |
| `continuity_ratio` | 0.999988 | ≥ 0.9999 ✅ |
| frames / expected | 102,595,200 / 102,596,394 | deficit 1,194 frames = 24.9 ms |
| `rate_offset_ppm` | `null` | ≤ 1 — **unmeasurable**, see below |

On its own terms the synthetic path was clean for 35.6 minutes: no loss, no gaps,
no discontinuities, no restarts, and a deficit of 25 ms that is sampling phase
rather than drift.

**One criterion was written wrong.** `rate_offset_ppm` is `null` on a synthetic
source — there is no crystal, so `AlsaSource` never computes one. Asking for
`|rate_offset_ppm| ≤ 1` was asking the station for a number it has no way to
produce. The right criterion for a generated stream is the one above it: the
deficit is bounded and no frames are missing. Recorded rather than quietly
reworded, because a criterion fixed in advance and then found unmeasurable is
worth more as a correction than as a deletion.

## Why it was stopped: it cost the live station real audio

At `2026-08-29T20:32:37Z`, ten minutes into the run, the **live** station took an
`overrun` and lost **2.2185 s** of audio. Before the run it had `gaps_with_loss` 0
and `estimated_missing_seconds` 0.0 on a stream then ~4 hours old; the poll
immediately before 20:32 still read 0, and the poll after read 1. Nothing else
about the station changed in that window.

The run was stopped as soon as that was seen, at 35.6 of the required 60 minutes.
`CLAUDE.md` puts capture correctness above everything this run was trying to prove,
and the charter's ordering says the same: a convenience test does not get to spend
evidence.

**This falsifies a claim written into `IMPLEMENTATION_PLAN.md` earlier the same
day** — that because gate (c) "needs no microphone, it cannot disturb capture".
Opening no ALSA device is not the same as not disturbing capture. A second station
resamples, runs three detectors, writes clips and drives a database on the same
four cores and the same I/O, and the CPU fence did not prevent the interference; it
only chose which cores it came from. This is the same class of failure
[[ADR-033 - Retention is paced|ADR-033]] and
[[ADR-060 - A stalled read is a dead stream|ADR-060]] documented from inside one
process, arriving here from a second one.

## How gate (c) should be run instead

One of these, and the choice is the operator's:

1. **With the live station stopped.** Honest, cheap, and it makes the hour mean
   what Milestone 1 meant. It costs an hour of recording, deliberately, rather than
   an unpredictable amount by accident.
2. **On a different machine.** It is a synthetic source; it does not need this Pi's
   microphone. It does need this Pi's *class* of hardware for the result to speak
   to the target, so a second Pi 5 would be the faithful version.
3. **Accepting the cost, with it declared first** — pause recording for the hour
   ([[ADR-055 - Timed recording pause|ADR-055]]) so the loss is recorded as a pause
   rather than as a gap, and the record says the hour was spent on purpose.

Not recommended: repeating exactly this run and hoping. It has now been measured
once, and the measurement was a loss of audio.

## Artefacts

The synthetic station's data directory (`~/gate-c-data` on the station) was left in
place, including its fresh database. It is not committed — it is a scratch
directory of generated audio and its detections, and [[ADR-020 - Non-live sources excluded|ADR-020]]
is why those detections must never reach a browsing view: they were produced from a
synthetic scene and are labelled as such at the source.
