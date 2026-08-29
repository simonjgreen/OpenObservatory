# Evidence-bank measurements, 2026-08-29 — on the station

Every number ADR-076 argues from, measured on the Raspberry Pi 5 at
`<station-host>` rather than estimated. Read-only against the live database
except where a lab copy is named.

## The shape of the archive

| | measured |
|---|---|
| `detection` rows | **903,240** |
| ...with a `common_name` (birds) | 116,744 |
| ...without one (bats and unnamed) | 786,496 |
| bat detections with a positive `peak_frequency_hz` | 66,902 |
| `media_asset` rows | 362,703 |
| ...live (`reclaimed_at IS NULL`) | 234,290 |
| `detection_media` rows | 362,703 |
| distinct `common_name`, lifetime | 153 |
| distinct `common_name` with an intact live detection | 127 |
| `kept_at IS NOT NULL` (ADR-061) | 112 |

**903,240 detections, not ~46,000.** Both `retention.py` and
`db/models.py` carry comments citing ~46,000 and 290,956 — figures from
ADR-037 and ADR-061 that are now stale by a factor of twenty and three
respectively. The planner arguments those comments record were sound when
written; the cardinalities they rest on are not.

Note the unit: ADR-074 counts **clips** (assets), this table counts
**detections**. European Robin's "38,016 clips" is 10,803 live detections at
about 3.5 assets each. The two are not in disagreement.

Live detections per species, top of the distribution: European Robin 10,803,
Common Woodpigeon 5,705, Eurasian Jackdaw 1,871, Eurasian Blue Tit 1,507,
Rook 1,435. **114 species hold fewer than 200 live detections, 2,593 between
them.**

## The census ADR-074 calls unaffordable — it is, by 12x

`_banked_counts` as shipped, against the live database:

    EXPLAIN QUERY PLAN
      CO-ROUTINE intact
      SCAN detection_media USING COVERING INDEX sqlite_autoindex_detection_media_1
      SEARCH media_asset USING INDEX sqlite_autoindex_media_asset_1 (id=?)
      SCAN intact
      SEARCH detection USING INDEX sqlite_autoindex_detection_1 (id=?)
      USE TEMP B-TREE FOR GROUP BY

| | measured | budget |
|---|---|---|
| `_banked_counts`, live database | **18.3219 s** | 1.5 s (ADR-062) |

The 900 s TTL does not rescue this. A sweep that runs it blocks for eighteen
seconds inside one statement, in the capture process, on the paced housekeeping
loop (ADR-033) — four times over the whole sweep's budget, and the failure
ADR-061 records began the same way.

For scale, a bare `SELECT count(DISTINCT common_name) FROM detection` — no
join, no media — takes **40.55 s**, because nothing indexes `common_name` and
the table carries a wide `native_result` JSON column.

## The replacement, measured on a lab copy

A narrow copy of the station's `detection`, `media_asset` and
`detection_media` (903,240 / 362,703 / 362,703 rows) with the production
indexes rebuilt on it. `native_result` deliberately not copied: every query
below is index-only or PK-driven and never reads it.

**One-off backfill** — bank the oldest live detections of each species up to
the cap, as a single windowed `UPDATE`:

| | measured |
|---|---|
| backfill, whole archive | **8.9753 s** |
| detections banked | **7,302** across 135 species |
| partial covering index build | 0.3179 s |

Offline, unbudgeted, run once from the CLI — the same shape as
`oo detections reconcile-plausibility` (ADR-032).

**The sweep's census, afterwards:**

| | measured | against |
|---|---|---|
| banked count per species | **0.0023 s** | 18.3219 s |

About **8,000x**, and it is now an index-only scan of the ~7,300 banked rows
rather than a census of the whole archive.

## The plan-regression check, which is the one that has hurt before

ADR-061 revision 0009 records a plain index on `kept_at` costing the planner
`ix_detection_event_start_utc` and wedging the station for five minutes inside
one statement. The new column is exposed to exactly that risk, so it was
measured rather than assumed. `_strip_native`'s candidate query, with and
without `AND detection.banked_at IS NULL`:

    -- WITHOUT                          -- WITH
    SCAN dm                             SCAN dm
    SEARCH ma USING INDEX ... (id=?)    SEARCH ma USING INDEX ... (id=?)
    SEARCH d  USING INDEX ... (id=?)    SEARCH d  USING INDEX ... (id=?)
    USE TEMP B-TREE FOR ORDER BY        USE TEMP B-TREE FOR ORDER BY

**Identical plan, and 5.5533 s versus 6.0093 s** — the predicate is free.

**Settled properly, and more cheaply than expected.** The lab copy above did
not reproduce the station's plan for this query — it has untyped columns and
was missing `ix_detection_media_asset` — which made its absolute seconds
meaningless. That looked like it would need a deploy to settle. It did not.

**The station carries no `ANALYZE` statistics at all** (verified read-only: no
`sqlite_stat*` table exists). SQLite's planner is therefore driven by the
schema alone — which indexes exist, over which columns — and *not* by row
counts. So a schema-only database built from the ORM metadata, holding **zero
rows**, reproduces the station's plan exactly. Built one, and it does:

| | plan |
|---|---|
| **station, live database** | `SEARCH media_asset USING INDEX ix_media_asset_live_kind_created (kind=? AND created_at<?)` → `SEARCH detection_media USING INDEX ix_detection_media_asset (media_asset_id=?)` → `SEARCH detection USING INDEX sqlite_autoindex_detection_1 (id=?)` → `USE TEMP B-TREE FOR ORDER BY` |
| **local schema-only, no rows** | *identical, line for line* |
| **local schema-only, `AND detection.banked_at IS NULL` added** | *identical, line for line* |

So the ADR-061 failure mode is ruled out: the new predicate costs the planner
nothing, and the check needs neither the station nor a copy of its data. It
reproduces in seconds from `orm.Base.metadata.create_all()` and should be
re-run that way whenever an index on `detection` changes.

The index is partial on `banked_at IS NOT NULL`, following
`ix_detection_kept_at_partial` exactly, so it is not available to the
`IS NULL` filter and cannot be preferred over the index serving the range and
the `ORDER BY` — which is the mechanism of the ADR-061 failure.

(The `USE TEMP B-TREE FOR ORDER BY` in every row above is pre-existing and not
introduced here: `kind IN (a, b)` is two index seeks, each already ordered by
`created_at`, and merging them needs a sort. It is the plan the station has
been running.)

## Every query ADR-076 adds, planned against the production schema

Same zero-row schema-only database, same reasoning: no `ANALYZE` statistics
anywhere, so these are the plans the station will use.

| query | plan | bounded by |
|---|---|---|
| `_evidence_bank` — one pass over the bank | `SCAN detection USING INDEX ix_detection_banked_partial` | the **bank** (~7,300 rows), not the archive |
| `_promotion_candidates` — no media join | `SEARCH detection USING INDEX ix_detection_event_start_utc (event_start_utc>?)` | the lookback window |
| `_has_live_evidence` — one detection | `SEARCH detection_media USING COVERING INDEX ... (detection_id=?)` → `SEARCH media_asset USING INDEX ... (id=?)` | two seeks, 0.46 ms |
| `_band_counts` — sparse bands | `SEARCH detection USING INDEX ix_detection_group_start (taxonomic_group=? AND event_start_utc>?)` | the trailing window |

Not one of them scans a table, and not one of them is bounded by the size of
the archive. That is the property ADR-074's census did not have and the whole
reason this redesign exists.

## Reproduce

`scripts/` holds nothing for this; the lab was built ad hoc. The live-database
figures re-run read-only in seconds:

    ssh <station> 'cd open-observatory && ./.venv/bin/python -c "
    import sqlite3, time
    c = sqlite3.connect(\"file:data/openobservatory.sqlite?mode=ro\", uri=True)
    t = time.perf_counter()
    print(len(c.execute(open(\"/tmp/census.sql\").read()).fetchall()),
          time.perf_counter() - t)"'

Nothing above writes to the station database.

## What the measuring cost — **corrected: not what I first wrote**

An earlier version of this section claimed the 2.2 s of audio lost at
`2026-08-29T20:32:37Z` was "very probably" caused by the paced database copy
taken for these measurements. **That attribution was wrong**, and it is left
here rather than quietly deleted because getting a cause wrong is the thing
this project's own lesson is about.

The gap belongs to [[DRIFT_GATE_C_2026-08-29]] — a concurrent session's
one-hour replay running on the live station, which was ten minutes in at that
timestamp and was **stopped because of it**. Their evidence is much better than
my inference was: they polled `gaps_with_loss` either side of the event and
watched it go 0 → 1 across 20:32, against a run that was pushing an hour of
audio through the resampler on the same box.

What I actually had was a coincidence in time and a plausible mechanism. Both
loads were on the machine, and a paced 1.35 GB copy competing for the same SD
card cannot be ruled out as a contributor. But "my load was running and a gap
happened" is not a cause, and stating it as one was exactly the error of
reasoning that this repository has a memory about. The honest position: gate
(c) accounts for it, my copy is at most a contributor, and I should have looked
for other load before claiming it.

**The point that survives the correction** is the one worth keeping: read-only
is not free. Every figure above was taken from a device that is also holding a
384 kHz capture ring, and the ADR-062 budget exists because that ring outranks
everything else. Prefer the narrow one-pass copy below to a full one, and
prefer running nothing at all while somebody else is measuring the clock.

Two practical consequences for anyone repeating this:

* the initial approach — `sqlite3`'s backup API against the live database —
  **never converges.** SQLite restarts a backup whenever the source is written,
  and capture writes continuously; the copy crawled at about 8 MB/min and was
  abandoned. The narrow one-pass `SELECT`-and-`INSERT` above copied 903,240
  rows in 51.3 s instead;
* prefer the narrow copy to a full one. It is 150 MB rather than 1.35 GB
  because it omits the wide `native_result` JSON, and every query measured here
  is index-only or PK-driven and never reads that column.
