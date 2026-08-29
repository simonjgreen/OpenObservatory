---
aliases:
  - ADR-010
tags:
  - adr
---
# ADR-010: An owned acoustic-activity detector is the first detector plugin
**Status:** active.

**Decision:** Ship `activity-v1`, a band-limited onset/energy segmentation detector with no
model dependency and no taxonomic output, as the reference implementation of
`DetectorPlugin`. BirdNET is an optional adapter that self-reports `unavailable` until its
model assets are installed by the operator.

**Reason:** [[ADR-006 - Model install and licensing|ADR-006]] forbids bundling BirdNET assets, so a build with no operator action must
still exercise the whole window → detector → normaliser → clip → event path. `activity-v1`
does that and is independently useful for diagnosing microphone and gain problems.

**Constraint:** `activity-v1` must never emit a species label, a scientific name, or a
canonical taxon id. Its `rank` is `null` and its group is `acoustic_event`.

**Reviewed 2026-08-29:** the decision and the constraint both hold, and the constraint is
enforced rather than only written down — `NON_TAXONOMIC_PLUGINS` holds `activity-v1` and
`_check_claims` raises `ClaimViolation` naming this ADR
(`src/open_observatory/normaliser.py:38,321`), covered by
`test_activity_detector_cannot_emit_a_species` (`tests/test_pipeline.py:221`). The path in
the reason above is one stage too long, and has been since the first implementation:
`clip_plugins` (`src/open_observatory/config.py:346`) has never listed `activity-v1`,
because it fires on ordinary energy blips several times a second. A build with no operator
action therefore exercises window → detector → normaliser → event on this detector, and
the clip stage on `ultrasonic-pass-v1` ([[ADR-013 - ultrasonic-pass-v1|ADR-013]]), which
also needs no model. The measured reason for the exclusion is in [[DETECTOR_STRATEGY]].

---
Part of the [[ADRS|Architecture Decision Record index]].
