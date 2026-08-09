# Detector Strategy

This document is the pre-implementation design spec, written before any detector existed.
It is kept as-is below to record intent. See **"As implemented"** at the end for the three
detectors that actually shipped, their real configuration keys and defaults, and where the
shipped system differs from what this document proposed — most notably, that BatDetect2
below was never implemented as a live detector.

Configuration keys and defaults in the "As implemented" sections were re-verified against
`src/open_observatory/config.py` on **2026-08-09**.

## Bird detection

Initial adapter: BirdNET through an officially maintained library or BirdNET-Analyzer-compatible model path. The implementation must record exact code, model, label and range-model versions.

BirdNET source code is permissively licensed, while distributed model assets carry separate non-commercial/share-alike terms. Model installation must therefore be a separate, attributable step and the product must expose licence metadata.

Expected windowing is typically approximately three seconds at 48 kHz, but adapter metadata is authoritative.

## Bat detection

*As implemented: not this.* A non-ML pulse-train detector (`ultrasonic-pass-v1`) shipped
instead and is running on the target device. **BatDetect2 was benchmarked on the target
on 2026-08-05 and deliberately not adopted as a live detector** — measured at 0.52×
realtime, against 36–40× for the detectors that do ship. There is no
`open_observatory.detectors.batdetect2` adapter and none is planned unless the cascade
below is promoted. See `BATDETECT2_EVALUATION.md` for the figures and ADR-017 for the
decision.

Initial candidate: BatDetect2, a deep-learning detector/classifier for bat echolocation in high-frequency recordings. Validate:

- ARM64 installation;
- inference speed on Pi 5;
- supported species and UK relevance;
- model licence and redistribution rules;
- required sample rate and input normalisation;
- whether continuous real-time operation is feasible.

If real-time BatDetect2 is not sustainable, use a bounded deferred-night queue and process windows after capture. The product must report lag honestly.

`acoupi_batdetect2` may be evaluated as an edge-oriented integration reference, but Open Observatory should not bind its core contracts to one framework before benchmarking.

## Frog and amphibian detection

Do not promise a universal frog detector in v1. Define the plugin interface and add support only for a model with:

- appropriate UK species coverage;
- redistributable or user-installable assets;
- documented confidence and input requirements;
- fixture recordings and repeatable acceptance tests.

Potential future approaches include custom BirdNET classifiers or region-specific ecoacoustic models.

## Insect detection

Treat insects as multiple domains:

- audible orthoptera and cicadas;
- ultrasonic orthoptera;
- wingbeat/frequency-specific monitoring;
- generic acoustic event detection.

No generic “insect detected” claim should be made without defining taxonomy and validation. The architecture supports it through native or derived stream plugins.

## General sound events

A general AudioSet-style classifier may label rain, wind, aircraft, dog bark and speech-like audio. This should be a low-priority sampled detector initially, because broad models can be noisy and resource-heavy.

Its most valuable early use is operational context and privacy minimisation, not wildlife taxonomy.

## Consensus and deduplication

Detections from different models must remain individually preserved. A separate correlation layer may create a `detection_cluster` when events overlap in time and taxonomy.

Never average incompatible confidence scores. Consensus can be expressed as:

- independent model count;
- temporal overlap;
- taxonomy mapping certainty;
- rule-based status such as `corroborated`.

## Confidence handling

Store raw score and optional calibrated probability separately. UI label should default to “model score” unless calibration is documented.

Alerts should support repeated-event rules because one high score is not necessarily stronger evidence than several coherent calls.

## As implemented

Three detector plugins are running on the target Pi 5. None of them match the strategy
above exactly — the first was not anticipated at all, the second matches the "Bird
detection" section, and the third replaces the "Bat detection" section's BatDetect2 plan.
Configuration keys and defaults below are read from `src/open_observatory/config.py`.

