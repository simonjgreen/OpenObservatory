"""Sample the station's capture clock for an hour and decide whether the deficit is drift.

This is the measuring instrument for **the one-hour drift run on live capture** —
test (b) of the two tests ADR-069 separates. It reproduces the method ADR-046
used over 42.7 minutes, at the duration Milestone 1's exit gate actually asks
for, and it applies that ADR's pass criterion rather than inventing one after
the numbers are in.

    python scripts/measure_capture_drift.py --host <station-host> --seconds 3900 \
        --csv results/drift-<date>.csv --declare-load "refinement runner idle"

    python scripts/measure_capture_drift.py --analyse results/drift-<date>.csv

**What it measures.** `expected_frames - frames` is not lost audio: it is
sampling phase, plus crystal drift, plus anchor bias, plus loss (ADR-046). The
phase term is removable because the station publishes `block_age_s`, the age of
the *last block's start*:

    corrected = expected_frames - block_age_s x sample_rate - (frames - block_frames)

That took ADR-046's scatter from ~100 ms to 0.3 ms. The slope of the corrected
deficit against uptime is the crystal's rate, and it is compared against the
station's own independently-derived `rate_offset_ppm`.

**Four things this script is careful about, each because it went wrong before:**

1. **It holds one HTTP connection open.** A 13-minute run that opened a fresh
   `ssh ... curl` per sample was itself the load: it took the station from 5
   overruns in 2 h to 6 in 13 minutes and nothing from that window could be
   attributed (`OPEN_INVESTIGATION_CAPTURE_GAPS.md`, "Traps this round
   produced"). Sample from the laptop over a persistent connection, and read the
   journal afterwards rather than during.

2. **It fits with Theil-Sen over per-minute medians, never OLS.** A late read
   corrupts the phase correction for a sample or two (a block's
   `monotonic_start_ns` is read-completion minus one block duration, so a stalled
   read reports a start later than the true one). OLS read **6 ppm high** on
   ADR-046's own data; the median pairwise slope is immune.

3. **It refuses to fit across a restart.** Every counter resets when the unit
   restarts, and ADR-046 found the station had been restarted every ~18 minutes
   by concurrent deploys without anyone noticing. A change of `stream_id`, or
   `frames` going backwards, ends the segment. The result is reported per
   segment; a run whose longest restart-free segment is under the required
   duration has not met the gate, however long it ran in total.

4. **It declares load rather than assuming there is none.** `--declare-load` is
   recorded verbatim in the summary, and the sampled `loop_lag_max_s`,
   `late_reads` and `hot_path_cpu_ratio` are reported per segment so a reader can
   see contamination that was not declared. ADR-046's run was contaminated by
   another agent's two-core load probe and was salvaged only because the
   contamination was visible in these counters.

Interrupting with Ctrl-C is safe and expected: every sample is flushed to the
CSV as it is taken, and the summary is computed over whatever was collected.
Re-analysing a CSV later costs the station nothing.
"""

from __future__ import annotations

import argparse
import csv
import http.client
import json
import math
import signal
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

#: Columns written per sample. Raw counters first, derived quantities after, so
#: the file can be re-derived from itself if a derivation is later found wrong.
FIELDS = [
    "wall_utc",
    "monotonic_s",
    "stream_id",
    "sample_rate",
    "blocks",
    "frames",
    "expected_frames",
    "block_age_s",
    "block_frames",
    "uptime_s",
    "raw_deficit_frames",
    "corrected_deficit_frames",
    "phase_in_band",
    "estimated_missing_seconds",
    "estimated_missing_frames",
    "rate_offset_ppm",
    "continuity_ratio",
    "gaps_with_loss",
    "gaps_without_loss",
    "overruns",
    "late_reads",
    "late_read_max_frames",
    "loop_lag_max_s",
    "hot_path_cpu_ratio",
    "clock_reanchors",
    "stream_restarts",
]


