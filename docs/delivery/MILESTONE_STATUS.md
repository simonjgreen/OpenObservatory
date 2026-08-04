# Milestone status

Honest state of the implementation against `IMPLEMENTATION_PLAN.md`. Anything not
demonstrated on the actual Pi is marked as not done, regardless of whether the code
exists.

Recorded 2026-08-04.

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
| Minimal FastAPI list/detail endpoints | done, plus health, metrics, devices, streams, gaps, detectors, models, taxa activity, media |
| Temporary UI | done — the real-time debug UI |

**Exit gate — known bird fixture produces expected candidate label and an aligned
playable clip: partially met.** BirdNET produced real identifications on live audio
within minutes (including *Columba palumbus*) with playable, aligned clips. That is
a live demonstration, not the repeatable fixture test the gate asks for, because no
licensed reference recording is committed to the repository.

## Milestone 4 — Product dashboard and review — **not started**

The debug UI is explicitly **not** this (ADR-011). Timeline, filters, review
workflow, retention UI and the authentication foundation remain outstanding. The
`review` table exists in the schema; nothing writes to it.

## Milestone 5 — Ultrasonic and bat support — **partial, beyond plan**

Brought forward because the device turned out to capture at 384 kHz, which makes
the native stream genuinely useful rather than theoretical.

| Deliverable | State |
|---|---|
| Native high-rate window profile | done |
| Ultrasonic detection | done, but as a **pulse-train detector, not a classifier** — `ultrasonic-pass-v1`, no species claim |
| BatDetect2 evaluation harness | not started |
| Pi 5 benchmark report | partial — `ultrasonic-pass-v1` measured at ~36–40× realtime |
| Night scheduler / deferred mode | **not started** — the ultrasonic detector runs continuously, day and night |
| Native-rate evidence + audible playback derivative | done |

## Milestone 6 — MQTT and Home Assistant — **not started**

The event envelope is already the published one, so a publisher is additive.

## Milestone 7 — MCP, export and hardening — **not started**

Partial credit only: the systemd unit applies privilege reduction
(`NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome=read-only`, a memory cap and
an explicit writable path), model licences are surfaced through
`/api/v1/models` and in the UI, and media paths are validated against the clip
directory before being served.

## Quality gates

| Gate | State |
|---|---|
| Unit tests | 111 passing on the target device |
| Integration tests | done — `tests/test_api.py` drives the real app and real pipeline end to end |
| Target-device smoke test | `oo audio probe`, `oo audio test-capture`, `oo audio resample-check`, `./deploy/deploy.sh` health wait |
| Rollback note | `deploy/deploy.sh` is idempotent; `sudo systemctl stop open-observatory` halts cleanly. ADR-007 records how to move to PostgreSQL and back |
| Updated docs | done |
| Measured CPU, memory, dropped audio | done — `TARGET_DIAGNOSTICS.md` |

## What must not be claimed yet

Per `CLAUDE.md`, this system is **not complete**. Outstanding before that word applies:

- the 72-hour soak test;
- the one-hour drift run at full duration;
- a committed fixture test proving a known species from a known recording;
- authentication;
- the Milestone 4 product dashboard, and Milestones 6 and 7 entirely.
