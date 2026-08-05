# Detector Strategy

This document is the pre-implementation design spec, written before any detector existed.
It is kept as-is below to record intent. See **"As implemented"** at the end for the three
detectors that actually shipped, their real configuration keys and defaults, and where the
shipped system differs from what this document proposed — most notably, that BatDetect2
below was never implemented.

## Bird detection

Initial adapter: BirdNET through an officially maintained library or BirdNET-Analyzer-compatible model path. The implementation must record exact code, model, label and range-model versions.

BirdNET source code is permissively licensed, while distributed model assets carry separate non-commercial/share-alike terms. Model installation must therefore be a separate, attributable step and the product must expose licence metadata.

Expected windowing is typically approximately three seconds at 48 kHz, but adapter metadata is authoritative.

## Bat detection

*As implemented: not this.* A non-ML pulse-train detector (`ultrasonic-pass-v1`) shipped
instead and is running on the target device. BatDetect2 below remains planned, not
implemented — see "As implemented".

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
| `range_threshold` (constructor default) | `0.03` |
| `threshold_in_range` | `0.55` |
| `threshold_uncommon` | `0.75` |
| `threshold_out_of_range` | `0.90` |

Plausibility is banded by whether the range/occurrence model considers a species likely at
this station's location and week: species in range clear `threshold_in_range` (0.55),
uncommon species must clear 0.75, and species the model considers out of range must clear
0.90 before being reported at all. With no range model loaded, every candidate is judged
against `threshold_in_range` only — there is no invented prior.

The shipped default is `birdnet_use_location_filter=False` with no coordinates, so out of
the box every species is judged on confidence alone. At the development station the coordinates and
the filter are set in the Pi's `config/runtime.env`, which is not in version control
because the data model treats station location as access-controlled — so the repository
defaults and the running station legitimately differ here. Enabling it is not cosmetic:
before it was on, this station stored "Great Bittern" and "Spotted Crake" at 0.9
confidence in a the development area garden.

Fixture testing is partial. `tests/test_detectors.py` exercises BirdNET's logic — week
calculation, label parsing, plausibility-band thresholds, licence metadata, missing-model
handling — but there is no fixture test that runs inference on a known recording and
asserts a known species. `docs/delivery/MILESTONE_STATUS.md` records this explicitly as the
Milestone 3 exit gate being only partially met: a live demonstration produced a real
identification (*Columba palumbus*, Common Woodpigeon) within minutes of starting, but that
is not the repeatable fixture test the gate requires. Per this project's rule that a
detector is only "supported" once an automated fixture test passes, `birdnet-v2.4` has not
yet cleared that bar.

### `ultrasonic-pass-v1` — replaces the BatDetect2 plan (ADR-013)

BatDetect2, proposed above under "Bat detection", was never implemented. What shipped
instead is a non-ML pulse-train detector operating on the native 384 kHz stream. It detects
bat *passes* — never species — and the normaliser rejects any detection from a
non-taxonomic plugin that carries a species name.

| Config key | Default |
|---|---|
| `band_hz` (constructor default) | `(15_000.0, 125_000.0)` |
| `min_snr_db` | `12.0` |
| `min_pulse_ms` | `1.5` |
| `min_pulses_per_pass` | `3` |

A detection reports a measured frequency band, pulse count and SNR, and nothing more. A
frequency band is evidence a human can interpret, not an identification: the detector's own
tests and ADR-013 both note that 18–21 kHz is genuinely ambiguous between noctule and
bush-cricket, and the shipped code does not attempt to resolve that ambiguity.

Two honesty-relevant limitations, stated in ADR-013 and in `TARGET_DIAGNOSTICS.md`:

- it has a known false-positive rate on broadband transients such as wind and handling
  noise;
- it has no night scheduler and currently runs 24 hours a day, including doing ultrasonic
  work at noon when nothing is flying.

Evidence clips for this plugin get an audible derivative via
`src/open_observatory/audio/ultrasound.py` (time-expansion or heterodyne — see
`docs/audio/AUDIO_PIPELINE.md`, "Ultrasound-to-audible rendering"), because an ultrasonic
clip as recorded is not checkable by ear. That rendering is peak-normalised and must not be
read as sound-pressure information: all levels in this system, native or rendered, are
uncalibrated dBFS, never SPL.

BatDetect2 itself remains a planned, unimplemented Milestone 5 item (`acoupi_batdetect2`
evaluation harness "not started" per `MILESTONE_STATUS.md`). Nothing shipped should be
read as a substitute classifier for it: `ultrasonic-pass-v1` answers "was there a bat pass
here" with supporting measurements, not "which species".
