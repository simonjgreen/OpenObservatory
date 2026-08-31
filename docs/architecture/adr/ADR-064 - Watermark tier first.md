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

**Reviewed 2026-08-30: the hoist is still in force, at
`src/open_observatory/retention.py:766` — it has moved from the line the note
above gives, because
[[ADR-076 - The evidence bank is a column, not a recomputed set|ADR-076]] lifted
it further, ahead of the preamble and the evidence-bank read as well as the age
tiers. It has never run on this station.**
`oo_retention_files_deleted_total{tier="watermark"}` reads **0**, and the disk
has sat at 81.6-81.7% throughout. So this decision is designed, built, unit
tested — `TestWatermarkTierIsNotShadowedByTheAgeTiers` — and **never once
exercised against a real disk**. Everything below is a projection from the code
and from measurements taken at 81.7%, and should be read as such.

Two things a reader should carry forward.

**The deferred call now reports `watermark` skipped on every sweep, and that is
noise.** Below the line the hoist does not fire, so the tier is reached at
`src/open_observatory/retention.py:981` with whatever budget and deadline
survive the age tiers — and since
[[ADR-077 - Acoustic events keep no recordings|ADR-077]]'s tier consumes the
whole budget, it never does. The station has reported
`last_tiers_skipped: ["acoustic_event", "watermark"]` continuously. This is the
*harmless* case — a tier that had nothing to do was not called — but it is
spelled identically in `tiers_skipped` to the failure this ADR was written to
fix, so the one field an operator would check to see whether the valve is
shadowed now says "shadowed" permanently and means nothing by it.

**What happens at 85%, worked through because nothing else records it.**
Measured 2026-08-30 20:10Z: `disk_free_bytes` 89,711,001,600 of 491,106,488,320,
falling at **2.20 GB/h** net over the preceding nine minutes — clips are written
faster than retention reclaims them, and four consecutive sweeps reclaimed
**28 assets and 93.8 MB between them**, about **0.31 GB/h**. The watermark is
16.0 GB away, about **7.3 hours**. At that point:

- the hoist fires, the tier runs first with the full 200-asset batch, and its
  candidate query is the one
  [[ADR-062 - Retention walks live assets|ADR-062]] measured at 0.0032 s, served
  by the partial `ix_media_asset_live_created` — so, unlike
  [[ADR-077 - Acoustic events keep no recordings|ADR-077]]'s, it should finish;
- it stops at `freed >= bytes_over`, so it reclaims roughly the 245 MB accrued
  since the last sweep — about 150 assets of the 200 available. It should hold,
  with ~25% of a batch in reserve, and the disk should sit just above 85%
  indefinitely rather than filling;
- **`freed` counts claimed bytes, not freed ones.**
  `src/open_observatory/retention.py:2021` adds `asset.byte_length`
  unconditionally, including for a row `_stage_delete` has just recorded as
  `existed_on_disk=False`. A run of rows whose files are already gone — the
  8,067 contiguous ones [[ADR-057 - Evidence rows must be checkable|ADR-057]]
  found were a block at the *oldest* end, which is exactly where this tier
  starts — would let the valve declare itself satisfied having freed nothing.
  It self-corrects across sweeps, because a reclaimed row leaves the partial
  index, but each affected sweep is a no-op;
- the tier reclaims **oldest-first, ignoring kind and tier**, so what goes is
  the oldest playback renderings — the audible clips a person would actually
  listen to — while 230 GB of full-rate native audio under seven days old is
  untouched. That is [[ADR-026 - Tiered clip retention|ADR-026]]'s NVR rule
  working as specified; it is worth saying out loud that "the disk is managed"
  and "the oldest evidence is what gets destroyed" are the same sentence here;
- `banked_at` protects nothing, because `evidence_value_enabled` is off and
  `oo clips bank-backfill` has never been run — so
  [[ADR-076 - The evidence bank is a column, not a recomputed set|ADR-076]]'s
  unbanked-first preference is inert and the first-ever recordings are at the
  front of the queue;
- `held` detections ([[ADR-043 - Taxon correction|ADR-043]]) are not exempt here
  — 62 of them on the station — as
  [[ADR-061 - Operator keep flag|ADR-061]]'s "known asymmetry" records;
- and `/api/v1/health` will start reporting `status: "degraded"` and stay there,
  because its watermark problem is gated on
  `disk_used_ratio > watermark_ratio and not last_sweep_complete`, and
  `last_sweep_complete` has been `false` on every sweep since
  [[ADR-077 - Acoustic events keep no recordings|ADR-077]] deployed. The station
  will therefore say "the retention sweep is not keeping up" permanently, at the
  moment the sweep starts keeping up.

**Correction, 2026-08-31: the fill rate above is wrong by a factor of 6.5, and
the "7.3 hours" it produced did not happen.** Left as written, because the way it
was got wrong is the point. 2.20 GB/h was measured over a **nine-minute** window
on a summer evening and extrapolated; clip volume on this station is strongly
diurnal, so that window is near its daily peak. Measured over the following
**24.75 hours** instead — `disk_free_bytes` 89,711,001,600 at 2026-08-30 20:10:01Z
to **81,352,847,360** at 2026-08-31 21:04:58Z, a filesystem figure that survives
the two service restarts in between — the real rate is **0.338 GB/h**.

The direction was right and the arithmetic was not. As of 2026-08-31 21:05Z the
disk is at **83.44%** with **7.69 GB** to the watermark, about **23 hours** away
rather than seven. Everything else in the note above stands: the tier has still
never run (`{tier="watermark"} 0`), nothing is banked, `eligible_for_deletion` is
still 0 clips, and `/api/v1/health` still reports `status: ok, problems: []` with
`retention_sweep_keeping_up: false` sitting inside the payload.

This is the project's own recorded failure mode — a short window read as a rate —
committed inside a review that exists to find it. The rule it breaks is the one
`MEMORY.md` states as *"measure the instrument, not just the thing"*: a
nine-minute sample of a diurnal quantity is not a daily rate, and it should have
been labelled as a bound rather than published as a projection.

---
Part of the [[ADRS|Architecture Decision Record index]].
