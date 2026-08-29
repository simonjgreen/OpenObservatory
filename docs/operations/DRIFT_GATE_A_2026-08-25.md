# Drift gate (a), 2026-08-25 — **PASSED** on the target device

The synthetic resampler run defined in [[ADR-069]], at full duration on the Pi 5 for
the first time.

Kept separate from [[DRIFT_GATE_B_2026-08-25]] deliberately. [[ADR-069]] exists
because "the one-hour drift run" had been used to mean two different tests, and
recording their results in one file is how they would be conflated again.

## The run

    oo audio resample-check --source-rate 384000 --target-rate 48000 \
        --seconds 3600 --block-ms 100 --json

Run over ssh on the station, `/usr/bin/time -v` around it. Exit status **0**.

## Result

| property | rule | measured | |
|---|---|---|---|
| group delay | `abs(delay) <= 1.0` output frame | **0.0** frames (0.0 ms) | PASS |
| delivery deficit trend | `abs(trend) < (max − min) + 8` → **820** | **−0.044** | PASS |
| seam continuity | `worst_step < median_step × 25` | **1.527×** | PASS |
| tone identity | peak within 1.465 Hz of 1000 Hz | **999.756 Hz** (0.244 Hz error) | PASS |

`"failures": []`, `"passed": true`.

Backend `soxr 1.1.0 quality=HQ`, ratio 1/8, 36,000 blocks of 38,400 frames,
1,382,400,000 input frames → 172,799,176 output against 172,800,000 expected.
Deficit band 112–924 frames, exactly the range [[ADR-069]]'s threshold was derived
from, so the adaptive limit did not move under this run.

## Why this could not be run until today

The gate needs `--seconds 3600`, and the version deployed on the station
accumulated the whole run in memory — peak RSS around **2,800 MB** at an hour,
on a machine that is also holding a 384 kHz capture ring. Running it would have
risked the microphone, which is the one thing that outranks it.

The streaming rewrite landed in `debba2d` on 2026-08-23 and could not be
deployed while the 72-hour soak was running. It went out on 2026-08-25 once the
soak completed, and this run followed immediately:

| | before | after |
|---|---|---|
| peak RSS at `--seconds 3600` | ~2,800 MB | **124 MB** |
| wall clock | — | 47.3 s |

Measured with `/usr/bin/time -v` on the Pi: `Maximum resident set size: 124,332
kB`. The figure predicted from the laptop was 137.8 MB, so the target device came
in slightly under.

## What this result is not

[[ADR-069]] says it plainly and it is repeated here because the two gates are easy
to confuse:

> `AudibleResampler` drives conversion from an exact `Fraction(48000, 384000)`,
> so arithmetic drift is impossible by construction; (a) tests the backend and
> the block-boundary state machine, not the ratio. It is a regression test on
> libsoxr and on our streaming of it. **It is not, and must not be quoted as,
> evidence about the station's clock.**

The station's clock is gate (b)'s subject, and gate (b) did not pass.

One honest weakness, restated from [[ADR-069]] rather than left to be rediscovered:
the trend threshold is **adaptive** — derived from the run's own deficit band —
so a backend whose band widened would widen its own limit too. A future libsoxr
or backend change must re-derive [[ADR-069]]'s detectability table rather than
assume 820 still holds.

## Reproduce

    ssh <station-host> 'cd open-observatory && /usr/bin/time -v ./.venv/bin/oo \
        audio resample-check --source-rate 384000 --target-rate 48000 \
        --seconds 3600 --block-ms 100 --json'

Exit status is the verdict. Nothing is written to the database and capture is
untouched — the run is synthetic and does not read the microphone.
