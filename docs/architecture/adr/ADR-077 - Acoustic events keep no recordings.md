---
aliases:
  - ADR-077
tags:
  - adr
---
# ADR-077: Acoustic events keep no recordings
**Status:** accepted, 2026-08-30
**Relates to:** [[ADR-049 - Sound categories are not species|ADR-049]] (a car is not a taxon), [[ADR-026 - Tiered clip retention|ADR-026]] (the age tiers),
[[ADR-074 - Evidence kept by value|ADR-074]] (evidence kept by value), [[ADR-076 - The evidence bank is a column, not a recomputed set|ADR-076]] (the bank is a column)

### The decision

**A detection whose `taxonomic_group` is `acoustic_event` keeps no clip.** Its
media is reclaimed by the retention sweep on sight, regardless of age. The
detection row itself is kept forever, exactly as [[ADR-026 - Tiered clip retention|ADR-026]] requires — *"a
siren happened at 04:12"* stays in the record; the recording of it does not.

Controlled by `retain_acoustic_event_clips`, a live, UI-editable boolean,
shipping **False**.

### Why

This station's detector labels non-wildlife sound: `Engine`, `Dog`,
`Power tools`, `Siren`, `Human vocal`. [[ADR-049 - Sound categories are not species|ADR-049]] already settled what those
labels *mean* — they are correct detections of things that are not taxa, and a
range model returning 4e-06 for "Engine" is not saying engines are absent from
the garden. They are kept as detections for exactly that reason.

But keeping the **audio** of them was never argued for; it was inherited. The
station is a wildlife monitor. Nobody is going to review a recording of a car,
and three things follow from keeping them anyway:

- **Disk.** Measured on 2026-08-30: acoustic events hold **12.07 GB** across
  9,356 live assets and 7,790 detections. Against 372 GB used that is not the
  main course, but it is 12 GB of provably uninteresting audio.
- **Privacy.** `Human vocal` is an acoustic-event label. The charter's rule is
  that continuous human speech is not retained by default and evidence
  retention is bounded and configurable. A category that exists partly to
  catch human sound is the last one that should be archiving audio by default.
- **[[ADR-076 - The evidence bank is a column, not a recomputed set|ADR-076]]'s bank was quietly banking them.** The first live dry-run of
  `oo clips bank-backfill` proposed banking **200 permanent recordings each of
  Dog, Engine, Power tools and Siren** — 681 detections in total — because
  promotion filtered on `common_name IS NOT NULL` and the detector gives
  acoustic events a name. That is fixed separately, but it is what surfaced
  the question: if they are not worth banking, what are they worth keeping?

[[ADR-074 - Evidence kept by value|ADR-074]]'s own effect table lists `acoustic_event` as *"13.2 GB → 13.2 GB
(untouched) | existing tiers"*. That line was an assumption carried forward
from before anyone asked. This ADR asks, and answers differently.

### What this does not do

**It does not delete detections.** [[ADR-026 - Tiered clip retention|ADR-026]]'s rule stands: detection metadata
is kept forever and this sweep never touches it. Counts, timings, scores and
the acoustic-event history all survive intact. Only `media_asset` rows are
reclaimed and only their files unlinked.

**It does not touch birds or bats**, whose 197.51 GB and 174.63 GB are governed
by [[ADR-026 - Tiered clip retention|ADR-026]] and [[ADR-076 - The evidence bank is a column, not a recomputed set|ADR-076]] as before.

**It is not the near-miss reviewer's enemy.** If a future workflow needs to
*listen* to acoustic events, the operator turns `retain_acoustic_event_clips`
on and the ordinary age tiers apply to them again from that moment. Recordings
already reclaimed do not come back, which is why this is a setting a person
sets rather than a default nobody noticed.

### The first implementation could not finish, and took the watermark with it

Recorded because the failure was invisible to every test and only appeared
against the station's real cardinalities.