| Plugin id | Module | Model dependency | Gets evidence clips? |
|---|---|---|---|
| `activity-v1` | `src/open_observatory/detectors/activity.py` | none | no |
| `birdnet-v2.4` | `src/open_observatory/detectors/birdnet.py` | BirdNET GLOBAL 6K V2.4, operator-installed | yes |
| `ultrasonic-pass-v1` | `src/open_observatory/detectors/ultrasonic.py` | none | yes |

`clip_plugins` defaults to `("birdnet-v2.4", "ultrasonic-pass-v1")`. `activity-v1` is
deliberately excluded: it fires on ordinary energy blips several times a second in a live
garden, and clipping every firing at native rate was measured writing 640 GB/day the first
time it was tried. Its purpose is to prove the window-to-event path and give an
always-available signal, not to produce evidence.

### `activity-v1` — the owned, always-available detector (ADR-010)

Not anticipated by this document. Ships as the reference implementation of the detector
plugin interface, specifically so a build with zero operator action — no model installed,
no licence accepted — still exercises the full window-to-detector-to-normaliser-to-clip-to-
event path. It is a band-limited onset/energy segmentation detector with no model
dependency and no taxonomic output.

| Config key | Default |
|---|---|
| `activity_enabled` | `True` |
| `activity_min_snr_db` | `18.0` |
| `activity_min_duration_ms` | `60.0` |
| `activity_band_hz` | `(1200.0, 11000.0)` |

`activity_min_snr_db` is calibrated against the noise ceiling measured on the target
device, not chosen arbitrarily — see the docstring in `detectors/activity.py` and the
input-level figures in `TARGET_DIAGNOSTICS.md`. ADR-010 constrains this detector to never
emit a species label, scientific name, or taxon id: `rank` is `null` and its group is
`acoustic_event`, enforced by the normaliser.

### `birdnet-v2.4` — matches this document's "Bird detection" section

Runs BirdNET GLOBAL 6K V2.4 (`MODEL_ID = "BirdNET_GLOBAL_6K_V2.4"`) through `ai-edge-litert`
— the maintained successor to `tflite-runtime`, required because `tflite-runtime` has no
cp312 aarch64 wheel and needs NumPy 1.x, both unusable on this stack (see the software
environment table in `TARGET_DIAGNOSTICS.md`).

| Config key | Default |
|---|---|
| `birdnet_enabled` | `True` |
| `birdnet_min_confidence` | `0.12` |
| `birdnet_window_stride_s` | `1.5` |
| `birdnet_use_location_filter` | `False` |
| `birdnet_plausibility_floor` | `0.0005` |
| `range_threshold` (constructor default) | `0.03` |
| `threshold_in_range` | `0.55` |
| `threshold_uncommon` | `0.75` |
| `threshold_out_of_range` | `0.90` |

Plausibility is banded by whether the range/occurrence model considers a species likely at
this station's location and week: species in range clear `threshold_in_range` (0.55),
uncommon species must clear 0.75, and species the model considers merely out of range must
clear 0.90 before being reported at all. With no range model loaded, every candidate is
judged against `threshold_in_range` only — there is no invented prior. Two further bands,
added by ADR-032, sit outside that score-space ladder entirely: a species at or below
`birdnet_plausibility_floor` (0.0005) is suppressed at any score, and a species the *loaded*
range model is simply silent about (`occurrence is None`) is judged against
`threshold_out_of_range`, not `threshold_in_range` — see "Known limitation" below.

The shipped default is `birdnet_use_location_filter=False` with no coordinates, so out of
the box every species is judged on confidence alone. At the development station the coordinates and
the filter are set in the Pi's `config/runtime.env`, which is not in version control
because the data model treats station location as access-controlled — so the repository
defaults and the running station legitimately differ here. Enabling it is not cosmetic:
before it was on, this station stored "Great Bittern" and "Spotted Crake" at 0.9
confidence in a the development area garden.

