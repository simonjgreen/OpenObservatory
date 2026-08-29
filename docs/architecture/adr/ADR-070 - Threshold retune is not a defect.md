---
aliases:
  - ADR-070
tags:
  - adr
---
# ADR-070: A threshold retune is not a discovery that the past was wrong
**Status:** accepted, 2026-08-23

(A parallel session was writing what is now [[ADR-069 - Two drift gates|ADR-069]] in the same working tree
at the same time; this one took 070 after both briefly claimed 069. Nothing
below depends on the number.)

### The problem: a repair command that would have withdrawn a third of the record

`oo detections reconcile-plausibility --apply` is irreversible in the only way
that matters. `plausibility_repair.find_implausible_detections` skips any row
that already carries a `native_result.plausibility_review`, so a second run
cannot lift a flag the first run set — by design, so a repeat pass does not
re-flag or overwrite a review an operator has already seen. Since [[ADR-044 - Withdrawn detections|ADR-044]] a
flagged row is withdrawn everywhere, immediately, with no restart.

`cli.py` called that function like this:

```python
findings = find_implausible_detections(
    session,
    model_dir=model_dir,
    latitude=settings.latitude,
    longitude=settings.longitude,
    plausibility_floor=settings.birdnet_plausibility_floor,
    limit=limit,
)
```

The floor, and nothing else. `find_implausible_detections` also takes
`common_prior`, `range_threshold`, `threshold_in_range`, `threshold_uncommon`
and `threshold_out_of_range`, all five of which are `Settings` fields with
environment surface (`OO_BIRDNET_THRESHOLD_IN_RANGE` and friends, added
precisely so an operator could tune them without editing Python). All five fell
back to the function's own signature defaults — 0.15 / 0.03 / **0.55** / 0.75 /
0.90 — whatever the station was actually running.

The live station has run `OO_BIRDNET_THRESHOLD_IN_RANGE=0.35` since 2026-08-09,
an operator tuning experiment set in its untracked `config/runtime.env`. So the
detector admitted and stored rows at 0.35, and the repair pass re-judged those
same rows at 0.55. **Every row in the 0.35–0.55 gap was reported implausible.**

Measured, full depth, read-only, 2026-08-23:

| | |
|---|---|
| findings | **32,660** |
| highest flagged score | **0.549992** — one ten-thousandth under the leaked default |
| Common Woodpigeon | 9,168 |
| European Robin | 7,434 |
| Collared Dove | 2,477 |
| genuinely implausible species among them | **none** |

Roughly a third of the bird record, including the operator's actual garden
birds, one `--apply` away from being withdrawn and not recoverable by
re-running. The highest flagged score is the tell: 0.549992 is not a
distribution of wrong species, it is a distribution cut off by a number.

[[HANDOVER]] §6.3 item 0 was, at the time, still telling a future operator to
run exactly that command.

### The general problem, which is larger than one missing argument

Passing the five settings through fixes today. It does not fix the shape of the
question, and it would be dishonest to ship it as though it did.

A band threshold is a *tunable*. The table therefore holds rows written under
several different bars, and **no single current threshold is correct for the
whole table.** Re-judging every stored row against today's numbers is wrong in
both directions: it withdraws rows that were correctly admitted under the bar in
force when they were written, and it says nothing about rows admitted under a
bar looser than any value this command was ever told about. Plumbing the
configuration through would have moved the cliff from 0.55 to 0.35 — it would
not have removed it. The next time the operator raises the bar, the same command
becomes armed again, and the dry run would look just as plausible.

The deeper point: **a threshold change is a statement about what to admit next,
not a discovery that the past was mis-decided.** [[ADR-032 - Plausibility bands|ADR-032]]'s repair exists because
two *band assignments* were wrong — a near-zero prior that a score used to
overrule, and a missing prior that got the easiest bar instead of the strictest.
Those are correctness defects. "I have decided to be stricter from now on" is
not, and a command that cannot tell the two apart will eventually rewrite the
record to match a preference.

