---
aliases:
  - ADR-003
tags:
  - adr
---
# ADR-003: Immutable time-addressed windows
**Decision:** Detectors consume immutable audio windows by reference.

**Reason:** Models have different window sizes, can run asynchronously, and may need retries without reading live audio directly.

---
Part of the [[ADRS|Architecture Decision Record index]].
