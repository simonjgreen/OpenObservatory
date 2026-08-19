# Handover: state, decisions, and what to do next

Written at the end of the first implementation session, 2026-08-04, against the
live development station; revised 2026-08-08 to cover Milestone 5's
completion, an AudioMoth outage and its fix, live playback being rebuilt onto a
different transport, and evidence storage moving to a USB SSD; **claims
re-verified against the code and the live station on 2026-08-09, after that day's
87 commits (ADR-041 through ADR-053) merged to `main`.**

Read `MILESTONE_STATUS.md` for progress against the plan; this file is the
operational and engineering context a successor needs. Read the ADR index in
`docs/architecture/ADRS.md` for the full reasoning behind any decision — this
file cross-references ADRs rather than restating them.

**Where facts live** (these documents have drifted apart from each other before):
measured figures are authoritative in `docs/operations/TARGET_DIAGNOSTICS.md`;
what is done versus outstanding is authoritative in `MILESTONE_STATUS.md`; the
map of everything is `docs/README.md`; setting up a dev environment is
`docs/development/SETUP.md`.

---

## 1. Where things stand in one paragraph

The station captures live 384 kHz mono audio from an AudioMoth on a Pi 5, derives a
48 kHz audible stream with verified zero group delay, cuts immutable time-addressed
windows to detectors' own specifications, normalises and persists detections, writes
checksummed evidence clips (including audible renderings of ultrasound) to a USB SSD,
and serves a real-time debug UI over two WebSocket channels plus a plain-HTTP audio
stream — in scrolling or waterfall orientation, with unidentified and non-live-source
events hidden by default — plus a history mode for browsing what was persisted. The
same API now also feeds an **ESP32 counter-top display** over a push channel it can
also update itself from, over the air (ADR-023, ADR-038, ADR-050), and an **MQTT
publisher** running live against the operator's Home Assistant broker (ADR-025). An
**authentication foundation** exists, off by default (ADR-034); **every setting is
editable from the browser** in three declared tiers (ADR-048); and a **refinement
runner** examines stored bat evidence nightly in its own CPU-fenced process, at
propose-only authority (ADR-045).

Measured 2026-08-09 on the development laptop against merged `main`: **817 Python
tests pass, 8 skip, 12 deselected** (`pytest -q --deselect
tests/test_api.py::TestLiveChannels`), or **826 pass, 11 skip** with a bare
`pytest -q`; the skips are the unbundled-model fixture tests plus three
`TestLiveChannels` cases carrying `@pytest.mark.skip`. **235 frontend tests pass**
in 22 files. `ruff check .` is clean; `mypy src` reports **22** pre-existing
errors in 11 files and has never been clean. The last recorded full run *on the
target device* was 197 Python tests on 2026-08-08 — that number is a snapshot of
that run, not the current suite, and neither is any number in this paragraph. It
runs as a systemd unit and survives reboots.

Milestone 5 (ultrasonic and bat support) is complete: a bat pass detector runs
live, gated to night by a solar scheduler, and BatDetect2 was benchmarked on the
target and deliberately *not* adopted as a live detector — see §1a. It is
**not complete** as a whole system, and `CLAUDE.md` forbids that word until the
acceptance criteria pass a continuous 72-hour soak on the Pi. **That soak ran
2026-08-10 to 2026-08-13 and failed** — 99.865% continuity against a ≥ 99.9%
criterion, 349.3 s lost out of 259,200 s; see `MILESTONE_STATUS.md` §Milestone
4.5 and §1e below for the wedge that followed it. Also outstanding: the one-hour drift run at full duration, what
remains of the capture-gap investigation, the three plausibility/taxonomy/privacy
repair commands never applied to the live database, Milestone 6's alert engine,
Milestone 7 entirely, and most of Milestone 8.