### What made this fixable: the record already knew

`detectors/birdnet.py` has stamped `native_result.threshold_applied` on every
detection it writes since the first BirdNET commit (db30257, 2026-08-04), which
predates the oldest row in this station's database. Until now **nothing had ever
read it** — one write, no readers, anywhere in `src/`.

So each row carries the bar it personally had to clear, and the answer to "was
this row correctly admitted?" is on the row rather than in today's environment.

### Decision

Two changes, and they are not the same change.

1. **`cli.py` passes all five configured band values through**, alongside the
   floor. The repair pass judges by the station's configuration, not by a
   function signature's defaults. This is the plumbing defect, and on its own it
   is not sufficient.

2. **`find_implausible_detections` will not flag a row for a threshold that
   moved after the row was written.** A row is exempt when *both*: its
   recomputed band is the same band it was stored in, and its score cleared its
   own recorded `threshold_applied`. What survives that filter is what this
   repair is actually for — a row whose **band** is different today (defect (b)),
   or one the plausibility floor now rules out at any score (defect (a)).

The floor is deliberately **not** given the exemption. [[ADR-032 - Plausibility bands|ADR-032]]'s claim is that a
near-zero prior is not a higher bar but a statement that no score is admissible;
applying today's floor to history is this command's purpose, not configuration
drift. The exemption is keyed on the *band being unchanged* precisely so a
floor change, which moves a row into `implausible`, still bites.

The finding gains an `admitting_threshold` field, surfaced in `--json` and in
`to_dict()`, so a dry run says for each row whether its original bar was known.

### The limitation that remains, stated plainly

A row that does not carry `threshold_applied`, or carries it as something other
than a finite number, **cannot be judged this way** and is still measured
against the currently configured threshold for its band. If that bar has been
raised since such a row was written, the row will be reported as implausible
when the only thing that changed is the operator's preference.

Those rows are identifiable — their `admitting_threshold` is `null` — and on
this station the class is expected to be empty, because the key predates the
data. But "expected to be empty" is not "impossible", and the honest position is
that for such a row this command still cannot tell a defect from a retune. The
mitigation is the dry run and the null marker, not a claim that the problem is
gone.

The alternative — refusing to judge any row that does not record its own bar —
was rejected: it makes the command silently narrower than it looks, and the rows
it would skip are exactly the oldest ones, which are the ones [[ADR-032 - Plausibility bands|ADR-032]]'s repair
was written for.

### A false alarm, resolved: the 5,890 `non_biological` findings

The same dry run reported 5,890 findings in a `non_biological` band, while
`find_implausible_detections`'s docstring said it *"skips BirdNET's eleven
non-taxonomic classes entirely ([[ADR-049 - Sound categories are not species|ADR-049]])"*. That looked like a contradiction and
is not one; both statements were true of different things, and the docstring was
the wrong one.

[[ADR-049 - Sound categories are not species|ADR-049]] exempts the eleven sound categories from the **occurrence prior and the
floor** — the range model returning 4e-06 for "Engine" is not "engines are
absent from this garden", it is "a car is not a taxon with a distribution".
`band_for` still sorts them into a band, `non_biological`
(`birdnet_classes.NON_TAXONOMIC_BAND`), and still judges them **on score**, at
the ordinary in-range bar. So they moved with the in-range bar exactly like
every other row, which is why 5,890 of them appeared in the 0.35–0.55 gap and
why they disappear once the configured 0.35 reaches the pass.

No behaviour changed here. The docstring was corrected to say "exempt from the
prior, still subject to a score bar", and
`test_sound_categories_are_judged_at_the_configured_in_range_bar` now pins both
halves so the next reader does not have to re-derive it.

### Checked and clean

- `apply_plausibility_flag` takes a finding, not a configuration: it re-checks
  human review ([[ADR-043 - Taxon correction|ADR-043]]) and writes. It has no thresholds to get wrong.
