---
aliases:
  - ADR-026
tags:
  - adr
---
# ADR-026: NVR-style tiered clip retention; detection metadata is kept forever
**Status:** active in its core rule — clip *bytes* age out, detection metadata
never does — with most of its specifics since amended: sweep cadence by
[[ADR-033 - Retention is paced|ADR-033]], its "no Alembic migrations anywhere" note by [[ADR-035 - Alembic environment|ADR-035]], the
first/best-of-species exemption and the 30–90 day and 90+ day tiers by
[[ADR-061 - Operator keep flag|ADR-061]], the timestamp tier age is measured on by [[ADR-062 - Retention walks live assets|ADR-062]], and tier
order by [[ADR-064 - Watermark tier first|ADR-064]]. [[ADR-074 - Evidence kept by value|ADR-074]] proposes a value layer on top of these tiers —
accepted 2026-08-29, not implemented. See the review note at the foot of this ADR.

**Decision:** Evidence clip *bytes* age out in four tiers; detection *metadata* never
does. `retention.py` (`RetentionSweeper`) implements this, driven by the database
(kind, species, score, age), not a filesystem walk:

| Age | What survives |
|---|---|
| 0–7 days | native (full-rate) clip + audible rendering |
| 7–30 days | audible rendering only — native clip deleted |
| 30–90 days | only the first-ever and best-of-species clip survive |
| 90+ days | deleted, including the exemplars |
| always | oldest-first reclaim above `retention_watermark_ratio` (default 85%), ignoring tier and exemplar status |

Every threshold is a `Settings` field (`retention_native_days`,
`retention_audible_only_days`, `retention_exemplar_only_days`,
`retention_watermark_ratio`, `retention_batch_size`, `retention_batch_budget_s`),
defaulting to exactly the values above.

**Reason.** This is the operator's own framing: "I do not need recordings going
back forever — the logs of bird species etc over time is the most interesting data,
not the noise they made." [[DATA_MODEL]] already recorded detections as
indefinite by default; this ADR makes clip storage match that intent explicitly,
as a CCTV NVR ages out footage while keeping its incident log.

**Why the operator's own thresholds, unreviewed by us.** The tier boundaries and
the 85% watermark were specified directly, not derived here. They are defaults
precisely so they can be revisited: `clip_max_total_gb` (from [[ADR-021 - Clips on their own device|ADR-021]], now 300 GB
against 465.8 GB) already showed one GB-budget knob was a coarse enough proxy that
it needed lifting once; a ratio-based watermark tracks the disk's real remaining
headroom instead of a number someone will eventually have to update by hand.

**Detection rows are never deleted, by any tier, including the 90+ day one.**
`RetentionSweeper` never issues a `DELETE` against `detection`, `detector`, or any
other metadata table, and never mutates a `Detection` row's columns. It only ever
sets `media_asset.reclaimed_at` / `reclaim_reason` after successfully unlinking a
file — the association (`detection_media`), the species/score/timestamp, and the
capture provenance (frame bounds, stream id) all survive. `/api/v1/media/{id}`
already returned `410 Gone` for a row whose file is missing (written before this
ADR, for the ordinary case of a clip lost to disk trouble); a reclaimed asset now
reaches that same, already-tested path deliberately instead of by accident.

**Why age is measured from `detection.event_start_utc`, not `media_asset.created_at`.**
The two are milliseconds apart in practice, but the detection is the entity the
operator's policy is actually about ("first-of-species", "best-of-species"); using
its timestamp keeps every clip belonging to one detection in the same tier
together, rather than letting a slow-to-render ultrasonic derivative drift into a
different age bracket than its own detection's native clip.

**Defining "best-per-species".** "Species" is `canonical_taxon_id` when one
exists (species-rank birds), else `common_name`, else the taxonomic group itself.
That last fallback is deliberate: `ultrasonic-pass-v1` identifies passes, not
species (the honesty rule `normaliser.py` enforces in code, not just docs), so
every bat pass collapses into one group — there is no finer species key to exempt
by without inventing an identification the detector never made. In practice this
keeps exactly one first-ever and one best-ever bat-pass clip, not one per
(non-existent) bat species.

"Best" is highest `score` for anything with a real, cross-comparable score. Bats
are the deliberate exception: `ultrasonic-pass-v1`'s `score` is a composite
(`0.4*min(1, pulses/8) + 0.6*min(1, (peak_snr_db-12)/24)`, see
`detectors/ultrasonic.py`) invented to rank passes against each other, not a
calibrated quality measure comparable to BirdNET's. Ranking bats by it anyway
would be exactly the kind of unearned precision the honesty rules exist to
prevent, so bats are ranked by `peak_snr_db` from `native_result` instead — the
detector's own physical measurement — with `pulse_count` as a tiebreaker.

