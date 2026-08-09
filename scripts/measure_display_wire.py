"""Print the actual bytes each `/api/v1/display` frame costs on the wire.

ADR-038's budget is a measurement, not an aspiration, so it gets a command that
reproduces it. Run: `python scripts/measure_display_wire.py`.
"""

from __future__ import annotations

from open_observatory.display_channel import (
    DisplayFilter,
    collapse_runs,
    detection_frame,
    encode,
    hello_frame,
    status_frame,
    wire_item,
)

BIRD = {
    "event_start_utc": "2026-08-08T13:46:39.755370Z",
    "common_name": "Common Woodpigeon",
    "scientific_name": "Columba palumbus",
    "taxonomic_group": "bird",
    "score": 0.91,
    "peak_frequency_hz": 480.0,
    "detector": {"plugin_id": "birdnet-analyzer"},
}
BAT = {
    "event_start_utc": "2026-08-08T22:04:11Z",
    "common_name": None,
    "scientific_name": None,
    "taxonomic_group": "bat",
    "score": 0.31,
    "peak_frequency_hz": 36240.0,
    "detector": {"plugin_id": "ultrasonic-pass-v1"},
}


def main() -> None:
    filt = DisplayFilter()
    bird_item = wire_item(BIRD, filt)
    bat_item = wire_item(BAT, filt)
    assert bird_item is not None and bat_item is not None
    frames = [
        ("bird detection", detection_frame(bird_item)),
        ("bird detection + count", detection_frame(bird_item, species_today=14)),
        ("bat pass", detection_frame(bat_item)),
        ("heartbeat", status_frame(now=1786196799, state="L", detail="", species_today=14)),
        (
            "hello, 6 rows",
            hello_frame(
                now=1786196799,
                state="L",
                detail="",
                species_today=14,
                heartbeat_s=10,
                items=collapse_runs([bird_item] * 3 + [bat_item] * 3, 6),
            ),
        ),
    ]
    for name, frame in frames:
        payload = encode(frame)
        print(f"{name:24s} {len(payload.encode()):5d} B   {payload}")


if __name__ == "__main__":
    main()