(The review workflow is no longer on that list — ADR-043 closed it on 2026-08-09,
and the live station's `review` table holds 65 rows.)

## 1a. The cascade finding — the most reusable idea from Milestone 5

BatDetect2 cannot follow a live 384 kHz stream (0.52x realtime, ADR-017), but it does
not have to. `ultrasonic-pass-v1` runs at 36-40x realtime and decides *when*
something happened; the expensive classifier only ever sees the few seconds already
flagged as a pass. Measured on this station's own clips, trimmed to 1.5 s centred on
the pass, BatDetect2 costs 2.1 s of inference per pass — so 1015 passes on the night
of 2026-08-05 is about 36 minutes of classifier work for a whole night, not hours.
Trimming is where the saving lives: an untrimmed 6 s evidence clip is mostly pre-roll
silence and costs four times as much. `scripts/classify_clips_batdetect2.py`
implements this cascade as a standalone offline tool — it reads stored clips and
prints a comparison, and does not write to the database.

**Updated 2026-08-09 (ADR-045): the cascade now ships as a scheduled job, and the
decision about it was the opposite of what this section anticipated.** It is *not*
a registered detector plugin and *not* carried by `DeferredDetectorWorker`. That
worker is an in-process queue of live `AudioWindow`s whose central safety property
is dropping anything older than `max_delivery_latency_s`; a clip written six hours
ago is exactly what it would, correctly, reject. Instead the cascade runs as a
**separate process** — `oo refine run`, `src/open_observatory/refinement/` — on a
systemd timer at 01:00 UTC, fenced to `AllowedCPUs=2-3` / `Nice=19` /
`MemoryMax=1G`, so it shares neither a GIL nor a core with capture. And it may only
**propose** a species for human review; it never edits a record, because BatDetect2
returned 0.77 for *Pipistrellus pygmaeus* on a call this station measured at 34 kHz
(item 6 below). `DeferredDetectorWorker` remains unused and remains the right
mechanism for a *live* detector too slow to run inline.

## 1b. The 2026-08-08/09 session — what changed and what it cost

A long multi-agent session. The transferable parts:

**Delivered on 2026-08-08:** an ESP32 counter-top display (ADR-023, ADR-038); MQTT +
Home Assistant, live on the operator's broker (ADR-025); tiered retention (ADR-026);
live ultrasonic retuning restored (ADR-022); coverage bounded by delivered frames
(ADR-024); the capture-gap root cause and fix (ADR-033); BirdNET plausibility
filtering (ADR-032); an Alembic environment (ADR-035); an authentication
foundation, off by default (ADR-034); the deficit estimator corrected (ADR-039);
a committed species fixture passing on target; a documentation audit; and
`docs/CHARTER.md`.

**Delivered on 2026-08-09**, a second and larger fan-out of 87 commits — read the
ADRs, not this list, which exists only so nothing is invisible:

| ADR | What landed |
|---|---|
| 041 | The ultrasonic spectrogram gets its own measured floor and ceiling |
| 042 | `alembic upgrade head` runs in `deploy.sh`; `create_all()` and the ALTER TABLE patcher retired from production |
| 043 | Taxon correction closes the review workflow; a human's ear outranks the machine |
| 044 | A withdrawn detection is marked in the record and suppressed on claim surfaces; the BirdNET week index audited and confirmed correct |
| 045 | The refinement runner: a separate CPU-fenced process, propose-only, on a timer |
| 046 | The frame deficit is 98% crystal drift — stop showing it as lost audio |
| 047 | Site parameters are runtime state; the repository ships no site |
| 048 | Every setting web-configurable, in three declared tiers |
| 049 | BirdNET's sound categories are not species; no clip is kept for human speech |
| 050 | Counter-top display OTA — **flashed and verified on hardware**, rollback drill included |
| 051 | The spectrogram marks where the sound being played back is, as an interval |
| 052 | A near-miss ledger: what BirdNET proposed and refused, with per-band histograms |
| 053 | Taxonomic grouping above species — **proposed only**, nothing implemented |

Milestones 8 (distribution) and 9 (nice-to-have) were added to the plan the same day.

**Four bugs that had been silently lying, all found by checking an instrument
against the thing it claimed to measure:**

| Instrument | Claimed | Truth |
|---|---|---|
| `estimated_missing_seconds` | 52.4 s lost | 4.06 s (12.9x, ADR-039) |
| `rate_offset_ppm` | +878 to +3,600 | approx -43 (same defect) |
| capture coverage | up to 1302% | arithmetically impossible (ADR-024) |
| MQTT `suppressed_unidentified_total` | 0 | filter never fired at all |

The last one is the sharpest: the filter shipped broken, its unit test was
green, and it was caught only by comparing the production counter against the
database. The test asserted an invented value (`taxonomic_group=None`) instead
of the sentinel the detector really emits (`"acoustic_event"`).

**Two regressions this session caused and fixed**, both worth knowing:

- Merging retention's 10 s sweep cadence reintroduced capture gaps at ~1.9/min.
  An executor partitions queueing, not scheduling, and nothing partitions the
  GIL (ADR-033).
- Two agents fixed "stream rows never record frames" independently and
  incompatibly; one wrote process-lifetime counters into per-stream rows, which
  is the same arithmetic that produced 1302% coverage. Resolved at merge.

**Traps recorded elsewhere but worth repeating here:** the station logs UTC
while `journalctl --since` takes local time (this produced an exactly opposite
conclusion once); React Testing Library was silently not cleaning up between
tests, so component tests could pass or fail on another test's DOM; and the
same `detector_id` does not imply the same operational config over time.

## 1c. The 2026-08-09/10 session — what changed, and the traps it cost

Another long multi-agent session, ending with the station deployed and a
72-hour soak scheduled for Tuesday 11th to Thursday 13th August. ADRs 041-059
all come from it. The transferable parts, not the changelog:

### Six defects that were invisible until something forced them into the open

| Defect | How it surfaced | Why it had survived |
|---|---|---|
| **Every settings write failed in production** | Trying to change a detector threshold on the station: HTTP 500, `EROFS` | `ProtectHome=read-only` in the unit; tests pass locally, where no sandbox applies |
| **The refinement runner had never run** | Reading the journal after noticing `last_run: None` | numba could not write its JIT cache under the same sandbox. `oo refine status` reported `last_run: None`, which is indistinguishable from "never fired" |
| **8,067 media rows claimed files that were gone** | Trying to fetch a clip for the operator to listen to | `ClipManager.enforce_retention()` unlinked oldest-first without touching the database. Nothing compared the two |
| **`disk_usage()` walked 40,888 files on the event loop every 30 s** | Investigating why ring headroom was trending the wrong way | Cost is linear in file count. It was invisible at 9,764 files (104 ms) and fatal at 47,000 (500 ms, the whole ring) |
| **`last-night` resolved into the future** | The operator looked at his phone at 12:28 and saw "0.0% captured" | The threshold was noon. Writing the property test then found `dawn-chorus` had a ternary whose branches were identical |
| **GO LIVE unreachable on a phone** | The operator tried to use it | `overflow-x: hidden` converted a layout bug into a page reporting `scrollWidth` 390 of 390 while the button could not be scrolled to |

The pattern in all six: **a mechanism nobody had ever watched actually run.**
Three were the systemd sandbox blocking a write path nobody had declared. The
station's own reporting hid two of them.

### Traps that cost time, and will again

- **A `--json` flag is not machine-readable until something parses it.** Every
  one in this CLI emitted ANSI escapes, because rich colourises even onto a
  pipe. Fixed, then reintroduced twice by agents on branches cut before the fix
  — hence `test_cli_json_output.py`, which asserts *no line in `cli.py` calls
  `console.print_json`* rather than enumerating commands.
- **`pkill -f "<pattern>"` matches the shell running it.** Killed two of my own
  commands mid-flight. Kill by listening port instead.
- **A headless browser measures a page that is not being drawn.** A canvas check
  reported 300x150 and zero pixels painted; the cause was `document.hidden` and
  `requestAnimationFrame` firing zero times. Screenshots force a paint; JS
  measurement does not.
- **Verifying a control while it is closed proves nothing about it open.** 30
  layout states were checked across six widths and called clean; the pause menu
  opened off the left of the screen because every capture had it shut.
- **Reading the wrong field name looks exactly like a bug.** Twice I nearly
  reported defects that did not exist: `ratio` instead of `fraction_captured`,
  and a `since` window that predated the thing it was meant to measure.
- **Running the refiner outside its unit defeats its CPU fence.** `oo refine run`
  over SSH is not `systemd-run` with `AllowedCPUs=2-3`; it competed with capture
  and dirtied the very measurement it was checking.

### What the soak will and will not settle

Deployed and left alone from 2026-08-10 evening. Two things are deliberately
running unresolved, because 72 hours of data beats the five samples we have:

1. **The retention sweep and capture.** After ADR-059 removed the 30 s beat, the
   remaining `capture.late_read` events fall ~304 s apart, which is
   `retention_interval_s`. Five overruns correlated with it, every interval a
   multiple. Not established. The experiment costs no code:
   `OO_RETENTION_ENABLED=false`, restart, see if the beat stops.
2. **Retention's first ever deletion**, due around 12 August — inside the soak.
   The oldest clip is 5 August and the native tier is 7 days, so nothing has ever
   aged out. If it works the archive settles near 231 GB of 458; if it does not,
   there are about 9 days of headroom, so the soak completes either way.

**`birdnet_threshold_in_range` is 0.35, not the shipped 0.55.** It was lowered as
a tuning experiment on 2026-08-09 and deliberately left. It roughly doubles
detections, and therefore clips: daily growth went from ~15 GB to ~33 GB. Judge
any soak figure about volume against that, not against the defaults.

## 1d. Mid-soak reading, 2026-08-12 21:10 UTC — 54.7 h into the 72

Taken read-only from the live station (`/api/v1/health`, `/metrics`, `journalctl`,
`oo refine status`). Nothing was deployed, restarted or written. **Every figure
here is a snapshot at 54.7 h, not a result**; the soak's own window runs to
**2026-08-13 14:27 UTC**, 72 h after the stream started at 2026-08-10 14:27:49 UTC
with `stream_restarts: 0`.

### The gate passes, with almost no margin

`continuity_ratio` was **0.999056** against an acceptance criterion of ≥ 99.9%
(`ACCEPTANCE_CRITERIA.md`). That is 0.006 pp of headroom: 175 s lost across the
run, 601 gaps with loss, 306 ALSA overruns. **One bad hour fails the gate**, so
the number to read at the end is this one, and a pass should be reported with its
margin attached rather than as a clean pass.

### `late_read_max_frames` went the wrong way

**188,982 of the 192,000-frame ALSA ring — 98.4%**, against the 81% that ADR-059
was written to fix. The reads are landing within about 8 ms of an overrun. This is
the sharpest live risk in the run and the first thing to look at afterwards. The
experiment named in §6.1 is still the right one: `OO_RETENTION_ENABLED=false`,
restart, see whether the ~304 s beat stops.

### Evidence writing does not keep up at activity peaks

Two distinct losses, neither of which touches capture, and both of which mean
"evidence clips obey maximum duration and retention" is not currently true in the
way the criteria assume:

| Symptom | Reading at 54.7 h | Mechanism |
|---|---|---|
| `evidence.dropped` | 2,743 detections published with **no clip at all**, every one `activity-v1` | `_evidence_queue` is `maxsize=32` and full; `station.py`'s bounded queue (Milestone 5 work, not ADR-045) dropping rather than blocking, working as designed |
| `clip.not_in_ring` / `clips_failed_total` | 1,909 | the audio had already been evicted from the native ring before the clip was cut |

Both cluster where the detections are: 540 drops in the 05:00 hour, 164 in the
20:00 hour. Sampled over 12 minutes at dusk the clip failure rate was **9 of 93
attempts, about 10%**. `ring_extraction_misses_total{ring="native"}` tracks
`clips_failed_total` exactly (1,909 = 1,909); `{ring="audible"}` is 0.

### Retention has still never deleted anything, and now it is overdue

The oldest clip is 2026-08-05 18:44 UTC, so the 7-day native tier **crossed its
threshold at about 18:44 UTC on the 12th** and every
`oo_retention_files_deleted_total{tier=…}` was still 0.0 two hours later. The
sweep also reports `complete=false` on every pass — `retention_batch_budget_s` is
**1.5 s** and the measured sweep takes **2.0 s**, so it hits its deadline every
time (276 `housekeeping.retention_not_keeping_up` warnings in 24 h).

**The open question is whether the deadline is being hit *before* the sweep
reaches a deletable candidate.** A `--dry-run` sweep answers it and mutates
nothing; it was deliberately not run mid-soak, because it competes for the same
CPU and the same already-contended SQLite connection as the thing being measured.
Run it first thing afterwards.

Volume is not the constraint either way: 200 GB of 458 used, growing at ~150 MB
per 12 min ≈ **18 GB/day** (below the ~33 GB/day §1c predicted from the lowered
`birdnet_threshold_in_range`), so ~14 days of headroom. The soak completes
whatever retention does.

### The refinement runner's first real pass completed

**2026-08-12 01:00 BST, exit 0**, 39 min wall and 56 min CPU inside its
`AllowedCPUs=2-3` fence, **2,399 proposals written**. That closes one of the four
watch items in §6.1 — the mechanism has now been observed running end to end, not
forced by hand. It does not close ADR-045's own warning: **15,704 bat detections
have never been examined by a refiner**, and retention deletes on age alone.

### Two things suspected, not established — do not report either as fact

1. **SQLite lock contention is losing detections.** 331 `sqlite3.OperationalError:
   database is locked` tracebacks in 24 h, and `detections_persist_failures_total`
   = 310. Bursty rather than continuous — flat across a 12-minute sample. Worth
   knowing that the counter and the traceback count agree to within 7%, which is
   the kind of agreement that usually means one mechanism.
2. **All three detectors report `lag_seconds` ≈ 185 s, identically**, and that is
   exactly `expected_frames - frames` expressed in seconds. Detections are being
   timestamped ~3 minutes behind wall clock. At −51.62 ppm the crystal accounts
   for only ~10 s of a 54.7 h deficit, so **ADR-046's "98% crystal drift" framing
   does not hold at this duration** and most of this deficit looks like real lost
   audio. This is the same class of defect as the four in §1b: an instrument
   disagreeing with the thing it claims to measure. Treat it as unexplained and
   measure it properly rather than patching the number.

### Healthy, for completeness

RSS oscillating 1.37–1.72 GB with no upward trend across the run (so no leak),
`hot_path_cpu_ratio` 0.036, SoC 61.7 °C, MQTT connected with 129,888 publishes and
0 failures, no pause taken (`detections_suppressed` 0, so ADR-055 is untested this
run), `rows_claiming_missing_files` 0 with a completed audit pass over 65,556 rows
— ADR-057 is holding.

### What to do at the end of the soak, in order

1. Read `continuity_ratio` and report it **with its margin**.
2. Run the retention `--dry-run` sweep and find out why nothing was deleted.
3. Take `late_read_max_frames` again; if still ≈98%, run the
   `OO_RETENTION_ENABLED=false` experiment.
4. Decide whether `_evidence_queue`'s 32 slots are the right bound, or whether
   `activity-v1` should stop requesting evidence it cannot be given.
5. Explain the 185 s detector lag before trusting any event timestamp to the
   second.

## 1e. The 2026-08-14 capture wedge — one `continue` disabled the whole recovery architecture

**Found because the operator looked at the indoor display**, which read `no audio
block for 12815.893s` beside a bat pass from three hours earlier. The station had
been deaf for **3 h 35 min** and nothing had alerted; `/api/v1/health` said
`degraded`, with `state: "capturing"`, while no audio had arrived since
02:12 UTC. That is the third time (§1c) this station's own reporting has described
a dead mechanism as a working one.

### The root cause, stated exactly

`src/open_observatory/audio/alsa_source.py:334` treats **any** ALSA error whose
text contains `Input/output error` as a transient xrun: it increments
`overrun_count`, logs, and `continue`s — **inside the `while collected <
self._block_frames` block-assembly loop**. When the error is permanent rather than
transient, that loop can never finish a block, so `_read_blocking` never returns
and never raises; `_capture_loop` never returns; and
**`_capture_supervisor` — whose own docstring is "Reopen the device with bounded
backoff after any loss", and which increments `stream_restarts`, applies backoff
and emits a `critical` health event — is never reached.**

The recovery architecture was correct and complete. One `continue` in the layer
below it meant control never got there. The evidence:

| Signal | Reading after 3.6 h wedged |
|---|---|
| `capture.overrun … 'Input/output error [hw:CARD=Microphone,DEV=0]'` | logged every 0.576 s, forever; count 306 → **23,135** |
| `stream_restarts` / `open_failures` | **0 / 0** — the supervisor never ran |
| `state` | still `"capturing"` |
| `/proc/asound/card2/pcm0c/sub0/status` | `state: RUNNING`, `avail: 0`, `hw_ptr` frozen at **768** |
| `dmesg` | **last USB event 2026-08-08 13:47** — no disconnect, no reset, no xhci error |

**The device never left the bus and was never at fault.** `power/control` was
`on`, so USB autosuspend is ruled out. A plain `systemctl restart
open-observatory` restored capture immediately — 0 overruns, 0 discontinuities,
`block_age_s` 0.12 — which proves the PCM was reopenable the whole time. **The
supervisor would have fixed this by itself within seconds of it starting.**

This is the same failure as §3a and the fix for §3a does not cover it: there,
ALSA raised `File descriptor in bad state`, which did *not* match the overrun
branch, so it propagated and the station fell back to synthetic. Here the string
*did* match, so it was swallowed. `hardware_recheck_s` only re-probes while
running on the **fallback synthetic** source, and this station never got there.

### Why it wedged when it did — evidenced, but not proven

Gap rows per hour are metronomic from 2026-08-13 00:00 onward: **22–24 rows/hour,
~7 s lost/hour, and they come in `overrun`+`frame_deficit` pairs about 300 s
apart — exactly `retention_interval_s`.** That is the ~304 s beat §1c and §6.1
suspected, now visible from an independent direction, and it means **every
retention sweep was costing audio**. The sweep also got slower as the archive grew
(2.0 s at 54.7 h, 2.44 s by the 14th, against a 1.5 s budget), and the loss rate
stepped up sharply around 13 Aug 00:00 — 184 s lost in the first 54.7 h, then
~168 s/day. The wedge at 02:12 UTC came at the end of that ramp, not out of a
clear sky. **Do not treat "the sweep caused the wedge" as established**; treat
"the sweep is costing ~7 s of audio an hour" as established, because the period
matches to the second.

### The two-branch version of the same bug

The `length == 0` branch twelve lines further down carries the comment "treat a
run of them as a stalled device rather than spinning" — and then does not: it
counts, logs every 500th, sleeps 1 ms and continues, with no bound either. **Both
branches need the same bound**, and a fix that only addresses the `-EIO` one
leaves an identical trap.

### What the fix has to do (needs an ADR, not a patch)

**Delivered by ADR-060; not yet deployed or re-verified on the station.**

1. **Bound the swallow.** Consecutive recoverable errors with no successful read
   must, after a configured count or elapsed time, raise `AlsaCaptureError` so the
   supervisor gets control. Both branches. — done: `AlsaSource.stall_timeout_s`
   (default 5 s), applied to both the `-EIO` and the `length == 0` branch.
2. **Watchdog on `block_age_s`** independent of the read loop, so a stall is
   detected even if a future branch swallows something else. — done:
   `_capture_loop` wraps a live read in `asyncio.wait_for(…, capture_read_timeout_s)`
   (default 15 s), ALSA sources only.
3. **`critical`, not `degraded`**, once no block has arrived for more than a few
   seconds — and the display and MQTT should say the station is *deaf*, not report
   a stale bat pass under a healthy-looking header. — done: `capture_silence_critical_s`
   (default 30 s) now caps severity independent of `capture.state`.
4. `state` must not read `capturing` when nothing has been captured for an hour.
   — deliberately **not** done this way: `capture.state` still describes what the
   capture task was asked to do; `block_age_s` and the new severity rule carry the
   "is audio actually arriving" fact instead, so no field is overloaded with two
   meanings. See ADR-060's "left deliberately unchanged" note.

**What ADR-060 does not fix, and does not attempt to: the wedge itself is
unexplained** — the AudioMoth never left the USB bus and `hw_ptr` was found frozen
at 768 with `state: RUNNING`. ADR-060 makes the station survive a repeat, not
prevents one. **What it does explain is the cost named above as "~7 s of audio an
hour":** ADR-061 traces that beat to the retention sweep's exemplar computation
(2.978 s against a 1.5 s budget) starving the capture event loop every
`retention_interval_s`, each overrun forcing an `snd_pcm_prepare()` restart, and
proves the coupling to this specific wedge to the millisecond — sweep `started_at`
02:18:19.893, `duration_s` 2.6608, first `-EIO` at 02:18:22.552. Same root cause,
same incident, two ADRs: ADR-060 bounds the failure mode, ADR-061 removes the
query that was driving it. Neither has been deployed to the station as of this
writing.

### The soak verdict

The 72-hour window (2026-08-10 14:27 → 2026-08-13 14:27 UTC) closed **11.7 hours
before** the wedge, so the wedge did not spoil it — but the soak **failed anyway**
on continuity: **99.865% against a ≥ 99.9% criterion**, 349.3 s lost of 259,200 s.
See `MILESTONE_STATUS.md`, which is the authority. The §1d reading of 99.906% at
54.7 h was accurate at the time; the run got worse after it, which is exactly why
that entry said one bad hour would take it out.

## 1f. The 2026-08-19 session — three failures a passing soak was hiding

The station looked healthy. It was 49 hours into a restart-free run at
0.999949 continuity with zero overruns, zero gaps and zero late reads. Every
one of those numbers was true. Three separate things were badly wrong anyway,
and the pattern connecting them is the reusable part.

### What was wrong

**1. The 72-hour soak had been voided two days earlier and nobody knew.** The
Pi restarted at 2026-08-17 09:07 UTC, 8.9 hours short, while passing at
99.9935%. Capture reopened in 25 seconds and health returned `ok`. Every
counter that would have shown it — `stream_restarts`, `continuity_ratio`,
`blocks`, `frames` — is process-scoped and reset with the process. Found by
running `uptime`. Cause never established; the evidence (flat memory, load
0.20, no kernel error, no undervoltage, one `LINUX RESTART` in eight days of
sysstat) says external power, with nothing in software to fix. ADR-065 makes
the *next* one visible.

**2. Retention had reclaimed nothing for two days** while the disk climbed
3.8 GB/hour toward a watermark whose own tier was among the ones being skipped.
Every tier's candidate query re-walked the rows it had already reclaimed —
~210,000 old detections examined per sweep to find the 1,699 outstanding —
so **it got slower every time it succeeded** until it could no longer finish
inside its 1.5 s budget. ADR-062.

**3. Every UTC timestamp for 49 hours was 106 seconds early.** Capture
anchored its frame-to-UTC mapping 1 m 45 s before NTP first synchronised after
the boot, and `StreamClock` never revisits that anchor. ADR-063.

### The pattern: a restart makes every instrument agree that everything is fine

All three hid behind the same property. The station's counters are
process-scoped, so they all reset together — and a station that has just
restarted, with a fresh clock anchor and fresh counters, reports a beautiful
clean run no matter what happened before or what is wrong now.

**Anything that must survive a restart to stay true cannot be a counter.** It
has to come from durable state. The two fixes here both do: ADR-065 reads
`audio_stream` rows the previous process left open, ADR-063 compares the anchor
against the wall clock itself.

### The measurement worth stealing

Retention's query was 2.2 s and three plausible explanations fitted (a bad
plan, a slow join, accumulating results). Timing it at increasing `LIMIT`
settled it in one step:

