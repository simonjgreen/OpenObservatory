---
aliases:
  - ADR-062
tags:
  - adr
---
# ADR-062: Retention walks the live assets, not the whole history
**Status:** accepted, 2026-08-19
**Supersedes in part:** [[ADR-026 - Tiered clip retention|ADR-026]] (tier age is now measured on the asset),
[[ADR-035 - Alembic environment|ADR-035]] (the `ix_media_asset_reclaimed_at` its revision `0002` added is
dropped), [[ADR-061 - Operator keep flag|ADR-061]] (its candidate queries' ordering by
`detection.event_start_utc`)

### The failure

Between 2026-08-17 and 2026-08-19 the station reclaimed nothing. 351 of roughly
526 sweeps ended `complete=False`, `total_deleted=0`, `tiers_skipped=['native',
'unkept', 'watermark']`, every five minutes, while the clip volume climbed from
72% to 78% at 3.8 GB/hour. `/api/v1/health` returned `status: "ok"` with an
empty `problems` list throughout.

The proximate error in the log was honest and specific:

    retention.statement_interrupted  after_s=1.5109  batch_budget_s=1.5  tier=native

`_bounded_statements` did exactly its job. The native tier's candidate query
could no longer finish inside the 1.5 s budget, so it was aborted, and because
the native tier is first, the budget was gone before the unkept and watermark
tiers were reached. **The watermark tier is the safety valve for disk pressure,
and it lives inside the sweep that was failing**, so there was no backstop: the
mechanism that should have caught the rising disk was one of the casualties.

### Root cause

Every tier ordered by `detection.event_start_utc` and filtered
`media_asset.reclaimed_at IS NULL`. Those two facts live in different tables,
so the walk was driven by `ix_detection_event_start_utc` and had to join out to
`media_asset` for each row to discover whether that row still had a file.

Rows already reclaimed stay in `ix_detection_event_start_utc` forever. So the
sweep had to step over everything it had already done to reach anything it had
not. Measured on the station's database on 2026-08-19: 38,268 reclaimed assets,
~210,000 old detections examined per sweep, to find **1,699** genuinely
outstanding native clips.

**The query got slower every single time it succeeded.** It ran in 0.986 s on
2026-08-17 and could not finish in 1.5 s two days later. Nothing "went wrong" —
this was the designed behaviour meeting enough history.

The measurement that identified it, and the one worth remembering:

| `LIMIT` | 1 | 10 | 50 | 250 | 1000 | 4000 |
|---|---|---|---|---|---|---|
| time | 2.29 s | 2.22 s | 2.25 s | 2.17 s | 2.20 s | 2.21 s |

Constant. A query whose `LIMIT 1` costs the same as its `LIMIT 4000` is not
selecting rows, it is scanning to reach the first one. That single table ruled
out three plausible-but-wrong hypotheses (a bad plan — the plans were identical
either way; a slow join; accumulation cost) in one step.

### Decision

**1. Partial indexes keyed on the asset, excluding reclaimed rows.**

    CREATE INDEX ix_media_asset_live_kind_created
        ON media_asset (kind, created_at) WHERE reclaimed_at IS NULL;
    CREATE INDEX ix_media_asset_live_created
        ON media_asset (created_at)       WHERE reclaimed_at IS NULL;

The `WHERE` clause is the mechanism, not an optimisation: **a reclaimed row
leaves the index**, so completed work is never walked again. `created_at` is
last so the range predicate and the `ORDER BY` are satisfied by one index and
the scan stops at `LIMIT` instead of sorting.

**2. Tier age is measured on `media_asset.created_at`, not
`detection.event_start_utc`.** This is a real semantic change and the reason it
is safe is empirical: across all 86,377 native assets on the station,
`created_at` is *always* later than its detection's `event_start_utc` (minimum
+0.45 s, mean +823 s, maximum +29 h, zero exceptions). So `created_at <= cutoff`
implies `event_start_utc <= cutoff`, and the substitution can only make a tier
**late**, never early — it cannot delete a clip before its policy age. The cost
is that a clip written unusually long after its event survives its tier by up to
that lag, which resolves itself as the cutoff advances.

**3. An index on the reverse join.** `detection_media`'s primary key indexes
`(detection_id, media_asset_id)`; retention travels the other way and had no
index for it (0.114 s per 250 candidates, scanning the link table).

**4. `ix_media_asset_reclaimed_at` is dropped, and that is load-bearing.**
`reclaimed_at` is NULL on 176,231 of 214,499 rows. SQLite treats a plain index
on it as a cheap way in and prefers it over the partial ones, losing the
`ORDER BY` and adding a `USE TEMP B-TREE`. With it present the native tier plans
at 0.1215 s; without it, 0.0004 s.

**5. A run of barren sweeps is a health problem.** Three consecutive sweeps that
neither complete nor delete anything now populate `problems`, so the endpoint
says what the storage block already knew.

### Measured result

Best of three, on a copy of the live database:

| tier | before | after |
|---|---|---|
| native | 2.2000 s | 0.0049 s |
| unkept | (same shape, would degrade identically) | 0.0000 s |
| watermark | 1.8048 s | 0.0032 s |
| asset → detection join, 250 candidates | 0.1144 s | 0.0046 s |

