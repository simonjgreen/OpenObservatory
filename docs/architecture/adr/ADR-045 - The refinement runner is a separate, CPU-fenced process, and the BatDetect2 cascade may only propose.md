---
aliases:
  - ADR-045
tags:
  - adr
---
# ADR-045: The refinement runner is a separate, CPU-fenced process, and the BatDetect2 cascade may only propose
**Decision:** Charter item 5 — "refine the record later, when better information
exists — and never silently" — ships as a **second process**, `oo refine run`,
started by `open-observatory-refine.timer` at 01:00 UTC and fenced with
`AllowedCPUs=2-3`, `Nice=19`, `MemoryMax=1G`, `CPUWeight=1`, `IOWeight=1`,
`IOSchedulingClass=idle`. Its first job is the [[ADR-017]] BatDetect2 cascade over
stored `evidence_native` ultrasonic clips.

Four things are enforced in code rather than left to discipline
(`src/open_observatory/refinement/`), because every honesty failure on this
project so far has been sincere:

1. **Only from new information.** `EvidenceIdentity` hashes refiner, model,
   weights *and configuration* into a fingerprint. `record_refinement` refuses
   (`RefinementViolation`) when the refiner's `(model_id, model_version)` is the
   pair that made the original claim, and `ix_refinement_evidence` is unique on
   `(detection_id, evidence_fingerprint)`, so the same instrument under the same
   settings cannot bank a second, more optimistic answer about the same event.
   A re-run returns `None`, idempotently — not a new claim. Configuration is in
   the hash deliberately: without it, the only way to get a second answer out of
   one model would be to bump a version string, which is exactly the quiet,
   sincere workaround this project keeps finding after the fact.
2. **The original claim is preserved.** The `refinement` row snapshots
   `original_common_name` / `original_scientific_name` /
   `original_taxonomic_group` / `original_score` verbatim, read from the live row
   at write time. The writer never touches the detection's claim columns and
   *proves* it: nine columns (including a deep copy of `native_result`, since a
   shallow snapshot would compare a dict with itself and pass) are compared
   before and after, and any movement raises.
3. **A refined record is distinguishable.** `detection.refined_at`,
   `refinement_version` and `refinement_outcome` say on the event itself that
   refinement ran, at what version, with what outcome — which is exactly what the
   charter's retention decision asks each event to carry.
4. **The cascade may only propose.** `Refiner.authority` is `"propose"` for
   BatDetect2 and `record_refinement` raises if a propose-authority refiner
   reports an `applied` outcome. No shipped refiner has `apply` authority.

**Reason — the fence.** [[ADR-033]] measured what "expensive work, isolated on its
own thread, inside the capture process" is actually worth: a 0.30 s retention
sweep starved the event loop 55–150 ms and produced **~1.9 false `capture.gap`
records a minute**, because an executor partitions queueing and nothing
partitions the GIL. A BatDetect2 pass is 2.1 s of inference — the same defect,
two orders of magnitude larger. `DeferredDetectorWorker` (`detectors/deferred.py`)
is the mechanism [[HANDOVER]] §1a nominated for this, and it is deliberately
**not** used: it is an in-process `asyncio.Queue` of live `AudioWindow`s whose
central safety property is dropping anything older than
`max_delivery_latency_s`, and a clip written six hours ago is exactly what it
would correctly reject. Reusing it here would have meant disabling the one thing
it exists to do. It remains the right mechanism for a *live* detector too slow to
run inline; a refiner over stored evidence is a different problem and gets its
own, smaller contract (`refinement/contracts.py`).

### The fence directives were verified on the target, not assumed

Measured on the station (Pi 5, `systemd 255 (255.4-1ubuntu8.16)`, cgroup v2,
`nproc` 4) on 2026-08-09, with a transient system unit carrying the exact
directives:

```
$ sudo systemd-run --unit=oo-fence-probe3 -p AllowedCPUs=2-3 -p Nice=19 -p MemoryMax=1G ...
/proc/self/status:Cpus_allowed_list:  2-3
EFFECTIVE=2-3           # cpuset.cpus.effective of the unit's own cgroup
MEMMAX=1073741824       # memory.max
NICE_FIELD=19           # field 19 of /proc/self/stat
```

