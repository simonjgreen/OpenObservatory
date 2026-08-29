---
aliases:
  - ADR-074
tags:
  - adr
---
# ADR-074: Evidence is retained by value, not by age
**Status:** accepted, 2026-08-29
**Supersedes:** age-only retention as the sole policy ([[ADR-026 - Tiered clip retention|ADR-026]]'s tiers remain as
the backstop)
**Relates to:** [[ADR-049 - Sound categories are not species|ADR-049]] (plausibility bands), [[ADR-026 - Tiered clip retention|ADR-026]] (the age tiers and the
watermark), [[ADR-062 - Retention walks live assets|ADR-062]] (the sweep that applies them), [[ADR-064 - Watermark tier first|ADR-064]] (the watermark tier
runs first when disk is already over the line), [[ADR-061 - Operator keep flag|ADR-061]] (the keep flag), [[ADR-017 - BatDetect2 as an optional adapter|ADR-017]]

### The problem, measured

The station holds **234,761 live clips totalling 388.8 GB** on a 458 GB SSD.
Nothing in the retention path asks whether a clip is *worth* keeping — only how
old it is (7 days native, 30 days audible, then a watermark sweep at 85%).

Where it goes:

| | | |
|---|---|---|
| bird `evidence_native` | 133.2 GB | bat `audible_ultrasonic` 132.9 GB |
| bird `playback` | 54.4 GB | bat `evidence_native` 40.1 GB |
| acoustic_event (all) | 13.2 GB | bat `playback` 15.1 GB |

Birds 48%, bats 48%, everything else 3%. And within birds the distribution is
brutally skewed — 143 species, but:

```
European Robin      38,016 clips   68.4 GB   36.5% of bird evidence
Common Woodpigeon   42,816 clips   51.8 GB   27.6%
--> those two alone = 120.2 GB = 31% of the entire SSD

all 91 species with fewer than 50 clips = 1.62 GB = 0.87%
```

**The decisive fact: the cost is concentrated, and the interesting material is
cheap.** The 91 species with fewer than 50 clips cost 1.62 GB *between them*.
The disk is not full of interesting things. It is full of robins.

But "keep the rare tail forever" does **not** follow, and an early draft of this
ADR said so wrongly. Everything outside the six commonest species is 15,024
clips and **26.1 GB — and that is a ~30-day steady state, not a lifetime**.
Kept indefinitely it grows about **313 GB/year**, which is not a policy, it is
the same problem later. The mid-range species (Goldfinch 1,447, Dunnock 877,
Blackbird 668) are individually reasonable and collectively ruinous.

So the design problem is not "what do we keep". It is **"what do we cap"**.

### The trap in "just keep the rare ones"

The rarest entries are not the most interesting ones. They are mostly wrong:

```
Chestnut-backed Chickadee 1    California Quail 1    Asian Brown Flycatcher 1
Eastern Screech-Owl 1          Grey-winged Inca-Finch 1
```

None of those can occur in a Surrey garden. A naive rarity bias would
preferentially archive BirdNET's mistakes. Rarity alone is the wrong axis.

### The decision

**Rarity × plausibility, plus a blind sample.** [[ADR-049 - Sound categories are not species|ADR-049]] already computes a
plausibility band per detection from the occurrence prior; cross it with volume:

| | plausible here | implausible here |
|---|---|---|
| **uncommon** | Grey Heron, Kingfisher, Great Spotted Woodpecker → **bank up to 200 clips per species, exempt from age expiry**, then quota | California Quail, Asian Brown Flycatcher → **keep 3 per species, ever**, then stop |
| **common** (operator list) | Robin, Woodpigeon → **no bank; straight to quota** (best, median and worst per species per day) | — |

**The species bank is what makes this bounded.** Each species accumulates up to
**K = 200** clips that never age out; past that it falls back to the daily
quota. The property that matters is that it is **self-limiting**: a genuinely
rare bird keeps everything it will ever produce, while a species that turns out
to be common banks its 200 and then stops costing anything. The heron has 139
today — it banks the lot, and when it eventually passes 200 that is precisely
the point at which further heron clips stop being interesting.

