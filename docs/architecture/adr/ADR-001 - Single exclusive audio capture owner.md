---
aliases:
  - ADR-001
tags:
  - adr
---
# ADR-001: Single exclusive audio capture owner
**Decision:** Only `capture` opens the physical ALSA device.

**Reason:** Multiple detector processes opening one USB source is fragile, device/plugin dependent, and does not provide deterministic shared timing or retryable windows.

---
Part of the [[ADRS|Architecture Decision Record index]].
