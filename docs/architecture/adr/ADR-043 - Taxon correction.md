---
aliases:
  - ADR-043
tags:
  - adr
---
# ADR-043: Taxon correction closes the review workflow; a human's ear outranks a machine's
**Status:** active. Closes the item [[ADR-029 - Retention UI, assumed API|ADR-029]] deliberately left open ("correcting a
misidentified taxon... is left for a future ADR") and [[HANDOVER]] §6.4 item 11
("`corrected_taxon_id` is always written `None`").

**Decision.** A reviewer can now replace a wrong identification with the correct
one, and the system treats that correction as the highest-quality information it
will ever hold about the event — visible everywhere, never overwritten by a later
machine refinement, and (for evidence specifically) exempt from the retention
sweeper on request.

1. **`Review.status` gains two values.** Was `{confirmed, rejected}`; is now
   `{confirmed, rejected, corrected, held}` (`db/models.py`, `review.STATUSES`).
   * `corrected` — the original identification was wrong; `corrected_taxon_id`
     says what it actually was. `ReviewIn` (`api/app.py`) requires it exactly
     when `status == "corrected"` and rejects it otherwise (a Pydantic
     `model_validator`, so a malformed request 422s before touching the
     database).
   * `held` — no verdict yet, but keep the evidence. See point 4.
2. **The original claim is never edited.** `Detection.common_name` /
   `scientific_name` / `canonical_taxon_id` are written once, by the detector
   pipeline, and nothing added by this ADR ever updates them. A correction is a
   new `Review` row — same append-only shape the table already had
   ("current status is derived from the latest valid review") — carrying
   `corrected_taxon_id` plus two new denormalised columns,
   `corrected_common_name` and `corrected_scientific_name`, captured once at
   write time from whichever of the station's own past detections the taxon id
   matched (`review.resolve_taxon`). `actor` records who: the logged-in
   operator's username when auth is enabled and a session/token is presented,
   else the existing anonymous default `"local"` ([[ADR-034 - Authentication foundation|ADR-034]]).
3. **Every consumer that shows an identification shows the correction
   alongside it, not instead of it.** `_detection_payload` (`api/app.py`) adds,
   next to the untouched `common_name`/`scientific_name`:
   `review` (the full annotation — status, actor, note, timestamp, and the
   corrected names/id when present), `identification_source`
   (`"human"` only when the latest review is a correction, else `"model"`),
   and `effective_common_name`/`effective_scientific_name` (the corrected name
   when corrected, else the original). This lands in `GET /detections/{id}`,
   `GET /detections` (list), and `GET /detections/export` (JSON and CSV — the
   CSV gains `identification_source`, `effective_common_name`,
   `effective_scientific_name`, `review_status`, `reviewed_by`, `reviewed_at`
   columns, alongside the untouched `common_name`/`scientific_name`). List and
   export batch-fetch the latest review per detection in one query
   (`review.latest_reviews_by_detection`, a `GROUP BY MAX(created_at)` join)
   rather than one query per row.
4. **An explicit hold exempts a detection's evidence from the retention
   sweeper's age-based tiers.** `retention.py`'s `_strip_native`,
   `_strip_non_exemplar` and `_strip_expired` all now skip any detection whose
   *current* review status is `held` (`review.held_detection_ids`, computed
   once per sweep, same shape as the existing exemplar-id set). Deliberately
   narrower than an unconditional hold: the watermark reclaim tier does
   **not** check it — see "Known limitations" below.
5. **A human's ear outranks a later machine refinement, enforced in code, not
   by convention.** `plausibility_repair.find_implausible_detections` now
   skips any detection with *any* human review at all — confirmed, rejected,
   corrected or held — via `review.reviewed_detection_ids`, and
   `apply_plausibility_flag` re-checks the same thing defensively (real time
   passes between an interactive CLI's find and confirm steps). Before this
   ADR the precedence was implicit: nothing stopped a repair pass from
   re-flagging a detection a human had already looked at, because the repair
   pass had no way to know a human had looked at it at all.
6. **Taxon lookup is built from the station's own detection history, not a
   new dependency.** `GET /api/v1/taxa/search?q=` (`review.search_taxa`)
   matches `q` against `common_name`/`scientific_name` (case-insensitive
   substring) among detections with `rank == "species"` and a
   `canonical_taxon_id` — i.e. every species any detector has ever actually
   identified here, keyed the same way `normaliser._canonical_taxon_id`
   already keys them (`sci:<scientific_name>`). `POST .../review` resolves
   `corrected_taxon_id` against the same table and 400s with a pointer to the
   search endpoint if it does not match anything the station has produced.
7. **MQTT does not republish a correction.** `mqtt/publisher.py`'s
   `_SUBSCRIBED_TYPES` is unchanged; there is no bus event for a review at
   all, because `post_detection_review` writes straight to the database. This
   is a decision, not an oversight (documented at length in that module): a
   correction typically lands well after the originating detection, about a
   clip a listener may already be finished with, and every entity this
   publisher creates models "what the station is hearing right now." Fabricating
   a fresh MQTT/HA event for a retrospective annotation would misrepresent it
   as a new acoustic occurrence. The correction is still fully visible through
   the API, the review drawer and the export — just not on the live bus.

**Why species come from the station's own history rather than BirdNET's label
list or a bundled/fetched taxonomy database.** Three options existed:
`birdnet_labels.txt` (6,522 entries, exact matches for anything BirdNET could
in principle say); a bundled or network-fetched taxonomy database (GBIF,
eBird's taxonomy, etc.); or the station's own `detection` table. The first two
were rejected. `birdnet_labels.txt` is model data under a separate
non-commercial/share-alike licence ([[ADR-006 - Model install and licensing|ADR-006]]) that this repository never
bundles and that is only present on disk after an operator has run
`oo models fetch` — a lookup source that might silently not exist is not a
foundation for a core review action. A bundled or fetched taxonomy database is
a new dependency this brief explicitly asked to avoid reaching for without
checking what the station already holds, would need its own licensing story,
and (fetched) would violate "never require cloud connectivity for core
capture, detection, review or query" (`CLAUDE.md`). The detection table is
data the station unconditionally already has, requires nothing new, is never
itself a fabricated claim (`canonical_taxon_id` is only ever set for a
species-rank identification a real detector actually made — see
`normaliser._canonical_taxon_id` and the "do not fabricate classifier
support" rule this extends naturally to "do not fabricate a taxonomy"), and
needs no online access.

**Migration.** `alembic/versions/20260809_0004_000000000005_add_review_correction_names.py`
(`0005_review_correction_names`, down-revision `0004_drop_dead_detection_indexes`)
adds `review.corrected_common_name` and `review.corrected_scientific_name` as
nullable `VARCHAR(240)` columns, following revision 0003's idempotent-skip
precedent so an operator who restarts before migrating (and so already has the
columns via `db/session.py`'s SQLite patcher) does not hit "duplicate column
name." Verified with a from-scratch `alembic upgrade head` (all five
revisions apply cleanly), `alembic check` (no drift against `models.py`), and a
downgrade/upgrade roundtrip. `corrected_taxon_id` itself needed no migration —
it shipped with the initial schema (revision `0001_initial`) and was simply
unused until now.

**Known limitations, stated rather than discovered later:**

* **A correction can only name a taxon this station has already identified at
  least once, by any detector.** A first-ever, genuinely-correct-by-ear
  identification of a species the station has never itself detected has no
  match in `GET /api/v1/taxa/search` and cannot currently be entered as a
  structured correction. Fixing this needs a real taxonomy source (option two
  above), deliberately not taken here.
* **The retention hold does not survive a full disk.** `_watermark_reclaim`
  (the one hard safety valve in `retention.py`: "disk space always wins over
  any retention preference") is unchanged and does not check `held_ids`. An
  operator who needs a hold that survives disk pressure should export the
  clip rather than rely on the hold alone.
* **The aggregate `GET /api/v1/history` endpoint (`history.timeline`,
  `history.species_summary`) does not fold corrections in.** Its SQL groups
  and counts by the *original* stored `taxonomic_group`/`common_name`/
  `scientific_name` columns; a corrected detection still counts toward its
  original (wrong) species' tally in the night's species list and timeline.
  Every place a single detection is displayed — detail, list, CSV/JSON export,
  the review drawer — is correct per point 3 above; the aggregate view is not,
  and folding a per-detection correction into a `GROUP BY` over tens of
  thousands of rows per night is real work, not a "half-do the sweep logic"
  shortcut this pass could responsibly take. Flagged here rather than left
  silently wrong.
* **No confirmation UI for "are you sure"** on a correction: picking a search
  result submits immediately. Consistent with the existing confirm/reject
  buttons (also immediate), but worth naming since a correction is a stronger
  claim than either.

**Tests.** `tests/test_review.py` (unit, `review.py`'s query functions against a
hand-seeded database); `tests/test_api.py::TestReviewWorkflow` (HTTP-level,
through the real FastAPI app — confirm/reject supersession, taxon search,
correcting a detection and verifying the original is untouched everywhere the
correction must show, validation 422s, unknown-taxon 400, hold); new cases in
`tests/test_retention.py::TestHumanHold` (hold survives the native/expired
tiers, a later review releases a hold, the watermark reclaim ignores a hold);
new cases in `tests/test_plausibility_repair.py` (a human-reviewed detection is
never flagged; `apply_plausibility_flag` is a no-op if reviewed since the
finding was computed). `web/src/components/DetectionDrawer.test.tsx` covers
the hold button, taxon search-then-correct, and rendering an existing
correction.

**Reviewed 2026-08-29:** the decision holds; `held` was deliberately left
untouched by [[ADR-061 - Operator keep flag|ADR-061]]. Two names in point 4 have since moved:
`_strip_non_exemplar` was renamed `_strip_unkept` and `_strip_expired` was
removed as dead code, both by [[ADR-061 - Operator keep flag|ADR-061]], which also deleted the
`_exemplar_detection_ids` set the held-id set is compared to above
(`_TIER_ORDER` is now `("native", "unkept", "watermark")`). The hold is still
honoured by both surviving age tiers (`src/open_observatory/retention.py:711`
and `:751`) and `_watermark_reclaim` still does not check it, so the second
known limitation above stands — but there is now a better answer than
exporting the clip: [[ADR-061 - Operator keep flag|ADR-061]]'s operator-set `kept` flag *is* exempt at the
watermark, and an operator who needs a hold to survive disk pressure should
mark the detection `kept` as well.

**Reviewed 2026-08-30.** The decision holds. Three corrections, four findings and
one thing this ADR turns out to have already done on the station; none of them
changes what was decided.

*Corrections to the note above.* The two line numbers are stale:
`held_ids` is applied at `src/open_observatory/retention.py:1367`
(`_strip_native`) and `:1414` (`_strip_unkept`). There is now a **third** tier
that honours the hold, added after that note was written — `_strip_acoustic_events`
(`:1491`, [[ADR-077 - Acoustic events keep no recordings|ADR-077]]), so `_TIER_ORDER` is `("native", "unkept",
"acoustic_event", "watermark")`. `_watermark_reclaim` still does not check
`held_ids` (`:2025`), so the second known limitation above is unchanged. The
same "two age tiers" count is carried by [[ADR-061 - Operator keep flag|ADR-061]]'s own correction, by
`retention.py`'s module docstring and by [[CHARTER]]; all three are one tier
behind, in the safe direction.

*Exercised on the station, and this is the strongest evidence this ADR has.*
Read-only over the API on 2026-08-30, across 17,694 detections of 2026-08-04 to
2026-08-10 scoring 0.70 or better: **31 `held` and 3 `confirmed` reviews**, every
one written by actor `local` (auth is off). All 31 holds are *Spotted Crake*, and
they were written at 2026-08-09T15:31:44Z — **19 seconds before** the
`oo detections reconcile-plausibility --apply` run of 15:32:03Z recorded in
[[ADR-044 - Withdrawn detections|ADR-044]]. One of them, `c2d60a09`, carries
`native_result.occurrence_probability` 0.000414, below the 0.0005 plausibility
floor, so today's `band_for` puts it in `implausible` at an unreachable bar: it
would have been a finding of that pass, and it comes back `withdrawn: false`
today. Point 5 — a human's ear outranks a machine's — is not merely unit-tested;
it stopped 31 withdrawals on the live station. Whether those 31 identifications
were right is not settled by any of this, and the hold does not claim it is: what
the hold did was stop an automated pass deciding the question. Spotted Crake is a
scarce but genuine British species, so a near-zero occurrence prior is exactly
what it should have, which is the case the floor cannot tell apart from an
American owl.

*Finding: `corrected` appears never to have been used.* Across the same 17,694
rows and a second read of 20,000 rows scoring 0.95 or better spanning
2026-08-11 to 2026-08-30, every review is `held` or `confirmed`, every row reports
`identification_source: "model"`, and the most recent review of any kind is
2026-08-10T09:02:44Z. Both reads are samples, not a census — there is no endpoint
or CLI command that lists reviews, so a census is not available read-only — but
the headline action of this ADR has no observed use on the station in three weeks,
and the confirm/reject/hold half predates it.

*Finding: the web UI never reads the correction.* `effective_common_name`,
`effective_scientific_name` and `identification_source` are declared in
`web/src/types.ts:347-351` and referenced nowhere in `web/src` except a test
fixture. `formatDetectionTitle` (`web/src/components/detectionTitle.ts:45`)
composes every title from `display_name`, which the API derives from the
detector's untouched `common_name`, so the list, the history view, the
spectrogram labels and the drawer's own `<h2>` all keep showing the original name
after a correction. The only rendering of a correction anywhere in the UI is the
"Corrected to …" line inside the open drawer's review controls
(`DetectionDrawer.tsx:428-434`). Point 3 above is true of the API and of the
export; it is not true of the dashboard. A *withdrawal* gets a title marker and a
full explanatory block in the same drawer, so the machine's retraction is louder
on screen than the human's correction.

*Finding: two of the four statuses do nothing at all.* `CORRECTED_STATUS` is read
by `_detection_payload` (`api/app.py:2927`) and `HELD_STATUS` by `retention.py`
and `media_repair.py`; `CONFIRMED_STATUS` and `REJECTED_STATUS` are read by no
consumer anywhere in `src/`. A reviewer who rejects a detection changes nothing
on any surface: it stays in `history.species_summary`, in
`GET /api/v1/taxa/activity`, on MQTT and in the counter-top display's connect
snapshot, unmarked. The only effect of any review is the one point 5 describes —
`plausibility_repair` skips the row.

*Finding: the append-only chain has no reader.* `supersedes_review_id` is written
at `api/app.py:1876` and read by nothing — not `_review_payload`, not the CLI, not
`web/`, not a test. `tests/test_api.py::TestReviewWorkflow::test_confirm_then_reject_supersedes`
asserts only that the *latest* review is the second one; it never asserts that the
first row survived or that the second points at it. The chain is a claim the tests
do not check.

The third known limitation above is also narrower than the endpoint list warrants:
`GET /api/v1/taxa/activity` (`api/app.py:1955`) groups by the detection's own
`common_name`/`scientific_name` with no `Review` join, exactly as
`history.species_summary` does, and is subject to the same gap.

*Correction to point 1.* `review.STATUSES` (`src/open_observatory/review.py:50`)
documents itself as "the single source of truth for the API's Pydantic pattern
(`api/app.py: ReviewIn`) so the two never drift". It is not: `ReviewIn.status`
carries the literal `Field(pattern="^(confirmed|rejected|corrected|held)$")`
(`api/app.py:165`), `Review.status` is a plain `String(24)` with no check
constraint, and `STATUSES` is referenced by nothing in `src/`, `tests/` or
`web/`. The two agree today by hand, not by construction.

---
Part of the [[ADRS|Architecture Decision Record index]].
