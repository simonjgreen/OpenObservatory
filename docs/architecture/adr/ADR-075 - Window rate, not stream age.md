---
aliases:
  - ADR-075
tags:
  - adr
---
# ADR-075: The slope-agreement check must compare like intervals, and the next linearity bar is set before the run
**Status:** accepted, 2026-08-29. The linearity decision below is written **before** the run it governs, deliberately.
**Relates to:** [[ADR-069 - Two drift gates|ADR-069]] (defines gate (b) and the six checks; this fixes one of them
and leaves the other five alone), [[DRIFT_GATE_B_2026-08-25]] (the two runs that
exposed it, neither re-scored), [[ADR-070 - Threshold retune is not a defect|ADR-070]] (a bar that moves after the
fact does not reach backwards), [[ADR-046 - Deficit is mostly drift|ADR-046]] (the method and the phase
correction), [[ADR-072 - Accepted crystal drift|ADR-072]] (the ~50 ppm offset itself, accepted)

### The problem: one of the six checks was measuring how old the stream was

[[ADR-069 - Two drift gates|ADR-069]] gate (b) requires, among six checks:

    abs(abs(slope_ppm) - abs(rate_offset_ppm)) <= 2 ppm

`slope_ppm` is a Theil–Sen fit over the **sampled window**. `rate_offset_ppm` is
read from the last sample and is not a window quantity at all: `AlsaSource`
computes it as

```python
presented = first_frame + self.missing_frames_total
self.observed_rate_hz = presented * NS_PER_S / elapsed_ns
self.rate_offset_ppm = (self.observed_rate_hz / rate - 1.0) * 1e6
```

where `elapsed_ns` is measured from the stream's **anchor**. It is a cumulative
average over the whole stream. The two quantities therefore have different
intervals, and the length of the difference is the age of the stream:

- On a **young** stream the cumulative interval barely outruns the window, so
  the two are nearly the same number by construction and the check passes
  without having tested anything.
- On an **old** stream the cumulative average cannot track one hour's local
  rate, and a crystal that varies at all across a day makes them differ. The
  check then fails for a reason that is not drift.

Both halves were observed on the same hardware a few hours apart
([[DRIFT_GATE_B_2026-08-25]]). Attempt 1 ran on a 72-hour-old stream and failed
at **3.563 ppm**; attempt 2 ran 40 minutes after a deploy had restarted capture,
so the "cumulative average" covered barely more than the window, and it passed at
**0.145 ppm**. Nothing about the clock changed between them.

Reproduced against the simulated station in `tests/test_capture_drift_sampler.py`,
where the crystal is a chosen constant and the only variable is the number the
station reports as its cumulative average:

| the same 50 ppm crystal, the same window, the same samples | `rate_offset_ppm` | agreement | old check |
|---|---|---|---|
| stream anchored just before the window | −50.00 | 0.001 ppm | **passes**, vacuously |
| stream anchored days earlier, cooler | −35.00 | 14.999 ppm | **fails**, on stream age |

[[ADR-069 - Two drift gates|ADR-069]] anticipated the direction of this — it records that the two have
"different anchors, a slope against a cumulative average" and are "complementary,
not corroborating" — but it then wrote a numeric threshold as though they were
comparable. That is the defect. It is in the gate's design, not in the station.

### Decision 1: the check compares two derivations of the same interval, and says so

The station-side figure is now derived over **the same segment the slope is
fitted through**, from the counters the sampler already records, in the form
`AlsaSource` itself uses:

    observed_rate = (delta frames + delta estimated_missing_frames) / delta elapsed
    station_ppm   = (observed_rate / sample_rate - 1) * 1e6

Lost frames are added back because they are frames the device *presented*, and
the crystal is what they measure — the same reason `AlsaSource` adds them
([[ADR-039 - Confirmed loss, not deficit|ADR-039]]).

**`delta elapsed` is the station's own clock at the last block's start, not the
sampler's `monotonic_s`.** Both reasons were measured on the two archived CSVs
rather than assumed, and both are larger than the 2 ppm bar:

| using the sampler's wall clock | measured |
|---|---|
| block quantisation — `frames` advances one 100 ms block at a time, so a raw endpoint difference carries up to one block at each end | **±25.65 ppm** over an hour. It read −44.12 and −54.89 ppm on two runs whose crystals were −47.96 and −50.78 |
| host clock skew — the laptop's `CLOCK_MONOTONIC` against the Pi's, plus request latency spanning 250 ms | **−2.4 ppm** across one run and **+4.1 ppm** across the next. A rate measured against it fails a 2 ppm bar on the wrong machine's clock |