Fixture testing: `tests/test_detectors.py` exercises BirdNET's logic — week calculation,
label parsing, plausibility-band thresholds, licence metadata, missing-model handling.
`tests/test_birdnet_fixture.py`, added 2026-08-08, is the fixture test that runs real
inference on a known recording and asserts a known species: a committed, individually
licence-checked European Robin recording (CC BY-SA 4.0, Xeno-canto XC441752 — see
`tests/fixtures/audio/ATTRIBUTION.md`) is analysed at the development station's coordinates with
ADR-032's plausibility filtering switched on, and the test asserts both that "European
Robin" appears among the candidates in an unsuppressed plausibility band, and that the
resulting evidence clip is playable and frame-aligned to where the call actually is in
the source recording (checked by exact sample comparison at the clip's own recorded frame
bounds, not just by overlap). It skips cleanly — like the BatDetect2 tests already do —
when the (unbundled) model assets or a TFLite runtime are absent.

That test passes **on the target Pi 5 (aarch64)**, run there on 2026-08-08 against the
station's own fetched model assets (3 passed in 6.83 s). Per this project's rule that a
detector is only "supported" once its fixture test passes on target architecture,
`birdnet-v2.4` now clears that bar. See `docs/delivery/MILESTONE_STATUS.md`'s Milestone 3
exit-gate note for the full output and the stride caveat.

### `ultrasonic-pass-v1` — replaces the BatDetect2 plan (ADR-013)

BatDetect2, proposed above under "Bat detection", was never implemented. What shipped
instead is a non-ML pulse-train detector operating on the native 384 kHz stream. It detects
bat *passes* — never species — and the normaliser rejects any detection from a
non-taxonomic plugin that carries a species name.

Every threshold below is now a configuration key on `Settings` — previously `station.py`
constructed `UltrasonicDetector` with no configuration path at all, so none of these could
be tuned without editing code. Defaults are unchanged from the previous hard-coded
constructor defaults, so behaviour did not change until an operator set something.

| Config key | Default |
|---|---|
| `ultrasonic_enabled` | `True` |
| `ultrasonic_band_hz` | `(15_000.0, 125_000.0)` |
| `ultrasonic_min_snr_db` | `12.0` |
| `ultrasonic_min_pulse_ms` | `1.5` |
| `ultrasonic_max_pulse_ms` | `40.0` |
| `ultrasonic_merge_gap_ms` | `2.0` — fragments of one call closer than this, measured onset to onset, merge. See "Sub-bin peak frequency" below for why onset-to-onset matters: a feeding buzz must never be merged away |
| `ultrasonic_pass_gap_s` | `1.5` |
| `ultrasonic_min_pulses_per_pass` | `3` |

Table verified against `src/open_observatory/config.py` on 2026-08-09.

A detection reports a measured frequency band, pulse count and SNR, and nothing more. A
frequency band is evidence a human can interpret, not an identification: the detector's own
tests and ADR-013 both note that 18–21 kHz is genuinely ambiguous between noctule and
bush-cricket, and the shipped code does not attempt to resolve that ambiguity.

Two honesty-relevant limitations, stated in ADR-013 and in `TARGET_DIAGNOSTICS.md`:

- it has a known false-positive rate on broadband transients such as wind and handling
  noise;
- it now has a night scheduler (below), but that only reduces *when* it runs; it does not
  reduce the false-positive rate of an individual pass.

### Night scheduling (`src/open_observatory/schedule.py`)

The detector is gated to civil dusk through civil dawn, plus configurable margins, computed
from the station's own coordinates using the NOAA low-precision solar position formulas at
the standard -6 degree civil-twilight elevation. No new dependency was added. The gate is
checked before any FFT work runs, so the CPU saving on a gated-out window is real, not
nominal.

| Config key | Default |
|---|---|
| `ultrasonic_schedule` | `"always"` (`"always"` \| `"night"`) |
| `ultrasonic_schedule_dusk_margin_min` | `30.0` |
| `ultrasonic_schedule_dawn_margin_min` | `30.0` |

Verified on the Pi for the development station on 2026-08-05: civil dusk 20:27Z, civil dawn 03:55Z,
the schedule reported active at 21:45 local and inactive at noon. The station's
`config/runtime.env` sets `OO_ULTRASONIC_SCHEDULE=night`; the repository default remains
`always`.