| `LIMIT` | 1 | 10 | 50 | 250 | 1000 | 4000 |
|---|---|---|---|---|---|---|
| time | 2.29 s | 2.22 s | 2.25 s | 2.17 s | 2.20 s | 2.21 s |

**A query whose `LIMIT 1` costs the same as its `LIMIT 4000` is not selecting
rows, it is scanning to reach the first one.** That killed all three hypotheses
and pointed straight at the reclaimed-row prefix. `EXPLAIN QUERY PLAN` had been
no help at all — the plans were byte-identical either side of the problem.

### The instrument that was right

The 106-second error was found because the operator looked at a live-listen
banner reading *"hearing 111.3 s ahead of the newest column"*. Sound cannot be
ahead of the picture, so one number had to be wrong.

Nothing else would have caught it. Health was `ok`, continuity was 0.999949 —
correctly, because continuity is a monotonic-clock property and was genuinely
fine. **A soak can pass every criterion in `ACCEPTANCE_CRITERIA.md` while
writing systematically wrong timestamps.** A UI that had clamped that
impossible-looking negative number to zero would have hidden a data-integrity
bug; keeping it honest is what surfaced it.

### The one the *new instrument* exposed, two hours after being written

ADR-065's unclean-restart note fired immediately after a completely graceful
`systemctl restart`. The signal looked wrong. It was reporting a true fact:
`Station.stop()` cancels the capture task, the supervisor re-raises
`CancelledError` by design, and so it never reaches the call that closes the
`audio_stream` row. **A clean shutdown had never once closed its own row.**
Every row on the station carried `end_reason='process_exited'` — the value
written by the *repair* path at the next startup.

