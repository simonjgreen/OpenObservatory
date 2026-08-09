# Live transport for the debug UI, and for the wall display

The API specification only commits to SSE for detection updates. A real-time
spectrogram and a live listen button need more than that, so this document
records what was added and why, rather than leaving it implicit in the code.

Four channels now: two WebSockets for the debug UI, deliberately separate
(ADR-012); a chunked-WAV HTTP stream added by ADR-019 as the default playback
path after Web Audio proved silent on a real laptop; and a fourth, much smaller
WebSocket added by ADR-038 for the ESP32 wall display, which needs detections and
nothing else. ADR-012's single-writer rule governs every WebSocket here — the WAV
addition has no socket and so no writer to serialise.

If you only want the wall display's channel, skip to
[`GET /api/v1/display`](#get-apiv1display--the-inside-observers-push-channel) at
the end; nothing above it applies to that client.

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

For the UI's **GO LIVE** button. Carries two channels, selected once at
connect time by a query parameter — `?channel=audible` (the default) or
`?channel=ultrasonic`. Switching channel reconnects; ADR-012's single-writer
rule is per-*socket*, and a socket's channel is fixed for its lifetime.

### Audible (default)

The derived 48 kHz audible mix — unchanged from before this channel existed.

```
GET /api/v1/live/audio
GET /api/v1/live/audio?channel=audible   # equivalent
```

### Ultrasonic

A **live heterodyne** rendering of the native ultrasonic stream, mixed down
to the audible band around a tuning frequency — the behaviour of a handheld
bat detector, not the offline time-expansion/heterodyne clip renderer below.
Real-time duration is preserved (this is heterodyne, not time-expansion:
time-expansion would fall behind live audio immediately and unboundedly).
Implemented by `audio/heterodyne_stream.py`; carries oscillator phase and
low-pass filter state continuously across chunks, so there is no click at
chunk boundaries the way there would be if a clip renderer were called once
per chunk.

```
GET /api/v1/live/audio?channel=ultrasonic
GET /api/v1/live/audio?channel=ultrasonic&tune_hz=45000   # initial tuning
```

`tune_hz` is optional; it defaults to `OO_ULTRASONIC_LIVE_TUNE_HZ` (45 kHz).
Requests are clamped to `OO_ULTRASONIC_BAND_HZ` (15–125 kHz by default) and to
just under the native stream's Nyquist. The bandwidth kept either side of the
tuning frequency is `OO_ULTRASONIC_HETERODYNE_BANDWIDTH_HZ`, shared with the
offline renderer — both describe the same "how selective is the mix"
question.

Retuning while connected does **not** require a reconnect: send a text frame
on the same socket —

```json
{ "type": "tune", "tune_hz": 42000 }
```

— read by a small concurrent reader task that only ever calls
`socket.receive_json()`; it never writes to the socket, so ADR-012's
single-writer invariant holds. Anything that isn't a `tune` frame, or fails
to parse, is ignored.

There is exactly one oscillator per station, shared by every ultrasonic
listener: the last tuning request wins for everyone connected to that
channel. Fine for the expected single-operator LAN use; a second concurrent
listener wanting a different band is not supported.

If the station's native rate cannot be decimated to the output rate by an
integer factor (only 384 kHz -> 48 kHz, an exact 1/8, is exercised in
practice), the ultrasonic channel is unavailable; `hello.available` is
`false` and `hello.reason` explains why. AudioMoth's one hardware profile on
this project's target is 384 kHz, so this is a defensive fallback rather than
an expected path.

### Hello frame

One JSON frame on connect, before any binary audio:

```json
{
  "type": "audio-hello",
  "channel": "audible",
  "sample_rate": 48000,
  "chunk_frames": 1920,
  "chunk_ms": 40.0,
  "encoding": "pcm_s16le_mono",
  "available": true
}
```

The ultrasonic channel's hello adds the tuning actually applied (which may
have been clamped from what was requested) and the kept bandwidth:

```json
{
  "type": "audio-hello",
  "channel": "ultrasonic",
  "sample_rate": 48000,
  "chunk_frames": 1920,
  "chunk_ms": 40.0,
  "encoding": "pcm_s16le_mono",
  "available": true,
  "tune_hz": 45000.0,
  "bandwidth_hz": 5000.0
}
```

so the client never has to guess what it actually got.

### Framing, both channels

