---
aliases:
  - ADR-076
tags:
  - adr
---
# ADR-076: The evidence bank is a column, not a recomputed set
**Status:** accepted, 2026-08-29
**Amends:** [[ADR-074 - Evidence kept by value|ADR-074]] — its *policy* stands unchanged; its *mechanism* is replaced
**Measurements:** [[EVIDENCE_BANK_MEASUREMENTS_2026-08-29]]
**Relates to:** [[ADR-026 - Tiered clip retention|ADR-026]] (age tiers and watermark), [[ADR-062 - Retention walks live assets|ADR-062]] (the 1.5 s batch budget),
[[ADR-064 - Watermark tier first|ADR-064]] (watermark first when over the line), [[ADR-061 - Operator keep flag|ADR-061]] (the keep flag, and the
index that wedged the station), [[ADR-049 - Sound categories are not species|ADR-049]] (plausibility bands), [[ADR-032 - Plausibility bands|ADR-032]] (the offline
reconcile command this borrows its shape from)

[[ADR-074 - Evidence kept by value|ADR-074]] decided *what* to keep and was right about it. It then shipped a
mechanism that cannot deliver it, and recorded two blocking defects of its own.
This ADR keeps every word of the policy and replaces the mechanism.

### Four defects, not two

[[ADR-074 - Evidence kept by value|ADR-074]] records the first two. The third and fourth were found while fixing
them, and the fourth is the one that matters most.

**1. The bank is a per-species cliff.** `_derive_bank` returns a set of species
*names*; the exclusion exempts every clip of a named species. The moment a
species reaches the cap the exemption vanishes for its whole back-catalogue at
once, and both age tiers order `created_at ASC` — so **the first-ever recording
of a species is the first thing deleted**. The same cliff fires when an operator
adds a banked species to the common list, contradicting [[ADR-074 - Evidence kept by value|ADR-074]]'s stated
consequence that this "does not retroactively delete its history".

**2. The census is unaffordable.** Measured on the station: **18.3219 s**
against a **1.5 s** budget, inside the capture process. Not marginal — twelve
times over, with a `TEMP B-TREE` and a scan of all 362,703 `detection_media`
rows. The 900 s TTL does not rescue a statement that blocks for eighteen
seconds when it does run.

**3. The bank is measured in the wrong unit for bats.** `classify()` is handed
a *pass* count and compares it against a *clip* budget, so a sparse band holding
more than `bank_size` passes is never banked at all — the rarest signal the
station has, excluded by an arithmetic slip.

**4. The policy as implemented cannot save a single byte.** This is not
recorded anywhere in [[ADR-074 - Evidence kept by value|ADR-074]] and it is the most important fact about it.
The bank is applied **only** as a `WHERE` exclusion on the two age tiers'
candidate queries. It can only ever *narrow* what is deleted. Turning
`evidence_value_enabled` on today makes disk usage go **up**.

Every saving in [[ADR-074 - Evidence kept by value|ADR-074]]'s "Expected effect" table — 388.8 GB down to ~28 GB —
comes from the daily quota and the bat 1% sample, and **neither exists**.
`Verdict.QUOTA` and `Verdict.SAMPLE` are tallied in the dry-run report and are
otherwise behaviourally identical to `EXPIRE`. That table describes a policy
nobody has written yet.

**5. And the watermark destroys the archive first.** `_watermark_reclaim`
orders `MediaAsset.created_at ASC` and is not passed the bank at all. Under
disk pressure the emergency valve therefore reclaims *the oldest clips on the
disk* — which, once the bank works, is precisely the banked set. The archive
would be the first thing sacrificed to protect the disk.

### The decision

**The bank becomes a persisted, nullable, indexed column on `detection`:**
`banked_at`. A detection is banked or it is not, and the answer is a fact
about that row rather than a set recomputed from scratch every sweep.

Everything [[ADR-074 - Evidence kept by value|ADR-074]] wants then falls out of one property: **`banked_at` is
monotone.** Once set it is never cleared except by the watermark tier actually
reclaiming that detection's evidence.

