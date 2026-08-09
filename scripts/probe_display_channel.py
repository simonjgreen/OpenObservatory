"""Connect to a live station's `/api/v1/display` and report what it actually costs.

The point of ADR-038 is a measured byte count, so this is the command that
measures it against a real station over the real network rather than against a
test double. Run:

    python scripts/probe_display_channel.py <station-host> 60
"""

from __future__ import annotations

import json
import sys
import time
from urllib.request import urlopen

from websockets.sync.client import connect


def main() -> None:
    if len(sys.argv) < 2:
        # Required, deliberately: the repository ships no station address (ADR-047).
        print("usage: probe_display_channel.py <station-host> [seconds]", file=sys.stderr)
        raise SystemExit(2)
    host = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0
    url = f"ws://{host}:8080/api/v1/display?min_score=0.75&bats=true&rows=6"

    total = 0
    counts: dict[str, int] = {}
    bytes_by_type: dict[str, int] = {}
    started = time.monotonic()
    print(f"connecting to {url}")
    with connect(url) as socket:
        while time.monotonic() - started < seconds:
            try:
                raw = socket.recv(timeout=max(0.5, seconds - (time.monotonic() - started)))
            except TimeoutError:
                break
            size = len(raw.encode())
            total += size
            frame = json.loads(raw)
            kind = frame.get("t", "?")
            counts[kind] = counts.get(kind, 0) + 1
            bytes_by_type[kind] = bytes_by_type.get(kind, 0) + size
            print(f"  {size:5d} B  {raw}")

    elapsed = time.monotonic() - started
    print(f"\n{total} bytes in {elapsed:.1f} s = {total / elapsed:.1f} B/s")
    for kind in sorted(counts):
        mean = bytes_by_type[kind] / counts[kind]
        print(f"  {kind}: {counts[kind]} frames, {bytes_by_type[kind]} B, mean {mean:.1f} B")

    with urlopen(f"http://{host}:8080/api/v1/station", timeout=10) as response:
        snapshot = json.load(response)
    print("\nstation-side view:", json.dumps(snapshot.get("display_channel"), indent=2))


if __name__ == "__main__":
    main()