The tier's candidate query was copied from `_strip_native`, which orders by
`media_asset.created_at`. That ordering cannot be served by any index reachable
from this join, so SQLite materialised **every** acoustic-event detection's
assets and sorted them before returning a row — and there are **761,589** such
detections. The `LIMIT` never bit. Observed on the station:

    retention.statement_interrupted  after_s=1.5003  batch_budget_s=1.5  tier=acoustic_event
    tier_counts={'native': 18, 'unkept': 0}
    tiers_skipped=['acoustic_event', 'watermark']

Two failures, and the second is the serious one. The tier reclaimed nothing, so
this ADR did nothing. And because `acoustic_event` precedes `watermark` in
`_TIER_ORDER`, its abort marked the watermark skipped **every sweep** — the
valve that stops a full disk stopping capture, disabled by a policy tier.

The fix is one line of ordering. `ix_detection_group_start` is
`(taxonomic_group, event_start_utc)`, so bounding and ordering on
`event_start_utc` turns the equality and the range into a single seek and an
ordered walk:

| | plan |
|---|---|
| ordering by `media_asset.created_at` | three index seeks, then `USE TEMP B-TREE FOR ORDER BY` |
| ordering by `detection.event_start_utc` | `SEARCH detection USING INDEX ix_detection_group_start (taxonomic_group=? AND event_start_utc<?)`, **no temp b-tree** |

Measured after: **0.5223 s**, `interrupted_tier=None`, 196 assets reclaimed in
one sweep, against 1.5003 s and zero before.

The ordering now means "oldest event" rather than "oldest clip file". This tier
has no age policy at all, so that changes nothing anyone can observe beyond
which 200 go first.

The regression test asserts on `EXPLAIN QUERY PLAN`, not on the outcome,
because a test with a handful of seeded rows cannot feel a temp B-tree — which
is exactly why the original passed.

### Rules that must not be broken

1. **`kept_at` still outranks this.** An operator who explicitly kept an
   acoustic-event recording ([[ADR-061 - Operator keep flag|ADR-061]]) keeps it. A "keep" that a sweep can
   overrule is not a keep, and that is true for a siren as much as a heron.
2. **Detection rows are never deleted by this tier.** Media only.
3. **The first run is a dry run.** [[ADR-074 - Evidence kept by value|ADR-074]] rule 3 applies unchanged: this
   deletes files irreversibly, so its cost is reported for a human to read
   before anything is unlinked.
4. **Held detections ([[ADR-043 - Taxon correction|ADR-043]]) are exempt**, as they are from every age tier.

### Consequences

- About 12 GB is freed on first application, and acoustic-event audio stops
  accumulating.
- The `Human vocal` category stops writing audio to disk by default, which
  moves the station closer to the charter's privacy position rather than
  further from it.
- An operator who wants this audio has one checkbox to change, and the setting
  is live — no restart.

**Reviewed 2026-08-30:** the tier is deployed, it worked, and it has stopped.
Read from the station between 19:43Z and 20:01Z, 7.8 h into a run:
`acoustic_event_deleted` **2,174** assets / **1.27 GB** — so the ordering fix
above did reclaim, and the "196 assets reclaimed in one sweep" figure was real.
It is no longer what the tier does. Every sweep now ends
`last_interrupted_tier: "acoustic_event"`, `last_interrupted_after_s: 1.5005`,
`last_tiers_skipped: ["acoustic_event", "watermark"]`, and the counter did not
move across four consecutive sweeps (19:48:44Z, 19:55:07Z, 20:01:34Z, 20:07:00Z).
Those four sweeps reclaimed **28 assets and 93.8 MB between them** — all of it in
the `native` tier, none in this one.

**The fix is the ADR-062 failure, one index along.** `ix_detection_group_start`
is a plain index, not a partial one, so a reclaimed acoustic-event detection
**never leaves it** — and the tier must walk everything it has already done, plus
the ~99% of acoustic-event detections that never had a clip at all, to reach the
next live asset. That is [[ADR-062 - Retention walks live assets|ADR-062]]'s root
cause verbatim: *"the query got slower every single time it succeeded."*
Reproduced on a database built from the ORM metadata at this station's
cardinalities (761,589 acoustic-event detections, 7,790 of them clipped, 240,000
bird/bat detections, no `ANALYZE`), best of three, `LIMIT 200`:

| acoustic assets already reclaimed | rows returned | time |
|---|---|---|
| 0 | 200 | 0.0095 s |
| 2,000 | 200 | 0.1305 s |
| 4,000 | 200 | 0.3133 s |
| 8,000 | 200 | 0.6968 s |
| all 8,573 | **0** | 0.7286 s |

The plan is byte-identical at every row of that table — `SEARCH detection USING
INDEX ix_detection_group_start`, no temp B-tree — which is exactly what
`tests/test_retention.py:2177` asserts. **The plan was right and the query still
cannot finish**, so the regression test is the same class of blind spot its own
docstring claims to have closed: it pins the plan and says nothing about how far
the scan has to walk. The last row matters most: once the tier has finished its
work the query still costs most of the budget and returns nothing, for ever. This
is a permanent tax, not a backlog that drains.

Three consequences follow, and none of them is visible to an operator.

- **The watermark tier is marked skipped on every single sweep.** Below the line
  that is harmless — the hoist in
  [[ADR-064 - Watermark tier first|ADR-064]] runs it first when the disk is over
  the line, ahead of this tier — but it means `tiers_skipped` has carried the
  same two names for hours and says nothing when the condition changes.
- **`/api/v1/health` reports `status: "ok"`, `problems: []`.**
  `consecutive_barren_sweeps` is 0 because the `native` tier still deletes a
  handful, so [[ADR-062 - Retention walks live assets|ADR-062]]'s barren-sweep
  escalation cannot fire for a sweep that aborts in the same tier every time
  while another tier deletes seven files.
- **The tier is invisible to Prometheus.** `api/metrics.py:461` iterates
  `("native", "unkept", "watermark")`, so
  `oo_retention_files_deleted_total{tier="acoustic_event"}` and its bytes
  counterpart are not published at all. The 2,174 assets this tier did reclaim
  appear only in `/api/v1/station`.

The privacy consequence in the Consequences list is therefore **half true**.
2,174 of the 9,356 live acoustic-event assets measured above were reclaimed; the
remaining ~7,200, plus everything written since, are still on disk, and the tier
is no longer removing them. `retain_acoustic_event_clips` gates the *reclaim*,
not the *write*, so `Human vocal` audio is still being written and is now not
being taken away again. "About 12 GB is freed on first application" reads as
1.27 GB and stopped.

**Confirmed 2026-08-31, on a second and third process, and the evidence is now
much sharper.** The station restarted at 2026-08-30 20:12:24Z and again at
2026-08-31 14:04:47Z. In the **7.0 hours** since that second restart the tier has
reclaimed **exactly 200 assets — one batch, 116,899,200 bytes — and then
nothing**, while every sweep since has ended `last_interrupted_tier:
"acoustic_event"`, `last_interrupted_after_s: 1.5003`, `last_tiers_skipped:
["acoustic_event", "watermark"]`. `preamble_s` is 0.0108 s, so the preamble is
again not the cause.

"One batch, then never again" is the degradation curve above read off the
station: the candidate query sits close enough to the 1.5 s budget that it
succeeds occasionally and aborts the rest of the time, and each success moves it
further the wrong way, because the 200 rows it just reclaimed stay in
`ix_detection_group_start` in front of everything it has not done. A restart does
not help — the reclaimed rows are in the database, not in the process.

Meanwhile `native` reclaimed **908 assets / 3.92 GB** over the same 7.0 hours, so
the sweep as a whole is working and only this tier is stuck; `unkept` is still
**0** and `eligible_for_deletion` still **0 clips**, 26 days into the archive; and
`/api/v1/health` still reports `status: "ok"`, `problems: []`, with
`retention_sweep_keeping_up: false` inside the payload and nothing escalating it.

---
Part of the [[ADRS|Architecture Decision Record index]].