### Do not run `ANALYZE` to "help"

It was tried. It made things worse, and instructively so. `sqlite_stat1` records
an average of 6 rows per `reclaimed_at` value — arithmetically true and
completely useless, because the NULL bucket holds 176,231 of them. With those
statistics present the planner abandoned the correct plan and returned to the
temp B-tree: 0.0004 s → 0.1215 s. This is the third time on this project a
confidently-wrong measurement has pointed at the wrong fix; see [[ADR-061 - Operator keep flag|ADR-061]]'s
addenda for the first two.

### Consequences

- This is the **third** index incident here with one shape: a low-selectivity
  index the planner prefers over the one that serves the query
  (`ix_detection_kept_at`, revision 0009; `ix_media_asset_reclaimed_at`, now).
  `tests/test_retention.py::TestCandidateQueryPlans` asserts the plans
  directly, via `EXPLAIN QUERY PLAN`, because every one of these incidents was
  invisible to functional tests — the answers stayed correct, they just took
  400× longer. A wall-clock assertion would be flaky on a loaded Pi and silent
  on a fast dev box; the plan is what regressed, so the plan is what is pinned.
- `retention_batch_budget_s` still cannot bound a statement on PostgreSQL
  (`_bounded_statements` is a SQLite progress handler). Unchanged by this ADR,
  still open.
- Rollback: `alembic downgrade 0010_kept_at_partial_index` restores the previous
  indexes and plans; the tier code reads `created_at` regardless, which without
  the partial indexes is merely slow, not wrong.

**Reviewed 2026-08-29:** the decision holds and is deployed. Both partial indexes
and `ix_detection_media_asset` are declared on the models
(`src/open_observatory/db/models.py:321`, `:327`, `:351`) and created by revision
`0011_retention_live_asset_indexes`, which drops `ix_media_asset_reclaimed_at`
last so a failure part-way through never leaves a database with neither; all
three tiers bound and order on `media_asset.created_at`
(`src/open_observatory/retention.py:707`, `:747`, `:806`); the barren-sweep
threshold is three (`src/open_observatory/api/app.py:130`). The station reported
`consecutive_barren_sweeps: 0` and `retention_sweep_keeping_up: true`, disk below
the watermark, sweeps five minutes apart. Two things a reader of the above would
otherwise get wrong:

- **The dropped index was not [[ADR-061 - Operator keep flag|ADR-061]]'s.** `ix_media_asset_reclaimed_at` is
  created by `0001_initial` and, on a database that reached that revision through
  the patched-column path, by [[ADR-035 - Alembic environment|ADR-035]]'s revision `0002`; [[ADR-061 - Operator keep flag|ADR-061]]'s own
  revisions are `0008`–`0010` and none of them touch it. The header line above
  said "revision 0001" against the wrong ADR and is corrected. [[ADR-035 - Alembic environment|ADR-035]] records
  the drop from its side; [[ADR-061 - Operator keep flag|ADR-061]] does not record that the ordering its
  addenda document (`order_by(event_start_utc.asc())`) was replaced here.
- **The partial `WHERE` clause is SQLite-only as written.** Both indexes are
  declared with `sqlite_where=` and no `postgresql_where`, in the model and in
  revision `0011`, so on the PostgreSQL target ([[ADR-004 - Database metadata, filesystem bytes|ADR-004]], [[ADR-007 - SQLite in developer mode|ADR-007]]) they
  would be built as full indexes and "a reclaimed row leaves the index" — the
  mechanism decision 1 rests on — would silently not hold. The same dialect gap
  as the `_bounded_statements` one recorded above.

**Reviewed again 2026-08-30: this ADR's own failure has recurred on a fourth
index, and the partial `WHERE` is why.** The Consequences above call the
low-selectivity index the recurring shape; the more useful generalisation, on
this evidence, is the one decision 1 already made and did not name as the
lesson: **a reclaimed row must leave the index the sweep walks.**
[[ADR-077 - Acoustic events keep no recordings|ADR-077]]'s tier bounds and orders
on `ix_detection_group_start`, which is a plain index, so every acoustic-event
detection the tier has already reclaimed — and the ~99% that never had a clip —
stays in front of the next live one for ever. Measured at this station's
cardinalities, the candidate query goes from 0.0095 s at zero reclaimed to
0.6968 s at 8,000 and 0.7286 s once there is nothing left to find; on the station
it aborts at 1.5 s every sweep. That is "the query got slower every single time
it succeeded", verbatim, four indexes later. The plan is correct throughout and a
regression test asserts it — which is the part worth carrying forward: a plan
assertion pins the plan, not how far the scan has to walk to reach its first row.

One smaller thing found the same day: `last_sweep_complete` initialises to
**`True`** (`src/open_observatory/retention.py:547`), so for the first sweep
interval after every restart `/api/v1/health` publishes
`retention_sweep_keeping_up: true` and its watermark problem cannot fire, on a
station that has swept zero times. The station restarted at 20:12:24Z during
this review and reported exactly that. Six minutes is short; it is also the
window in which a station that failed to start its sweep at all looks healthiest.

---
Part of the [[ADRS|Architecture Decision Record index]].
