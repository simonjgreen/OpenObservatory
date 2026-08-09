"""Time the two live-view spectrogram encoders, per block, on whatever machine
this runs on.

The station publishes `hot_path_cpu_ratio` -- hot-path seconds per second of
audio -- for the whole per-block path at once, so it cannot say how much of that
the spectrograms are. This can: it drives the same two encoders the station
builds, with the same settings, at the same block size, and reports the same
unit. Run it on the Pi. Numbers from a laptop are not evidence about the target.

    ./.venv/bin/python scripts/bench_spectrogram.py

The signal is noise rather than recorded audio deliberately: the encoder's cost
is an FFT plus a max-reduction over fixed-size arrays, which does not branch on
content, and noise is reproducible from a seed on any machine.
"""

from __future__ import annotations

import argparse
import time

import numpy as np

from open_observatory.audio.spectrogram import SpectrogramEncoder
from open_observatory.config import Settings

NATIVE_RATE = 384000
AUDIBLE_RATE = 48000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--block-ms", type=float, default=100.0)
    parser.add_argument("--audio-s", type=float, default=60.0, help="audio seconds per encoder")
    args = parser.parse_args()

    settings = Settings()
    block_s = args.block_ms / 1000.0
    rng = np.random.default_rng(7)
    native = (rng.standard_normal(int(NATIVE_RATE * block_s)) * 3000).astype(np.int16)
    audible = (rng.standard_normal(int(AUDIBLE_RATE * block_s)) * 3000).astype(np.int16)

    encoders = [
        (
            "audible",
            SpectrogramEncoder(
                channel=0,
                name="audible",
                sample_rate=AUDIBLE_RATE,
                fft_size=settings.spectrogram_fft,
                hop_ms=settings.spectrogram_hop_ms,
                bins=settings.spectrogram_bins,
                min_hz=settings.spectrogram_min_hz,
                max_hz=settings.spectrogram_max_hz,
                floor_db=settings.spectrogram_floor_db,
                ceiling_db=settings.spectrogram_ceiling_db,
                history_columns=settings.spectrogram_history_columns,
            ),
            audible,
        ),
        (
            "ultrasonic",
            SpectrogramEncoder(
                channel=1,
                name="ultrasonic",
                sample_rate=NATIVE_RATE,
                # Literals, matching `Station._build_spectrograms`: the
                # ultrasonic channel's shape is not settings-driven there.
                fft_size=4096,
                hop_ms=settings.spectrogram_hop_ms,
                bins=128,
                min_hz=15_000.0,
                max_hz=min(150_000.0, NATIVE_RATE / 2 * 0.98),
                floor_db=-105.0,
                ceiling_db=-25.0,
                history_columns=settings.spectrogram_history_columns,
            ),
            native,
        ),
    ]

    blocks = max(1, int(args.audio_s / block_s))
    total_ratio = 0.0
    for label, encoder, pcm in encoders:
        for i in range(20):  # warm up: first calls allocate the history deque
            encoder.push(pcm, i * len(pcm), 0.0)
        began = time.perf_counter()
        for i in range(blocks):
            encoder.push(pcm, (20 + i) * len(pcm), 0.0)
        elapsed = time.perf_counter() - began
        ratio = elapsed / (blocks * block_s)
        total_ratio += ratio
        print(
            f"{label:11s} {elapsed / blocks * 1000:7.3f} ms/block  "
            f"cpu_ratio={ratio:.4f}  ({elapsed:.2f}s of CPU for {blocks * block_s:.0f}s of audio)"
        )
    print(f"{'both':11s} {'':7s}  cpu_ratio={total_ratio:.4f}")


if __name__ == "__main__":
    main()