A continuous series of binary frames, each exactly `chunk_frames`
little-endian signed 16-bit mono samples (3840 bytes at the defaults, ~96 kB/s)
at `sample_rate` (48 kHz for both channels — the ultrasonic path decimates the
native stream down before it ever reaches the broadcaster). Same chunk
framing and encoding on both channels deliberately, so the client's existing
jitter buffer (below) needs no channel-specific logic.

Opening either channel costs nothing until someone presses the button, or
switches to it: each channel's broadcaster is a no-op with no listeners, and
the ultrasonic channel additionally skips the heterodyne computation itself
whenever it has no listeners — continuously heterodyning 384 kHz for nobody
would waste real CPU on a device whose capture must always win.

### Back-pressure

Each listener has a bounded queue (48 chunks ≈ 1.9 s). A listener that cannot keep
up loses the **oldest** audio, not the newest, so it converges back to live rather
than falling further behind; the drop count is reported per listener in
`/api/v1/station` under `live_audio.per_listener` (audible) or
`live_audio_ultrasonic.per_listener` (ultrasonic — that block also carries
`heterodyne`, the oscillator/filter state description, or `null` with
`unavailable_reason` set when the channel can't run for this stream's native
rate). Anything queued during the handshake is discarded before the send loop
starts, because a live feed should begin at *now*.

Capture always wins: no queue between capture and a listener can ever block the
capture loop.

### Client playback, and why not an AudioWorklet

**This section describes the WebSocket path's client, which this UI no longer uses
by default — see ADR-019 and the chunked-WAV section below.** It is retained
because the WebSocket channel itself is retained (a phone client still uses it),
and because the reasoning about why an `AudioWorklet` was never an option here still
applies to anything built against this channel in future.

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

## `GET /api/v1/live/audio.wav` — chunked-WAV listen channel

Added by ADR-019 after Web Audio playback proved completely silent on a real
laptop (Chrome 150, Ubuntu, 44.1 kHz hardware output) despite every
transport- and decoder-level signal reporting healthy — the WebSocket
delivered chunks, the client's Web Audio telemetry showed `out -18 dBFS`,
`buffer 112 ms`, `under 0`, and the `AudioContext` reported
`state: "running"`, yet nothing came out of the speakers by any Web Audio
route. This is now the debug UI's **default** GO LIVE path, played through a
plain `<audio>` element rather than decoded by Web Audio. `/api/v1/live/audio`
above is unchanged and still in use — a phone client never had this problem.

Same broadcaster, same bounded per-listener queue and drop policy, and the
same "no heterodyne work with zero listeners" invariant as the WebSocket
channel — a listener is only attached once the channel is confirmed
available. The only difference is the transport.

```
GET /api/v1/live/audio.wav
GET /api/v1/live/audio.wav?channel=audible      # equivalent (default)
GET /api/v1/live/audio.wav?channel=ultrasonic
GET /api/v1/live/audio.wav?channel=ultrasonic&tune_hz=45000
```

`channel` and `tune_hz` mean exactly what they mean on the WebSocket path. If
the ultrasonic channel is unavailable for this station's native rate, the
request gets `503` with the same `heterodyne_unavailable_reason` the
WebSocket's `hello.reason` would carry, rather than a stream that opens and
never plays anything.

Retuning while connected **is** supported, but not over this response — there
is no channel to carry a `tune` frame back to the server over a one-way HTTP
body. See `POST /api/v1/live/tune` below instead (ADR-022): the stream this
endpoint opens is never reconnected by a retune, so sweeping the tuning dial
does not restart playback.

### `POST /api/v1/live/tune` — retuning the WAV path in place

```
POST /api/v1/live/tune?tune_hz=42000
```

Retunes the shared heterodyne oscillator exactly as the WebSocket's
`{"type": "tune", "tune_hz": 42000}` frame does — because it calls the same
`Station.set_ultrasonic_tune_hz` — but as a plain, stateless HTTP POST that
needs no open control channel of its own. Since ADR-018 there is exactly one
oscillator per station shared by every ultrasonic listener regardless of
transport, so this call has no listener/session identifier: the last request
wins for everyone, same as the WebSocket path.

Response:

```json
{ "tune_hz": 42000.0, "bandwidth_hz": 5000.0, "available": true, "reason": null }
```

`tune_hz` is the value actually applied, which may be clamped from what was
requested. `available` is `false` (with `reason` set, `tune_hz` still the
clamped landing value) when the ultrasonic channel cannot run for this
station's native rate — the call is harmless to make in that state, it just
has nothing to retune yet.