**One real side effect, found by checking rather than assuming.** Before the
first such unit ran, the station's root `cgroup.subtree_control` was
`cpu memory pids` — the `cpuset` controller was available but *not enabled*.
systemd enabled `cpuset` (and `io`) in the root subtree on demand, permanently.
That does not constrain anything by itself: `open-observatory.service` continues
to report an empty `AllowedCPUs` and is scheduled on all four cores. It is
recorded because "a controller appeared in the root cgroup" is the kind of change
that is invisible until it is the explanation for something else. Note also that
`user.slice` does **not** carry `cpuset` on this host, so `systemd-run` *without*
`sudo` would silently fence nothing — the refiner must be a system unit.

### Capture impact, measured against a control window of equal length

Two consecutive ~10-minute windows on the live station, 2026-08-09, AudioMoth at
384 kHz, `started_utc` identical in all three snapshots so no restart voided the
comparison. The load window ran two busy-loop processes pinned to cores 2-3 under
the exact production fence — deliberately a **worse** load than the real refiner,
which alternates inference with clip reads rather than spinning both cores flat
out for ten minutes.

| Window | From (UTC) | Length | Frames delivered | Expected | **Deficit** | `capture.gap` | loop-lag events/min | worst loop lag |
|---|---|---|---|---|---|---|---|---|
| Control, cores 2-3 idle | 10:32:02 | 601 s | 230,745,600 | 230,757,589 | 11,989 frames = 0.031 s | **0** | 2.10 | ≤ 0.214 s |
| Fenced load on cores 2-3 | 10:42:03 | 621 s | 238,425,600 | 238,435,472 | 9,872 frames = 0.026 s | **0** | 2.22 | **0.293 s** |

**The frame deficit under load was *lower* than the control window's**, and
neither is evidence of lost audio. Two corrections have to be applied before that
column means anything, both already recorded on this project:

- **Crystal drift.** `expected_frames` is derived from elapsed time at the
  *nominal* rate, and this AudioMoth runs about 50 ppm slow
  (`observed_rate_hz` 383,980.8 → **−49.96 ppm**), so it legitimately delivers
  fewer frames than nominal with nothing lost — see
  [[OPEN_INVESTIGATION_CAPTURE_GAPS]], "the deficit has a bias of its own"
  (2026-08-09). At −50 ppm, pure drift accounts for **11,538 frames** of the
  control window's 11,989 and **11,922** of the load window's 9,872. The load
  window's deficit is *smaller than drift alone predicts*.
- **Block quantisation.** Frames arrive in 38,400-frame blocks (100 ms), so a
  single instantaneous read of `frames` vs `expected_frames` carries up to one
  whole block of sampling error — **38,400 frames**, which is more than three
  times the drift term and eighteen times the 2,117-frame difference between the
  two windows.

So the honest reading is: **both windows are consistent with zero lost audio, and
the difference between them is inside the measurement's own resolution.** Which
is the finding — not "the fence made capture better".

Zero capture gaps in either window, against [[ADR-033]]'s ~1.9 per minute when the
same class of work ran inside the capture process. Loop-lag *events* rose by
0.12/min (+6%), which is not distinguishable from run-to-run variation at these
counts.

**What did move, and is reported rather than buried:** the worst single
event-loop stall in the load window was **293 ms**, exceeding the 214 ms high
water mark accumulated over the whole preceding 13 minutes. `loop_lag_max_s` is
cumulative over the process lifetime, so the control row can only be stated as an
upper bound. The fence does not eliminate scheduling excursions on a 4-core box;
it stops them turning into lost audio. On this evidence the cost is a longer tail
on a metric nobody consumes, and no measurable cost to the thing that matters.

**Cross-checked against ground truth, per the standing rule that a counter on
this project is not evidence until it agrees with something else** (four
instruments on this project were found lying on 2026-08-08/09). A direct query of
the station's own `capture_gap` table returns `(0 rows, 0 estimated_missing_frames)`
for both windows and for the whole session, agreeing with `gaps_with_loss` /
`gaps_without_loss`. Detection writing continued throughout — 89 detection rows
in the control window, 122 in the load window — so the pipeline was doing real
work, not idling through the comparison. `estimated_missing_seconds` was
deliberately not used: it over-reported by 12.9× ([[ADR-039]], SETUP.md trap 10).

**What this measurement is not.** The refiner itself was not deployed to the
station and no BatDetect2 pass was timed on target in this session; the 2.1 s per
pass figure is [[ADR-017]]'s, from 2026-08-05. What is measured here is the *fence* —
that two cores can be saturated under it without capture noticing — which is the
claim the design rests on.

