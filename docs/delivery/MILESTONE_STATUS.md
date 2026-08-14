# Milestone status

Honest state of the implementation against `IMPLEMENTATION_PLAN.md`. Anything not
demonstrated on the actual Pi is marked as not done, regardless of whether the code
exists.

Recorded 2026-08-04, revised 2026-08-05 to cover the waterfall spectrogram view, the
event-stream filtering default and history browsing, and **re-verified against the
code and the live station on 2026-08-09, after the whole of that day's 87 commits
were merged to `main`** (ADR-041 through ADR-053, Milestones 8 and 9). See
`HANDOVER.md` for operational context, the bugs found by measurement, and a
prioritised list of what to do next.

Every figure below is a **snapshot of one measurement**, dated where it matters.
Test counts, detection counts and station readings all move; treat a number here as
"what it read when someone looked", not as a standing property.

**This file is the authority on what is done and what is outstanding.** Measured
figures live in `docs/operations/TARGET_DIAGNOSTICS.md`; design reasoning lives in
`docs/architecture/ADRS.md`. Where another document disagrees with this one about
delivery state, this one is meant to win — tell whoever wrote the other one.

## Milestone 0 — Repository and target diagnostics — **complete**

| Deliverable | State |
|---|---|
| Python monorepo scaffolding | done (`src/open_observatory`, `web/`, `tests/`) |
| Lint/type/test commands | done (`ruff`, `mypy`, `pytest` configured in `pyproject.toml`) |
| `oo` CLI skeleton | done |
| `oo audio probe` | done, and used to produce `docs/operations/TARGET_DIAGNOSTICS.md` |
| `oo system report` | done as `oo system-report` |
| Docker Compose baseline | **deferred** — see ADR-008; native systemd used instead |
| Target diagnostic report | done |

**Exit gate — AudioMoth formats and stable device identity recorded from the actual
Pi: met.** Single profile, 384 kHz mono S16_LE, stable key
`usb-16d0:06f3:0384_2453800264933F8F`.

## Milestone 1 — Deterministic capture and replay — **complete**

| Deliverable | State |
|---|---|
| Exclusive ALSA capture service | done; addresses `hw:CARD=…`, never a card index, and refuses substituted rates |
| Capture block contract | done (`audio/contracts.py`) |
| Monotonic/UTC correlation | done (`ClockCorrelation`, `StreamClock`), anchored on the first block actually read |
| Rolling memory buffer | done, sized in seconds, with eviction and extraction-miss accounting |
| WAV fixture replay source | done, with `realtime` / `accelerated` / `step` modes and gap injection |
| Gap/overrun detection | done; distinguishes crystal offset from lost audio by looking for a *step* |
| Audio-level metrics | done, per block and per second, labelled uncalibrated |
| Prometheus health endpoint | done (`/metrics`, `/api/v1/health`) |

**Exit gate — no timestamp drift or unexplained gaps over an hour: still NOT met at
full duration, but much better evidenced than it was.** Resampler timing was verified
over 5 minutes of generated audio (zero group delay, bounded delivery deficit, no
trend). Since then, ADR-046's work sampled the live station every 2 s across a
**42.7-minute restart-free run**, and showed the frame deficit growing as a straight
line at the crystal's own rate (+51.17 and +51.00 ppm in two clean windows 32 minutes
apart, against an independently fitted `rate_offset_ppm` asymptote of −50.43) with no
step anywhere larger than 0.5 ms. That is strong evidence against a *continuous* drift
or leak.

**It is still not the one-hour run, and the distinction is not pedantry.** The longest
*clean* window in that run was 22.2 minutes; a loss mechanism with a period longer than
that — an hourly sweep, a nightly rotation, a thermal cycle — would not have appeared.
ADR-046 says so itself. The full-hour drift run and the 72-hour soak both remain
outstanding.

## Milestone 2 — Derivation, windows and job transport — **complete**