The failure mode is deliberate and stated plainly in the module docstring: with coordinates
unset there is no schedule to compute, so the detector runs continuously rather than gating
to nothing. A station that silently records nothing overnight would look identical to a
quiet night, which is worse than running the detector when it need not. Schedule mode,
tonight's computed dusk/dawn, and the count of windows gated out are all visible in
`GET /api/v1/detectors`.

### Feeding-buzz flagging

A feeding buzz — the terminal acceleration in pulse rate as a bat closes on prey — is
flagged when a run of at least `ultrasonic_buzz_min_pulses` (default `5`) consecutive
inter-pulse intervals falls below `ultrasonic_buzz_max_interval_ms` (default `12.0`) **and**
that run's own median interval is below `ultrasonic_buzz_interval_ratio` (default `0.4`) of
the whole pass's median interval. The second condition is what separates a genuine terminal
collapse from a bat that simply calls fast throughout the pass; the first alone is not
sufficient.

| Config key | Default |
|---|---|
| `ultrasonic_buzz_max_interval_ms` | `12.0` |
| `ultrasonic_buzz_min_pulses` | `5` |
| `ultrasonic_buzz_interval_ratio` | `0.4` |

`native_result` gains `has_feeding_buzz`, `buzz_offset_s`, `buzz_min_interval_ms` and
`buzz_pulse_count`. `min_interval_ms` is also emitted on every pass, buzz or not, so a
threshold that later proves wrong can be re-judged against stored data rather than against
audio that no longer exists.

### Sub-bin peak frequency (a measurement bug, fixed)

The pulse-detection FFT uses a 128-sample window so it can resolve a 1.5 ms call; at 384 kHz
that gives 3000 Hz bins, so every reported peak frequency used to land on an exact multiple
of 3 kHz. The candidate-species band edges fall *between* bin centres — 38 kHz, the boundary
between the Myotis group and common pipistrelle, sits between the 36 and 39 kHz bins — so a
bat calling at, say, 37.5 kHz was assigned to a species group by quantisation artefact
rather than by its actual call.

Parabolic interpolation through the peak bin and its two neighbours, done in the log domain,
now recovers the peak to a fraction of a bin, clamped to the neighbouring half-bins so a
parabola fitted to noise cannot place the vertex arbitrarily far away. Confirmed live on the
Pi: before the fix the station reported peak frequencies of 33000.0, 36000.0, 39000.0,
54000.0 Hz exactly; after it, the same passes reported 35286.6, 35841.5, 36225.0, 53520.4 Hz.
The 35–36 kHz cluster therefore survives as a genuine signal rather than being an artefact of
bin quantisation.

### Candidate naming — presentational only

Bat pass event titles now carry the peak frequency and a candidate group name, for example
"36 kHz - Myotis / barbastelle?" and "54 kHz - soprano pipistrelle?"; the question mark is
mandatory and always present. The 17–21 kHz band additionally carries "may be a bush-cricket"
— an insect, not a bat. This is presentational only: the stored record keeps
`label = "bat pass"` with no species name anywhere in it, and the normaliser's
`ClaimViolation` guard, which rejects any species claim from a non-taxonomic plugin, is
unchanged and covered by test. The candidate name is a hint for a human to weigh, never an
identification, and it must not be read as one.

Evidence clips for this plugin get an audible derivative via
`src/open_observatory/audio/ultrasound.py` (time-expansion or heterodyne — see
`docs/audio/AUDIO_PIPELINE.md`, "Ultrasound-to-audible rendering"), because an ultrasonic
clip as recorded is not checkable by ear. That rendering is peak-normalised and must not be
read as sound-pressure information: all levels in this system, native or rendered, are
uncalibrated dBFS, never SPL.