Absolute ceiling: 143 species × 200 × 1.53 MB ≈ **44 GB**, and only if every
species maxes out, which none but the common six will. Measured today, 102
species sit under 200 lifetime detections and hold 3.80 GB between them.

**Everything not banked keeps the existing age tiers** (7-day native, 30-day
audible). Quota clips and sampled clips are a rolling window, not an archive, so
they cannot grow without bound either. Only banked clips are exempt from expiry.

Three examples of a systematic misidentification are enough to judge it; the
3,000th adds nothing. And keeping the *median and worst* alongside the best is
deliberate: an archive selected purely on score is selected on the very variable
anyone would later want to measure.

**Plus a 1% blind sample of everything**, chosen by
`sha256(detection_id)[:8] % 100 == 0` — deterministic, reproducible, auditable,
stateless, and independent of score. Without it the archive can never answer
"what is our actual false-positive rate?", because a best-of collection cannot
estimate a distribution it was selected from. Cost ≈ 4 GB.

### "Common" is an operator list, not a computed threshold

Which birds are boring is a matter of taste and place, not statistics. The
common list is therefore a **UI-editable setting**, `evidence_common_species`,
a `tuple[str, ...]` following the existing `preferred_formats` / `pause_presets`
pattern, persisted to `config/runtime.env` through `site_settings.py`, `tier="live"`.
`_split_sequence` already accepts both JSON and comma-separated spellings.

**Pre-populated with the six species that are 86% of all bird evidence:**

    Common Woodpigeon, European Robin, Eurasian Jackdaw,
    Eurasian Blue Tit, Rook, Collared Dove

Nothing below Rook is added. Every remaining species has under 900 detections
and is individually cheap, so capping it would trade real information for
negligible disk. Note that a purely volumetric rule would have swept up Spotted
Flycatcher (397, a declining species) — which is exactly why this is a list a
person edits rather than a threshold a machine picks.

### Suggesting additions, without nagging

A species is **suggested** for the common list when, over the trailing 30 days,
it has produced **> 500 detections** and **> 2% of evidence bytes**, is **not**
already listed, is **not** dismissed, and its plausibility band is `in_range` —
that last clause matters, because a false-positive burst should be *investigated*,
not silenced by adding it to a list of boring birds.

Surfaced on the settings page as a dismissible row: *"European Greenfinch
produced 1,204 clips (3.1 GB) in the last 30 days. Add to the common list?"*
with **Add** and **Never suggest this species**. The dismissal list is persisted
too; a prompt that returns after being declined is a prompt that gets ignored
along with everything else on the page.

### Bats: the axis is frequency, and the sample is 1%

Bat passes are never given a species, by design, so rarity cannot come from a
name. It comes from **peak frequency band**, and the distribution is as skewed
as the birds':

```
20-25 kHz  36,180  (54%)      50-55 kHz   1,464
30-35 kHz   8,655             25-30 kHz     768
35-40 kHz   7,603             55-60 kHz     107   <-- sparse
15-20 kHz   7,040             60-65 kHz       4   <-- sparse
45-50 kHz   3,328
```

- A band holding **< 1% of the trailing-90-day passes is sparse: keep every
  pass**, banked and exempt from expiry. Today that is 55–60 kHz and 60–65 kHz —
  111 passes, 0.3 GB.
- All other bands: **keep 1%**, by the same deterministic hash, subject to the
  normal age tiers.
- The bank cap applies per band as it does per species, so a band that stops
  being sparse stops accumulating.

The operator's instruction was "agreed on frequency, and also let's keep only
1%". Read literally those pull in different directions, so the resolution is
recorded explicitly: **1% within common bands, 100% within sparse bands.** A
flat 1% everywhere would leave the 60–65 kHz band with an expected 0.04 passes —
it would throw away the rarest signal the station has, which is the opposite of
the intent.

This is the single largest saving: bat evidence is 188 GB and almost all of it
is three common bands.

