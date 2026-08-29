---
aliases:
  - ADR-018
tags:
  - adr
---
# ADR-018: Live ultrasonic monitoring is a second heterodyne implementation, not a reuse of the clip renderer, sharing one oscillator per station
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
not supported; this is a debug/operator surface for one person on a LAN ([[ADR-011]],
[[ADR-015]]), and multi-tenant tuning would be new architecture for a need nobody has yet.

**Reason for the CPU gate:** heterodyning 384 kHz continuously has a real, measurable
cost, and `station.py`'s ordering already treats "capture always wins" as non-negotiable
([[ADR-001]], [[ADR-002]]). The live ultrasonic path is therefore gated exactly like
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

---
Part of the [[ADRS|Architecture Decision Record index]].