| [[ADR-074 - Evidence kept by value\|ADR-074]] wanted | how it now follows |
|---|---|
| "up to K clips per species that never age out" | the K rows carrying `banked_at`; the cap is enforced at *promotion*, once, not re-litigated per sweep |
| no cliff at the cap | reaching the cap stops *promotion*; it unbanks nothing |
| the first-ever recording is the most protected | it is promoted first and never demoted |
| adding to the common list does not delete history | the list gates promotion only; banked rows keep `banked_at` |
| the bank forgets what the watermark reclaimed | the watermark clears `banked_at` when it reclaims a banked detection, freeing the slot |
| bats banked by band | the same column, promoted by band instead of by name |

The exclusion the age tiers apply collapses from a 127-arm `OR` over species
names to a single predicate: `detection.banked_at IS NULL`.

### Promotion, and where the expensive part goes

The cost that made the census unaffordable does not disappear — it moves to
where there is no budget to break.

**One-off backfill, offline.** `oo retention bank-backfill` banks the oldest
live detections of each species and each sparse band, up to the cap. Measured:
**8.9753 s** for the whole archive, banking **7,302 detections** across 135
species. Unbudgeted, outside the capture hot path, and the same shape as
`oo detections reconcile-plausibility` ([[ADR-032 - Plausibility bands|ADR-032]]) — a command that walks the
archive under no time budget precisely so the sweep never has to.

**Incremental promotion, in the sweep, bounded.** Only species and bands below
their cap can be promoted, and only their oldest unbanked live detections are
candidates, ordered and `LIMIT`ed. A species at its cap is skipped without a
query. The work is bounded by `batch_budget_s` like everything else, and an
abort means *fewer promotions this sweep*, never a failed sweep.

**The census that remains** is "how many detections has each species banked",
an index-only scan of the ~7,300 banked rows: **0.0023 s**, against 18.3219 s.

### The index, and the mistake it is avoiding

`ix_detection_banked_partial` is `(common_name, event_start_utc)`
**partial on `banked_at IS NOT NULL`**.

Partial deliberately, and this is the whole reason the shape is spelled out
here. [[ADR-061 - Operator keep flag|ADR-061]] revision 0009 records a plain index on `kept_at` — NULL for
99.8% of rows — that SQLite preferred for the `IS NULL` filter, costing the
planner `ix_detection_event_start_utc` and turning an ordered indexed scan into
a temp B-tree sort. **That blocked one sweep inside a single statement for over
five minutes and wedged the housekeeping loop behind it.** `banked_at` is NULL
for 99.2% of rows: the identical trap.

A partial index cannot serve `banked_at IS NULL`, so the planner cannot reach
for it in a tier's candidate query. Measured, rather than assumed: the plan for
`_strip_native`'s candidate query is byte-identical with and without the new
predicate. See [[EVIDENCE_BANK_MEASUREMENTS_2026-08-29]] for the caveat on where
that was measured, and the confirmation still owed on the station.

### The watermark prefers unbanked, and still always wins

`_watermark_reclaim` gains a preference, not an exemption. It reclaims in two
passes: unbanked material oldest-first, and only if that did not free enough,
banked material oldest-first — clearing `banked_at` as it goes.

[[ADR-074 - Evidence kept by value|ADR-074]]'s rule 1 is untouched: value never overrides the watermark, and a
disk that can only be saved by deleting the archive still gets the archive
deleted. What changes is the *order*. "The watermark may delete banked
evidence" and "the watermark deletes banked evidence first" are not the same
rule, and only the first one was ever intended.

This means the bank must be computed **before** the watermark tier, reversing
[[ADR-074 - Evidence kept by value|ADR-074]]'s ordering. That ordering existed because the census cost eighteen
seconds and must never delay the one tier that stops a full disk stopping
capture. At **0.0023 s** that reason is gone. The safety property is kept
another way: if the census aborts or the flag is off, the bank is `None` and
the watermark behaves exactly as it does today.

### Plausibility: an operator list, as [[ADR-074 - Evidence kept by value|ADR-074]]'s second amendment concluded

