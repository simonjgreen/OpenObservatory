# Detector Strategy

## Bird detection

Initial adapter: BirdNET through an officially maintained library or BirdNET-Analyzer-compatible model path. The implementation must record exact code, model, label and range-model versions.

BirdNET source code is permissively licensed, while distributed model assets carry separate non-commercial/share-alike terms. Model installation must therefore be a separate, attributable step and the product must expose licence metadata.

Expected windowing is typically approximately three seconds at 48 kHz, but adapter metadata is authoritative.

## Bat detection

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
