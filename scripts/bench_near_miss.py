"""Measure what the ADR-052 near-miss ledger costs on the detector hot path.

Charter item 1: capture always wins, and this project has already been bitten
by a "small" addition -- a 10 s retention sweep that cost ~1.9 capture
gaps/minute (ADR-033). So the cost of this one is measured rather than
asserted.

Two figures, because they answer different questions:

* **per candidate** -- the marginal cost of recording one rejection or one
  admission, which is what scales with a noisy site;
* **per window** -- the same cost against BirdNET's own measured inference
  time (72.4 ms p95 on the live Pi, 2026-08-09), which is what decides whether
  the detector can still keep up with a 1.5 s stride.

Run:

    PYTHONPATH=src ./.venv/bin/python scripts/bench_near_miss.py
"""

from __future__ import annotations

import statistics
import time

from open_observatory.detectors.near_miss import NearMissLedger

#: Measured on the live station (`/metrics`, 2026-08-09): BirdNET p95 analysis
#: runtime per 3 s window, at a 1.5 s stride.
BIRDNET_P95_MS = 72.36
STRIDE_S = 1.5


def _time(fn, iterations: int, repeats: int = 7) -> float:
    """Best-of-N nanoseconds per call. Best rather than mean: the thing being
    measured is a few hundred nanoseconds, so scheduler noise only ever adds."""
    samples = []
    for _ in range(repeats):
        start = time.perf_counter_ns()
        fn(iterations)
        samples.append((time.perf_counter_ns() - start) / iterations)
    return min(samples)


def bench_rejections(ledger: NearMissLedger, iterations: int) -> None:
    for i in range(iterations):
        ledger.record_rejected(
            at_ns=1_700_000_000_000_000_000 + i,
            label_index=i % 60,
            common_name="European Robin",
            scientific_name="Erithacus rubecula",
            score=0.12 + (i % 80) / 100.0,
            occurrence=0.41,
            band="in_range" if i % 3 else "implausible",
            threshold=0.55,
        )


def bench_admissions(ledger: NearMissLedger, iterations: int) -> None:
    for i in range(iterations):
        ledger.record_admitted(band="in_range", label_index=i % 60, score=0.8)


def main() -> None:
    iterations = 200_000

    ledger = NearMissLedger(capacity=200)
    reject_ns = _time(lambda n: bench_rejections(ledger, n), iterations)

    ledger_hist_only = NearMissLedger(capacity=0)
    reject_hist_ns = _time(lambda n: bench_rejections(ledger_hist_only, n), iterations)

    ledger_admit = NearMissLedger(capacity=200)
    admit_ns = _time(lambda n: bench_admissions(ledger_admit, n), iterations)

    snap_ledger = NearMissLedger(capacity=200)
    bench_rejections(snap_ledger, 5_000)
    snapshot_samples = []
    for _ in range(20):
        start = time.perf_counter_ns()
        snap_ledger.snapshot(thresholds={"in_range": 0.55})
        snapshot_samples.append((time.perf_counter_ns() - start) / 1e6)
    snapshot_ms = statistics.median(snapshot_samples)

    print(f"record_rejected  (ring + histogram + species) : {reject_ns:8.0f} ns/candidate")
    print(f"record_rejected  (histogram + species only)   : {reject_hist_ns:8.0f} ns/candidate")
    print(f"record_admitted                               : {admit_ns:8.0f} ns/candidate")
    print(f"snapshot() (API read, off the hot path)       : {snapshot_ms:8.3f} ms")
    print()
    for candidates in (5, 10, 30):
        per_window_us = candidates * reject_ns / 1000.0
        print(
            f"{candidates:3d} rejected candidates/window: "
            f"{per_window_us:7.1f} us/window "
            f"= {100.0 * per_window_us / (BIRDNET_P95_MS * 1000.0):.4f}% of BirdNET's "
            f"{BIRDNET_P95_MS} ms, "
            f"= {per_window_us / 1e6 / STRIDE_S:.3e} CPU-s per second of audio"
        )


if __name__ == "__main__":
    main()
