# Milestone status

Honest state of the implementation against `IMPLEMENTATION_PLAN.md`. Anything not
demonstrated on the actual Pi is marked as not done, regardless of whether the code
exists.

Recorded 2026-08-04, revised 2026-08-05 to cover the waterfall spectrogram view, the
event-stream filtering default and history browsing. See `HANDOVER.md` for operational
context, the bugs found by measurement, and a prioritised list of what to do next.

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

**Exit gate — no timestamp drift or unexplained gaps over an hour: met in
substance, at 5 minutes rather than 60.** Resampler timing verified over 5 minutes
of generated audio (zero group delay, bounded delivery deficit, no trend) and live
capture ran with 0 gaps and 0 overruns at continuity 0.999. A full hour has not been
run; the 72-hour soak remains outstanding.

## Milestone 2 — Derivation, windows and job transport — **complete**

| Deliverable | State |
|---|---|
| 48 kHz resampling | done, libsoxr `HQ`, measured zero group delay |
| Source-frame mapping | done and tested — output frame *n* ↔ native frame *8n* |
| Window specification and segmenter | done; one segmenter per distinct `WindowSpec`, shared between detectors |
| Transient asset lease manager | done, with sweep and leak accounting |
| Redis Streams job contract | **deferred** — in-process `EventBus` behind the same protocol, per ADR-009 and the explicit permission in `CLAUDE.md` |
| Bounded queue policies | done; per-detector bounded queues, delivery deadline, circuit breaker |
| Window inspection CLI | partial — `oo audio resample-check` inspects timing; there is no window dump command |
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
| Detector fixture self-test | done for `activity-v1` and `ultrasonic-pass-v1`; BirdNET's *logic* is tested but there is **no fixture test asserting a known species from a known recording** |
| Normalised detection persistence | done |
| Evidence clip manager | done, with a rate limit, size budget, disk reserve and retention sweep |
| Minimal FastAPI list/detail endpoints | done, plus health, metrics, devices, streams, gaps, detectors, models, taxa activity, media, history and history windows |
| Temporary UI | done — the real-time debug UI, with a LIVE and a HISTORY mode |
| History browsing | done, beyond plan — named windows resolved in the station's timezone, SQL-aggregated timeline and species summary, shown against capture coverage so an empty stretch is distinguishable from a quiet one. No historical spectrogram: the ring buffer is memory-only by design |
| Audible rendering of ultrasound | done, beyond plan — time-expansion and heterodyne derivatives, verified on live bats at 18-54 kHz |
| Low-latency live listening | done, beyond plan — ~180 ms end to end |

**Exit gate — known bird fixture produces expected candidate label and an aligned
playable clip: partially met.** BirdNET produced real identifications on live audio
within minutes (including *Columba palumbus*) with playable, aligned clips. That is
a live demonstration, not the repeatable fixture test the gate asks for, because no
licensed reference recording is committed to the repository.

## Milestone 4 — Product dashboard and review — **foundation in place, not started**

Reassessed 2026-08-05. ADR-016 supersedes the part of ADR-011 that treated the debug
UI as a surface to be replaced: this milestone **promotes** it instead. The plan's own
exit gate asks that a user can operate *and diagnose* through one local UI, which is a
single surface with two depths.

| Deliverable | State |
|---|---|
| React dashboard | foundation done — application shell and component set; surface-agnostic WebSocket and audio clients |
| Styling | **not foundation** — `styles.css` is a colour-token header over ad-hoc component CSS, no spacing or type scale |
| Frontend test harness | **absent** — no component testing library installed; only pure functions are testable, which is why `geometry.test.ts` stands alone |
| Timeline, filters, detail | foundation done — HISTORY mode: named windows, stacked timeline, species summary, click-to-focus, coverage bar |
| Spectrogram and playback | done — two orientations, live listening, per-detection clips including audible ultrasound |
| Health/system page | partial — the diagnostic half exists; no operator-facing view |
| Operator/diagnostic disclosure | **not started** |
| `App.tsx` state extraction | **not started** — around 25 `useState` hooks in one 425-line component; a prerequisite, not a tidy-up |
| Review workflow | **not started** — no table writer, no endpoint, no UI |
| Retention job and UI | **not started** |
| CSV/JSON export | **not started** — required by the acceptance criteria, absent from the original plan |
| Authentication foundation | **not started** — ADR-015 records the deviation this must close |