**Why this is a new module rather than extending `ClipManager.enforce_retention`
in place.** `clips.py` already had a retention sweep, a size budget and a disk
reserve before this ADR, and they are not removed: `ClipManager.admits()` and its
`min_free_bytes` write-time reserve are untouched (a different concern — refusing
to *write* a new clip when the disk is nearly full, evaluated on the capture
path), and `ClipManager.enforce_retention()` still exists and is still tested.
What changed is what `station.py`'s housekeeping loop calls: it now drives
`RetentionSweeper.sweep()` instead, because tiering requires information
`enforce_retention`'s plain filesystem walk never had — a clip's `kind`, its
detection's species and score — so a walk keyed on file mtime cannot express
"delete the native file but keep the audible one" or "keep this one clip out of
a thousand." A rewrite in place would have entangled a filesystem-only algorithm
with a database-driven one for no benefit; the old method is simply unused by the
running station now.

> **What that old method had already done, found 2026-08-10 ([[ADR-057 - Evidence rows must be checkable|ADR-057]]).**
> Between 2026-08-05 and 2026-08-08 `enforce_retention` unlinked 8,166 clips
> (20.84 GB) to stay inside its 20 GB budget and marked not one row, leaving
> 8,067 `media_asset` rows asserting evidence that no longer exists. "Unused by
> the running station" was true and was not enough: nothing ever checked whether
> the rows and the disk still agreed, so the divergence was invisible for five
> days. `RetentionSweeper.audit_missing_files()` is now that check.

