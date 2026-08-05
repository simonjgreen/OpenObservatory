#!/usr/bin/env python3
"""BatDetect2 evaluation harness — Milestone 5 (ADR-017).

Measures whether BatDetect2 1.3.1 is viable as a real-time detector on the
target device (Raspberry Pi 5, shared with capture, BirdNET and the
ultrasonic pass detector) or whether it belongs behind the deferred-queue
path that ADR-017 already reserves for it.

This script does **not** decide the answer. It measures p50/p95/max
inference time, a realtime factor, RSS before/after model load, and what the
model actually detected on labelled audio — and prints a verdict grounded in
those numbers. ADR-017 is explicit: "A benchmark, not an expectation,
decides."

Nothing this script touches is committed to the repository. BatDetect2's
code, weights and example recordings are all CC-BY-NC-4.0 (ADR-006/ADR-017);
acquiring them is a deliberate operator step, not something a checkout
carries.

Usage (on the Pi, after installing the optional dependencies — see
docs/detectors/BATDETECT2_EVALUATION.md):

    python scripts/benchmark_batdetect2.py
    python scripts/benchmark_batdetect2.py --audio /path/to/clips --threads 2
    python scripts/benchmark_batdetect2.py --json results.json

The model expects 256 kHz mono. This station's native stream is 384 kHz — a
1.5x ratio, not an integer decimation — so any resampling here goes through
the project's own soxr-backed ``AudibleResampler`` rather than BatDetect2's
internal librosa resampling path. That is why this script calls
``batdetect2.api.process_audio`` (which accepts pre-resampled audio) instead
of ``process_file`` (which would resample internally and silently bypass the
path this project actually uses in production).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path and SRC_DIR.is_dir():
    # Allow running this script straight from a checkout that hasn't been
    # `pip install -e`'d, matching how other one-off scripts on the Pi run.
    sys.path.insert(0, str(SRC_DIR))

TARGET_SAMPLE_RATE_HZ = 256_000

#: BatDetect2's three shipped UK example recordings, by the species code in
#: their filename. Used only to report a pass/fail-looking comparison in the
#: console output — the pytest fixture in tests/test_batdetect2.py is the
#: actual exit gate, not this script.
EXPECTED_SPECIES_BY_CODE = {
    "MYOMYS": "Myotis myotis",
    "EPTSER": "Eptesicus serotinus",
    "RHIFER": "Rhinolophus ferrumequinum",
}

#: Comparison figures this project already measured for the two detectors
#: BatDetect2 would run alongside (see docs/detectors/DETECTOR_STRATEGY.md
#: and the fixture benchmarks for birdnet/ultrasonic). Not claims about
#: BatDetect2 — just the bar the operator judges these numbers against.
COMPARISON_DETECTORS = {
    "BirdNET": {"p95_ms": (77, 109), "realtime_factor": "~40x"},
    "ultrasonic pass detector": {"p95_ms": (54, 104), "realtime_factor": "~36-40x"},
}


def _fail(message: str) -> None:
    """Exit with an actionable message. Never a traceback: ADR-006/017 treat
    a missing optional adapter as an expected outcome, not a bug."""
    print(f"\nBatDetect2 benchmark cannot run: {message}\n", file=sys.stderr)
    sys.exit(1)


@dataclass
class ClipResult:
    path: str
    expected_species: str | None
    native_sample_rate_hz: int
    duration_s: float
    resample_backend: str
    runtimes_ms: list[float]
    p50_ms: float
    p95_ms: float
    max_ms: float
    realtime_factor_p50: float
    realtime_factor_p95: float
    top_detections: list[dict[str, object]] = field(default_factory=list)
    matched_expected_species: bool | None = None


def _rss_mb() -> float:
    import psutil

    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def _find_example_audio() -> Path | None:
    """Locate BatDetect2's own labelled examples, if the operator fetched them.

    Checked in order:
    1. ``OO_BATDETECT2_EXAMPLE_AUDIO`` env var (a directory).
    2. The conventional local path this project uses for fetched-but-not-
       committed assets: ``models/batdetect2/example_data/audio``.
    3. Inside the installed ``batdetect2`` package itself, in case a future
       release vendors its example data (1.3.1's PyPI wheel does not).
    """
    env_dir = os.environ.get("OO_BATDETECT2_EXAMPLE_AUDIO")
    if env_dir and Path(env_dir).is_dir():
        return Path(env_dir)

    local = REPO_ROOT / "models" / "batdetect2" / "example_data" / "audio"
    if local.is_dir() and (any(local.glob("*.wav")) or any(local.glob("*.WAV"))):
        return local

    try:
        import batdetect2

        packaged = Path(batdetect2.__file__).resolve().parent.parent / "example_data" / "audio"
        if packaged.is_dir() and (any(packaged.glob("*.wav")) or any(packaged.glob("*.WAV"))):
            return packaged
    except Exception:
        pass

    return None


def _collect_audio_files(audio_arg: str | None) -> list[Path]:
    if audio_arg is not None:
        path = Path(audio_arg)
        if path.is_file():
            return [path]
        if path.is_dir():
            files = sorted(list(path.glob("*.wav")) + list(path.glob("*.WAV")))
            if not files:
                _fail(f"no .wav files found under {path}")
            return files
        _fail(f"--audio path does not exist: {path}")

    example_dir = _find_example_audio()
    if example_dir is None:
        _fail(
            "no audio given and BatDetect2's example recordings are not present locally.\n"
            "  ADR-017: they are CC-BY-NC-4.0, same as the model weights, and are never "
            "committed to this repository.\n"
            "  Fetch them yourself (they ship in the batdetect2 GitHub repo, not the PyPI "
            "wheel):\n"
            "    git clone --depth 1 --branch v1.3.1 "
            "https://github.com/macaodha/batdetect2 /tmp/batdetect2-src\n"
            "    mkdir -p models/batdetect2\n"
            "    cp -r /tmp/batdetect2-src/example_data models/batdetect2/example_data\n"
            "  or point at any audio of your own with --audio PATH."
        )
    files = sorted(list(example_dir.glob("*.wav")) + list(example_dir.glob("*.WAV")))
    if not files:
        _fail(f"no .wav files found under {example_dir}")
    return files


def _expected_species_for(path: Path) -> str | None:
    name = path.name.upper()
    for code, species in EXPECTED_SPECIES_BY_CODE.items():
        if code in name:
            return species
    return None


def _load_and_resample(path: Path) -> tuple[np.ndarray, int, float, str]:
    """Read audio and, if needed, resample it to 256 kHz via this project's
    soxr-backed resampler rather than BatDetect2's internal librosa path."""
    import soundfile as sf

    pcm, native_rate = sf.read(str(path), dtype="float32", always_2d=False)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1).astype(np.float32)  # downmix, matching the station's mono capture
    duration_s = pcm.shape[0] / native_rate

    if native_rate == TARGET_SAMPLE_RATE_HZ:
        return pcm.astype(np.float32), native_rate, duration_s, "passthrough (already 256 kHz)"

    from open_observatory.audio.resample import AudibleResampler

    resampler = AudibleResampler(native_rate, TARGET_SAMPLE_RATE_HZ)
    block = resampler.process(pcm)
    backend = resampler.backend_detail or resampler.backend
    return block.pcm.astype(np.float32), native_rate, duration_s, backend