It hid for months because the repair (ADR-024) was good: history, coverage and
frame counts were all correct, and the only costs were a fictional
`end_reason` on every row and a `end_utc` up to one heartbeat stale.

**A repair mechanism that works perfectly will hide the absence of the thing it
repairs.** Worth looking for elsewhere: anywhere this project has a
reconcile/repair/orphan-sweep step, it is worth asking when the primary path
last actually ran. ADR-066.

### And one the retention fix immediately exposed

The first healthy sweep after ADR-062 reclaimed its full 200-file batch and
reported `tiers_skipped=['unkept', 'watermark']`. Correct by the old rules —
and the disk safety valve still did not run, because it was last in a fixed
order and the tiers share one budget. So the whole "the watermark will catch
it at 85%" reassurance had been false the entire time. ADR-064 promotes it
when disk is already over the line.

## 2. How to operate it

```bash
# From a workstation with the repo checked out:
HOST=<user>@<station-host> ./deploy/deploy.sh          # build UI, sync, install unit, restart
./deploy/deploy.sh --no-web --no-deps          # fast code-only redeploy

# On the Pi:
sudo systemctl status open-observatory
sudo journalctl -u open-observatory -f
cd ~/open-observatory && .venv/bin/oo audio probe
.venv/bin/python -m pytest -q
```

UI: `http://<station-host>:8080`. API: `/api/v1/…`. Metrics: `/metrics`. Live
listening now defaults to `GET /api/v1/live/audio.wav` (a plain `<audio>` element),
not the WebSocket channel — see ADR-019 and
`docs/operations/DEPLOYMENT_AND_OPERATIONS.md`.

**`config/runtime.env` on the Pi is not in version control** and holds the station
name, coordinates and any device override. `deploy.sh` excludes it — it must stay
excluded, because `rsync --delete` deleted it once already. As of 2026-08-08 it also
sets `OO_CLIPS_REQUIRE_MOUNT=true` and the clip/rendering limits restored after the
move to a USB SSD — see `docs/operations/DEPLOYMENT_AND_OPERATIONS.md`.

**Evidence clips now live on a USB SSD mounted at `data/clips`, not the SD card**
(ADR-021). The mount must exist before the service starts — it runs inside a systemd
mount namespace — so mounting or replugging the SSD always needs
`sudo systemctl restart open-observatory` afterwards. `OO_CLIPS_REQUIRE_MOUNT=true`
makes a missing mount a named, visible degradation in `/api/v1/health` rather than a
silent write to the SD card.

## 3. The ten bugs found by measuring, and why they matter

Every one of these looked fine until a number was checked. They are listed because
the *method* is the transferable part: measure the property, do not assume it.

| Bug | How it presented | How it was found |
|---|---|---|
| Gap estimator anchored on `open()` | A phantom 0.21 s gap reported on **every** block | The reported "missing frames" was constant, not growing — a fixed bias, not lost audio |
| Clock offset conflated with lost frames | One overrun reported forever as −1439 ppm | 8 s CLI test showed 0.2 ppm; the number had to be wrong |
| Derived-stream timestamps from a running output count | Up to 19 ms of wander | libsoxr emits 4884/4070-frame chunks, not 4800; measured the deficit band over 10 min |
| Activity detector threshold below the noise floor | Fired on 100% of windows | Measured the statistic on pure noise: median 8.5 dB, max 11.9 dB, against a 7 dB threshold |
| Clipping every event | 890 MB in 2 minutes ≈ 640 GB/day | `du` on the clip directory |
| Window leases leaked | Lease count grew without bound | `lease.expired` warnings; releases only happened on the detection path |
| **Concurrent WebSocket writers** | **Spectrogram froze after ~1 frame while JSON kept flowing** | Ran the measurement *in the browser over Wi-Fi*; loopback had looked perfect |
| Ultrasonic hop wider than its FFT | 55% of the audio never inspected; 4% too many columns | Column timeline showed overlapping timestamps and more audio than wall time |
| `/` in SQLAlchemy 2 is *true* division | History buckets never truncated: 1899 ten-minute buckets in a twelve hour window | The bucket count was arithmetically impossible; raw SQL was fine, the ORM expression was not |
| Streams left unclosed by killed processes | Capture coverage of a night reported as 1302% | A fraction above 1 cannot be true |

Two more, cosmetic but misleading: `/metrics` and `/api/v1/detectors` both returned
500 (caught by the new integration tests), and derivative clip durations were
computed from native frame bounds divided by the derivative's own rate, reporting a
4.6 s clip's playback copy as 36.5 s.

### The one that matters most for future work

**Loopback is not a test of a network path.** The concurrent-writer bug produced a
perfect result from a probe on the Pi (100 ms cadence, zero gaps, zero malformed)
and near-total failure in the actual browser, because on loopback sends complete too
fast to ever overlap. Any future work on the live channels must be measured from a
real client over the real link.

## 3a. The 2026-08-08 microphone incident — the most important operational lesson

The AudioMoth's mode switch was moved to `USB/OFF`, most likely while adjusting its
gain (see `TARGET_DIAGNOSTICS.md`). In that position it enumerates as USB ID
`10c4:0002` (HID only) and presents **no ALSA card at all**; `oo audiomoth info`
still works in that state, because it talks to the HID interface — which is itself
the diagnostic trap: a working `oo audiomoth info` is not evidence that streaming
capture will work. Streaming mode is a different USB identity, `16d0:06f3`.

- Live capture failed with `AlsaCaptureError: ALSA read failed: File descriptor in
  bad state` — the device changing USB identity under a file descriptor the capture
  process still had open.
- The station fell back to the synthetic source and correctly reported itself
  degraded, but **never recovered on its own**: `SyntheticSource` never ends, and the
  capture supervisor only rebuilds a source once the current one ends. Recovery
  needed a manual restart. Roughly a day of recording was lost.
- Detectors kept running on synthetic audio throughout and persisted 5 bird
  detections as *Grey-winged Inca-Finch* (a South American species with no plausible
  presence here) plus 515 acoustic events into the real database, indistinguishable
  from genuine records until ADR-020's fix.

**Two fixes, at different layers:**

1. `hardware_recheck_s` (default 30 s, `src/open_observatory/config.py`) makes the
   station periodically re-probe for the real device while running on the *fallback*
   synthetic source specifically — never when synthetic was chosen deliberately — so
   a corrected switch position is picked up without a manual restart.
2. ADR-020: every endpoint that presents detections as observations excludes rows
   whose `source_kind` is not `alsa` by default (`include_synthetic=true` to see
   them). Rows are kept, not deleted — they are a true record of detector behaviour
   on synthetic input, useful for testing — but hidden from browsing views.

## 4. Architecture decisions a successor must not accidentally undo

- **One process owns the microphone.** Everything else consumes windows. Do not add
  a second ALSA opener.
- **Frames, not timestamps, address audio.** `StreamClock` maps frame → time,
  anchored on the first block actually read. Never timestamp derived audio with the
  native block's clock.
- **Never trust an ALSA card index.** Devices resolve by `stable_device_key`
  (USB vendor:product:serial). The AudioMoth moved from card 2 to card 0 across a
  reboot during commissioning.
- **Refuse rate substitution.** `arecord -r 48000` appears to work on this device
  but silently resamples. Capture rejects any rate the device substitutes.
- **One writer per WebSocket.** See §3.
- **Capture always wins.** Every queue between capture and a consumer is bounded
  with an explicit drop policy, and drops are counted and surfaced.
- **Honesty rules are enforced in code**, not just documented: a non-taxonomic
  detector cannot emit a species name (the normaliser raises), an uncalibrated
  detector cannot report a probability, and a synthetic source is announced loudly
  everywhere including `/api/v1/health`.

Deviations from the seed spec are ADR-007 onwards in `docs/architecture/ADRS.md`,
which now carries an **index with a status per ADR** — read that rather than any
list reproduced here, because such a list goes stale every time an ADR is added.
The load-bearing ones for a successor: SQLite in developer mode (ADR-007), native
systemd instead of Compose (ADR-008), an in-process event bus instead of Redis
Streams (ADR-009), an owned activity detector as the first plugin (ADR-010), the
debug UI promoted to the product surface rather than replaced (ADR-011 → ADR-016),
the two-channel live transport and its single-writer rule (ADR-012), the
ultrasonic pass detector as a second owned plugin (ADR-013), the audible rendering
of ultrasonic evidence (ADR-014), and anonymous read access with authentication
deferred (ADR-015) — **now closed by ADR-034, which is off by default, so a
station that has not opted in is still in ADR-015's position.**

## 5. Known-good measured figures (regressions should be judged against these)

**`docs/operations/TARGET_DIAGNOSTICS.md` is the authoritative home for measured
figures.** This table is the summary a successor needs at hand; where the two
disagree, that file wins. Measured on the Pi across 2026-08-04 to 2026-08-08
unless noted.

