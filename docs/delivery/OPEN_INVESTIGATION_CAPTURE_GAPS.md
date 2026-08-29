# Capture gaps and overruns: what was measured, what was fixed, what is still open

> **How to read this file.** It is a chronological engineering record, appended to
> across five rounds, and **later sections supersede earlier ones**. Several
> paragraphs are preserved with wording that was true when written and is not now
> ("not yet deployed", "still unproven on target"); each carries a pointer
> forward. If you want the current state, read the **last** section first, then
> come back for the reasoning.
>
> Nothing here is a soak. The longest window recorded in this document is 4.03
> hours; the longest *clean* one is 22.2 minutes. **The 72-hour soak ran
> 2026-08-10 to 2026-08-13 and failed** its continuity criterion (99.865%
> against ≥ 99.9%; see [[MILESTONE_STATUS]] §Milestone 4.5) — the first
> window long enough to expose a deficit this document's shorter runs could
> not.

> **2026-08-08, evening.** Gaps returned after seven branches were merged into
> `main` and deployed at 17:13Z. They were caused by the retention sweep moving to
> a 10 s cadence, and they were **not** losing any audio. Both statements are
> measured; see "The regression of 2026-08-08 evening" at the end of this document,
> and [[ADR-033 - Retention is paced|ADR-033]]. The ALSA-ring fix described below was not implicated and is not in
> question.

Rewritten 2026-08-08 (afternoon session) from the handover of the same day. The
previous version listed four untested hypotheses. Three of them have now been tested
on the live station. Everything below is measured on the Pi unless it is explicitly
labelled inference.

## Summary

Two independent defects were found in the capture path, both by measuring a property
rather than reasoning about it.

1. **The ALSA ring was shallower than one capture block.** `AlsaSource` requested
   `periods=8` at a 10 ms period — an **80 ms** kernel ring behind a **100 ms** read.
   The station could not absorb a scheduling stall of a tenth of a second.
2. **An ALSA overrun's cost was never estimated.** The frame-deficit estimator was
   gated on `discontinuity is None`, so any block on which ALSA had already raised
   EPIPE skipped the estimate and was published with `missing_frames=0`. The single
   event most likely to have lost audio was the one event whose cost was not measured.

Both are fixed ([[ADR-030 - ALSA ring and capture thread|ADR-030]]). The ring is now sized from `capture_buffer_ms` (500 ms
default) and the estimate runs either way, with gaps reported split into
`gaps_with_loss` and `gaps_without_loss`.

## Result, measured on the live station

Two windows of comparable length, same afternoon, same station, same
`config/runtime.env` — the code was the only variable:

| | Before (44.8 min) | After (44.3 min) |
|---|---|---|
| Window (UTC) | 13:08:50 → 13:53:40 | 14:39:00 → 15:23:19 |
| ALSA ring | 30,720 frames (80 ms, 8 periods) | 192,000 frames (500 ms, 50 periods) |
| Continuity | 0.999376 | **0.999945** |
| `capture.gap` records | 24 | **0** |
| — with real audio lost | 9 | **0** |
| — with nothing lost | 15 | **0** |
| ALSA overruns (EPIPE) | 14 | **0** |
| Audio lost | 1.16 s | **0** |
| Per-block hot-path CPU | 10.6% of one core | 10.55% of one core |
| Reported device offset | −245 to −270 ppm | **−52 ppm** |

Zero gaps and zero overruns is the figure [[HANDOVER]] records for normal running and
which the station had stopped achieving. Continuity of 0.999945 is *above* the
known-good band of 0.9990–0.9997. Hot-path CPU is unchanged, which is the expected
result: a deeper kernel ring does no extra work, it only tolerates more delay.

The device offset moving from −245 ppm to −52 ppm is a second, independent confirmation
that the estimator was wrong rather than the hardware: the true measured offset recorded
in [[TARGET_DIAGNOSTICS]] is −43 ppm, and uncredited lost frames were what dragged the
figure five times below it.

**This is a 44-minute daytime window, not a soak.** It is not evidence that the problem
cannot return under a busy night's load. See "What is still open".


## What was measured, in order

### Baseline, before any change

Clean window, no work being done on the Pi, station running the restored settings
(`OO_ULTRASONIC_AUDIBLE_METHOD=both`, `OO_CLIP_MAX_PER_MINUTE=20`):

| Window | 2026-08-08 13:08:50Z → 13:53:40Z (44.8 min) |
|---|---|
| Continuity | 0.999376 |
| `capture.gap` records | 24 |
| — of which lost real audio | **9** |
| — of which lost nothing measurable | **15** |
| ALSA overruns (EPIPE) | 14 |
| Audio actually lost | 445,431 frames = **1.16 s** |
| Individual losses (frames) | 90910, 43774, 46272, 40467, 42505, 41326, 40864, 55787, 43526 |

Every single real loss is between 40,467 and 90,910 frames — **0.105 s to 0.237 s**,
that is, about one capture block each. A ring that overflows because nobody drained it
in time loses roughly what it was holding. This is the signature that pointed at the
ring, and it is visible only once the two kinds of gap are separated.

### A natural experiment: CPU load

The test suite was run on the Pi during the second half of the pre-fix run. That was
not planned as an experiment, but it is a clean one, because nothing else changed:

| Window (same station, same settings) | Gaps/min | Audio lost per minute |
|---|---|---|
| 13:08:50Z → 13:53:40Z, station idle | 0.49 | 0.021 s |
| 13:53:40Z → 14:38:27Z, pytest running | 0.80 | 0.041 s |

Sustained CPU load roughly **doubled** both figures. That is consistent with a
scheduling-stall mechanism and inconsistent with a bus, device or storage mechanism —
none of which care what the CPU is doing. It is also why hypothesis 4 (CPU contention
from the restored rendering settings) was plausible: it is the same mechanism. The fix
addresses the mechanism rather than the load.

## The hypotheses, resolved

### 1. USB bus / host-controller contention — RULED OUT by topology

The AudioMoth and the SSD are on **different xHCI host controllers**, so no physical
port move is required and none is recommended for capture's sake:

| Device | Bus / port | Controller | Speed |
|---|---|---|---|
| AudioMoth | 002 / 1 | `1f00200000.usb` → `xhci-hcd.0` | 12 Mbit/s (full speed) |
| SanDisk Extreme SSD | 004 / 2 | `1f00300000.usb` → `xhci-hcd.1` | 480 Mbit/s (high speed) |

The previous session's reading of `lsusb` was right that both buses report as
`1d6b:0002` USB 2.0 root hubs, but that is because each Pi 5 xHCI controller exposes a
USB 2.0 root hub *and* a separate USB 3.0 one (`usb3` and `usb5`). The two devices are
not sharing anything.

Two real facts did come out of this, and they are recorded in
[[TARGET_DIAGNOSTICS]]:

- **The AudioMoth is a full-speed device at 75% of its bus budget.** Its isochronous
  IN endpoint declares `wMaxPacketSize 768 bytes, bInterval 1` — 768 bytes every 1 ms,
  against a full-speed maximum of 1023. There is no bus headroom to find and no faster
  port to move it to. Host-side slack is the only lever, which is what was fixed.
- **The SSD is in a USB 2.0 port**, running at roughly a tenth of its capability. This
  does not affect capture. If it is moved, it must go to the blue port that enumerates
  as `usb5` (same controller, `xhci-hcd.1`); the other blue port is `usb3` on the
  AudioMoth's controller and would *create* the contention that currently does not
  exist.

### 2. Capture reads share the default thread pool — CONFIRMED as a real exposure, FIXED

`alsa_source.read` used `asyncio.to_thread`, i.e. the default executor: 8 workers on a
4-core Pi, shared with database inserts, gap-row writes, health-event writes, device
probes and every FastAPI `def` endpoint — and SQLite is configured `busy_timeout=5000`,
so one contended write can hold a worker for seconds. `AlsaSource` now owns a private
single-thread executor (`oo-capture`) for open, read and close.

This is stated as a real exposure rather than a measured cause: it was fixed in the
same deploy as the ring, so the two are not separated by measurement. The CPU-load
experiment above is consistent with it but does not isolate it.

### 3. ALSA period/buffer sizing too tight — CONFIRMED, and the primary finding

`/api/v1/health` reported the negotiated configuration all along, and it said:

```
"period_size": 3840, "periods": 8, "buffer_size": 30720,   # 80 ms
"block_frames": 38400,                                     # 100 ms
```

The kernel could hold **less audio than one read consumes**. The ring is the only slack
the capture path has — it is how much audio may accumulate while nothing is reading —
and between reads the event loop runs the resampler, two spectrograms, level telemetry
and window dispatch. The ring is now 192,000 frames (500 ms, 50 periods), verified from
the station's own negotiated figures after the deploy. It costs 384 kB and adds no
latency: a read still returns as soon as one block's worth of frames exists.

### 4. CPU contention from the restored settings — NOT TESTED, and deliberately so

`OO_ULTRASONIC_AUDIBLE_METHOD=both` and `OO_CLIP_MAX_PER_MINUTE=20` were left exactly
as they were, so that the code change was the only variable between the before and
after windows. Reverting them remains available as a cheap experiment if gaps return.
The CPU-load experiment above suggests load does matter at the margin, so this is a
real effect — but treating it by throttling the product would have been treating the
symptom, and the mechanism it acts through is the one that has now been widened.

