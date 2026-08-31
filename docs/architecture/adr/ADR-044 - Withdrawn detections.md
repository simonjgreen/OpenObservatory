---
aliases:
  - ADR-044
tags:
  - adr
---
# ADR-044: A withdrawn detection is marked in the record and suppressed on the claim surfaces; and the BirdNET week index is correct
**Status:** active; completes [[ADR-032 - Plausibility bands|ADR-032]]. [[ADR-049 - Sound categories are not species|ADR-049]] later narrowed which rows the
repair command may flag — BirdNET's eleven non-taxonomic classes are exempt from
the occurrence prior — without changing what any surface here does with a flag
that is set.

**Decision.** [[ADR-032 - Plausibility bands|ADR-032]] stopped the detector from ever writing another implausible
identification, and shipped `oo detections reconcile-plausibility` to flag the
ones already stored under `native_result.plausibility_review`. Nothing read that
flag. This ADR makes the consumers read it, through one shared definition
(`plausibility.py`: `REVIEW_KEY`, `is_withdrawn`, `withdrawal`), and splits
their treatment along one line:

| Surface | Treatment | Why |
|---|---|---|
| `GET /api/v1/detections`, `/detections/{id}` | **Kept, marked** `withdrawn: true` plus a `withdrawal` block | A record, with room for nuance |
| `GET /api/v1/detections/export` (CSV/JSON) | **Kept, marked** — new `withdrawn` column | Same, and a spreadsheet gets cited |
| `GET /api/v1/history` → `species` | **Excluded**, `excluded_withdrawn_count` reported | Names a species; an aggregate has no row to mark |
| `GET /api/v1/taxa/activity` | **Excluded**, `excluded_withdrawn_count` reported | Same |
| `GET /api/v1/history` → `timeline` | **Unchanged** | Counts detections; names nothing |
| MQTT publisher | **Suppressed**, counted | A Home Assistant state is a bare claim |
| `/api/v1/display` (ESP32 push) | **Suppressed** in SQL *and* on the wire | No score, no marker, no room in an MTU |
| ESP32 HTTP fallback (`detection_feed.cpp`) | **Refused** | It reads `/api/v1/detections`, which still returns the row |
| Web UI | **Marked** everywhere, explained in the drawer | `formatDetectionTitle` is the one composition point |

**Reason: the charter draws this line, not taste.** Item 5 is explicit —
*"Withdraw", not "delete". Preserve the original claim. The prior verdict stays
visible and attributable* — and a record the system got wrong is evidence about
the system. Deleting a row, or hiding it from the API, was never available. But
item 6 is equally explicit that *an answer that is wrong is worse than no
answer, because it will be believed*, and the honesty constraint requires that
"unverified" stay available **all the way to the surface**. On the two surfaces
where it cannot — a Home Assistant entity state, and a counter-top display that shows a
name and an elapsed time with no score at all by [[ADR-023 - The ESP32 inside observer|ADR-023]]'s rule — carrying the
row with an unrenderable caveat *is* presenting it as fact. Suppression there is
the honest reading of the same constraint, not an exception to it.

The species-tally endpoints fall on the suppression side for a mechanical reason
rather than a philosophical one: they `GROUP BY` species, so there is no row left
to attach a marker to. "Western Screech-Owl, 4 detections, best score 0.96" is a
claim with nowhere to put a retraction. They therefore follow [[ADR-020 - Non-live sources excluded|ADR-020]]'s existing
precedent exactly — exclude by default, expose an `include_*` escape hatch, and
**report the count of what was excluded** (`excluded_withdrawn_count`), because a
filter on a wildlife-facing view is only honest if the exclusion is
discoverable. `timeline` is deliberately left alone: it counts detections per
bucket per group and names nothing, and the withdrawn detection genuinely did
occur.

