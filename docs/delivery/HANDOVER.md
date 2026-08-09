# Handover: state, decisions, and what to do next

Written at the end of the first implementation session, 2026-08-04, against the
live station at `station.example`; revised 2026-08-08 to cover Milestone 5's
completion, an AudioMoth outage and its fix, live playback being rebuilt onto a
different transport, and evidence storage moving to a USB SSD; **claims
re-verified against the code and the live station on 2026-08-09.**

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
same API now also feeds an **ESP32 wall display** (ADR-023) and an **MQTT publisher**
running live against the operator's Home Assistant broker (ADR-025), and an
**authentication foundation** exists, off by default (ADR-034).

Measured 2026-08-09 on the development laptop against this branch: **389 Python
tests pass, 6 skip** (`pytest -q --deselect tests/test_api.py::TestLiveChannels`;
the skips are the unbundled-model fixture tests, by design) and **140 frontend
tests pass**. `ruff check .` is clean; `mypy src` reports 29 pre-existing errors
and has never been clean. The last recorded full run *on the target device* was
197 Python tests on 2026-08-08 — that number is a snapshot of that run, not the
current suite. It runs as a systemd unit and survives reboots.

Milestone 5 (ultrasonic and bat support) is complete: a bat pass detector runs
live, gated to night by a solar scheduler, and BatDetect2 was benchmarked on the
target and deliberately *not* adopted as a live detector — see §1a. It is
**not complete** as a whole system: no 72-hour soak, an open capture-gap
investigation, a minimal review workflow, and Milestone 7 not started.

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
prints a comparison, and does not write to the database. It is not wired into the
live pipeline as a registered detector plugin; `DeferredDetectorWorker` exists as the
general mechanism that could carry it (`detectors/deferred.py`), but nothing uses it
yet. Whether to promote the offline script into a live, queued adapter is an open
decision, not a technical blocker — see ADR-017's 2026-08-05 update for the numbers.

## 1b. The 2026-08-08/09 session — what changed and what it cost

A long multi-agent session. The transferable parts:

**Delivered:** an ESP32 wall display (ADR-023, ADR-038); MQTT + Home Assistant,
live on the operator's broker (ADR-025); tiered retention (ADR-026); live
ultrasonic retuning restored (ADR-022); coverage bounded by delivered frames
(ADR-024); the capture-gap root cause and fix (ADR-033); BirdNET plausibility
filtering (ADR-032); an Alembic environment (ADR-035); an authentication
foundation, off by default (ADR-034); the deficit estimator corrected (ADR-039);
a committed species fixture passing on target; a documentation audit; and
`docs/CHARTER.md`.

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

## 2. How to operate it

```bash
# From a workstation with the repo checked out:
HOST=station.example ./deploy/deploy.sh          # build UI, sync, install unit, restart
./deploy/deploy.sh --no-web --no-deps          # fast code-only redeploy

# On the Pi:
sudo systemctl status open-observatory
sudo journalctl -u open-observatory -f
cd ~/open-observatory && .venv/bin/oo audio probe
.venv/bin/python -m pytest -q
```

UI: `http://station.example:8080`. API: `/api/v1/…`. Metrics: `/metrics`. Live
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
| Capture continuity | 0.9990–0.9997; **0.999907** on the live station 2026-08-09 over 12.4 h |
| Gaps / overruns in normal running | 0 — **but see the open investigation.** On 2026-08-09 the station reported 369 `capture.gap` records and 3 ALSA overruns over 12.4 h, and its real frame deficit over that period was ~4.1 s (0.0095%) against an `estimated_missing_seconds` of 54.5 — a ~13× over-report by the deficit estimator, not lost audio. See `OPEN_INVESTIGATION_CAPTURE_GAPS.md` finding 2 and ADR-033 |
| Device clock offset | −43 ppm (a real crystal property, not an error) |
| Per-block hot-path CPU | 10.9% of one core (was 9.5% before ultrasonic sub-windowing) |
| Whole-process CPU | ~29% of 4 cores with all three detectors |
| Resampler group delay | 0 frames |
| Resampler delivery deficit | bounded 112–924 frames, no trend over 5 min |
| Live spectrogram delivery | 1246 columns / 29.9 s audio per 30 s wall, 0 gaps, 0 overlaps |
| Live audio latency | ~180 ms end to end (139 ms buffer + 42 ms device) |
| BirdNET | p95 77–109 ms, ~40× realtime, 6522 labels |
| Ultrasonic detector | p95 54–104 ms, ~36–40× realtime |
| Activity detector | p95 13–16 ms, ~95× realtime, fires on ~9% of windows |
| BirdNET plausibility | week 29, 139 species plausible at the development station |
| BatDetect2 (measured, not adopted live) | p95 968 ms per 0.5 s clip, 0.52× realtime, +459 MB RSS |
| BatDetect2 cascade (offline, trimmed 1.5 s clips) | 2.1 s inference per pass; ~36 min classifier work for 1015 passes in one night |