The debug UI's frequency slider calls this, throttled client-side to at most
one in-flight request per 80 ms (trailing-edge, so the value the slider
settles on is always eventually sent) — see `LiveAudioPlayer.setTuneHz` in
`web/src/audio.ts`.

### Response

No JSON hello frame — there is nothing to carry one over a plain HTTP
response — so the equivalent information is in response headers, present
before the body starts streaming:

| Header | Meaning |
|---|---|
| `Cache-Control: no-store` | Never cache a live stream |
| `X-Live-Sample-Rate` | The broadcaster's output rate (48000) |
| `X-Live-Tune-Hz` | Ultrasonic only: the tuning actually applied, possibly clamped from what was requested |
| `X-Live-Bandwidth-Hz` | Ultrasonic only: the kept bandwidth either side of the tuning frequency |

### Body: the endless-header convention

The body opens with a hand-built 44-byte canonical PCM WAV header
(`_wav_stream_header` in `api/app.py`) — RIFF chunk size and `data` chunk size
both set to `0xFFFFFFFF`, the conventional placeholder a decoder reads as "size
unknown, keep playing" rather than a real byte count, because the true length
is unknowable for a stream with no end. Everything else in the header matches
what `soundfile`/libsndfile itself writes for 16-bit PCM mono at the
broadcaster's sample rate. After the header, the body is continuous 16-bit
little-endian mono PCM, chunked exactly as the WebSocket path chunks it, for
as long as the client stays connected.

### Listener lifecycle on disconnect

The handler polls `request.is_disconnected()` between chunks (1 s timeout on
the queue read, so this check runs at least once a second even during a lull)
and releases the listener the moment it's true, rather than waiting for the
queue to notice — for the ultrasonic channel that listener is the only thing
keeping the heterodyne computation running at all, so a slow client-side
teardown would otherwise keep it computing for nobody. As on the WebSocket
path, anything queued between connection and the point the response actually
starts consuming is drained before the first chunk is sent, because a live
feed should begin at *now*.

### What was lost moving off Web Audio, and why it wasn't faked

`web/src/audio.ts`'s `AudioTelemetry` no longer reports `out dBFS`,
`buffer ms` or `underruns` — those were measured inside Web Audio nodes that
this path does not have. It reports what `HTMLMediaElement` genuinely
exposes instead: `readyState`/`networkState`, seconds buffered ahead of the
play cursor, and `stalled`/`waiting` event counts. The **+24 dB monitor
make-up gain is also gone** and has no client-side replacement — a plain
`<audio>` element has no gain stage, only the browser's volume control — so a
quiet garden near −45 dBFS is quieter on this path than it was on the old
one. Server-side gain applied to the stream itself is the fix if that proves
a problem in practice; a client-side Web Audio `GainNode` is not an option,
because it reintroduces the exact failure this endpoint exists to route
around.

## `GET /api/v1/display` — the inside observer's push channel

A fourth channel, added by ADR-038 for one client: the ESP32 wall display
(`firmware/inside-observer`). Detections only, in compact JSON, sized so that
every frame this channel can produce fits inside a single Ethernet MTU with room
to spare.

Deliberately **not** a mode of `/api/v1/live`. That socket carries binary
spectrogram columns, a `hello` with forty full detection records and sixty
events, a full station snapshot every two seconds and a 30 s spectrogram
backfill — none of which an ESP32 can use, so filtering it would have meant
replacing every frame it sends while still paying ADR-012's warning that any
change to that channel must be re-measured over real Wi-Fi. See ADR-038.

ADR-012's single-writer rule holds here exactly as it does on the other two
sockets: `DisplayClient.run()` is the only code in the process that writes a
display socket. The pump and the receive loop only ever queue and receive.

### Query parameters

| Parameter | Default | Meaning |
|---|---|---|
| `min_score` | `0.75` | Named detections below this are **not sent**. Never appears in any frame. |
| `bats` | `true` | Whether bat passes are sent at all. They are never score-filtered. |
| `rows` | server config (6) | Rows in the connect snapshot. 1–12. |

Filtering is server-side so the device never receives-and-discards, which is the
whole point of the change. The filter lives in the URL, so changing it means
reconnecting.

### Frames

All text, all compact JSON (`separators=(",", ":")`, no whitespace). Every frame
carries `t`.