## How much recording was actually lost, 2026-08-07 to 08-08

The previous handover said this was unresolved and should be re-derived. It has been.
**The three facts do reconcile, and the `frame_count` was the honest one.**

The stream row for `9d210aae…` claims 2026-08-07 03:38:54 → 2026-08-08 11:36:36 with
`frame_count` 3,852,212,352 — 2.79 hours of audio across a 32-hour window. Its
supporting rows say plainly which of those is true:

| Evidence for that stream | Value |
|---|---|
| `frame_count` | 3,852,212,352 = **2.786 h** |
| `capture_gap` rows | 89, from 2026-08-07 03:39:05 to **06:24:45** |
| `detection` rows | 2089, from 2026-08-07 03:38:57 to **06:26:07** |
| `discontinuity_count` | 245 |

Gaps, detections and frames all stop together at about **06:26 on 2026-08-07**. The
`end_utc` of 2026-08-08 11:36:36 is not when audio stopped; it is when the process
finally raised `ALSA read failed: File descriptor in bad state` and closed the row, 29
hours later. No stream row and no detection of any kind exists between 2026-08-07
06:26 and 2026-08-08 12:03.

**So roughly 29.6 hours of recording were lost**, from 2026-08-07 ~06:26 to 2026-08-08
~12:03 — not "roughly a day", and not spread across the window as the row's span
implies. The cause is the one already documented in [[HANDOVER]] §7: the AudioMoth's
mode switch was moved, the device stopped presenting audio, and nothing noticed.
Within the 2.79 h that *did* record, gaps cost 4,072,782 frames = **10.6 s**.

### A capture-side defect found while deriving that: stream rows record nothing

`frame_count` and `discontinuity_count` are written **only** by `_close_stream_row`,
which runs on a graceful stop. Measured on the station:

| `audio_stream` rows | 49 |
|---|---|
| with `frame_count > 0` | **1** |
| ended by the orphan sweep (`process_exited`) | 47 |

Every row ended by a kill, a crash or a redeploy says the stream captured zero frames.
The single exception is the one stream that ended through the supervisor's own error
path — the 32-hour row above, which is why it was the only one with a usable number.
**Any capture coverage computed from `frame_count` therefore reads zero for almost
every session this station has ever recorded.** The station now checkpoints the running
totals into the open row every 30 s, so a crashed stream's row says what it took.

This is reported rather than acted on beyond the capture side: the history aggregation
and coverage layer belong to another workstream.

### Still unexplained, and worth a successor's attention

At 2026-08-08 10:55:24Z the journal logs `capture.gap missing_frames=43890` against
that stream, but there is **no `capture_gap` row anywhere in the database at that
time** — the rows for that stream stop 29 hours earlier. A gap row is written only when
`missing_frames > 0`, so one should exist. The likeliest explanation is that the insert
raised and the exception was swallowed: it was dispatched with a bare
`create_task(asyncio.to_thread(...))` whose exception nobody retrieved. A SQLite
`database is locked` after the 5 s busy timeout, during the heavy backfill clip writing
visible in the log at that moment, would look exactly like this. **This is inference,
not measurement.** A `done` callback now logs `capture.gap_row_failed`, so the next
occurrence will say so instead of vanishing.

## Instrumentation added

- `/api/v1/health` `capture` now reports `gaps_with_loss`, `gaps_without_loss`,
  `estimated_missing_seconds` and `alsa_buffer_frames`. `discontinuities` is the sum of
  the first two.
- The `capture.gap` log line carries `lost_audio=true|false`.
- Prometheus: `oo_capture_gaps_with_loss_total`, `oo_capture_gaps_without_loss_total`,
  `oo_capture_alsa_overruns_total`, `oo_capture_alsa_buffer_frames`.
- `capture.buffer_shallower_than_block` warns at open if ALSA clamps the ring below one
  block — the condition that caused this whole investigation and that no other counter
  the station publishes would have revealed.
- `capture.gap_row_failed` logs a gap-row insert that raises.

## Traps this investigation produced

- **`grep -c capture.gap` overstates lost recording by roughly 2.7×**, measured. Use
  `gaps_with_loss` from `/api/v1/health`, or grep for `lost_audio=True`.
- **`missing_frames=0` used to mean "not measured", not "nothing lost".** Any log line
  from before 2026-08-08 15:00 that says so should be read as unknown, and any
  `rate_offset_ppm` from before then is contaminated in the same way: the station read
  −245 to −270 ppm against a true device offset near −43 ppm, purely because losses it
  had not credited looked like a slow crystal.
- **`deploy/deploy.sh --no-web` will delete the Pi's `web/dist`.** The rsync uses
  `--delete` and does not exclude `web/dist`, so deploying without building the UI
  removes it from the target and the dashboard stops being served. Either build the web
  UI, or deploy only what changed (`rsync -a --delete --exclude __pycache__ ./src/
  HOST:open-observatory/src/` then `sudo systemctl restart open-observatory`).
- **`rsync --delete` into `src/` fails on root-owned `__pycache__`** left by the
  service. Exclude `__pycache__`.
- **A stream row's `end_utc` is not when audio stopped.** It is when the process
  noticed. Cross-check against `capture_gap` and `detection` rows before believing a
  span.

## What is still open

- **Whether 500 ms is enough** under a genuinely busy bat night, which is when evidence
  writing, three detectors and clip rendering all peak together. The measurement above
  is a daytime one. If gaps return, the ring is the first thing to widen
  (`OO_CAPTURE_BUFFER_MS`), and the next structural step — a free-running reader thread
  feeding an internal queue — is described and deliberately deferred in [[ADR-030 - ALSA ring and capture thread|ADR-030]].
- **The missing gap row of 2026-08-08 10:55:24Z**, above. Inference only.
- **Hypothesis 4 was never isolated.** The restored rendering settings were left in
  place on purpose so the code was the only variable. They may still cost something at
  the margin; the CPU-load experiment says load matters. Nobody has measured them alone.
- **No 72-hour soak has run.** These are 45-minute windows.

## Smoke test on the target

```bash
ssh <user>@<station-host>
curl -s localhost:8080/api/v1/health | python3 -m json.tool | head -40
# Expect, after several minutes of running:
#   alsa_buffer_frames  192000        (500 ms; must exceed block_frames 38400)
#   gaps_with_loss      0
#   gaps_without_loss   0
#   overruns            0
#   continuity_ratio    >= 0.9990
#   rate_offset_ppm     around -43 to -55, not -200-something
sudo journalctl -u open-observatory --since "-30 min" | grep 'lost_audio=True' | wc -l
curl -s localhost:8080/metrics | grep oo_capture_
```

`capture.buffer_shallower_than_block` in the log at open means ALSA clamped the ring
below one block and the original failure mode is back.

## Rollback

The change is confined to `src/`, with no schema change and no new dependency.

```bash
git revert 3db9092
rsync -a --delete --exclude __pycache__ ./src/ <user>@<station-host>:open-observatory/src/
ssh <user>@<station-host> sudo systemctl restart open-observatory
```

The ring depth alone can be rolled back without reverting anything, by setting
`OO_CAPTURE_BUFFER_MS=80` in `config/runtime.env` and restarting. Note that
`_periods_for_buffer` floors the ring at two capture blocks, so 80 will still yield
200 ms; reproducing the original 80 ms ring needs the code change reverted.

---

# The regression of 2026-08-08 evening

Seven branches were merged into `main` and deployed at **17:13Z**. `capture.gap`
came back immediately: zero in the 73 minutes before the deploy, ~1.9/min after it.
MQTT was ruled out by the operator before this investigation started (same rate with
the publisher off and on). The Pi was not loaded: load average 0.33–0.78, `oo` at
18% CPU, 53.8 °C, `get_throttled=0x0`.

## Finding 1: the retention sweep's 10 s cadence starved the event loop

The merge moved `RetentionSweeper.sweep()` from every 5 minutes (`ticks % 30`) to
every housekeeping tick. It runs in the evidence executor's own thread and bounds
itself to `batch_size=200` / `batch_budget_s=1.5`, taking ~0.30 s per call.

**Single-variable experiment**, `OO_RETENTION_ENABLED` the only thing changed
between the two windows, same code, same `config/runtime.env` otherwise:

| Window (UTC) | Retention | `capture.gap` | `estimated_missing_frames` | `overruns` | `rate_offset_ppm` | `loop.lag` events |
|---|---|---|---|---|---|---|
| 18:01–18:06 (5 min) | every 10 s | **8** (1.6/min) | 252,495 | 0 | **+2,680** | ~25 (5/min) |
| 18:06–18:14 (7 min) | disabled | **0** | **0** | 0 | **−51.75** | 11 (1.6/min) |

The fix is [[ADR-033 - Retention is paced|ADR-033]]: a `retention_interval_s` setting, default 300 s, rounded to the
nearest tick. The pre-merge behaviour, restored behind a knob.

### The fix, verified on the station

Deployed 18:16:58Z, retention re-enabled, 15 minutes measured:

| | 10 s cadence | disabled | **300 s cadence (the fix)** |
|---|---|---|---|
| Window | 5 min | 7 min | **15 min** |
| `capture.gap` | 8 (1.6/min) | 0 | **2 (0.13/min)** |
| `continuity_ratio` | — | — | **0.999869** |
| ALSA `overruns` | 0 | 0 | **0** |
| `loop_lag_max_s` | 0.140 | 0.140 | **0.142** |
| `hot_path_cpu_ratio` | 0.1123 | — | **0.1059** |
| `housekeeping_blocking_s` | 0.0012 | — | **0.0017** |

**A 12× reduction, not elimination — and the residue proves the mechanism.** Both
remaining gap records land on a retention sweep to the second: `last_sweep_at`
18:31:41.595, `capture.gap` at 18:31:41.930; the other at 18:26:41.400, five minutes
earlier. Every sweep costs ~120 ms of event-loop lag and roughly two sweeps in three
trip the estimator. Pacing changes how often that happens, not what happens.

Two further levers exist and neither was taken, because each belongs to somebody
else's territory and neither is needed to close the regression:

- **The sweep does no work at all right now** — `total_deleted=0`, every clip is
  under seven days old, the disk is 6.3% full — and still costs 0.40 s of ORM
  queries. A cheap precondition (oldest clip younger than `retention_native_days`
  *and* disk below the watermark ⇒ return immediately) would make the common case
  free. That is a change to `retention.py`, [[ADR-026 - Tiered clip retention|ADR-026]]'s author's code.
- **The estimator should not be firing at all**, since no audio is being lost — see
  finding 2 below. Fixing that removes the visible symptom without touching
  retention, but it is the wrong order: the loop stall is real whether or not it is
  reported, and it would be reported by nothing else.

## How it was isolated, in order — and what each step ruled *out*

Three things were eliminated before retention was reached, each by measurement, and
each elimination is worth as much as the finding:

1. **The event loop really was being blocked, ~250 ms, on a ~10.4 s beat.** No
   deploy needed for this one: a script on the Pi polled `/api/v1/station` every
   50 ms for 3 minutes and timed the responses. Median 4.65 ms; thirteen outliers of
   140–270 ms, spaced 10.4 s apart. The API handler runs on the event loop, so a
   slow response is a blocked loop. **This is the cheapest possible first
   measurement for any "capture is stalling" report and it needs nothing installed.**
2. **`status_snapshot()` was innocent — 1.2 ms.** It was the obvious suspect: it
   runs synchronously on the loop every tick, and it now carries retention, clip,
   storage and detector sections. `snapshot_phase_s` (added permanently) puts a
   number on every contributor; the largest was `_version()` at 0.6 ms. Nothing else
   reached 0.1 ms. Reasoning would not have settled this — `clips.disk_usage()`
   walks the clip tree, and looks far more expensive than it is because the walk is
   cached for 30 s.
3. **The whole synchronous part of the tick was innocent — 1.2 ms total**, and the
   housekeeping loop's own sleep woke on time (`loop_lag_s` ≈ −0.0002). That is what
   pointed at the *second half* of the tick, after the `bus.emit`: the [[ADR-024 - Coverage bounded by frames|ADR-024]]
   heartbeat and the retention sweep, both awaited, both invisible to a metric that
   only watches the sleep. The dedicated `loop.lag` watchdog — a task that sleeps
   0.1 s in a loop and reports its overshoot — covers the whole tick and is what
   produced the table above.

## Why a dedicated thread did not protect capture

This is the part that matters for the next person, because the architecture already
looked correct. Retention has had its own executor since [[ADR-021 - Clips on their own device|ADR-021]] precisely so it
could not queue in front of the ALSA read, and [[ADR-030 - ALSA ring and capture thread|ADR-030]] gave the read its own
executor too. Neither helps here.

The sweep is SQLAlchemy ORM work in Python, so it **holds the GIL**, and CPython
returns the GIL to a waiting I/O-bound thread reluctantly. The event loop is that
I/O-bound thread, and it still has to issue every `run_in_executor` capture read and
consume its result. A read issued 130 ms late starts 130 ms late no matter how
private its thread is. **An executor partitions queueing, not scheduling, and
nothing partitions the GIL.** "Give it its own thread" is necessary and not
sufficient; the test is whether the work is CPU-bound in Python.

## Finding 2: `capture.gap lost_audio=True` was lying — no audio was lost

> **FIXED 2026-08-09, [[ADR-039 - Confirmed loss, not deficit|ADR-039]]. Since deployed and confirmed on target** — see
> "Confirmed on the target" and the 2026-08-09 late section at the end of this
> document; the "not yet deployed" wording below is preserved as written.
> The estimator now confirms a
> deficit step against the following blocks before crediting it, and reserves
> `reason=overrun` for a step ALSA actually reported. Measured off-target against
> a fake device with a real ring: against a trace that loses **nothing**, the old
> estimator reported 259,596 phantom frames and +15,013 ppm where the new one
> reports **0** frames, 8 `late_reads` and **-43.0 ppm** (the device's true
> offset). Against a trace where the device really drops 422,365 frames, the new
> estimate is 422,444 — **0.019%** error, agreeing with `expected_frames - frames`
> to 845 frames (2.2 ms) — where the old one claimed 779,406. The live-station
> corroboration and the on-target verification that is still outstanding are at
> the end of this document under "Closing finding 2".

Measured mid-regression, from one `/api/v1/station` reading:

| | Frames | Seconds |
|---|---|---|
| `frames` actually captured | 92,505,600 | — |
| `expected_frames` from elapsed time | 92,526,900 | — |
| **Real deficit** | **21,300** | **0.055** |
| `estimated_missing_frames` claimed | 252,495 | 0.657 |
| `overruns` (ALSA EPIPE) | **0** | — |

The station claimed to have lost twelve times more audio than it was actually
behind, and ALSA never reported a ring overflow at all. It could not have: the
500 ms ring from [[ADR-030 - ALSA ring and capture thread|ADR-030]] absorbs a 130 ms stall and then catches up, which is
exactly what it was widened to do.

The cause is in `_read_blocking`. The deficit-step estimator credits any step of
more than one block in `expected_frames − frames_read` as lost audio *immediately*,
and labels it `reason=overrun` even when ALSA said nothing. Against an 80 ms ring
that inference was sound — a step that big really had overflowed. Against a 500 ms
ring it is not, because the frames are still in the kernel and arrive on the next
read. Each stall therefore mints one phantom gap.

**This also resolves the nonsense `rate_offset_ppm`.** It is not a second bug and
not a hardware fault: phantom frames are added to `presented` in the observed-rate
calculation, and 252,495/92,505,600 = 2,729 ppm — the +2,680 ppm that was read. With
retention disabled and no stalls, the same station read **−51.75 ppm**, against the
true device offset of −43 ppm in [[TARGET_DIAGNOSTICS]].

**Not fixed here**, deliberately: correcting the estimator means changing the
afternoon session's work while its author is not around to check it, and once
finding 1 is fixed the estimator stops firing anyway. The fix is to confirm a
deficit step against the following few blocks before crediting it, and to reserve
`reason=overrun` for a step ALSA actually reported. Until then, read
`capture.gap lost_audio=True` as **"the read was late"**, and cross-check
`estimated_missing_frames` against `expected_frames − frames` before believing it.

## Traps this round produced

- **`journalctl --since/--until` takes LOCAL time; the station logs UTC.** BST is
  UTC+1, so an hour's worth of conclusions can be drawn about the wrong window. This
  was got wrong once today and produced the opposite answer. Always print both.
- **`retention.snapshot()` used to hardcode `"enabled": true`** whatever the setting
  said, because `RetentionSweeper` does not know whether anyone calls it. Fixed —
  the station now overrides it — but if you are on an older build, verify a
  retention experiment against `last_sweep_at`, not against `enabled`.
- **A `loop_lag` metric taken across a task's own `sleep` misses everything that
  task does after waking.** The first version of this instrumentation measured only
  the sleep overshoot, reported ~0, and would have exonerated housekeeping entirely.
  Lag has to be watched by a task that does nothing else.
- **`grep -c capture.gap` still overstates lost recording**, now for a second and
  larger reason on top of the one recorded above: with a 500 ms ring, most gaps cost
  nothing at all.

## What is still open after this round

- ~~**The deficit-step estimator over-credits**, finding 2 above. Not fixed.~~
  **Fixed 2026-08-09 ([[ADR-039 - Confirmed loss, not deficit|ADR-039]]); not yet deployed.** See "Closing finding 2" below.
- **A residual ~1.6 event/min of 60–120 ms event-loop lag on a 30 s beat**, present
  with retention disabled, producing no gaps. Unattributed. `clips.disk_usage()`
  caches its clip-tree walk for exactly 30 s and is the obvious candidate, but that
  is inference — it measured 0.0 ms on the sample taken, which is what a cache hit
  looks like.
- Everything in the previous "What is still open" section: no 72-hour soak, no
  isolation of hypothesis 4, the missing gap row of 10:55:24Z.

## Rollback and smoke test for the ADR-033 change

The change is confined to `src/`, with no schema change and no new dependency. The
cadence can be reverted with no deploy at all:

```bash
# Restore the every-tick behaviour without touching code:
echo 'OO_RETENTION_INTERVAL_S=10' >> ~/open-observatory/config/runtime.env
sudo systemctl restart open-observatory
# Or disable retention entirely:
echo 'OO_RETENTION_ENABLED=false' >> ~/open-observatory/config/runtime.env
```

Smoke test on the target, after several minutes of running:

