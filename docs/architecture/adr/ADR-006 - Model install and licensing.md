---
aliases:
  - ADR-006
tags:
  - adr
---
# ADR-006: Separate model installation and licensing
**Status:** active.

**Decision:** Do not bundle third-party model binaries by default.

**Reason:** Code and model licences differ, notably for BirdNET model assets.

**Reviewed 2026-08-29:** the rule holds without exception — nothing under `models/`
is tracked but `manifest.tsv`, and `oo models fetch` names each asset's licence and
requires a confirmation. A section lead-in that belonged to the old single-file
`ADRS.md` ("The following ADRs record deviations taken during the Milestone 0–3
debug slice…") was swept into this file by the 2026-08-29 split; it has been moved
to the index, where "the following ADRs" means something.

---
Part of the [[ADRS|Architecture Decision Record index]].