**The operator's instinct, and where the code corrected it.** The brief proposed
"visible in the API/history with a withdrawn marker, suppressed on the counter-top
display and MQTT". That is what shipped, with one correction: *history* turned
out to be two different things. `/api/v1/history` returns a `timeline` (counts —
nothing to mark, and nothing needing marking) and a `species` list (an aggregate
that names species, with nothing to mark it *with*). Marking was not available
for the second, so it is excluded and counted instead.

**Implementation notes a successor will need.**

* `plausibility.py` is deliberately dependency-free — no SQLAlchemy, no numpy,
  no FastAPI — because `display_channel.py` is documented as free of the
  database and of FastAPI and has to import it. The one thing that genuinely
  needs SQLAlchemy, the predicate, lives in `history.py` next to
  `is_live`/`is_not_live` instead.
* `history.is_not_withdrawn()` is NULL-safe by construction and this is not
  optional. `native_result` is a `JSON` column, almost no row has a review
  block, and the extracted value is SQL `NULL` for all of them; a plain
  `= false` would have hidden the entire database rather than one owl. Same
  three-valued-logic trap `is_not_live` documents, with a far worse failure
  mode. It compiles to `json_extract` on SQLite and `->>`/`CAST` on PostgreSQL
  with no dialect branch ([[ADR-007 - SQLite in developer mode|ADR-007]]).
* The display's connect snapshot filters in **SQL**, not in Python. [[ADR-038 - Display push channel|ADR-038]]'s
  whole point is that this query reads six narrow columns and never touches the
  ~1.8 kB `native_result` blob; reading the blob back just to test a flag would
  have undone that. `display_channel.wire_item` then checks the flag again for
  the live delta path, which does carry `native_result` on the bus. Two
  independent barriers, deliberately.
* The MQTT check is expected to be dead code on a healthy station: a withdrawal
  is written by a repair CLI long after capture, so no live bus event should
  ever carry one. It exists, and is counted as
  `oo_mqtt_suppressed_withdrawn_total`, because if that counter ever moves,
  something is republishing historical rows onto the bus and that is worth
  knowing rather than quietly forwarding.
* The ESP32's *streaming JSON filter* had to learn the field too
  (`buildDetectionsFilter`). Without that, `withdrawn` is discarded during the
  parse and every row reads as standing — a silent failure that a test of the
  parsed model alone would sail straight through, which is why
  `test_the_streaming_filter_keeps_the_withdrawn_flag` asserts the filter
  itself.

**Consequence: `--apply` now has teeth.** Before this change, running
`oo detections reconcile-plausibility --apply` changed nothing anybody could
see. It now takes effect immediately, with no restart, on every surface. The
command's help and its post-apply message were updated to say so. ~~It has still
**never been run against the live station**~~ — **it was, on
2026-08-09T15:32:03Z, flagging 61 rows; see [[ADR-070 - Threshold retune is not a defect|ADR-070]], which also records why it
must not be run again until that fix is deployed.**

---

### The second half: the week index passed to the range model is **correct**

[[ADR-032 - Plausibility bands|ADR-032]] left this explicitly unverified, and it was the higher-stakes of its two
open items: a wrong week makes every occurrence prior wrong *globally*, which
would silently invalidate the plausibility floor [[ADR-032 - Plausibility bands|ADR-032]] built on top of it.

**Derived independently from the code.** `birdnet_week` computes
`(month - 1) * 4 + min(4, int(day / 7.25) + 1)`. That form obscures what it
does, so it was checked against the convention as normally stated — four weeks
per calendar month, week 1 being days 1-7, the fourth absorbing days 22-31,
i.e. `(month - 1) * 4 + min(4, (day - 1) // 7 + 1)` — for **every day of a
common year and a leap year**. Zero mismatches, 48 distinct values, range
exactly [1, 48], 29 February included (week 8). Locked in
`tests/test_detectors.py::TestBirdNetAdapter::test_every_day_of_a_leap_and_a_common_year_lands_in_1_to_48`.

This is **not** an ISO week and must never become one. For 2026-08-08 the
BirdNET week is 30 and the ISO week is 32; on 2026-12-31 they are 48 and 53.

