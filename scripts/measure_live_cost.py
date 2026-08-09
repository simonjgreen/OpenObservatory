"""Sample `/api/v1/station` over a window and report the hot path's cost.

Written for the "what does an open browser actually cost?" experiment (ADR-040).
Every headline counter the station publishes is cumulative since capture opened,
so a single reading tells you about the whole uptime, not about the condition you
are currently holding. This takes two readings a window apart and differences
them, which is the only way to attribute cost to a condition.

    python scripts/measure_live_cost.py --host <station-host> --seconds 300 --label baseline

`hot_path_seconds` is not published directly, only as `hot_path_cpu_ratio`
against audio seconds, so it is reconstructed as ratio x frames / rate. The
ratio is rounded to four places, which over a window of a few hundred seconds
is a sub-percent error on the difference -- stated here rather than discovered
later.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from typing import Any


def _get(host: str, path: str) -> Any:
    with urllib.request.urlopen(f"http://{host}:8080{path}", timeout=10) as response:
        return json.load(response)


def fetch(host: str) -> dict[str, Any]:
    """Two reads, because the two numbers the experiment needs live apart: the
    live hub's socket count is only on `/api/v1/debug/pipeline`, and
    `display_channel` is only on `/api/v1/station`."""
    payload = _get(host, "/api/v1/debug/pipeline")
    snapshot = payload["station"]
    snapshot["live_hub"] = payload["live_hub"]
    snapshot["display_channel"] = _get(host, "/api/v1/station").get("display_channel", {})
    return snapshot


def hot_path_seconds(snapshot: dict[str, Any]) -> float:
    capture = snapshot["capture"]
    ratio = capture.get("hot_path_cpu_ratio") or 0.0
    audio_s = capture["frames"] / capture["sample_rate"]
    return ratio * audio_s


def summarise(first: dict[str, Any], last: dict[str, Any], wall_s: float) -> dict[str, Any]:
    a, b = first["capture"], last["capture"]
    audio_s = (b["frames"] - a["frames"]) / b["sample_rate"]
    hot_s = hot_path_seconds(last) - hot_path_seconds(first)
    display_a = first.get("display_channel", {}).get("per_client", [])
    display_b = last.get("display_channel", {}).get("per_client", [])
    return {
        "wall_s": round(wall_s, 1),
        "audio_s": round(audio_s, 1),
        "blocks": b["blocks"] - a["blocks"],
        "hot_path_cpu_ratio_window": round(hot_s / audio_s, 4) if audio_s else None,
        "hot_path_cpu_ratio_cumulative": b.get("hot_path_cpu_ratio"),
        "loop_lag_events": b["loop_lag_events"] - a["loop_lag_events"],
        "loop_lag_events_per_min": round(
            (b["loop_lag_events"] - a["loop_lag_events"]) / (wall_s / 60.0), 2
        ),
        "loop_lag_max_s_window": (
            b["loop_lag_max_s"] if b["loop_lag_max_s"] > a["loop_lag_max_s"] else None
        ),
        "loop_lag_max_s_cumulative": b["loop_lag_max_s"],
        "deficit_frames": (b["expected_frames"] - b["frames"])
        - (a["expected_frames"] - a["frames"]),
        "estimated_missing_frames": b["estimated_missing_frames"] - a["estimated_missing_frames"],
        "gaps_with_loss": b["gaps_with_loss"] - a["gaps_with_loss"],
        "gaps_without_loss": b.get("gaps_without_loss", 0) - a.get("gaps_without_loss", 0),
        "late_reads": (b.get("late_reads") or 0) - (a.get("late_reads") or 0),
        "late_read_max_frames": b.get("late_read_max_frames"),
        "overruns": b["overruns"] - a["overruns"],
        "rate_offset_ppm": b["rate_offset_ppm"],
        "block_age_s": b["block_age_s"],
        "live_sockets": last.get("live_hub", {}).get("sockets"),
        "live_hub_frames_sent": last.get("live_hub", {}).get("frames_sent", 0)
        - first.get("live_hub", {}).get("frames_sent", 0),
        "live_hub_dropped": last.get("live_hub", {}).get("dropped", 0)
        - first.get("live_hub", {}).get("dropped", 0),
        "display_clients": last.get("display_channel", {}).get("clients"),
        "display_sent": sum(c["sent"] for c in display_b) - sum(c["sent"] for c in display_a),
        "display_dropped": sum(c["dropped"] for c in display_b)
        - sum(c["dropped"] for c in display_a),
        "display_mean_frame_bytes": [c["mean_frame_bytes"] for c in display_b],
        "spectrogram_columns": [
            s["columns_emitted"] for s in last.get("spectrograms", [])
        ],
        "spectrogram_columns_delta": [
            y["columns_emitted"] - x["columns_emitted"]
            for x, y in zip(first.get("spectrograms", []), last.get("spectrograms", []), strict=False)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host",
        required=True,
        help="the station to measure (required: no station address is committed, ADR-047)",
    )
    parser.add_argument("--seconds", type=float, default=300.0)
    parser.add_argument("--label", default="window")
    parser.add_argument("--settle", type=float, default=15.0, help="ignore this many seconds first")
    args = parser.parse_args()

    if args.settle:
        time.sleep(args.settle)
    began = time.monotonic()
    first = fetch(args.host)
    time.sleep(args.seconds)
    last = fetch(args.host)
    result = summarise(first, last, time.monotonic() - began)
    result["label"] = args.label
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