| Deliverable | State |
|---|---|
| 48 kHz resampling | done, libsoxr `HQ`, measured zero group delay |
| Source-frame mapping | done and tested — output frame *n* ↔ native frame *8n* |
| Window specification and segmenter | done; one segmenter per distinct `WindowSpec`, shared between detectors |
| Transient asset lease manager | done, with sweep and leak accounting |
| Redis Streams job contract | **deferred** — in-process `EventBus` behind the same protocol, per ADR-009 and the explicit permission in `CLAUDE.md` |
| Bounded queue policies | done; per-detector bounded queues, delivery deadline, circuit breaker |
| Window inspection CLI | done — `oo audio resample-check` inspects resampler timing and `oo audio window-dump` (added 2026-08-09) dumps a specific segmenter window: actual frame bounds, actual sample count (cross-checked against an independent `RingBuffer` read, not just restated `WindowSpec` arithmetic), UTC and local-time rendering, and gap-injection to show how a discontinuity shows up in the segmenter's own frame accounting |
| Live spectrogram transport | done, beyond plan — two channels, binary framing, verified from a real browser at 1246 columns / 29.9 s of audio per 30 s wall with zero gaps or overlaps. Recorded as ADR-012 |
| Spectrogram presentation | done, beyond plan — scrolling and waterfall orientations, with the shared coordinate mapping in `web/src/components/geometry.ts` tested against both |
| Event stream filtering | done — unidentified events are hidden by default, so the stream shows what was identified rather than everything the activity detector fired on |

**Exit gate — synthetic impulse timing error under 100 ms, target under 10 ms:
met, at 0 ms.** An impulse at native frame *N* appears at derived frame *N/8* to
within one frame (20 µs), verified by test.

## Milestone 3 — Bird detector vertical slice — **complete**

| Deliverable | State |
|---|---|
| BirdNET adapter | done, GLOBAL 6K V2.4 via `ai-edge-litert`, ~40× realtime on this Pi |
| Documented model acquisition path | done — `oo models fetch`, checksummed manifest, licences shown before download |
| Detector fixture self-test | done for `activity-v1` and `ultrasonic-pass-v1`; BirdNET now also has one, `tests/test_birdnet_fixture.py` — see the exit-gate note below |
| Normalised detection persistence | done |
| Evidence clip manager | done, with a rate limit, size budget, disk reserve and retention sweep |
| Minimal FastAPI list/detail endpoints | done, plus health, metrics, devices, streams, gaps, detectors, models, taxa activity, media, history and history windows |
| Temporary UI | done — the real-time debug UI, with a LIVE and a HISTORY mode |
| History browsing | done, beyond plan — named windows resolved in the station's timezone, SQL-aggregated timeline and species summary, shown against capture coverage so an empty stretch is distinguishable from a quiet one. No historical spectrogram: the ring buffer is memory-only by design |
| Audible rendering of ultrasound | done, beyond plan — time-expansion and heterodyne derivatives, verified on live bats at 18-54 kHz |
| Low-latency live listening | done, beyond plan — ~180 ms end to end |
| BirdNET's sound categories are not species | done (ADR-049) — the eleven non-biological labels (Engine, Human vocal, Dog…) no longer get a species rank, a fabricated `sci:engine` taxon id, or a plausibility floor the range model cannot speak to. A dry run against the live database found ADR-032's floor was about to withdraw 91 *correct* detections; the exemption took the findings from 114 to 23 at the default limit. `oo detections reconcile-taxonomy` repairs the 247 stored rows without deleting any |
| Human speech is not stored as evidence | done (ADR-049) — `clip_human_audio` defaults **off**, so no new clip is written for a human-sound detection; `oo clips purge-human-audio` removes the 48 existing assets (125 MB, 24 detections) and keeps the detection rows. This is the charter's privacy constraint made operational rather than promised. Neither command has been run with `--apply` on the live station |

**Exit gate — known bird fixture produces expected candidate label and an aligned
playable clip: MET, on the target Pi 5 (aarch64), 2026-08-08.** `tests/test_birdnet_fixture.py`, added 2026-08-08, is
the repeatable test the gate asks for. It uses a committed reference recording —
`tests/fixtures/audio/erithacus_rubecula_XC441752.mp3`, a European Robin song from
Xeno-canto (XC441752, recordist Jan Cibulka), individually licence-checked (CC BY-SA
4.0, not the NC-SA terms many Xeno-canto recordings carry) and committed with its
required attribution in `tests/fixtures/audio/ATTRIBUTION.md` and a checksummed
`manifest.tsv` in the same shape as `models/manifest.tsv` (ADR-006). Unlike the
BirdNET model assets, this is a short third-party recording whose own licence
explicitly permits redistribution, so it is committed directly rather than fetched
on demand.

