# ADR-026: NVR-style tiered clip retention; detection metadata is kept forever
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
not the noise they made." `DATA_MODEL.md` already recorded detections as
indefinite by default; this ADR makes clip storage match that intent explicitly,
as a CCTV NVR ages out footage while keeping its incident log.

**Why the operator's own thresholds, unreviewed by us.** The tier boundaries and
the 85% watermark were specified directly, not derived here. They are defaults
precisely so they can be revisited: `clip_max_total_gb` (from ADR-021, now 300 GB
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

> **What that old method had already done, found 2026-08-10 (ADR-057).**
> Between 2026-08-05 and 2026-08-08 `enforce_retention` unlinked 8,166 clips
> (20.84 GB) to stay inside its 20 GB budget and marked not one row, leaving
> 8,067 `media_asset` rows asserting evidence that no longer exists. "Unused by
> the running station" was true and was not enough: nothing ever checked whether
> the rows and the disk still agreed, so the divergence was invisible for five
> days. `RetentionSweeper.audit_missing_files()` is now that check.

**I/O discipline (ADR-021's lesson applied again).** `sweep()` never walks the
clip directory tree. Every tier is one bounded `LIMIT`-ed SQL query plus, at most,
`retention_batch_size` file `unlink()` calls (default 200), and the whole call
bails out at `retention_batch_budget_s` (default 1.5s) of wall-clock time even if
the batch isn't exhausted — a large backlog drains across many housekeeping ticks
rather than stalling once. It runs in the same single-thread `_evidence_executor`
that evidence extraction already uses, never the default pool the ALSA read
shares (ADR-021's fix, still the load-bearing one). The one read that is not
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
`OPEN_INVESTIGATION_CAPTURE_GAPS.md`) — that stays an explicit, operator-triggered
cleanup outside any automatic sweep, per instruction. It does not add an Alembic
migration for the two new `media_asset` columns
(`reclaimed_at`, `reclaim_reason`): this project has no Alembic migrations yet
anywhere (`db/session.create_all` is still how every profile gets its schema, per
its own docstring), so a fresh SQLite database picks the columns up automatically;
a live deployment with an existing database needs an explicit `ALTER TABLE` before
this code runs against it, which is a deploy-time concern for whoever performs
that deploy, not something decided here.

> **Status 2026-08-08.** Two later changes amend this ADR without replacing it.
> **ADR-035** built the Alembic environment: both `media_asset` columns are in the
> `0001_initial` baseline, and revision `0002` adds the `reclaimed_at` index that
> `ALTER TABLE ADD COLUMN` could never have created. **ADR-033** paced the sweep:
> it runs every `retention_interval_s` (default 300 s), not on every housekeeping
> tick, because a ~0.30 s ORM sweep holds the GIL and starved the event loop. The
> tiers, thresholds and "detection metadata is kept forever" rule are unchanged.