**I/O discipline ([[ADR-021 - Clips on their own device|ADR-021]]'s lesson applied again).** `sweep()` never walks the
clip directory tree. Every tier is one bounded `LIMIT`-ed SQL query plus, at most,
`retention_batch_size` file `unlink()` calls (default 200), and the whole call
bails out at `retention_batch_budget_s` (default 1.5s) of wall-clock time even if
the batch isn't exhausted — a large backlog drains across many housekeeping ticks
rather than stalling once. It runs in the same single-thread `_evidence_executor`
that evidence extraction already uses, never the default pool the ALSA read
shares ([[ADR-021 - Clips on their own device|ADR-021]]'s fix, still the load-bearing one). The one read that is not
batch-limited is the first-of/best-of-species computation, which is a plain read
over the `detection` table filtered to rows with at least one live media asset —
justified because detection metadata is, in the operator's own words, kilobytes
per day; it is a small, growing-slowly table, not clip storage. `find_orphans()`
(files on disk with no database row) *is* a tree walk, and is therefore
deliberately excluded from the automatic sweep entirely — it exists only for the
CLI's manual diagnostic use, and it never deletes anything.

**Dry run.** `RetentionSweeper.sweep(dry_run=True)` runs every query and every
decision unchanged, logs `retention.would_delete` instead of `retention.delete`,
and returns the same `RetentionReport` shape — but never calls `unlink()` and
never mutates an ORM object, so `session.commit()` at the end of a dry run is a
guaranteed no-op. `oo clips retention --dry-run` exposes this on the CLI. Given
that this code deletes irreversibly, the dry run is not a convenience feature; it
is treated as one of the module's primary contracts and is tested as such
(`tests/test_retention.py::test_dry_run_matches_real_run`).

**What this ADR does not do.** It does not delete
`data/clips.sdcard-backup` (~21 GB, pre-migration copy noted in
[[OPEN_INVESTIGATION_CAPTURE_GAPS]]) — that stays an explicit, operator-triggered
cleanup outside any automatic sweep, per instruction. It does not add an Alembic
migration for the two new `media_asset` columns
(`reclaimed_at`, `reclaim_reason`): this project has no Alembic migrations yet
anywhere (`db/session.create_all` is still how every profile gets its schema, per
its own docstring), so a fresh SQLite database picks the columns up automatically;
a live deployment with an existing database needs an explicit `ALTER TABLE` before
this code runs against it, which is a deploy-time concern for whoever performs
that deploy, not something decided here.

> **Status 2026-08-08.** Two later changes amend this ADR without replacing it.
> **[[ADR-035 - Alembic environment|ADR-035]]** built the Alembic environment: both `media_asset` columns are in the
> `0001_initial` baseline, and revision `0002` adds the `reclaimed_at` index that
> `ALTER TABLE ADD COLUMN` could never have created. **[[ADR-033 - Retention is paced|ADR-033]]** paced the sweep:
> it runs every `retention_interval_s` (default 300 s), not on every housekeeping
> tick, because a ~0.30 s ORM sweep holds the GIL and starved the event loop. The
> tiers, thresholds and "detection metadata is kept forever" rule are unchanged.

> **Reviewed 2026-08-29.** Three further ADRs have replaced specifics above.
> The tier table, the "best-per-species" section and the `event_start_utc`
> section are left as the record of what was decided, not as a description of
> the running code. **[[ADR-061 - Operator keep flag|ADR-061]]** replaced the computed first/best-of-species
> exemption with an operator-set `kept` flag (`detection.kept_at`/`kept_by`),
> and its third addendum removed the 90+ day tier as dead code together with
> the `retention_exemplar_only_days` setting: `_TIER_ORDER` is now
> `("native", "unkept", "watermark")` (`src/open_observatory/retention.py:105`)
> and the station reports two age tiers, 7 d and 30 d
> (`GET /api/v1/retention/status`). **[[ADR-062 - Retention walks live assets|ADR-062]]** reversed "why age is
> measured from `detection.event_start_utc`": both age tiers now bound and
> order on `media_asset.created_at` (`src/open_observatory/retention.py:707`
> and `:747`), which is never earlier than the detection, so the substitution
> can only make a tier act late, not early. **[[ADR-064 - Watermark tier first|ADR-064]]** runs the watermark
> tier first when the disk is already over the line, instead of last. What is
> unchanged: `RetentionSweeper` still issues no `DELETE` against `detection`,
> still marks `reclaimed_at`/`reclaim_reason` onto the already-tested 410 path,
> `sweep()` still never walks the clip tree, and `find_orphans()` is still
> excluded from it.

> **Reviewed again 2026-08-30.** Four corrections, from the station and from the
> code, none of which change the decision.
>
> **The 30-day tier has never had a candidate.** `GET /api/v1/retention/status`
> puts all 238,017 live assets inside the two age buckets (94,764 under 7 days,
> 143,253 between 7 and 30) and `eligible_for_deletion` at **0 clips, 0 bytes**;
> `oo_retention_files_deleted_total{tier="unkept"}` reads **0**. No asset on this
> station is yet 30 days old, so `_strip_unkept` has run thousands of times and
> deleted nothing, ever. The whole disk is currently governed by the 7-day native
> tier and by [[ADR-077 - Acoustic events keep no recordings|ADR-077]]'s tier.
> When the archive does cross 30 days the backlog arrives as a step, not a ramp,
> into a sweep whose budget is already spent — that is a dated, checkable
> prediction, not a fault yet.
>
> **`find_orphans()` has no CLI command.** The I/O-discipline section above says
> it "exists only for the CLI's manual diagnostic use". It is called from
> `tests/test_retention.py:1413` and from nowhere else; `oo clips` offers
> `purge-human-audio`, `reconcile-missing`, `retention` and `bank-backfill`
> (`src/open_observatory/cli.py`) and no orphan scan. This matters because the
> two figures it would reconcile disagree: the station reports **245,713 `.wav`
> files** under the clip directory against **238,017 live `media_asset` rows**.
> [[ADR-057 - Evidence rows must be checkable|ADR-057]]'s audit only checks the
> other direction — rows whose file is gone — so nothing on this station can
> currently account for the ~7,700 difference.
>
> **`clip_max_total_gb` is still published as a budget and enforces nothing.**
> The "why the operator's own thresholds" section above cites it as the knob the
> watermark replaced, and that is right; what is not recorded anywhere is that it
> is still live-tunable, still in the settings UI as "clip directory budget"
> (`src/open_observatory/site_settings.py:737`), still pushed into `ClipManager`
> (`src/open_observatory/station.py:265`), and still published in the station
> snapshot as `clips.policy.max_total_gb`. Its only reader is
> `ClipManager.enforce_retention` (`src/open_observatory/clips.py:280`), which
> nothing outside `tests/test_pipeline.py:639` calls. The station is running it
> at **300 GB** while holding **395.6 GB**, and reports `budget_deleted: 0` and
> `bytes_reclaimed: 0` beside it. `clip_retention_days` is inert in the same way:
> it sets `media_asset.expires_at` at write time and is otherwise read only by
> that same dead method. Two settings an operator can change with no effect and
> no warning.
>
> **Nothing sweeps `.partial` files or empty day directories any more, either.**
> That cleanup lives in the same dead method
> (`src/open_observatory/clips.py:292-300`); `RetentionSweeper` never walks the
> tree, by design, so an abandoned partial write is now permanent.

---
Part of the [[ADRS|Architecture Decision Record index]].