- `oo detections reconcile-taxonomy` (`taxonomy_repair.py`) takes only `limit`
  and decides from the label list. No configured thresholds, no defect.
- `oo history reconcile-streams` takes `ratio_threshold` as an explicit CLI
  option defaulting to the `SUSPECT_FRAME_RATIO` constant. It is a heuristic
  belonging to that command, not a value the capture path also uses to admit
  rows, so there is no second copy to drift from. Not the same shape.
- `media_repair.py` has no thresholds at all.

The defect was specific to the one repair command that shares tuning with a
live detector.

### Consequences

- The 61 rows flagged on the live station on 2026-08-09T15:32:03Z are untouched
  and stay withdrawn. This ADR does not un-flag anything; it changes what a
  *future* run would flag.
- [[HANDOVER]] §6.3 item 0, [[MILESTONE_STATUS]], [[DETECTOR_STRATEGY]] and
  [[GAP_REPORT]] all said the command had never been run with `--apply`. All
  four were wrong, and had been for a fortnight. Corrected in the same change,
  with the evidence.
- The command is safe to re-run against a station once this code is deployed.
  It is **not** safe on the station as it stands, and every one of those four
  documents now says so.

**Reviewed 2026-08-29:** the decision holds and the code is unchanged. The third
bullet above is out of date about the station: the deployed build is at or after
the commit that carried this change. `GET /api/v1/history?since=2026-08-27` is
answered with a coverage block rather than the 500 a date-only `since` used to
produce, and that repair shipped in the same commit as this one (debba2d,
2026-08-24); the station published display firmware 0.2.5 at
2026-08-24T11:43:45Z, later still. Both are indirect — the repair pass is
CLI-only and nothing about it is observable over a read-only HTTP check — but
nothing points the other way. [[HANDOVER]] §6.3 item 0, [[MILESTONE_STATUS]],
[[GAP_REPORT]] and [[ADR-032 - Plausibility bands|ADR-032]] all still carry the standing instruction not to
run `--apply` until this is deployed; the delivery state is theirs to settle,
not this ADR's, and it has not been settled.

### Verification, including one check that cannot be performed

- `tests/test_cli_detections.py::test_the_configured_band_thresholds_reach_the_repair_pass`
  — the plumbing. Fails against the pre-ADR-070 CLI.
- `tests/test_plausibility_repair.py::TestThresholdChangesAreNotDefects` — the
  behaviour, both directions: a row admitted at 0.35 is never flagged when
  judged at 0.55, *and* a species below the floor is still flagged by the same
  call. Fails against the pre-ADR-070 module.
- Suite: 973 passed, 9 skipped, 2026-08-23, with
  `--deselect tests/test_api.py::TestLiveChannels` (a pre-existing TestClient
  limitation, not a regression). Ruff and mypy clean on every touched file.
- **Not verifiable:** `GET /api/v1/taxa/activity` caps `hours` at 168, so it
  cannot be asked about the 2026-08-09 apply at all. That cross-check is
  **unfulfillable, not passed**, and must not be recorded as evidence. The
  withdrawal was confirmed instead against a single row through
  `GET /api/v1/detections/{id}`: `a233415f3f72406f9e67769e972c5e62`,
  *Flammulated Owl*, score 0.8756, `withdrawn: true` with a populated
  `withdrawal` object.

### Rollback

Revert the `cli.py` call site and the exemption in `plausibility_repair.py`;
nothing is persisted by either and no schema or stored row changes. The
`admitting_threshold` key simply stops appearing in `--json` output. Rolling
back restores the loaded gun, so the [[HANDOVER]] warning must stay whatever
happens to the code.

### Smoke test

    oo detections reconcile-plausibility --json --limit 100000 > /tmp/findings.json
    jq 'length, ([.[] | select(.admitting_threshold == null)] | length)' /tmp/findings.json

Second number is the rows judged without knowing the bar that admitted them —
the class this command still cannot judge safely. Read those before applying
anything.

---
Part of the [[ADRS|Architecture Decision Record index]].
