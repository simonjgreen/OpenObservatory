---
aliases:
  - ADR-002
tags:
  - adr
---
# ADR-002: Highest practical native capture rate with derived audible stream
**Status:** active; the caveat is discharged. The device offers exactly one rate, so the
required audible-only fallback exists as degradation in the *consumers* of the native
stream, not as a lower capture rate.

**Decision:** Capture at the highest target-supported rate needed by ultrasonic analysis, then downsample for audible models.

**Reason:** Upsampling a 48 kHz stream cannot recover bat ultrasound. Downsampling a high-rate authoritative stream preserves both use cases.

**Caveat:** Actual AudioMoth/Linux mode and Pi stability must be measured. Audible-only fallback is required.

**Reviewed 2026-08-29:** the decision holds and is implemented as written. Capture opens
the native rate, `AudibleResampler` derives the 48 kHz stream, and both rings are built
side by side (`src/open_observatory/audio/resample.py`,
`src/open_observatory/station.py:614`). The audible detectors take
`audible_sample_rate`; `ultrasonic-pass-v1`, the ultrasonic spectrogram and the live
heterodyne take the native rate. Both halves of the caveat have since been measured.

**The mode.** The AudioMoth USB Microphone offers exactly one hardware profile —
384 kHz mono S16_LE, with 250, 192, 96 and 48 kHz all *unsupported* ([[TARGET_DIAGNOSTICS]]).
The four lower entries in `preferred_sample_rates` have therefore never been exercised on
this hardware: the device chose, not the ladder, and "highest practical" was never a
trade-off the station got to make.

**The stability.** 384 kHz sustained over 72.107 restart-free hours on 2026-08-25 —
continuity 99.9948%, 0.597 s of audio lost ([[MILESTONE_STATUS]] §Milestone 4.5).
[[ADR-073 - Five capture SLOs|ADR-073]] has since decomposed that single number, and [[ADR-072 - Accepted crystal drift|ADR-072]] accounts for the
crystal drift inside it.

**The fallback** is real, but takes a different form from the one this ADR's wording
implies. It cannot be a lower capture rate, because the device has none; it is graceful
degradation in each consumer of the native stream. `UltrasonicDetector.initialise()`
raises `DetectorUnavailable` below 96 kHz (`detectors/ultrasonic.py:299`, exercised at
48 kHz by `TestUltrasonicDetector.test_unavailable_below_the_useful_rate` in
`tests/test_detectors.py`); the ultrasonic spectrogram is only built at ≥ 96 kHz
(`station.py:741`); `_build_heterodyne` refuses a rate it cannot evenly decimate and
records `heterodyne_unavailable_reason` for the API to report (`station.py:684`); and
`AudibleResampler` becomes a `passthrough` when source and target rates already match
(`resample.py:70`). A 48 kHz-only station would still capture, still run activity and
BirdNET, and say why the ultrasonic path is unavailable.

---
Part of the [[ADRS|Architecture Decision Record index]].