```bash
ssh <user>@<station-host>
curl -s localhost:8080/api/v1/station | python3 -m json.tool | grep -E \
  'loop_lag_max_s|loop_lag_events|housekeeping_blocking_s|continuity_ratio|overruns'
# Expect: housekeeping_blocking_s < 0.01, overruns 0, continuity_ratio >= 0.9990.
# loop_lag_max_s around 0.14 is the current normal and is NOT yet zero.

# Remember: journalctl takes LOCAL time, the log lines are UTC.
sudo journalctl -u open-observatory --since "-15 min" | grep -c capture.gap   # expect 0-2
sudo journalctl -u open-observatory --since "-15 min" | grep loop.lag | tail
curl -s localhost:8080/api/v1/station | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["snapshot_phase_s"])'
```


---

# Closing finding 2: the estimator now measures loss instead of lateness

2026-08-09. [[ADR-039 - Confirmed loss, not deficit|ADR-039]]. **Written but not deployed** *at the time this section was
written* — the station was owned by another agent that session, so everything in
this section is either an off-target measurement or a read-only reading of the
*unfixed* station. It has since been deployed and confirmed twice; see the two
sections that follow.

## What changed

`AlsaSource._settle_deficit` replaces the immediate credit. A step larger than one
block, or an EPIPE, opens a suspicion; the lowest deficit over the next ring-plus-
two-blocks (0.7 s on target) is taken as the part that never came back, and only
that is credited. So `estimated_missing_frames` is now a decomposition of
`expected_frames - frames` rather than a second number that can disagree with it.

- `reason=overrun` only for an event ALSA reported. A confirmed loss with no EPIPE
  is the new `frame_deficit` reason.
- A late read that cost nothing is no longer a gap: `late_reads` and
  `late_read_max_frames` are new counters on `/api/v1/station`, and the log line is
  `capture.late_read` at info.
- `CaptureBlock.discontinuity_at_frame` carries where the loss happened, since the
  verdict now arrives a few blocks after the event.

## Measured off-target, against a device that drops frames on cue

The instrument is `RingedDevice` in `tests/test_alsa_source.py` — a fake capture
device with a real ring, its own crystal rate, and ALSA's own `Input/output error`
on overflow. `device.dropped` is ground truth injected by the test.

| Trace | Truth | Old estimate | New estimate |
|---|---|---|---|
| 8 stalls of 250–400 ms behind a 500 ms ring | **0 frames lost** | 259,596 (0.676 s), 5 gaps "with loss", **+15,013 ppm** | **0**, 0 gaps, 8 late reads, **−43.0 ppm** |
| 10 stalls of 150–1200 ms, ring overflows twice | **422,365 frames** | 779,406 (+84.5%) | **422,444 (+0.019%)** |

In the second trace `expected_frames - frames` was 423,289: the new estimate agrees
with it to **845 frames (2.2 ms)**, the old one disagreed by 356,117.

The first row reproduces the live defect almost exactly — 259,596 phantom frames
against the station's measured 252,495, with nothing lost and ALSA silent.

## Corroboration from the live station, read-only, 2026-08-09 08:43Z

This is the **unfixed** build still running:

| `frames` | `expected_frames` | Real deficit | `estimated_missing_frames` | `overruns` | `gaps_with_loss` | `rate_offset_ppm` |
|---|---|---|---|---|---|---|
| 376,089,600 | 376,133,372 | 43,772 (0.114 s) | 348,786 (0.908 s) | **0** | 7 | **+878** |

An 8.0x over-report in 16 minutes, every gap labelled as lost audio, ALSA
reporting no overrun. 348,786/376,089,600 = 927 ppm; 927 − 43 (the true crystal
offset) = 884, against the +878 observed. The contamination arithmetic is exact,
which is the strongest available evidence that the ppm figure and the estimator
are one defect and not two.

## On-target verification, still outstanding

Run this **before** deploying, on the station as it is:

```bash
ssh <user>@<station-host> "curl -s localhost:8080/api/v1/station" | python3 -c '
import json,sys; c=json.load(sys.stdin)["capture"]
d=c["expected_frames"]-c["frames"]
print("deficit      ", d, "frames =", round(d/c["sample_rate"],4), "s")
print("estimated    ", c["estimated_missing_frames"], "=", c["estimated_missing_seconds"], "s")
print("ratio        ", round(c["estimated_missing_frames"]/max(1,d),2), "x")
print("overruns     ", c["overruns"], " gaps_with_loss", c["gaps_with_loss"],
      " gaps_without_loss", c["gaps_without_loss"])
print("rate_offset  ", c["rate_offset_ppm"], "ppm")'
```

Then deploy (this is a deploy the orchestrator must serialise — it restarts
capture and resets every counter):

```bash
rsync -a --delete --exclude __pycache__ ./src/ <user>@<station-host>:open-observatory/src/
ssh <user>@<station-host> sudo systemctl restart open-observatory
sleep 1800   # 30 minutes, so the ratio is not dominated by startup
```

Run the same block again. **Pass criteria:**

- `ratio` ≤ 1.0 and the two figures within one block (38,400 frames) of each other,
  against ~8x before.
- `rate_offset_ppm` between −60 and −30. It was +878.
- `overruns` 0 and `gaps_with_loss` 0 while `late_reads` may be non-zero — that is
  the whole point: stalls are visible, losses are not invented.
- `late_read_max_frames` well below `alsa_buffer_frames` (192,000). If it
  approaches it, the ring is close to too shallow and `OO_CAPTURE_BUFFER_MS` is the
  lever — this counter is the early warning the station never had.

```bash
sudo journalctl -u open-observatory --since "-30 min" | grep -c capture.late_read
sudo journalctl -u open-observatory --since "-30 min" | grep 'lost_audio=True'
# Remember: journalctl takes LOCAL time (BST = UTC+1); the log lines are UTC.
```

## Rollback

Confined to `src/`, no schema change, no new dependency, no new setting.

```bash
git revert <this commit>
rsync -a --delete --exclude __pycache__ ./src/ <user>@<station-host>:open-observatory/src/
ssh <user>@<station-host> sudo systemctl restart open-observatory
```

There is no configuration lever to disable the new behaviour, deliberately: a
setting that restores a known-wrong measurement is not worth its own failure mode.

## Traps this round produced

- **`estimated_missing_frames` is now block-granular and settles late.** A gap is
  published up to 0.7 s after it happened, so do not correlate a `capture.gap`
  timestamp with an event to better than that — use `at_frame`, which is exact.
- **A loss smaller than one ALSA period (10 ms) is absorbed into the drift
  baseline, not credited.** The estimator can under-report by up to 10 ms per
  event. That is the stated tolerance, and it is the direction the project prefers
  to be wrong in.
- **`late_reads` is not a gap and must never be added to `discontinuities`.** It is
  the event-loop stall signal that `capture.gap` used to impersonate; it belongs
  next to `loop_lag_events`, not next to lost recording.
- **Any `rate_offset_ppm` or `estimated_missing_seconds` recorded before this fix
  is contaminated**, on top of the earlier contamination noted above. Only
  `expected_frames - frames` was trustworthy across the whole history of this file.

  > **Corrected by [[ADR-046 - Deficit is mostly drift|ADR-046]], later the same day, and this is the trap the file
  > itself fell into.** `expected_frames - frames` is not a loss figure either. It
  > is loss *plus* crystal drift (~0.18 s per hour at this device's −50 ppm, 4.4 s
  > per day, with nothing lost) *plus* a block-sampling phase artefact worth about
  > **±50 ms on any single reading**. Every deficit quoted in this document —
  > 0.055 s, 0.066 s, 0.104 s, 0.114 s — is one draw from that distribution and
  > none should be read as a point measurement. **After [[ADR-039 - Confirmed loss, not deficit|ADR-039]],
  > `estimated_missing_seconds` is the figure to judge lost audio by**; it is a
  > decomposition of the deficit rather than a rival number. Do not tell anyone to
  > prefer the raw deficit.

## Confirmed on the target, 2026-08-09 (ADR-039's pass criteria)

[[ADR-039 - Confirmed loss, not deficit|ADR-039]] shipped stating "this change has **not** been deployed to the Pi and no
on-target before/after exists". It has now been deployed, incidentally to the
[[ADR-040 - Spectrograms only when watched|ADR-040]] work, and the pass criteria above hold. Read from `/api/v1/station` on
the live station with the AudioMoth at 384 kHz, across several windows of an hour
of running:

| Criterion | Required | Measured | |
|---|---|---|---|
| deficit ratio | ≤ 1.0, within one block | `estimated_missing_frames` **0** against a real deficit of 25,245 frames (0.066 s) | pass |
| `rate_offset_ppm` | −60 to −30 | **−50.0, −50.3, −51.6, −53.2, −50.7** across five windows | pass |
| `overruns` | 0 | **0** | pass |
| `gaps_with_loss` | 0 | **0**; `grep -c 'lost_audio=True'` over 40 minutes returns **0** | pass |
| `late_reads` may be non-zero | — | **12** `capture.late_read` lines in 40 minutes | pass |
| `late_read_max_frames` ≪ `alsa_buffer_frames` | — | **57,952** of 192,000 (30% of the ring) | pass |

