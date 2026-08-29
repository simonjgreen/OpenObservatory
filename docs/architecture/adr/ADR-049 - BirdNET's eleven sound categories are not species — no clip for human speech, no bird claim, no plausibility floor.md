# ADR-049: BirdNET's eleven sound categories are not species — no clip for human speech, no bird claim, no plausibility floor
**Status:** active. Corrects ADR-032's plausibility floor and ADR-044's
withdrawal flag where they meet a non-taxonomic class, and adds the first
implementation of the charter's privacy constraint beyond "we do not record
continuously".

**Context.** The first dry run of `oo detections reconcile-plausibility`
against the live station's real database — 67,679 detections, 2026-08-09,
read-only — proposed flagging 114 rows as implausible. Inspecting them turned
up three problems of increasing seriousness, all measured rather than inferred:

| common | rank | group | n | best score |
|---|---|---|---|---|
| Engine | species | bird | 203 | 0.997 |
| Human vocal | species | bird | 25 | 0.984 |
| Dog | species | bird | 18 | 0.990 |
| Gray Wolf | species | bird | 1 | 0.964 |

1. **91 of the 114 findings were correct detections.** 62 `Engine`, 24 `Human
   vocal`, 5 `Dog`. The range model returns 4e-06 for "Engine" at this station
   — not because engines are absent from the garden but because a car is not a
   taxon with a distribution — and ADR-032's floor reads that as "essentially
   impossible here". A passing car detected at 0.99 is very probably a passing
   car, and withdrawing it also costs the operator the honest "that was
   traffic, not a bird" signal.
2. **The taxonomy was wrong at the source.** Every one of those 247 rows is
   stored `rank='species'`, `taxonomic_group='bird'`, with `scientific_name`
   repeating the common name and a fabricated `canonical_taxon_id` of
   `sci:engine`. The system asserts that a car engine is a bird, at species
   rank. That is an honesty-constraint failure on the *live pipeline*, not
   merely in history — the normaliser accepts these claims from BirdNET today,
   so they keep arriving.

   **Corrected at merge.** An earlier draft of this ADR read the stored
   `plausibility_band: 'out_of_range'` / `threshold_applied: 0.9` on those rows
   as evidence that ADR-032 was never deployed to the station. It was not: those
   are historical rows written before the deploy of 2026-08-09 14:04 UTC.
   Checked afterwards, all 141 banded detections written since carry `in_range`
   at `0.55` and none carry `out_of_range` at `0.9`, and
   `oo_birdnet_suppressed_total{reason="suppressed_implausible_prior"}` — a
   counter that exists only under ADR-032 — was already at 19. ADR-032 is live
   and suppressing. The taxonomy defect described here is real and independent
   of it; the deployment claim was not.
3. **24 `Human vocal` detections held 48 evidence clips and 125 MB** of
   neighbours and passers-by talking in a garden.

**Decision, in charter order.**

### Privacy first: no clip is written for human sound, by default

New setting `clip_human_audio`, default **False**, live-tier, editable in the
browser behind a `danger` acknowledgement (ADR-048). With it off, a detection
of `Human vocal`, `Human non-vocal` or `Human whistle` gets its detection row
and **no audio at all**.

Three options were available: never write the clip; write it with a much
shorter retention; write it and rely on the existing tiers. The charter
settles it rather than taste. The privacy constraint is not a priority that
can be traded — *"No efficiency, accuracy or feature gain justifies relaxing
this"* — and its stated concern is people "who never consented". A shorter
retention still retains, and still requires the operator to have understood
and accepted a window during which a neighbour's conversation is on an SD card
in the garden. Not writing it is the only option that needs no such
explanation. The detection row is kept because it contains no speech: "somebody
was talking at 18:55" is a fact about the soundscape, and deleting it would
trade a privacy gain for an item-3 loss that buys nothing.

The gate is the **first** check in `ClipManager.admits`, ahead of the plugin
filter, the score bar, the rate limit and the disk guard, and
`test_the_privacy_gate_is_checked_before_every_resource_rule` asserts that
ordering. Every other rule there is a resource decision an operator may tune;
a gate placed after one of them stops applying whenever that one
short-circuits first. Refusals are counted (`clips.skipped_human_audio`,
surfaced in the clip snapshot) and logged — a privacy control whose effect
nobody can see is a promise rather than a mechanism.

