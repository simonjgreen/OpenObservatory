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

Revised 2026-08-05 against what Milestone 3 actually built. Per ADR-016, this milestone
**promotes the existing UI** rather than starting a second one.

Already delivered, and not to be rebuilt:

- the React application shell and its component set;
- timeline, filters and detail — HISTORY mode's named windows, stacked timeline,
  species summary, click-to-focus and capture-coverage bar, over real aggregation SQL;
- a species-grouped detection list (`Suggestions`) that reads as product already;
- spectrogram and playback — two orientations, live listening and per-detection clips,
  including audible renderings of ultrasound;
- surface-agnostic infrastructure: the reconnecting WebSocket client and the audio
  playback engine;
- the diagnostic half of the health/system page.

Not foundation, despite appearances — see ADR-016 for the measurements:

- `styles.css` is a colour-token header over ad-hoc component CSS, with no spacing or
  type scale. A non-technical surface needs restyling, not just recomposition.
- the frontend has no component testing library, so everything with behaviour is
  untested and cannot currently be tested.

Deliver:

- a component testing library and tests for the behaviour being promoted, before
  promoting it;
- extraction of `App.tsx` state into hooks or a store — the prerequisite, not a tidy-up;
- URL-driven state, so a view survives a refresh and can be linked to;
- progressive disclosure separating the operator view from the diagnostic view;
- review workflow, end to end: the `review` table has no writer, no endpoint and no UI;
- derivation of a detection's current status from its latest valid review;
- retention job, plus a UI for the clip budget and what retention has removed;
- CSV/JSON export, which the acceptance criteria require and this plan had omitted;
- API token and authentication foundation, closing ADR-015.

Exit gate: a user can operate **and** diagnose the station entirely through the local UI,
with anonymous read access disabled by default.

## Milestone 4.5 — Close the Milestone 1–3 exit gates

Split out because these are unfinished gates, not new scope, and two of them are bounded
by wall clock rather than effort.

Deliver:

- the 72-hour soak on the target device;
- a committed fixture test proving a known species from a known recording, which needs a
  reference recording whose own licence permits redistribution;
- the drift test at its full one-hour duration;
- `oo audio window-dump`, the window inspection CLI Milestone 2 asked for.

**Sequencing note.** `deploy.sh` restarts the systemd unit, which resets capture and
voids a soak in progress. A soak and a deployment are therefore mutually exclusive:
decide what build is to be frozen before starting one, and do UI work that needs no
deploy while it runs.

Exit gate: the acceptance criteria for capture continuity pass over 72 continuous hours,
and a detector fixture test passes in CI on the target architecture.

## Milestone 5 — Ultrasonic and bat support

Revised 2026-08-05. Parts of this milestone were brought forward into Milestone 3
because the AudioMoth captures at 384 kHz, which made the ultrasonic band real rather
than theoretical.

Already delivered:

- native high-rate window profile;
- native-rate evidence and audible playback rendering — time expansion and heterodyne,
  per ADR-014, which is what makes a bat detection checkable by ear;
- `ultrasonic-pass-v1`, a pulse-train detector per ADR-013. It was never in this plan.
  It detects passes, claims no species, and does **not** discharge the BatDetect2
  deliverable below.

Deliver, in this order:

- **Feeding-buzz flagging.** Specified in
  `docs/superpowers/specs/2026-08-05-bat-feeding-buzz-and-frequency-titles-design.md`.
  The pulse timing is already computed and discarded; a buzz is a terminal collapse in
  inter-pulse interval. Emits `min_interval_ms` on every pass so a wrong threshold can be
  re-judged from stored data rather than from audio that no longer exists.
- **Frequency-band candidate titles.** Peak frequency and a candidate name in the event
  title. The candidate is presentational only: the stored record keeps `label = "bat
  pass"` and no species name, and the normaliser's guard continues to hold.
- **Ultrasonic detector configuration.** `station.py` currently constructs the detector
  with no configuration wiring at all, so `min_snr_db`, `min_pulses_per_pass` and the
  band cannot be set from `runtime.env` — despite the handover instructing a successor to
  tune exactly those. This blocks the false-positive work below.
- **Night scheduler and deferred mode.** Civil dusk to civil dawn plus a margin. The
  detector currently runs 24 hours a day, which wastes CPU through daylight and produces
  false positives from wind and handling noise when no bat could plausibly be flying.
- **False-positive review**, using the buzz figures and the audible renderings as
  evidence. 18–21 kHz remains genuinely ambiguous between noctule, serotine and
  bush-cricket, which is an insect; no amount of tuning resolves that from frequency
  alone.
- **BatDetect2 evaluation harness and Pi 5 benchmark**, then a bat adapter only if it
  meets the acceptance threshold and sustains real-time inference alongside BirdNET.

Exit gate: a known bat fixture is processed, provenance retained, and capture continuity
unaffected under the operating profile. A species claim requires a classifier that
declares itself taxonomic; the pass detector cannot satisfy that clause and is not
intended to.

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
