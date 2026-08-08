# Capture gaps and overruns: what was measured, what was fixed, what is still open

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

Both are fixed (ADR-030). The ring is now sized from `capture_buffer_ms` (500 ms
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

Zero gaps and zero overruns is the figure `HANDOVER.md` records for normal running and
which the station had stopped achieving. Continuity of 0.999945 is *above* the
known-good band of 0.9990–0.9997. Hot-path CPU is unchanged, which is the expected
result: a deeper kernel ring does no extra work, it only tolerates more delay.

The device offset moving from −245 ppm to −52 ppm is a second, independent confirmation
that the estimator was wrong rather than the hardware: the true measured offset recorded
in `TARGET_DIAGNOSTICS.md` is −43 ppm, and uncredited lost frames were what dragged the
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
`docs/operations/TARGET_DIAGNOSTICS.md`:

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
implies. The cause is the one already documented in `HANDOVER.md` §7: the AudioMoth's
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
  feeding an internal queue — is described and deliberately deferred in ADR-030.
- **The missing gap row of 2026-08-08 10:55:24Z**, above. Inference only.
- **Hypothesis 4 was never isolated.** The restored rendering settings were left in
  place on purpose so the code was the only variable. They may still cost something at
  the margin; the CPU-load experiment says load matters. Nobody has measured them alone.
- **No 72-hour soak has run.** These are 45-minute windows.

## Smoke test on the target

```bash
ssh observer@station.example
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
rsync -a --delete --exclude __pycache__ ./src/ observer@station.example:open-observatory/src/
ssh observer@station.example sudo systemctl restart open-observatory
```

The ring depth alone can be rolled back without reverting anything, by setting
`OO_CAPTURE_BUFFER_MS=80` in `config/runtime.env` and restarting. Note that
`_periods_for_buffer` floors the ring at two capture blocks, so 80 will still yield
200 ms; reproducing the original 80 ms ring needs the code change reverted.
