---
aliases:
  - ADR-069
tags:
  - adr
---
# ADR-069: "The one-hour drift run" is two different tests, and this is the criterion each must meet
**Status:** accepted, 2026-08-23. Written **before** either run, deliberately.
**Run records:** [[DRIFT_GATE_A_2026-08-25]] (gate (a), passed),
[[DRIFT_GATE_B_2026-08-25]] (gate (b), failed on linearity).

### The problem: one line item, two tests, five documents

[[IMPLEMENTATION_PLAN]] §Milestone 4.5 carries a single outstanding line item,
"the drift test at its full one-hour duration". Five documents track it as one
thing. It is not one thing, and reading them together shows two incompatible
resolutions of the same phrase:

**(a) A synthetic resampler run.** [[AUDIO_PIPELINE]]'s "Resampling
correctness" list asks for "no cumulative timestamp drift over one hour of
generated PCM", and its later note says that test "has been run at **five
minutes**, not one hour". [[TEST_PLAN]] §L7 repeats it as "the one-hour drift
run (verified at 5 minutes only)". The five-minute evidence is the
`oo audio resample-check` table in [[TARGET_DIAGNOSTICS]]. Wherever a document
says "verified at 5 minutes", it means (a). This test touches no hardware and no
station.

**(b) A live capture-clock drift run.** [[MILESTONE_STATUS]]'s Milestone 1
exit-gate note, its §Milestone 4.5 list and its outstanding-items list, plus
[[HANDOVER]] §6.3 item 3 and [[OPEN_INVESTIGATION_CAPTURE_GAPS]], all say the
best evidence for "the one-hour drift run" is [[ADR-046 - Deficit is mostly drift|ADR-046]]'s 42.7-minute sampled run
whose longest *clean* segment was 22.2 minutes. "Clean segment", "restart-free"
and "a mechanism with an hourly or nightly period" are properties of a live
station; none of them mean anything for a synthetic run. [[HANDOVER]]'s "the
method is written down and takes 15 minutes" quotes [[ADR-046 - Deficit is mostly drift|ADR-046]], which is about the
**live sampler**, not `resample-check`.

**(c) And a third, in Milestone 1's own exit gate**, which neither of the above
satisfies: "one-hour generated/replayed stream shows no timestamp drift or
unexplained gaps" — the *capture pipeline* run for an hour over the replay or
synthetic source. Nothing in the repository records this ever having been run.

The two readings are not interchangeable, and the cheap one does not close the
expensive one. (a) is about a libsoxr conversion; (b) is about an AudioMoth's
crystal, an ALSA ring and a read loop. A document that ticks the line item on
(a)'s evidence would be claiming something about the station that was never
measured — the precise failure mode this project keeps rediscovering.

**Decision:** the line item is **two gates, (a) and (b), both required**, tracked
separately from here on, with (c) recorded as a distinct open item rather than
quietly absorbed into either. This ADR defines each one's pass criterion **in
advance of the run**, because a threshold chosen after seeing the numbers is not
a threshold.

### Gate (a): the synthetic resampler run

    oo audio resample-check --source-rate 384000 --target-rate 48000 \
        --seconds 3600 --block-ms 100 --json

Run on the target device. Exit status 0 is the verdict; the four rules live in
`audio/resample_check.py` and are unchanged from the implementation that has
been in use since Milestone 2:

| property | rule | where the number comes from |
|---|---|---|
| group delay | `abs(delay) <= 1.0` output frame | one frame at 48 kHz is 20.8 µs. Anything larger biases *every* audible detection timestamp by a fixed amount, and a fixed bias is exactly what a fixture test cannot see. The measured value is 0 |
| delivery deficit trend | `abs(trend) < (deficit_max - deficit_min) + 8` frames | the deficit oscillates within a band because libsoxr emits ragged chunks; a trend is only meaningful once it exceeds the band the ragged chunking itself produces. The `+ 8` keeps the rule from degenerating when the band is near zero |
| seam continuity | `worst_step < median_step * 25` | a click at a block boundary is a step far outside the signal's own step distribution. 25x is well clear of the 1.53x a clean hour produces |
| tone identity | peak within 2 FFT bins (1.46 Hz) of 1000 Hz | two bins of a 65,536-point FFT at 48 kHz; one bin is the resolution floor, so one bin would fail on rounding alone |

