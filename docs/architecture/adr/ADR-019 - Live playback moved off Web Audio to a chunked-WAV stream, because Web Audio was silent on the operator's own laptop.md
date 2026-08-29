---
aliases:
  - ADR-019
tags:
  - adr
---
# ADR-019: Live playback moved off Web Audio to a chunked-WAV stream, because Web Audio was silent on the operator's own laptop
**Decision:** Add `GET /api/v1/live/audio.wav`, streaming a 44-byte WAV header with
both size fields set to `0xFFFFFFFF` — the conventional placeholder for a stream of
unknown, effectively endless length — followed by continuous 16-bit little-endian
mono PCM at the broadcaster's sample rate. The debug UI's GO LIVE button now points a
plain `<audio>` element at this endpoint by default. `/api/v1/live/audio`, the
WebSocket channel from [[ADR-012]], is unchanged and still serves other clients — a phone
uses it and had no problem to fix.

**Reason:** Diagnosed empirically, not guessed, in the operator's own browser.
Listening worked on an iPhone and was completely silent on the laptop (Chrome 150,
Ubuntu, hardware output 44.1 kHz). The transport was proven fine first: the server
reported one listener connected, and the client's own Web Audio telemetry reported
`out -18 dBFS`, `buffer 112 ms`, `under 0` — a graph that believed it was producing
audible sound. The Web Audio graph was correctly wired
(`gain -> limiter -> analyser -> context.destination`) and its `AudioContext`, built
at 48 kHz, reported `state: "running"`. None of that mattered: an oscillator routed
through `context.destination` was inaudible, and so was the same graph routed through
`createMediaStreamDestination()` into an `<audio srcObject>`. Web Audio produced no
audible output by any route on that machine. A generated WAV played through a plain
`<audio>` element *was* audible, as was YouTube in the same browser — media-element
playback works there; Web Audio does not. The chunked-WAV endpoint routes around
Web Audio entirely rather than debugging it further, because there was no remaining
node in that graph left to suspect.

**Consequences, recorded rather than smoothed over:**

- The page is served over plain HTTP to a LAN IP, so `window.isSecureContext` is
  false, `navigator.mediaDevices` is undefined, and `AudioWorklet` was already
  unavailable before today (see [[DEBUG_UI_TRANSPORT]]'s discussion of why the
  original WebSocket client scheduled `AudioBuffer`s on an explicit cursor instead of
  using a worklet). That pre-existing constraint shaped the original design and is
  worth restating here, because it is adjacent to but not the cause of today's bug —
  the worklet was never in use on either path.
- **All client-side audio telemetry (`out dBFS`, `buffer ms`, underruns) is gone.**
  It was measured *inside* Web Audio nodes, which no longer exist in this path, and
  was not ported across because there is nothing genuine to port: an
  `HTMLMediaElement` exposes no per-sample level and no explicit jitter buffer. It
  was replaced with what the element actually reports —
  `readyState`/`networkState`, seconds buffered ahead of the play cursor, and counts
  of `stalled`/`waiting` events — in `web/src/audio.ts`'s `AudioTelemetry`. Nothing
  is fabricated to fill the gap.
- **The +24 dB monitor make-up gain is also gone**, and has no replacement yet. A
  calm garden sits near −45 dBFS, so that gain was doing real work, and the plain
  `<audio>` element has no gain stage of its own — only the browser's own volume
  control. If the WAV path proves too quiet in practice, the fix is server-side gain
  applied to the stream before it reaches the broadcaster, not a client-side node,
  because any Web Audio node reintroduces exactly the failure this ADR routes
  around.
- The response carries `Cache-Control: no-store` and `X-Live-Sample-Rate`, plus
  `X-Live-Tune-Hz`/`X-Live-Bandwidth-Hz` on the ultrasonic channel, since there is no
  JSON hello frame over plain HTTP to carry that information instead. See
  [[DEBUG_UI_TRANSPORT]] for the full header and lifecycle description.

**The lesson worth generalising, because it will recur:** every meter the old client
displayed was measured *inside* the failing subsystem, upstream of where the signal
was actually lost, so it confidently reported health while producing silence. A
transport-layer check (listener count, one) and a decoder-layer check
(`out dBFS`) both passed while the final hop — Web Audio's connection to this
machine's actual output device — was silently broken. A meter that cannot observe
the failure is worse than no meter, because it actively suggests there is nothing
left to check.

---
Part of the [[ADRS|Architecture Decision Record index]].