BatDetect2 has been **evaluated and deliberately not adopted as a live detector**, not
left unimplemented by omission. The evaluation harness that this sentence previously
called "not started" is done: `scripts/benchmark_batdetect2.py`,
`tests/test_batdetect2.py` and `BATDETECT2_EVALUATION.md`, measured on the Pi on
2026-08-05 (ADR-017). The `results/batdetect2-pi5.json` this sentence also used to
cite **does not exist and never has** — see the provenance warning in
`BATDETECT2_EVALUATION.md`; the figures survive, their raw artefact does not. What is
genuinely open is whether to promote the offline cascade in
`scripts/classify_clips_batdetect2.py` into a live, queued plugin against
`DeferredDetectorWorker` — an unmade decision, not a technical blocker.

Nothing shipped should be read as a substitute classifier: `ultrasonic-pass-v1` answers
"was there a bat pass here" with supporting measurements, not "which species".

### Deferred mode — as implemented (Milestone 5 item 3)

A Pi 5 benchmark measured a candidate heavy detector at p95 968 ms for a 0.5 s clip —
0.52x realtime — against 36-40x for the detectors that actually ship. This is exactly the
"real-time BatDetect2 is not sustainable" case this document anticipated, and settles that
no expensive model can run inline in the live detector path on this hardware.

`detectors/deferred.py` implements the bounded deferred-night queue this document asks
for, as a general capability of `DetectorWorker` rather than anything specific to one
model: `DeferredDetectorWorker` subclasses the worker every live detector already uses,
keeping its bounded queue, drop-and-count policy, stale-window rejection and circuit
breaker unchanged, and adds three things a queue meant to survive a whole capture session
needs on top of a queue meant to survive a few seconds of live windows:

- **Lower priority.** Analysis runs on a dedicated single-worker thread pool instead of
  the shared default pool every live detector's `asyncio.to_thread` call competes for, so
  an expensive job can never take a thread slot away from a real-time detector.
- **Honest lag.** Queue depth, oldest-queued-item age, items processed, items dropped
  (queue-full, stale, and abandoned-on-shutdown, each counted separately) and processing
  lag are all in `GET /api/v1/detectors` via `DeferredDetectorWorker.snapshot()`'s
  `deferred` object, and as `oo_detector_deferred_*` Prometheus gauges alongside the
  existing `oo_detector_*` metrics.
- **Deterministic shutdown.** `stop()` keeps draining already-queued windows for
  `deferred_shutdown_drain_timeout_s`, then abandons whatever remains: each abandoned
  window is still released through the same `on_window_done` hook the processed path
  uses, and the count is logged. One caveat stated rather than hidden: a window already
  handed to the executor thread cannot be interrupted mid-analysis, so shutdown always
  lets that one in-flight item finish before abandoning the rest of the queue — what
  happens to each item is deterministic, its wall-clock duration is not.

A plugin opts in by convention — setting `self.deferred = True` — rather than through a
required `DetectorPlugin` protocol member, specifically so the three shipped detectors
never need to change to keep conforming to the protocol.

**Lease lifetime**, addressed rather than quietly worked around: a deferred window can sit
queued for a large fraction of a session, far longer than the roughly 60 s lease the live
path grants via `station.py`. `DeferredDetectorWorker` does not invent a long-lived lease
policy of its own — it has no visibility into `TransientAssetStore` — but it does guarantee
the hook shape a caller needs to implement one safely: `on_window_admitted` fires exactly
once, synchronously, the instant a window is accepted into the queue, and `on_window_done`
is guaranteed to fire exactly once later, on every removal path (processed, dropped as
stale, or abandoned at shutdown). A caller wanting a long-lived lease grants it in the
first hook and releases it in the second; a window rejected at `offer()` (queue full) never
had a lease granted in the first place, matching the convention the live path already uses.

Tested with a synthetic slow plugin (`tests/test_deferred.py`), not BatDetect2 — there is
still no BatDetect2 adapter, per ADR-017, and this capability makes no assumption about
which model eventually uses it. The capability is not yet wired into `station.py`: no
shipped plugin declares itself deferred, so there is nothing for it to run against yet.
`deferred_enabled` defaults to `False`.

