# Live transport for the debug UI

The API specification only commits to SSE for detection updates. A real-time
spectrogram and a live listen button need more than that, so this document
records what was added and why, rather than leaving it implicit in the code.

Two WebSocket channels, deliberately separate.

## `GET /api/v1/live` — visual channel

Carries both JSON control frames and binary spectrogram data on one socket.

### JSON frames

Every JSON frame has a `type`:

| `type` | When | Contents |
|---|---|---|
| `hello` | Immediately on connect | `server_utc`, the full station snapshot, spectrogram channel descriptors, the last 40 detections and last 60 events |
| `status` | Every ~2 s | The full station snapshot |
| `event` | As they occur | One event envelope, exactly as specified in `API_AND_INTEGRATIONS.md` |

`server_utc` lets the client measure its own clock skew against the station, so
spectrogram column times and detection times can be reconciled without assuming
the browser's clock is right.

Events use the published envelope unchanged, so the same JSON can be republished
to MQTT or a webhook without translation:

```json
{
  "schema_version": "1.0",
  "event_id": "uuid",
  "event_type": "detection.created",
  "occurred_at": "2026-08-04T19:26:14Z",
  "station_id": "uuid",
  "data": { }
}
```

Event types emitted: `capture.started`, `capture.stopped`, `capture.gap`,
`capture.levels`, `window.dropped`, `detector.state`, `detection.created`,
`clip.written`, `health.event`, `station.status`.

### Binary frames — spectrogram columns

Little-endian, 16-byte header followed by raw `uint8` payload:

| Offset | Type | Field |
|---|---|---|
| 0 | `u8` | frame type; `1` = spectrogram |
| 1 | `u8` | channel: `0` = audible, `1` = ultrasonic |
| 2 | `u16` | bins per column |
| 4 | `u16` | number of columns in this frame |
| 6 | `u16` | reserved (zero) |
| 8 | `f64` | UTC seconds of the **centre** of the first column |
| 16 | `u8[bins × columns]` | column-major-by-column payload, row-major overall |

Each column is `bins` bytes, ordered from **lowest frequency first**. Bins are
log-spaced between the channel's `min_hz` and `max_hz`; the exact centre
frequencies are in the `hello` frame's channel descriptor rather than being
recomputed by the client.

Each byte is the level mapped from the channel's `floor_db`…`ceiling_db` range
onto 0…255. Each output bin takes the **maximum** of the FFT bins it covers, not
the mean, so a brief narrowband chirp stays visible rather than being averaged
into the noise floor.

Why binary rather than JSON: the audible channel alone produces about 42 columns
per second of 192 bytes each. As JSON numbers that would be roughly 40× larger and
would dominate both the Pi's CPU and the browser's parse time.

### Backfill on connect

After `hello`, the server sends one binary frame per channel containing up to
the configured `spectrogram_backfill_s` seconds of retained history (default
**30 seconds**), so a newly-opened page shows context immediately instead of
an empty canvas.

This is capped and yielded between channels for a measured reason: sending the
full 2400-column history for both channels in one burst (~770 kB) delayed the
audio consumer on the same event loop enough to produce ~1.9 s of backlog and
around 900 dropped audio chunks on the listen channel.

## `GET /api/v1/live/audio` — listen channel

For the UI's **GO LIVE** button.

1. One JSON frame on connect:

```json
{
  "type": "audio-hello",
  "sample_rate": 48000,
  "chunk_frames": 1920,
  "chunk_ms": 40.0,
  "encoding": "pcm_s16le_mono"
}
```

2. Then a continuous series of binary frames, each exactly `chunk_frames`
   little-endian signed 16-bit mono samples (3840 bytes at the defaults) from the
   derived 48 kHz audible stream. About 96 kB/s.

Opening this channel costs nothing until someone presses the button; the
broadcaster is a no-op with no listeners.

### Back-pressure

Each listener has a bounded queue (48 chunks ≈ 1.9 s). A listener that cannot keep
up loses the **oldest** audio, not the newest, so it converges back to live rather
than falling further behind; the drop count is reported per listener in
`/api/v1/station` under `live_audio.per_listener`. Anything queued during the
handshake is discarded before the send loop starts, because a live feed should
begin at *now*.

Capture always wins: no queue between capture and a listener can ever block the
capture loop.

### Client playback, and why not an AudioWorklet

