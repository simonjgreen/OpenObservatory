---
aliases:
  - ADR-063
tags:
  - adr
---
# ADR-063: The stream clock re-anchors when the wall clock steps
**Status:** accepted, 2026-08-19
**Amends:** the `StreamClock` contract (technical spec §4.3)

### The failure

The operator noticed a banner in the live-listen view reading

    hearing 111.3 s ahead of the newest column ±0.2 s

on both the audible and ultrasonic panels. Hearing sound *ahead* of the picture
is not a thing that can happen, so one of the two numbers was wrong.

It was the picture — or rather, the timestamp on it. Measured against the
station's own wall clock (agreeing with an independent host to 0.044 s), live
spectrogram columns arrived stamped **113.8 s in the past**, steadily, on both
channels, while frames arrived at full real-time rate. The station's own
arithmetic agreed: over 178,749 s of wall time it had delivered 178,634 s of
audio, a 115 s deficit that frame accounting could not explain (actual vs
expected frames differed by only 9.26 s, the AudioMoth's −51 ppm crystal
offset).

Two different channels, derived from two different frame counters at two
different sample rates, wrong by the identical amount. That points at the one
thing they share: `Station.clock`.

### Root cause

`StreamClock` samples the monotonic and wall clocks **once**, when the first
block of a stream arrives, and thereafter answers "when did frame N happen?" by
counting frames from that anchor. That is a deliberate and good design — it is
what makes derived-audio timestamps exact rather than wandering by up to ~19 ms
with the resampler's ragged chunk sizes, and `contracts.py` says so.

What nobody had considered is that it makes the anchor's *correctness*
permanent too. The module docstring claims "an NTP step can never reorder
frames", which is true and was the only property anyone checked. An NTP step
cannot reorder frames. It can make every single one of them carry the wrong
UTC, for the life of the stream.

The journal from the boot in question:

    10:07:30  capture.opened
    10:07:32  station.clock_anchored  utc=2026-08-17T09:07:32.173784+00:00
    10:09:17  systemd-timesyncd: Initial clock synchronization to 10:09:17 BST
    10:09:17  systemd-resolved: Clock change detected. Flushing caches.

A Raspberry Pi has no battery-backed RTC. It boots with the timestamp systemd
saved at last shutdown and NTP steps it forward once the network is up. Capture
anchored **1 minute 45 seconds before** that step, and the step was ~106 s.

So this is not an exotic race. On this hardware it is the ordinary boot path,
and it will happen on **every** unattended reboot where the network comes up
after capture does — including the 2026-08-17 reboot that voided the 72-hour
soak.

### Blast radius

Every UTC value the station derived from frames for those 49 hours was ~106 s
early: `detection.event_start_utc` and `event_end_utc`, clip filenames, clip
`created_at`, spectrogram column times, MQTT payload timestamps.

Not affected: anything keyed to the monotonic clock — frame ordering, gap
detection, durations, `capture_gap` records, the continuity ratio. That part of
the original design held exactly as intended.

### Decision

1. **Detect and correct steps, once per housekeeping tick.**
   `StreamClock.stepped_by(sample)` compares the anchor's implied wall-minus-
   monotonic skew against a fresh `ClockCorrelation`; past a 1 s threshold,
   `Station._reanchor_clock_if_stepped` swaps in `clock.reanchored(sample)`.

2. **`monotonic_ns_at_frame_zero` is never touched.** The monotonic clock does
   not step; frame N still happened when it happened. Only the UTC *name* of
   that instant changes, so every ordering and duration measurement stays valid
   across a re-anchor.

3. **Steps only, never slew.** NTP slews at up to 500 ppm — about 5 ms across a
   10 s tick, three orders of magnitude under the threshold. Correcting slew
   continuously would reintroduce precisely the timestamp jitter `StreamClock`
   exists to remove.

4. **The correction is forward-only, and says so.** Rows already written keep
   their wrong value. `station.clock_reanchored` logs the step size and both
   anchors, and `oo_capture_clock_reanchors_total` /
   `oo_capture_clock_last_step_seconds` make it alertable — so "which timestamps
   are wrong, and by how much" is answerable after the fact rather than
   guessable. No backfill is attempted: the correct offset for an arbitrary
   historical row is not recoverable from anything the station kept.

5. **`After=time-sync.target` on the unit**, as a cheap reduction in how often
   this fires at boot. Deliberately *not* `systemd-time-wait-sync.service`,
   which would block capture indefinitely on a station with no network —
   recording with an imperfect clock beats not recording.

### Consequences

- A re-anchor introduces a one-off discontinuity in derived UTC. Audio
  timestamped either side of it is up to `clock_last_step_s` apart in UTC while
  being contiguous in the recording. This is the honest representation: the
  timeline really did have a naming error, and the alternative is keeping the
  error forever.
- **The only reason this was ever found is that a human looked at a live-listen
  banner in a browser.** Nothing in the health payload, the metrics, the logs or
  the soak criteria would have caught a 106 s timestamp error — the continuity
  ratio was 0.999949 throughout, because continuity is a monotonic-clock
  property and was genuinely fine. `oo_capture_clock_reanchors_total` exists so
  the next one is caught by the instrument rather than by eye.
- The playhead banner was right, and was the only thing that was. It is worth
  keeping honest for that reason: a UI that had clamped the impossible-looking
  negative number to zero would have hidden a real data-integrity bug.

---
Part of the [[ADRS|Architecture Decision Record index]].