def _time_inference(
    api, model, config, device, audio: np.ndarray, runs: int
) -> tuple[list[float], list[dict]]:
    runtimes_ms: list[float] = []
    last_predictions: list[dict] = []
    for _ in range(runs):
        began = time.perf_counter()
        predictions, _features, _spec = api.process_audio(
            audio, samp_rate=TARGET_SAMPLE_RATE_HZ, model=model, config=config, device=device
        )
        runtimes_ms.append((time.perf_counter() - began) * 1000.0)
        last_predictions = predictions
    return runtimes_ms, last_predictions


def _percentiles(values: list[float]) -> tuple[float, float, float]:
    arr = np.asarray(values, dtype=np.float64)
    return (
        float(np.percentile(arr, 50)),
        float(np.percentile(arr, 95)),
        float(np.max(arr)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure BatDetect2 1.3.1 inference viability on this device.",
    )
    parser.add_argument(
        "--audio",
        default=None,
        help="A .wav file or a directory of .wav files. Defaults to BatDetect2's own "
        "labelled example recordings if fetched locally (see docs/detectors/"
        "BATDETECT2_EVALUATION.md); never auto-downloads them.",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=2,
        help="Torch CPU threads to use (default: 2, matching BirdNET's default so the two "
        "can coexist on a 4-core Pi 5 without either starving the other or capture). "
        "Torch defaults to all cores if left unset, which is not the operating condition "
        "this station runs under.",
    )
    parser.add_argument(
        "--runs", type=int, default=20, help="Timed inference runs per clip (default: 20)."
    )
    parser.add_argument(
        "--detection-threshold",
        type=float,
        default=None,
        help="Override BatDetect2's default detection_threshold (0.01). Leave unset to use "
        "the library default.",
    )
    parser.add_argument("--json", default=None, help="Write full results as JSON to this path.")
    args = parser.parse_args()

    if args.threads < 1:
        _fail("--threads must be at least 1")
    if args.runs < 2:
        _fail("--runs must be at least 2 (one warm-up run plus at least one timed run)")

    # Must happen before torch is imported: these env vars are read at import
    # time by the BLAS/OpenMP backends torch links against.
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[var] = str(args.threads)

    audio_files = _collect_audio_files(args.audio)

    rss_before_mb = _rss_mb()

    try:
        import torch
    except ImportError:
        _fail(
            "PyTorch is not installed. BatDetect2 requires torch, torchaudio and "
            "torchvision. See docs/detectors/BATDETECT2_EVALUATION.md for the install "
            "command (aarch64/cp312 manylinux wheels exist, ~1-1.5 GB installed)."
        )
        return  # unreachable, keeps type-checkers happy

    try:
        torch.set_num_threads(args.threads)
    except RuntimeError as exc:
        print(f"warning: could not set torch thread count: {exc}", file=sys.stderr)

    try:
        from batdetect2 import api
    except ImportError:
        _fail(
            "the 'batdetect2' package is not installed. Install it with:\n"
            "    pip install batdetect2==1.3.1\n"
            "See docs/detectors/BATDETECT2_EVALUATION.md for the full procedure and the "
            "CC-BY-NC-4.0 licence terms that come with it."
        )
        return

    print(f"BatDetect2 benchmark — threads={args.threads} (explicit; not torch's all-core default)")
    print(f"Host: {platform.platform()}, Python {platform.python_version()}")
    print(f"RSS before model load: {rss_before_mb:.1f} MB")

    load_began = time.perf_counter()
    try:
        model, params = api.load_model()
    except (OSError, FileNotFoundError, RuntimeError) as exc:
        _fail(
            f"model weights failed to load ({exc}). BatDetect2 1.3.1 ships its checkpoint "
            "inside the pip package itself — a reinstall (`pip install --force-reinstall "
            "batdetect2==1.3.1`) is the usual fix if this is a corrupted wheel."
        )
        return
    load_s = time.perf_counter() - load_began
    rss_after_mb = _rss_mb()

    device = torch.device("cpu")
    config = api.get_config(detection_threshold=args.detection_threshold) if (
        args.detection_threshold is not None
    ) else api.get_config()

    print(
        f"Model loaded in {load_s:.2f}s: {params.get('model_name')}, "
        f"{len(params.get('class_names', []))} classes"
    )
    print(f"RSS after model load: {rss_after_mb:.1f} MB (+{rss_after_mb - rss_before_mb:.1f} MB)")
    print()

    # A single global warm-up: first-call overhead (thread-pool spin-up, lazy
    # kernel selection) is real but not representative of steady-state
    # inference, and must not be counted in the reported statistics.
    warmup_audio, _, _, _ = _load_and_resample(audio_files[0])
    api.process_audio(
        warmup_audio,
        samp_rate=TARGET_SAMPLE_RATE_HZ,
        model=model,
        config=config,
        device=device,
    )

    clip_results: list[ClipResult] = []
    for path in audio_files:
        expected = _expected_species_for(path)
        audio, native_rate, duration_s, backend = _load_and_resample(path)
        runtimes_ms, predictions = _time_inference(api, model, config, device, audio, args.runs)
        p50, p95, worst = _percentiles(runtimes_ms)
        rtf_p50 = duration_s * 1000.0 / p50 if p50 > 0 else float("inf")
        rtf_p95 = duration_s * 1000.0 / p95 if p95 > 0 else float("inf")

        ranked = sorted(predictions, key=lambda p: p.get("det_prob", 0.0), reverse=True)
        top = [
            {
                "species": p.get("class"),
                "det_prob": round(float(p.get("det_prob", 0.0)), 4),
                "class_prob": round(float(p.get("class_prob", 0.0)), 4),
                "start_time_s": round(float(p.get("start_time", 0.0)), 4),
                "end_time_s": round(float(p.get("end_time", 0.0)), 4),
                "low_freq_hz": p.get("low_freq"),
                "high_freq_hz": p.get("high_freq"),
            }
            for p in ranked[:3]
        ]
        matched = None
        if expected is not None:
            matched = bool(ranked) and ranked[0].get("class") == expected

        result = ClipResult(
            path=str(path),
            expected_species=expected,
            native_sample_rate_hz=native_rate,
            duration_s=round(duration_s, 4),
            resample_backend=backend,
            runtimes_ms=[round(v, 3) for v in runtimes_ms],
            p50_ms=round(p50, 3),
            p95_ms=round(p95, 3),
            max_ms=round(worst, 3),
            realtime_factor_p50=round(rtf_p50, 2),
            realtime_factor_p95=round(rtf_p95, 2),
            top_detections=top,
            matched_expected_species=matched,
        )
        clip_results.append(result)

        print(f"{path.name}  ({native_rate} Hz native -> {backend}, {duration_s:.3f}s)")
        print(
            f"  inference: p50={p50:.1f}ms p95={p95:.1f}ms max={worst:.1f}ms  "
            f"realtime factor (p50/p95): {rtf_p50:.1f}x / {rtf_p95:.1f}x"
        )
        if expected is not None:
            verdict = "MATCH" if matched else "no match"
            print(
                f"  expected species: {expected}  -> top detection: "
                f"{ranked[0].get('class') if ranked else 'none'} [{verdict}]"
            )
        for det in top:
            print(
                f"    {det['species']}: det_prob={det['det_prob']:.3f} class_prob={det['class_prob']:.3f} "
                f"[{det['start_time_s']:.2f}-{det['end_time_s']:.2f}s, "
                f"{det['low_freq_hz']}-{det['high_freq_hz']} Hz]"
            )
        print()

    all_runtimes = [v for r in clip_results for v in r.runtimes_ms]
    agg_p50, agg_p95, agg_max = _percentiles(all_runtimes)
    mean_duration_s = statistics.fmean(r.duration_s for r in clip_results)
    agg_rtf_p50 = mean_duration_s * 1000.0 / agg_p50 if agg_p50 > 0 else float("inf")
    agg_rtf_p95 = mean_duration_s * 1000.0 / agg_p95 if agg_p95 > 0 else float("inf")

    print("=" * 72)
    print(f"Aggregate over {len(clip_results)} clip(s), {args.runs} runs each (warm-up excluded):")
    print(f"  p50={agg_p50:.1f}ms  p95={agg_p95:.1f}ms  max={agg_max:.1f}ms")
    print(f"  realtime factor: {agg_rtf_p50:.1f}x (p50) / {agg_rtf_p95:.1f}x (p95)")
    print(f"  model load time: {load_s:.2f}s (excluded from inference stats)")
    print(f"  RSS: {rss_before_mb:.1f} MB before load -> {rss_after_mb:.1f} MB after "
          f"(+{rss_after_mb - rss_before_mb:.1f} MB)")
    print()
    print("For comparison, this project's other detectors measured:")
    for name, figures in COMPARISON_DETECTORS.items():
        lo, hi = figures["p95_ms"]
        print(f"  {name}: p95 {lo}-{hi} ms, {figures['realtime_factor']} realtime")
    print()

    # Verdict: grounded in headroom, not just "keeps up". Three detectors plus
    # capture will contend for the same cores, so treat anything under ~3x
    # realtime at p95 as too tight to call sustainable, per ADR-017's
    # instruction that a benchmark decides rather than an expectation.
    if agg_rtf_p95 >= 3.0:
        verdict = "SUSTAINABLE"
        reasoning = (
            f"p95 realtime factor {agg_rtf_p95:.1f}x leaves headroom to run alongside "
            "BirdNET and the ultrasonic pass detector on shared cores."
        )
    elif agg_rtf_p95 >= 1.0:
        verdict = "MARGINAL"
        reasoning = (
            f"p95 realtime factor {agg_rtf_p95:.1f}x keeps up on its own but leaves little "
            "headroom once BirdNET and the ultrasonic detector are also running; expect "
            "queueing under load."
        )
    else:
        verdict = "NOT SUSTAINABLE"
        reasoning = (
            f"p95 realtime factor {agg_rtf_p95:.1f}x means inference is slower than the "
            "audio it analyses, even in isolation."
        )
    print(f"VERDICT: {verdict} for real-time inference alongside the existing detectors.")
    print(f"  {reasoning}")
    if verdict != "SUSTAINABLE":
        print("  Per ADR-017, use the deferred-queue path rather than the live detector path.")
    print("=" * 72)

    mismatches = [r for r in clip_results if r.matched_expected_species is False]
    if mismatches:
        print(
            f"\nWARNING: {len(mismatches)} clip(s) did not match their expected species. "
            "A fast wrong answer is not a pass — see per-clip detail above."
        )

    if args.json:
        payload = {
            "generated_at_unix": time.time(),
            "host": platform.platform(),
            "python_version": platform.python_version(),
            "torch_version": torch.__version__,
            "batdetect2_model_name": params.get("model_name"),
            "batdetect2_class_count": len(params.get("class_names", [])),
            "threads": args.threads,
            "runs_per_clip": args.runs,
            "model_load_s": round(load_s, 4),
            "rss_before_load_mb": round(rss_before_mb, 2),
            "rss_after_load_mb": round(rss_after_mb, 2),
            "aggregate": {
                "p50_ms": round(agg_p50, 3),
                "p95_ms": round(agg_p95, 3),
                "max_ms": round(agg_max, 3),
                "realtime_factor_p50": round(agg_rtf_p50, 2),
                "realtime_factor_p95": round(agg_rtf_p95, 2),
            },
            "comparison_detectors": COMPARISON_DETECTORS,
            "verdict": verdict,
            "verdict_reasoning": reasoning,
            "clips": [asdict(r) for r in clip_results],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nResults written to {args.json}")


if __name__ == "__main__":
    main()