The test asserts both halves of the gate against the real, shipped model and range
model, with the plausibility filtering from ADR-032 switched on and the neutral
Greenwich reference coordinates (see the test module docstring): (1) "European Robin" / *Erithacus rubecula* appears among the
candidates, with a real, in-range plausibility band, not a suppressed or
strictly-gated one; (2) an evidence clip is written, is readable as 48 kHz audio, has
a sane duration, and — checked at the frame level, not just by overlap — its samples
match the source recording exactly at the clip's own recorded frame bounds (to within
16-bit PCM quantisation). It skips cleanly, exactly like the existing BatDetect2 tests,
when the (unbundled) BirdNET model assets or a TFLite runtime are absent.

**Target-device run: done.** Run on the Pi 5 itself after the 2026-08-08 deploy,
against the station's own fetched BirdNET assets and `ai-edge-litert`:

```
tests/test_birdnet_fixture.py::TestBirdNetKnownRecording
  test_the_fixed_date_and_location_keep_the_species_plausible   PASSED
  test_known_recording_produces_the_expected_candidate_label    PASSED
  test_evidence_clip_is_playable_and_aligned_to_the_call        PASSED
3 passed in 6.83s
```

CLAUDE.md's rule that a detector is only "supported" once its fixture test passes on
target architecture is therefore satisfied for `birdnet-v2.4`. The test was written
on x86_64 and run here unchanged; nothing in it is architecture-specific, which is
the point of committing the recording rather than depending on live audio.

Note the test deliberately uses a denser analysis stride (0.25 s) than production
(`birdnet_window_stride_s`, 1.5 s): a 6.68 s clip can otherwise have the call fall
entirely between two windows, which would make the assertion flaky rather than
wrong.

## Milestone 4 — Product dashboard and review — **delivered; review workflow closed 2026-08-09 (ADR-043)**

Reassessed 2026-08-05. ADR-016 supersedes the part of ADR-011 that treated the debug
UI as a surface to be replaced: this milestone **promotes** it instead. The plan's own
exit gate asks that a user can operate *and diagnose* through one local UI, which is a
single surface with two depths.

| Deliverable | State |
|---|---|
| React dashboard | foundation done — application shell and component set; surface-agnostic WebSocket and audio clients |
| Styling | done (ADR-027) — spacing and type scales applied to new surfaces; ~700 lines of pre-existing component CSS deliberately not migrated, recorded as scoped-out |
| Frontend test harness | done — `@testing-library/react` + `jest-dom` + `user-event`, all exact-pinned. **235 frontend tests in 22 files**, measured on merged `main` 2026-08-09, up from 1 file with only pure functions testable |
| Timeline, filters, detail | foundation done — HISTORY mode: named windows, stacked timeline, species summary, click-to-focus, coverage bar |
| Spectrogram and playback | done — two orientations, live listening, per-detection clips including audible ultrasound |
| Health/system page | done (ADR-028) — `OperatorSummary` gives plain-language listening/storage/detection cards; the diagnostic depth is still there behind the toggle |
| Operator/diagnostic disclosure | done (ADR-028) — one depth toggle via `?view=operate\|diagnose`, not a second route |
| `App.tsx` state extraction | done — decomposed into `web/src/hooks/*` and `web/src/state/*`; `useLiveAudio.test.tsx` guards the ADR-022 retune fix from regressing |
| Review workflow | done (ADR-043) — confirm, reject, **correct** and hold. A correction is a new `Review` row, never an edit: the detection's own claim columns are untouched, so the original stays visible and attributable. Human review outranks machine refinement in code (`plausibility_repair` skips any human-reviewed detection at both find and apply time), and a held review exempts evidence from the age tiers though **not** from the disk watermark. Two stated limits: a correction can only name a taxon the station has itself identified before, and `/api/v1/history` still aggregates on the original taxonomy. **In real use, not merely built** — the live station's `review` table held 65 rows on 2026-08-09 |
| Retention job and UI | done — tiered age-out backend (ADR-026) and `RetentionPanel`, reconciled against the real `GET /api/v1/retention/status` after the two were built in parallel against different assumptions |
| CSV/JSON export | done — `GET /api/v1/detections/export`, registered before `/detections/{id}` so it is not swallowed by the path parameter |
| Authentication foundation | done (ADR-034) — Argon2id, sessions, revocable API tokens, rate-limited login. **Off by default**, with a configurable public-read allow-list so the ESP32 counter-top display keeps working |
| Inside-observer push channel | done (ADR-038) — `GET /api/v1/display`, a detections-only WebSocket. 49 B a detection against the polled transport's ~127 kB/20 s; deployed to the Pi and flashed to the board on 2026-08-09. Elapsed times ("4s ago") ticking once a second, partial repaints only. HTTP polling retained and exercised as the fallback. Verified live 2026-08-09 21:46Z: one client connected, 2,784 frames sent, 0 dropped, mean 42.5 B a frame |
| Spectrogram playhead marker | done (ADR-051) — the spectrogram says *where* the sound being played back is, as a measured interval rather than a line. Frontend only; the offset was measured against a ground-truth rig rather than assumed |
| BirdNET near-miss ledger | done (ADR-052) — `GET /api/v1/detectors/near-misses` records what BirdNET proposed and refused, with per-band score histograms, so a threshold can be tuned on evidence instead of on a bare counter. A bounded in-memory ring at ~2 µs per candidate; **persists nothing**, by design |
| Every setting web-editable | done (ADR-048) — `GET`/`PUT /api/v1/settings` and `GET /api/v1/setup`, in three declared tiers (live, restart, excluded) with the exclusions named. The service writes its own `config/runtime.env`. Confirmed working on the live station, which now carries an `OO_BIRDNET_THRESHOLD_IN_RANGE` line written by the settings page |

