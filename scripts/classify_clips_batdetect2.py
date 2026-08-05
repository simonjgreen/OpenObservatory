#!/usr/bin/env python3
"""Classify stored evidence clips with BatDetect2, after the fact.

This is the cheap-trigger / expensive-classifier cascade in its simplest form.
`ultrasonic-pass-v1` runs live at ~36-40x realtime and decides *when* something
happened; BatDetect2 runs at 0.52x realtime and cannot follow a live stream, but
it does not have to — it only ever sees the few seconds of audio a pass detector
already flagged. A night of 400 passes at ~1 s of useful audio each is about
7 minutes of classifier work, not 8 hours.

Nothing here is a species claim by the station. It reports what BatDetect2 says
next to what the pass detector measured, so a human can compare them. Per
ADR-017 BatDetect2 is evaluated, not adopted, and this script does not write to
the database.

Usage on the Pi:

    .venv/bin/python scripts/classify_clips_batdetect2.py --min-hz 32000 --max-hz 37000
    .venv/bin/python scripts/classify_clips_batdetect2.py --limit 20 --json out.json
"""

from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

TARGET_SAMPLE_RATE_HZ = 256_000


def _die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def find_clips(
    db_path: Path, min_hz: float, max_hz: float, limit: int, since: str | None = None
) -> list[tuple[str, float, float, str]]:
    """Return (storage_uri, peak_hz, score, event_start_utc) for native clips."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        """
        SELECT m.storage_uri, d.peak_frequency_hz, d.score, d.event_start_utc
        FROM detection d
        JOIN detection_media dm ON dm.detection_id = d.id
        JOIN media_asset m ON m.id = dm.media_asset_id
        WHERE d.taxonomic_group = 'bat'
          AND m.kind = 'evidence_native'
          AND d.peak_frequency_hz BETWEEN ? AND ?
          AND (? IS NULL OR d.event_start_utc >= ?)
        ORDER BY d.event_start_utc DESC
        LIMIT ?
        """,
        (min_hz, max_hz, since, since, limit),
    ).fetchall()
    conn.close()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="SQLite path (default: data/*.sqlite)")
    parser.add_argument("--min-hz", type=float, default=0.0)
    parser.add_argument("--max-hz", type=float, default=200_000.0)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--since", default=None, help="only detections at/after this UTC ISO time")
    parser.add_argument(
        "--trim-s",
        type=float,
        default=0.0,
        help=(
            "classify only this many seconds centred on the loudest part of the clip. "
            "An evidence clip is mostly pre/post roll: the pass itself is a fraction of "
            "a second, so trimming is where the cascade's cost actually goes."
        ),
    )
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--json", dest="json_path", default=None)
    args = parser.parse_args()

    try:
        import torch
    except ImportError:
        _die("torch is not installed. See docs/detectors/BATDETECT2_EVALUATION.md")
    try:
        from batdetect2 import api
    except ImportError:
        _die("batdetect2 is not installed. See docs/detectors/BATDETECT2_EVALUATION.md")

    import soundfile as sf

    from open_observatory.audio.resample import AudibleResampler

    torch.set_num_threads(args.threads)

    db_path = Path(args.db) if args.db else None
    if db_path is None:
        candidates = glob.glob(str(REPO_ROOT / "data" / "*.sqlite"))
        if not candidates:
            _die("no database found under data/")
        db_path = Path(candidates[0])

    rows = find_clips(db_path, args.min_hz, args.max_hz, args.limit, args.since)
    if not rows:
        _die(f"no native bat clips with peak between {args.min_hz} and {args.max_hz} Hz")

    model, _params = api.load_model()
    config = api.get_config()
    device = torch.device("cpu")

    print(f"{len(rows)} clip(s), peak {args.min_hz:.0f}-{args.max_hz:.0f} Hz, {args.threads} threads")
    print(f"{'when':>9}  {'our peak':>9}  {'score':>5}  BatDetect2 top species (det_prob)")
    print("-" * 78)

    results = []
    total_audio_s = 0.0
    total_infer_s = 0.0

    for storage_uri, peak_hz, score, when in rows:
        path = Path(storage_uri)
        if not path.is_absolute():
            path = REPO_ROOT / storage_uri
        if not path.exists():
            print(f"{when[11:19]:>9}  {peak_hz:9.0f}  {score:5.2f}  (clip missing on disk)")
            continue

        pcm, native_rate = sf.read(str(path), dtype="float32", always_2d=False)
        if pcm.ndim > 1:
            pcm = pcm.mean(axis=1).astype("float32")
        if args.trim_s > 0.0 and pcm.shape[0] > int(args.trim_s * native_rate):
            # Centre on the loudest sample: the pass, not the pre-roll silence.
            width = int(args.trim_s * native_rate)
            centre = int(abs(pcm).argmax())
            start = max(0, min(centre - width // 2, pcm.shape[0] - width))
            pcm = pcm[start : start + width]
        duration_s = pcm.shape[0] / native_rate
        if native_rate != TARGET_SAMPLE_RATE_HZ:
            pcm = AudibleResampler(native_rate, TARGET_SAMPLE_RATE_HZ).process(pcm).pcm

        started = time.perf_counter()
        predictions, _f, _s = api.process_audio(
            pcm, samp_rate=TARGET_SAMPLE_RATE_HZ, model=model, config=config, device=device
        )
        elapsed = time.perf_counter() - started
        total_audio_s += duration_s
        total_infer_s += elapsed

        ranked = sorted(predictions, key=lambda p: p.get("det_prob", 0.0), reverse=True)
        # Collapse to distinct species, keeping each one's best call, since a pass
        # contains many calls and the raw list is dominated by repeats.
        best_by_species: dict[str, float] = {}
        for pred in ranked:
            name = pred.get("class", "?")
            best_by_species[name] = max(best_by_species.get(name, 0.0), pred.get("det_prob", 0.0))
        top = sorted(best_by_species.items(), key=lambda kv: kv[1], reverse=True)[:3]
        summary = ", ".join(f"{name} {prob:.2f}" for name, prob in top) or "no calls found"
        print(f"{when[11:19]:>9}  {peak_hz:9.0f}  {score:5.2f}  {summary}")

        results.append(
            {
                "event_start_utc": when,
                "our_peak_hz": peak_hz,
                "our_score": score,
                "clip": str(path),
                "clip_duration_s": round(duration_s, 3),
                "inference_s": round(elapsed, 3),
                "batdetect2": [{"species": n, "det_prob": round(p, 3)} for n, p in top],
            }
        )

    if total_audio_s:
        print("-" * 78)
        factor = total_audio_s / total_infer_s if total_infer_s else 0.0
        print(
            f"{total_audio_s:.1f}s of clip audio classified in {total_infer_s:.1f}s "
            f"({factor:.2f}x realtime)"
        )
        print(
            "Cascade cost: this is the whole per-night classifier budget, because only "
            "clips a live detector already flagged are ever classified."
        )

    if args.json_path:
        Path(args.json_path).write_text(json.dumps(results, indent=2))
        print(f"Results written to {args.json_path}")


if __name__ == "__main__":
    main()