| Property | Value |
|---|---|
| Capture continuity | 0.9990–0.9997; **0.999474** on the live station 2026-08-09 21:46Z over a 4.03 h restart-free run |
| Gaps / overruns in normal running | Usually 0. On that same 4.03 h run: **12 ALSA overruns, 12 gaps with loss, 0 without**, `estimated_missing_seconds` 6.82. **The estimator is now believable and this is the first on-target case of a real loss**: the raw deficit was 7.64 s, of which 0.75 s is crystal drift at the measured −51.5 ppm, leaving 6.89 s — agreeing with the estimate to 0.08 s over four hours. Contrast the pre-ADR-039 behaviour, which over-reported by 8–13× *while losing nothing*. See `OPEN_INVESTIGATION_CAPTURE_GAPS.md` |
| `late_read_max_frames` | **155,243 of a 192,000-frame ring (81%)** on that run, against 114,362 (60%) and 57,952 (30%) on the two previous readings. Nothing was lost to it, but the trend is the wrong way and `OO_CAPTURE_BUFFER_MS` is the lever |
| Device clock offset | −43 to −52 ppm (a real crystal property, not an error; it moves a few ppm with temperature) |
| Per-block hot-path CPU | 10.9% of one core (was 9.5% before ultrasonic sub-windowing) |
| Whole-process CPU | ~29% of 4 cores with all three detectors |
| Resampler group delay | 0 frames |
| Resampler delivery deficit | bounded 112–924 frames, no trend over 5 min |
| Live spectrogram delivery | 1246 columns / 29.9 s audio per 30 s wall, 0 gaps, 0 overlaps |
| Live audio latency | ~180 ms end to end (139 ms buffer + 42 ms device) |
| BirdNET | p95 77–109 ms, ~40× realtime, 6522 labels |
| Ultrasonic detector | p95 54–104 ms, ~36–40× realtime |
| Activity detector | p95 13–16 ms, ~95× realtime, fires on ~9% of windows |
| BirdNET plausibility | ISO week 29, 2026 (2026-07-13 to 07-19): 139 species plausible at the station's configured location — this figure changes weekly with BirdNET's seasonal range model |
| BatDetect2 (measured, not adopted live) | p95 968 ms per 0.5 s clip, 0.52× realtime, +459 MB RSS |
| BatDetect2 cascade (offline, trimmed 1.5 s clips) | 2.1 s inference per pass; ~36 min classifier work for 1015 passes in one night |

## 6. Immediate next steps, in the order I would do them

### 6.1 Close the Milestone 1–3 gates properly

1. ~~**Run the 72-hour soak.**~~ **Run 2026-08-10 to 2026-08-13, and it
   FAILED.** The acceptance criteria require it before the word "complete" may
   be used, and continuity over the exact 72 hours came in at 99.865% against
   a ≥ 99.9% criterion — 349.3 s of audio lost out of 259,200 s. See
   `MILESTONE_STATUS.md` §Milestone 4.5 for the full account and §1e below for
   the wedge that followed the window. A re-run is needed once ADR-060 and
   ADR-061 are deployed and verified.

   Watch `oo_capture_continuity_ratio`, `oo_ring_extraction_misses_total`,
   `oo_detector_windows_dropped_total`, RSS and the clip budget — and these four,
   which are specific to this run:

   - **`late_read_max_frames` against the 192,000-frame ring.** ADR-059 removed
     the 30 s beat that had taken it to 81%. What remains beats at ~304 s, which
     is `retention_interval_s`. If that climbs, retention is the cause and
     `OO_RETENTION_ENABLED=false` is the experiment. **Read 98.4% at 54.7 h
     (§1d) — worse than before ADR-059, so run the experiment.**
   - **The first retention deletion**, due around 12 August, mid-soak. Nothing has
     ever aged out of the 7-day native tier. Steady state should be ~231 GB of
     458; if no deletion happens, there are ~9 days of headroom, so the soak
     finishes either way but the mechanism is unproven. **Overdue as of
     2026-08-12 18:44 UTC and still zero deletions (§1d); the sweep hits its
     1.5 s budget every pass.**
   - **The refinement timer at 01:00 UTC.** It has never completed a real pass.
     A forced dry run classified 1,200 candidates and 1,796 s of audio after
     ADR-057 unblocked it, but nothing has been written. **Closed: a real timed
     pass completed 2026-08-12 01:00 BST with 2,399 proposals written (§1d).**
   - **`detections_suppressed` on the pause endpoint**, if the operator pauses
     during the run. A paused stretch is recorded and reported beside capture,
     never subtracted from it (ADR-055).

   **Do not deploy during the soak.** `deploy.sh` restarts capture and voids it.
2. ~~**Commit a fixture test that proves a known species from a known recording.**~~
   **Done 2026-08-08** — `tests/test_birdnet_fixture.py`, a committed CC BY-SA
   European Robin recording, passing on the target Pi 5. Struck rather than
   deleted so the record of what was outstanding survives.
3. **Run the one-hour drift test at full duration.** Still outstanding. ADR-046's
   42.7-minute restart-free sampling run is the best evidence so far and its
   longest *clean* segment was 22.2 minutes, which does not reach a mechanism with
   an hourly or nightly period. The method is written down and takes 15 minutes;
   what it needs is a window with nobody deploying.
4. **Re-run the BatDetect2 benchmark and commit its output.** Found 2026-08-09:
   three documents cited `results/batdetect2-pi5.json` as the retained provenance
   for Milestone 5's exit gate, and that file does not exist anywhere — not in the
   working tree, not in git history, not on the Pi, and `results/` is not
   gitignored. The measured figures are retained and are not in doubt, but they
   cannot be traced to an artefact, and "provenance retained" is literally what
   the plan's exit gate asks for. Cheap to close:
   `python scripts/benchmark_batdetect2.py --json results/batdetect2-pi5.json`
   on the Pi, then commit the file. Needs BatDetect2 installed there.

### 6.2 History browsing, and what it still lacks

`HISTORY` mode reads persisted detections, aggregates them in SQL, and shows capture
coverage beside them. What it deliberately does **not** offer is a historical
*spectrogram*: the ring buffer is memory-only by design, and the audio pipeline spec
rules out continuous native-rate archival (66 GB/day at 384 kHz). If a day-view
spectrogram is wanted, the honest way is to persist the uint8 spectrogram columns —
about 40 MB/day for both channels — rather than the audio. That would be a genuinely
useful addition and is not currently planned.

### 6.3 Fix the things I know are wrong or unfinished

