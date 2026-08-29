---
aliases:
  - ADR-019
tags:
  - adr
---
# ADR-019: Live playback moved off Web Audio to a chunked-WAV stream, because Web Audio was silent on the operator's own laptop
**Status:** active; still the debug UI's default listen path. Two later ADRs amend
it without disturbing the transport decision: [[ADR-022 - HTTP retune control|ADR-022]] replaced the
reconnect-to-retune trade-off with `POST /api/v1/live/tune`, and [[ADR-055 - Timed recording pause|ADR-055]] added a
503 pause refusal ahead of the stream.

**Decision:** Add `GET /api/v1/live/audio.wav`, streaming a 44-byte WAV header with
both size fields set to `0xFFFFFFFF` — the conventional placeholder for a stream of
unknown, effectively endless length — followed by continuous 16-bit little-endian
mono PCM at the broadcaster's sample rate. The debug UI's GO LIVE button now points a
plain `<audio>` element at this endpoint by default. `/api/v1/live/audio`, the
WebSocket channel from [[ADR-012 - One writer per WebSocket|ADR-012]], is unchanged and still serves other clients — a phone
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

**Reviewed 2026-08-29:** the decision holds and every claim above still matches the
code. `GET /api/v1/live/audio.wav` is at `src/open_observatory/api/app.py:2369`;
`_wav_stream_header` still emits 44 bytes with `_WAV_UNKNOWN_SIZE = 0xFFFFFFFF` in
both size fields (`app.py:2903`); the headers are still set at `app.py:2406`-`2412`
and read back off a probe request in `web/src/audio.ts:229`-`238`; and
`web/src/audio.ts` still points a plain `<audio>` element at the endpoint and
contains no Web Audio node of any kind. The `+24 dB` make-up gain still has no
replacement — the volume slider only sets `HTMLMediaElement.volume`, clamped to
0..1, and no server-side gain was added in its place. Two claims need qualifying.
The `Cache-Control` header is set exactly as recorded (`app.py:2407`), but the
`_no_store` middleware then rewrites every response under the API prefix to
`no-store, must-revalidate` (`app.py:2773`), which is what the station actually
returns. And the WebSocket channel is still mounted and unchanged (`app.py:2226`),
but no client *in this repository* uses it any more: `buildLiveAudioUrl` is
exported and unit tested with no runtime caller, because the debug UI serves one
bundle to every device — so "a phone uses it" describes the state before this ADR,
and now means a phone pointed at the endpoint by hand.

**What this ADR is not covered by, recorded because it is the same failure shape as
the lesson above:** the three tests asserting its central artefact — the RIFF
header, the ultrasonic `X-Live-*` headers, and listener release on disconnect — are
all `@pytest.mark.skip`ped (`tests/test_api.py:1083`, `:1143`, `:1166`), because
starlette 0.41.3's synchronous `TestClient` blocks forever on this endpoint's
genuinely infinite generator. The skip reasons name the correct replacement
(`httpx.AsyncClient` over `ASGITransport`); it was never written. Re-checked on
2026-08-29 against the version actually installed — starlette 1.6.0, not the 0.41.3
the skip reasons still name — and the block is unchanged: a `TestClient.stream`
against an endless generator never returns. The endpoint this ADR exists to justify
therefore has no executing test behind it, and a green suite does not observe that.
The first of the three would also now fail as written even if the blocking were
fixed, because it asserts `cache-control == "no-store"` and the middleware above
makes that `no-store, must-revalidate`.

---
Part of the [[ADRS|Architecture Decision Record index]].