**Verified empirically against the real model, not only against arithmetic.**
Self-consistent arithmetic would not catch a formula that is coherent but off by
a fortnight, so the real V2.4 MData model was run at the station's configured coordinates
for all 48 weeks and checked against known UK phenology
(`scripts/birdnet_week_audit.py`, 2026-08-09):

| Species | Prior by week | Reality |
|---|---|---|
| Common Swift | ~0 to w11, rises w15-17, peaks **w22** (0.863), gone by w37 | Arrives late April/early May, leaves early August ✔ |
| Barn Swallow | rises w13, 0.98+ through w33, falls away w37-39 | April to September ✔ |
| Common Cuckoo | peaks **w17** (0.448), ~0 from w29 | Late April to July ✔ |
| Fieldfare | 0.28 in w1-5 and w45-48, 0.02 mid-summer | Winter visitor ✔ |
| Common Woodpigeon | 0.96-1.00 all year | Resident ✔ |
| Tawny Owl | 0.01-0.036 all year | Resident, and consistent with [[ADR-032 - Plausibility bands\|ADR-032]]'s measured 0.019253 ✔ |
| Western Screech-Owl, Flammulated Owl | **0.000 in every one of the 48 weeks** | North America ✔ |

Week 30 — the week the owls were measured in — returns Common Woodpigeon 1.00,
Barn Swallow 0.98, European Robin 0.83, matching the sane priors [[ADR-032 - Plausibility bands|ADR-032]]
reported from the live database, from an independent run here.

**Verdict: the week index is right.** The seasons land on the right calendar
dates to within a quarter-month, which is this convention's entire resolution.
The North American owls were never a week problem: their prior is zero in every
week of the year.

**One thing found along the way, worth writing down.** A week outside [1, 48] is
not rejected by the MData model — it returns the *year-round* prior. Measured:
Common Swift 0.913 at weeks 52, 53, 0 and −1, against 0.000 in January and 0.863
at its June peak. So an ISO week reaching this model would not error; it would
quietly disable seasonality and inflate the prior for every migrant, weakening
the filter rather than breaking it visibly. `birdnet_week` cannot produce such a
value by construction and the new test asserts that for every date, which is why
no runtime guard was added.

The station's local timezone is used for the conversion
(`datetime.fromtimestamp(window.utc_start_ns / 1e9, self._timezone)`) rather
than UTC. That is correct and immaterial: at quarter-month resolution it can
only matter for a detection within an hour of local midnight on the 7th, 14th or
21st of a month, and local time is the right choice for a seasonal index.

### Rollback and smoke test (ADR-044)

Nothing here has a runtime setting to turn off, because a flag consumers can be
configured to ignore is the bug this fixes. `git revert` the commit to restore
the previous behaviour; the stored `plausibility_review` blocks are untouched by
that and would simply go unread again, exactly as before.

No station currently has a single flagged row, so this change is a no-op on the
live station until an operator runs the repair command with `--apply`.

**Reviewed 2026-08-29:** that last sentence has been overtaken by the `--apply`
run recorded above. The live station now carries flagged rows: they come back
from `/api/v1/detections` and from the CSV export marked `withdrawn`, and
`/api/v1/history` reports a non-zero `excluded_withdrawn_count` for the days
they fall in. A `git revert` here would now change what those surfaces return
rather than nothing.

```bash
# 1. Dry run first, on the station, and read it. Never --apply blind.
oo detections reconcile-plausibility --json > /tmp/plausibility.json

# 2. After --apply: the marker must be present, the row must still be there,
#    and the species tally must have dropped it and said how many.
curl -s 'http://<station-host>:8080/api/v1/detections?limit=200' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["detections"]; print([(r["common_name"], r["withdrawn"]) for r in d if r["withdrawn"]])'
curl -s 'http://<station-host>:8080/api/v1/history?window=last-24h' \
  | python3 -c 'import json,sys; h=json.load(sys.stdin); print(h["excluded_withdrawn_count"], [s["common_name"] for s in h["species"]])'

# 3. The counter-top display's own feed, which is the point of the exercise.
python scripts/watch_display_channel.py --seconds 60 --label "post-withdrawal"

# 4. The week audit, re-runnable whenever the model assets change.
python scripts/birdnet_week_audit.py
```