0. **North American owls are being reported by the station, and they now reach a
   screen in the operator's house.** — **Fixed in code by ADR-032 (detector) and
   ADR-044 (consumers, plus the week audit). One operator action remains:
   `oo detections reconcile-plausibility` has still never been run against the
   live database, so the historical rows are still unflagged and therefore still
   presented.** Read "What is still open" at the end of this item before doing
   anything.

   Measured 2026-08-08 on the live station: *Western Screech-Owl* at score **0.96**,
   and *Flammulated Owl* separately, both above threshold. Neither occurs in the UK.
   This is the same family of problem as the *Grey-winged Inca-Finch* records in
   §3a, but it is **not** the same cause — these are on genuine `alsa` audio, not
   synthetic, so ADR-020's filter does not hide them.

   The location filter was **on** and correctly configured
   (`OO_BIRDNET_USE_LOCATION_FILTER=true`, with the station's real coordinates
   set in its untracked `runtime.env`), and **the range model
   itself is working**. Priors read straight from the station database are sane:
   Common Woodpigeon 0.995, European Goldfinch 0.781, Western House Martin 0.771,
   and "Engine" 4e-06. This is not a wrong-coordinates problem.

   There were **two distinct defects**, both measured on the live database:

   **(a) A near-zero prior only raised a bar, and the bar was in the wrong space.**
   `BirdNetDetector._band_for` did not suppress implausible species; it put them
   in an `out_of_range` band with `threshold_out_of_range`, default **0.90**.
   Measured: *Flammulated Owl* at `occurrence_probability` **8e-06** with score
   **0.959**, and again at 1e-05 with 0.954, 8e-06 with 0.931, 8e-06 with 0.924.
   The range model was saying "essentially impossible here" and being overruled by
   a number that is not a probability. **Fixed:** `_band_for` (now a module-level
   `band_for`) adds an `implausible` band with an unreachable (`math.inf`) bar for
   any species at or below the new `birdnet_plausibility_floor` setting (default
   `0.0005`, derived from the measured owls at 8e-06–1.6e-04 versus a genuine,
   seasonally-uncommon Tawny Owl at 0.019253) — no score admits it, which is a
   different operation from raising the bar further, since a bar set in
   score-space cannot separate a Eurasian Jackdaw at 0.617 from a Flammulated Owl
   at 0.959.

   **(b) A *missing* prior got the LOWEST bar, not the highest.** When occurrence
   was `None`, `_band_for` returned `("unfiltered", self._thresholds["in_range"])`
   — the in-range threshold, which is the easiest of the three — regardless of
   whether the range model was loaded at all. So a species the range model
   cannot speak for was treated as though it were a garden regular. Measured:
   *Great Horned Owl* score 0.917 `occ=None`; *Flammulated Owl* 0.876 and 0.805,
   both `occ=None`. **202 of 5833 named detections (3.5%) took this path.**
   **Fixed:** `band_for` now takes an explicit `range_model_loaded` argument
   instead of inferring "no range model" from `occurrence is None`; with the
   model loaded but silent about a species, the result is a `no_prior` band using
   `threshold_out_of_range` (0.90), not `threshold_in_range` (0.55). With no range
   model loaded at all, the old uniform behaviour is unchanged — that remains
   defensible, since there is genuinely no plausibility information in that case.

   `_suppressed_out_of_range` counted every candidate that fell below its band's
   threshold, including `uncommon` ones — not a count of *suppressed
   out-of-range* species. **Fixed:** split into
   `_suppressed_implausible_prior` / `_suppressed_no_prior` / `_suppressed_uncommon`
   / `_suppressed_out_of_range`, each scoped to exactly its own band, surfaced via
   `BirdNetDetector.plausibility_snapshot()` and
   `oo_birdnet_suppressed_total{plugin_id, reason}` in `api/metrics.py`.

   **What is still open:**
   - **The ~5833 historical rows are not retroactively corrected by the code
     change.** `oo detections reconcile-plausibility` (`cli.py`, logic in
     `plausibility_repair.py`) re-evaluates them against the current range model
     and floor, dry-run by default, and on `--apply` writes a
     `native_result.plausibility_review` block — never deletes a row or
     overwrites the original `native_result`. **Not yet run against the live
     station's actual database** (only against synthetic fixtures and a stubbed
     range model); do that read-only/dry-run first (`--json` piped to a file) and
     review the output before ever passing `--apply` there.
   - ~~**No consumer hides a flagged historical row.**~~ **Done (ADR-044,
     2026-08-09.)** `plausibility.py` is now the single definition of "withdrawn"
     and five surfaces read it: `GET /api/v1/detections` (and the detail view and
     the CSV/JSON export) keep the row and mark it `withdrawn: true` with a
     `withdrawal` block carrying the reviewer's recomputed prior, threshold,
     reason and timestamp; `/api/v1/history`'s species list and
     `/api/v1/taxa/activity` exclude it and report `excluded_withdrawn_count`;
     the MQTT publisher does not publish it (counted as
     `oo_mqtt_suppressed_withdrawn_total`); the `/api/v1/display` channel filters
     it in SQL *and* on the wire; and the ESP32 refuses it on the HTTP fallback
     path (`detection_feed.cpp`, including its streaming JSON filter). The web UI
     marks it everywhere and explains it in the detection drawer. The split — a
     *record* is marked, a *claim* is suppressed — is argued from charter items 5
     and 6 in ADR-044. ~~The firmware change is committed but NOT flashed.~~
     **Flashed 2026-08-09** as part of ADR-050's OTA work; the station reported
     the display at firmware `0.2.4`. (The push path already protected an
     unflashed device anyway, since the station never sends a withdrawn row; the
     firmware change only ever mattered for the HTTP fallback.)
   - ~~**The week index passed to the range model was not re-audited.**~~
     **Done (ADR-044): it is correct.** Re-derived from the code and asserted for
     every day of a common and a leap year (48 weeks, four per calendar month,
     range exactly [1, 48] — *not* an ISO week: 2026-08-08 is BirdNET week 30 and
     ISO week 32), then verified empirically by running the real MData model at
     the station's coordinates for all 48 weeks: Common Swift peaks w22, Cuckoo
     w17, Fieldfare in w45-w5, Woodpigeon flat, both North American owls 0.000 in
     *every* week. `scripts/birdnet_week_audit.py` re-runs it. Note found on the
     way: a week outside [1, 48] is not rejected by the model, it returns the
     year-round prior (Swift 0.913 rather than 0.000 in January), so a wrong week
     would have disabled seasonality silently rather than failing.

   **What is still open, and it is an operator decision, not a code one:**
   `oo detections reconcile-plausibility` has **never been run against the live
   station's database**, so no row there is flagged yet — which means the
   ~202+ historical rows, including the measured Western Screech-Owl and
   Flammulated Owl, are still presented everywhere. The consumer side is now
   ready and takes effect immediately on `--apply`, with no restart. Do the
   dry run first (`--json` piped to a file), read it, and only then decide.

   **Update, 2026-08-09: the dry run was done, read-only, and it changed the
   answer (ADR-049).** Against the live database — 67,679 rows at the time — it
   proposed 114 findings, and **91 of them were correct detections**: 62
   `Engine`, 24 `Human vocal`, 5 `Dog`. These are BirdNET's non-biological
   output classes, for which the range model has no meaningful prior (it
   returns 4e-06 for "Engine" because a car is not a taxon with a
   distribution), so ADR-032's floor was about to withdraw a stack of true
   observations. Three things came out of that and are now fixed in code:

   * `band_for` exempts the eleven sound categories
     (`detectors/birdnet_classes.py`). Re-measured on the same database
     read-only: **114 → 23** findings at the command's default `--limit 5000`,
     369 → 123 over the whole table. The remainder is genuinely implausible
     species — Flammulated Owl, Grey-winged Inca-Finch, both Screech-Owls,
     Gray Wolf, and 62 `Spotted Crake` at occurrence 4.14e-04 that the
     default limit had never reached.
   * Those classes were **stored as birds at species rank** — 247 rows, with
     `scientific_name` repeating the common name and a fabricated
     `canonical_taxon_id` of `sci:engine`. The detector no longer does that,
     the normaliser has a per-detection backstop, and
     `oo detections reconcile-taxonomy` corrects the stored rows without
     deleting any.
   * **24 `Human vocal` detections were holding 48 evidence clips and 125 MB.**
     `clip_human_audio` (default off) stops new ones;
     `oo clips purge-human-audio` removes the existing ones and keeps the
     detection rows.

   **None of the three commands has been run with `--apply` on the live
   station** — re-confirmed 2026-08-09. The order to run them in is privacy,
   taxonomy, then plausibility. Until then the historical rows, including the
   measured *Western Screech-Owl* and *Flammulated Owl*, are still presented
   everywhere. This is the largest remaining item in §6.3 and it is an operator
   decision, not a code one: the code side is finished and takes effect
   immediately on `--apply`, with no restart.

4. **Reduce the AudioMoth gain.** The input still clips on loud nearby events. This
   needs the AudioMoth USB Microphone app with the switch in `USB/OFF`; the HID
   app-packet format for writing configuration is not implemented here. **Warn
   whoever does this that capture stops for the duration** — moving the switch to
   `USB/OFF` is exactly what caused the incident in §3a, and the automatic recovery
   in `hardware_recheck_s` only helps once the switch is back in `DEFAULT`. Either
   implement the HID write path (see `AudioMoth-USB-Microphone-App` for the packet
   layout) or do it by hand and record the new setting in `TARGET_DIAGNOSTICS.md`.

   **Deprioritised 2026-08-09, on the operator's instruction, and the reason
   matters.** The microphone currently sits next to a plant rubbing against a
   shed, so there is loud periodic mechanical noise in the recordings. That is a
   *siting* problem he intends to solve physically once wiring and mounting are
   settled — not a gain problem and not a software problem. Chasing the noise
   floor against a temporary physical fault would tune the system to a condition
   that is about to disappear, and any threshold derived from it would then be
   wrong.

   So: **do not tune anything against the current noise floor.** What was asked
   for instead is that the knobs be *exposed* — spectrogram floor/ceiling,
   `min_snr_db`, `min_pulses_per_pass`, band edges, detector thresholds — so the
   operator can tune locally when the microphone is in its final position
   (ADR-048). Treat any measurement of background level taken before the move as
   provisional and label it so.

   This also colours item 6: the hot gain remains a live confound for the Myotis
   question, and now so does the plant. Neither is resolved by code.
5. **Decide whether to promote the BatDetect2 cascade from an offline script to a
   live, queued detector.**
   **Decided and delivered 2026-08-09 (ADR-045), differently from how this item
   framed it.** Not a live detector and not `DeferredDetectorWorker`: a separate
   process, `oo refine run`, on `open-observatory-refine.timer` at 01:00 UTC,
   CPU-fenced to cores 2-3. It writes append-only `refinement` rows and three
   bookkeeping columns on `detection` (`refined_at`, `refinement_version`,
   `refinement_outcome`) and **never edits a detection's species, score or
   `native_result`** — enforced in code, with a before/after comparison of the
   claim columns that raises if anything moved.

   **What is still open here:** nothing connects a proposal to the review
   workflow. `POST /api/v1/detections/{id}/review` exists (ADR-029) but knows
   nothing about proposals, so `refinement.resolved_at` stays NULL and the only
   way to see a proposal is `oo refine status`. Wiring "accept this proposal" to
   a `review` row — and deciding whether an accepted one may finally move the
   detection's claim — is the next piece of charter item 5.

   **And the retention gap ADR-045 records but does not close:** `retention.py`
   still deletes clips on age alone. `oo refine status` reports how many bat
   detections have never been examined by any refiner; those clips will be
   reclaimed at 7/30/90 days regardless. The one-line predicate that would fix it
   is in ADR-045; applying it changes a live station's deletion policy and needs
   the operator, plus at least one completed refinement cycle first, or every
   deletion freezes.
6. **Review the ultrasonic detector's false-positive rate, and resolve the Myotis
   question.** On a windy or handling-noisy evening it reports "bat pass" for
   broadband transients. Offline BatDetect2 classification of the station's own
   33-36 kHz cluster leaned Myotis on 6 of 8 clips but at low confidence
   (0.20-0.30), and produced one confident contradiction — 0.77 for *Pipistrellus
   pygmaeus* on a 34 kHz call, when soprano pipistrelle actually peaks near 55 kHz.
   The hot AudioMoth gain (item 4) is a plausible confound. This needs a human
   listening to the audible renderings, not a code change; use them to tune
   `min_snr_db`, `min_pulses_per_pass` and the band as well.

   **Still open, and now instrumented (ADR-045).** The refinement runner records
   each BatDetect2 opinion as a `refinement` row carrying the station's own
   `peak_frequency_hz`, `peak_snr_db` and `pulse_count` next to the model's
   species and det_prob — the exact pairing that exposed the *pygmaeus*
   contradiction — plus a `caution` string flagging a sub-0.5 det_prob as a lean,
   a runner-up within 0.15 as the model failing to separate species, and the hot
   gain as an unresolved confound. This gathers the evidence for the human ear
   this item asks for; it does not substitute for it, and nothing the runner
   writes reaches a display or the API.
