---
aliases:
  - ADR-061
tags:
  - adr
---
# ADR-061: An operator-set keep flag replaces the computed exemplar rule
**Status:** active. Removes `RetentionSweeper._exemplar_detection_ids()`;
supersedes [[ADR-026]]'s first/best-of-species exemption; deployment and
on-station verification pending (Task 6 of the implementing plan is
documentation only, by the controller's instruction).

**The measurement.** `_exemplar_detection_ids()` ran first inside `sweep()`,
before any deadline check: an unbounded `DISTINCT` join across every live
`media_asset` — ~46,000 rows, seven columns including the `native_result` JSON
blob — materialised into Python so first-of-species/best-of-species ranking
could be done in a loop. Measured on the station: **2.978 s**, against a
`retention_batch_budget_s` of **1.5 s**.

**Three failures from one query.**

1. **Retention never deleted a single file, nine days past its threshold.**
   The budget was spent in the preamble, before the first tier's guard ran, so
   `_strip_native` was never entered. `complete=False` and a flat zero
   deletion count read identically whether a sweep merely ran out of time with
   real work left, or never started at all — which is why nine days of this
   produced no symptom worth noticing.
2. **~7 s of audio lost per hour**, which cost the 72-hour soak (2026-08-10 to
   2026-08-13) its continuity gate: **99.865%** against a **≥99.9%**
   criterion ([[MILESTONE_STATUS]], Milestone 4.5). ~3 s of GIL-holding ORM
   work every 300 s starved the capture event loop long enough to overrun the
   0.5 s ALSA ring. `capture_gap` rows arrive in pairs ~300 s apart, matching
   `retention_interval_s` to the second — the same beat [[HANDOVER]] §1e
   names.
3. **The 2026-08-14 wedge.** Each overrun forces an `snd_pcm_prepare()`
   device restart — roughly 12 an hour — and one of those did not come back,
   leaving the microphone deaf for **3 h 35 min** ([[ADR-060]], [[HANDOVER]]
   §1e). The coupling to this query is proven to the millisecond, not merely
   correlated: the sweep began at **02:18:19.893** with **`duration_s`
   `2.6608`**, and the first `-EIO` landed at **02:18:22.552** — 2.66 s later,
   the length of the sweep itself. [[ADR-060]] made the wedge survivable (bounded
   reads, a watchdog, a severity that no longer trusts `capture.state`); this
   ADR removes the query that was forcing the restarts in the first place.
   The self-reinforcing shape is worth naming: the query's cost scales with
   the number of live media assets, which grows precisely because the query
   was the thing preventing deletion.

**Decision: a human sets `kept`; nothing computes it.** `detection.kept_at`
(indexed) / `detection.kept_by` replace the first-of-species/best-of-species
computation. `kept` means **keep forever, until a human removes the flag** —
set and cleared only through `PUT`/`DELETE /api/v1/detections/{id}/keep`,
`oo detections keep <id> [--unkeep]`, or the drawer's keep toggle. Every
tier's candidate query now excludes `kept_at IS NOT NULL`: `_strip_native`
(7 d), `_strip_unkept` (renamed from `_strip_non_exemplar`, 30 d),
`_strip_expired` (90 d), and — the one place a computed exemplar rule never
reached — `_watermark_reclaim`. A kept recording survives the disk running
out, not just the calendar.

**Why a human flag beats a computed one.** The computed rule answered "is
this recording special", cheaply for one row but expensively for the whole
table, and an operator had no way to add to or override its judgement. A flag
answers "did someone decide to keep this", is one indexed boolean check per
row, and puts the judgement where it belongs — with the person who heard the
recording, not a query. The cost of a wrong computed answer was silent (a
recording quietly not exempted, or exempted for the wrong reason); the cost
of a wrong flag is visible and correctable by the same operator who set it.

**Why first-of-species is backfilled and best is not.** The `0008_detection_kept`
migration (Alembic revision `0008_detection_kept`, parent
`0007_capture_pause`) stamps `kept_at`/`kept_by = 'exemplar-backfill'` on
exactly one detection per species key — the earliest by `event_start_utc`,
ranked with a `ROW_NUMBER()` window function rather than `GROUP BY … HAVING
event_start_utc = MIN(…)`, because two detectors can fire on the same window
and share an `event_start_utc` to the second, which would otherwise stamp
both rows for one species. A first-ever record of a species at this station
cannot be recreated once its clip is gone. A *better* recording, by contrast,
may come along at any time — "best" was always a moving target the computed
rule re-evaluated every sweep, and there is no equivalent loss in leaving it
unflagged: nothing is destroyed by not backfilling it, and a human can mark a
genuinely exceptional recording kept the moment they hear it. The backfill
set is therefore smaller than the ~177 recordings the old computation
protected, which was the union of first and best.

**Why kept survives the watermark, and what that costs.** [[ADR-026]]'s
watermark tier is this project's one hard safety valve: disk space wins over
any retention preference, so that clip writes never fail outright. Making
`kept` exempt there too means a station whose operator keeps enough evidence
can, in principle, stay over its watermark with nothing left to reclaim.
`_watermark_reclaim` now reports this rather than overriding it:
`RetentionReport.watermark_blocked_by_kept` sums the bytes held by kept,
un-reclaimed assets, computed only when the watermark is actually exceeded so
a healthy station never pays for the query. It surfaces in
`housekeeping.retention_not_keeping_up`, `GET /api/v1/retention/status`, and
as a named `/api/v1/health` `problems` entry naming the byte figure and that
these are operator-kept recordings the sweep will not delete. The operator
gets a loud health problem instead of a silently deleted keep, which is the
trade this project's charter requires: a human's decision outranks the
sweep's convenience. `held` ([[ADR-043]]) is unchanged and deliberately narrower
— it exempts the three age-based tiers but not the watermark, since it is a
review-workflow marker ("needs my ear"), not a permanent keep; an operator
who needs the watermark exemption too must mark the detection `kept`.

**Breaking metric rename, accepted deliberately.**
`oo_retention_exemplar_detections` becomes `oo_retention_kept_detections`,
with **no backwards-compatible alias**. Any Home Assistant sensor or Grafana
panel bound to the old name breaks on this deploy. This was a deliberate
choice, not an oversight: the number now counts something genuinely
different — detections a human chose to keep, not detections a computation
decided were exemplary — and giving it the old, familiar name over changed
semantics would be exactly the kind of dishonest instrumentation this
project's counters exist to refuse (see `MEMORY.md`,
"Measure the instrument, not just the thing"). `RetentionReport.exemplar_detections`
is renamed to `kept_detections` for the same reason, and every consumer
(`api/app.py`, `api/metrics.py`, `cli.py`, `RetentionPanel.tsx`) was updated
in the same commit so nothing reads the old field name.

