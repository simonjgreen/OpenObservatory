---
aliases:
  - ADR-022
tags:
  - adr
---
# ADR-022: Live ultrasonic retuning restored as a plain HTTP control call alongside the chunked-WAV stream
**Status:** active.
**Decision:** Add `POST /api/v1/live/tune?tune_hz=<value>`. It calls the same
`Station.set_ultrasonic_tune_hz` the WebSocket's `{"type": "tune", ...}` frame
already called, and returns the clamped value actually applied plus the kept
bandwidth and availability. The `audio.wav` stream itself is completely untouched
by this call — no reconnect, no new URL, no gap. `web/src/audio.ts`'s
`LiveAudioPlayer.setTuneHz` now POSTs here (throttled, see below) instead of
tearing the stream down and reopening it at a new `tune_hz` query parameter.

**Reason.** [[ADR-019 - Chunked-WAV live playback|ADR-019]] moved the debug UI's listen path from a WebSocket to chunked
WAV over plain HTTP to fix silent playback on the operator's laptop; the comment
written on `LiveAudioPlayer.setTuneHz` in that same change noted in passing that
retuning would have to mean reconnecting at a new URL, "same as switching
channel" — a WAV response has no return channel of its own to carry a `tune`
frame the way the WebSocket did. That trade-off turned out to be worse in
practice than recorded at the time: the debug UI's frequency slider calls
`setTuneHz` on every `onChange` tick, which for a `<input type="range">` fires
continuously while dragging — often dozens of times a second during a sweep.
Each call tore down the `<audio>` element and reopened the stream at a new URL,
so sweeping the dial across the ultrasonic band repeatedly killed and restarted
playback, which is what the operator reported as "it now breaks the UI as you
move the slider." The fix is not a transport change — [[ADR-019 - Chunked-WAV live playback|ADR-019]]'s reasoning about
Web Audio being silent on this hardware is untouched and nothing here reintroduces
a Web Audio node — it is recognising that retuning never needed the stream's own
transport at all. The heterodyne oscillator lives on the server, station-wide
([[ADR-018 - Live heterodyne, one oscillator|ADR-018]]: one oscillator shared by every ultrasonic listener, last tuning request
wins for everyone), so any small side channel that can reach the station process
can retune it. A one-shot HTTP POST is the smallest one available over the
existing plain-HTTP surface.

**Why not target a specific listener/session.** [[ADR-018 - Live heterodyne, one oscillator|ADR-018]] already established that
there is exactly one heterodyne per station, not one per listener — retuning is
inherently a broadcast operation, not a per-connection one. `POST
/api/v1/live/tune` has no listener/session identifier for the same reason the
WebSocket's `tune` frame never needed one: the last request wins for every
connected ultrasonic listener, on whichever transport. This is a pre-existing
constraint restated, not a new one introduced here.

**Client-side throttling.** A range input's `onChange` fires far faster than a
human needs a new tuning frequency applied, and far faster than is polite to a
Raspberry Pi doing continuous capture. `LiveAudioPlayer.setTuneHz` now sends the
first tick immediately (so the dial feels responsive) and throttles subsequent
ticks to at most one in-flight request per 80 ms, trailing-edge — the last value
the slider settles on is always the one actually sent, even though intermediate
values during a fast sweep are coalesced and never reach the server. `stop()`
cancels a pending throttled send, so a tune request never lands after the
listener has torn down.

**What this does not restore.** The `+24 dB` monitor make-up gain lost in [[ADR-019 - Chunked-WAV live playback|ADR-019]]
is still gone; this ADR is only about retuning. The WebSocket path
(`/api/v1/live/audio?channel=ultrasonic`) is unaffected and still supports its own
in-place `tune` frame for clients that use it (a phone).

**Consequence, recorded rather than smoothed over:** the debug UI now issues an
HTTP request per throttle window while the operator drags the slider, on top of
the continuous WAV stream already flowing. Each is a tiny, stateless GET-shaped
POST against a plain FastAPI route with no body to parse — negligible next to the
~96 kB/s the audio stream itself already costs — but it is still one more request
class hitting the station during a sweep, worth knowing about if a future
regression looks like periodic latency spikes correlated with slider use.

**Reviewed 2026-08-29:** the decision holds and the code still matches it — the
route is at `src/open_observatory/api/app.py:2447`, the 80 ms trailing-edge
throttle at `web/src/audio.ts:115`, and both carry executing tests
(`tests/test_api.py:1201` and `:1247`, `web/src/audio.test.ts:132`), which the
chunked-WAV endpoint this control call sits beside still does not. One
qualification to the paragraph above: the WebSocket path is still mounted
(`app.py:2226`) and its `tune` reader still applies frames (`app.py:2338`), but
no client in this repository points at it any more, so "clients that use it (a
phone)" means a phone aimed at the endpoint by hand — as
[[ADR-019 - Chunked-WAV live playback|ADR-019]]'s own 2026-08-29 review records.

---
Part of the [[ADRS|Architecture Decision Record index]].
