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

| [[ADR-074 - Evidence kept by value|ADR-074]] wanted | how it now follows |
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

### Still outstanding

- **The backfill dry-run has not been run on the station.** ADR-074 rule 3
  requires it before the flag is enabled, and it is the only prediction here
  still untested against real data — the expected shape is ~7,302 detections
  across ~135 species, from a lab copy of the archive. Not run because a
  concurrent session was measuring the station's clock and a deploy restarts
  capture. **This is a precondition on rollout, not a nicety.**
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

---
Part of the [[ADRS|Architecture Decision Record index]].