`evidence_implausible_species`, mirroring `evidence_common_species` — a
`tuple[str, ...]`, UI-editable, `tier="live"`. A species on it is promoted to a
cap of `implausible_cap` (3) instead of `bank_size` (200). Zero schema change
beyond the column above, zero migration of its own, zero extra query: it is one
more lookup in a dict the promotion step already builds.

Pre-populated with the five [[ADR-074 - Evidence kept by value|ADR-074]] names and measured at one detection each:
Chestnut-backed Chickadee, California Quail, Asian Brown Flycatcher, Eastern
Screech-Owl, Grey-winged Inca-Finch.

This closes the gap [[ADR-074 - Evidence kept by value|ADR-074]]'s first amendment left open, and closes it the
cheap way its second amendment identified rather than the expensive way the
first one proposed.

### What this ADR does **not** do

**It does not implement the quota, and therefore does not deliver
[[ADR-074 - Evidence kept by value|ADR-074]]'s savings.** Defect 4 above is diagnosed here, not fixed. After
this ADR the policy is still exemption-only: it makes the bank correct,
affordable and safe to enable, and enabling it will still cause disk usage to
rise by the size of the bank — measured at **7,302 detections, roughly 28 GB**,
bounded and one-off.

That is a deliberate stopping point, not an oversight. The bank half is worth
having on its own: it is what makes the heron, the kingfisher and the 60 kHz
bat pass permanent instead of losing them to a 30-day timer. The quota half
deletes clips that survive today, on a code path that unlinks files, and it
wants its own ADR, its own dry-run and its own measurement. [[ADR-074 - Evidence kept by value|ADR-074]]'s
"Expected effect" table remains unmet until it exists, and is marked as such.

### What shipped, 2026-08-29

Implemented across nine tasks; the plan is [[2026-08-29-evidence-bank-redesign]].
Full suite **1,096 passed, 12 skipped, exit 0**, ruff clean, mypy clean.

| defect | state |
|---|---|
| 1. the per-species cliff | **fixed** — `banked_at` is monotone; `TestTheCliffIsGone` fails against any boolean-per-species bank |
| 2. the 18.32 s census | **deleted**, not mitigated. Replaced by one index-only pass over the banked rows |
| 3. bat bank in the wrong unit | **fixed** — banded detections compared against the cap on both sides |
| 4. the policy cannot save a byte | **not fixed, by design** — see below |
| 5. the watermark eats the archive first | **fixed** — two passes, unbanked first, and the second still takes banked evidence when it must |

Four things are worth recording because they were not obvious going in.

**The promotion query was nearly the census again.** The first draft grouped
live assets by species — measured at **5.9754 s**, and still **2.6672 s**
bounded to a two-hour window, because SQLite scans `detection_media` whole
regardless of a time bound on the far side of the join. Bounding the window is
not the fix; not joining is. The shipped query touches no media table and runs
in 0.0149 s, and liveness is checked only on the handful about to be promoted,
at 0.46 ms each.

**The ADR-061 plan check needed neither the station nor its data.** The station
carries no `ANALYZE` statistics, so SQLite's planner is schema-driven; a
zero-row database built from the ORM metadata reproduces its plan line for
line. It does, and `banked_at IS NULL` changes nothing.

**The watermark's `bank is None` path had to be made genuinely identical.** An
early version still cleared `banked_at` when the flag was off, so an operator
disabling the policy after evidence was banked would have had it silently
nulled on the next reclaim.

**`_PROMOTION_LOOKBACK` is 24 hours, and that is a real limit.** The sweep
promotes only from a trailing window; everything older is
`oo clips bank-backfill`'s job. A station that was down for a week catches up by
running that command, not by widening the window.

### The Critical the task reviews missed

Nine task reviews passed this work. The final whole-branch review found a
**Critical** that none of them did, and it is worth recording because of *why*
they missed it.

