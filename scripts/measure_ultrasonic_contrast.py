"""Measure the real dBFS distribution of the ultrasonic spectrogram, before the
uint8 colour mapping, so a display floor/ceiling can be chosen from evidence
rather than picked by eye (ADR-041).

Connects to the live `/api/v1/live` WebSocket -- which is itself the thing
that keeps the encoders running under ADR-040's viewer gate, so this script
*is* the viewer for the duration of the sample -- reads binary spectrogram
frames for the ultrasonic channel, and inverts the encoder's own mapping
(`out_u8 = clip((db - floor) / (ceiling - floor), 0, 1) * 255`, see
`SpectrogramEncoder._column`) using whatever floor/ceiling the station
currently reports in its `hello` message, so the reconstructed dB values are
correct regardless of what the station is configured with when this runs.

Reports p1/p50/p95/p99 dBFS split by frequency: 15-45 kHz (where bats are
expected and the operator reported saturation) versus >=50 kHz (which looked
fine in the reported screenshot).

    python scripts/measure_ultrasonic_contrast.py --host <station-host> --seconds 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct

import numpy as np
import websockets

HEADER = struct.Struct("<BBHHHd")
FRAME_SPECTROGRAM = 1


async def sample(host: str, seconds: float) -> None:
    uri = f"ws://{host}:8080/api/v1/live"
    centre_freqs: np.ndarray | None = None
    floor_db = ceiling_db = None
    channel_index = None
    columns: list[np.ndarray] = []

    async with websockets.connect(uri, max_size=None) as ws:
        async for message in ws:
            if isinstance(message, str):
                payload = json.loads(message)
                if payload.get("type") == "hello":
                    for spec in payload["spectrograms"]:
                        if spec["name"] == "ultrasonic":
                            centre_freqs = np.array(spec["centre_frequencies"])
                            floor_db = spec["floor_db"]
                            ceiling_db = spec["ceiling_db"]
                            channel_index = spec["channel"]
                continue
            if channel_index is None:
                continue
            header = message[: HEADER.size]
            frame_type, channel, bins, ncols, _reserved, _first_utc = HEADER.unpack(header)
            if frame_type != FRAME_SPECTROGRAM or channel != channel_index:
                continue
            data = np.frombuffer(message[HEADER.size :], dtype=np.uint8).reshape(ncols, bins)
            columns.append(data)
            hop_s = 0.024  # matches spectrogram_hop_ms default; close enough to bound the sample
            if sum(c.shape[0] for c in columns) * hop_s >= seconds:
                break

    if centre_freqs is None or not columns:
        raise SystemExit(
            "No ultrasonic frames arrived. Is the station's native rate >=96 kHz, "
            "and did the viewer gate actually open (ADR-040)?"
        )

    span = ceiling_db - floor_db
    all_cols = np.concatenate(columns, axis=0)
    db = all_cols.astype(np.float64) / 255.0 * span + floor_db

    low_mask = (centre_freqs >= 15_000) & (centre_freqs < 45_000)
    high_mask = centre_freqs >= 50_000

    def report(label: str, mask: np.ndarray) -> None:
        values = db[:, mask].ravel()
        pcts = {p: float(np.percentile(values, p)) for p in (1, 50, 95, 99)}
        print(
            f"{label} ({int(mask.sum())} bins): "
            f"p1={pcts[1]:.1f}  p50={pcts[50]:.1f}  p95={pcts[95]:.1f}  p99={pcts[99]:.1f}  "
            f"max={float(values.max()):.1f} dBFS"
        )

    print(f"columns sampled: {all_cols.shape[0]} (~{all_cols.shape[0] * hop_s:.1f} s)")
    print(f"current encoder range: floor={floor_db} ceiling={ceiling_db}")
    report("15-45 kHz (bat band)", low_mask)
    report(">=50 kHz (quiet band)", high_mask)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        required=True,
        help="the station to measure (required: no station address is committed, ADR-042)",
    )
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args()
    asyncio.run(sample(args.host, args.seconds))


if __name__ == "__main__":
    main()
