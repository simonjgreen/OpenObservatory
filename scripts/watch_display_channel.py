"""Watch the counter-top display's own counters while something else happens.

ADR-038's channel and the debug UI's live channel share one process and one event
loop, and ADR-040's premise is that the display is the first-class surface. That
makes "a browser connecting must not cost the display anything" a property worth
an instrument rather than an assumption.

Prints one line per sample: connected displays, frames sent, frames dropped,
queue depth, and the inter-arrival time of the frames that did land, which is the
closest thing to latency this channel publishes.

    python scripts/watch_display_channel.py --seconds 60 --label "one browser"
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from typing import Any


def sample(host: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"http://{host}:8080/api/v1/station", timeout=10) as response:
        return json.load(response).get("display_channel", {})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host",
        required=True,
        help="the station to measure (required: no station address is committed, ADR-047)",
    )
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--label", default="window")
    args = parser.parse_args()

    began = time.monotonic()
    first = sample(args.host)
    previous = first
    previous_at = began
    gaps: list[float] = []
    while time.monotonic() - began < args.seconds:
        time.sleep(args.interval)
        now = sample(args.host)
        if now.get("per_client") and previous.get("per_client"):
            delta = now["per_client"][0]["sent"] - previous["per_client"][0]["sent"]
            elapsed = time.monotonic() - previous_at
            if delta:
                gaps.append(elapsed / delta)
        previous, previous_at = now, time.monotonic()

    last = sample(args.host)
    report = {
        "label": args.label,
        "seconds": round(time.monotonic() - began, 1),
        "clients": last.get("clients"),
        "sent": [c["sent"] for c in last.get("per_client", [])],
        "sent_delta": [
            b["sent"] - a["sent"]
            for a, b in zip(first.get("per_client", []), last.get("per_client", []), strict=False)
        ],
        "dropped": [c["dropped"] for c in last.get("per_client", [])],
        "queued": [c["queued"] for c in last.get("per_client", [])],
        "mean_frame_bytes": [c["mean_frame_bytes"] for c in last.get("per_client", [])],
        "mean_seconds_between_frames": round(sum(gaps) / len(gaps), 2) if gaps else None,
        "max_seconds_between_frames": round(max(gaps), 2) if gaps else None,
    }
    print(json.dumps(report))


if __name__ == "__main__":
    main()