**A deviation from the approved design, ruled on here.**
[[2026-08-14-keep-flag-retention-design]] specified
that a sweep exiting with budget unspent while candidates remain should log a
`WARN` enriching the existing `housekeeping.retention_not_keeping_up` event.
The implementation instead adds a **separate ERROR event**,
`housekeeping.retention_never_reached_a_tier`, gated on
`len(result.tiers_skipped) == 4`, and leaves `retention_not_keeping_up`
untouched. **Ruling: accepted as an improvement on the design doc, not a
regression from it.** The existing WARN fired every 300 s for nine days
during the incident this ADR fixes, and nobody noticed — the noise was *why*
the failure hid, not despite it. Enriching a WARN that already fires
routinely would have reproduced the same blind spot with more fields
attached. An event that fires only when a sweep did no work at all — every
tier skipped, not merely a backlog trailing off — is rare enough in a healthy
station's steady state to mean something when it does fire.
`len(tiers_skipped) == 4` is sound as the gate specifically because the
watermark tier's guard has no config-disable clause, unlike the other three
(each can be turned off by setting its `*_days` to 0): all four can only
land in `tiers_skipped` together through genuine deadline or budget
exhaustion, never through configuration. `RetentionReport.preamble_s` (the
monotonic time from sweep start to the first tier guard) rides alongside
both events and the new `oo_retention_preamble_seconds` gauge, so a future
preamble regression is visible as a number close to `batch_budget_s`, not
just as an absence of deletions.

