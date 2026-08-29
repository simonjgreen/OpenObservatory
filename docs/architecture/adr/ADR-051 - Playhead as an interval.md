---
aliases:
  - ADR-051
tags:
  - adr
---
# ADR-051: The spectrogram says where the sound you are hearing is, as a measured interval rather than a line
**Status:** active. Frontend only; the offset is measured against a ground-truth
rig rather than assumed, and the shipped estimator is in the station's deployed
bundle.
**Decision:** The live spectrogram draws a playhead marker: an amber band across
the frequency axis at the point on its own time axis corresponding to the audio
currently leaving the speakers, with a hairline through the middle of it and a
badge reading `hearing 2.4 s ago ±0.3 s`. It is drawn from the same
`newestUtc + elapsedColumns()` anchor and the same `spanRect` the detection
overlay uses, so it cannot drift out of agreement with the boxes beside it, and
it is correct in both `scroll` and `waterfall` (`playhead.test.ts` asserts both).
Entirely client-side: no new endpoint, no new header, no server change of any
kind. `web/src/components/playhead.ts` holds the whole estimate as pure
functions.

**Reason:** The operator listens live while watching the scrolling display, and
what he hears is seconds behind what he sees. Nothing on screen said by how
much, so the two surfaces silently contradicted each other. On this station
that is not a cosmetic problem: he uses the picture to decide whether the call
he just heard is the shape in front of him.

**The reason this is an ADR and not a line of canvas code** is the charter's
honesty constraint. A marker confidently in the wrong place is worse than no
marker, because it will be believed and acted on. So the offset had to be
*established*, not assumed, and what could not be established had to be shown as
width rather than rounded away.

### What is knowable, and how

Everything is in station UTC — the clock the spectrogram's columns and the
detections already carry — so the marker is a subtraction on one timeline rather
than a parallel wall-clock estimate. `LiveConnection.clockSkewS`, already
measured at the live socket's `hello`, does the conversion. The visual
pipeline's own lag cancels exactly instead of being modelled.

Two independent estimators of the station-UTC of the sound at the speakers:

- **B, buffer-anchored:** `now − bufferedAhead − outputLatency`. The newest
  sample the element holds arrived a moment ago; the play cursor is
  `bufferedAhead` behind it.
- **A, epoch-anchored:** `streamOpened + currentTime − outputLatency`. Media
  time 0 is the first sample the server sent, and `live_audio_wav` drains its
  queue before streaming, so that is the audio which was live when the element
  opened the stream.

On a stream that behaves these are algebraically identical. They part company
for exactly two reasons — a browser reporting `buffered` staler than what it
holds (B too new), and the server shedding the oldest chunk from this listener's
bounded queue (A too old) — and **from inside the browser those two are
indistinguishable, with the same sign**. That is the real limit on this
measurement. So the pair is treated as a *bracket*: the reported value is the
midpoint, and half the gap is added to the claimed uncertainty. Neither
estimator is ever trusted alone.

Exactly one term is estimated rather than read: the browser/OS output buffer
between `currentTime` and the speaker. A media element exposes no equivalent of
`AudioContext.outputLatency`, and Web Audio is unavailable to this UI at all
([[ADR-019 - Chunked-WAV live playback|ADR-019]]), so it is carried as an interval, 0.02–0.20 s, half of which is
charged to the uncertainty along with 0.05 s for the skew measurement's own
round trip. That gives a floor of **±0.14 s** on a healthy stream, and the
observed working figure is **±0.26 s**.

### Measured, against ground truth

`scripts/measure_playhead_offset.py` is the rig, and it is in the repository so
the claim is re-checkable rather than remembered. It streams the same
endless-header WAV shape at the same 48 kHz in the same 40 ms real-time chunks,
and **records the wall-clock instant it wrote every chunk** — so for any media
time the browser reports, it knows exactly when that audio was live. The page
posts its readings back and imports the *real* `playhead.ts`, bundled, not a
copy of the arithmetic.

Run against headless Chromium 150 (the operator's browser family), 169 samples
over 45 s:

| | median | worst |
|---|---|---|
| True lag of the playhead behind live | 2.47 s | 2.49 s |
| Error of the reported centre | **+0.03 s** | **0.13 s** |
| Claimed half-width | 0.26 s | 0.35 s |
| Gap between the two estimators | 0.24 s | 0.42 s |
| **True playhead inside the claimed band** | **169 / 169** | |

The 2.5 s is the operator's "a few seconds", now a number: it is the browser's
own media buffering, not the station.

An earlier design reported B alone and widened by the whole gap. The same rig
rejected it: 804 samples, centre error median +0.05 s and worst 0.25 s, with a
band nearly twice as wide (±0.48 s median). The bracket midpoint is both tighter
and more accurate, and the measurement is what chose it.

**What this rig could not measure, stated plainly rather than glossed:**

- **The output buffer.** Headless has no DAC, so the one estimated term is the
  one term still untested. It is why a band is drawn at all.
- **The network hop and the station's own block staleness.** Loopback has
  neither. Both push the truth *older*, in the same direction the output-latency
  correction already moves the estimate, so the residual error on the real
  station is expected to be smaller than the +0.03 s bias measured here, not
  larger — but that is an argument, not a measurement.
- Nothing was measured on the station itself, deliberately: the operator was
  tuning detector thresholds on it and a deploy would have voided the run.

**The honest summary, which is what the badge says:** the marker is good to
roughly **a quarter of a second** on a healthy stream, against a lag of two to
three seconds. That is comfortably enough to tell which call you are hearing and
nowhere near enough to time an onset, and the band is drawn at true scale so the
display makes that distinction itself.

### Where it says nothing

No marker at all — never a stale one — when the element is paused, when
`readyState` is below `HAVE_FUTURE_DATA`, when `currentTime` did not advance
since the last sample (rebuffering), or before playback has started. A frozen
playhead is not where the sound is; it is where the sound stopped. When the
whole interval falls outside the selected history window the band is not drawn
at all rather than pinned to an edge — pinning asserts a position, and the
position it asserts is wrong — while the badge still reports the number. Past
±1 s the hairline is dropped and only the band is drawn, because at that width a
single line overclaims.

**Cost when nobody is listening: nothing.** `playhead` is `null`, no interval
runs, and the overlay's only expense is one ref read per frame. [[ADR-040 - Spectrograms only when watched|ADR-040]]'s
premise — the steady state of this station is nobody watching — is not weakened.

**Rollback:** revert the commit. The marker is additive; `playhead` is an
optional prop and the four new `AudioTelemetry` fields are genuine readings that
nothing else depends on.

**Reviewed 2026-08-29:** the decision holds and `web/src/components/playhead.ts`
is unchanged; its 31 tests pass. One thing has happened since it was written. On
2026-08-17 the marker read `hearing 111.3 s ahead of the newest column ±0.2 s` —
a sound cannot be heard before the picture of it — and that banner is what found
the boot-time NTP step recorded in [[ADR-063 - Clock re-anchor|ADR-063]]. The refusal above to clamp an
impossible position, or to pin an off-scale one to an edge, is why the error was
visible at all; nothing in the health payload, the metrics or the soak criteria
saw it.

---
Part of the [[ADRS|Architecture Decision Record index]].