## Known limitation: fixed for new detections; historical rows need repair (ADR-032)

Before ADR-032, `BirdNetDetector._band_for` sorted each candidate into `in_range`,
`uncommon` or `out_of_range` by the range model's occurrence probability and applied a
different confidence threshold to each, but never excluded a species outright, and
treated a *missing* prior as the *easiest* case rather than the hardest. Measured on the
live station's own database, 2026-08-08, with the location filter enabled and coordinates
correct (the development station — **the range model itself works**: Common Woodpigeon
0.995, European Goldfinch 0.781, Western House Martin 0.771, "Engine" 4e-06, so this was
never a misconfiguration): a *Flammulated Owl* at `occurrence_probability` 8e-06 scored
0.959 and was admitted, and 202 of 5833 named detections (3.5%) had `occurrence=None` and
were judged against the *easiest* threshold rather than the strictest.

Both are now fixed in `birdnet.py`:

**(a) Near-zero prior now suppresses outright, not just a higher bar.** BirdNET scores are
not calibrated probabilities — enforced everywhere else in this codebase — so a high score
on a species the range model puts at ~0 for this location and week is evidence the score
carries no information for that species, not evidence of the bird. Raising 0.90 to 0.97
would only move the boundary; a Eurasian Jackdaw at 0.617 and a Flammulated Owl at 0.959 are
not separable by any single cutoff. `_band_for` now returns an `implausible` band with an
unreachable (`math.inf`) threshold when `occurrence <= birdnet_plausibility_floor`
(default `0.0005`), derived from the measured data: implausible owls sit at
8e-06–1.6e-04, while a genuine, seasonally-uncommon Tawny Owl sits at 0.019253 — the floor
keeps the Tawny Owl (`_band_for(0.019253, ...)` still lands in `out_of_range`, needing 0.90,
which its measured 0.974 clears) and rejects the owls.

**(b) A missing prior now gets the strictest bar, not the easiest.** `_band_for` takes an
explicit `range_model_loaded` argument instead of inferring "no range model" from
`occurrence is None`. With the range model loaded but silent about a species, the result is
a `no_prior` band using `threshold_out_of_range` (0.90) — the strictest available, not
`threshold_in_range` (0.55). With no range model loaded at all, the old uniform behaviour
(`unfiltered`, `threshold_in_range`) is unchanged; that case is still defensible, since there
is genuinely no plausibility information to act on.

**Counters.** `_suppressed_out_of_range` previously incremented for every candidate that
fell below its band's threshold, including `uncommon` ones, so it was not a count of
suppressed *out-of-range* species. It is now split into
`_suppressed_implausible_prior`, `_suppressed_no_prior`, `_suppressed_uncommon` and
`_suppressed_out_of_range` (each counting exactly its own band), surfaced via
`BirdNetDetector.plausibility_snapshot()` and as `oo_birdnet_suppressed_total{reason=...}`
in `api/metrics.py`.

**What is not fixed by this change: the ~5833 already-persisted rows, three of them are
consumers.** Going forward, an implausible candidate is suppressed by the detector before a
row is ever created, so the API, the MQTT publisher and the ESP32 wall display are
automatically consistent — there is nothing for any of them to filter. But the ~202
historical rows already in the database were written under the old logic and still read as
plain fact everywhere, including the living-room display. `oo detections
reconcile-plausibility` (dry-run by default, `cli.py`) re-evaluates stored detections
against the *current* range model and *floor*, and on `--apply` writes a
`native_result.plausibility_review` block recording the finding — it never deletes a row or
overwrites the original `native_result`, mirroring `oo history reconcile-streams`'s
`detail.reconciliation` pattern. **This agent's territory did not include the API, the MQTT
publisher or the ESP32 firmware**, so nothing yet reads `plausibility_review` to actually
hide a flagged historical row from a consumer — that is a follow-up, tracked in
`HANDOVER.md` §6.3 item 0, and until it lands, a flagged historical row is still visible
everywhere it was before, just auditable as flagged in the database.