**Names that outlived the concept — left deliberately, decision deferred to
the operator.** The tier key `tier="exemplar_only"` (a Prometheus label and
an `/api/v1/retention/status` field) and the setting
`retention_exemplar_only_days` (env var `OO_RETENTION_EXEMPLAR_ONLY_DAYS`,
present in the live station's `runtime.env`) both still name the mechanism
this ADR removes — the 90-day tier now exempts `kept`, not "exemplars".
Renaming either is a second breaking metric change (`exemplar_only` is a
label value scraped by anything graphing per-tier deletions) plus a hand-edit
to an operator-owned env file this repository does not control. That is a
cost worth naming, not paying silently: this ADR leaves both names as they
are and records the mismatch here so it is found by reading this document,
not rediscovered as a mystery by whoever next greps `retention.py` for
"exemplar" and finds nothing computing one.

**Known asymmetry, out of scope here.** `_watermark_reclaim` already ignored
`held_ids` before this change — a held-but-not-kept recording could be
reclaimed at the watermark. This ADR makes `kept` exempt there; it
deliberately leaves `held`'s narrower, unchanged behaviour alone, since
widening [[ADR-043]]'s watermark behaviour is a separate decision. An operator
who needs a hold to survive the watermark must mark it `kept` as well.

**Consequences.** A station that has never once deleted a file (nine days
overdue) starts deleting again on the next sweep after this deploys. The
preamble drops from an unbounded ~3 s scan to one indexed `COUNT` and one
`held_detection_ids` query — small and bounded regardless of table size,
which also removes the self-reinforcing growth loop. `oo_retention_exemplar_detections`
disappears from `/metrics`; anything graphing it must be repointed at
`oo_retention_kept_detections` by hand.

**Rollback.** `.venv/bin/alembic downgrade 0007_capture_pause` drops
`kept_at`/`kept_by` entirely. **Every operator keep is lost and is not
recoverable from the code** — export the kept detection ids first:

```bash
sqlite3 -json data/openobservatory.sqlite \
  "SELECT id, kept_at, kept_by FROM detection WHERE kept_at IS NOT NULL" \
  > kept-detections-backup.json
.venv/bin/alembic downgrade 0007_capture_pause
sudo systemctl restart open-observatory
```

Reverting the application code without downgrading the migration is safe on
its own: `kept_at`/`kept_by` are additive columns and nothing reads them once
`retention.py`'s exemption clauses are reverted alongside it — but doing that
without the migration leaves the columns in place unused, which is a valid
intermediate state, not a hazard.

**Target smoke test**, to run after deploying and before trusting the
figures:

```bash
HOST=simon@192.168.1.195 ./deploy/deploy.sh --no-web   # runs alembic upgrade head

curl -s http://192.168.1.195:8080/metrics | grep -E "oo_retention_(files_deleted|kept_detections|preamble_seconds)"
curl -s http://192.168.1.195:8080/api/v1/retention/status | python3 -m json.tool
curl -s http://192.168.1.195:8080/api/v1/health | python3 -m json.tool | grep -A3 retention
```

Pass criteria, none of which may be assumed from the first two alone (Task 6
of the implementing plan flags this explicitly):

1. `oo_retention_files_deleted_total{tier="native"}` is **non-zero** —
   deletion has never once happened on this station, so this is the claim
   that matters most.
2. Sweep `duration_s` is **inside** `retention_batch_budget_s` (1.5 s), and
   `complete` is `true`. `preamble_s` should read as a small fraction of the
   budget, not close to it.
3. After ~30 minutes, `capture_gap` rows are **no longer arriving in pairs
   ~300 s apart** (read-only against the station database,
   `sqlite3.connect('file:...?mode=ro', uri=True)`). If the beat is still
   there, the sweep is still costing audio and the fix is incomplete — that
   must be reported plainly rather than reporting (1) and (2) as success on
   their own.

### ADR-061 addendum, 2026-08-14: the first deploy failed, and the index was why

**The pass criteria above were run, and criterion 1 failed.** Recorded here
rather than quietly fixed, because the failure is more instructive than the
decision it followed.

The 2.978 s exemplar preamble was genuinely gone: `preamble_s` was ~0 and the
sweep reached `_strip_native` for the first time in the station's life. It then
blocked **inside a single SQL statement for over five minutes.** `py-spy dump`
caught it exactly:

```
do_execute (sqlalchemy/engine/default.py:941)
  _strip_native (open_observatory/retention.py:397)
    sweep (open_observatory/retention.py:288)
```

Retention runs in the evidence executor and the housekeeping loop awaits it, so
everything behind it stopped: stream heartbeats, the [[ADR-057]] media audit, the
[[ADR-059]] disk-usage refresh. `clip_usage_age_s` climbed 1:1 with wall clock and
`housekeeping_blocking_s` was byte-identical between samples — a stopped clock,
not a slow one. Capture was untouched, because it owns its own thread ([[ADR-030]]).

**Cause: `ix_detection_kept_at`, added by this ADR's own revision 0008.**
`kept_at IS NULL` matches ~99.8% of rows (112 non-null of ~46,000), so the index
narrows nothing — but SQLite preferred it, and preferring it meant abandoning
`ix_detection_event_start_utc`, which had been serving the range predicate *and*
the `ORDER BY` together. Measured on the station's own database, best of three:

| | Plan | Time |
|---|---|---|
| Index present | `SEARCH d USING ix_detection_kept_at` + `USE TEMP B-TREE FOR ORDER BY` | 0.555 s |
| Index dropped | `SEARCH d USING ix_detection_event_start_utc (event_start_utc<?)`, no sort | 0.117 s |

Revision `0009_drop_kept_at_index` drops it. The predicate still applies; it is
just evaluated against rows the ordering index has already narrowed.

**Three things worth keeping from this.**

1. **923 passing tests could not have caught it, and did not.** No fixture in
   this repository is within two orders of magnitude of 119,476 media assets and
   ~46,000 detections, and below some size SQLite's planner simply makes the
   right choice. A green suite is not evidence about a query plan. Measure the
   plan on a database the size of the station's, or do not claim anything.
2. **`retention_batch_budget_s` cannot bound a single statement.** The budget is
   checked *between* rows of the result. A slow query therefore blows through it
   entirely instead of degrading to "fewer deletions", which is what the budget
   was designed to do. This was always true; the old code never reached a tier,
   so it never showed. **Still open** — the honest fix is a statement timeout
   (`sqlite3` `progress_handler`, or `Connection.interrupt` from a watchdog),
   not a larger budget.
3. **An index added to make a filter cheap can make the query dearer**, when the
   planner takes it in preference to one that was also providing the ordering. A
   low-selectivity index is not merely useless; it is a live hazard next to an
   `ORDER BY ... LIMIT`.

**Interim state on the station:** `retention_enabled=false` was written to the
station's `config/runtime.env` to unwedge the housekeeping loop, and must be set
back to `true` once revision 0009 is deployed and criterion 1 actually passes.

### ADR-061 second addendum, 2026-08-14: the same symptom, a third cause

Revision 0009 fixed `_strip_native` and broke the other half of the same sweep.
`RetentionReport.kept_detections` counts `kept_at IS NOT NULL`; with no index
that is a full `SCAN detection` over **290,956 rows**, measured ~6 s on the live
station under WAL contention. It sits in the sweep's *preamble*, before any tier
guard, so the 1.5 s budget was gone before the first tier and all four were
skipped. Zero deletions again — same symptom, third distinct cause.

**The observability from this ADR is what made that a ten-minute diagnosis
rather than a nine-day one.** The first sweep after deploy reported
`preamble_s: 6.0778`, `duration_s: 6.0781` and
`tiers_skipped: ["native","unkept","expired","watermark"]`. That is the entire
failure, stated by the station, on its first occurrence. Before this ADR the
same condition produced `complete=False` and a flat zero — indistinguishable
from "nothing to delete".

**Resolution: a partial index (revision 0010).** The two requirements only look
contradictory:

* the count wants an index on `kept_at`;
* the four candidate queries must have none available, or SQLite prefers it over
  `ix_detection_event_start_utc` and loses the ordering.

`CREATE INDEX ... ON detection(kept_at) WHERE kept_at IS NOT NULL` indexes 112
rows of 290,956. The planner may use it for `IS NOT NULL` and cannot use it for
`IS NULL`. Measured on a copy of the station's database, best of three:

| | before | after |
|---|---|---|
| `kept_detections` count | 0.151 s, `SCAN detection` | **0.000 s**, covering index |
| `_strip_native` candidates | 0.113 s, `ix_detection_event_start_utc` | 0.115 s, **unchanged**, no sort |

`tests/test_migrations.py` asserts the `WHERE` clause itself, not the index
name: a plain index would satisfy a name check and restore both failures at
once.

**The generalisable lesson, since this ADR has now been wrong twice about the
same column.** An index is not a property of a column, it is a property of the
*query plans it makes available* — including the plans you did not want. Both
mistakes were locally reasonable ("the filter should be indexed", then "so drop
the index") and both were made without measuring a plan on data the size of the
station's. The rule this ADR should have followed from the start, and now
states: **for any index on a hot path, produce `EXPLAIN QUERY PLAN` against a
station-sized database, for every query that touches the column, before and
after.** 924 passing tests said nothing useful about any of this.

### ADR-061 third addendum, 2026-08-14: the deferred rename, taken — and the 90-day tier retired as dead code

The "Names that outlived the concept" section above deliberately left
`tier="exemplar_only"` and `retention_exemplar_only_days` alone and deferred
the decision to the operator. The operator has now made it, and a second
finding came with it: the 90-day tier those names belonged to,
`_strip_expired`, is unreachable and is removed as dead code, not merely
renamed.

**Why `_strip_expired` never ran.** `_strip_unkept` (30 d) and
`_strip_expired` (90 d) issued byte-for-byte identical queries — same joins,
same `reclaimed_at IS NULL`, same `kept_at IS NULL`, same `notin_(held_ids)`,
same `order_by(event_start_utc.asc())`, same `limit(budget)` — differing only
in the cutoff constant and the label written to the decision. Because 90 > 30,
`_strip_expired`'s candidate set was always a subset of `_strip_unkept`'s, and
`_strip_unkept` ran first, oldest-first, against the same shared `budget`.
Three ways this was checked, not assumed:

1. If `_strip_unkept` exhausts its `budget` or the wall-clock `deadline`
   before finishing, the `budget > 0 and time.monotonic() < deadline` guard
   before `_strip_expired` fails and it is skipped outright.
2. If `_strip_unkept`'s query returns fewer rows than `budget` (proof it
   found every row matching a strictly looser condition), every row
   `_strip_expired` could ever have wanted was already offered to and
   processed by `_strip_unkept` first.
3. There is no asset-kind filter, ordering subtlety, or budget interaction
   that changes this: both queries scan the same table with the same joins
   and the same order.

**Correction (fourth correction to this ADR, final pre-merge review,
2026-08-14): the unreachability claim above is about row *logic*, given the
default tier configuration, and was stated without qualification. It is not
also true of every configuration.** Both `_strip_native` (`retention_native_days`)
and `_strip_unkept` (`retention_audible_only_days`) are individually
disabled by setting their `*_days` to `0` (`sweep()`'s guards:
`self.native_days > 0 and ...`, `self.audible_only_days > 0 and ...`). With
both set to `0`, both age tiers are disabled by configuration, not by the
row-subset argument above, and since `_strip_expired` no longer exists to
fall back on, **nothing in `retention.py` ever deletes a clip by age** —
only `_watermark_reclaim`, which `kept` and `held` both partially or wholly
exempt. `validate_merged` (`site_settings.py`) previously permitted this
combination outright, and the settings help text for both fields never
said `0` disables the tier. Fixed the same day this was found: a
`validate_merged` rule now rejects `retention_native_days == 0 and
retention_audible_only_days == 0` together, and both fields' help text
states plainly that `0` disables the tier and what disabling both leaves
running (the watermark only). An operator who wants every clip kept forever
should raise `retention_watermark_ratio` and mark evidence `kept`, not zero
both age tiers -- the watermark is this project's one tier with no
config-disable clause, by design (see the "Why kept survives the
watermark" section above), so it is the correct place to express "never
delete by age" rather than a configuration this ADR now blocks.

This was true before [[ADR-026]]'s `kept` predicate existed too, but it did not
matter then: exemplars were exempt from the 30-day tier but not the 90-day
one, so the two tiers protected different rows and "between 30 and 90 days,
only exemplars survive" was a real, distinct policy. `kept` (this ADR)
exempts a detection from *every* tier identically, which is what collapsed
the two tiers into duplicates without anyone deciding that on purpose.

**What changed.** `_strip_expired`, its `sweep()` call site, the `"expired"`
tier key, the `exemplar_only_days` constructor parameter/attribute, and the
`retention_exemplar_only_days` setting (`OO_RETENTION_EXEMPLAR_ONLY_DAYS`) are
all removed. `_TIER_ORDER` and `RetentionReport.tiers_skipped` go from four
tiers to three (`native`, `unkept`, `watermark`), so
`housekeeping.retention_never_reached_a_tier`'s gate moves from
`len(tiers_skipped) == 4` to `== 3` — still every tier, still sound for the
same reason: the watermark guard has no config-disable clause, so all three
land in `tiers_skipped` together only through genuine deadline or budget
exhaustion, never configuration. `GET /api/v1/retention/status` loses its
"kept only" tier entry (it described the now-nonexistent 30–90 day band);
`eligible_for_deletion`'s cutoff moves from `exemplar_only_days` to
`audible_only_days`, since that is now the last age boundary the policy has.

**Test-first verification.** Before touching `retention.py`, a
characterisation test was added and confirmed to pass against the
*unmodified* code: a detection 200 days old is deleted, its recorded tier is
never `"expired"`, and `tier_counts.get("expired", 0) == 0` — i.e. the 90-day
tier already contributes nothing, on the code as it stood. The same test
passes unchanged after the removal (`"expired"` simply cannot appear once the
key no longer exists), which is the point of a characterisation test: the
observable behaviour is identical on both sides of the change.

**The tier key rename, taken this time.**
`oo_retention_files_deleted_total{tier="exemplar_only"}` becomes
`{tier="unkept"}`, and `{tier="expired"}` disappears with the tier it named.
Both are breaking Prometheus label changes, accepted deliberately for the
same reason the metric rename earlier in this ADR was: a label naming a
mechanism (`exemplar_only`) or a tier (`expired`) that no longer exists is a
worse instrument than a breaking one. No alias is added.

**The silent-drop hazard this leaves behind.** `Settings` is built with
`extra="ignore"`, so an operator's `runtime.env` that still sets
`OO_RETENTION_EXEMPLAR_ONLY_DAYS=90` — including the live station's, per the
"Names that outlived the concept" section above — will not raise on startup;
Pydantic drops it silently, and the value is simply never read again. That is
the dangerous kind of stale config: not a crash that demands attention, but a
setting that keeps *looking* live in an env file while doing nothing. This is
recorded here rather than fixed here, because fixing it (an unknown-key
warning, or refusing to start) is a `Settings`-wide decision, not one this
retention change should make unilaterally — but the next person who greps
`runtime.env` for why a number they changed had no effect should find the
answer here.

**Consequences.** No behaviour change to what gets deleted or when — the
characterisation test is the evidence for that claim, not merely an
assertion of it. `retention.py`'s module docstring's tier table drops the
30–90/90+ split in favour of a single "30+ days: only kept survives"
statement. The live station's `runtime.env` still carries
`OO_RETENTION_EXEMPLAR_ONLY_DAYS=90`; per the paragraph above, this deploy
makes that line inert rather than erroring, and it should be removed by the
operator at the same time as the deploy, not left to be rediscovered later.

---
Part of the [[ADRS|Architecture Decision Record index]].
