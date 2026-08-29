# Product Requirements Document

> **This is the seed product specification, unedited.** It states what the product
> is *for*, and that has not changed — the principles in §7 and the privacy rules
> in §10 are still binding, and several are enforced in code rather than merely
> stated.
>
> It is **not** a status document. §13's release scope (v0.1 … v1.0) is
> superseded by [`../delivery/IMPLEMENTATION_PLAN.md`](../delivery/IMPLEMENTATION_PLAN.md)'s
> milestones, and what is actually delivered is in
> [`../delivery/MILESTONE_STATUS.md`](../delivery/MILESTONE_STATUS.md). Nothing
> here should be read as a claim that a requirement is met.
>
> Requirements known **not** to be met as of 2026-08-09, so this file is not
> mistaken for one: FR-007's bat detector identifies no species ([[ADR-013 - ultrasonic-pass-v1|ADR-013]]);
> FR-010's alert rules and FR-011's telemetry correlation do not exist; FR-015's
> MCP interface does not exist; FR-017's upgrade preflight and backup guidance do
> not exist; §10's configurable location precision does not exist; and §11's
> 72-hour soak ran 2026-08-10 to 2026-08-13 and **failed** its continuity
> criterion (99.865% against ≥ 99.9%; see [[MILESTONE_STATUS]]
> §Milestone 4.5).

## 1. Product name

**Open Observatory**

## 2. Vision

Create an appliance-like, privacy-conscious acoustic observatory that continuously listens to a garden or local habitat, identifies wildlife calls locally, preserves short evidence clips, correlates detections with environmental conditions, and makes long-term biodiversity activity understandable.

The product must feel like infrastructure rather than an experiment: it boots unattended, recovers from faults, explains its health, avoids losing time continuity, and makes classifier uncertainty visible.

## 3. Primary user

A technically capable homeowner or field enthusiast operating one fixed station continuously. The initial deployment is a single Raspberry Pi 5 and one AudioMoth USB microphone at a UK residential property.

## 4. Jobs to be done

- Tell me which bird and bat species were acoustically active and when.
- Let me inspect the evidence behind an identification.
- Notify me when an unusual or personally interesting species is repeatedly detected.
- Show how activity changes by time of day, season and environmental conditions.
- Keep operating without internet access.
- Let me add new detector types without rebuilding the capture platform.
- Let Home Assistant and an AI assistant query the observatory through stable interfaces.
- Make failures, gaps and classifier limitations obvious.

## 5. Goals

### G1 — Reliable continuous capture

Capture from the AudioMoth continuously with explicit detection of overruns, disconnects, gaps and clock discontinuities.

### G2 — Multi-rate detector support

Support audible and ultrasonic analysis from one physical microphone by capturing once at the highest practical negotiated sample rate and deriving detector-specific representations.

### G3 — Modular classification

Run detectors independently through a stable plugin contract. BirdNET is the first audible detector. BatDetect2 or an equivalent proven UK-capable bat detector is the first ultrasonic detector.

### G4 — Evidence-led output

Every retained detection must reference the model, version, confidence, time interval, source stream and optional evidence clip. Users must be able to mark detections confirmed, rejected or uncertain.

### G5 — Local-first privacy

Core operation must not upload audio or detections. External publication and notifications are opt-in.

### G6 — Long-term operation

The appliance must recover from reboots, temporary microphone loss, worker crashes, database restarts and network outages.

### G7 — Extensibility

Make frogs, insects, generic sound events and environmental telemetry feasible additions without changing capture ownership or the canonical detection schema.

## 6. Non-goals for v1

- Scientifically validated population abundance estimates.
- Guaranteed identification of every audible organism.
- Training models on the Pi.
- Real-time public livestreaming.
- Multi-site fleet management.
- Continuous archival of raw audio.
- Cloud account, hosted control plane or mobile application.
- Automatic submission to scientific databases without explicit opt-in and review.
- A single synthetic “biodiversity score” presented as scientifically authoritative.

## 7. Product principles

1. **One microphone owner.** All consumers use derived streams or immutable audio windows.
2. **Evidence over assertion.** A label without inspectable provenance is not a useful record.
3. **Unknown is valid.** Low-confidence and unclassified events are first-class outcomes.
4. **No silent gaps.** Missing audio must be recorded as a health event.
5. **Local by default.** Cloud integrations are adapters, not dependencies.
6. **Models are replaceable.** Their licences and output taxonomies differ; the platform must not pretend otherwise.
7. **Raw confidence is not probability.** The UI must not overstate model scores.
8. **Privacy by minimisation.** Keep short wildlife evidence clips, not an indefinite ambient surveillance archive.

## 8. Functional requirements

### FR-001 Device setup

The user can discover the attached audio device, inspect supported formats/sample rates, perform a monitored test capture, select timezone/location and initialise storage.

### FR-002 Capture

The system records mono PCM from the selected ALSA source. It timestamps frames against monotonic and wall clocks, tracks sequence numbers, and emits gap events.

### FR-003 Rolling buffer

