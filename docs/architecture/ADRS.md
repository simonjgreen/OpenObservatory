# Architecture Decisions

Every material deviation from [[TECHNICAL_SPEC]] is recorded as an ADR.
**ADR numbers are referenced by number from source comments across `src/`,
`web/`, `firmware/`, `tests/`, `alembic/` and `config/example.env`.** Therefore:

- **Never delete an ADR**, including a superseded one. Mark it, keep it.
- **Never renumber an ADR.** The gaps at 031 and 036 are numbers that were
  reserved and not needed; a gap is harmless, a broken cross-reference is not.
- **One file per ADR**, under [`adr/`](adr/), named `ADR-NNN - Short slug.md`.
  Split out of a single 8,910-line document on 2026-08-29: every content-bearing
  line was verified identical across the move (7,372 lines, zero differences), and
  only blank lines and the `---` separators between entries were dropped.
- **The number is the identity; the slug is only a label.** The first split used
  each ADR's full sentence as its filename, which made the graph unreadable and
  the links unwieldy; the files were renamed to slugs the same day (longest now 51
  characters, was 152). The sentence still opens each file as its `# ADR-NNN:`
  heading, and this table's Decision column carries it too. Renaming a slug is
  safe — source comments cite the number, never the title — but **do it with
  `git mv` and rewrite every `[[…]]` that names the old file in the same commit.**
  Link as `[[ADR-045 - Refinement runner|ADR-045]]`; a bare `[[ADR-045]]` does not
  resolve.
- **This file is the index and the authoritative ordering.** Several ADRs were
  written out of numeric order — 016 between 011 and 012; 034 after 035; 043
  after 044; 046 after 047; 053 between 050 and 051; 056 after 059 — and the
  filenames now make that irrelevant. The table below is sorted properly.
- **Refer to an ADR as `[[ADR-045 - Refinement runner|ADR-045]]` in prose.** Each file carries an
  `aliases: [ADR-NNN]` property, so the short form resolves in Obsidian without
  repeating a 90-character filename; the table below uses ordinary relative links
  so it also renders on GitHub.
- New ADRs get a new file **and** a row here. **Adding a body without adding an
  index row is the failure mode that actually happens** — 037, 038 and 040 were
  missing until 2026-08-09, and **062 through 074 were missing until this split**.
  Check with:

      ls docs/architecture/adr/ | wc -l    # 72 files = 74 index rows - 031 and 036, which are reserved gaps with no body

## Index

**Each ADR now carries its own `**Status:**` line as its first body line, and that
line is the authority; this column is the digest of it.** Every ADR was reviewed
against the code and the live station on **2026-08-29**, one at a time, and that
is when the missing Status lines were written. The sentence this replaces claimed
statuses for 001–061 had been verified on 2026-08-09, which cannot have been true
of 059 (deployed 2026-08-10, and its first verification failed) or 061 (written
2026-08-14). "Superseded" never means deleted: the original reasoning is retained
and is often the most useful part.

