# Open Observatory documentation

## Start here

| I want to... | Read |
|---|---|
| Know what this system is *for*, and what wins in a conflict | **[CHARTER.md](CHARTER.md)** |
| Set up a development environment without losing an hour | **[development/SETUP.md](development/SETUP.md)** |
| Know what "tested" has to mean here | **[development/TEST_PLAN.md](development/TEST_PLAN.md)** |
| Understand why something is the way it is | [architecture/ADRS.md](architecture/ADRS.md) |
| Know what is actually done | [delivery/MILESTONE_STATUS.md](delivery/MILESTONE_STATUS.md) |
| Operate or deploy the station | [operations/DEPLOYMENT_AND_OPERATIONS.md](operations/DEPLOYMENT_AND_OPERATIONS.md) |
| Pick up where the last session left off | [delivery/HANDOVER.md](delivery/HANDOVER.md) |


Start here. This page is the map: what the system is, how to run it, and which
document answers which question.

Verified against the code on **2026-08-09**. Where a document has not been
re-verified since it was written, it says so in its own header.

---

## What this is

A local-first passive acoustic observatory. One AudioMoth USB microphone on a
Raspberry Pi 5 captures a continuous **384 kHz mono** stream; the station derives
a 48 kHz audible stream from it, cuts immutable time-addressed windows, runs
three detectors over them, normalises and persists the detections, writes
checksummed evidence clips to a USB SSD, and serves them through a FastAPI
control plane, a React debug/operator UI, an optional MQTT publisher and an
ESP32 wall display.

It is **not complete**. `CLAUDE.md` forbids that word until the acceptance
criteria pass a continuous 72-hour soak on the Pi, and that soak has never been
run. See [`delivery/MILESTONE_STATUS.md`](delivery/MILESTONE_STATUS.md) for an
honest account of what works.

### The pipeline in one diagram

```
AudioMoth 384 kHz ──▶ capture ──▶ native ring (120 s) ──▶ evidence clips ──▶ USB SSD
                         │                    ▲
                         ├──▶ soxr 1/8 ──▶ audible ring
                         │         │
                         │         ├──▶ spectrogram ──┐
                         ├──▶ spectrogram (ultrasonic)┤──▶ WebSocket ──▶ debug UI
                         │         └──▶ live audio ───┘
                         │
                         └──▶ segmenter ──▶ windows ──▶ detector workers
                                                            │
                                          normaliser ◀──────┘
                                               │
                            ┌──────────────────┼──────────────────┐
                            ▼                  ▼                  ▼
                    SQLite/PostgreSQL     event bus         evidence clips
                            │              │      │
                        REST API ◀─────────┘      └──▶ MQTT publisher ──▶ Home Assistant
                         │     │
              React UI ◀─┘     └──▶ ESP32 wall display (HTTP polling)
```

### Consumers of the API, as of today

| Surface | Where | Transport |
|---|---|---|
| Debug/operator web UI | `web/` | WebSocket + REST + chunked-WAV HTTP |
| ESP32 wall display ("inside observer") | `firmware/inside-observer/` | REST polling every 20 s |
| Home Assistant | `src/open_observatory/mqtt/` | MQTT + HA Discovery (off by default) |
| Prometheus | `GET /metrics` | scrape |

---

## I want to…

| …do this | Read this |
|---|---|
| **Get a dev environment working** | [`development/SETUP.md`](development/SETUP.md) — read this first, it lists traps that will otherwise cost you an hour |
| Understand *why* the product exists | [`product/PRD.md`](product/PRD.md) |
| Understand the intended architecture | [`architecture/TECHNICAL_SPEC.md`](architecture/TECHNICAL_SPEC.md) (seed spec — see the ADRs for where reality diverged) |
| Understand a decision, or why the code does something odd | [`architecture/ADRS.md`](architecture/ADRS.md) — every deviation is here, numbered, and referenced from source comments |
| See what was assumed before hardware existed, and how it resolved | [`architecture/GAP_REPORT.md`](architecture/GAP_REPORT.md) |
| Know what is done and what is not | [`delivery/MILESTONE_STATUS.md`](delivery/MILESTONE_STATUS.md) |
| Pick up the project cold as the next engineer | [`delivery/HANDOVER.md`](delivery/HANDOVER.md) — traps, next steps, the bugs found by measuring |
| Deploy, operate, back up, or turn on auth | [`operations/DEPLOYMENT_AND_OPERATIONS.md`](operations/DEPLOYMENT_AND_OPERATIONS.md) |
| Find a measured number about the real station | [`operations/TARGET_DIAGNOSTICS.md`](operations/TARGET_DIAGNOSTICS.md) — the authoritative home for measurements |
| Work on the microphone or its switch/gain | [`operations/AUDIOMOTH_FIRMWARE.md`](operations/AUDIOMOTH_FIRMWARE.md) |
| Wire the station into Home Assistant | [`operations/HOME_ASSISTANT.md`](operations/HOME_ASSISTANT.md) |
| Call the REST API | [`api/API_AND_INTEGRATIONS.md`](api/API_AND_INTEGRATIONS.md) |
| Build a live client (spectrogram, listening) | [`api/DEBUG_UI_TRANSPORT.md`](api/DEBUG_UI_TRANSPORT.md) |
| Understand the audio path and its correctness rules | [`audio/AUDIO_PIPELINE.md`](audio/AUDIO_PIPELINE.md) |
| Query or migrate the database | [`data/DATA_MODEL.md`](data/DATA_MODEL.md) |
| Work on a detector | [`detectors/DETECTOR_STRATEGY.md`](detectors/DETECTOR_STRATEGY.md) |
| Know why BatDetect2 is not a live detector | [`detectors/BATDETECT2_EVALUATION.md`](detectors/BATDETECT2_EVALUATION.md) |
| Chase the open capture-gap problem | [`delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md`](delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md) |
| Work on the ESP32 wall display | [`../firmware/inside-observer/README.md`](../firmware/inside-observer/README.md) |

