# Drift gate (b), 2026-08-25 — **DID NOT PASS**, and what it found instead

The live capture-clock drift run defined in ADR-069, run for the first time at
full duration. **It did not pass.** Three of six checks failed.

This document records the failure as a failure. ADR-069 says a threshold chosen
after seeing the numbers is not a threshold, so nothing below moves one. The
proposal at the end is a proposal.

## The run

    python3 scripts/measure_capture_drift.py --host 192.168.1.195 --port 8080 \
        --seconds 3900 --interval 2 --min-restart-free-s 3600 \
        --csv results/drift-2026-08-25.csv --declare-load "..."

| | |
|---|---|
| window | 2026-08-25T05:57:58.969Z → 07:02:57.744Z |
| segment | **one**, unbroken, `ef29e800-5c03-409d-a769-f4d1719c784c` |
| duration | 3898.8 s (64.98 min) against 3600 s required |
| samples | 1950, **0 failed**, **0 HTTP reconnects**, 3 dropped out of phase band |
| minute points fitted | 65 |
| declared load | post-soak; station idle apart from normal capture and detection; display connected on 0.2.5; no deploy, no ssh, no other agent. **Unprotected mains** (ADR-069 requires this be stated) |

The run itself was clean. It was not interrupted, not contaminated, and not
short. Whatever this result is, it is not a measurement failure.

Immediately before this run the station completed a valid 72-hour soak
(`SOAK_2026-08-22.md`), so the clock under test is the same one that produced
99.9948% continuity.

## Result

| check | threshold | measured | |
|---|---|---|---|
| restart-free duration | ≥ 3600 s | 3898.8 s | **PASS** |
| points fitted | ≥ 10 | 65 | **PASS** |
| no confirmed loss | deltas unchanged | all 0 | **PASS** |
| slope vs station figure | ≤ 2 ppm | **3.563 ppm** | **FAIL** |
| linearity | ≤ 0.5 ms | **3.156 ms** | **FAIL** |
| no step | ≤ 0.5 ms | **0.663 ms** | **FAIL** |

- Theil–Sen slope: **+46.287 ppm**, 95% CI **[45.576, 47.269]**
- Station's own `rate_offset_ppm`: **−49.85**
- Signs differ by convention and agree physically: the microphone runs ~50 ppm
  *slow* (`observed_rate_hz` 383,980.8 against 384,000), so the deficit *grows*.
  Both say the same thing about the same crystal.

## The linearity failure is the interesting one, and it is not noise

Residual from the Theil–Sen line, per-minute medians, in milliseconds:

| minute | 0 | 8 | 20 | 30 | **42** | 50 | 64 |
|---|---|---|---|---|---|---|---|
| residual | −1.65 | −0.08 | +0.29 | +1.33 | **+3.16** | −0.96 | −2.59 |

It is a **single smooth hump**: rises monotonically from −1.65 ms, crosses zero
around minute 8, peaks at +3.16 ms at minute 42, then falls smoothly to
−2.59 ms by minute 64. Total excursion about **5.8 ms**.

That shape rules out several things by inspection:

- **Not a step.** A discrete loss event arrives as a step that stays up. This
  comes back down. The 0.663 ms "step" that failed the third check is the
  steepest single minute of the smooth descent (minute 43→44), not a
  discontinuity.
- **Not periodic at 300 s.** The retention sweep's signature would be 13 bumps
  across this window, not one.
- **Not measurement noise.** The per-minute medians are smooth to a few tens of
  microseconds against a 5.8 ms excursion, and the CI on the slope is ±0.85 ppm.

Differentiating the hump, the instantaneous rate varies from roughly **+1.6 ppm
above** the hour's mean to **4.4 ppm below** it — about **6 ppm of swing** inside
the hour, around a mean of 46.3 ppm.

## Leading hypothesis: the crystal is warming

**Stated as a hypothesis, because it was not measured.**

ADR-069 sets the 2 ppm agreement threshold explicitly so that it sits "above the
agreement actually achieved and below the thermal excursion", recording that the
crystal moved about **3 ppm under a thermal change inside one hour**. A smooth
single-humped ~6 ppm excursion is that phenomenon, larger.

The window ran **06:58–08:03 BST** — through sunrise and into morning warming.
SoC temperature at the end of the run was 44.4 °C.

**This was not tested.** Nothing recorded temperature during the window, and the
Pi keeps no temperature history, so the correlation cannot be recovered after the
fact. `scripts/measure_capture_drift.py` should sample
`/sys/class/thermal/thermal_zone0/temp` alongside each health poll; then the
hypothesis becomes a scatter plot instead of a story. That change is not made
here.

Both failing numeric checks are consistent with it: a rate that swings 6 ppm
inside an hour cannot be linear to 0.5 ms, and an hour whose mean sits 3.6 ppm
off a **72-hour cumulative average** is exactly what a diurnally varying crystal
produces. ADR-069 already notes these two quantities have different anchors —
"a slope against a cumulative average" — and that they are complementary rather
than corroborating.

## What the run does establish

ADR-069 states the purpose of this gate plainly: the failed soak left ~175 s of
unexplained deficit over 54.7 hours, **about 890 ppm if it were continuous**, and
an hour-long run at the 2 ppm criterion "rules out a continuous mechanism of that
size".

Measured: **46.3 ppm ± 0.85**, with a ~6 ppm thermal-looking wobble, and **zero
confirmed loss** across the window (`estimated_missing_frames`, `gaps_with_loss`,
`gaps_without_loss` and `overruns` all unchanged; one late read).

**A continuous 890 ppm mechanism is excluded by a factor of about nineteen.** The
question the gate was built to answer is answered. The gate still did not pass,
because passing requires all six checks, and that distinction is the whole reason
the criteria were written before the run.

## Proposal — not applied

The linearity and step thresholds (0.5 ms) were derived from ADR-046's three
clean segments, which measured 0.30, 0.25 and 0.44 ms. **Those segments were
22.2 minutes and shorter.** A thermal excursion with a period of hours is
approximately linear across 22 minutes and demonstrably is not across 65, so a
threshold calibrated on the short window may not be transferable to the long one
— which is the specific thing ADR-069 lengthened the window to expose.

That is an argument for a *new* ADR, decided on its own evidence, and it needs
the temperature series to be worth anything. Until then:

- **This run is recorded as a fail.** It is not re-scored.
- The next run should log SoC temperature per sample.
- If the hump tracks temperature, the honest fix is to fit drift over a window
  short enough to be linear while testing loss over the long one — two
  measurements, not one relaxed threshold.
- If it does **not** track temperature, there is an unexplained 6 ppm
  hour-periodic mechanism in the capture clock, and that is a finding worth
  chasing rather than accommodating.

## Artefacts

- `results/drift-2026-08-25.csv` — 1950 samples, 26 columns, untracked per `.gitignore`
- `results/drift-2026-08-25.log` — full JSON verdict including every threshold beside the measurement it judged

Re-analyse without re-sampling:

    python3 scripts/measure_capture_drift.py --analyse results/drift-2026-08-25.csv