**Why an hour beats five minutes, quantified — this is written down nowhere
else.** The trend statistic compares the mean deficit of the first tenth of the
run with the last tenth, so their centres are 0.9 of the run apart. A drift of
*r* ppm therefore shows up as `0.9 x r x 1e-6 x seconds x 48000` frames, and
must clear the measured 820-frame threshold (band 112–924, plus 8):

| duration | smallest detectable drift | that drift over a 72-hour soak |
|---|---|---|
| 5 minutes (what has been run) | **63 ppm** | 16.4 s |
| **60 minutes (this gate)** | **5.3 ppm** | 1.4 s |

Five minutes cannot distinguish a perfect resampler from one drifting at 60 ppm
— roughly the size of this station's own crystal offset, and 16 seconds of
timestamp error across a soak. The hour buys a **12x** improvement and puts the
floor below anything that would matter across a 72-hour run. That, and not
duration for its own sake, is the argument.

**Two honest weaknesses of (a), stated rather than discovered later.** First,
the trend threshold is *adaptive*: it is derived from the run's own deficit
band, so a backend whose band widened would also widen its own limit. It is kept
because the band is a property of the chunking, which is what makes a trend
readable at all — but a future backend change must re-derive the table above
rather than assume 820. Second, `AudibleResampler` drives conversion from an
exact `Fraction(48000, 384000)`, so arithmetic drift is impossible by
construction; (a) tests the backend and the block-boundary state machine, not
the ratio. It is a regression test on libsoxr and on our streaming of it. It is
not, and must not be quoted as, evidence about the station's clock.

### Gate (b): the live capture-clock drift run

    python scripts/measure_capture_drift.py --host <station-host> \
        --seconds 3900 --interval 2 --min-restart-free-s 3600 \
        --csv results/drift-<date>.csv --declare-load "<what else was running>"

Sampled **from the laptop over one persistent HTTP connection**, never over
one-shot `ssh … curl`: a probe that opened an SSH handshake per sample was
itself the load, taking the station from 5 overruns in 2 h to 6 in 13 minutes,
and nothing from that window could be attributed
([[OPEN_INVESTIGATION_CAPTURE_GAPS]], "Traps this round produced"). 65 minutes
are sampled to leave slack for the hour that must survive inside them.

The quantity fitted is the **phase-corrected** deficit, because the raw
`expected_frames - frames` sawtooths across a whole block while nothing is wrong
and carries ±50 ms of pure artefact ([[ADR-046 - Deficit is mostly drift|ADR-046]]):

    corrected = expected_frames − block_age_s × sample_rate − (frames − block_frames)

**Pass criterion — every one of these, on a single unbroken segment:**

