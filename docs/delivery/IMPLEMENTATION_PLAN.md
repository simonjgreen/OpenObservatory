# Implementation Plan

## Milestone 0 — Repository and target diagnostics

Deliver:

- Python/TypeScript monorepo scaffolding;
- CI, linting, typing and test commands;
- CLI `oo` skeleton;
- `oo audio probe` and `oo system report`;
- Docker Compose development baseline;
- target-device diagnostic report.

Exit gate: AudioMoth formats and stable device identity are recorded from the actual Pi.

## Milestone 1 — Deterministic capture and replay

Deliver:

- exclusive ALSA capture service;
- capture block contract;
- monotonic/UTC correlation;
- rolling memory buffer;
- WAV fixture replay source;
- gap/overrun detection;
- audio-level metrics;
- Prometheus health endpoint.

Exit gate: one-hour generated/replayed stream shows no timestamp drift or unexplained gaps.

## Milestone 2 — Derivation, windows and job transport

Deliver:

- 48 kHz resampling;
- source-frame mapping;
- window specification and segmenter;
- transient asset lease manager;
- Redis Streams job contract;
- bounded queue policies;
- window inspection CLI.

Exit gate: synthetic impulse appears in native and derived windows with documented timing error under 100 ms, target under 10 ms.

## Milestone 3 — Bird detector vertical slice

Deliver:

- BirdNET adapter installed through a documented model acquisition path;
- detector fixture self-test;
- normalised detection persistence;
- evidence clip manager;
- minimal FastAPI list/detail endpoints;
- simple server-rendered or temporary UI acceptable.

Exit gate: known bird fixture produces expected candidate label and an aligned playable clip.

## Milestone 4 — Product dashboard and review

Deliver:

- React dashboard;
- timeline, filters, detail, spectrogram and playback;
- review workflow;
- health/system page;
- retention job;
- API token/authentication foundation.

Exit gate: user can operate and diagnose the bird station entirely through the local UI.

## Milestone 5 — Ultrasonic and bat support

Deliver:

- native high-rate window profile;
- BatDetect2 evaluation harness;
- benchmark report on Pi 5;
- bat adapter if acceptance threshold met;
- night scheduler and deferred mode;
- native-rate evidence and audible playback rendering where useful.

Exit gate: known bat fixture is processed, provenance retained, and capture continuity unaffected under operating profile.

## Milestone 6 — MQTT, Home Assistant and alerts

Deliver:

- MQTT state/event publisher;
- Home Assistant discovery;
- environmental telemetry ingestion;
- alert rule engine with repetition and cooldown;
- HMAC webhooks.

Exit gate: Home Assistant shows station health and receives a test detection/alert.

## Milestone 7 — MCP, export and hardening

Deliver:

- read-only MCP tools;
- export bundles;
- backup/restore commands;
- setup wizard/commissioning report;
- privilege reduction and vulnerability scan;
- model licence screen;
- 72-hour soak test.

Exit gate: all v1 acceptance criteria pass.

## Explicitly deferred

- frog/insect production detectors;
- scientific biodiversity index;
- fleet management;
- mobile app;
- continuous raw audio archive;
- cloud-hosted service.