## Milestone 4.5 — Close the Milestone 1–3 exit gates — **fixture and window-dump gates closed; soak and drift run outstanding**

Unfinished gates rather than new scope: the 72-hour soak, a committed species fixture
test, the full-hour drift run, and `oo audio window-dump`. A soak and a deploy are
mutually exclusive, because deploying restarts capture and voids the run.

**Committed species fixture test: done, and passing on the target Pi 5**
(`tests/test_birdnet_fixture.py`; 3 passed in 6.83 s on aarch64, 2026-08-08). See the
Milestone 3 exit-gate note above for what it asserts and its provenance.

**`oo audio window-dump`: done (2026-08-09).** It runs the real `StreamClock`,
`AudibleResampler`, `StreamSegmenter` and `RingBuffer` classes over a replayed WAV
file or a synthetic scene — never against the live station, since the native ring
buffer is in-process memory owned by whichever process holds the microphone, and
this command deliberately does not attach to a running station to avoid perturbing
capture. Every window it reports carries its actual frame bounds and actual sample
count (the array's own shape, not `WindowSpec` arithmetic restated), independently
cross-checked against a second `RingBuffer` read of the same frames so a segmenter
bug and a ring bug would both have to agree to go unnoticed. `--gap-at-s` injects a
capture gap so the segmenter's real reaction (dropping its buffered tail across a
discontinuity) is directly observable in the reported frame numbers, not asserted.
See `tests/test_cli_audio.py` (9 tests) and `docs/development/SETUP.md`.

Still outstanding in this milestone:

* **The 72-hour soak. Run 2026-08-10 to 2026-08-13, and it FAILED its own
  continuity criterion.** The run was restart-free for the whole window, which is
  itself a first, but continuity over the exact 72 hours was **99.865%** against a
  criterion of ≥ 99.9% — 349.3 s of audio lost out of 259,200 s. Reconstructed
  from the `capture_gap` table, and cross-checked against the live
  `continuity_ratio` counter over an identical window (184.4 s vs 175.0 s, within
  5%), so the two independent instruments agree and the loss is real. Eleven hours
  after the window closed the stream wedged completely — see `HANDOVER.md` §1e,
  which also explains why the loss rate stepped up mid-run. **A re-run is needed
  after the §1e defect is fixed**, and it is still true that a soak and a deploy
  are mutually exclusive.
* **The one-hour drift run.** Not run at full duration. The best evidence to date is
  ADR-046's 42.7-minute restart-free sampling run, whose longest *clean* segment was
  22.2 minutes — see the Milestone 1 exit-gate note above for why that is not the same
  thing. The method now takes 15 minutes and about 150 lines and is written down, so
  this is cheap; what it needs is a stretch with nobody deploying.

This milestone's own exit gate ("the acceptance criteria for capture continuity
pass over 72 continuous hours, and a detector fixture test passes in CI on the
target architecture") is still **not met** — closing the window-dump line item
does not close the gate, since the 72-hour soak is unaffected by it.