Against the pre-fix reading in [[ADR-039 - Confirmed loss, not deficit|ADR-039]] — 348,786 phantom frames on a real deficit
of 43,772 (**8.0x**), every gap labelled as having lost audio, `rate_offset_ppm`
**+878** — the estimator now reports nothing lost when nothing was lost, and the
observed rate lands on the device's true crystal offset. The stalls did not go
away and were never supposed to: they are now `late_reads` with their headroom
recorded, which is what the ring was widened for.

The one thing still not verified is the case [[ADR-039 - Confirmed loss, not deficit|ADR-039]]'s off-target tests cover but
the station has not produced: a stall the 500 ms ring genuinely fails to absorb.
No overrun occurred here, so the "estimate what a real loss cost" path remains
proven only against `RingedDevice`. **(Superseded: it fired on the target later
the same day — see the final section.)**


## Follow-up, 2026-08-09: the deficit has a bias of its own

[[ADR-039 - Confirmed loss, not deficit|ADR-039]]'s fix is confirmed in production — `rate_offset_ppm` reads −49.88
against +878 before, and `estimated_missing_seconds` no longer over-reports.

But a first post-deploy reading raises a question worth resolving rather than
waving through. At 7.5 minutes' uptime, with zero gaps and zero overruns:

```
estimated_missing_seconds : 0.0
expected_frames - frames  : 39,781 frames = 0.104 s
```

Those disagree, and this whole session used `expected_frames - frames` as the
ground truth against which the estimator was judged. It has its own bias:

- `expected_frames` is derived from elapsed time at the **nominal** rate. This
  device runs about **50 ppm slow**, so it legitimately delivers fewer frames
  than nominal implies. Over 450 s that is ~0.022 s of pure crystal offset,
  with **no audio lost**.
- The remainder is plausibly the frame-zero anchor, which matters
  proportionally more at short uptimes.

So the deficit is not a clean loss figure either — it is loss *plus* crystal
drift *plus* anchoring. Over a long run the drift term grows linearly (~0.18 s
per hour at 50 ppm) and will dominate, which means **a naive deficit reading
will eventually look like a slow leak of audio that is not happening**.

Neither number is wrong; they measure different things. What is missing is a
figure that subtracts the measured crystal offset from the deficit, which is
the quantity a human actually wants when they ask "did we lose any audio".

**To resolve:** take a reading at several hours' uptime and check whether the
deficit grows at the rate the measured ppm predicts. If it does, the estimator
and the deficit agree and the deficit simply needs drift-correcting before it
is displayed — `web/src/components/Pipeline.tsx` currently shows it raw as
"audio lost". If it grows faster, there is a real loss the estimator is now
missing, and [[ADR-039 - Confirmed loss, not deficit|ADR-039]]'s confirmation window is too permissive.

---

# RESOLVED, 2026-08-09: the deficit grows at the rate the measured ppm predicts

**Answer to the resolving check above: yes — and under artificial CPU load it
grows *slower*, not faster, which is the opposite of the failure mode being
tested for.** The deficit is crystal drift. The estimator's `0.0` is right.
**[[ADR-039 - Confirmed loss, not deficit|ADR-039]]'s confirmation window is not too permissive**; there is no real loss it
is missing. The display was the only thing wrong, and that is fixed in [[ADR-046 - Deficit is mostly drift|ADR-046]].

| | ppm |
|---|---|
| growth of the phase-corrected deficit, clean window A (10.5 min) | **+51.17** [50.52, 51.81] |
| growth of the phase-corrected deficit, clean window C (22.2 min) | **+51.00** [50.74, 51.30] |
| asymptote of `rate_offset_ppm` over the whole run | **+50.43** [50.20, 50.55] |
| a concurrent agent's independent figure, same station, same hour | **+49.96** |

At about 50.4 ppm the drift term is **0.18 s per hour, 4.4 s per day**. That is
the whole of the deficit, and none of it is lost audio. Counters at the end of
the run: `estimated_missing_frames` **0**, `gaps_with_loss` **0**,
`gaps_without_loss` **0**, `overruns` **0**.

Three things had to be fixed about the *method* before the check could be run,
and they are more transferable than the answer.

## First: the raw deficit is mostly a sampling artefact, not a measurement

`counters.frames` advances in whole blocks of **38,400 frames (100 ms)** while
`expected_frames` is computed from a continuous monotonic clock at snapshot
time. So `expected_frames - frames` sawtooths across an entire block *while the
station is perfectly healthy*. Measured over 43 minutes with zero gaps, zero
overruns and `estimated_missing_frames` at 0, the raw figure ranged **−162 ms to
+185 ms**, a spread of **347 ms**.

**A single reading of `expected_frames - frames` carries about ±50 ms of pure
artefact**, and larger transients on a late read. The **0.104 s** that opened
this question is inside that noise. Every figure quoted for this row anywhere in
this document — 0.055 s, 0.066 s, 0.104 s, 0.114 s — is one draw from that
distribution, and none of them should have been treated as a point measurement.
That, and not any leak, is most of what "the two measurements disagree" was.

The artefact is removable because the station already publishes the phase.
`block_age_s` is the age of the **last block's start**, so re-evaluating the
deficit at that instant cancels it:

```
corrected = expected_frames − block_age_s × sample_rate − (frames − 38,400)
```

That took the scatter from ~100 ms to **0.3 ms** (median absolute residual) — a
hundredfold — and is what made a 10-minute window sufficient where hours had
been assumed necessary.

Two traps found while using it. `block_age_s` sweeps **0.112 → 0.199 s** in
normal running, not 0 → 0.1 s, because it is measured to the block's *start* and
the block is one duration long; a filter written for the wrong range silently
discards half the samples and biases the slope. And a late read corrupts the
correction for a sample or two, because a block's `monotonic_start_ns` is
computed as read-completion minus one block duration, so a stalled read reports a
start later than the true one — visible as a −200 ms excursion that fully
recovers. Theil-Sen (median pairwise slope) over per-minute medians is immune to
both; ordinary least squares is not, and read 6 ppm high on the same data.

## Second: the station was being restarted every ~18 minutes

Every earlier reading was taken at short uptime because concurrent agent deploys
were restarting `open-observatory` — `station.started` at **09:52:43Z,
10:10:31Z and 10:29:05Z**, an 18-minute cadence, with `NRestarts=0` (systemd
never restarted it; each was a deliberate stop/start). Every counter resets on
each one. That, not any property of the capture path, is why "several hours'
uptime" was never available, and why the anchor-bias term was proportionally
large in every reading taken.

## Third: this window was contaminated by another agent's load probe, and it is declared

**This must be stated rather than reported through.** While this run was in
progress, a concurrent agent pinned two busy-loops to cores 2–3 under a systemd
cgroup fence. From the station's own journal (prefix BST, log lines UTC):

```
11:42:03+01:00 systemd[1]: Started oo-refine-loadprobe.service
                 - /bin/sh -c "for i in 1 2; do (while :; do :; done) & done; sleep 600; ..."
11:52:03+01:00 systemd[1]: oo-refine-loadprobe.service: Main process exited
11:52:03+01:00 systemd[1]: oo-refine-loadprobe.service: Consumed 19min 57.966s CPU time
```

**19 min 58 s of CPU in 10 min of wall clock — two cores saturated, exactly as
designed.** In UTC that is **10:42:03 → 10:52:03**, the middle of the sampling
run. Four earlier `oo-fence-probe{,2,3,4}` units (10:30:14–10:30:57 UTC) were
instantaneous configuration probes — started and deactivated in the same second,
no CPU — and they precede this window's opening at 10:31:32 UTC.

The load is visible in the station's own counters, which is how it was confirmed
rather than taken on trust: `loop_lag_max_s` stepped **0.2139 → 0.2553** at
10:43 UTC and **→ 0.2928** at 10:50, matching the other agent's independently
reported 214 ms → 293 ms; `late_reads` accrued at **1.1/min under load against
0.7/min clean**; and `hot_path_cpu_ratio` *fell* 0.1055 → 0.0913 as the station
was descheduled.

