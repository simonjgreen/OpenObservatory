# Evidence-bank measurements, 2026-08-29 — on the station

Every number ADR-075 argues from, measured on the Raspberry Pi 5 at
`192.168.1.195` rather than estimated. Read-only against the live database
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

Two honest caveats. The lab copy has no `ANALYZE` statistics and its tables are
untyped copies, so it did not reproduce the station's own plan for this query
(the station uses `ix_media_asset_live_kind_created`; the lab fell back to a
scan). That makes the *absolute* seconds here meaningless. It does not weaken
the finding, which is a comparison of two plans against the same tables: adding
the predicate changed neither the plan nor the cost. The station's own plan for
this query must still be confirmed after the migration lands, and ADR-075's
plan carries that as a step.

The index is partial on `banked_at IS NOT NULL`, following
`ix_detection_kept_at_partial` exactly, so it is not available to the
`IS NULL` filter and cannot be preferred over the index serving the range and
the `ORDER BY` — which is the mechanism of the ADR-061 failure.

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

## What the measuring cost

**It cost about 2.2 seconds of audio.** At `2026-08-29T20:32:37Z` capture
recorded `reason=overrun`, `alsa_overrun=True`, `missing_frames=851919`,
`seconds=2.2185` — one confirmed gap, during the window in which a paced copy
of the 1.35 GB database was competing for the same SD card the database and
the journal live on. It is not proven to be the cause and no other load
changed; the honest statement is that it is very probably the cause.

Recorded rather than quietly dropped, for two reasons. Read-only is not the
same as free: every figure above was taken from a device that is also holding
a 384 kHz capture ring, and the ADR-062 budget exists because that ring is
what everything else is subordinate to. And ADR-073 is the ADR that says a
consumer bird monitor should not be fixated on gaps measured in seconds over a
week — this is one such gap, it is within every SLO ADR-073 sets, and the
right response is to say so rather than to hide it.

Two practical consequences for anyone repeating this:

* the initial approach — `sqlite3`'s backup API against the live database —
  **never converges.** SQLite restarts a backup whenever the source is written,
  and capture writes continuously; the copy crawled at about 8 MB/min and was
  abandoned. The narrow one-pass `SELECT`-and-`INSERT` above copied 903,240
  rows in 51.3 s instead;
* prefer the narrow copy to a full one. It is 150 MB rather than 1.35 GB
  because it omits the wide `native_result` JSON, and every query measured here
  is index-only or PK-driven and never reads that column.