`expected_frames / sample_rate - block_age_s` is the station's elapsed time at
the block's start, and `frames - block_frames` is the frame count at that same
instant. Both come from one `time.monotonic_ns()` reading inside one snapshot, so
the quantisation and the latency cancel exactly. Across an eight-by-eight grid of
endpoint choices on both real runs that estimate moves by **0.47 ppm**; the
`monotonic_s` form moves by 4.5 ppm.

**What the check now is, stated plainly so nobody quotes it as more.** Both sides
are derived from the same frame counter over the same interval and agree
algebraically — the corrected deficit is `rate x (block_start - anchor) - frames
at block start`, so its slope is `rate - observed_rate` by construction. This is a
**consistency cross-check between two derivations of the same quantity over the
same interval, not independent confirmation of the drift**. [[ADR-069 - Two drift gates|ADR-069]] already
conceded that; the only change here is that the threshold now matches the
concession instead of contradicting it.

What it still catches, which is not nothing:

- a Theil–Sen fit dragged by outliers, or per-minute bucketing that moved the
  answer — the fit is robust and median-collapsed, this is a plain endpoint
  difference, and a window whose deficit is bent enough will separate them;
- a phase-band filter that dropped a biased subset of samples;
- **confirmed loss**, which is the one term the deficit slope does not carry:
  19,200 frames of loss inside an hour is 13.5 ppm of disagreement. That is
  asserted in `test_confirmed_loss_fails_the_run`.

What it can no longer do is report the age of the stream. The **±2 ppm bar is
unchanged** — it is [[ADR-069 - Two drift gates|ADR-069]]'s number and this ADR does not touch it.

**The degenerate case is guarded explicitly rather than left to divide by zero.**
`block_age_s` is rounded to milliseconds by the station, so two endpoints put at
most 1 ms of rounding into the elapsed time: 1e6/elapsed ppm, which is half the
bar at 1000 s and worse below it. `STATION_PPM_MIN_WINDOW_S = 1000.0` refuses to
report a figure below that, as does a segment with fewer than two in-band
samples, a non-finite elapsed time, or a missing column. Refusing returns `None`,
and **a check that could not be evaluated is not a pass** — it reports `false`.
A window that short has already failed the 3600 s duration check anyway, so this
refuses nothing a passing run needs; at 3600 s the derivation's own resolution is
0.28 ppm, or 14% of the bar.

**Three JSON keys change**, and that is stated here rather than discovered by a
reader diffing two run logs:

| was | is |
|---|---|
| `checks.slope_agrees_with_rate_offset_ppm_within_2` | `checks.slope_agrees_with_window_frame_rate_ppm_within_2` |
| `ppm_agreement` | `window_ppm_agreement` (`null` when the window is too short) |
| — | `window_frame_rate_ppm`, the new derivation (`null` likewise) |

`station_rate_offset_ppm` stays in the summary, unchanged, as context for the
reader — it is a real measurement of a real thing, just not of this window, and
dropping it would lose the evidence that the two differ. `ppm_agreement` is
**renamed rather than redefined in place**: the same key with a new referent is
exactly how a recorded result gets silently misread later.

Nothing else reads any of these keys — grepped across `src/`, `web/`,
`firmware/`, `tests/`, `scripts/` and `docs/`; the only occurrences outside the
script and its tests are the two archived run logs in `results/`, which are
historical records and stay as written.

**Neither 2026-08-25 run is re-scored.** Re-analysed with the fix, attempt 1's
agreement is 0.028 ppm and attempt 2's is 0.257 ppm, so both now clear this
check — and both still **fail** the gate on linearity and step, unchanged, which
is the outcome [[DRIFT_GATE_B_2026-08-25]] records and this ADR leaves standing.

### Decision 2: the next linearity run happens before the linearity bar moves

Gate (b) has failed twice on linearity, reproducibly, at the same size and the
same shape: **3.147 ms** and **2.743 ms** against a **0.5 ms** bar, with a step of
0.663 and 0.664 ms — two figures that agree to a thousandth of a millisecond.
Attempt 2 recorded temperature and returned `residual_vs_temperature_r` **+0.693**
(peaking at +0.706 at a 1–2 minute lag, decaying smoothly to zero by 20 minutes;
r² ≈ 0.50).

There is a standing proposal that the 0.5 ms bar is untransferable: it was
derived from [[ADR-046 - Deficit is mostly drift|ADR-046]]'s three clean segments, all **22.2 minutes or
shorter**, and a thermal excursion with a period of hours is approximately linear
across 22 minutes and demonstrably is not across 65. That may well be right. It
is not yet decided, and this ADR does not decide it, because **both attempts
deliberately covered the same morning warming ramp** and half the variance is
still unexplained — plausibly because the thermometer is on the Pi's SoC and the
crystal under test is inside the AudioMoth, whose own sensor is unreachable while
it is capturing.