`_watermark_pass` iterates one row per media **asset**, but a detection carries
two to four (`evidence_native`, `playback`, sometimes `audible_ultrasonic`), and
the loop breaks part-way through a detection as soon as it has freed enough
bytes. A banked detection was marked unbanked the moment **one** of its assets
was staged. So: the first-ever Grey Heron loses its `evidence_native`, keeps its
`playback`, and has `banked_at` nulled — and on the next sweep that surviving
clip is the oldest unbanked asset on the disk, and the age tier deletes it.

**The exact defect this ADR exists to prevent, restored by a different route.**

It survived nine reviews because every watermark test in this work seeded
`kinds=("evidence_native",)` — a single asset. The tests never exercised the
multi-asset shape that is normal in production. The fix re-reads which banked
detections still hold a live asset after the flush, and clears `banked_at` only
for the ones genuinely emptied; the regression test now seeds the helper's
two-asset default.

Two more the same review found: an aborted bank read propagated with
`current_tier == "preamble"`, which is not in `_TIER_ORDER`, so **all three
tiers were skipped including the watermark** — the disk-fills-capture-stops
failure, in code whose own comment promised the opposite. And
`bank_backfill --dry-run` executed ~7,300 `UPDATE`s before rolling back, holding
SQLite's write lock for ~9 s against a 5 s `busy_timeout` on a station that
*drops* detections rather than retrying them: the safety step ADR-074 rule 3
mandates was itself unsafe. Both fixed.

### One pacing trade, recorded rather than left implicit

The watermark tier now runs **before** the sweep's preamble, and on one narrow
path — an interrupted evidence-bank read, reachable only when
`evidence_value_enabled` is on — it is granted a **fresh, bounded** deadline of
one extra `batch_budget_s`. So that sweep can occupy about **twice** the pacing
bound, 3.0 s at the default, of GIL-holding ORM work.

That exceeds what ADR-033 was written to enforce, and it is deliberate. It is
ADR-064's own trade taken to its conclusion: *"a safety valve downstream of the
thing it protects against is not a safety valve."* A sweep that overruns its
pacing budget costs a few late reads; a watermark tier that never runs costs a
full disk, and a full disk stops capture altogether. The overrun is bounded (one
extra budget, no loop, no reset), applies to no healthy sweep, and cannot occur
at all while the flag ships `False`.

Recorded here because it was found in review and would otherwise be an
undocumented violation of ADR-033 sitting in the hot path.

### Still outstanding

- **The backfill dry-run has not been run on the station.** ADR-074 rule 3
  requires it before the flag is enabled, and it is the only prediction here
  still untested against real data — the expected shape is ~7,302 detections
  across ~135 species, from a lab copy of the archive. Not run because a
  concurrent session was measuring the station's clock and a deploy restarts
  capture. **This is a precondition on rollout, not a nicety.**
- **A pre-existing hole, found by this work and deliberately not fixed by it.**
  An abort in the sweep's *first* preamble block — the `kept_detections` count
  and `held_detection_ids` — still propagates with `current_tier == "preamble"`
  and skips all three tiers **including the watermark**. That is the same
  disk-fills-capture-stops failure fixed above for the bank read, on a path
  that predates ADR-076 entirely. Left alone because it is outside this ADR's
  scope and the status quo is unchanged by leaving it, but it wants its own
  fix and it is the most consequential thing on this list.
- **The quota is still not built**, so ADR-074's "Expected effect" table is
  still unmet. Enabling the flag after the backfill will *raise* disk usage by
  the size of the bank — bounded, one-off, roughly 28 GB — and save nothing.
  That is the honest trade: a permanent archive of the rare material, paid for
  in disk, until the quota exists.

### Rules that must not be broken

1. **`banked_at` is monotone.** Nothing clears it except `_watermark_reclaim`
   actually reclaiming that detection's evidence, in the same transaction. A
   sweep that recomputes the bank and finds a row "should not" be banked must
   leave it alone. The cliff is exactly what a non-monotone bank produces.
2. **The index stays partial.** A plain index on `banked_at` re-creates the
   [[ADR-061 - Operator keep flag|ADR-061]] failure that wedged the station for five minutes. Any change to
   it must re-run the plan check in [[EVIDENCE_BANK_MEASUREMENTS_2026-08-29]].
