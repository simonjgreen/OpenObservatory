"""The drift sampler's arithmetic, against a station whose drift is known.

`scripts/measure_capture_drift.py` is the instrument for the live one-hour drift
run (ADR-069 test (b)). An instrument that has never been checked against a known
input is not evidence, and this project has been bitten by exactly that: four
counters were confidently reporting wrong numbers before anyone cross-checked
them against ground truth.

So here the station is simulated with a crystal running a *chosen* number of ppm
slow, and the script has to recover that number, refuse to fit across a restart,
and notice loss when there is loss.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "measure_capture_drift",
    Path(__file__).resolve().parents[1] / "scripts" / "measure_capture_drift.py",
)
assert _SPEC and _SPEC.loader
drift = importlib.util.module_from_spec(_SPEC)
sys.modules["measure_capture_drift"] = drift
_SPEC.loader.exec_module(drift)

RATE = 384_000
BLOCK_FRAMES = 38_400


def _snapshot(
    t: float,
    ppm: float,
    *,
    stream_id: str = "stream-a",
    missing_frames: int = 0,
    gaps_with_loss: int = 0,
) -> dict:
    """A station whose crystal is `ppm` slow, sampled at uptime `t`.

    The block duration is one block of frames at the *actual* delivered rate, so
    `block_age_s` sweeps one block duration to two exactly as the real station's
    does -- which is the band the phase filter has to be written for.
    """
    actual_rate = RATE * (1.0 - ppm * 1e-6)
    duration = BLOCK_FRAMES / actual_rate
    blocks = max(1, int(t // duration))
    frames = blocks * BLOCK_FRAMES
    block_age = t - (blocks - 1) * duration
    return {
        "capture": {
            "stream_id": stream_id,
            "sample_rate": RATE,
            "blocks": blocks,
            "frames": frames,
            "expected_frames": RATE * t,
            "block_age_s": block_age,
            "estimated_missing_seconds": missing_frames / RATE,
            "estimated_missing_frames": missing_frames,
            "rate_offset_ppm": -ppm,
            "continuity_ratio": 0.99995,
            "gaps_with_loss": gaps_with_loss,
            "gaps_without_loss": 0,
            "overruns": 0,
            "late_reads": 3,
            "late_read_max_frames": 60_000,
            "loop_lag_max_s": 0.21,
            "hot_path_cpu_ratio": 0.0177,
            "clock_reanchors": 0,
            "stream_restarts": 0,
        }
    }


def _series(
    ppm: float,
    seconds: float = 3700.0,
    interval: float = 2.0,
    jitter_s: float = 0.05,
    seed: int = 7,
    **kwargs,
) -> list[dict]:
    """A sampled run, with the scheduling jitter a real poller has.

    The jitter matters: without it every sample lands at the same phase within
    the block and the raw deficit looks deceptively stable, which is the
    opposite of the artefact this whole method exists to remove.
    """
    import random

    rng = random.Random(seed)
    rows = []
    start_uptime = 120.0  # a real run starts at some uptime, not at zero
    elapsed = 0.0
    while elapsed <= seconds:
        offset = rng.uniform(-jitter_s, jitter_s)
        monotonic = elapsed + offset
        rows.append(drift.derive(_snapshot(start_uptime + monotonic, ppm, **kwargs), monotonic))
        elapsed += interval
    return rows


def test_recovers_a_known_drift_rate() -> None:
    """A 50 ppm slow crystal must come back as 50 ppm, with a tight interval."""
    rows = _series(50.0)
    result = drift.report(rows, "none", 3600.0)
    segment = result["per_segment"][0]

    assert segment["drift_ppm_theil_sen"] == pytest.approx(50.0, abs=0.5)
    low, high = segment["drift_ppm_ci95"]
    # The interval must be tight and must bracket the estimate. It is not
    # asserted to bracket 50.0 exactly: a crystal p ppm slow delivers a deficit
    # growing at p/(1-p) ppm, so the true slope here is 50.0025, and pretending
    # otherwise would be tuning the test to a rounding.
    assert low <= segment["drift_ppm_theil_sen"] <= high
    assert (high - low) < 1.0
    assert segment["ppm_agreement"] <= 2.0
    assert segment["checks"]["slope_agrees_with_rate_offset_ppm_within_2"]
    assert result["gate_met"]


def test_phase_correction_removes_the_sampling_artefact() -> None:
    """The corrected deficit must be far quieter than the raw one.

    ADR-046 measured that as ~100 ms of scatter becoming 0.3 ms. The raw deficit
    sawtooths across a whole block while nothing is wrong, which is the single
    most misread number on this station.
    """
    import numpy as np

    rows = _series(50.0, seconds=600.0)
    t = np.array([float(r["monotonic_s"]) for r in rows])
    raw = np.array([float(r["raw_deficit_frames"]) for r in rows])
    corrected = np.array([float(r["corrected_deficit_frames"]) for r in rows])

    def scatter(series: np.ndarray) -> float:
        """Spread about the series' own trend, in frames.

        Detrended deliberately: both series carry the crystal's real growth, and
        comparing raw range against corrected range would be comparing the
        artefact against the artefact plus 11,520 frames of genuine drift.
        """
        slope = drift.theil_sen(t, series)
        residual = series - (slope * t + np.median(series - slope * t))
        return float(residual.max() - residual.min())

    raw_scatter, corrected_scatter = scatter(raw), scatter(corrected)
    assert raw_scatter > BLOCK_FRAMES * 0.5, raw_scatter
    assert corrected_scatter < raw_scatter / 100, (raw_scatter, corrected_scatter)
    assert all(r["phase_in_band"] == 1 for r in rows)


def test_a_restart_splits_the_run_and_no_fit_spans_it() -> None:
    """Counters reset on restart; a fit across one is meaningless.

    ADR-046 found the station being restarted every ~18 minutes by concurrent
    deploys, which is why no long window existed to measure.
    """
    first = _series(50.0, seconds=1800.0)
    second = _series(50.0, seconds=1800.0, stream_id="stream-b")
    for row in second:
        row["monotonic_s"] = float(row["monotonic_s"]) + 1802.0
    result = drift.report(first + second, "none", 3600.0)

    assert result["segments"] == 2
    assert result["longest_segment_s"] < 3600.0
    # Each half is internally clean, but neither reaches the required duration,
    # so the gate is not met however long the run was in total.
    assert not result["gate_met"]
    assert all(
        not s["checks"]["restart_free_for_required_duration"] for s in result["per_segment"]
    )


def test_confirmed_loss_fails_the_run() -> None:
    """`estimated_missing_frames` moving is real loss, and must fail the gate."""
    rows = _series(50.0)
    for row in rows[len(rows) // 2 :]:
        row["estimated_missing_frames"] = 19_200
        row["gaps_with_loss"] = 1
    result = drift.report(rows, "none", 3600.0)
    segment = result["per_segment"][0]
    assert not segment["checks"]["no_confirmed_loss"]
    assert not segment["passed"]
    assert not result["gate_met"]


def test_out_of_band_phase_samples_are_dropped_and_counted_not_hidden() -> None:
    """A corrupted phase sample is excluded, and the exclusion is reported.

    The trap this guards is the opposite one: a filter written for 0 -> block
    rather than block -> 2*block silently discards half the samples and biases
    the slope. Here the band is correct, so only the genuinely bad sample goes.
    """
    rows = _series(50.0, seconds=900.0)
    rows[10]["block_age_s"] = 0.0005  # a nonsense reading
    rows[10]["phase_in_band"] = 0
    result = drift.report(rows, "none", 600.0)
    segment = result["per_segment"][0]
    assert segment["samples_dropped_out_of_phase_band"] == 1
    assert segment["samples"] == len(rows)


def test_theil_sen_is_not_dragged_by_a_late_read_excursion() -> None:
    """OLS read 6 ppm high on ADR-046's data; the median pairwise slope did not."""
    import numpy as np

    x = np.arange(60, dtype=float) * 60.0
    y = 0.02 * x  # a clean line
    y[30] -= 500.0  # one late-read excursion that recovers
    ols = float(np.polyfit(x, y, 1)[0])
    sen = drift.theil_sen(x, y)
    assert sen == pytest.approx(0.02, rel=1e-9)
    assert abs(ols - 0.02) > abs(sen - 0.02)
