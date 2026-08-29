---
aliases:
  - ADR-064
tags:
  - adr
---
# ADR-064: The watermark tier runs first when disk is already over the line
**Status:** accepted, 2026-08-19
**Amends:** [[ADR-026 - Tiered clip retention|ADR-026]], [[ADR-062 - Retention walks live assets|ADR-062]]

### The problem

Retention's three tiers share one `batch_size` budget and ran in a fixed order:
`native`, `unkept`, `watermark`. The watermark tier is the disk-pressure safety
valve — "whatever the age policy says, if the filesystem is over the line,
reclaim the oldest unkept clips" — and it was last.

So it was skipped whenever either age tier had a backlog large enough to spend
the budget. That is not a rare coincidence: a native backlog and a filling disk
have the same cause, so the tier was most likely to be skipped exactly when it
was most needed.

[[ADR-062 - Retention walks live assets|ADR-062]]'s fix demonstrated it within minutes of deploying. The first healthy
sweep in two days reclaimed its full batch and reported:

    total_deleted=200  tier_counts={'native': 200}
    tiers_skipped=['unkept', 'watermark']

Correct behaviour by the old rules, and the safety valve still did not run.

This also explains why the [[ADR-062 - Retention walks live assets|ADR-062]] incident was as dangerous as it was. The
narrative "the disk climbed toward an 85% watermark" quietly assumed something
would happen at 85%. Nothing would have: the watermark tier was inside the
sweep that was failing, and *behind* the tier that was failing.

### Decision

When `_disk_over_watermark()` is true at the start of a sweep, run the watermark
tier **first**, then the age tiers with whatever budget remains. Below the
watermark, the order is unchanged and the cost is a single `shutil.disk_usage`
call.

Deliberately *not* done: giving the watermark tier its own separate budget.
That would let a sweep do up to twice the configured work, and the whole point
of `batch_size` and `retention_batch_budget_s` is that one sweep's cost is
bounded — [[ADR-021 - Clips on their own device|ADR-021]] and [[ADR-033 - Retention is paced|ADR-033]] measured what unbounded housekeeping does to
capture. Reordering changes which work a bounded sweep chooses, not how much it
does.

### Consequences

- Over the watermark, the age tiers are the ones that get starved. That is the
  correct inversion: reclaiming oldest-first to keep the filesystem alive
  matters more than degrading 7-day-old clips on schedule, and the age tiers
  catch up on the sweeps after the disk comes back under the line.
- `kept` recordings remain exempt from the watermark tier, unchanged. A station
  that cannot get under the watermark without deleting kept evidence still
  reports the problem and waits for a human ([[ADR-061 - Operator keep flag|ADR-061]]).
- A sweep now calls `shutil.disk_usage` up to three times: once for
  `disk_used_ratio_before`, once for this guard, once inside
  `_watermark_reclaim`. Measured at ~20 µs each on the station; not worth
  caching, and a cache is exactly the kind of staleness that makes a safety
  valve unreliable.

**Reviewed 2026-08-29:** the decision holds — the promotion is at
`src/open_observatory/retention.py:440` and `TestWatermarkTierIsNotShadowedByTheAgeTiers`
(`tests/test_retention.py:2004`) covers both sides of the guard. The call count
above is one short: `sweep()` also reads the ratio at the end for
`disk_used_ratio_after` (`src/open_observatory/retention.py:550`), a call that
predates this ADR, so an over-the-watermark sweep makes four `shutil.disk_usage`
calls and an ordinary one three. The cost argument is unchanged.

---
Part of the [[ADRS|Architecture Decision Record index]].
