# Drift gate (b), third attempt, 2026-08-29 evening — **did not pass, and the thermal explanation is now refuted**

The run [[ADR-075 - Window rate, not stream age|ADR-075]] required before any
threshold could move: gate (b) over a **falling** temperature, where both earlier
attempts had covered the same morning warming ramp. It failed, on the same two
checks as before — but it failed differently, and what it eliminated is worth more
than a pass would have been.

Window `2026-08-29T21:01:12Z → 22:06:10Z`, 64.98 minutes, one restart-free segment,
1,950 samples at 2 s, 65 per-minute medians fitted, 0 dropped out of the phase band.
Declared load: the live station only. The gate (c) synthetic station
([[DRIFT_GATE_C_2026-08-29]]) had been stopped ~15 minutes before the window opened.

## Result

| check ([[ADR-069 - Two drift gates\|ADR-069]], as amended by [[ADR-075 - Window rate, not stream age\|ADR-075]]) | required | measured | |
|---|---|---|---|
| restart-free duration | ≥ 3600 s | 3898.5 s | ✅ |
| per-minute points | ≥ 10 | 65 | ✅ |
| slope vs **window** frame rate | ≤ 2 ppm | **0.794 ppm** | ✅ |
| linearity (max abs residual) | ≤ 0.5 ms | **3.7796 ms** | ❌ |
| worst step | ≤ 0.5 ms | **0.9827 ms** | ❌ |
| confirmed loss in window | 0 | 0 | ✅ |

Drift 52.307 ppm (95% CI 51.661–52.874), station `rate_offset_ppm` −50.74, window
frame rate −51.513. Continuity 0.999837 at the end of the window; two late reads,
one excursion of 69,738 frames (182 ms) at minute 5, absorbed by the estimator
without crediting loss — [[ADR-039 - Confirmed loss, not deficit|ADR-039]] working
as designed.

## What this run establishes

**1. Temperature is not the mechanism.** SoC fell **60.05 → 44.65 °C**, a 15.4 °C
drop and a larger excursion than either morning attempt, and the residual got
*worse*, not better: 3.78 ms against 3.147 ms and 2.743 ms. The correlation flipped
sign and collapsed — `residual_vs_temperature_r` **−0.2194**, against **+0.693** and
+0.706 on the warming ramps. A mechanism that tracked temperature would not do that.
The thermal reading in [[DRIFT_GATE_B_2026-08-25]] was a reasonable inference from
two runs that happened to share a ramp; three runs kill it.

**2. The 0.5 ms bar is defensible — it is not asking for impossible precision.**
This was worth checking before blaming the clock, because a bar below the
instrument's own noise floor can never be met. It is not: the corrected deficit has
a within-minute standard deviation of about **1.00 ms** across ~30 samples a minute,
so a per-minute median carries a standard error of about **0.182 ms**, and the
largest of 65 such medians should sit near **0.50 ms** from a straight line by
chance alone. That is the bar [[ADR-069 - Two drift gates|ADR-069]] chose in
advance, which turns out to be about right. The measured 3.78 ms is **7.5×** that
floor, so the gate is failing on real structure rather than on sampling noise.

**3. [[ADR-075 - Window rate, not stream age|ADR-075]]'s fix works in the field.**
The slope-agreement check passed at 0.794 ppm against the window's own frame rate.
This was its first live use.

## What is now open, stated honestly

The residual is real, is not thermal, is not sampler noise, and is not a single late
read — the one excursion this window contained was absorbed cleanly and is not where
the residual lives. Nothing here identifies what it is.

**Do not move the bar.** [[ADR-075 - Window rate, not stream age|ADR-075]] allows a
temperature-corrected residual only if the falling-temperature run still failed *and*
temperature explained the residual. The first condition is met and the second is
refuted, so the coefficient it contemplated has no basis and must not be adopted.
[[ADR-070 - Threshold retune is not a defect|ADR-070]] applies with full force here:
a threshold that moves after three failures is not a measurement.

The next question is what has ~4 ms of structure over an hour that neither
temperature nor the sampler explains. Candidates worth a run each, cheapest first:
a periodicity search on the residual series (the CSV is committed, so this costs
nothing but analysis); the same window sampled at a shorter interval, to see whether
the structure survives a different sampling cadence; and a window with the
refinement timer and unattended-upgrade window deliberately excluded, since both are
known periodic loads ([[ADR-045 - Refinement runner|ADR-045]],
[[ADR-067 - Unattended package work|ADR-067]]).

## Artefacts

`results/drift-2026-08-29-evening.csv` (1,698 samples) and
`results/drift-2026-08-29-evening.log` (the sampler's own JSON verdict). Neither
records the station's address, per [[ADR-047 - The repository ships no site|ADR-047]].