---

## Documentation layout

```
docs/
  README.md            this map
  product/             why the product exists                    PRD.md
  architecture/        how it is meant to work, and every         TECHNICAL_SPEC.md
                       decision that changed that                 ADRS.md
                                                                  GAP_REPORT.md
  api/                 the contracts other things depend on       API_AND_INTEGRATIONS.md
                                                                  DEBUG_UI_TRANSPORT.md
  audio/               the audio path's correctness rules         AUDIO_PIPELINE.md
  data/                schema, indexes, migrations                DATA_MODEL.md
  detectors/           detector design and evaluation             DETECTOR_STRATEGY.md
                                                                  BATDETECT2_EVALUATION.md
  operations/          running the real station                   DEPLOYMENT_AND_OPERATIONS.md
                                                                  TARGET_DIAGNOSTICS.md
                                                                  AUDIOMOTH_FIRMWARE.md
                                                                  HOME_ASSISTANT.md
  development/         working on the code                        SETUP.md
  delivery/            plan, status and the engineering record    IMPLEMENTATION_PLAN.md
                                                                  MILESTONE_STATUS.md
                                                                  HANDOVER.md
                                                                  ACCEPTANCE_CRITERIA.md
                                                                  OPEN_INVESTIGATION_CAPTURE_GAPS.md
  design/              dated design specs, kept as a record       (one, 2026-08-05)
  SOURCES_AND_ASSUMPTIONS.md
```

Outside `docs/`: [`../README.md`](../README.md) (project overview),
[`../CLAUDE.md`](../CLAUDE.md) (the operator's own brief — read it, do not
rewrite it), [`../config/example.env`](../config/example.env) (every setting),
[`../firmware/inside-observer/README.md`](../firmware/inside-observer/README.md).

### One authoritative home per fact

These documents overlap, and have drifted apart from each other before. When two
disagree, prefer the authority named here:

| Fact | Authority |
|---|---|
| Measured CPU, continuity, latency, inference timing, hardware identity | `operations/TARGET_DIAGNOSTICS.md` |
| What is delivered vs. outstanding, per milestone | `delivery/MILESTONE_STATUS.md` |
| Why a design is the way it is | `architecture/ADRS.md` |
| Setting names, types and defaults | `src/open_observatory/config.py` (then `config/example.env`) |
| Endpoint paths and query parameters | `src/open_observatory/api/app.py` (then `api/API_AND_INTEGRATIONS.md`) |
| The event wire envelope | `schemas/detection-event.schema.json` |
| Operational traps and the next-steps list | `delivery/HANDOVER.md` |

---

## Rules this project enforces in code, not just prose

Breaking one of these is a correctness bug, not a style disagreement.

- **A non-taxonomic detector cannot emit a species name.** `normaliser.py` raises
  `ClaimViolation`. `activity-v1` and `ultrasonic-pass-v1` are non-taxonomic.
- **`ultrasonic-pass-v1` detects bat *passes*, never species.** No document, UI,
  MQTT payload or display may imply otherwise.
- **A BirdNET score is not a calibrated probability.** Never render it as a
  percentage, a likelihood, or "confidence that it is correct".
- **Levels are uncalibrated dBFS, never SPL.** No calibration procedure exists.
- **One process owns the microphone.** Never add a second ALSA opener.
- **Frames, not timestamps, address audio.** `StreamClock` maps frame → time.
- **Devices resolve by `stable_device_key`, never by ALSA card index.**
- **One writer per WebSocket.** Concurrent writers caused the worst bug in the
  project, and it is invisible on loopback (ADR-012).
- **Capture always wins.** Every queue between capture and a consumer is bounded,
  drops explicitly, and counts its drops.
- **A synthetic or replayed source is announced loudly**, including in
  `/api/v1/health`, and its detections are excluded from browsing views by
  default (ADR-020).
- **The system is not complete** until the 72-hour soak passes.