7. **`oo audio window-dump`.** **Done, 2026-08-09.** Milestone 2 asked for a window
   inspection CLI; only `oo audio resample-check` existed before this. It runs the
   real `StreamClock`/`AudibleResampler`/`StreamSegmenter`/`RingBuffer` classes over
   a replayed WAV or synthetic scene — deliberately not against a running station,
   since the native ring is in-process memory owned by whichever process holds the
   microphone, and this command must not perturb capture. Each reported window's
   frame count comes from its own `pcm` array shape, cross-checked against an
   independent `RingBuffer` read of the same frames, and `--gap-at-s` shows a
   discontinuity's real effect on the segmenter's frame accounting rather than
   asserting one. See `oo audio window-dump --help`, `tests/test_cli_audio.py`, and
   `docs/operations/DEPLOYMENT_AND_OPERATIONS.md`'s CLI table. This closes the
   line item but not Milestone 4.5's own exit gate, which still needs the 72-hour
   soak and the full-hour drift run.
8. **Cover the history endpoints at the HTTP level.** `tests/test_history.py` tests
   the aggregation functions; nothing exercises `/api/v1/history` or
   `/api/v1/history/windows` through the app, which is where the true-division
   bucket bug would have shown itself.
   **Done (ADR-024 session):** `tests/test_history.py::TestHistoryHTTP` now runs a
   real `FastAPI` app + synthetic capture through `TestClient` and hits both
   endpoints, including the coverage block and bucket-truncation check.
   Same session also found and fixed a live-data instance of the 1302%-coverage
   family of bug: a stream row claimed a 32 hour span but its own `frame_count`
   showed 2.79 hours of actual audio. See ADR-024, `history.coverage()`, and
   `oo history reconcile-streams` for the repair path.
9. **Close the event-envelope schema gap.** `schemas/detection-event.schema.json`
   set `additionalProperties: false` and omitted `rank` and `taxonomic_group`,
   which internal records carry.
   **Done (ADR-025 session, 2026-08-08):** the schema now describes the full bus
   envelope, adds `rank`, `taxonomic_group` and `media`, and is bumped to
   `schema_version` 1.1 — a version bump rather than a silent widening, because a
   strict consumer validating against 1.0 would have rejected the new fields.
   `tests/test_mqtt_schema.py` validates real emitted events against it so it
   cannot drift again. The MQTT publisher was indeed the forcing function.
10. **Write the Alembic migration environment before, not during, the PostgreSQL
    move.**
    **Done (ADR-035, 2026-08-08):** `alembic/` now exists with three revisions,
    wired to `Settings` and the declarative metadata, `render_as_batch=True` for
    SQLite. The live station was adopted by `alembic stamp 0001_initial` then
    `alembic upgrade head` and now reports `0003_auth_tables (head)` with its
    50,396 detections intact. Building it found a real latent bug:
    `media_asset.reclaimed_at` existed but its index never did, because
    `ALTER TABLE ADD COLUMN` cannot create one — revision 0002 repairs it.
    **Fully closed (ADR-042, 2026-08-09):** `deploy/deploy.sh` now runs
    `alembic upgrade head` as an explicit step before every restart, and
    `api/app.py`/`cli.py` call a new `ensure_schema_at_head()` instead of
    `create_all()`. The `create_all()`+ALTER TABLE patcher is retired from every
    production code path (`create_all()` itself survives only as a test helper).
    **There were six revisions on 2026-08-09, with the live station at
    `0006_refinement`, which was `head` at the time** — read from its own
    `alembic_version` table, read-only, on 2026-08-09 at 21:46Z, holding 74,969
    detections, 35,285 media assets, 65 reviews and 0 refinements. **As of
    2026-08-14 there are ten revisions; head is `0010_kept_at_partial_index`.**
    ADR-042's deploy step has therefore carried a real
    station across three revisions (`0004` → `0005` → `0006`) unattended, which is
    the thing that was untested when that ADR was written. An earlier reading at
    `0004` (65,515 detections, 28,183 media assets) also confirmed
    `alembic upgrade head` a true idempotent no-op against a read-only copy.

### 6.3b Closed on 2026-08-10, recorded so they are not rediscovered

- **Settings could not be written on the station.** `ProtectHome=read-only` with
  only `data/` whitelisted, so every `PUT /api/v1/settings` returned 500 with
  `EROFS`. `config/` is now in `ReadWritePaths`, and `tests/test_systemd_unit.py`
  asserts every directory the code writes to is whitelisted.
- **The refinement runner had never run.** numba, via librosa, JIT-compiles on
  first use and caches beside the library's own source, which the same sandbox
  makes unwritable. `NUMBA_CACHE_DIR` now points into `data/`. Verified under a
  replica of the real sandbox.
- **8,067 media rows claimed files that were gone** (ADR-057). Reconciled on the
  live station: rows kept, `reclaim_reason = "missing"` rather than a tier name,
  original claim preserved. This is what unblocked the refiner, which is
  oldest-first and had never reached the 4,759 bat detections that still have
  audio.
- **`data/clips.sdcard-backup` deleted**, 21 GB freed on the SD card. Verified
  first that no live row pointed into it and that no live row had an absent file.
- **`disk_usage()` walked the archive on the event loop** (ADR-059).
- **Two named windows resolved into the future** — `last-night` and
  `dawn-chorus`. A property test now asserts none can, at every hour of the day.
- **Mobile layout** (ADR-054), the **pause menu opening off-screen**, and
  **overlapping detection labels** (ADR-058).
- **All eight original Dependabot PRs resolved**; five new ones are open.

### 6.3a Small, known, and unfixed

Minor items that are real but not worth a numbered slot. Recorded so they are
not rediscovered.

- **The web UI favicon — done, 2026-08-09.** Previously there was no
  `web/public/` and no `<link rel="icon">` in `web/index.html`, so every page
  load requested `/favicon.ico` and got the SPA fallback or a 404.

  Shipped the operator's choice, the Material Design Icons `bird` glyph,
  fetched genuinely rather than reconstructed: `@mdi/svg` **7.4.47** was
  downloaded from the npm registry (tarball SHA-256
  `de92e5dc9ce46c392ab5c53aa7190b19f82b40cb48872a083f788c7e13e91fef`), and the
  `d` path of `svg/bird.svg` (SHA-256
  `70e0790bd69196c357bf47fe353941eb5e3614a46058a8622f3f4661048deec1`) was
  copied verbatim into `web/public/favicon.svg` — see
  `web/public/ATTRIBUTION.md` for the full provenance record, in the manner
  of `models/manifest.tsv`, plus the Apache-2.0 licence text and what
  presentational changes (background tile, colour, scale/centring) were made
  and why. The glyph's own bounding box already ran edge-to-edge in its
  24x24 canvas, so at 16px it needed a background treatment to read at all:
  it now sits on a rounded dark tile (`--bg`, `#08090d`) at 80% scale,
  recoloured to the app's own `--bird` accent (`#5ce08a`) — confirmed
  legible (a recognisable bird silhouette, eye visible at 32px) by rendering
  and inspecting the actual 16px and 32px output, not by assumption.

  Shipped: `favicon.svg`, a multi-resolution `favicon.ico` (16/32/48px),
  `apple-touch-icon.png` (180px), `icon-192.png`/`icon-512.png`, and
  `site.webmanifest`, all in `web/public/` (copied verbatim into
  `web/dist/` by Vite's existing public-dir build step, so nothing changed
  there) and linked from `web/index.html`. All served from the station's own
  `StaticFiles` mount in `src/open_observatory/api/app.py` — nothing is
  fetched from a CDN at runtime. Verified with real HTTP requests through
  the actual FastAPI app (`TestClient`, both against a synthetic `dist/`
  fixture and against a real `vite build` output) that every path returns
  200 with the right content-type, not just that the files exist on disk;
  see `tests/test_web_icons.py`.

### 6.4 Then the plan's own next milestones

Milestone 5 (ultrasonic and bat support) is complete. **Milestones 4 and 6 were
largely delivered on 2026-08-08** — this section is updated rather than removed,
because what remains of each is the useful part. `MILESTONE_STATUS.md` is the
authority; the short version:

11. **Milestone 4 — largely delivered.** Styling (ADR-027), the frontend test
    harness, `App.tsx` state extraction, operator/diagnostic disclosure
    (ADR-028), CSV/JSON export, the tiered retention backend and its UI
    (ADR-026, ADR-029) and the authentication foundation (ADR-034, closing
    ADR-015) have all landed. **Review workflow closed (2026-08-09, ADR-043):**
    `POST`/`GET /api/v1/detections/{id}/review` now support `confirmed`,
    `rejected`, `corrected` (with `corrected_taxon_id`, resolved against a new
    `GET /api/v1/taxa/search`) and `held`. The correction is denormalised onto
    the `review` row and surfaced everywhere an identification is shown
    (detail, list, CSV/JSON export) without ever editing the original
    detection; a human review now outranks `plausibility_repair.py`'s
    machine refinement; and a `held` review exempts a detection's evidence
    from the retention sweeper's age-based tiers (not the watermark safety
    valve — see ADR-043's "known limitations"). Known gap: the aggregate
    `GET /api/v1/history` species/timeline view does not yet fold corrections
    into its `GROUP BY`.
