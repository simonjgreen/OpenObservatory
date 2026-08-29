# Design: an operator-set "keep" flag replaces the computed exemplar rule

**Date:** 2026-08-14
**Status:** approved, not yet implemented
**Supersedes in part:** [[ADR-026]] (tiered retention) — the exemplar mechanism only

## Why this exists

`RetentionSweeper.sweep()` calls `_exemplar_detection_ids()` at `retention.py:225`,
before any deadline check. That method is unbounded: a `DISTINCT` join across every
live media asset, seven columns including the `native_result` JSON blob, ~46,000
rows, materialised into Python so the bat ranking can be done in a loop.

Measured on the station: **2.978 s**, against a `retention_batch_budget_s` of
**1.5 s**.

One query causes three separate failures.

| Symptom | Mechanism | Status |
|---|---|---|
| Retention has never deleted anything, in nine days past its threshold | The budget is spent before the guard on line 230, so all four tiers are skipped and `_strip_native` is never entered | **Proven** |
| ~7 s of audio lost per hour, which cost the 72-hour soak its continuity gate (99.865% against ≥99.9%) | ~3 s of GIL-holding ORM work every 300 s starves the event loop, so the capture read's continuation is late and the 0.5 s ALSA ring overruns | **Proven** — gap rows arrive in pairs exactly `retention_interval_s` apart |
| The 2026-08-14 wedge: 3 h 35 min deaf | Each overrun triggers `snd_pcm_prepare()`; ~12 forced device restarts an hour, one of which did not come back | Coupling **proven** to the millisecond (sweep `started_at` 02:18:19.893, `duration_s` 2.6608, first `-EIO` 02:18:22.552). The prepare-failure mechanism is **inferred** from `hw_ptr` frozen at 768 = exactly two packets after a prepare |

It is also self-reinforcing: the query's cost scales with the number of live media
assets, which grows precisely because the query prevents anything being deleted.

[[ADR-060]] made the wedge survivable. This removes the exposure that causes it.

The failure was invisible for nine days because `complete=False` reads identically
whether a sweep ran out of time with work outstanding or never reached a tier at
all, and the only other symptom was a flat zero counter — which looks exactly like
"nothing to delete".

## The decision

Replace the computed first-of-species/best-of-species exemption with a flag the
operator sets by hand.

**`kept` means keep forever, until a human removes the flag.** Nothing else may
clear it — not age, not the 90-day expiry that currently retires exemplars, and
not disk pressure. A machine deleting what a human asked it to keep is the class
of quiet dishonesty this project exists to avoid.

### Data model

```
detection.kept_at   TIMESTAMP(tz) NULL   -- indexed
detection.kept_by   TEXT NULL            -- actor: "simon", or "exemplar-backfill"
```

Mutable columns, deliberately. `Review` is append-only because a correction is a
*claim* about what a recording contains and the original claim must survive
([[ADR-043]], charter priority 5). Keeping a recording is a storage preference, not a
claim about the world, so it does not need that treatment. `kept_by` and `kept_at`
retain who and when.

Alembic revision `0008_detection_kept`, parent `0007_capture_pause`. Applied by
`deploy.sh` ([[ADR-042]]).

### Migration backfill: first-of-species only

The migration runs the existing first-of-species computation one final time,
stamps `kept_at = now()` and `kept_by = 'exemplar-backfill'`, then the computation
is deleted from the codebase.

**First-ever only, not best-of-species.** A first-ever record cannot be recreated;
a better recording may come along. This also drops the `peak_snr_db`/`pulse_count`
bat ranking, which existed only to make "best" meaningful for a detector that
identifies passes rather than species.

Without this backfill the change would make ~177 currently-protected recordings
deletable on the very next sweep — including the first-ever record of a species —
before any UI existed to protect them by hand. That is irreversible; un-keeping
later is not.

### Retention changes

`_exemplar_detection_ids()` and its call site are deleted. `_strip_non_exemplar`
becomes `_strip_unkept`.

**All four tiers** gain the same clause on their candidate query — `_strip_native`
(7 d), `_strip_unkept` (30 d), `_strip_expired` (90 d) and `_watermark_reclaim`:

```python
.where(orm.Detection.kept_at.is_(None))
```

Applying it to every tier is what makes "forever" true; applying it only to the
30-day tier would leave a kept recording deletable at 90 days, which is the
current exemplar behaviour and the thing being fixed.