## Milestone 4.5 — Close the Milestone 1–3 exit gates — **not started**

Unfinished gates rather than new scope: the 72-hour soak, a committed species fixture
test, the full-hour drift run, and `oo audio window-dump`. A soak and a deploy are
mutually exclusive, because deploying restarts capture and voids the run.

## Milestone 5 — Ultrasonic and bat support — **complete**

Brought forward because the device turned out to capture at 384 kHz, which makes
the native stream genuinely useful rather than theoretical. **Exit gate met
2026-08-05**: a known bat fixture is processed on the target device
(`tests/test_batdetect2.py`), provenance is retained in
`results/batdetect2-pi5.json`, and capture continuity was unaffected (0.999318,
0 overruns). The bat adapter is deliberately *not* built: the benchmark measured
BatDetect2 at 0.52x realtime, below the threshold, and ADR-017 records that.

| Deliverable | State |
|---|---|
| Native high-rate window profile | done |
| Ultrasonic detection | done, but as a **pulse-train detector, not a classifier** — `ultrasonic-pass-v1`, no species claim |
| BatDetect2 evaluation harness | **done** — `scripts/benchmark_batdetect2.py`, `tests/test_batdetect2.py`, `docs/detectors/BATDETECT2_EVALUATION.md`, results in `results/batdetect2-pi5.json` |
| Pi 5 benchmark report | **done** — BatDetect2 measured on the target: p95 968 ms per 0.5 s clip, **0.52× realtime in isolation**, +459 MB RSS. Against BirdNET at ~40× and the pass detector at ~36–40×. Verdict: not sustainable for real-time inference |
| Detector configuration | **done** — every ultrasonic threshold, the band, the buzz parameters and the schedule are wired to `Settings`, with defaults equal to the previous constructor defaults so behaviour was unchanged until set |
| Night scheduler | **done** — civil dusk to civil dawn plus configurable margins, from the NOAA solar formulas with no new dependency. Verified on the Pi 2026-08-05: dusk 20:27Z, dawn 03:55Z, active at 21:45 local, inactive at noon. The gate returns before any FFT work, so the CPU saving is real. With coordinates unset it runs continuously and reports why, rather than silently detecting nothing |
| Deferred mode | **the generic `DeferredDetectorWorker` mechanism exists** (`detectors/deferred.py`) but no BatDetect2 plugin is registered against it. The cascade it would enable has instead been proven **offline**: `scripts/classify_clips_batdetect2.py` runs BatDetect2 against clips `ultrasonic-pass-v1` already flagged, and measured on this station's own clips (trimmed to 1.5 s centred on the pass) it costs 2.1 s of inference per pass — about 36 minutes of classifier work for the 1015 passes of a full night. See ADR-017's 2026-08-05 update. Whether to wire this into the live pipeline as a registered plugin, versus leaving it a manual/offline tool, is not decided |
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

## Milestone 6 — MQTT and Home Assistant — **partially implemented, not verified on target**

Built and unit/integration tested off-target (see below): the MQTT publisher
(`src/open_observatory/mqtt/`), Home Assistant MQTT Discovery, and the
`schema_version` 1.1 fix to `schemas/detection-event.schema.json` that closes
HANDOVER.md section 6.3 item 9 (see ADR-022). Off by default
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

## Quality gates

| Gate | State |
|---|---|
| Unit tests | 197 passing on the target device (3 BatDetect2 tests skip until its assets are fetched), plus 49 frontend tests |
| Integration tests | done — `tests/test_api.py` drives the real app and real pipeline end to end. **Gap:** the history endpoints have no HTTP-level test; `tests/test_history.py` exercises the aggregation functions only |
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

Per `CLAUDE.md`, this system is **not complete**. Outstanding before that word applies:

- the 72-hour soak test;
- the one-hour drift run at full duration;
- a committed fixture test proving a known species from a known recording;
- authentication;
- the Milestone 4 product dashboard, and Milestones 6 and 7 entirely.
