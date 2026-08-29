---
aliases:
  - ADR-001
tags:
  - adr
---
# ADR-001: Single exclusive audio capture owner
**Status:** active.

**Decision:** Only `capture` opens the physical ALSA device.

**Reason:** Multiple detector processes opening one USB source is fragile, device/plugin dependent, and does not provide deterministic shared timing or retryable windows.

**Reviewed 2026-08-29:** the decision holds. A capture PCM is opened in exactly two
modules — `audio/alsa_source.py:216`, the capture owner, and `audio/probe.py:295`, the
diagnostic — and `AlsaSource` is constructed in exactly two places outside the tests:
`station.py:488` for the running station, and `cli.py:248` for `oo audio test-capture`,
an operator command run against a stopped station. Detectors, the segmenter, the live listen path
([[ADR-018 - Live heterodyne, one oscillator|ADR-018]], [[ADR-019 - Chunked-WAV live playback|ADR-019]]) and the refinement runner ([[ADR-045 - Refinement runner|ADR-045]]) all take audio by
window reference or from stored clips; none of them opens a PCM. Two clarifications the
original wording does not carry:

- `capture` is a *role*, not a process. Since [[ADR-008 - systemd, not Compose|ADR-008]] and [[ADR-009 - In-process event bus|ADR-009]] the owner is
  `Station` plus `AlsaSource` inside the single `oo serve` unit, and the second unit
  added by [[ADR-045 - Refinement runner|ADR-045]], `open-observatory-refine.service`, never opens the device.
- The probe genuinely opens the hardware, by design — it is the only way to tell a rate
  the device supports from one it silently substitutes. It defers rather than competes:
  the API checks whether the station holds the stream and returns
  `probe_skipped_because_in_use` instead of probing (`api/app.py:1380-1386`), the CLI
  reports such rates as `busy` rather than `unsupported` (`cli.py:212`), and both the CLI
  and [[DEPLOYMENT_AND_OPERATIONS]] tell the operator to stop the station first.

Exclusivity therefore rests on ALSA refusing the second open, plus those guards. There
is no lock file and no automated test asserts the invariant.

---
Part of the [[ADRS|Architecture Decision Record index]].