`oo clips purge-human-audio` deals with what a station already has. It deletes
the files and marks the `media_asset` rows `reclaimed_at` /
`reclaim_reason='privacy_human_audio'`, exactly the shape `retention.py` uses
when a clip ages out (ADR-026), so `/api/v1/media/{id}` keeps answering 410
rather than 500. **Detection rows are never touched.** This is a delete rather
than a withdrawal because the charter's "withdraw, not delete" rule is about
*records*; clip bytes have always been deletable, and this is an existing
operation with a new reason for it, not a new kind of operation.

### Honesty second: a sound category is not a taxon

`detectors/birdnet_classes.py` is the catalogue: eleven labels — `Dog`,
`Engine`, `Environmental`, `Fireworks`, `Gun`, `Human non-vocal`, `Human
vocal`, `Human whistle`, `Noise`, `Power tools`, `Siren` — each with a kind,
of which only `human` changes behaviour.

**How they were identified, and why the list is curated rather than computed.**
Read from the real shipped `birdnet_labels.txt` on the live station
(6,522 lines, en_uk, sha256 `487937b6…` per `models/manifest.tsv`): exactly
thirteen entries have `scientific == common`, and two of those thirteen —
`Gryllus assimilis` and `Miogryllus saussurei` — are genuine binomials for
real crickets with no vernacular name in this list. The remaining eleven are
the sound categories. Seven are single words and so cannot be binomials by
shape; four (`Human non-vocal`, `Human vocal`, `Human whistle`, `Power tools`)
are a capitalised word followed by a lowercase word and are shaped **exactly**
like `Turdus merula`. There is no string rule that keeps the crickets and
rejects "Power tools", which is why this is a list and not a regular
expression. `tests/test_birdnet_classes.py::TestAgainstTheShippedLabels`
re-derives the eleven from the real file and skips when the file is absent
(ADR-006 — the labels are never committed); it was run against the station's
own copy on 2026-08-09 and the derivation matches exactly.

These classes now emit `rank=None`, `scientific_name=None` and
`taxonomic_group='acoustic_event'`, plus `native_result.sound_kind`.
`acoustic_event` is deliberately the **existing** sentinel — it is already in
`normaliser.NON_TAXONOMIC_GROUPS`, which is what stops `_canonical_taxon_id`
minting `sci:engine`, and `detectors/activity.py` has emitted it since
Milestone 1. `common_name` is kept: "Engine" is an honest description of what
was heard, and it is exactly the signal the operator's history view wants.

**Should the normaliser's existing guard have caught this? Partly, and the
gap is instructive.** `_check_claims` asks whether this *plugin* may make
taxonomic claims at all, keyed on `NON_TAXONOMIC_PLUGINS = {activity-v1}`.
BirdNET may, so it was exempt from any scrutiny of whether an individual claim
was well formed — nothing anywhere looked at the claim itself. A second,
per-detection, detector-agnostic check now runs: `rank == "species"` requires a
`scientific_name` shaped like a binomial, and raises `ClaimViolation`
otherwise. It is a backstop and should never fire now that the detector
classifies its own output; it exists so a future adapter with the same bug is
caught without anyone remembering to add it. It catches seven of the eleven
(the single-word ones) and, for the reason above, **cannot** catch the other
four. Stating that plainly is the point: the shape check is not the fix, the
catalogue is.

### The repair third

`band_for` gains `non_taxonomic`, checked before the range model is consulted
at all, returning a new `non_biological` band at the ordinary in-range bar. The
detector and `plausibility_repair` share it, as ADR-032 intended — one
definition of "implausible", not two.

**Measured on the live station's database, read-only, 2026-08-09** (68,023
rows by then; the command's default `--limit 5000` reproduces the operator's
original figure exactly):

| | without the exemption | with it |
|---|---|---|
| `--limit 5000` (the default) | **114** | **23** |
| whole table (`--limit 200000`) | **369** | **123** |

The 246 rows the exemption removes are precisely 203 `Engine`, 25 `Human
vocal` and 18 `Dog` — every one of them a correct detection. What remains at
the default limit is 23 genuinely implausible species: Flammulated Owl,
Grey-winged Inca-Finch, Great Horned Owl, Barred Owl, both Screech-Owls,
Buff-bellied Pipit, Northern Rough-winged Swallow, Gray Wolf and others.
Across the whole table the largest group is 62 `Spotted Crake` at occurrence
4.14e-04 with scores to 0.973 — below the 5e-04 floor, and older than the most
recent 5,000 rows, which is why the operator's first dry run never saw them.

`oo detections reconcile-taxonomy` corrects the historical rows: `rank` to
NULL, `taxonomic_group` to `acoustic_event`, `scientific_name` and
`canonical_taxon_id` cleared, `common_name` and everything else untouched,
**no row deleted**, and the original four values preserved verbatim under
`native_result.taxonomy_review` with a timestamp and a reason. Dry-run by
default, `--json`, confirmation before `--apply`, idempotent.