**The decision, taken now, before the run:**

1. **The next attempt runs over a *falling* temperature — an evening run.** The
   thermal hypothesis makes a sharp, cheap prediction: the residual hump
   **inverts**. Nothing about a third repetition of the same dawn ramp can
   distinguish "the crystal follows temperature" from "something with a
   one-hour period happens at dawn", which is why two runs of it have settled
   nothing.
2. **No threshold moves before that run.** The 0.5 ms linearity bar and the
   0.5 ms step bar stand exactly as [[ADR-069 - Two drift gates|ADR-069]] wrote them, and the evening run
   is scored against them.
3. **Only if the residual still exceeds 0.5 ms** may a *temperature-corrected*
   residual be adopted — and the correction coefficient, in ms per °C, must be
   **declared in writing before the run it is applied to**, with the run that
   fitted it named. A coefficient fitted on a window and then applied to that
   same window is not a correction, it is a curve fit with a spare parameter.
4. **The relaxation is not on the table.** If drift is not linear across an
   hour, the honest fix is [[DRIFT_GATE_B_2026-08-25]]'s own suggestion — fit
   drift over a window short enough to be linear while testing loss over the
   long one, two measurements rather than one widened threshold. Widening a bar
   after seeing the numbers that failed it is the specific thing [[ADR-069 - Two drift gates|ADR-069]]
   exists to prevent ("a threshold chosen after seeing the numbers is not a
   threshold"), and [[ADR-070 - Threshold retune is not a defect|ADR-070]] settles the general case: a bar that moves
   later does not reach backwards, so a moved bar would leave 2026-08-25's two
   runs recorded as failures against the bar they were actually run under, not
   retroactively passed.
5. **If the residual does *not* track a falling temperature**, there is an
   unexplained ~6 ppm hour-periodic mechanism in the capture clock. That is a
   finding to chase, not to accommodate, and it would be a reason to keep the
   0.5 ms bar rather than to move it.

### What this ADR does not decide

It does not tick gate (b), which has still not passed and is still recorded as
having failed twice. It does not touch the other five checks, the phase
correction, the Theil–Sen fit, or the 3600 s restart-free requirement. It does
not settle the thermal hypothesis, which remains supported and unproven. It makes
no claim about [[ADR-072 - Accepted crystal drift|ADR-072]]'s accepted ~50 ppm offset, which this check was never
capable of confirming independently and now no longer pretends to.

### Rollback

The change is confined to `scripts/measure_capture_drift.py` and its tests. The
script is a laptop-side measuring instrument: it opens one read-only HTTP
connection to `/api/v1/health` and `/metrics` and writes a CSV. No service calls
it, nothing in the capture path, the schema, the settings or the dependency set
is touched, and nothing is deployed to the station. `git revert` and rebuild
nothing. Archived CSVs re-analyse under either version; only the three key names
above differ between them.

### Smoke test

```bash
# Re-analysis costs the station nothing and needs no host. Both archived runs
# must now clear the agreement check and must still fail on linearity.
python scripts/measure_capture_drift.py --analyse results/drift-2026-08-25.csv \
  | grep -E 'window_frame_rate_ppm|window_ppm_agreement|slope_agrees|max_residual'

python -m pytest tests/test_capture_drift_sampler.py -q
```

**Outcome, 2026-08-29 evening — the run this ADR required has happened, and the
temperature coefficient it contemplated must not be adopted.** Gate (b) was run over
a falling ramp (60.05 → 44.65 °C) and failed again: linearity 3.7796 ms, step
0.9827 ms. But it failed *differently*. `residual_vs_temperature_r` came out
**−0.2194**, against +0.693 and +0.706 on the two warming ramps — the correlation
flipped sign and collapsed, so temperature is not the mechanism and the precondition
this ADR set for a corrected residual ("if the residual still exceeds 0.5 ms *and*
temperature explains it") is not met. The bar stays where [[ADR-069 - Two drift gates|ADR-069]]
put it.

Two things this ADR did not anticipate, both recorded in [[DRIFT_GATE_B_2026-08-29]]:
the 0.5 ms bar turns out to sit almost exactly at the sampler's own noise floor —
a per-minute median carries a standard error of ~0.182 ms, so the largest of 65
should land near 0.50 ms by chance — which means the bar is neither arbitrary nor
impossible, and the measured 3.78 ms is 7.5× it. And the slope-agreement check this
ADR rewrote passed its first live use at **0.794 ppm**.

---
Part of the [[ADRS|Architecture Decision Record index]].