**Reviewed 2026-08-30 — the two suppression surfaces this ADR added are correct in
code and unexercised on the station.** Both barriers on the display channel are
present as described: the connect snapshot filters in SQL
(`src/open_observatory/api/app.py:2580`) and `wire_item` checks the flag again for
the live delta path (`src/open_observatory/display_channel.py:179`). The ESP32
fallback still refuses a withdrawn row
(`firmware/inside-observer/src/model/detection_feed.cpp:59`) and the streaming
filter still keeps the field (`:176`), asserted by
`test_the_streaming_filter_keeps_the_withdrawn_flag`. The MQTT check is where this
ADR predicted it would be, and its counter has not moved.

What none of that shows is any of it running. On the live station,
`oo_mqtt_suppressed_withdrawn_total` is `0`, the display channel has dropped no
frames, and `/api/v1/history` reported `excluded_withdrawn_count: 0` for
`last-hour`, `last-24h` and `last-7d`, with no withdrawn row in the most recent 200
detections. **This does not contradict the note above**, and should not be read as
doing so: the 61 rows flagged on 2026-08-09 are three weeks behind the longest
window `/api/v1/history/windows` offers (`last-7d`), and `GET /api/v1/detections`
caps at 500 rows, so six four-hour slices of 2026-08-09 each hit the cap without
reaching them. A CSV export over 2026-08-01 to 2026-08-15 would settle it; it was
not run, because that query timed out at 8 s and this review was read-only against a
station that was capturing. **Someone should confirm the flagged rows are still
marked before treating the 2026-08-29 note as current** — the claim is unverified
today rather than shown to be wrong.

**Reviewed 2026-08-30: one of the eight surfaces above has had no running test
for nineteen days.** `tests/test_plausibility_consumers.py::TestApiSurfaces::test_taxa_activity_excludes_it_and_says_how_many`
is the only automated check that `GET /api/v1/taxa/activity` excludes a withdrawn
detection and reports `excluded_withdrawn_count`. It computes its look-back from
`NIGHT = datetime(2026, 8, 4, ...)` to now and calls `pytest.skip` when that
exceeds the endpoint's 168-hour cap, which it has done since 2026-08-11. The suite
reports it as one skip among 61 passes, so the row in the table above that reads
"`GET /api/v1/taxa/activity` — Excluded, `excluded_withdrawn_count` reported" is
presently asserted by nothing that runs. The same 168-hour cap is why [[ADR-070 - Threshold retune is not a defect|ADR-070]]
recorded the live cross-check as unfulfillable, and the two combine badly: this
surface has neither a passing test nor a station observation. The station's only
withdrawn rows are the 61 of 2026-08-09, all older than seven days, so the check
cannot be made there either until a new withdrawal lands. Proposed fix: seed the
fixture relative to `datetime.now(UTC)` rather than at a fixed date, so the test
runs instead of aging out.

The rest of the chain was re-checked read-only on 2026-08-30 and is intact.
Detection `a233415f3f72406f9e67769e972c5e62` (*Flammulated Owl*, 0.875556) comes
back from the station with `withdrawn: true` and a populated `withdrawal` block
naming occurrence 9.806e-06 against the 0.0005 floor, reviewed
2026-08-09T15:32:03Z. A CSV export of 17,694 rows over 2026-08-04 to 2026-08-10
carries the `withdrawn` column with 42 rows set. `oo_mqtt_suppressed_withdrawn_total`
reads 0, which is what a healthy station should show — the counter exists to move
only if something starts republishing history onto the bus.

---
Part of the [[ADRS|Architecture Decision Record index]].