**Why this one rewrites typed columns when ADR-043 says the original claim is
never edited and ADR-044 marks rather than rewrites.** Both precedents are
about a *claim*: which species this was. Nobody is proposing that "Engine" was
really a wren. `rank` and `taxonomic_group` say what *kind* of statement the
row is; they were set by this pipeline rather than by the detector, and they
are false. A marker alone cannot fix what they do: `/api/v1/history`'s species
list and `/api/v1/taxa/activity` `GROUP BY` those columns and would keep
counting engines among the garden's birds, and `GET /api/v1/taxa/search`
(ADR-043 point 6) offers `sci:engine` as a taxon a reviewer can correct a real
bird *into*. So ADR-044's binding rules are kept — nothing deleted, the
original preserved and attributable — while declining to leave a knowingly
false category assertion in a column four consumers aggregate over. That is
also why this command does *not* skip human-reviewed rows the way
`find_implausible_detections` does: the review workflow has no field in which
a human could have endorsed a rank, so skipping would leave the false claim
standing on precisely the rows somebody cared enough to look at. No `Review`
row is read or written.

**Metrics.** `oo_birdnet_non_biological_total{plugin_id}` is a **separate**
series, not another `reason` label on `oo_birdnet_suppressed_total`: these
detections were admitted, not suppressed, and a number shown to a human must
mean what its label says.

**Known limitations, stated rather than discovered later.**

* **`Gray Wolf` is still filed under `taxonomic_group='bird'`.** `Canis lupus`
  is a real binomial and a real species, so it is correctly outside this
  catalogue and correctly flagged as implausible in a UK garden — but BirdNET
  GLOBAL 6K contains mammals, amphibians and insects as well as birds, and
  this adapter has no way to tell which is which. Fixing that needs a real
  taxonomy source, which ADR-043 argued at length against introducing. One row
  on the live station today.
* **Nothing reads `sound_kind` on a presentation surface.** The web UI, MQTT
  and the counter-top display treat a corrected row as any other
  `acoustic_event`, which is honest but plain. Rendering an engine differently
  from an unidentified acoustic event would be a genuine improvement and is
  not done here.
* **The purge is by label, so it cannot find human speech BirdNET did not
  label as human.** A conversation recorded incidentally inside a clip of a
  blackbird is still on the disk. Bounding that is what the retention ladder
  is for, not this command.

### Migration

**None.** No schema change: `sound_kind` and both audit blocks live inside the
existing `native_result` JSON column, and the purge uses `media_asset`'s
existing `reclaimed_at`/`reclaim_reason`. Alembic head stays `0006_refinement`.

### Rollback and smoke test (ADR-049)

`git revert` restores the previous behaviour. Values already written by the
two repair commands are unaffected by the revert: a `taxonomy_review` block
would simply go unread, and the corrected columns would stay corrected (which
is the safe direction — they would merely be repopulated wrongly for *new*
detections). `clip_human_audio` reverts to not existing, which means clips of
human speech resume; that is the one thing a reverter must know.

```bash
# 1. What the plausibility repair proposes now. Compare with the pre-change
#    figure: 114 -> 23 at the default limit on this station.
oo detections reconcile-plausibility --json > /tmp/plausibility.json

# 2. What the taxonomy repair proposes. Expect Engine / Human vocal / Dog only.
oo detections reconcile-taxonomy --json > /tmp/taxonomy.json
python3 -c 'import json,collections;print(collections.Counter(r["common_name"] for r in json.load(open("/tmp/taxonomy.json"))))'

# 3. What human audio is stored. Expect 48 assets / 24 detections / ~125 MB
#    on the development station.
oo clips purge-human-audio --json > /tmp/human-audio.json

# 4. After applying: the engine is still in the record and is no longer a bird.
curl -s 'http://<station-host>:8080/api/v1/detections?limit=200' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["detections"]; print([(r["common_name"], r["rank"], r["taxonomic_group"]) for r in d if r["common_name"]=="Engine"][:3])'

# 5. And it is no longer in the species tally or the taxon search.
curl -s 'http://<station-host>:8080/api/v1/taxa/search?q=Engine'

# 6. No new clip of human speech is written.
curl -s http://<station-host>:8080/api/v1/status \
  | python3 -c 'import json,sys; c=json.load(sys.stdin)["clips"]; print(c["skipped_human_audio"], c["policy"]["clip_human_audio"])'
```