12. **Milestone 6 — publisher delivered, alert engine not.** The MQTT publisher
    and Home Assistant Discovery are live against the operator's real broker,
    six entities under one device (ADR-025). **Still open, and unchanged from
    the original scope:** environmental telemetry ingestion, the alert rule
    engine with repetition and cooldown, and HMAC-signed outgoing webhooks.
13. **Milestone 7 — not started.** Read-only MCP tools, export bundles,
    backup/restore commands, a setup wizard/commissioning report, and a
    vulnerability scan. Partial credit only: the systemd unit already applies
    privilege reduction, model licences are surfaced through `/api/v1/models`,
    and media paths are validated against the clip directory before being served.

### 6.5 Longer-term, and worth deciding early

- **PostgreSQL migration.** ADR-007 keeps SQLite for the debug slice. Anything
  needing concurrent writers, `LISTEN/NOTIFY` or JSON indexing must wait for this.
  The DSN is intended to be the only change. **The Alembic environment now
  exists** (ADR-035; ten revisions as of 2026-08-14, head `0010_kept_at_partial_index`),
  so the prerequisite this entry used to name is met — but it
  has only ever been exercised against SQLite, so "the DSN swap is
  configuration-only" stays unverified until someone runs it against a real
  PostgreSQL 16 instance.
  **Done (ADR-042, 2026-08-09):** startup now calls `ensure_schema_at_head()`
  and `deploy/deploy.sh` runs `alembic upgrade head` before every restart (see
  §6.3 item 10). PostgreSQL itself remains unexercised in this repository.
- **Redis Streams.** ADR-009's `EventBus` protocol is the seam. The bounded queues
  and drop counters already model the back-pressure a real transport imposes.
- **USB SSD: done (2026-08-08, ADR-021).** Evidence clips now live on a 465.8 GB
  SanDisk Extreme Portable SSD mounted at `data/clips`; the SD card's old clip
  directory, `data/clips.sdcard-backup`, was **deleted 2026-08-10** (see §6.3b),
  freeing 21 GB on the SD card. The database deliberately stays on
  the SD card. A continuous native-rate audio archive remains explicitly out of
  scope regardless of available space (see §6.2).

## 7. Traps worth knowing about

- **`pkill -f "oo serve"` from an ssh command matches its own command line** and
  kills the ssh session, not the service. Use `systemctl`.
- **`rsync --delete` will remove anything gitignored from the target.** Already
  bitten once (`config/runtime.env`).
- **The AudioMoth's switch position changes its USB identity.** `USB/OFF` gives HID
  and no ALSA card at all; `DEFAULT` gives USB Audio. A device that seems missing is
  usually in the wrong position, not broken.
- **A weak PSU limits USB current.** An intermittently-enumerating microphone is a
  plausible symptom; one was replaced during commissioning.
- **`ai-edge-litert`, not `tflite-runtime`.** The latter has no cp312 aarch64 wheel
  and needs NumPy 1.x.
- **Test helpers can mask a fix.** The activity detector's recalibration appeared not
  to work because the test helper passed the old threshold explicitly.
- **`/` on an Integer column in SQLAlchemy 2 is true division**, not integer
  division: it casts to NUMERIC. Use `x - (x % n)` to truncate, which also needs no
  `FLOOR` and behaves the same on SQLite and PostgreSQL.
- **A stream row is only closed on graceful shutdown.** `Station.start` now closes
  any left open by a previous process; without that, history treats them as still
  running and coverage exceeds 100%.
- **The default thread pool is shared with everything except capture.** Anything
  using `asyncio.to_thread` — clip writing, retention sweeps, database inserts,
  health-event writes, every FastAPI `def` endpoint — competes for the same 8 worker
  threads on a 4-core Pi, and SQLite is configured `busy_timeout=5000` so one
  contended write can hold a worker for seconds. Evidence extraction and retention
  got their own single-thread executor on 2026-08-05; `AlsaSource` got one
  (`oo-capture`) on 2026-08-08. Anything else doing sustained disk I/O needs the
  same treatment.
- **Ring depth is the only slack the capture path has, and it was 80 ms.**
  `AlsaSource` asked for `periods=8` at a 10 ms period, behind a 100 ms block: the
  kernel could hold less audio than one read consumes. `/api/v1/health` was
  publishing `buffer_size: 30720` next to `block_frames: 38400` the whole time and
  nobody compared them. It is now sized from `capture_buffer_ms` (500 ms) and a ring
  clamped below one block logs `capture.buffer_shallower_than_block`. See ADR-030.
- **`grep -c capture.gap` overstates lost recording — measured at 2.7×.** Many gap
  records lose nothing; a minority lose real audio. Worse, until 2026-08-08 an ALSA
  overrun *skipped* the frame-loss estimate entirely, so `missing_frames=0` meant
  "not measured", not "nothing lost" — and the uncredited deficit dragged
  `rate_offset_ppm` to −245 against a true device offset near −43. Use
  `gaps_with_loss` / `gaps_without_loss` from `/api/v1/health`.
- **`expected_frames - frames` is not a measure of lost audio, and an earlier
  round of this project told people it was.** It is loss *plus* crystal drift
  (~0.18 s per hour at this device's ≈−50 ppm — 4.4 s a day with nothing lost)
  *plus* a block-sampling phase artefact worth about **±50 ms on any single
  reading**, because `frames` advances in whole 100 ms blocks while
  `expected_frames` comes from a continuous clock. Since ADR-039,
  **`estimated_missing_seconds` is the figure to judge loss by** — it is a
  decomposition of the deficit, not a rival to it — and ADR-046 is why the UI
  shows the two separately as "audio lost" and "behind clock". If a document tells
  you to prefer the raw deficit, it predates ADR-046.
- **A stream row's `end_utc` is when the process noticed, not when audio stopped**,
  and `frame_count` is written only on a graceful close. 48 of 49 rows on the station
  carried `frame_count = 0`. The station now checkpoints frames into the open row
  every 30 s, but old rows are still zero. Cross-check a span against `capture_gap`
  and `detection` rows before believing it.
- **`deploy/deploy.sh --no-web` deletes the Pi's `web/dist`.** The rsync uses
  `--delete` and does not exclude it, so skipping the UI build removes the built UI
  from the target. Build the web UI, or sync only what changed.
- **A bottleneck can be load-bearing.** Evidence writing was awaited inline in
  `ultrasonic-pass-v1`'s own detector task, so it analysed 29 windows and dropped 69
  with a 42 s lag, even though its own inference p95 was 57 ms — the stall was
  entirely in the write, not the model. Routed through a bounded queue instead: 76
  analysed, 0 dropped, lag 0.14 s. Fixing that stall tripled evidence volume and
  exposed an SD-card I/O limit that had never been reached — the limit that
  eventually justified the USB SSD (ADR-021). Expect the next constraint to appear
  when you remove one.
- **Clip volume on a busy bat night is the binding storage constraint — and it was
  fixed at the device, not by throttling.** Roughly 15 MB per pass across four
  clips, 15 GB in one night, against a 20 GB SD-card budget that was already
  exceeded. The temporary mitigation was heterodyne-only rendering
  (`OO_ULTRASONIC_AUDIBLE_METHOD=heterodyne`) and `OO_CLIP_MAX_PER_MINUTE=6`; as of
  2026-08-08 (ADR-021) evidence moved to a 465.8 GB USB SSD and both throttles were
  lifted — `OO_ULTRASONIC_AUDIBLE_METHOD=both`, `OO_CLIP_MAX_PER_MINUTE=20`,
  `OO_CLIP_MAX_TOTAL_GB=300`. If a future station shows the same symptom before its
  SSD is fitted, the same temporary settings are the known-good stopgap.
- **`pytest` escalates `DeprecationWarning` to an error** by configuration. That is
  deliberate — it is what forced the FastAPI lifespan migration — but it means a
  dependency deprecation will fail the suite.
- **`oo audiomoth info` succeeding is not evidence that capture will work.** It
  talks to the HID interface, which is present even in `USB/OFF` — the position that
  has no ALSA card at all. §3a's incident was diagnosed by this exact confusion.
- **A mount created while the service is running is invisible to it.** The systemd
  unit runs in a mount namespace (`ProtectHome=read-only`,
  `ReadWritePaths=/home/<user>/open-observatory/data`). Mounting or replugging the
  USB SSD at `data/clips` always needs
  `sudo systemctl restart open-observatory` afterwards — `mount`/`df` showing it on
  the host is not sufficient. See ADR-021.

## 8. What must not be claimed

Per `CLAUDE.md`, this system is not complete and must not be described as such
until the acceptance criteria pass a 72-hour soak. Specifically avoid claiming:

- that any detector "identifies" anything the detector itself does not claim —
  `ultrasonic-pass-v1` detects passes, not species;
- that scores are probabilities, unless the detector declares calibration;
- that levels are sound pressure levels — no calibration procedure exists;
- that bat support includes species identification — a night scheduler and pulse-
  train pass detector run live, but there is no live classifier: BatDetect2 was
  measured and deliberately not adopted for real-time inference (ADR-017), and its
  offline cascade (§1a) is evaluated, not adopted, per the same ADR;
- that the 33-36 kHz cluster this station reports is Myotis — offline classification
  is suggestive (6 of 8 clips) but low-confidence and contradicted once; unresolved;
- that the species list this station reports is filtered to what actually occurs
  here. It is not. The range model raises the confidence bar for implausible
  species, it does not exclude them, and *Western Screech-Owl* (0.96) and
  *Flammulated Owl* both cleared it on live audio on 2026-08-08. See §6.3 item 0.
  A high BirdNET score on a species absent from the continent is evidence that the
  score is meaningless for that species, not evidence of the bird.