**007 onward record deviations taken during the Milestone 0–3 debug slice on the
actual target device** — see [[GAP_REPORT]] for the findings that motivated them.
(That sentence was the section lead-in of the single-file `ADRS.md`; the 2026-08-29
split swept it into ADR-006's file, where "the following ADRs" referred to nothing.)

| # | Decision | Status |
|---|---|---|
| [[ADR-001 - Single capture owner\|001]] | Single exclusive audio capture owner | active |
| [[ADR-002 - Native rate, derived audible\|002]] | Highest practical native rate, with a derived audible stream | active |
| [[ADR-003 - Immutable windows\|003]] | Immutable time-addressed windows | active |
| [[ADR-004 - Database metadata, filesystem bytes\|004]] | PostgreSQL canonical metadata, filesystem canonical clip bytes | **partly superseded by [[ADR-007 - SQLite in developer mode\|ADR-007]]** — SQLite is the only database actually exercised |
| [[ADR-005 - No biodiversity score\|005]] | No authoritative composite biodiversity score in v1 | active |
| [[ADR-006 - Model install and licensing\|006]] | Separate model installation and licensing | active |
| [[ADR-007 - SQLite in developer mode\|007]] | SQLAlchemy storage with SQLite in developer mode | active in its SQLite-default decision; its "no Alembic environment" position is **superseded by [[ADR-035 - Alembic environment\|ADR-035]]**, its "`create_all()` at startup" position by **[[ADR-042 - Migrations run in deploy.sh\|ADR-042]]**, and its Constraint (retention and review gated on PostgreSQL, no concurrent writers) by what shipped |
| [[ADR-008 - systemd, not Compose\|008]] | Native systemd deployment for the debug slice; Compose deferred | active; the single unit is now two — [[ADR-045 - Refinement runner\|ADR-045]] gave the refinement runner its own timer-driven unit |
| [[ADR-009 - In-process event bus\|009]] | In-process event bus behind a transport-neutral protocol | active |
| [[ADR-010 - activity-v1, the first plugin\|010]] | An owned acoustic-activity detector is the first plugin | active |
| [[ADR-011 - Debug UI is not the dashboard\|011]] | The debug UI is an observability surface, not the product dashboard | **partly superseded by [[ADR-016 - Debug UI is the dashboard's foundation\|ADR-016]]** |
| [[ADR-012 - One writer per WebSocket\|012]] | Two live channels, and exactly one writer per WebSocket | active; the single-writer rule now governs every WebSocket on the station, and the live surface has grown past two channels ([[ADR-019 - Chunked-WAV live playback\|ADR-019]], [[ADR-022 - HTTP retune control\|ADR-022]], [[ADR-038 - Display push channel\|ADR-038]]) |
| [[ADR-013 - ultrasonic-pass-v1\|013]] | `ultrasonic-pass-v1`, a non-taxonomic detector on the native stream | active; its "no night scheduler" constraint is **closed** — a scheduler shipped in Milestone 5 (`schedule.py`). **Its Constraint overstates the enforcement**: `NON_TAXONOMIC_PLUGINS` holds only `activity-v1`, so what restrains this detector is its own construction plus [[ADR-049 - Sound categories are not species\|ADR-049]]'s shape check |
| [[ADR-014 - Ultrasonic rendered audible\|014]] | Ultrasonic evidence rendered into the audible band | active; the "or" is in practice "both" — `ultrasonic_audible_method` defaults to `both`, so a bat pass normally yields two renderings |
| [[ADR-015 - Anonymous read, auth deferred\|015]] | Anonymous read access, authentication deferred to Milestone 4 | **closed by [[ADR-034 - Authentication foundation\|ADR-034]]** — and it understated the exposure: three configurable credential-free paths plus three hardcoded ones, `/metrics` never gated at all, and it is write as well as read |
| [[ADR-016 - Debug UI is the dashboard's foundation\|016]] | The debug UI is the foundation of the product dashboard | active; supersedes part of [[ADR-011 - Debug UI is not the dashboard\|ADR-011]]. Its measurements of the *starting* state (styles.css line count, no component test library, `App.tsx` size) are historical — the test library and `App.tsx` were addressed in Milestone 4; the stylesheet only partly, since [[ADR-027 - Spacing and type scale\|ADR-027]] left ~700 lines unmigrated on purpose |
| [[ADR-017 - BatDetect2 as an optional adapter\|017]] | BatDetect2 is evaluated as an optional adapter; weights never bundled | active; **two mechanisms it names are overtaken** — the weights arrive by `pip install batdetect2`, not `oo models fetch`, so their CC-BY-NC-4.0 terms reach no API or UI surface, and [[ADR-045 - Refinement runner\|ADR-045]] rejected `DeferredDetectorWorker` as the route |
| [[ADR-018 - Live heterodyne, one oscillator\|018]] | Live ultrasonic monitoring is a second heterodyne, one oscillator per station | active |
| [[ADR-019 - Chunked-WAV live playback\|019]] | Live playback moved off Web Audio to a chunked-WAV stream | active; the debug UI's default listen path, amended by [[ADR-022 - HTTP retune control\|ADR-022]] (retune over HTTP) and [[ADR-055 - Timed recording pause\|ADR-055]] (503 while paused). Its three endpoint tests are skipped |
| [[ADR-020 - Non-live sources excluded\|020]] | Non-live-source detections excluded from browsing views by default | active |
| [[ADR-021 - Clips on their own device\|021]] | Evidence clips live on their own device, mounted over the clips directory | active; the ALSA read it blamed for sharing the default thread pool got its own executor in [[ADR-030 - ALSA ring and capture thread\|ADR-030]], which cites this ADR's evidence executor as the precedent; and the 300 GB clip-directory budget it raised is no longer the bound — [[ADR-026 - Tiered clip retention\|ADR-026]]'s watermark replaced it, and nothing calls `ClipManager.enforce_retention` |
| [[ADR-022 - HTTP retune control\|022]] | Live ultrasonic retuning as a plain HTTP control call | active |
| [[ADR-023 - The ESP32 inside observer\|023]] | The inside observer is an ESP32 counter-top display that never shows a score | active on the no-score rule and the device choice; its premise that "no code in this repository publishes to a broker" is **superseded by [[ADR-025 - MQTT and Home Assistant\|ADR-025]]**, its **polling decision is superseded by [[ADR-038 - Display push channel\|ADR-038]]** (push, with polling retained as an exercised fallback), and the **single-app-slot partition table it froze is superseded by [[ADR-050 - Display OTA slots\|ADR-050]]** |
| [[ADR-024 - Coverage bounded by frames\|024]] | Capture coverage is bounded by delivered frames, not a stream row's claim | active; its "no Alembic environment exists" note is **superseded by [[ADR-035 - Alembic environment\|ADR-035]]**, and the `ALTER TABLE` patcher it describes was deleted by [[ADR-042 - Migrations run in deploy.sh\|ADR-042]] |
| [[ADR-025 - MQTT and Home Assistant\|025]] | MQTT publisher and Home Assistant Discovery, off by default | active |
| [[ADR-026 - Tiered clip retention\|026]] | NVR-style tiered clip retention; detection metadata kept forever | active in its core rule (clip bytes age out, detection metadata does not); most specifics amended — sweep cadence by [[ADR-033 - Retention is paced\|ADR-033]], the "no Alembic migrations anywhere" note by [[ADR-035 - Alembic environment\|ADR-035]], the first/best-of-species exemption and the 30–90 d and 90+ d tiers by [[ADR-061 - Operator keep flag\|ADR-061]], tier age measurement by [[ADR-062 - Retention walks live assets\|ADR-062]], tier order by [[ADR-064 - Watermark tier first\|ADR-064]] |
| [[ADR-027 - Spacing and type scale\|027]] | A spacing/type scale for the web UI, applied to new surfaces | active on the scale itself; its "no new one-off pixel values" constraint has **not held** — the login ([[ADR-034 - Authentication foundation\|ADR-034]]), settings ([[ADR-048 - Web-configurable settings\|ADR-048]]), first-run and firmware CSS added since is built from raw pixel values and four hard-coded colours, and nothing enforces the rule |
| [[ADR-028 - One depth toggle\|028]] | Operator/diagnostic disclosure as one depth toggle | active; its description of `OperatorSummary` as four standing cards is historical — since 2026-08-09 the summary renders only what needs attention |
| [[ADR-029 - Retention UI, assumed API\|029]] | Retention UI built against an assumed API shape; review shipped minimally | active; the assumed endpoint now exists (`GET /api/v1/retention/status`) and the two were reconciled; its "`corrected_taxon_id` left for a future ADR" note is **closed by [[ADR-043 - Taxon correction\|ADR-043]]** |
| [[ADR-030 - ALSA ring and capture thread\|030]] | The ALSA ring is sized for scheduling jitter; the capture read owns its thread | active; [[ADR-039 - Confirmed loss, not deficit\|ADR-039]] and [[ADR-046 - Deficit is mostly drift\|ADR-046]] amend its estimator and its presentation, and [[ADR-033 - Retention is paced\|ADR-033]] qualifies its thread argument. **Confirmed on target** — 192,000-frame ring live, and the 72-hour soak ran on it |
| 031 | *(number reserved and not used — do not reuse)* | — |
| [[ADR-032 - Plausibility bands\|032]] | A near-zero occurrence prior suppresses a BirdNET candidate outright | active; completed by [[ADR-044 - Withdrawn detections\|ADR-044]], floor corrected for non-taxonomic classes by [[ADR-049 - Sound categories are not species\|ADR-049]], repair command fixed by [[ADR-070 - Threshold retune is not a defect\|ADR-070]] |
| [[ADR-033 - Retention is paced\|033]] | Retention is paced, because a dedicated thread is not the same as out of the way | active; amends [[ADR-026 - Tiered clip retention\|ADR-026]]'s sweep cadence (still 300 s). Its open item is closed by [[ADR-039 - Confirmed loss, not deficit\|ADR-039]]; its "pacing rather than making the sweep cheaper" reasoning was overtaken by [[ADR-061 - Operator keep flag\|ADR-061]] and [[ADR-062 - Retention walks live assets\|ADR-062]] |
| [[ADR-034 - Authentication foundation\|034]] | An authentication foundation closes [[ADR-015 - Anonymous read, auth deferred\|ADR-015]], off by default | active; its "no Alembic migration is written here" note is **closed** — revision `0003_auth_tables` was added afterwards. `auth_public_read_paths` now defaults to three paths, and the title's "cannot be reflashed" premise was overtaken by [[ADR-050 - Display OTA slots\|ADR-050]]'s OTA slots, though the exemption was kept deliberately |
| [[ADR-035 - Alembic environment\|035]] | A real Alembic migration environment, written before the PostgreSQL move | active; its startup-wiring follow-up ("`api/app.py` and `cli.py` calling Alembic instead of `create_all()`") is **closed by [[ADR-042 - Migrations run in deploy.sh\|ADR-042]]**; the index its revision `0002` added is **dropped again by [[ADR-062 - Retention walks live assets\|ADR-062]]** (revision `0011`) |
| 036 | *(number reserved and not used — do not reuse)* | — |
| [[ADR-037 - Prune the dead indexes\|037]] | Detection-table growth is real but slow; prune the five indexes nothing reads, and defer the rest | **options B and C accepted, implemented and deployed**; verified running on the station 2026-08-29 (revision `0004`). Options A and D–K remain open; three of the ADR's own revisit triggers have since fired — detections/day, SSD past 250 GB and the whole-history query ([[ADR-056 - Long-window history\|ADR-056]], [[ADR-074 - Evidence kept by value\|ADR-074]]). The research above the "B and C" section is preserved verbatim as the reasoning, not as current schema truth |
| [[ADR-038 - Display push channel\|038]] | The inside observer is pushed to over a lean WebSocket, and shows elapsed times | active; **supersedes [[ADR-023 - The ESP32 inside observer\|ADR-023]]'s polling decision**, though polling stays in the firmware as an exercised fallback. Extended by [[ADR-050 - Display OTA slots\|ADR-050]], which adds a fourth frame type to this wire, and by [[ADR-044 - Withdrawn detections\|ADR-044]], which suppresses withdrawn claims on it |
| [[ADR-039 - Confirmed loss, not deficit\|039]] | A deficit step is only lost audio once it fails to come back | active; closes the open item in [[ADR-033 - Retention is paced\|ADR-033]] and amends [[ADR-030 - ALSA ring and capture thread\|ADR-030]]'s estimator. **Confirmed on target** 2026-08-09 against its own pass criteria; its remaining question is resolved by [[ADR-046 - Deficit is mostly drift\|ADR-046]] |
| [[ADR-040 - Spectrograms only when watched\|040]] | The live view's pictures are drawn only while somebody is looking | active; the charter's "item 8 loses to items 1–2" precedent |
| [[ADR-041 - Ultrasonic spectrogram range\|041]] | The ultrasonic spectrogram gets its own floor/ceiling, measured on the live station | active |
| [[ADR-042 - Migrations run in deploy.sh\|042]] | `alembic upgrade head` wired into `deploy/deploy.sh`; `create_all()`/the ALTER TABLE patcher retired from startup | active; closes [[ADR-035 - Alembic environment\|ADR-035]]'s remaining follow-up. Its "no longer reachable from any production code path" claim about `create_all()` is false — [[ADR-045 - Refinement runner\|ADR-045]]'s `oo refine run`/`oo refine status` reintroduced it; see the ADR's 2026-08-29 note |
| [[ADR-043 - Taxon correction\|043]] | Taxon correction closes the review workflow; a human's ear outranks a machine's | active; closes the open item in [[ADR-029 - Retention UI, assumed API\|ADR-029]] |
| [[ADR-044 - Withdrawn detections\|044]] | A withdrawn detection is marked in the record and suppressed on the claim surfaces; the BirdNET week index is confirmed correct | active; completes [[ADR-032 - Plausibility bands\|ADR-032]]; flag scope narrowed by [[ADR-049 - Sound categories are not species\|ADR-049]] |
| [[ADR-045 - Refinement runner\|045]] | The refinement runner is a separate, CPU-fenced process, and the BatDetect2 cascade may only propose | active; promotes [[ADR-017 - BatDetect2 as an optional adapter\|ADR-017]]'s offline cascade to a scheduled job without adopting it as a source of station claims. Its schema-rollback command and its `retention.py` symbol names are stale — see the 2026-08-29 notes in the file |
| [[ADR-046 - Deficit is mostly drift\|046]] | The frame deficit is 98% crystal drift, and "audio lost" must not show it | active; resolves the open question in [[ADR-039 - Confirmed loss, not deficit\|ADR-039]] and amends [[ADR-030 - ALSA ring and capture thread\|ADR-030]]'s presentation. The 72-hour caveat is closed (2026-08-29): the residual was real loss from the pre-[[ADR-060 - A stalled read is a dead stream\|ADR-060]] era, and the decomposition now holds at 72 h — see the 2026-08-29 note in the ADR body |
| [[ADR-047 - The repository ships no site\|047]] | Site parameters are runtime state, managed through the web UI; the repository ships no site | active; widened by [[ADR-048 - Web-configurable settings\|ADR-048]] from site identity to the whole of `Settings` |
| [[ADR-048 - Web-configurable settings\|048]] | Every setting is web-configurable, in three declared tiers, with the exclusions named | active; extends [[ADR-047 - The repository ships no site\|ADR-047]]'s mechanism rather than replacing it |
| [[ADR-049 - Sound categories are not species\|049]] | BirdNET's eleven sound categories are not species: no clip for human speech, no bird claim, no plausibility floor | active; corrects [[ADR-032 - Plausibility bands\|ADR-032]]'s floor and [[ADR-044 - Withdrawn detections\|ADR-044]]'s flag for non-taxonomic classes |
| [[ADR-050 - Display OTA slots\|050]] | The counter-top display gets two OTA app slots and updates itself from the station, with rollback | active, **flashed and verified on hardware 2026-08-09**, including a deliberate rollback drill; changes the partition table [[ADR-023 - The ESP32 inside observer\|ADR-023]] froze, and adds one frame to [[ADR-038 - Display push channel\|ADR-038]]'s wire |
| [[ADR-051 - Playhead as an interval\|051]] | The spectrogram says where the sound you are hearing is, as a measured interval rather than a line | active; frontend only, offset measured against a ground-truth rig rather than assumed; its impossible-reading banner found [[ADR-063 - Clock re-anchor\|ADR-063]]'s clock step |
| [[ADR-052 - Near-miss ledger\|052]] | A counter is not a diagnostic — record what BirdNET proposed and refused, in a bounded ring with per-band score histograms | active; the tuning diagnostic [[ADR-032 - Plausibility bands\|ADR-032]]'s counters could not provide, on by default at 2 µs per candidate. Its 512-species ledger bound is now exhausted on the station (334 species omitted) |
| [[ADR-053 - Grouping above species\|053]] | Taxonomic grouping above species: genus is free, family is a data dependency | **proposed, not implemented** — recorded so the tempting wrong answer is refused in writing; re-confirmed unimplemented 2026-08-29 |
| [[ADR-054 - Responsive layout\|054]] | Responsive layout is intrinsic first, breakpoints last, and overflow is never hidden | active; frontend only; fixes an unreachable GO LIVE button and removes the `overflow-x: hidden` that concealed it. One argued exception since: the wall clock is hidden below 640 px |
| [[ADR-055 - Timed recording pause\|055]] | The operator can pause recording for a chosen time — and it expires, survives a restart, and is recorded as a pause | active; makes the charter's privacy constraint operable rather than aspirational |
| [[ADR-056 - Long-window history\|056]] | History longer than a day is a different question, a different shape and a different table | design spike; range grammar implemented, roll-up and the period/season views proposed only |
| [[ADR-057 - Evidence rows must be checkable\|057]] | A row that claims evidence must be checkable; reconcile the ones that lie, and keep "missing" distinct from "reclaimed" | active. 8,067 of 48,941 live `media_asset` rows (16.5%, 20.59 GB) claimed clips that `ClipManager.enforce_retention` had unlinked without marking them — **not** [[ADR-021 - Clips on their own device\|ADR-021]], which is where it stopped. None were recoverable; reconciled on the station. No schema change of its own; the head has since moved to `0011_retention_live_asset_indexes` ([[ADR-062 - Retention walks live assets\|ADR-062]]). The rolling audit now needs ~4 days per pass over 234k live rows, so `exact` is rarely true |
| [[ADR-058 - Placed spectrogram labels\|058]] | The spectrogram's detection labels are placed, not drawn where they fall | active; a truncated species name can read as a different species, so a dropped label is the honest failure |
| [[ADR-059 - Clip archive measured off-loop\|059]] | The clip archive is measured off the event loop, because a status snapshot must not walk a filesystem | active; deployed 2026-08-10 and its first verification failed at 188,982 of 192,000 frames; re-verified on the station 2026-08-29 — 94,374 of 192,000 across 5 late reads, `storage` snapshot phase 0.000 s on a 241,404-file archive |
| [[ADR-060 - A stalled read is a dead stream\|060]] | A read that never returns is a dead stream, not a slow one | active; the wedge itself remains unexplained, and its cost is attributed by [[ADR-061 - Operator keep flag\|ADR-061]] |
| [[ADR-061 - Operator keep flag\|061]] | An operator-set keep flag replaces the computed exemplar rule | active; supersedes [[ADR-026 - Tiered clip retention\|ADR-026]]'s first/best-of-species exemption; deployed, and all three pass criteria verified on the station 2026-08-29 |
| [[ADR-062 - Retention walks live assets\|062]] | Retention walks the live assets, not the whole history | accepted 2026-08-19, in force on the station; supersedes in part [[ADR-026 - Tiered clip retention\|ADR-026]] (tier age is measured on `media_asset.created_at`), [[ADR-035 - Alembic environment\|ADR-035]]'s revision `0002` index and [[ADR-061 - Operator keep flag\|ADR-061]]'s candidate-query ordering |
| [[ADR-063 - Clock re-anchor\|063]] | The stream clock re-anchors when the wall clock steps | accepted, 2026-08-19 |
| [[ADR-064 - Watermark tier first\|064]] | The watermark tier runs first when disk is already over the line | accepted, 2026-08-19 |
| [[ADR-065 - Unclean restart is reported\|065]] | A restart that voids a measurement must say so | accepted, 2026-08-19 |
| [[ADR-066 - Graceful shutdown closes the row\|066]] | A graceful shutdown closes its own stream row | accepted, 2026-08-19 |
| [[ADR-067 - Unattended package work\|067]] | Unattended package work runs at 15:00, not in the dawn chorus | accepted, 2026-08-21 |
| [[ADR-068 - Deliberately unused\|068]] | Deliberately unused | not a decision — the number is unused; do not reuse |
| [[ADR-069 - Two drift gates\|069]] | "The one-hour drift run" is two different tests, and this is the criterion each must meet | accepted, 2026-08-23. Written **before** either run, deliberately. Both gates run 2026-08-25: (a) passed, (b) failed on linearity, twice. Reviewed 2026-08-29. |
| [[ADR-070 - Threshold retune is not a defect\|070]] | A threshold retune is not a discovery that the past was wrong | accepted, 2026-08-23 |
| [[ADR-071 - WiFi reconnect backoff\|071]] | A reconnect that fires faster than an association can complete is not a retry | accepted, 2026-08-24; **flashed over the air 2026-08-24** as firmware 0.2.5. The drop-recovery half is still unexercised — no AP-down drill has been run |
| [[ADR-072 - Accepted crystal drift\|072]] | Capture timestamps drift with the microphone's crystal, and that is accepted | accepted, 2026-08-25 |
| [[ADR-073 - Five capture SLOs\|073]] | What "missing audio" means, and five SLOs instead of one continuity number | accepted, 2026-08-29 |
| [[ADR-074 - Evidence kept by value\|074]] | Evidence is retained by value, not by age | accepted, 2026-08-29 |
