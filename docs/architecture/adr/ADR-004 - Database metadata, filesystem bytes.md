---
aliases:
  - ADR-004
tags:
  - adr
---
# ADR-004: PostgreSQL canonical metadata, filesystem canonical clip bytes
**Status:** the split is active and load-bearing; the database half is **partly superseded
by [[ADR-007 - SQLite in developer mode|ADR-007]]** — SQLite is the only database this project has ever exercised.

**Decision:** Metadata belongs in PostgreSQL; large media belongs on SSD with checksummed database references.

**Reviewed 2026-08-29:** the *filesystem canonical* half holds and is implemented.
`media_asset` stores `storage_uri`, `byte_length` and `sha256` and never the bytes
(`src/open_observatory/db/models.py:260`); a clip is written to a `.partial` name and
`replace()`d into place (`clips.py:463`); the serve path refuses any `storage_uri` that
resolves outside `clip_dir` and answers 410, not 404, when the file is gone
(`api/app.py:2096`); and both directions of divergence have a check — a row without a file
is reconciled by `media_repair.py` ([[ADR-057 - Evidence rows must be checkable|ADR-057]]), a file without a row is reported,
never deleted, by `RetentionSweeper.find_orphans()` (`retention.py:1198`).
[[ADR-021 - Clips on their own device|ADR-021]] moved the clip tree onto its own device without touching any of this, which
is the split doing its job.

Two words in the decision now overstate it. **"PostgreSQL"** is aspirational:
`resolved_database_dsn` falls back to SQLite, no test in this repository runs against
PostgreSQL, and the `postgres:16` service in `docker-compose.yml` is [[ADR-008 - systemd, not Compose|ADR-008]]'s
deferred production target rather than something that runs. The schema has since
acquired SQLite-specific tuning — the `media_asset` partial indexes are declared with
`sqlite_where=` only, so on PostgreSQL they would be built as full indexes ([[ADR-062 - Retention walks live assets|ADR-062]])
— so the swap is less configuration-only than [[ADR-007 - SQLite in developer mode|ADR-007]] assumes. **"Checksummed"**
promises more than it delivers: the `sha256` is computed once at write and stored, and
nothing here ever recomputes it. It is published to callers (`api/app.py:2864`) but
never verified, unlike the model assets in `src/open_observatory/models.py`, which are
checked against `models/manifest.tsv` at install and whenever `oo models status` or
`GET /api/v1/models` asks (`models.py:80`) — though not at load either: BirdNET digests the
file it opened and records that as provenance without comparing it to anything
(`detectors/birdnet.py:387`). A missing clip is detected; a corrupted one is not.

---
Part of the [[ADRS|Architecture Decision Record index]].