### The accuracy decision: propose, do not apply

The speed case for the cascade was settled by [[ADR-017]]'s 2026-08-05 update — 2.1 s
of inference per pass, so 1015 passes is ~36 minutes for a whole night, and
trimming to 1.5 s centred on the loudest sample is where three quarters of that
saving comes from. **The accuracy case is not settled, and this ADR does not
pretend otherwise.** On this station's own 33–36 kHz cluster ([[HANDOVER]] §6.3
item 6):

- 6 of 8 clips leaned *Myotis*, at det_prob **0.20–0.30** — a lean, not an
  identification;
- one clip returned *Pipistrellus pygmaeus* at **0.77** on a call whose measured
  peak was **34 kHz**, when soprano pipistrelle peaks near 55 kHz;
- the AudioMoth gain is hot and still clips on loud nearby events ([[HANDOVER]]
  §6.3 item 4), an unresolved confound for all of the above.

That middle item is the decisive one. It is the same shape as the 0.96 BirdNET
score on a species absent from the continent that [[ADR-032]] ruled on: *a confident
answer contradicted by a physical fact the station measured itself is evidence
that the score is meaningless for that species, not evidence of the animal.* A
classifier that fails that test on this station's audio has not earned authority
over this station's record, and the honesty constraint — "never claim more than
the evidence supports" — puts the ceiling at `propose`.

This is not a placeholder for "apply, once we are braver". Charter item 5 lists
*a human ear* as a basis for refinement in its own right, the `review` table is
the mechanism, and the honest sequence is: proposals first, a human listening to
the audible renderings second, and only then any argument *from evidence* that
this model has earned more. Three supporting choices follow from the same
reasoning:

- **No species-frequency plausibility filter**, tempting as one is after the
  *pygmaeus* contradiction. This station has no calibrated, sourced reference for
  UK species peak frequencies, and reconstructing one from memory is the class of
  plausible fabrication this project avoids elsewhere (see the favicon note in
  [[HANDOVER]] §6.3a for the same reasoning applied to an icon). Instead every
  proposal carries the station's *own* `peak_frequency_hz`, `peak_snr_db` and
  `pulse_count` next to the model's species and det_prob — the pairing that
  exposed the contradiction in the first place — plus a `caution` string
  assembled only from measured facts.
- **The 0.05 det_prob floor is a noise floor, not a truth threshold.** It exists
  so one pass does not emit a dozen near-zero species rows. The measured
  0.20–0.30 leans must survive it: a low-confidence lean is precisely what a
  human ear should arbitrate, not what we hide.
- **Only `evidence_native` clips are classified.** A heterodyne rendering has
  discarded everything outside its tuned band and a time-expanded one is no
  longer at its original rate; classifying either is classifying the renderer.

### `unavailable` is not `no_change`

The outcome vocabulary separates "the refiner examined this and could not improve
it" (`no_change`, `confirmed`) from "the refiner never saw it" (`unavailable`,
`failed`). `EXAMINED_OUTCOMES` is the set that counts as examined. This exists
because the charter's retention safeguard is aimed at exactly that confusion:
*"the risk is not old data, it is data the refiner never actually saw — a failed
timer, missing model assets, a station that was down."* A pipeline that recorded
a missing clip as `no_change` would make the safeguard useless while looking
correct. For the same reason, missing model assets make a run **skipped, with a
reason** (`RefinerUnavailable`), never a pass that silently found nothing.

### What this does not do

- **It does not change `retention.py`.** The sweeper still deletes on age alone:
  `_strip_native`, `_strip_non_exemplar` and `_strip_expired` filter only on
  `event_start_utc`, and nothing anywhere reads `refined_at`. So today a clip can
  be reclaimed at 7, 30 or 90 days having never been examined once — the failure
  the charter's first safeguard names. The schema now makes the fix cheap and
  indexed (`ix_detection_refined_at`); the guard is one predicate per tier:

  ```sql
  AND detection.refinement_outcome IN ('proposed','no_change','confirmed','applied')
  ```

  It is deliberately **not** applied here. Turning it on before the refiner has
  completed a full cycle would freeze all deletion on a station whose disk is a
  real constraint, and changing a live station's deletion policy is the
  operator's call, not a side effect of adding a column. `oo refine status`
  reports how many events have never been examined, which is the number that
  decision needs. `tests/test_refinement.py::TestRetentionGap` and
  `tests/test_refinement_integration.py::TestRetentionInteraction` pin the
  current behaviour so whoever changes it changes those too.