| `t` | When | Example | Measured |
|---|---|---|---|
| `h` | once, on connect | `{"t":"h","v":1,"now":1786263065,"hb":10,"st":"L","sp":30,"f":[…]}` | 150–294 B |
| `d` | as detections occur | `{"t":"d","n":"Common Woodpigeon","at":1786263086}` | 40–57 B |
| `s` | every `hb` seconds | `{"t":"s","now":1786263075,"st":"L","sp":30}` | 43 B |

Frame keys:

| Key | Frames | Meaning |
|---|---|---|
| `v` | `h` | Wire version, currently `1`. A client that does not know it must refuse the frame rather than half-parse it. |
| `now` | `h`, `s` | The station's Unix epoch seconds. The display has no RTC and no NTP; this is its only clock. |
| `hb` | `h` | Heartbeat period in seconds. The display treats three missed beats as a stale feed. |
| `st` | `h`, `s` | `L` listening, `D` degraded. **Never offline** — that is a fact only the client can know. |
| `d` | `h`, `s` | The station's own words for a degraded state. Absent when listening. |
| `sp` | any | Distinct species today. On a `d` frame only when the count moved. |
| `f` | `h` | The connect snapshot: `rows` rows, already run-collapsed. |

Row keys, shared by `f` entries and by the body of a `d` frame:

| Key | Meaning |
|---|---|
| `n` | Species display name. **Absent on a bat pass.** |
| `at` | Event start, whole Unix epoch seconds, UTC. |
| `b` | `1` when this is a bat pass. Absent otherwise. |
| `k` | Peak frequency in kHz, one decimal. Bat passes only. |
| `r` | Detections collapsed into this row. Absent when 1. |

### What is not on this wire

`native_result`, the media list and its checksums, every UUID, detector
plugin/model/licence metadata, `rank`, `canonical_taxon_id`, `duration_s`, frame
bounds, `stream_id`, `title_hint` — and **`score`**.

There is no score field and no way to add one without editing both
`src/open_observatory/display_channel.py` and
`firmware/inside-observer/src/model/push_frame.h`. ADR-023's rule that no number
readable as a confidence figure reaches the glass is structural here rather than
behavioural: the threshold is applied on the station and the number never leaves
it.

A bat pass carries no name — only `b` and `k`. The words "Bat pass" are supplied
by the firmware, so no server change can put a species on a pass
(`ultrasonic-pass-v1` detects passes, not species; ADR-013). The frequency-band
candidate that `title_hint` carries elsewhere ("45 kHz · common pipistrelle?") is
deliberately not forwarded: it is a legitimate hint in a UI that can print the
sentence explaining it, and a species claim on a wall.

### Snapshot, then deltas

Connect costs one short, column-limited SQL read run off the event loop: the rows
the screen shows, plus one `DISTINCT` for today's species names. Once per
connection — a display connects and then stays connected for days — rather than
once per 20 s. After that the species count is advanced in memory, because a
species counts exactly when one of its detections cleared the threshold, which is
exactly when a frame was sent.

While the station is not capturing from the real microphone, **no detections are
sent at all** (ADR-020). The `st`/`d` fields say why, and the display shows the
banner. A test scene is not an observation of the garden.

### Back-pressure

One bounded queue per client (`display_channel_queue_max`, default 64). When it
is full the **oldest detection frame** is shed — never a status frame, because
losing the banner to a burst of woodpigeons would make a broken station look
merely quiet. Counters are reported in `/api/v1/station` under `display_channel`,
including `mean_frame_bytes`.

Capture always wins: nothing on this path can block or apply back-pressure to the
capture loop.

### Configuration

| Setting | Default | Meaning |
|---|---|---|
| `display_channel_heartbeat_s` | `10.0` | Heartbeat period. Also sets how long a dead station takes to look dead (3 beats). |
| `display_channel_snapshot_rows` | `6` | Connect snapshot size when `rows` is not given. |
| `display_channel_queue_max` | `64` | Frames a display may fall behind by. |

### Measuring it

`scripts/measure_display_wire.py` prints the byte cost of each frame type
offline. `scripts/probe_display_channel.py <host> <seconds>` connects to a real
station and reports what actually crossed the network. Measured against the live
station on 2026-08-09: **1,030 bytes in 90 s, 11.4 B/s**, against the polled
transport's ~6,350 B/s.

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