`AudioWorklet` is only exposed in a **secure context**. The debug UI is served over
plain HTTP from a LAN address, and only `localhost` gets the secure-context
exemption, so `context.audioWorklet` is `undefined` on the real deployment. A
worklet implementation cannot start at all there.

The client therefore schedules each chunk as a short `AudioBuffer` on an explicit
playback cursor. Latency is controlled directly:

- the cursor is placed `targetLatencyMs` (120 ms) ahead of the audio clock when
  playback starts or recovers;
- if the cursor falls behind, that is an **underrun** and it is re-seated;
- if the lead exceeds ~200 ms, chunks are **dropped** so the lead converges back
  down. Whatever this threshold is becomes the resting latency, which is why it
  sits just above the target rather than being a generous ceiling — set to 600 ms
  initially, the feed settled at 575 ms and stopped being live in any useful sense;
- far beyond that, the cursor is **re-seated** and the stale backlog skipped.

Measured end-to-end on LAN Wi-Fi: **131–173 ms of jitter buffer plus 42 ms of
browser device latency, so roughly 180 ms**, with no underruns in steady state.

The UI displays buffer depth, device latency, underruns, trims and resyncs, plus
an output level meter taken after the gain stage — so "I hear nothing" can be told
apart from "nothing is arriving".

### Monitor gain

The raw stream is quiet: a calm garden sits near −45 dBFS, which is inaudible on
laptop speakers at unity. The client applies adjustable make-up gain (default
+24 dB) followed by a limiter, so the feed is usable without a loud close event
becoming painful. This gain is monitoring only and never affects what is analysed,
stored or measured.

## Making ultrasound audible in evidence clips

An ultrasonic detection is inaudible in both of the clips written for an audible
one: the authoritative recording is at 384 kHz, which no browser will decode, and
the 48 kHz playback derivative is band-limited to 24 kHz — so the call has been
filtered away entirely. A clip you cannot listen to is only half a piece of
evidence.

Detections whose peak frequency is above 24 kHz therefore get extra derivatives,
served through the same `/api/v1/media/{id}` endpoint with `kind:
"audible_ultrasonic"`. Both are produced by default. Configure with
`OO_ULTRASONIC_AUDIBLE_METHOD` (`time-expansion`, `heterodyne`, `both`, `none`).

### Time expansion — the analysis view

Replays the recording slowed by a factor *N*, so every frequency divides by *N*: a
48 kHz call is heard at 4.8 kHz with `N = 10`. Harmonics, sweep shape, amplitude
envelope and inter-pulse timing all survive exactly, which is why hardware
detectors offer it as TE mode and why recordists use it for identification.

No resampling is involved. The samples are written unchanged with a lower rate in
the WAV header, which *is* time expansion — exactly, and with no filter artefacts.

The factor is chosen per detection so the call lands near
`OO_ULTRASONIC_TARGET_HZ` (default 4 kHz), rounded to a whole number. A single
fixed factor would suit one species and bury another: a 25 kHz noctule and a
110 kHz lesser horseshoe need different treatment. Set
`OO_ULTRASONIC_TIME_EXPANSION_FACTOR` to pin it.

Cost: the clip lasts *N* times longer than the event did.
`OO_ULTRASONIC_AUDIBLE_MAX_S` caps the result.

### Heterodyne — the listening view

Multiplies the signal by a local oscillator tuned to the detection's peak frequency
and keeps the difference, exactly as a handheld bat detector does. Real-time
duration is preserved, so a pass sounds like the sequence of clicks a surveyor
would recognise.

Everything outside `± OO_ULTRASONIC_HETERODYNE_BANDWIDTH_HZ` of the tuning is
discarded, so this one is for listening, not for measurement. The band-pass is
applied *before* mixing, so out-of-band content cannot alias into the result.

### Both are processed, and say so

Each derivative is high-pass filtered first (`OO_ULTRASONIC_HIGHPASS_HZ`, default
12 kHz), because wind and traffic dominate the low end and either method would
otherwise fold that rumble in on top of the call — under time expansion, 90 Hz of
wind becomes a 9 Hz thump that masks everything.

Each is then peak-normalised to −3 dBFS, because ultrasonic calls sit tens of dB
below full scale after filtering and an un-normalised derivative is inaudible even
once shifted into the audible band.

Both steps mean **these files' amplitudes are not comparable with the
authoritative recording's**. The asset detail records
`amplitudes_comparable_to_native: false`, the applied gain in dB, the method and its
parameters; the UI shows a "processed" chip and a sentence explaining what was done
and what it cost. Only the native clip is evidence of level.
