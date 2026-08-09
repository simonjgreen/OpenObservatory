# Sources and Assumptions

Checked during preparation on 4 August 2026:

- Open Acoustic Devices describes AudioMoth USB Microphone as a Raspberry Pi/Linux-compatible full-spectrum USB microphone supporting sample rates up to 384 kHz.
- BirdNET-Analyzer and the BirdNET Python library publish MIT-licensed source code, with distributed model assets under separate CC BY-NC-SA 4.0 terms. Installation and redistribution must preserve that distinction.
- BatDetect2 describes itself as a deep-learning model/software package for detecting and classifying bat echolocation calls in high-frequency recordings.
- BirdNET-Go is a relevant implementation reference for continuous Raspberry Pi soundscape analysis and recent multi-model operation, but Open Observatory remains independently specified.

## Assumptions requiring target-device verification

Every item below has since been checked on the real device. Resolutions added
2026-08-09; the measurements themselves live in
`docs/operations/TARGET_DIAGNOSTICS.md` and
`docs/detectors/BATDETECT2_EVALUATION.md`.

- ~~Exact AudioMoth USB firmware and switch configuration.~~ **RESOLVED.**
  `AudioMoth-USB-Microphone` 1.3.2, uid `2453800264933F8F`, shipped with the
  right firmware. `USB/OFF` presents `10c4:0002` (HID, **no ALSA card at all**);
  `DEFAULT` and `CUSTOM` present `16d0:06f3` (USB Audio Class).
- ~~ALSA-visible sample rates, formats and channel count.~~ **RESOLVED.** Exactly
  one hardware profile: 384 kHz, mono, S16_LE. Every lower rate is unsupported
  natively; `arecord -r 48000` only *appears* to work because ALSA's `plug` layer
  silently resamples, which the capture path refuses.
- ~~Sustained Pi 5 capture stability at 384 kHz.~~ **RESOLVED over hours, not over
  days.** Continuity 0.9990–0.9997, ~29% of four cores with all three detectors.
  **The 72-hour soak that would make this a durable claim has never been run**,
  and there is an open capture-gap investigation.
- **Whether USB/host power and enclosure produce electrical or fan noise.**
  *Partly open.* A weak PSU was found to cause intermittent USB enumeration and
  was replaced. Enclosure and fan noise as such have not been characterised.
- ~~BirdNET and BatDetect2 ARM64 dependency compatibility.~~ **RESOLVED.** BirdNET
  runs via `ai-edge-litert` (not `tflite-runtime`, which has no cp312 aarch64
  wheel and needs NumPy 1.x). BatDetect2 installs cleanly on aarch64/cp312.
- ~~BatDetect2 real-time throughput and useful UK taxonomy on this hardware.~~
  **Throughput RESOLVED: not viable.** p95 968 ms per 0.5 s clip, 0.52× realtime,
  +459 MB RSS. **Taxonomy/accuracy is NOT resolved** — the only evidence is three
  0.5 s clips shipped for the library's own tests, through a confounded resampling
  path, and the top-ranked detection matched the label on one of the three.
- ~~Required external storage capacity and retention preferences.~~ **RESOLVED.** A
  465.8 GB USB SSD mounted at `data/clips` (ADR-021), after a busy bat night wrote
  15 GB against a 20 GB budget. Retention is NVR-style tiered age-out with
  detection metadata kept forever (ADR-026).

## Licensing warning

This seed is not legal advice. Claude Code must add a third-party notices workflow and must not bundle model files until their exact licence and distribution conditions are reviewed.

**Status 2026-08-09.** No model binaries or third-party model code are committed,
and none ever have been. This code is Apache-2.0. BirdNET's *code* is permissively
licensed but its released model assets are **CC BY-NC-SA 4.0**, which prohibits
commercial use; they are fetched by the explicit operator step `oo models fetch`,
which shows the licence before downloading, checksums against
`models/manifest.tsv` (ADR-006), and surfaces what is installed and under what
terms at `GET /api/v1/models` and in the UI. BatDetect2's licence is **CC-BY-NC-4.0
uniformly across code, weights and example audio** — there is no split the way
there is for BirdNET — so nothing of it is committed either (ADR-017).

One third-party *recording* is committed, deliberately and after an individual
licence check: `tests/fixtures/audio/erithacus_rubecula_XC441752.mp3`, a European
Robin song from Xeno-canto (XC441752, recordist Jan Cibulka), under **CC BY-SA
4.0** — not the NC-SA terms many Xeno-canto recordings carry. Its required
attribution is in `tests/fixtures/audio/ATTRIBUTION.md` with a checksummed
`manifest.tsv` alongside. Xeno-canto licences vary per recording; check each one.

A general "third-party notices" workflow beyond `/api/v1/models` and those
attribution files does **not** exist and is still owed.
