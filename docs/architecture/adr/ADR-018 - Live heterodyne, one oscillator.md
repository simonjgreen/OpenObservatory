---
aliases:
  - ADR-018
tags:
  - adr
---
# ADR-018: Live ultrasonic monitoring is a second heterodyne implementation, not a reuse of the clip renderer, sharing one oscillator per station
**Status:** active. Extended, not superseded, by [[ADR-019 - Chunked-WAV live playback|ADR-019]]
(the debug UI's listen path moved to chunked WAV) and [[ADR-022 - HTTP retune control|ADR-022]]
(retuning via `POST /api/v1/live/tune`); the WebSocket channel and the
one-oscillator-per-station rule described below are unaffected by both.

**Decision:** `/api/v1/live/audio` gains a `?channel=ultrasonic` option: a live,
real-time heterodyne of the native stream, tuned by the listener and reconfigurable
without reconnecting. It is implemented in `audio/heterodyne_stream.py`, a new module —
not by calling `audio/ultrasound.py`'s `heterodyne()` per chunk. That function processes
a fixed array in one shot; called repeatedly on live chunks it would regenerate the
oscillator's phase and the low-pass filter's memory from nothing at every chunk boundary,
producing an audible click at the join every time. `heterodyne_stream.StreamingHeterodyne`
carries both continuously: phase as a wrapped running accumulation, the filter as
overlap-save with a retained tail. `ultrasound.py`'s constants, wording and "for
listening, not measurement" framing are reused throughout; its code is not, because its
code's whole shape assumes a bounded clip.

There is exactly **one** oscillator per station, shared by every ultrasonic listener —
retuning is a broadcast, not a per-connection state. This mirrors how the spectrogram and
`live_audio` (audible) channels already work: one produced stream, fanned out to however
many browsers are watching. A second concurrent listener wanting a different tuning is
not supported; this is a debug/operator surface for one person on a LAN ([[ADR-011 - Debug UI is not the dashboard|ADR-011]],
[[ADR-015 - Anonymous read, auth deferred|ADR-015]]), and multi-tenant tuning would be new architecture for a need nobody has yet.

**Reason for the CPU gate:** heterodyning 384 kHz continuously has a real, measurable
cost, and `station.py`'s ordering already treats "capture always wins" as non-negotiable
([[ADR-001 - Single capture owner|ADR-001]], [[ADR-002 - Native rate, derived audible|ADR-002]]). The live ultrasonic path is therefore gated exactly like
`LiveAudioBroadcaster.publish`: `_handle_block` only calls
`StreamingHeterodyne.process()` when `live_audio_ultrasonic.listener_count > 0`. Skipping
calls while idle and resuming later is safe because all continuity state lives inside the
`StreamingHeterodyne` instance, not in the call pattern — it simply continues from
wherever it left off.

**Deviation from plan, recorded rather than silently made:** the implementation brief for
this feature assumed the live tuning frequency should default from the existing
`ultrasonic_target_hz` setting. That setting is not a tuning frequency — it is where a
*rendered clip's* time-expansion should land in the audible band (default 4 kHz, an
audible-range value), unrelated to where a live oscillator should be tuned in the
ultrasonic band. Reusing it would have defaulted live monitoring to 4 kHz, well outside
`ultrasonic_band_hz` (15–125 kHz) and useless for listening to anything. A new setting,
`ultrasonic_live_tune_hz` (default 45 kHz, the common pipistrelle range), was added
instead. `ultrasonic_heterodyne_bandwidth_hz` genuinely is the right shared default for
bandwidth and is reused unchanged, exactly as briefed.

**Constraint:** Only an integer native-rate-to-48 kHz decimation ratio is supported
(384 kHz -> 48 kHz is exactly 1/8). A native rate that does not divide evenly leaves the
channel unavailable (`hello.available: false`, with a reason) rather than silently
approximating a fractional ratio — AudioMoth's one hardware profile on this project's
target is 384 kHz, so this is a defensive fallback, not an expected path.

**Reviewed 2026-08-29:** the decision holds. `audio/heterodyne_stream.py` still carries
oscillator phase and overlap-save filter state across calls and imports nothing but
numpy, so none of `ultrasound.py`'s code is reached from the live path; the
`ultrasonic_live_tune_hz` / `ultrasonic_target_hz` split recorded above is intact
(45 kHz at `src/open_observatory/config.py:454`, 4 kHz at `:432`). One detail has
moved: the gate is no longer `listener_count` alone but a three-way conjunction —
`not paused and self.heterodyne is not None and self.live_audio_ultrasonic.listener_count`
(`src/open_observatory/station.py:1031`-`1035`) — because [[ADR-055 - Timed recording pause|ADR-055]] added the operator
pause, which refuses live listening on both bands. The CPU reasoning above is
unchanged; there are simply two more ways for the answer to be no.

---
Part of the [[ADRS|Architecture Decision Record index]].