class Sampler:
    """One keep-alive HTTP connection, reopened only if the station drops it."""

    def __init__(self, host: str, port: int = 8080, timeout: float = 10.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self._conn: http.client.HTTPConnection | None = None
        self.reconnects = 0

    def _connect(self) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        return self._conn

    def health(self) -> dict[str, Any]:
        for attempt in (1, 2):
            conn = self._connect()
            try:
                conn.request("GET", "/api/v1/health", headers={"Connection": "keep-alive"})
                response = conn.getresponse()
                body = response.read()
                if response.status != 200:
                    raise OSError(f"HTTP {response.status}")
                return json.loads(body)
            except Exception:
                self._conn = None
                if attempt == 2:
                    raise
                self.reconnects += 1
        raise AssertionError("unreachable")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def derive(snapshot: dict[str, Any], monotonic_s: float) -> dict[str, Any]:
    """One CSV row: the raw counters, plus the phase-corrected deficit."""
    capture = snapshot["capture"]
    rate = capture["sample_rate"]
    frames = capture["frames"]
    expected = capture["expected_frames"]
    blocks = capture.get("blocks") or 0
    # Derived rather than assumed: the block size is whatever this station
    # negotiated, and 100 ms at 384 kHz is a configuration, not a constant.
    block_frames = round(frames / blocks) if blocks else 0
    age = capture.get("block_age_s")
    age = float(age) if age is not None else float("nan")
    corrected = expected - age * rate - (frames - block_frames)
    block_s = block_frames / rate if rate else 0.0
    # `block_age_s` is measured to the block's START and the block is one
    # duration long, so it sweeps block_s -> 2*block_s in normal running, NOT
    # 0 -> block_s. A filter written for the wrong band silently discards half
    # the samples and biases the slope (OICG, "Two traps found while using it").
    in_band = bool(block_s and (0.9 * block_s) <= age <= (2.2 * block_s))
    return {
        "wall_utc": datetime.now(UTC).isoformat(),
        "monotonic_s": round(monotonic_s, 3),
        "stream_id": capture.get("stream_id"),
        "sample_rate": rate,
        "blocks": blocks,
        "frames": frames,
        "expected_frames": expected,
        "block_age_s": age,
        "block_frames": block_frames,
        "uptime_s": expected / rate if rate else 0.0,
        "raw_deficit_frames": expected - frames,
        "corrected_deficit_frames": corrected,
        "phase_in_band": int(in_band),
        "estimated_missing_seconds": capture.get("estimated_missing_seconds"),
        "estimated_missing_frames": capture.get("estimated_missing_frames"),
        "rate_offset_ppm": capture.get("rate_offset_ppm"),
        "continuity_ratio": capture.get("continuity_ratio"),
        "gaps_with_loss": capture.get("gaps_with_loss"),
        "gaps_without_loss": capture.get("gaps_without_loss"),
        "overruns": capture.get("overruns"),
        "late_reads": capture.get("late_reads"),
        "late_read_max_frames": capture.get("late_read_max_frames"),
        "loop_lag_max_s": capture.get("loop_lag_max_s"),
        "hot_path_cpu_ratio": capture.get("hot_path_cpu_ratio"),
        "clock_reanchors": capture.get("clock_reanchors"),
        "stream_restarts": capture.get("stream_restarts"),
    }


# --------------------------------------------------------------------------
# analysis


def theil_sen(x: np.ndarray, y: np.ndarray) -> float:
    """Median of pairwise slopes. Immune to the late-read excursions OLS is not."""
    if x.size < 2:
        return float("nan")
    i, j = np.triu_indices(x.size, k=1)
    dx = x[j] - x[i]
    keep = dx != 0
    return float(np.median((y[j] - y[i])[keep] / dx[keep]))


def bootstrap_ci(
    x: np.ndarray, y: np.ndarray, iterations: int = 1000, seed: int = 20260823
) -> tuple[float, float]:
    """95% percentile interval over resampled per-minute medians."""
    if x.size < 4:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    slopes = np.empty(iterations)
    for k in range(iterations):
        pick = rng.integers(0, x.size, x.size)
        slopes[k] = theil_sen(x[pick], y[pick])
    return (float(np.nanpercentile(slopes, 2.5)), float(np.nanpercentile(slopes, 97.5)))


@dataclass
class Segment:
    rows: list[dict[str, Any]]

    @property
    def stream_id(self) -> str:
        return str(self.rows[0]["stream_id"])

    @property
    def duration_s(self) -> float:
        return float(self.rows[-1]["monotonic_s"]) - float(self.rows[0]["monotonic_s"])


def split_segments(rows: list[dict[str, Any]]) -> list[Segment]:
    """A restart ends a segment: counters reset, so no fit may span one."""
    segments: list[Segment] = []
    current: list[dict[str, Any]] = []
    for row in rows:
        if current:
            previous = current[-1]
            restarted = (
                row["stream_id"] != previous["stream_id"]
                or float(row["frames"]) < float(previous["frames"])
                or float(row["clock_reanchors"] or 0) != float(previous["clock_reanchors"] or 0)
            )
            if restarted:
                segments.append(Segment(current))
                current = []
        current.append(row)
    if current:
        segments.append(Segment(current))
    return segments


def per_minute_medians(segment: Segment) -> tuple[np.ndarray, np.ndarray, int]:
    """Collapse to one point per minute, dropping out-of-band phase samples.

    Reported, never silent: the count of dropped samples comes back with the
    medians so the summary can state it.
    """
    used = [r for r in segment.rows if int(r["phase_in_band"]) == 1]
    dropped = len(segment.rows) - len(used)
    if not used:
        return np.zeros(0), np.zeros(0), dropped
    t0 = float(used[0]["monotonic_s"])
    buckets: dict[int, list[float]] = {}
    for row in used:
        minute = int((float(row["monotonic_s"]) - t0) // 60)
        buckets.setdefault(minute, []).append(float(row["corrected_deficit_frames"]))
    minutes = sorted(buckets)
    x = np.array([m * 60.0 + 30.0 for m in minutes])
    y = np.array([float(np.median(buckets[m])) for m in minutes])
    return x, y, dropped


def summarise(segment: Segment, min_seconds: float) -> dict[str, Any]:
    x, y, dropped = per_minute_medians(segment)
    rate = float(segment.rows[-1]["sample_rate"])
    slope_frames_per_s = theil_sen(x, y)
    low, high = bootstrap_ci(x, y)
    ppm = slope_frames_per_s / rate * 1e6 if rate else float("nan")
    ppm_ci = (low / rate * 1e6, high / rate * 1e6) if rate else (float("nan"), float("nan"))

    # Residual from the fitted line, per minute, in milliseconds. A real loss
    # arrives as a step that stays up; drift is a straight line.
    residual_ms: list[float] = []
    step_ms = 0.0
    if x.size >= 2 and not math.isnan(slope_frames_per_s):
        intercept = float(np.median(y - slope_frames_per_s * x))
        residual = y - (slope_frames_per_s * x + intercept)
        residual_ms = [float(v / rate * 1000.0) for v in residual]
        step_ms = float(np.max(np.abs(np.diff(residual))) / rate * 1000.0) if x.size > 2 else 0.0

    first, last = segment.rows[0], segment.rows[-1]
    station_ppm = float(last["rate_offset_ppm"] or 0.0)
    agreement = abs(abs(ppm) - abs(station_ppm)) if not math.isnan(ppm) else float("nan")

    def delta(key: str) -> float:
        return float(last[key] or 0) - float(first[key] or 0)

    checks = {
        "restart_free_for_required_duration": segment.duration_s >= min_seconds,
        # A slope fitted through a handful of points is not a measurement. Ten
        # per-minute medians is the floor; ADR-046's shortest quoted window had
        # ten and it was already the weakest of its three.
        "at_least_10_minute_points": int(x.size) >= 10,
        "slope_agrees_with_rate_offset_ppm_within_2": (
            not math.isnan(agreement) and agreement <= 2.0
        ),
        "max_residual_within_0.5ms": bool(residual_ms) and max(map(abs, residual_ms)) <= 0.5,
        "no_step_over_0.5ms": step_ms <= 0.5,
        "no_confirmed_loss": delta("estimated_missing_frames") == 0
        and delta("gaps_with_loss") == 0,
    }
    return {
        "stream_id": segment.stream_id,
        "samples": len(segment.rows),
        "samples_dropped_out_of_phase_band": dropped,
        "duration_s": round(segment.duration_s, 1),
        "duration_min": round(segment.duration_s / 60.0, 2),
        "minutes_fitted": int(x.size),
        "first_utc": first["wall_utc"],
        "last_utc": last["wall_utc"],
        "uptime_start_s": round(float(first["uptime_s"]), 1),
        "uptime_end_s": round(float(last["uptime_s"]), 1),
        "drift_ppm_theil_sen": round(ppm, 3),
        "drift_ppm_ci95": [round(ppm_ci[0], 3), round(ppm_ci[1], 3)],
        "station_rate_offset_ppm": station_ppm,
        "ppm_agreement": round(agreement, 3),
        "max_abs_residual_ms": round(max(map(abs, residual_ms)), 4) if residual_ms else None,
        "max_step_ms": round(step_ms, 4),
        "estimated_missing_seconds_delta": delta("estimated_missing_seconds"),
        "gaps_with_loss_delta": delta("gaps_with_loss"),
        "gaps_without_loss_delta": delta("gaps_without_loss"),
        "overruns_delta": delta("overruns"),
        "late_reads_delta": delta("late_reads"),
        "late_read_max_frames_end": last["late_read_max_frames"],
        "loop_lag_max_s_start": first["loop_lag_max_s"],
        "loop_lag_max_s_end": last["loop_lag_max_s"],
        "hot_path_cpu_ratio_end": last["hot_path_cpu_ratio"],
        "continuity_ratio_end": last["continuity_ratio"],
        "checks": checks,
        "passed": all(checks.values()),
    }


def report(rows: list[dict[str, Any]], declared_load: str, min_seconds: float) -> dict[str, Any]:
    segments = split_segments(rows)
    summaries = [summarise(s, min_seconds) for s in segments if len(s.rows) >= 4]
    longest = max((s["duration_s"] for s in summaries), default=0.0)
    return {
        "declared_load": declared_load,
        "samples": len(rows),
        "segments": len(segments),
        "longest_segment_s": longest,
        "longest_segment_min": round(longest / 60.0, 2),
        "required_restart_free_s": min_seconds,
        "gate_met": any(s["passed"] for s in summaries),
        "per_segment": summaries,
        "note": (
            "gate_met means: one restart-free segment of at least the required "
            "duration passed every check in ADR-069. Load contamination is "
            "declared above and visible in loop_lag/late_reads per segment; a "
            "reader must judge it, not this script."
        ),
    }


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", help="station host (no address is committed, ADR-047)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--seconds", type=float, default=3900.0, help="sampling duration; 65 min by default"
    )
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between samples")
    parser.add_argument("--csv", type=Path, help="where to write the raw series")
    parser.add_argument(
        "--min-restart-free-s",
        type=float,
        default=3600.0,
        help="the duration the gate requires in one unbroken segment",
    )
    parser.add_argument(
        "--declare-load",
        default="not declared",
        help="what else was running on the station during the window; recorded verbatim",
    )
    parser.add_argument("--analyse", type=Path, help="re-analyse an existing CSV and exit")
    args = parser.parse_args()

    if args.analyse:
        result = report(read_csv(args.analyse), args.declare_load, args.min_restart_free_s)
        print(json.dumps(result, indent=1))
        return

    if not args.host:
        parser.error("--host is required unless --analyse is given")

    sampler = Sampler(args.host, args.port)
    rows: list[dict[str, Any]] = []
    writer = None
    handle = None
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        handle = args.csv.open("w", newline="")
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()

    stopping = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    began = time.monotonic()
    failures = 0
    print(
        f"sampling {args.host}:{args.port}/api/v1/health every {args.interval}s "
        f"for {args.seconds}s; Ctrl-C is safe",
        file=sys.stderr,
    )
    try:
        while not stopping and (time.monotonic() - began) < args.seconds:
            tick = time.monotonic()
            try:
                row = derive(sampler.health(), tick - began)
            except Exception as exc:  # a dropped sample is data, not a crash
                failures += 1
                print(f"sample failed ({failures}): {exc}", file=sys.stderr)
            else:
                rows.append(row)
                if writer:
                    writer.writerow(row)
                    handle.flush()  # type: ignore[union-attr]
            sleep_for = args.interval - (time.monotonic() - tick)
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        sampler.close()
        if handle:
            handle.close()

    result = report(rows, args.declare_load, args.min_restart_free_s)
    result["interrupted"] = stopping
    result["failed_samples"] = failures
    result["http_reconnects"] = sampler.reconnects
    result["csv"] = str(args.csv) if args.csv else None
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