## 6. Immediate next steps, in the order I would do them

### 6.1 Close the Milestone 1–3 gates properly

1. **Run the 72-hour soak.** This is the single biggest outstanding item and the
   acceptance criteria require it before the word "complete" may be used. Watch
   `oo_capture_continuity_ratio`, `oo_ring_extraction_misses_total`,
   `oo_detector_windows_dropped_total`, RSS, and the clip budget.
2. **Commit a fixture test that proves a known species from a known recording.**
   Milestone 3's exit gate asks for this and it is currently only demonstrated
   live. Needs a freely-licensable reference recording (Xeno-canto CC0/BY are
   candidates — check each recording's own licence, they vary).
3. **Run the one-hour drift test at full duration.** Currently verified at 5 min.
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

0. **North American owls are being reported at the development station, and they now reach a
   screen in the operator's house.** — **Partially fixed by ADR-032.** Both defects
   below are fixed for every *new* detection; the ~5833 detections already in the
   database, and three consumers, are not.

   Measured 2026-08-08 on the live station: *Western Screech-Owl* at score **0.96**,
   and *Flammulated Owl* separately, both above threshold. Neither occurs in the UK.
   This is the same family of problem as the *Grey-winged Inca-Finch* records in
   §3a, but it is **not** the same cause — these are on genuine `alsa` audio, not
   synthetic, so ADR-020's filter does not hide them.

   The location filter was **on** and correctly configured
   (`OO_BIRDNET_USE_LOCATION_FILTER=true`, 51.4769/−0.0005), and **the range model
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
   - **No consumer hides a flagged historical row.** Suppression happens at the
     detector for new detections, so the API, MQTT publisher and ESP32 display are
     automatically consistent going forward — there is nothing left for any of
     them to filter. But nothing yet reads
     `native_result.plausibility_review.implausible` to stop presenting an
     already-flagged historical row (e.g. a still-unflagged Western
     Screech-Owl/Flammulated Owl) as an observation on the wall display or the API.
     This was out of scope for ADR-032's territory (`detectors/birdnet.py`,
     `config.py`, `cli.py`, metrics, docs — explicitly not the API, MQTT publisher
     or ESP32 firmware) and needs a follow-up agent.
   - **The week index passed to the range model was not re-audited.** A wrong week
     would make the priors wrong globally; ADR-032 did not investigate this, since
     the measured priors (Common Woodpigeon 0.995 etc.) already look sane for the
     season they were captured in, but it was not independently re-derived here.

   **Why this is still urgent, not resolved:** the inside observer (ADR-023) puts
   detections above 0.75 on a wall in the operator's living room, with no score
   shown. An implausible species there still reads as a plain factual claim that a
   screech-owl was in the garden, for any of the ~202+ historical rows until the
   repair CLI is run with `--apply` *and* a consumer-side follow-up ships to
   respect the flag.

4. **Reduce the AudioMoth gain.** The input still clips on loud nearby events. This
   needs the AudioMoth USB Microphone app with the switch in `USB/OFF`; the HID
   app-packet format for writing configuration is not implemented here. **Warn
   whoever does this that capture stops for the duration** — moving the switch to
   `USB/OFF` is exactly what caused the incident in §3a, and the automatic recovery
   in `hardware_recheck_s` only helps once the switch is back in `DEFAULT`. Either
   implement the HID write path (see `AudioMoth-USB-Microphone-App` for the packet
   layout) or do it by hand and record the new setting in `TARGET_DIAGNOSTICS.md`.
5. **Decide whether to promote the BatDetect2 cascade from an offline script to a
   live, queued detector.** The speed case is now made (§1a): 36 minutes of
   classifier work for a whole night's passes. `DeferredDetectorWorker` already
   exists as the mechanism (`detectors/deferred.py`, `deferred_enabled` setting);
   nothing currently registers a plugin against it. What is not settled is accuracy:
   see item 6.
6. **Review the ultrasonic detector's false-positive rate, and resolve the Myotis
   question.** On a windy or handling-noisy evening it reports "bat pass" for
   broadband transients. Offline BatDetect2 classification of the station's own
   33-36 kHz cluster leaned Myotis on 6 of 8 clips but at low confidence
   (0.20-0.30), and produced one confident contradiction — 0.77 for *Pipistrellus
   pygmaeus* on a 34 kHz call, when soprano pipistrelle actually peaks near 55 kHz.
   The hot AudioMoth gain (item 4) is a plausible confound. This needs a human
   listening to the audible renderings, not a code change; use them to tune
   `min_snr_db`, `min_pulses_per_pass` and the band as well.