| check | threshold | derivation |
|---|---|---|
| restart-free duration | **≥ 3600 s** in one segment, `stream_id` unchanged, `clock_reanchors` unchanged | the unproven hypothesis is a mechanism with a period longer than [[ADR-046 - Deficit is mostly drift\|ADR-046]]'s 22.2-minute clean window — an hourly sweep, a nightly rotation. An hour covers the hourly class. It does not cover the nightly class, and this ADR does not claim it does |
| points fitted | **≥ 10** per-minute medians | a slope through a handful of points is not a measurement; [[ADR-046 - Deficit is mostly drift\|ADR-046]]'s shortest quoted window had ten and was already its weakest |
| slope vs the station's own figure | `abs(abs(slope_ppm) − abs(rate_offset_ppm)) ≤ 2 ppm` | [[ADR-046 - Deficit is mostly drift\|ADR-046]] measured these agreeing to about **1 ppm** (3.6 ms/hour, ambiguous sign) on clean windows, while the crystal itself moved about **3 ppm** under a thermal change inside one hour. 2 ppm is above the agreement actually achieved and below the thermal excursion, so it fails on disagreement rather than on weather |
| linearity | max abs residual from the Theil–Sen line, per-minute medians, **≤ 0.5 ms** | [[ADR-046 - Deficit is mostly drift\|ADR-046]]'s three segments measured 0.30, 0.25 and 0.44 ms. 0.5 ms is just above the worst clean value observed on this station |
| no step | no residual step **> 0.5 ms** | drift is a straight line; real loss arrives as a step that stays up. Same basis as the row above |
| confirmed loss | `estimated_missing_frames` and `gaps_with_loss` both **unchanged** across the window | this is the station's one measurement of lost audio ([[ADR-039 - Confirmed loss, not deficit\|ADR-039]]). Note it is *not* independent of the slope check — `rate_offset_ppm` is `-(deficit − missing_frames)/expected × 1e6` computed inside `AlsaSource`, so the two agree algebraically. The checks are complementary (different anchors, a slope against a cumulative average), not corroborating |
| load | `--declare-load` recorded verbatim; `loop_lag_max_s`, `late_reads` and `hot_path_cpu_ratio` reported per segment | [[ADR-046 - Deficit is mostly drift\|ADR-046]]'s run was contaminated by another agent's two-core load probe and was salvaged only because the contamination was visible in these counters. Declaring beats assuming |

**The run can be voided by the mains, and the record must say so.** The station
is on unprotected mains: the 2026-08-22 restart is now established as a **mains
power cut**, confirmed by the operator, with the kernel back 17 s after the last
log line. A cut of a few seconds ends the restart-free segment and voids the
run, and no amount of care in the sampler can prevent it. Two consequences.
First, the evidence record for a passing run must state that the station was on
unprotected mains for the window — a run is only as unbroken as the supply that
carried it. Second, `--seconds 3900` against a 3600 s requirement exists partly
for this: it is cheaper to sample 65 minutes and discard the tail than to
discover at minute 58 that the hour must start again. A run interrupted this way
is not a failure of the gate and must not be recorded as one; it is a run that
did not happen.

The fit is **Theil–Sen over per-minute medians**, never ordinary least squares:
a late read corrupts the phase correction for a sample or two, because a block's
`monotonic_start_ns` is read-completion minus one block duration, and OLS read
**6 ppm high** on [[ADR-046 - Deficit is mostly drift|ADR-046]]'s own data where the median pairwise slope did not.

**Why (b) is worth the hour, and why it should run before the next soak
attempt.** The failed soak left ~175 s of deficit that drift does not explain
over 54.7 hours — about **890 ppm** if it were continuous. A one-hour run
passing at the 2 ppm criterion above rules out a continuous mechanism of that
size by a factor of roughly **400**, which would establish that the soak's
residual is *episodic* rather than a leak. That is a diagnostic result about the
open soak failure, not merely a box being ticked.

### Gate (c): Milestone 1's generated/replayed hour

Neither (a) nor (b) exercises the capture pipeline over a full hour of
generated or replayed audio, which is what Milestone 1's exit gate literally
asks for. It is achievable without touching the microphone — `OO_SOURCE=synthetic`
(or `replay`) with its own `OO_DATA_DIR` and port opens no ALSA device — and it
is **left open here rather than retired**, because retiring a written exit gate
by not mentioning it is how this line item came to mean two things in the first
place.

### Consequence: the instrument had to be fixed before it could be used

**The memory fault (a) would have hit.** The original `audio_resample_check`
accumulated every output block, concatenated them, and diffed the result. At the
five minutes it had ever been run for, that is ~300 MB and invisible. At the
hour this gate requires it peaks at about **2.8 GB**, measured by extrapolation
from 60/300/900-second runs, on a device with 7.8 GiB that is simultaneously
running capture (1.37–1.72 GB RSS) at 384 kHz. `resample-check` is a documented
target-device smoke test, so anyone running it at full duration hits this, and
an OOM there kills capture — which `CLAUDE.md` ranks above closing this gate.