In `_strip_unkept` this also removes the `limit(budget * 4)` over-fetch and the
Python filter loop. The existing comment says exemplar filtering "isn't
expressible as a join condition without materialising `exemplar_ids` into the
query" — as a column, it is.

The index exists to keep that clause cheap on the candidate queries:
`ix_detection_kept_at` on `detection(kept_at)`.

`held_ids` stays exactly as it is: 0.000 s, 62 rows, and `held` keeps its distinct
meaning of "keep this, it needs a human ear". A detection may be both.

### The watermark consequence, stated plainly

At `retention_watermark_ratio` (0.85) the sweep reclaims unkept evidence oldest
first, as now. If it cannot get under the watermark without touching kept
recordings, **it stops and health reports a `problems` entry** naming how many
kept recordings hold how many bytes. It does not delete them.

If the operator ignores that, the disk fills and clip writes begin to fail.
Capture itself is unaffected — capture always wins, and evidence writing is
already a bounded queue that drops rather than blocks (`station.py:309-313`). A
visible, nameable full disk is a better outcome than silently deleting the
recording someone asked to keep.

### Surfaces

- `PUT /api/v1/detections/{id}/keep` — sets `kept_at`/`kept_by`, returns the
  updated detection. `DELETE` on the same path un-keeps. Both behind the auth gate
  when auth is enabled (they are operator actions, not public reads).
- A keep toggle in `web/src/components/DetectionDrawer.tsx`, beside the existing
  review controls — the decision belongs at the moment of listening.
- `oo detections keep <id>` and `--unkeep`, output routed through `emit_json`
  (`test_cli_json_output.py` asserts no `console.print_json` call in `cli.py`).

### Observability, so this cannot hide again

`RetentionReport` gains `preamble_s` and `tiers_skipped`. A sweep that exits with
its full budget unspent while candidates exist logs a `WARN` naming which tier was
skipped and why. `complete=False` stops being the only signal, and stops
conflating "ran out of time" with "never started".

## Testing

Test-first throughout.

| Test | Proves |
|---|---|
| A sweep deletes native assets past the cutoff on the first tick | The tier is reached at all — the regression that started this |
| A kept detection survives every tier, including 90-day expiry and watermark | "Forever" means forever |
| Un-keeping makes it deletable again | Only a human clears it, but a human can |
| A held detection is still exempt, and held ≠ kept | [[ADR-043]]'s mechanism is untouched |
| The sweep completes within `retention_batch_budget_s` with a realistic row count | The 3 s preamble is gone, not relocated |
| The watermark reports a problem rather than deleting kept evidence | The honest failure mode |
| Migration backfills exactly one detection per species key — the earliest by `event_start_utc` — and no `best`-ranked rows | The backfill set is first-of-species only. It will be smaller than the 177 the station currently reports, since that figure is the union of first and best; the test asserts the rule, not a row count |
| Migration is reversible | `0008 -> 0007` round-trips on a throwaway DB |

The sweep-duration test needs enough rows to be meaningful; generate them in a
fixture rather than asserting against the live station.

## Rollback

`alembic downgrade 0007_capture_pause` drops `kept_at`/`kept_by`. Every keep an
operator has set is lost and is not recoverable from the code, so a downgrade
after real use should be preceded by exporting the kept detection ids.

Reverting the code without the migration is safe: the columns are additive and
nothing else reads them.

## Sequencing

1. Migration, backfill, retention change, observability — one commit. Stops the
   data loss and removes the wedge exposure.
2. API, CLI, UI toggle — a second commit.
3. Deploy, then verify on the station that a sweep actually deletes, that sweep
   duration is inside its budget, and that the `capture_gap` pair-per-300 s beat
   stops.

Step 3 is the one that matters: every previous claim in this area has been
measured, and two of them were wrong.

## Out of scope

- The ESP32 display gets no keep control. It is a separate firmware deploy with
  its own OTA rollback drill ([[ADR-050]]).
- The 15,704 bat detections never examined by a refiner ([[ADR-045]]) remain
  unaddressed. Retention still deletes on age alone, and this change gives the
  operator a manual way to protect individual recordings, not a policy for that
  backlog.
- Making the exemplar *idea* cheap in SQL. It is being removed, not optimised.