Maintain a configurable rolling high-rate audio buffer, initially 120 seconds, sufficient to produce pre-roll and post-roll evidence clips.

### FR-004 Stream derivation

Produce at least:

- audible PCM: 48 kHz mono, detector-compatible encoding;
- ultrasonic PCM: native negotiated high-rate mono, preferably 256 or 384 kHz;
- optional monitor stream: compressed, rate-limited and disabled by default.

### FR-005 Detector scheduling

Detector plugins subscribe to window specifications rather than raw device handles. Plugins declare sample-rate, window-size, stride, latency tolerance and resource class.

### FR-006 Bird detector

Run BirdNET-compatible local inference with configured latitude, longitude, week/date prior where supported, confidence threshold, overlap and locale labels.

### FR-007 Bat detector

Run a UK-relevant high-frequency detector over native-rate windows. Initial target is BatDetect2, subject to Raspberry Pi performance validation and licence review.

### FR-008 Canonical detections

Normalise detector output into a shared schema without discarding model-native fields.

### FR-009 Detection review

The dashboard lists detections, filters by taxonomy/time/model/confidence/review status, plays evidence clips and records user review.

### FR-010 Alerts

Rules can require repeated detections, minimum confidence, species allow/deny lists, time windows and cooldowns. Initial outputs: MQTT and local webhook.

### FR-011 Telemetry correlation

Ingest environmental readings from MQTT or Home Assistant-compatible topics and associate nearest readings with a detection at query time. Do not duplicate all telemetry into each detection row.

### FR-012 Health

Expose capture continuity, buffer depth, audio level, clipping, detector lag, queue depth, dropped jobs, disk usage, microphone state, CPU temperature, throttling and service health.

### FR-013 Retention

Configure retention independently for raw rolling audio, evidence clips, rejected detections, confirmed detections, metrics and logs.

### FR-014 Export

Export detections and reviews as CSV/JSON and evidence as a documented directory bundle with a manifest.

### FR-015 APIs

Provide local REST, WebSocket/SSE, MQTT and MCP interfaces described in the technical spec.

### FR-016 Offline operation

All core screens and analysis remain functional without internet connectivity after dependencies and model assets are installed.

### FR-017 Update safety

Configuration and schema migrations must be versioned. Upgrades must provide preflight checks and database backup guidance.

## 9. User experience

### Dashboard home

- station status and last uninterrupted capture period;
- detections today and species count;
- timeline of wildlife activity;
- latest noteworthy detections;
- audio pipeline warnings;
- disk retention forecast.

### Detection detail

- common/scientific label;
- detector and exact model version;
- confidence/score with caveat;
- timestamp and duration;
- spectrogram and audio playback;
- surrounding detections;
- environmental readings;
- confirm/reject/uncertain controls;
- original model-native metadata.

### System page

- microphone negotiated format;
- gain/filter configuration recorded from setup;
- live levels and clipping;
- worker utilisation and lag;
- capture gaps;
- model licences and attribution;
- update/export/diagnostic actions.

## 10. Privacy and ethics requirements

- No continuous raw archive by default.
- Evidence clips default to a maximum of 12 seconds with configurable pre/post roll.
- Optional voice-likelihood classifier may suppress evidence retention where human speech dominates; this is a future enhancement, not a v1 blocker.
- Clearly warn that outdoor microphones may capture neighbours and conversations.
- Publishing adapters must be disabled until explicitly configured.
- Location precision exposed through APIs must be configurable; external integrations should default to coarse location.

## 11. Success metrics

- 72-hour soak test with at least 99.9% audio-frame continuity excluding explicitly logged hardware disconnects.
- No unbounded queue growth under the configured v1 detector set.
- Recovery from AudioMoth unplug/replug without host reboot.
- Evidence clip timestamps align to detections within 100 ms under normal load.
- Dashboard displays a new detection within 10 seconds of detector output.
- No dependency on internet during a 24-hour disconnected test.
- User can identify why any detector was not operating from the health UI.

## 12. Risks

- Pi 5 may not sustain multiple high-rate real-time inference workloads; scheduling and deferred analysis are required.
- AudioMoth USB negotiated modes may vary by firmware/switch/configuration.
- Ultrasound performance depends strongly on enclosure, placement and microphone response.
- Classifier licences may restrict redistribution or commercial use.
- Bat taxonomy and model geographic coverage may be narrower than users assume.
- Rain, wind, insects, clipping and anthropogenic noise can produce false detections.
- High-rate PCM creates substantial transient I/O and memory pressure.

## 13. Release scope

### v0.1 Foundation

Device diagnostics, deterministic capture, rolling buffer, recorded-file replay, health metrics.

### v0.2 Birds

Bird detector adapter, canonical detections, clip retention, basic dashboard.

### v0.3 Bats

High-rate windows, bat detector adapter, resource scheduler and night profile.

### v0.4 Integrations

MQTT, Home Assistant discovery, alerts, export and MCP read-only tools.

### v1.0 Appliance

Hardening, setup wizard, backup/restore, model licence UI, 72-hour soak acceptance.
