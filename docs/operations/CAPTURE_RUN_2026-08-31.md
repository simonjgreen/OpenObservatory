# Capture run 2026-08-31 to 2026-09-07 — 158.2 h unbroken, banked before a deploy

The longest unbroken capture stream in this station's life: **158.204 hours**
(6.59 days), more than double the 72.107 h that passed the acceptance soak on
2026-08-25 ([[SOAK_2026-08-22]]).

**Nobody staged this run either**, and it was never a scored soak. It began when
`open-observatory.service` restarted on 2026-08-31 at 22:28 BST and simply kept
going. It is written down here because it was about to be ended deliberately —
the deploy carrying the [[ADR-038 - Display push channel|ADR-038]] display-feed
fix (issue #30) restarts capture — and the counters behind it are process-scoped,
so a restart erases them. Banking them costs a minute; losing them costs the
longest clean window this station has produced.

All figures read at `2026-09-07T11:40:18.995108Z`, 158.204 h in.

## Validity — checked first, before any figure was recorded

| check | required | measured |
|---|---|---|
| `started_utc` | unchanged | **2026-08-31T21:28:03.777446Z** |
| `stream_id` | unchanged | `29ba4de4-a9aa-4d97-a01a-effc31a2c4d8` |
| `stream_restarts` | 0 | **0** |
| `clock_reanchors` | 0 | **0** |
| `open_failures` | 0 | **0** |
| `unclean_restart` | false | **false** |
| `NRestarts` (systemd) | 0 | **0** |
| elapsed | — | **158.204 h** |

Host uptime at the same moment was **16 days 5 h**, dating to the 2026-08-22
mains-cut reboot. The two numbers differ because the *service* was restarted on
31 August while the machine was not, and confusing them is how a run gets
credited with a week it did not have.

## Against the five SLOs

[[ADR-073 - Five capture SLOs]]. Drift is excluded from every loss SLO, as that
ADR requires: the audio it describes exists and is fine.

| | SLO | target | measured | |
|---|---|---|---|---|
| **B** | capture integrity | ≥ 99.99% (≤ 8.6 s/day) | **99.99962%**, 0.326 s/day | **pass**, 26× headroom |
| **C** | timestamp accuracy | ≤ 60 s absolute | **29.04 s** and rising 4.41 s/day | **pass**, but see below |
| **A/A2** | coverage | ≥ 99.5% / ≥ 99.9% | not scored — this is one stream, not a month | — |
| **D** | detection coverage | ≥ 99% | not scored — the counters needed (`windows_in`, `windows_dropped`, `coverage_ratio`) all read `null` on `/api/v1/station` | — |
| **E** | evidence sufficiency | ≥ 95% | not scored | — |

Raw capture counters, for the record:

| | |
|---|---|
| `continuity_ratio` | 0.999945 |
| `capture_integrity_ratio` | 0.999996229 |
| `audio_lost_seconds` | **2.1466** |
| `drift_seconds` | 29.038 |
| `discontinuities` / `gaps_with_loss` / `gaps_without_loss` | 4 / 4 / 0 |
| `overruns` / `late_reads` / `late_read_max_frames` | 4 / 26 / 138,131 of 192,000 |
| `hot_path_cpu_ratio` | 0.0232 |
| `observed_rate_hz` / `rate_offset_ppm` | 383,980.437 / −50.94 |
| `blocks` / `frames` | 5,691,719 / 218,562,009,600 |

**SLO C is the reason this run could not have continued indefinitely.** At
−50.9 ppm the stream accrues 4.41 s of timestamp error per day, so the 60 s bound
falls at about **day 13.6** — 2026-09-14. ADR-073 says as much in the abstract
("at 50 ppm this bounds a stream to ~14 days"); this is the first run to get far
enough to make it a real date. The deploy resets it to zero, so ending the run
here is not merely harmless to SLO C, it is the thing SLO C was going to require
within a week anyway.

## Every gap in the window

Four, all `overrun`, all with loss. They reconcile **exactly** against the
counter — 202,118 + 198,876 + 220,035 + 203,252 = **824,281 frames**, which is
`estimated_missing_frames` to the frame, and 2.1466 s at 384 kHz.

| when (UTC) | frames | duration | at frame | in the database? |
|---|---|---|---|---|
| 2026-09-04 01:37:23.829 | 202,118 | 0.5263 s | 105,271,526,400 | **no** |
| 2026-09-06 22:10:07.698 | 198,876 | 0.5179 s | 200,023,833,600 | yes |
| 2026-09-07 01:08:09.526 | 220,035 | 0.5730 s | 204,125,222,400 | **no** |
| 2026-09-07 01:31:26.823 | 203,252 | 0.5293 s | 204,661,555,200 | **no** |

Notably uniform: four overruns of about half a second each, where the 72-hour
soak's two gaps were 0.04 s and 0.56 s. Three of the four are in the small hours
(01:08, 01:31, 01:37) and one at 22:10, which is peak bat activity.

## What this run found that the counters alone would not have

### Three of the four gap records were never written

`GET /api/v1/gaps` returns **one** row for this stream. The other three failed to
insert with `sqlite3.OperationalError: database is locked`, logged as
`capture.gap_row_failed` and then dropped — `_insert_gap_row` has no retry
(`src/open_observatory/station.py:1175`).

The in-memory counter is therefore the *only* honest account of this run's lost
audio, and it dies with the process. The durable record — the one a person would
consult next month — is missing 76% of it. The table above was reconstructed from
the journal, which is why it is there.

### 2,188 detections were lost to the same lock

`persistence` reads `written: 402006, queued: 0, dropped: 0, failures: 2188`.
Those 2,188 are detections that reached `_persist_loop`, hit `database is locked`,
and were discarded — one attempt, no retry, gone
(`src/open_observatory/station.py:1476`). That is **0.54% of all detections in the
window**, permanently absent from the database.

Worth stating plainly because of *which* counter stayed clean: `dropped: 0`. The
queue never overflowed, so every back-pressure indicator this system has reads
healthy. The loss is entirely inside `failures`, a counter nothing alerts on.

### The lock contention is also why the disk is over its watermark

6,585 "database is locked" occurrences in the window, alongside 1,099
`retention.statement_interrupted` and 586
`housekeeping.retention_never_reached_a_tier`. `retention.last_sweep_complete` is
**false**, and disk stands at **85.55%** against an 85% watermark with
581,232,352 bytes of it operator-kept recordings the sweep will not delete.

That closes a circle worth drawing explicitly. The sweep cannot finish, so the
disk crosses the watermark, so `/api/v1/health` reports a problem, so
`display_channel.health_state` returns `D` — and until the fix that shipped with
this deploy, a `D` letter stopped the counter-top display's feed entirely
(issue #30). The frozen display was six hours downstream of SQLite lock
contention.

[[ADR-007 - SQLite in developer mode]] anticipated that SQLite would not be the
production answer. This run is the first measurement of what it costs while it
still is: 0.54% of detections, 76% of the gap record, and the retention sweep.

## Not measured

- **Coverage (SLO A/A2)** and **evidence sufficiency (SLO E)** — a single stream
  cannot answer a monthly question.
- **Detection coverage (SLO D)** — `/api/v1/station` reports `windows_analysed`
  per detector but leaves `windows_in`, `windows_dropped` and `coverage_ratio`
  `null`, so the ratio ADR-073 asks for cannot be computed from the surface that
  is supposed to carry it. All three detectors reported `state: ok` with lag
  around 31.2 s.
- **Whether any of the four overruns cost a detection.** The gaps are half a
  second each; nothing correlates them against detector windows.

---
Part of [[docs/operations]].