- **It does not implement the second safeguard**, the explicit human hold, and it
  does not connect proposals to the review workflow. `review` *is* written to —
  `POST /api/v1/detections/{id}/review` has inserted rows since 2026-08-08
  ([[ADR-029]]), which corrects the charter's "nothing writes to it yet" — but that
  endpoint knows nothing about proposals, so `refinement.resolved_at` and
  `resolved_review_id` stay NULL. Wiring "accept this proposal" to a `review` row
  (and deciding whether accepting one may finally move the detection's claim) is
  the next piece of charter item 5, and it is the piece where a human ear is the
  new information.
- **It does not surface refinement in the API, the UI, the MQTT publisher or the
  counter-top display.** Nothing a person sees on those surfaces changes, which is
  correct while every refinement is a proposal: a proposal is a question, not an
  observation, and putting one on a counter-top display would be precisely the
  over-claiming this ADR exists to prevent. `oo refine status` is the surface.
- **It does not add BatDetect2 as a dependency or an extra.** Its whole
  repository is CC-BY-NC-4.0 ([[ADR-006]], [[ADR-017]]); the operator installs it
  (`pip install batdetect2==1.3.1` plus CPU torch, see
  [[BATDETECT2_EVALUATION]]), and the tests stub the library at
  its own boundary rather than requiring it.

### Rollback and smoke test (ADR-045)

**Rollback is a single command and needs no deploy**, because the runner is a
separate unit and the station process never imports `refinement/`:

```bash
sudo systemctl disable --now open-observatory-refine.timer
```

That leaves capture, detection, retention and every surface byte-identical to
pre-ADR-045 behaviour. `OO_REFINEMENT_ENABLED=false` in `config/runtime.env` is
the equivalent for a station that keeps the timer armed.

To roll the *schema* back — only necessary if the `refinement` table itself is a
problem, which is unlikely since nothing else reads it:

```bash
.venv/bin/alembic downgrade 0004_drop_dead_detection_indexes   # drops the table and the three columns
```

Note that `db/session.py`'s `create_all()` + ALTER TABLE patcher re-adds the
columns on the next start ([[ADR-035]]'s known coupling), so a full schema rollback
also means reverting the code.

Target smoke test — run **on the Pi**, in this order, checking capture at each
step:

```bash
# 1. The fence really is a fence, on this systemd.
systemctl show open-observatory-refine -p AllowedCPUs -p Nice -p MemoryMax
sudo systemctl start open-observatory-refine
cat /sys/fs/cgroup/system.slice/open-observatory-refine.service/cpuset.cpus.effective  # 2-3

# 2. A dry run makes no claim and writes nothing.
.venv/bin/oo refine run --force --dry-run

# 3. What has never been examined -- the number the retention safeguard needs.
.venv/bin/oo refine status --json

# 4. Capture, before and after. NOTE: this instruction was written before ADR-046
#    and had it backwards. `expected_frames - frames` is ~98% crystal drift plus
#    block-sampling phase (+-50 ms on a single reading), NOT lost audio; it grows
#    ~0.18 s/hour on this device while nothing is lost. Judge loss by
#    estimated_missing_seconds, which ADR-039 made a decomposition of the deficit
#    rather than a second number, and cross-check the gap counters against the
#    database rather than believing either alone.
#    2026-08-14: the "~98% crystal drift" figure holds at this run's duration
#    (<=1h) only. At the 72-hour soak the residual deficit was unexplained by
#    drift -- see ADR-046's 2026-08-14 status note.
curl -s localhost:8080/api/v1/health | python3 -c \
  'import json,sys; c=json.load(sys.stdin)["capture"]; print(c["frames"], c["expected_frames"], c["gaps_with_loss"], c["gaps_without_loss"], c["loop_lag_max_s"], c["loop_lag_events"])'
python3 -c "import sqlite3; print(sqlite3.connect('data/openobservatory.sqlite').execute(
  \"select count(*), coalesce(sum(estimated_missing_frames),0) from capture_gap where start_utc >= datetime('now','-1 hour')\").fetchall())"
```

---
Part of the [[ADRS|Architecture Decision Record index]].