The measurement now lives in `audio/resample_check.py` and streams in one pass
with bounded state. Measured peak RSS at `--seconds 3600`: **138 MB**, against
128 MB at `--seconds 60` — flat in duration, as intended. `tests/test_resample_check.py`
holds the original accumulating algorithm as a reference implementation and
asserts the two agree: group delay, output frames, deficit band, trend, worst
step and spectrum peak are **exact matches**, and the median absolute step
agrees to 1e-4 relative, because it is now a 2^20-bin histogram estimate rather
than an exact sort. That is four orders inside the 25x threshold it feeds, so no
histogram error can move a verdict.

`--json` was added so the hour's result is diffable against the five-minute
baseline, and it carries **each threshold beside the measurement it judged** — a
recorded result that cannot be re-checked against its own criterion is how a
threshold gets moved after the fact.

`scripts/measure_capture_drift.py` is the instrument for (b), tested against a
simulated station whose crystal offset is chosen in advance
(`tests/test_capture_drift_sampler.py`): it must recover 50 ppm from a 50 ppm
station, refuse to fit across a restart, fail on confirmed loss, and not be
dragged by a late-read excursion the way OLS is.

### What this ADR does not decide

It does not tick anything. No box in [[ACCEPTANCE_CRITERIA]] currently covers
either gate — that omission is real and a wording is proposed alongside this
ADR — and neither run has been executed. It also does not extend [[ADR-046 - Deficit is mostly drift|ADR-046]]'s
"98% crystal drift" claim: that holds at ≤ 1 hour and, per [[ADR-046 - Deficit is mostly drift|ADR-046]]'s own
2026-08-14 status note, does not hold at 72 hours.

**Reviewed 2026-08-29:** both gates have since been run — the run records are
linked at the top — so "neither run has been executed" is true only of the day
this was written. [[ACCEPTANCE_CRITERIA]] still carries no box for either gate,
and the criteria themselves are unchanged in `audio/resample_check.py` and
`scripts/measure_capture_drift.py`. Three corrections to the reasoning above,
none of them to a threshold. The ~890 ppm exclusion argument assumed a passing
run implied a slope near zero; 2 ppm is an *agreement* limit, and against the
~46 ppm actually measured the exclusion is a factor of about 19, not 400
([[DRIFT_GATE_B_2026-08-25]]). [[ADR-072 - Accepted crystal drift|ADR-072]] then accepted that ~50 ppm offset
outright, and [[ADR-073 - Five capture SLOs|ADR-073]] separates drift from loss and places the failed soak's
deficit in the pre-[[ADR-060 - A stalled read is a dead stream|ADR-060]] gap era — an episodic mechanism, which is the
reading gate (b) existed to test — so the diagnostic question has largely been
answered without gate (b) passing. Neither ADR touches the defect the second
attempt exposed: the slope-agreement check compares one hour's slope against
`rate_offset_ppm`, a cumulative average over the whole stream, so it is close to
vacuous on a young stream and close to certain to fail on an old one. That needs
its own ADR, and none has been written.

### Rollback

`audio/resample_check.py` and `scripts/measure_capture_drift.py` are new files;
the CLI change is confined to one command that no service calls. Nothing in the
capture path, the schema, the settings or the dependency set changed, so a
rollback cannot affect capture. `git revert` and rebuild nothing.

### Smoke test

```bash
# (a), short form -- the same four properties, seconds instead of minutes.
oo audio resample-check --seconds 30 --json | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(d["passed"], d["group_delay_frames"], \
   d["deficit_trend"], "<", d["deficit_trend_limit"])'

# (b), instrument only -- 60 s of sampling proves the connection and the CSV,
# and must report gate_met false, because 60 s is not an hour.
python scripts/measure_capture_drift.py --host <station-host> --seconds 60 \
  --csv /tmp/drift-smoke.csv --declare-load "smoke test" | grep gate_met
```

---
Part of the [[ADRS|Architecture Decision Record index]].
