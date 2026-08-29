---
aliases:
  - ADR-003
tags:
  - adr
---
# ADR-003: Immutable time-addressed windows
**Status:** active.

**Decision:** Detectors consume immutable audio windows by reference.

**Reason:** Models have different window sizes, can run asynchronously, and may need retries without reading live audio directly.

**Reviewed 2026-08-29:** the decision holds. Window PCM is marked read-only as the window is cut (`src/open_observatory/segmenter.py:131`), detectors are handed the object under a lease, and no detector opens the ring or the device. Two things the wording invites a reader to get wrong. Windows are addressed by *frame*, not by timestamp: `start_frame`/`end_frame` are the authoritative bounds and the UTC fields are derived from them (`src/open_observatory/audio/contracts.py:310`), which matters because [[ADR-063 - Clock re-anchor|ADR-063]] re-anchors UTC after a clock step and [[ADR-072 - Accepted crystal drift|ADR-072]] accepts that it drifts against the microphone's crystal. And nothing retries a window — a failed analysis is counted and dropped (`src/open_observatory/detectors/base.py:221`); re-analysis after the fact reads evidence clips from disk instead ([[ADR-045 - Refinement runner|ADR-045]]).

---
Part of the [[ADRS|Architecture Decision Record index]].
