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
    extra_deficit_frames: float = 0.0,
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
            # `extra_deficit_frames` bends the deficit without touching `frames`,
            # which must stay a whole number of blocks for the phase filter to mean
            # anything. Added on the expected side instead: it simulates a station
            # whose deficit curves away from a straight line, which is all the
            # thermal tests need.
            "expected_frames": RATE * t + extra_deficit_frames,
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
    soc_temp_c: float | None = None,
    thermal_ppm_amplitude: float | None = None,
    thermal_temp_amplitude: float | None = None,
    **kwargs,
) -> list[dict]:
    """A sampled run, with the scheduling jitter a real poller has.

    The jitter matters: without it every sample lands at the same phase within
    the block and the raw deficit looks deceptively stable, which is the
    opposite of the artefact this whole method exists to remove.
    """
    import math
    import random

    rng = random.Random(seed)
    rows = []
    start_uptime = 120.0  # a real run starts at some uptime, not at zero
    elapsed = 0.0

    def hump(t: float) -> float:
        """0 at both ends, 1 in the middle, smooth throughout.

        The shape drift gate (b) actually produced on 2026-08-25: a single rise
        and fall across the hour, not a step and not a 300 s beat.
        """
        return math.sin(math.pi * min(max(t / seconds, 0.0), 1.0))

    while elapsed <= seconds:
        offset = rng.uniform(-jitter_s, jitter_s)
        monotonic = elapsed + offset
        extra = 0.0
        if thermal_ppm_amplitude:
            # A `thermal_ppm_amplitude` ppm excursion sustained for ten minutes,
            # which puts a 6 ppm swing at about 3.5 ms -- the size of the real
            # residual that failed the gate.
            extra = thermal_ppm_amplitude * 1e-6 * RATE * 600.0 * hump(elapsed)
        temp = soc_temp_c
        if thermal_temp_amplitude is not None:
            temp = 20.0 + thermal_temp_amplitude * hump(elapsed)
        rows.append(
            drift.derive(
                _snapshot(start_uptime + monotonic, ppm, extra_deficit_frames=extra, **kwargs),
                monotonic,
                temp,
            )
        )
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
    assert all(not s["checks"]["restart_free_for_required_duration"] for s in result["per_segment"])


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


# ---------------------------------------------------------------------------
# Temperature, added 2026-08-25 after drift gate (b) failed on linearity.
#
# The residual that failed the gate was a single smooth hump -- -1.65 ms at
# minute 0, +3.16 at minute 42, -2.59 at minute 64 -- which is what a crystal
# warming through sunrise looks like, and also what several other things look
# like. Nothing recorded temperature during that run and the Pi keeps no
# history, so the hypothesis could not be tested afterwards at any price.
#
# The station already publishes `oo_host_cpu_temperature_celsius` on /metrics,
# so this costs one more GET on the connection the sampler already holds open.
# It must never cost the run: temperature is diagnostic, it is not an input to
# any gate check, and a station that stops serving it must not fail a drift run
# that is otherwise valid.
# ---------------------------------------------------------------------------

METRICS = """# HELP oo_host_cpu_temperature_celsius SoC temperature
# TYPE oo_host_cpu_temperature_celsius gauge
oo_host_cpu_temperature_celsius 44.65
# HELP oo_mqtt_connect_attempts_total MQTT connection attempts
oo_mqtt_connect_attempts_total 1.0
"""


def test_temperature_is_read_from_the_metrics_the_station_already_serves() -> None:
    assert drift.parse_soc_temperature(METRICS) == pytest.approx(44.65)


def test_a_station_not_serving_a_temperature_yields_none_rather_than_raising() -> None:
    # The comment line alone must not satisfy the parse: a HELP line contains
    # the metric name and no value, and reading it as one would put the string
    # "SoC" where a temperature belongs.
    assert drift.parse_soc_temperature("# HELP oo_host_cpu_temperature_celsius SoC\n") is None
    assert drift.parse_soc_temperature("") is None
    assert drift.parse_soc_temperature("oo_host_cpu_temperature_celsius NaN\n") is None
    assert drift.parse_soc_temperature("oo_host_cpu_temperature_celsius wat\n") is None


def test_a_missing_temperature_does_not_change_a_single_gate_verdict() -> None:
    """The whole point of it being diagnostic.

    Same run, once with temperatures and once without. Every check, the slope
    and the residual must be identical -- temperature informs the reader, never
    the verdict.
    """
    without = drift.report(_series(50.0), "none", 3600.0)
    with_temp = drift.report(_series(50.0, soc_temp_c=41.0), "none", 3600.0)

    a, b = without["per_segment"][0], with_temp["per_segment"][0]
    assert a["checks"] == b["checks"]
    assert a["passed"] == b["passed"]
    assert a["drift_ppm_theil_sen"] == pytest.approx(b["drift_ppm_theil_sen"])
    assert a["max_abs_residual_ms"] == pytest.approx(b["max_abs_residual_ms"])


def test_a_run_with_no_temperature_reports_none_not_zero() -> None:
    """Zero degrees is a reading. Absent is not, and the two must not collapse."""
    result = drift.report(_series(50.0), "none", 3600.0)
    segment = result["per_segment"][0]
    assert segment["soc_temp_c_start"] is None
    assert segment["soc_temp_c_end"] is None
    assert segment["residual_vs_temperature_r"] is None


def test_a_residual_that_tracks_temperature_is_reported_as_correlated() -> None:
    """The measurement that 2026-08-25's run could not make.

    A station whose deficit bends with a temperature ramp must come back with a
    correlation near 1, so "thermal" stops being a story and becomes a number.
    """
    rows = _series(50.0, thermal_ppm_amplitude=6.0, thermal_temp_amplitude=5.0)
    segment = drift.report(rows, "none", 3600.0)["per_segment"][0]
    assert segment["residual_vs_temperature_r"] is not None
    assert abs(segment["residual_vs_temperature_r"]) > 0.9


def test_a_residual_that_ignores_temperature_is_not_reported_as_correlated() -> None:
    """The other outcome, which would mean a real unexplained mechanism.

    Temperature swings; the crystal does not follow it. The correlation must
    come back weak rather than being manufactured by the ramp's own shape.
    """
    rows = _series(50.0, thermal_ppm_amplitude=0.0, thermal_temp_amplitude=5.0)
    segment = drift.report(rows, "none", 3600.0)["per_segment"][0]
    assert segment["residual_vs_temperature_r"] is not None
    assert abs(segment["residual_vs_temperature_r"]) < 0.5


def test_a_steady_temperature_declines_to_correlate_rather_than_inventing_a_number() -> None:
    """Found by checking the numbers rather than the assertion.

    With the station held at one temperature there is no bend to match, so the
    honest answer is None. The first implementation guarded on `std() == 0` and
    returned r = 0.03 here: removing a straight line from a constant leaves
    floating-point dust, and `corrcoef` correlates the deficit against the dust.
    A small r is not "no relationship" to a reader -- it is a measurement.
    """
    rows = _series(50.0, thermal_ppm_amplitude=6.0, soc_temp_c=41.0)
    segment = drift.report(rows, "none", 3600.0)["per_segment"][0]
    assert segment["soc_temp_c_min"] == 41.0
    assert segment["soc_temp_c_max"] == 41.0
    assert segment["residual_vs_temperature_r"] is None