## Milestone 5 — Ultrasonic and bat support — **complete**

Brought forward because the device turned out to capture at 384 kHz, which makes
the native stream genuinely useful rather than theoretical. **Exit gate met
2026-08-05**: a known bat fixture is processed on the target device
(`tests/test_batdetect2.py`) and capture continuity was unaffected (0.999318,
0 overruns). The bat adapter is deliberately *not* built: the benchmark measured
BatDetect2 at 0.52x realtime, below the threshold, and ADR-017 records that.

> ⚠️ **The provenance half of this gate is not actually satisfied. Checked
> 2026-08-09.** This entry claimed "provenance is retained in
> `results/batdetect2-pi5.json`". That file does not exist in the working tree, in
> git history, or on the live station, and `results/` is not gitignored — it was
> never committed. The measured figures are retained (see
> `docs/detectors/BATDETECT2_EVALUATION.md`) but cannot currently be traced to an
> artefact, and the plan's exit gate for this milestone asks explicitly for
> "provenance retained". **Re-run
> `scripts/benchmark_batdetect2.py --json results/batdetect2-pi5.json` on the Pi
> and commit the output** to close it properly. This does not affect the other
> half of the gate: `tests/test_batdetect2.py` is committed and skips cleanly when
> the unbundled assets are absent.

