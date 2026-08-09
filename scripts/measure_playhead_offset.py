"""Ground-truth harness for the spectrogram playhead marker (ADR-051).

Answers "is the marker where it says it is?" with a number rather than an
argument, and does it without touching the station.

It stands in for ``GET /api/v1/live/audio.wav`` exactly where it matters: a
44-byte endless-header WAV, then continuous 16-bit mono PCM at 48 kHz in 40 ms
chunks paced in real time, with the queue drained at connect so media time 0 is
the audio that was live when the element opened the stream.

The difference from the station is that this server *knows the answer*. It
records the wall-clock instant it wrote every chunk, so for any media time the
browser reports it can say exactly when that audio was live. The page posts its
readings back and imports the real ``web/src/components/playhead.ts`` -- bundled,
not a copy of the arithmetic -- so what is under test is the shipped estimator.

Usage::

    cd web && npx esbuild src/components/playhead.ts --bundle --format=esm \
        --outfile=/tmp/oo-playhead/playhead.js
    python3 scripts/measure_playhead_offset.py            # serves on :8899
    chromium --headless=new --mute-audio --disable-audio-output \
        --autoplay-policy=no-user-gesture-required \
        --user-data-dir=<a scratch profile> http://127.0.0.1:8899/

**Pass --mute-audio and --disable-audio-output.** Headless Chromium on Linux
still opens the default output device, and this server streams a tone: without
those flags the rig plays out loud through whoever's speakers are attached. It
cost the operator a startled minute during ADR-051's own measurement. Muting
changes nothing that is measured -- ``currentTime`` still advances and
``buffered`` still fills.

Results land in ``samples.jsonl`` next to this file, one JSON object per
250 ms sample: the browser's telemetry, the estimate the shipped code produced
from it, and this server's record of when that audio was actually live.

Optional positional arguments ``<drop_after> <drop_count>`` shed chunks the way
the station's bounded per-listener queue does, to exercise the case where the
two estimators part company.
"""

from __future__ import annotations

import json
import math
import os
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SAMPLE_RATE = 48000
CHUNK_MS = 40.0
CHUNK_FRAMES = int(SAMPLE_RATE * CHUNK_MS / 1000)
HERE = Path(__file__).parent
LOG = Path(os.environ.get("OO_PLAYHEAD_DIR", HERE)) / "samples.jsonl"

# chunk index -> wall clock at which it was written to the socket.
written: dict[int, float] = {}
written_lock = threading.Lock()
# Chunks the server deliberately never sends, to reproduce the station's
# oldest-first shedding under back-pressure.
drop_after = float("inf")
drop_count = 0


def wav_header(sample_rate: int) -> bytes:
    """The same endless header `api/app.py`'s `_wav_stream_header` writes."""
    return (
        b"RIFF"
        + struct.pack("<I", 0xFFFFFFFF)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
        + b"data"
        + struct.pack("<I", 0xFFFFFFFF)
    )


def chunk_pcm(index: int) -> bytes:
    """A tone whose frequency identifies the chunk, so the content is not
    silence and the decoder has something real to do."""
    hz = 300 + (index % 12) * 50
    out = bytearray()
    for frame in range(CHUNK_FRAMES):
        t = (index * CHUNK_FRAMES + frame) / SAMPLE_RATE
        out += struct.pack("<h", int(6000 * math.sin(2 * math.pi * hz * t)))
    return bytes(out)


PAGE = """<!doctype html><meta charset=utf-8><title>playhead probe</title>
<body><audio id=a></audio><pre id=out>starting</pre>
<script type="module">
import { estimatePlayhead } from './playhead.js'

const audio = document.getElementById('a')
const openedEpochS = Date.now() / 1000
audio.src = '/audio.wav'
audio.play().catch((e) => { document.getElementById('out').textContent = 'play failed: ' + e })

let lastCurrentTimeS = 0
setInterval(async () => {
  let bufferedAheadS = 0
  if (audio.buffered.length > 0) {
    bufferedAheadS = Math.max(0, audio.buffered.end(audio.buffered.length - 1) - audio.currentTime)
  }
  const currentTimeS = audio.currentTime
  const telemetry = {
    bufferedAheadS,
    currentTimeS,
    streamOpenedEpochS: openedEpochS,
    sampledEpochS: Date.now() / 1000,
    sampledPerfMs: performance.now(),
    paused: audio.paused,
    readyState: audio.readyState,
    advancing: currentTimeS > lastCurrentTimeS,
  }
  lastCurrentTimeS = currentTimeS
  // Exactly the production estimator, bundled from the real module.
  const estimate = estimatePlayhead(telemetry, 0)
  document.getElementById('out').textContent = JSON.stringify({ telemetry, estimate }, null, 1)
  await fetch('/report', {
    method: 'POST',
    body: JSON.stringify({ telemetry, estimate, bufferedRanges: audio.buffered.length }),
  })
}, 250)
</script>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/playhead.js":
            body = (LOG.parent / "playhead.js").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/audio.wav"):
            self.stream_audio()
            return
        self.send_error(404)

    def stream_audio(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Live-Sample-Rate", str(SAMPLE_RATE))
        self.end_headers()
        self.wfile.write(wav_header(SAMPLE_RATE))
        self.wfile.flush()
        started = time.time()
        sent = 0
        for index in range(20_000):
            # Real-time pacing: the station's broadcaster publishes one chunk
            # per 40 ms of captured audio and no faster.
            due = started + index * CHUNK_MS / 1000
            delay = due - time.time()
            if delay > 0:
                time.sleep(delay)
            if index >= drop_after and (index - drop_after) < drop_count:
                # Shed it, as the bounded per-listener queue would. The media
                # timeline gets shorter than real time by exactly this much.
                continue
            with written_lock:
                # The audio in this chunk is live *now*, by construction.
                written[sent] = time.time()
            try:
                self.wfile.write(chunk_pcm(index))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            sent += 1

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        now = time.time()
        estimate = payload.get("estimate")
        telemetry = payload["telemetry"]
        record = {"now": now, "telemetry": telemetry, "estimate": estimate}
        if estimate:
            # Ground truth: the sample at `currentTime` sits in the chunk this
            # many chunks into the stream, and the server wrote that chunk at a
            # known instant.
            index = int(telemetry["currentTimeS"] // (CHUNK_MS / 1000))
            with written_lock:
                truth = written.get(index)
                newest_index = max(written) if written else None
                newest_written = written.get(newest_index) if newest_index is not None else None
            if truth is not None:
                record["truthUtcS"] = truth
                record["errorS"] = estimate["utcS"] - truth
                record["trueLagS"] = now - truth
                record["claimedLagS"] = now - estimate["utcS"]
            if newest_written is not None:
                # What the spectrogram's live edge would be showing: the newest
                # audio the station has produced.
                record["liveEdgeUtcS"] = newest_written
        with LOG.open("a") as handle:
            handle.write(json.dumps(record) + "\n")
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 2:
        drop_after = int(sys.argv[1])
        drop_count = int(sys.argv[2])
    LOG.unlink(missing_ok=True)
    ThreadingHTTPServer(("127.0.0.1", 8899), Handler).serve_forever()