Rather than discard the run, it was split at the probe's own boundaries. That
turns the contamination into a control experiment — a stronger test than the one
originally planned, because the failure mode under suspicion (losses too small
for [[ADR-039 - Confirmed loss, not deficit|ADR-039]]'s window to credit) is precisely the one that should get *worse*
under scheduling pressure.

## The measurement

`/api/v1/health` sampled every 2 s from the development laptop. **All times UTC**
unless a command is shown, in which case its own timezone is stated.

- Stream `728ac7be-4fad-4bb5-b864-b604dae77852`, started **10:29:05.474916Z**.
  Process 354105, `ExecMainStartTimestamp` 11:29:02 BST, `NRestarts=0`, still
  running at the end — **no restart inside any window below**.
- Sampling window **10:31:32 → 11:14:13 UTC** (= 11:31:32 → 12:14:13 BST),
  1,268 samples, uptime 147 s → 2,708 s. AudioMoth at 384 kHz, `alsa`, mono.

| segment | window (UTC) | length | growth of phase-corrected deficit | max residual | late reads |
|---|---|---|---|---|---|
| **A** control, pre-load | 10:31:32 → 10:42:01 | 10.5 min | **+51.17 ppm** [50.52, 51.81] | 0.30 ms | +7 |
| **B** two cores saturated | 10:42:03 → 10:52:01 | 10.0 min | **+48.25 ppm** [47.12, 48.75] | 0.25 ms | +11 |
| **C** control, post-load | 10:52:03 → 11:14:13 | 22.2 min | **+51.00 ppm** [50.74, 51.30] | 0.44 ms | +22 |
| whole run (mixed — do not quote) | 10:31:32 → 11:14:13 | 42.7 min | +50.32 ppm [50.13, 50.45] | 0.79 ms | +40 |

Intervals are 95% bootstrap over per-minute medians. The two **clean** windows,
32 minutes apart, agree: **+51.17 and +51.00 ppm, overlapping**. Under load the
deficit grew at **+48.25 ppm — nearly 3 ppm slower**, which is the opposite
direction from uncredited loss and cannot be explained by it. (The likeliest
cause is thermal: two cores at 100% warms the enclosure, and a crystal's rate
moves with temperature. It is not lost audio in either direction.)

**The shape is the evidence, not just the slope.** Per-minute medians of the
phase-corrected deficit in the clean window A:

| uptime (s) | corrected deficit (frames) | ms | step vs previous |
|---|---|---|---|
| 163 | 2,973 | 7.7 | — |
| 210 | 3,841 | 10.0 | +48.7 ppm |
| 270 | 5,111 | 13.3 | +55.0 ppm |
| 330 | 6,312 | 16.4 | +51.7 ppm |
| 391 | 7,486 | 19.5 | +50.5 ppm |
| 450 | 8,637 | 22.5 | +50.3 ppm |
| 510 | 9,784 | 25.5 | +50.2 ppm |
| 571 | 10,982 | 28.6 | +51.3 ppm |
| 630 | 12,117 | 31.6 | +49.7 ppm |
| 690 | 13,449 | 35.0 | +58.3 ppm |
| 750 | 14,486 | 37.7 | +44.6 ppm |

A straight line to within **0.30 ms** over eleven medians. Real loss arrives as a
step that stays up; there is no step anywhere in this run, clean or loaded,
larger than 0.5 ms.

Independently, `rate_offset_ppm` is a cumulative average from its own anchor and
so converges as `−D + B/t`. Fitted over the run's per-minute medians:

```
rate_offset_ppm(t) → asymptote −50.43 ppm  [−50.55, −50.20]
                     anchor bias 131 frames = 0.34 ms
```

The deficit's growth and the crystal's own rate therefore agree to within about
**1 ppm — 3.6 ms per hour, 0.09 s per day — and of ambiguous sign.** There is no
leak at any rate this run could have detected.

Counters at the end: `estimated_missing_frames` **0**, `gaps_with_loss` **0**,
`gaps_without_loss` **0**, `overruns` **0**, `late_reads` **40**,
`late_read_max_frames` **114,362** of a 192,000-frame ring (**60%** — see
"worth watching" below).

## Log cross-check, with the timezone stated

`journalctl --since` takes **local time (BST = UTC+1)**; the log lines themselves
are **UTC**. The UTC window 10:29:05 → 10:42:00 is the BST window 11:29:05 →
11:42:00, and that is what must be typed:

```bash
sudo journalctl -u open-observatory \
  --since "2026-08-09 11:29:05" --until "2026-08-09 11:42:00" -o cat   # BST
```

- `capture.late_read`: **7 lines**, matching the `late_reads` counter for that
  window exactly. Stalls of **101.6, 112.6, 117.6, 122.3, 123.0, 147.6,
  168.4 ms** against a 500 ms ring, every one logged
  `note=absorbed by the ring; no audio lost`.
- `loss_confirmed`, `lost_audio=True`, `capture.overrun`, `capture.gap`:
  **none**.
- The 10:32:38.3Z late read is the same event as the −200 ms excursion at uptime
  214 s in the sampled data, which recovered in full — the correction artefact
  described above, seen from both sides.

**The trap, demonstrated rather than described:** pasting the UTC digits into the
local-time flag (`--since "2026-08-09 10:29:05"`) returns **208 lines from a
different hour**, spanning two service restarts. It does not error and it does
not return nothing — it returns a confident, wrong answer.

## Worth watching, found incidentally

`late_read_max_frames` reached **114,362 of 192,000** — 60% of the ring, a 298 ms
stall — during this run, against 57,952 (30%) recorded in the previous round. The
ring still absorbed everything and no audio was lost, but the headroom counter
[[ADR-039 - Confirmed loss, not deficit|ADR-039]] added is doing its job and the trend is the wrong way.
`OO_CAPTURE_BUFFER_MS` is the lever if it keeps climbing. Note that part of this
run was under deliberate two-core load, so it is not a clean baseline.

## What this does *not* prove, stated plainly

- **The longest clean window is 22.2 minutes, not hours.** A loss mechanism with
  a period longer than that — an hourly sweep, a nightly rotation, a thermal
  cycle — would not appear. The linearity is strong evidence against a
  *continuous* leak, which was the specific worry, and is not evidence about rare
  events. **A restart-free multi-hour run is still worth taking**, and is now
  cheap: the method above needs 15 minutes and about 150 lines, and both are
  written down here.
- **The load segment shows the crystal rate is not a constant of nature.** It
  moved about 3 ppm under a thermal change inside one hour. Any figure derived
  from it — including the drift term the UI now displays — is a live measurement,
  not a calibration, and the UI derives it live rather than hardcoding it.
- **The two numbers were never independent.** `rate_offset_ppm` is
  `-(deficit − missing_frames)/expected × 1e6` computed inside `AlsaSource`, so
  subtracting the drift term from the deficit returns `estimated_missing_frames`
  *algebraically*. The check above is still evidence — different anchors, and a
  slope against a cumulative average, cross-checked against a clean/loaded
  contrast and a second agent's independent figure — but **the station has one
  measurement of lost audio, not two that can corroborate each other.** The plan
  recorded in the previous section, to "drift-correct the deficit and display
  that", would have produced a rename of the estimator's own figure presented as
  a second opinion. That is why [[ADR-046 - Deficit is mostly drift|ADR-046]] separates the terms instead.
- **The unabsorbed-stall path is still unproven on target.** No overrun has
  occurred on this station since [[ADR-039 - Confirmed loss, not deficit|ADR-039]] shipped, so "estimate what a real loss
  cost" remains proven only against `RingedDevice` in
  `tests/test_alsa_source.py`. Unchanged from the previous round, and the load
  probe did not produce one either.

## What was changed as a result

Nothing on the station — no capture code, schema, setting or dependency, so this
work cannot have affected the measurement it reports. `web/` only ([[ADR-046 - Deficit is mostly drift|ADR-046]]):
`audio lost` now shows `estimated_missing_seconds`; the deficit is shown
separately as `behind clock` with its drift term named and its ±50 ms phase
uncertainty stated; `late_reads` is shown beside `overruns`. `describeDeficit` is
exported from `Pipeline.tsx` and unit-tested in `Pipeline.test.ts` against the
readings above, including the 0.104 s one that started this;
`CapturePanel.test.tsx` asserts the labels in the rendered DOM, which is the part
that was actually wrong.

**The better fix, deliberately not taken:** evaluate `expected_frames` at the
last block's start inside `Station.status_snapshot` instead of at snapshot time.
That removes the sawtooth at source and would stabilise `continuity_ratio` too,
and it is a few lines. It is left for whoever can deploy and soak it, because it
changes a measured quantity in the capture path and a deploy would have voided
two agents' measurements running concurrently. The UI states the uncertainty
rather than hiding it in the meantime.

---

# 2026-08-09, late: the unabsorbed-stall path finally fired on the target

Every round above ended with the same caveat — *"the 'estimate what a real loss
cost' path remains proven only against `RingedDevice`"*, because no ALSA overrun
had occurred on this station since [[ADR-039 - Confirmed loss, not deficit|ADR-039]] shipped. **One has now.** Twelve, in
fact, and they agree with the estimator.

Read from `/api/v1/health` on the live station, **read-only, 2026-08-09 21:46:05Z**,
against a stream started 17:44:01Z — a **4.03 h restart-free run**, the longest
recorded anywhere in this document:

| | |
|---|---|
| `frames` | 5,574,144,000 |
| `expected_frames` | 5,577,078,828 |
| raw deficit | 2,934,828 frames = **7.643 s** |
| `rate_offset_ppm` | **−51.49** |
| crystal drift over 14,524 s at that rate | **0.748 s** |
| deficit **minus** drift | **6.895 s** |
| `estimated_missing_seconds` | **6.815 s** |
| `overruns` | **12** |
| `gaps_with_loss` / `gaps_without_loss` | **12 / 0** |
| `continuity_ratio` | 0.999474 |

**The estimator and the drift-corrected deficit agree to 0.08 s over four hours.**
Set that against the behaviour this whole document was written to chase: the
pre-[[ADR-039 - Confirmed loss, not deficit|ADR-039]] estimator over-reported by **8–13×** *while nothing at all was being
lost*. It now reports roughly 6.9 s lost when roughly 6.9 s was lost, and every
one of those twelve gaps is backed by an EPIPE ALSA actually raised. [[ADR-039 - Confirmed loss, not deficit|ADR-039]]'s
`RingedDevice` result has a real-hardware counterpart.

**Four cautions, because one snapshot is not a study:**

- This is **one reading**, not a sampled run. A single raw deficit carries about
  ±50 ms of pure phase artefact (see "the raw deficit is mostly a sampling
  artefact" above); at 7.6 s that is negligible, which is the only reason the
  arithmetic above is worth doing at all.
- **Nothing here says *why* twelve stalls exceeded a 500 ms ring** when none had
  before. It was a bat-active August evening on a station that had been deployed
  to repeatedly that day. Load, not a new defect, is the obvious hypothesis and it
  is untested.
- `estimated_missing_seconds` is now believable enough that **`continuity_ratio`
  of 0.999474 is a real figure**, below the 0.9990–0.9997 known-good band's upper
  half. Watch it.
- **`late_read_max_frames` reached 155,243 of the 192,000-frame ring — 81%.**
  Against 57,952 (30%) two rounds ago and 114,362 (60%) one round ago, that is the
  third consecutive reading in the wrong direction, and this time the ring did
  overflow twelve times. `OO_CAPTURE_BUFFER_MS` is the lever, and [[ADR-030 - ALSA ring and capture thread|ADR-030]]'s next
  structural step — a free-running reader thread feeding an internal queue —
  deserves re-reading before the 72-hour soak rather than after it.

## What is still open after this round

- ~~**The 72-hour soak has still never run.**~~ Nothing *in this document* is a
  soak — the longest window recorded anywhere in it is the 4.03 h above, and the
  longest *clean* one is 22.2 minutes. **A soak has since run and passed:**
  2026-08-22 to 2026-08-25, 72.107 restart-free hours at 99.9948% continuity
  ([[SOAK_2026-08-22]]).
- ~~**The one-hour drift run at full duration is still outstanding.**~~ **Run
  2026-08-25.** Gate (a) passed; gate (b) did not, failing linearity with a
  residual shaped like a thermal excursion — which is a mechanism with a period
  longer than this document's 22.2-minute clean window, exactly the class it
  warned could not be seen from here
  ([[DRIFT_GATE_B_2026-08-25]]).
- ~~**Why the ring overflowed twelve times**, above. Unattributed.~~
  **Attributed 2026-08-10 — see the final section.** The headroom was being eaten
  by a filesystem walk on the event loop, and the overruns themselves land on the
  retention sweep's beat.
- Hypothesis 4 was never isolated; the missing gap row of 2026-08-08 10:55:24Z is
  still inference only.

---

# 2026-08-10: the headroom is being eaten by a filesystem walk, and it is dated

The previous section ended with `late_read_max_frames` at **155,243 of 192,000
(81%)** — the third consecutive reading in the wrong direction — twelve real ALSA
overruns, and the sentence *"Why the ring overflowed twelve times. Unattributed."*

**It is now attributed.** The 30 s beat that [[ADR-033 - Retention is paced|ADR-033]] noticed and could not name
("the lag events fell to ~1.6/min on an unrelated 30 s beat"), and that the round
before this one guessed at ("`clips.disk_usage()` caches its clip-tree walk for
exactly 30 s and is the obvious candidate, but that is inference — it measured
0.0 ms on the sample taken, which is what a cache hit looks like"), is
`ClipManager.disk_usage()` walking the clip archive **on the event loop**. The
guess was right. What nobody had was the size of it, or that it grows.

**The short answer: this is a real risk to the soak, not a benign counter, and it
has a date on it.** It is also not the thing that has actually lost audio. Those
are two different findings and they are separated below.

## The window, and its timezone

| | |
|---|---|
| Window (UTC) | **2026-08-10 11:37:53 → 13:40:00**, 2.02 h |
| Window (BST, what `journalctl --since` takes) | 2026-08-10 12:37:53 → 14:40:00 |
| Stream | `9766b45e-d2a6-4d84-8a17-4881d06f5fb9`, `NRestarts=0`, restart-free throughout |
| Third-party activity | a deploy ended 12:38:48 BST, 10 s before the window opens; after that only `sysstat-collect` every 10 min until it closes |
| This investigation's own activity | **none until 13:40:06Z**, after the window closed |

The window ends because somebody else restarted the service at 13:42:46Z. That is
the operator's station and the operator was using it; the window was already
complete.

## What the station said

Read from `/api/v1/health` at 13:40:14Z, and confirmed by the `capture.closed`
line at 13:42:46Z, which reports the same totals:

| | |
|---|---|
| `frames` | 2,817,830,400 |
| `expected_frames` | 2,819,054,620 |
| raw deficit | 1,224,220 frames = **3.188 s** |
| `rate_offset_ppm` | **−53.28** |
| crystal drift over 7,341 s at that rate | **0.391 s** |
| deficit **minus** drift | **2.797 s** |
| `estimated_missing_seconds` | **2.698** |
| `overruns` / `gaps_with_loss` / `gaps_without_loss` | **5 / 10 / 0** |
| `late_reads` | **262** (2.14/min) |
| `late_read_max_frames` | **142,137** of 192,000 (**74%**, 370 ms) |
| `loop_lag_max_s` / `loop_lag_events` | **0.4625** / 275 |
| `continuity_ratio` | 0.999566 |

[[ADR-039 - Confirmed loss, not deficit|ADR-039]]'s estimator agrees with the drift-corrected deficit to **0.099 s over two
hours**, a second hardware corroboration on top of the 0.08 s one in the previous
section. The estimator is not in question here and was not re-derived.

Note `gaps_with_loss` 10 against `overruns` 5: each overrun is followed ~0.8 s
later by a second `capture.gap` with `reason=frame_deficit` — ALSA reports the
overflow, then the estimator confirms a further step the ring never gave back.
The pairs cost **0.113–0.139 s** and **0.400–0.430 s** respectively, so the true
price of one of these events is about **0.54 s**, not 0.13 s.

## Finding 1: every late read is a filesystem walk, and it is 74% of the ring

**262 of 262.** They arrive on a **30.4 s** beat — 223 of the 261 inter-arrival
intervals within 2.5 s of 30 s — with stalls of **254–370 ms** (median 300).

No inference is required, because `snapshot_phase_s` ([[ADR-033 - Retention is paced|ADR-033]]) names the phase in
the same millisecond. Three consecutive log lines, UTC:

```
13:38:07.428665Z housekeeping.tick  blocking_total_s=0.4532 leases_s=0.0002
                                    snapshot_s=0.4529 snapshot_phases={'storage': 0.4512}
13:38:07.433749Z loop.lag           lag_s=0.4275
13:38:07.782400Z capture.late_read  stall_ms=361.6 stall_frames=138862
                                    note='absorbed by the ring; no audio lost'
```

`storage` is `ClipManager.disk_usage()`. It is called from
`Station.status_snapshot()`, which runs **on the event loop** — for every
housekeeping tick, every live viewer, every API caller and every metrics scrape —
and it walked the whole clip tree with `Path.rglob("*.wav")` + `path.stat()`
whenever its 30 s cache had expired. Every `housekeeping.tick` in the window that
reports a `storage` phase reports **0.4462–0.4727 s**.

Reproduced by hand on the station, same tree, same interpreter, best of three:

| walk | files | time | per file |
|---|---|---|---|
| `Path.rglob` + `path.stat()` (the shipped code) | 40,888 | **435 ms** | 10.6 µs |
| `os.scandir` + `entry.stat()` | 40,888 | 189 ms | 4.6 µs |
| `os.scandir`, names only, no sizes | 40,888 | 48 ms | 1.2 µs |
| `find . -name '*.wav' \| wc -l` | 40,888 | 77 ms | — |

`find` at 77 ms against Python's 435 ms is the useful comparison: **this is not
the USB SSD and it is not the filesystem, it is interpreter overhead** — which
means it holds the GIL, which is why it lands on capture.

### Why it is climbing, which is the question that was actually asked

The cost is linear in the number of files, and **nothing has been deleted yet**:
the oldest clip is 2026-08-05, the native tier is 7 days, and
`/api/v1/retention/status` reports `eligible_for_deletion: 0 clips`. Counting the
files whose mtime precedes each of this document's previous headroom readings:

| Reading (UTC) | files then | predicted walk at 10.6 µs/file | `late_read_max_frames` recorded |
|---|---|---|---|
| 2026-08-08 15:00 | 9,764 | 104 ms | 57,952 (30%) |
| 2026-08-09 11:14 | 20,468 | 218 ms | 114,362 (60%) |
| 2026-08-09 21:46 | 27,174 | 289 ms | 155,243 (81%) |
| 2026-08-10 13:40 | 40,888 | 435 ms | 142,137 (74%) |

That fourth column is the trend that prompted this investigation, and the third
column is why. The correspondence is close for the first two rows and loose for
the last two — `late_read_max_frames` is a *maximum over a whole run*, so it also
carries whatever else was happening, and the 155,243 reading comes from a run
that took twelve overruns, after each of which the ring restarts. The claim here
is the mechanism and its growth rate, not a fit.

**The dated part.** Clips accrue at roughly **14,000 files/day** (per-day file
counts on the SSD: 08-05 3,002; 08-06 4,988; 08-07 324 — the 29.6 h outage day;
08-08 6,462; 08-09 14,116; 08-10 11,996 by 14:40 BST).

- The walk reaches **500 ms — the whole ring — at about 47,000 files**, which at
  the current rate is **roughly half a day away**.
- At the end of a 72-hour soak started now: ~83,000 files, **~0.9 s**, nearly
  twice the ring, every 30 seconds.
- At the steady state the retention tiers imply — 7 days of native clips plus 30
  days of playback derivatives — ~250,000 files and **~2.7 s**, every 30 seconds.

A 72-hour soak begun today would therefore not be measuring the capture path. It
would be measuring this.

## Finding 2: the audio actually lost is on the retention beat, and it is not the walk

All 2.698 s came from five events, at **11:52:57.0, 12:08:01.9, 12:33:12.9,
13:13:39.7 and 13:38:59.0 UTC**. The intervals are 904.9, 1511.0, 2426.8 and
1519.3 s — **every one a multiple of ~302 s** (3, 5, 8, 5), and 302 s is
`retention_interval_s` 300 rounded to the ~10.07 s housekeeping tick. The last of
them begins **0.5 s after** `retention_last_sweep_at = 13:38:57.936Z`, and
`RetentionSweeper.sweep()` stamps that field with a clock read taken at its
*start*, so the sweep was running through the stall.

These do not look like the 30 s beat. A storage walk produces one `loop.lag` of
0.35–0.44 and a late read that is absorbed. Each overrun is preceded by **two**
lag reports about 0.45 s apart — a small one, then a large one:

```
13:38:58.585776Z loop.lag  lag_s=0.1009
13:38:59.043281Z loop.lag  lag_s=0.3472
13:38:59.746711Z capture.loss_confirmed  alsa_overrun=True  frames=50987  seconds=0.1328
13:39:00.546697Z capture.loss_confirmed  alsa_overrun=False frames=164984 seconds=0.4296
```

— a contiguous block of roughly **0.55 s**, which is what it takes to exceed a
500 ms ring. None of the five coincides with a storage walk; the nearest walk to
the 13:38:59 event ran 21 s earlier.

This is [[ADR-033 - Retention is paced|ADR-033]]'s mechanism exactly, un-fixed and grown: the sweep is SQLAlchemy
ORM work in Python holding the GIL, in the evidence thread, and the loop still has
to issue and consume each capture read. [[ADR-033 - Retention is paced|ADR-033]] measured it at ~0.30 s costing
55–150 ms of lag. It is now large enough to empty the ring. The likely reason it
has grown is in the sweep's own prologue — `_exemplar_detection_ids(session)` and
`held_detection_ids(session)` run unconditionally, before any budget or deadline
check, over a `detection` table now at **107,808 rows**.

**This is correlation with n=5, not causation.** It has not been isolated and it
is not fixed here. The single-variable experiment that would settle it is the one
[[ADR-033 - Retention is paced|ADR-033]] already used, and it costs no code: set `OO_RETENTION_ENABLED=false` in
`config/runtime.env`, restart, and see whether the ~302 s overruns stop.

## Ruled out, by measurement

- **Evidence clip I/O to the USB SSD.** 396 `clip.written` lines in the window and
  not one is near a loss. The nearest clip write to any of the five events is
  **6 min 17 s** away (11:46:40 vs 11:52:57); the rest are 1.5–8 min away. The
  stalls are also the wrong shape for I/O — `find` walks the same filesystem in
  77 ms while Python takes 435 ms.
- **The USB power budget.** `vcgencmd get_throttled` = `0x0` throughout, so no
  undervoltage has ever been recorded; and both beats are periodic in *software*
  time (30.4 s and 302 s) rather than correlated with device traffic. Both are
  fully explained by named, timed, in-process work.
- **Thermal or frequency scaling.** ARM clock pegged at 1,600,013,696 Hz; 60.4 °C,
  warm against the 39 °C in the brief but nowhere near the ~80 °C throttle point,
  and `get_throttled` says so.
- **The refinement runner ([[ADR-045 - Refinement runner|ADR-045]]).** `open-observatory-refine.timer` last ran at
  **02:01 BST** and next runs at 02:03. It is not running in any window here.
- **The near-miss ledger ([[ADR-052 - Near-miss ledger|ADR-052]]), withdrawn-detection filtering, the pause gate
  and the spectrogram encoder.** None appears in either beat, and both beats are
  accounted for to the millisecond by work that names itself.

## What was changed, and what was not

**[[ADR-059 - Clip archive measured off-loop|ADR-059]]** fixes finding 1 only. `disk_usage()` now reports instead of measuring;
the walk becomes `refresh_disk_usage()`, an `async` method that yields to the loop
every 512 files and is driven from housekeeping on the **same 30 s cadence**, so
exactly one thing moved. It also switches to `os.scandir` (2.3× cheaper).
Chunking rather than a thread is deliberate, and is [[ADR-033 - Retention is paced|ADR-033]]'s own lesson: an
executor partitions queueing, not scheduling, and nothing partitions the GIL.

**It is not deployed and not verified on target**, under the standing instruction
not to deploy or restart while the operator was using the station. Every figure in
this section is a read-only measurement of the *unfixed* station. [[ADR-059 - Clip archive measured off-loop|ADR-059]] carries
the verification block to run afterwards.

Finding 2 is left alone on purpose. It is [[ADR-033 - Retention is paced|ADR-033]]'s code, it has n=5, and the
operator asked to start the soak knowing rather than with a speculative change in
place.

## Traps this round produced

- **A polling probe that opens a fresh SSH connection per sample is itself the
  load.** A 13-minute run sampling `/api/v1/health` every 2 s over one-shot
  `ssh … curl` — a public-key handshake every 2 s on a 4-core Pi — took the
  station from 5 overruns in 2 h to **6 in 13 minutes**, with per-event loss
  rising from 0.13 s to **1.00–1.07 s** and `loop_lag_max_s` to **1.0989**. That
  window (13:45:35 → 13:58:33Z) is contaminated by this measurement *and* by two
  third-party restarts, and **nothing is attributed from it**. It is recorded
  because it demonstrates cleanly that the mechanism gets much worse under load,
  and because the previous round's method — sampling from the laptop over a
  *persistent* connection — is the one to copy. Reading the journal after the fact
  costs the station nothing.
- **`retention.last_sweep_at` is `null` on a fresh process and stays null until
  the first sweep, ~300 s in.** Reading it just after a restart and concluding
  retention is not running is available and wrong. Cross-check `NRestarts` and
  `ExecMainStartTimestamp` before believing any counter on this station today — it
  was restarted at 11:37:51Z, 13:42:46Z and 13:55:59Z.
- **`ClipManager.enforce_retention()` and `sweep_retention` are unreachable in the
  station.** Nothing calls them; `RetentionSweeper` ([[ADR-026 - Tiered clip retention|ADR-026]]) superseded them and
  only `tests/test_pipeline.py` exercises them. They look like a live second
  retention path and are not.
- **`late_read_max_frames` is a maximum over a run, not a level.** It resets on
  every stream open, and an overrun restarts the ring, so a run *with* overruns
  can read lower than a clean run on a worse station. Compare the beat and the
  stall distribution, not the maximum.
- The timezone trap holds and was obeyed: every window above states UTC or BST
  explicitly.

## What is still open after this round

- ~~**Finding 2 is unfixed and unisolated.** The `OO_RETENTION_ENABLED=false`
  experiment above is cheap and has not been run.~~ **CLOSED 2026-08-14 by
  [[ADR-061 - Operator keep flag|ADR-061]], and the experiment was never needed.** The cause was identified
  directly rather than by ablation: `RetentionSweeper.sweep()` ran an unbounded
  2.978 s query *before* any deadline check, and the coupling to capture was
  proven to the millisecond from the station's own log — a sweep beginning
  02:18:19.893 with `duration_s=2.6608`, and the first `-EIO` at 02:18:22.552.
  Verified on the station after the fix: **zero `capture_gap` rows in the 30
  minutes following deployment**, against 22-24 per hour beforehand, while
  retention deleted 800 files / 3.5 GB with a sweep duration of 0.696 s inside
  its 1.5 s budget and a preamble of 0.0027 s.
- ~~**[[ADR-059 - Clip archive measured off-loop|ADR-059]] is unverified on target.** No post-fix reading exists.~~ **A
  post-fix reading exists and it FAILED.** [[ADR-059 - Clip archive measured off-loop|ADR-059]] predicted
  `late_read_max_frames` "well under 100,000"; the reading at 54.7 h into the
  soak was **188,982 of 192,000 (98.4%)** — worse than the 81% it was written
  to fix. Recorded on [[ADR-059 - Clip archive measured off-loop|ADR-059]] itself. Its diagnosis (a filesystem walk on the
  event loop) was correct but incomplete: the walk's *successor* cost, the
  retention sweep, was the larger term and is what [[ADR-061 - Operator keep flag|ADR-061]] removed.
- **The 72-hour soak was run anyway, 2026-08-10 to 2026-08-13, before finding 1
  was deployed and confirmed — and it failed**, at 99.865% continuity against
  a ≥ 99.9% criterion. This document's own prediction held: the walk starved
  the capture loop and forced device restarts through most of the window. See
  [[MILESTONE_STATUS]] §Milestone 4.5 and [[ADR-061 - Operator keep flag|ADR-061]], which removes the walk's
  successor cost (the retention sweep's unbounded exemplar query).
- **`OO_CAPTURE_BUFFER_MS` was not touched.** Widening the ring was the lever the
  previous round nominated, and it would buy time — but the walk grows without
  bound and the ring does not, so it treats the symptom for a few days at most.
- Hypothesis 4 was never isolated; the missing gap row of 2026-08-08 10:55:24Z is
  still inference only.
