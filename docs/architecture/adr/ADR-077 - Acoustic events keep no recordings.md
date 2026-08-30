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

---
Part of the [[ADRS|Architecture Decision Record index]].
