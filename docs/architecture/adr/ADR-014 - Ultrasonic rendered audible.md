---
aliases:
  - ADR-014
tags:
  - adr
---
# ADR-014: Ultrasonic evidence is rendered into the audible band for human review
**Status:** active; the decision's "time-expansion or heterodyne" is in practice *both* —
the configured set grew to `both` and `none`, and `both` is the default.

**Decision:** Alongside the native evidence clip, ultrasonic detections get an audible
derivative — time-expansion or heterodyne, configurable — stored as a distinct media kind
(`audible_ultrasonic`) and marked as not amplitude-comparable to the native recording.

**Reason:** An ultrasonic clip is unfalsifiable by ear as recorded. Rendering it audible is
what makes the detector's false positives *checkable* by a human, which is the only review
mechanism that exists until a classifier does.

**Constraint:** The rendering is peak-normalised and high-pass filtered, so its levels carry
no information about the original amplitude and must never be presented as if they did.
Levels anywhere in this system are uncalibrated; no sound-pressure calibration procedure
exists.

**Reviewed 2026-08-29:** the decision holds, and the constraint is enforced end to end
rather than merely asserted. `audio/ultrasound.py` implements both methods; `render()`
honours `ultrasonic_audible_method`, which is now
`Literal["time-expansion", "heterodyne", "both", "none"]` defaulting to `both`
(`config.py:427`), so a bat pass normally gets two derivatives, not one. Both paths
high-pass first and peak-normalise to −3 dBFS afterwards, recording
`normalisation_gain_db` and `amplitudes_comparable_to_native: False` in the asset detail
rather than hiding the gain. `clips.py:542` writes them as kind `audible_ultrasonic` and
stamps `"authoritative": False`; the API passes that detail through
(`api/app.py:2861`) and the drawer turns it into a "processed" chip whose tooltip says
the levels are not comparable (`web/src/components/DetectionDrawer.tsx:452`). One
refinement worth recording: the trigger is the *measured* peak frequency, not the
detector that fired — `clips.py:421` renders whenever `peak_frequency_hz` clears
`ultrasonic_audible_min_peak_hz` (15 kHz), so a high-frequency event reaches a reviewer's
ear whatever proposed it. The reason's "until a classifier does" has partly come due:
[[ADR-045 - Refinement runner|ADR-045]] shipped the BatDetect2 cascade as a scheduled refinement runner, but
propose-only, so the audible rendering remains the review mechanism rather than a
superseded one. The live heterodyne monitor is out of scope here and stays a separate
implementation under [[ADR-018 - Live heterodyne, one oscillator|ADR-018]]. The uncalibrated-levels constraint is unchanged and
repeated consistently in [[AUDIO_PIPELINE]] and [[DETECTOR_STRATEGY]]. Verified against
the repository — `tests/test_ultrasound.py` carries 25 tests over both renderers and the
clip writer — and against the running station, where a bat pass recorded on 2026-08-29
carries two `audible_ultrasonic` assets beside its native clip, one per method, each
stamped `authoritative: false` and `amplitudes_comparable_to_native: false` in its detail.

---
Part of the [[ADRS|Architecture Decision Record index]].
