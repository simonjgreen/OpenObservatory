---
aliases:
  - ADR-053
tags:
  - adr
---
# ADR-053: Taxonomic grouping above species — genus is free, family is a data dependency
**Status: proposed. Nothing is implemented.** Recorded because the question has
a tempting wrong answer, and because the cheap option and the correct option are
different things that are easy to conflate.

**The question.** Does the detector expose any layer of classification between a
species name and nothing at all — families, for instance? Can it group corvids?

**The answer, measured rather than assumed.** No.

`models/birdnet_labels.txt` is 6,522 lines of `Scientific name_Common name` and
nothing else. It contains zero occurrences of "family", "order" or "Corvidae".
BirdNET GLOBAL 6K V2.4 emits one score per label at species level; there is no
hierarchy in the asset.

Two fields in our own schema look like they might help and do not. Values as
they actually stand in the live database:

| field | values |
|---|---|
| `rank` | `species` (9,620) or `None` (61,582) |
| `taxonomic_group` | `bird` (9,620), `bat` (5,532), `acoustic_event` (56,050) |

`taxonomic_group` answers "what kind of claim is this" — bird, bat, or a sound
that is not an animal ([[ADR-049]]). It is not a taxonomic rank and must not be
pressed into service as one.

**What is free.** The genus is the first token of the binomial, so grouping by
genus needs no new data, no dependency and no licence — 1,843 distinct genera
across the label file, derived exactly from what we already store.

**Why that is not sufficient, using the operator's own example.** Corvids
recorded by this station:

| n | species | genus |
|---|---|---|
| 785 | Eurasian Jackdaw | *Corvus* |
| 240 | Rook | *Corvus* |
| 12 | Carrion Crow | *Corvus* |
| 7 | Common Raven | *Corvus* |
| 25 | Common Magpie | *Pica* |
| 1 | Eurasian Jay | *Garrulus* |

Grouping by genus captures 1,044 of those and **silently drops the Magpie and
the Jay**, which are Corvidae. A "corvids" view built on genus would be
confidently incomplete, which the honesty constraint forbids more strongly than
it forbids having no such view at all. Worse, some authorities place the
Jackdaw in *Coloeus* rather than *Corvus*, so even the largest group depends on
which taxonomy the label list happened to follow.

**Decision (proposed).**

1. **Genus grouping may ship as genus**, labelled as genus, never as family. It
   is exact, free, and honest about its own scope.
2. **Family and order require a real taxonomic reference** — a checksummed,
   versioned, separately-licensed data file acquired the way model assets are
   ([[ADR-006]]), not a table typed into the source. eBird/Clements is the natural
   choice because BirdNET's own labels derive from it, so the join is clean
   rather than fuzzy-matched.
3. **A hardcoded list of corvid species is refused.** It is the tempting answer,
   it works for the example that prompted the question, and it rots silently the
   first time a species is added or a genus is revised. Naming it here so a
   successor under time pressure has to argue with this paragraph first.
4. **Whatever ships must handle labels that do not resolve.** A species absent
   from the reference is reported as ungrouped, never assigned to a plausible
   family by prefix matching.

**Relationship to [[ADR-043]].** [[ADR-043]] argued against introducing a taxonomy
dependency when the payoff was a single mislabelled `Gray Wolf` row. That
reasoning stands for that case. The payoff here is larger — family-level
browsing and history for a general user — so this is a fairer trade than it was
then, and is why this is recorded as a proposal rather than a refusal.

**Cost, honestly.** Genus is hours. Family is a day or more once acquisition,
checksums, licence documentation, the unresolved-label path and the UI are
counted. Neither should displace the 72-hour soak.

---
Part of the [[ADRS|Architecture Decision Record index]].