### Expected effect

| | now | after (steady state) | bounded by |
|---|---|---|---|
| bird species bank | — | 3.8 GB today, **≤44 GB ever** | K=200 × species count |
| bird quota + rolling tiers | 187.5 GB | ~5 GB | 7/30-day expiry |
| bat sparse bands (kept whole) | — | 0.3 GB | 111 passes today |
| bat 1% + rolling tiers | 188.1 GB | ~2 GB | 1% × expiry |
| acoustic_event | 13.2 GB | 13.2 GB (untouched) | existing tiers |
| 1% blind sample | — | ~4 GB | 1% × expiry |
| **total** | **388.8 GB** | **~28 GB now, ≤70 GB at the ceiling** | |

The second figure matters more than the first. The current 388.8 GB is not a
steady state — it is what the disk happened to hold when the watermark last
swept. The point of this policy is a total that is bounded **by construction**
rather than by a high-water mark.

And it keeps *more* of what is wanted: the heron, the kingfisher and the 60 kHz
bat pass survive as a permanent bank, instead of being lost to a 30-day timer
while robins fill the disk.

### Amendment, 2026-08-29: shipped as rarity-only, and why the flag stays off

The implementation landed with **the plausibility half of this decision not
wired**. `retention.py` sets `_ASSUMED_BAND = "in_range"` and calls `classify()`
as though every detection were plausible, because the band lives inside
`native_result` JSON and cannot be read inside the sweep's 1.5 s budget
(ADR-062). **The implausible cap of three never fires.**

So what is running today is *rarity alone* — which this ADR says, in terms, is
the wrong axis: a naive rarity bias preferentially archives BirdNET's mistakes.
A California Quail would bank 200 clips rather than three.

Three things follow, and the third is the one that matters.

1. **The ceiling is unaffected.** `bank_size` bounds every species at 200
   whatever its band, so the ~44 GB worst case in the table above still holds.
   The gap wastes bank slots on misidentifications; it cannot run away with the
   disk.
2. **This ADR is now ahead of its implementation, and says so here** rather than
   continuing to promise a crossing the code does not perform.
3. **`evidence_value_enabled` must not be turned on until the band is
   available.** Enabling it today would fill the bank with exactly the
   misidentifications this ADR was written to keep out — the failure mode, not
   a lesser version of the success. This is a precondition on rollout, not a
   preference.

**The fix, deliberately out of scope here:** persist the plausibility band as an
indexed column on `detection` at write time. The detector already computes it;
nothing needs re-deriving. That is a migration plus a write-path change, and it
wants its own ADR because it changes what a detection row means.

### Rules that must not be broken

1. **Age tiers remain as a backstop.** Value-based selection decides what is
   *worth* keeping; [[ADR-026 - Tiered clip retention|ADR-026]]'s watermark still decides what the disk can *hold*.
   Value never overrides the watermark.
2. **Never delete a human-reviewed or operator-kept detection**, whatever its
   species. The `kept` flag ([[ADR-061 - Operator keep flag|ADR-061]]) already outranks everything and continues to.
3. **Deletion is irreversible and this policy is new.** First rollout runs in
   `--dry-run` and reports what it *would* remove, per category, for a human to
   read before anything is deleted.
4. **The sample must stay blind.** If the hash is ever replaced by anything that
   consults score, band or species, the 1% stops being able to estimate anything
   and becomes another best-of pile.

### Consequences

- Retention gains a value dimension it has never had, and the operator gains a
  control they have never had.
- A species moved onto the common list does **not** retroactively delete its
  history; the change applies to the next sweep forward, matching [[ADR-070 - Threshold retune is not a defect|ADR-070]]'s rule
  that corrections fix the future rather than rewriting the past.
- SLO E in [[ADR-073 - Five capture SLOs|ADR-073]] ("evidence sufficiency") changes meaning under this policy:
  the denominator becomes *detections worth keeping*, not *all detections*. It
  cannot be measured until this lands.

---
Part of the [[ADRS|Architecture Decision Record index]].
