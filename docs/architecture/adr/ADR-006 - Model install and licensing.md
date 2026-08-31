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

**Closed 2026-08-29, the disclosure gap this ADR's promise had:** the licence
machinery was manifest-only, so a model acquired any other way was invisible to it —
BatDetect2 arrives as `pip install batdetect2==1.3.1` and its CC-BY-NC-4.0 terms
reached no surface at all, and no component in `web/` fetched `/api/v1/models`
either, so "surfaced in the UI" was unmet even for BirdNET. Package-acquired models
are described beside file-acquired ones **in the repository — not yet on the station,
where this is uncommitted and undeployed (checked 2026-08-30)**; (`PACKAGE_MODELS` in
`src/open_observatory/models.py`, tagged `kind: "package"`), `/api/v1/models`
returns both, `oo models status` prints both, and a diagnose-depth panel renders
them. A package is deliberately *not* given the vocabulary of a file: it carries no
checksum and is never called "verified" — the station can see that it imports and
what version is present, not that the bytes are the ones this ADR's reasoning was
checked against.

**Reviewed 2026-08-29:** the rule holds without exception — nothing under `models/`
is tracked but `manifest.tsv`, and `oo models fetch` names each asset's licence and
requires a confirmation. A section lead-in that belonged to the old single-file
`ADRS.md` ("The following ADRs record deviations taken during the Milestone 0–3
debug slice…") was swept into this file by the 2026-08-29 split; it has been moved
to the index, where "the following ADRs" means something.

---
Part of the [[ADRS|Architecture Decision Record index]].