| Deliverable | State |
|---|---|
| Native high-rate window profile | done |
| Ultrasonic detection | done, but as a **pulse-train detector, not a classifier** — `ultrasonic-pass-v1`, no species claim |
| BatDetect2 evaluation harness | **done** — `scripts/benchmark_batdetect2.py`, `tests/test_batdetect2.py`, `docs/detectors/BATDETECT2_EVALUATION.md`. **The `results/batdetect2-pi5.json` this row used to cite does not exist** — see the provenance warning above |
| Pi 5 benchmark report | **done** — BatDetect2 measured on the target: p95 968 ms per 0.5 s clip, **0.52× realtime in isolation**, +459 MB RSS. Against BirdNET at ~40× and the pass detector at ~36–40×. Verdict: not sustainable for real-time inference |
| Detector configuration | **done** — every ultrasonic threshold, the band, the buzz parameters and the schedule are wired to `Settings`, with defaults equal to the previous constructor defaults so behaviour was unchanged until set |
| Night scheduler | **done** — civil dusk to civil dawn plus configurable margins, from the NOAA solar formulas with no new dependency. Verified on the Pi 2026-08-05: dusk 20:27Z, dawn 03:55Z, active at 21:45 local, inactive at noon. The gate returns before any FFT work, so the CPU saving is real. With coordinates unset it runs continuously and reports why, rather than silently detecting nothing |
| Deferred mode | **`DeferredDetectorWorker` exists and is still unused** (`detectors/deferred.py`); it remains the right mechanism for a *live* detector too slow to run inline, and the wrong one for stored clips, since it drops anything older than `max_delivery_latency_s`. **Decided 2026-08-09 (ADR-045):** the cascade ships instead as a **separate process** on a systemd timer — `oo refine run`, `src/open_observatory/refinement/` — at 01:00 UTC, fenced to `AllowedCPUs=2-3` / `Nice=19` / `MemoryMax=1G` (all three verified on the target under systemd 255). Cost basis unchanged: 2.1 s of inference per pass, ~36 minutes for the 1015 passes of a full night (ADR-017's 2026-08-05 update) |
| Species refinement of stored bat passes | **done as *proposals only*, deliberately** (ADR-045). The runner writes append-only `refinement` rows and stamps each event with `refined_at` / `refinement_version` / `refinement_outcome`; it never edits a detection's species, score or `native_result`, and the writer raises if any claim column moves. It may not apply a change automatically, because BatDetect2 returned **0.77 for *Pipistrellus pygmaeus* on a call this station measured at 34 kHz** (soprano pipistrelle peaks near 55 kHz) and leaned *Myotis* on 6 of 8 clips at only 0.20–0.30. Nothing it writes reaches the API, MQTT, the web UI or the counter-top display. **Not yet run against the live station** |
| Feeding-buzz flagging | **done** — a run of short inter-pulse intervals that is also well below the train's own median, which is what distinguishes a terminal collapse from a bat calling fast throughout. `min_interval_ms` is emitted on every pass so a wrong threshold can be re-judged from stored data |
| Frequency-band candidate titles | **done** — presentational only; the stored record keeps `label = "bat pass"` and no species name, and the normaliser's guard is asserted by test |
| Sub-bin peak frequency | **done** — the pulse FFT has 3 kHz bins at 384 kHz and the candidate band edges fall between bin centres, so peaks were being assigned to a species group by quantisation. Parabolic interpolation fixes it; live, the station's 35–36 kHz cluster survives as a genuine 35.3–36.2 kHz signal |
| Native-rate evidence + audible playback derivative | done |
| Ultrasound rendered for human review | done — time expansion (frequencies divide, everything preserved) and heterodyne (real time preserved, band-limited), each labelled with what it changed and that its levels are not comparable with the native recording |
| Ultrasonic spectrogram coverage | fixed — the hop (9216 frames) exceeded the FFT (4096), so 55% of the audio was never inspected and a 4 ms pulse could fall entirely between columns. Columns now max-combine four sub-windows across the whole hop. |


### What tonight's work changed, measured on the station

| Finding | Evidence |
|---|---|
| Peak frequencies were quantised to 3 kHz | Every reported peak was a multiple of 3000 Hz. Sub-bin interpolation now yields 35286.6, 35841.5, 36225.0, 53520.4 Hz where before it reported 36000.0 exactly |
| Single calls were counted as whole passes | A "pass" of 8 pulses spanning 28 ms with a 3.8 ms median interval is one fragmented call, not eight calls. Real search-phase spacing measured on the same station is 120 ms. Fragments now merge below 2 ms onset spacing, measured onset to onset so a feeding buzz is never merged away |
| Live ultrasonic monitoring works and is selective | Heterodyne RMS falls from −61.6 dBFS tuned to 25 kHz to −77.3 dBFS at 120 kHz, so it band-limits rather than passing broadband noise. Both live channels verified over a real WebSocket, 40 chunks each, no stalling |
| Evidence writing was stalling the detector that produced it | `ultrasonic-pass-v1` analysed 29 windows and dropped 69 with a 42 s lag, while its own inference p95 was 57 ms. Clip extraction was awaited inline in the detector's task. Now a bounded queue off that path: 76 analysed, 0 dropped, lag 0.14 s |
| Clip I/O was overrunning the capture ring | Once the detector kept up, evidence volume tripled and SD-card writes delayed the ALSA read through the shared default thread pool — 11 gaps, 8 overruns, continuity 0.997. Evidence and retention now have a dedicated executor; final state 0.999318 with 0 overruns and 0 gaps |
| Capture is unaffected | Continuity 0.999318, 0 overruns, measured after every change tonight |

**Still open, and not resolvable by code:** whether the 33–36 kHz cluster this station
reports is genuinely Myotis. The frequencies are consistent and survive the
interpolation fix, so they are not an artefact, but distinguishing a Myotis from a
mislabelled pipistrelle needs the audible renderings reviewed by ear. That is what
the false-positive review deliverable is for, and it needs a human.

## Milestone 6 — MQTT and Home Assistant — **publisher live on the operator's broker; alert engine not built**

**Updated 2026-08-08, later the same day:** deployed and running against the
operator's real Home Assistant broker on the LAN. Six entities appear
under one device named after the configured station: last detection, species today,
bat passes tonight, bat activity, station healthy, and a `detection` event
entity for automations. Measured live: connected first attempt, zero publish
failures, zero drops.

One fix after deployment, found by checking the counter against reality rather
than trusting a green test: unidentified "acoustic event" detections were being
forwarded to Home Assistant despite the filter written to stop them, because
`activity-v1` sets `taxonomic_group="acoustic_event"` — a truthy sentinel — and
the check treated any non-null group as an identification. Now filtered
(`mqtt_publish_unidentified`, default false) and verified on the live station at
6 suppressed against 6 unidentified detections, exactly matching. Bat passes are
explicitly exempt and asserted by test: a pass claims something positive
happened even though it names no species.

The original off-target assessment follows, for the record.


Built and unit/integration tested off-target (see below): the MQTT publisher
(`src/open_observatory/mqtt/`), Home Assistant MQTT Discovery, and the
`schema_version` 1.1 fix to `schemas/detection-event.schema.json` that closes
HANDOVER.md section 6.3 item 9 (see ADR-025). Off by default
(`mqtt_enabled=false`); no operator credentials exist anywhere in this
repository. Verified against a locally-run mosquitto container
(`tests/test_mqtt_integration.py`) and 51 other unit tests with fake/mocked
clients (`tests/test_mqtt_*.py`) — **not** against the operator's actual Home
Assistant broker, which this agent was deliberately not given the address or
credentials for, and **not** deployed to the Pi. `docs/operations/HOME_ASSISTANT.md`
lists exactly what the operator needs to provide (broker host, port,
auth/TLS) to go live.

**Not implemented**, as scoped by `IMPLEMENTATION_PLAN.md`'s Milestone 6 entry:
environmental telemetry ingestion, the alert rule engine with repetition and
cooldown, and HMAC-signed outgoing webhooks. Exit gate ("Home Assistant shows
station health and receives a test detection/alert") is therefore only
partly met, and only off-target: the publisher and discovery half is done and
tested; the operator has not yet pointed a real Home Assistant instance at it,
and no alert has been sent because the alert engine does not exist yet.

## Milestone 7 — MCP, export and hardening — **not started**

Partial credit only: the systemd unit applies privilege reduction
(`NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome=read-only`, a memory cap and
an explicit writable path), model licences are surfaced through
`/api/v1/models` and in the UI, and media paths are validated against the clip
directory before being served.

## Milestone 8 — Distribution: somebody else's station — **one of six deliverables done; the rest not started, and correctly last**

Added 2026-08-09 at the operator's direction. A prebuilt Raspberry Pi image
published as a GitHub release, first-boot provisioning with no keyboard or SSH,
remote update of the station after deployment, and over-the-air update of the
counter-top display triggered from the Pi so it never has to be unplugged. Full
scope and exit gate in `IMPLEMENTATION_PLAN.md`.

**Done: over-the-air update of the counter-top display (ADR-050).** Two OTA app
slots, an image served by the station, SHA-256 verified before anything becomes
bootable, and a rollback the display owns. **Flashed and verified on real
hardware on 2026-08-09**, including a deliberate rollback drill; the station
reported the display at firmware `0.2.4` and up to date. The drill earned its
keep — it found that the whole rollback mechanism was unreachable code, because
`arduino-esp32` marks a pending image valid before `setup()` runs. See ADR-050's
"Verified on hardware" section.

Still not started: the prebuilt image, first-boot network provisioning, remote
update of the *station*, backup and restore of a station's identity and history,
and the signed release process.

Two pieces of groundwork already exist and were not built for this:

- **ADR-047** made site parameters runtime state rather than committed defaults,
  which is the precondition for one image serving every site. **ADR-048
  completed it** by putting the whole of `Settings` in the browser, in three
  declared tiers.
- **ADR-038's push channel** means the station already knows the display's
  address and already talks to it. That is the transport ADR-050's OTA trigger
  actually used.

What makes this genuinely hard, and why it must not be started early: **an
update mechanism that cannot roll back is worse than none**, and capture may not
pause for a release. Charter item 1 does not have a maintenance window.

## Milestone 9 — Nice to have, once the core is settled — **not started, by design**

A home for enhancements that are wanted and not urgent, so they stop being ideas
and start being decisions. Nothing here blocks an exit gate.

Three entries.

**Watching the model assets for updates**, raised 2026-08-10. Dependabot covers
pip, npm, Docker and Actions; it cannot see the BirdNET and BatDetect2 models,
which come from `oo models fetch` against a checksummed manifest rather than a
package index. A scheduled comparison against upstream could open an issue on
drift. An issue rather than a pull request, because a model change alters what
the station reports and needs the fixture test re-run on target first.

**Environmental sensors: lux and rain**, raised 2026-08-10. Both would make the
acoustic record easier to interpret — measured light rather than an almanac
calculation, and a rain flag to explain a noisy hour. Undecided how to attach
them: Pi GPIO, the ESP32 display, or ingestion from Home Assistant under
Milestone 6's environmental telemetry. The last is cheapest but least
self-contained, and the charter requires the station to work without Home
Assistant present.

**Taxonomic grouping above species (ADR-053)**, raised
2026-08-09. The detector exposes nothing between a species binomial and
"bird" — the label file has no hierarchy and `taxonomic_group` is a claim-kind
marker, not a rank. Genus grouping is free and exact; family is a real data
dependency and the honest version needs a licensed, checksummed taxonomy.
ADR-053 records why the hardcoded corvid list is refused.

## Quality gates

| Gate | State |
|---|---|
| Unit tests | **895 passing, 8 skipped** with `--deselect tests/test_api.py::TestLiveChannels`, plus **296 frontend tests**, measured on merged `main` 2026-08-10. This number moves as work lands; SETUP.md carries the current expectation and this row is a snapshot of one run |
| Lint / types | `ruff check .` clean, re-run 2026-08-09 on merged `main`. **`mypy src` is not clean and never has been** — **22 errors in 11 files** across 59 source files, re-measured on merged `main` 2026-08-09 (an earlier figure of "29 in 12" is stale). All pre-existing; the ADR-041–053 work added none. Judge a change by whether it adds errors |
| Integration tests | done — `tests/test_api.py` drives the real app and real pipeline end to end. The history-endpoint gap recorded here is **closed**: `tests/test_history.py::TestHistoryHTTP` now exercises `/api/v1/history` and `/api/v1/history/windows` through a real app (ADR-024) |
| Target-device smoke test | `oo audio probe`, `oo audio test-capture`, `oo audio resample-check`, `./deploy/deploy.sh` health wait |
| Rollback note | `deploy/deploy.sh` is idempotent; `sudo systemctl stop open-observatory` halts cleanly. ADR-007 records how to move to PostgreSQL and back |
| Updated docs | done |
| Measured CPU, memory, dropped audio | done — `TARGET_DIAGNOSTICS.md` |

## Verified from a real client, not just from the server

The live channels are now measured **in a browser over Wi-Fi**, not only from a
probe on the Pi. That distinction found the worst bug in the project: concurrent
writers to one WebSocket, which produced a flawless result on loopback (sends
complete too fast to overlap) and near-total failure over Wi-Fi (the socket was
evicted from the fan-out after a send error, so the spectrogram died while JSON
kept flowing). Any future work on these channels must be measured the same way.

## What must not be claimed yet

Per `CLAUDE.md`, this system is **not complete**. Outstanding before that word
applies, corrected 2026-08-09 — four items on the earlier version of this list
(a committed species fixture test, authentication, the Milestone 4 dashboard and
`oo audio window-dump`) have since been delivered and are struck rather than
deleted, so the record of what was outstanding when survives:

- **the 72-hour soak test** — the single biggest item, never run;
- **the one-hour drift run at full duration** — best evidence is a 42.7-minute
  run whose longest clean window was 22.2 minutes (ADR-046);
- **Milestone 6's alert engine**, environmental telemetry ingestion and HMAC
  webhooks;
- **Milestone 7 entirely** — MCP tools, export bundles, backup/restore, setup
  wizard, vulnerability scan;
- **what remains of the capture-gap investigation** — see
  `OPEN_INVESTIGATION_CAPTURE_GAPS.md`. ~~The deficit-step estimator over-credits,
  so `capture.gap lost_audio=True` means "the read was late".~~ **That is fixed,
  deployed and confirmed on target (ADR-039), and the residual deficit was settled
  against the crystal (ADR-046).** What is still open there is narrower: no clean
  window longer than 22 minutes, hypothesis 4 never isolated, the missing gap row
  of 2026-08-08 10:55:24Z, and `late_read_max_frames` trending upward;
- **the historical BirdNET rows written under the pre-ADR-032 plausibility
  logic.** The consumer side is now done (ADR-044) and the sound-category
  exemption is in (ADR-049), but **none of the three repair commands has been run
  with `--apply` against the live station**, so those rows are still presented —
  see `HANDOVER.md` §6.3 item 0;
- ~~a committed fixture test proving a known species from a known recording~~ —
  done 2026-08-08, `tests/test_birdnet_fixture.py`, passing on the target;
- ~~authentication~~ — done 2026-08-08, ADR-034, **off by default**;
- ~~`oo audio window-dump`~~ — done 2026-08-09, see the Milestone 2 and
  Milestone 4.5 sections above;
- ~~the Milestone 4 product dashboard~~ — largely delivered 2026-08-08; the
  review workflow remains minimal.

And the standing honesty rules, which no amount of delivery relaxes: no detector
"identifies" anything it does not itself claim; `ultrasonic-pass-v1` detects
passes, not species; a score is not a probability; levels are uncalibrated dBFS,
never SPL; and the species list this station reports is *not* filtered to what
actually occurs here.