3. **The watermark still always wins.** The preference for unbanked material is
   an ordering, never an exemption. A pass that cannot free enough from
   unbanked material must go on to reclaim banked material.
4. **`kept_at` outranks `banked_at`.** [[ADR-061 - Operator keep flag|ADR-061]]'s operator flag is exempt from
   every tier including the watermark. The bank is not, and must never be
   presented to an operator as though it were.
5. **The flag stays off until the backfill has run and its dry-run has been
   read by a human.** [[ADR-074 - Evidence kept by value|ADR-074]] rule 3, unchanged and still binding.

### Consequences

- One migration: a nullable column and one partial index. Additive and
  reversible; no existing column changes meaning.
- The sweep gets *cheaper* than it is today, not more expensive — the 18.3 s
  census is deleted outright rather than mitigated.
- `RetentionSweeper` gains promotion, which is the first thing in the sweep
  that writes to `detection` rather than to `media_asset`. It is bounded by the
  same deadline as every other statement and degrades to "promoted nothing".
- An operator can see the bank: `banked_at` is a column, so "what is in the
  archive, and since when" is a query rather than an inference.
- [[ADR-074 - Evidence kept by value|ADR-074]]'s `_banked_counts`, `_derive_bank`, `_EvidenceBank.species` and
  `_EvidenceBank.bands` are deleted, along with `_EVIDENCE_CENSUS_TTL_S`,
  `_ASSUMED_BAND` and the census-abort machinery built to survive them.

### Reviewed 2026-08-30

**The "Still outstanding" list is one item out of date, and it is the item it
called the most consequential.** The pre-existing hole — an abort in the sweep's
first preamble block propagating with `current_tier == "preamble"` and skipping
all three tiers including the watermark — **is fixed in the tree**. `sweep()`
now hoists the evidence-bank read and the watermark tier above the preamble
(`src/open_observatory/retention.py:766`) and wraps the preamble in its own
`except OperationalError` that sets `preamble_aborted`, skips only the tiers that
depend on `held_ids`, and lets the sweep continue. The watermark is never gated
on that flag. The commits are `f2b4210`, `9b2ec66` and `17165be`; the "One pacing
trade" section above was added for the same work and the outstanding list was not
revised alongside it.

**The other two items are still outstanding, and one of them now has a
deadline.** `evidence_value_enabled` is `false` on the station, `bank_size_now`
is `0`, and `oo clips bank-backfill` has not been run — so nothing carries
`banked_at`. That is rule 5 being honoured, and it means defect 5's fix is inert:
`_watermark_reclaim`'s unbanked-first preference has no banked material to
prefer. The disk is filling at ~2.14 GB/h and is about **7.7 hours** from the
85% watermark (measured 2026-08-30 20:01Z; see the 2026-08-30 note on
[[ADR-064 - Watermark tier first|ADR-064]]). When the valve fires it will reclaim
oldest-first across the whole archive, which is exactly the first-ever recordings
this ADR exists to protect. **Running the backfill before the watermark is
reached is the difference between defect 5 being fixed and being fixed on
paper.**

**A fourth index has now been added to a hot path without the plan check this
ADR's rule 2 requires.** [[ADR-077 - Acoustic events keep no recordings|ADR-077]]
put a tier's range predicate and `ORDER BY` on `ix_detection_group_start`, which
is **not partial** — so a reclaimed acoustic-event detection never leaves it, and
the tier's candidate query degrades with every sweep that succeeds. Its
regression test asserts the plan, exactly as rule 2 asks, and the plan is right
and unchanged the whole way down. Measured degradation is in that ADR's own
2026-08-30 note. The generalisation worth adding to rule 2: **a plan assertion
pins the plan, not the length of the scan**, and
[[ADR-062 - Retention walks live assets|ADR-062]]'s partial-index mechanism was
about the second.

---
Part of the [[ADRS|Architecture Decision Record index]].