7. **`oo audio window-dump`.** Milestone 2 asked for a window inspection CLI and
   only the resampler check exists.
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
    **Fully closed (ADR-041, 2026-08-09):** `deploy/deploy.sh` now runs
    `alembic upgrade head` as an explicit step before every restart, and
    `api/app.py`/`cli.py` call a new `ensure_schema_at_head()` instead of
    `create_all()`. The `create_all()`+ALTER TABLE patcher is retired from every
    production code path (`create_all()` itself survives only as a test helper).
    A fourth revision (`0004_drop_dead_detection_indexes`, ADR-037) landed since
    this item was written; the live station is confirmed at
    `0004_drop_dead_detection_indexes (head)` with 65,515 detections and 28,183
    media assets, verified against a read-only backup — `alembic upgrade head`
    against that copy is a true idempotent no-op.

### 6.3a Small, known, and unfixed

Minor items that are real but not worth a numbered slot. Recorded so they are
not rediscovered.

- **The web UI has no favicon.** No `web/public/`, no `<link rel="icon">` in
  `web/index.html`. Every page load therefore requests `/favicon.ico` and gets
  the SPA fallback or a 404 — small, but it is a request against the station on
  every load, which the charter's network-efficiency constraint does care about,
  and an unbranded tab is a poor showing for a surface the operator pins. Wants
  an icon plus the apple-touch/manifest entries, so the display's own web
  surfaces and any phone shortcut look deliberate.

  **Operator's choice: the Material Design Icons `bird` glyph.** Two things to
  get right when implementing it. First the licence: Pictogrammers' Material
  Design Icons are Apache-2.0, so redistribution is fine, but `CLAUDE.md`
  requires third-party assets to carry a separately documented licence — commit
  the attribution alongside the file, as `tests/fixtures/audio/ATTRIBUTION.md`
  does for the Xeno-canto recording. Second, do not hand-copy or reconstruct the
  path data from memory: fetch the real glyph from the upstream package and
  record its version and checksum, in the manner of `models/manifest.tsv`. An
  invented approximation of a known icon is exactly the class of plausible
  fabrication this project has been careful to avoid elsewhere.

  Ship it as an SVG favicon with an ICO or PNG fallback, plus
  `apple-touch-icon` and a small web manifest, all served from the station
  itself — nothing may be fetched from a CDN at runtime, since core surfaces
  must not require internet access.

### 6.4 Then the plan's own next milestones

Milestone 5 (ultrasonic and bat support) is complete. **Milestones 4 and 6 were
largely delivered on 2026-08-08** — this section is updated rather than removed,
because what remains of each is the useful part. `MILESTONE_STATUS.md` is the
authority; the short version:

11. **Milestone 4 — largely delivered.** Styling (ADR-027), the frontend test
    harness, `App.tsx` state extraction, operator/diagnostic disclosure
    (ADR-028), CSV/JSON export, the tiered retention backend and its UI
    (ADR-026, ADR-029) and the authentication foundation (ADR-034, closing
    ADR-015) have all landed. **Still open:** the review workflow is minimal —
    `POST`/`GET /api/v1/detections/{id}/review` plus confirm/reject in the
    drawer. The `review` table *is* now written to; correcting a misidentified
    taxon (`corrected_taxon_id`) is always written `None` and is deliberately
    left for a future ADR.
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
  exists** (ADR-035, three revisions, live station at `0003_auth_tables`), so the
  prerequisite this entry used to name is met — but it has only ever been
  exercised against SQLite, so "the DSN swap is configuration-only" stays
  unverified until someone runs it against a real PostgreSQL 16 instance.
  **Done (ADR-041, 2026-08-09):** startup now calls `ensure_schema_at_head()`
  and `deploy/deploy.sh` runs `alembic upgrade head` before every restart (see
  §6.3 item 10). PostgreSQL itself remains unexercised in this repository.
- **Redis Streams.** ADR-009's `EventBus` protocol is the seam. The bounded queues
  and drop counters already model the back-pressure a real transport imposes.
- **USB SSD: done (2026-08-08, ADR-021).** Evidence clips now live on a 465.8 GB
  SanDisk Extreme Portable SSD mounted at `data/clips`; the SD card's old clip
  directory is retained at `data/clips.sdcard-backup` pending deletion — that
  deletion is a small remaining cleanup item. The database deliberately stays on
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
  `ReadWritePaths=/home/observer/open-observatory/data`). Mounting or replugging the
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
