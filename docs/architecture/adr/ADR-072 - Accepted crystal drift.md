---
aliases:
  - ADR-072
tags:
  - adr
---
# ADR-072: Capture timestamps drift with the microphone's crystal, and that is accepted
**Status:** accepted, 2026-08-25
**Component:** `audio/contracts.py` (`StreamClock`), `audio/alsa_source.py`
**Relates to:** [[ADR-039 - Confirmed loss, not deficit|ADR-039]] (loss accounting), [[ADR-063 - Clock re-anchor|ADR-063]] (the anchor), [[ADR-069 - Two drift gates|ADR-069]] (the
gate that surfaced this)

### The fact

`StreamClock` maps frames to UTC by anchoring once and counting frames at the
**nominal** rate:

```python
def utc_ns(self, frame: int, sample_rate: int) -> int:
    return self.utc_ns_at_frame_zero + frame * NS_PER_S // sample_rate
```

The AudioMoth delivers **383,980.8 Hz** against a nominal 384,000 — about
**−50 ppm**. Every UTC timestamp the station writes therefore falls **behind
real time by roughly 4.3 seconds per day of unbroken capture**, and nothing
corrects it. `observed_rate_hz` and `rate_offset_ppm` are measured in
`AlsaSource` and reported in `/api/v1/health`; they feed no clock and no alarm.

The existing re-anchor path does **not** cover this. `stepped_by()` compares the
system *wall* clock against the system *monotonic* clock and fires above a 1 s
step. The capture clock is a **third** clock, on a separate USB device, and
nothing compares it to either. Drift is also slew-shaped, so it would not trip a
step detector even if one were watching.

### Why this is tolerable, with numbers

**The error resets on every stream restart** — a new stream re-anchors against
the NTP-disciplined wall clock — so the error at any instant is 50 ppm × *the
age of the current stream*, never × calendar time. It does not accumulate across
months of operation.

Measured over this station's entire recorded history (19 closed streams,
`audio_stream`): **median 1.77 h, mean 18.39 h, longest ever 83.84 h.**

| unbroken stream | timestamp error |
|---|---|
| 1 hour | 0.18 s |
| 1 day | 4.3 s |
| **83.8 h — the longest this station has ever achieved** | **15.1 s** |
| 1 week | 30 s |
| 1 month | 2 min 9 s |
| 6 months | 12 min 56 s |

So the worst error ever produced here is about **fifteen seconds**, and the
operator has explicitly accepted that (2026-08-25).

### What is *not* affected, and why

This is the part that makes the decision easy. Drift touches only the **UTC name
of an instant**. It does not touch:

- **Ordering, durations and gap sizes** — all keyed to the monotonic clock.
- **Evidence clips** — extracted by frame index (`clips.py`), so the audio in a
  clip always matches its detection exactly. A wrong UTC label never
  desynchronises the sound from the event.
- **Detection quality** — 50 ppm is 2.25 Hz on a 45 kHz bat call and a 0.005%
  time-base error to BirdNET.
- **Solar and night gating** — seconds are irrelevant to sunset.
- **Home Assistant's own history.** The MQTT payload's `detected_at` is
  `event_end_utc` and carries the drift, but HA's recorder stamps
  `last_changed`/`last_updated` from its own NTP-disciplined clock. Timelines,
  graphs and automation triggers are unaffected; only the `detected_at`
  attribute drifts, and early in a stream the publish latency likely exceeds it.

### Decision

**Accept the drift. Do not correct the clock.**

Chasing a moving rate estimate would reintroduce exactly the per-block timestamp
jitter `StreamClock` exists to remove, and would break the invariant that frame
*N* always maps to the same UTC — which matters more for a permanent record than
fifteen seconds does.

**Sanctioned mitigation: a periodic restart.** The operator's own suggestion, and
it is the cheapest possible bound: a monthly restart caps the error at ~2 min 9 s
by construction, needs no change to the timestamp path, and risks nothing.

If implemented, it must **not** land at dawn, dusk, or during bat hours. [[ADR-067 - Unattended package work|ADR-067]]
already moved unattended package work to 15:00 for this reason, and a restart is
more disruptive than a package upgrade: it costs a real capture gap. The same
slot, or another verified-quiet one, is the right home for it. A graceful stop
closes its own stream row ([[ADR-066 - Graceful shutdown closes the row|ADR-066]]), so a scheduled restart is clean in the
record rather than appearing as an unclean restart.

**Not implemented by this ADR.** This ADR records the fact and the tolerance.

### The related gap, which is not about drift

`rate_offset_ppm` has **no alert of any kind**. There is no entry in the health
`problems` list, no metric threshold, no log warning. A crystal degrading to
500 ppm — 43 seconds a day — would produce `"status": "ok"` with an empty
`problems` array, and would be found by somebody noticing that detections looked
oddly timed.

That is a different concern from the drift itself: 50 ppm is accepted, but
*undetected change* in it is not obviously acceptable, and it is the same class
of failure this project has hit repeatedly — a counter measured, displayed, and
trusted by nobody to raise an alarm. **Proposed, not decided:** a health problem
when `abs(rate_offset_ppm)` leaves a sane band (±200 ppm is well outside any
crystal and well inside a fault). Roughly ten lines, and independent of
everything above.

### If this is ever revisited

The right fix is **not** a periodic re-anchor with a tolerated step. It is a
piecewise-linear clock whose segments change *slope* rather than *offset*, each
new segment anchored at the previous one's value for the boundary frame — which
is continuous by construction, so there is no step to tolerate, and keeps frame
*N* mapping to a stable UTC provided the segments are persisted with the stream.
Error would be bounded by (rate-estimate error) × (time since last update):
about 3 ms at hourly updates given the ±0.85 ppm the [[ADR-069 - Two drift gates|ADR-069]] sampler achieves.

Any such change must refuse to update from a window containing confirmed loss —
otherwise a station losing audio distorts its own time base — must clamp the
accepted rate to a sane band, and must treat monotonicity as absolute rather
than as a threshold.

### Consequences

- The drift is now written down where somebody will find it: here, in
  `StreamClock`'s docstring, and in the operations guide.
- Nobody should be surprised by a detection timestamped a few seconds early on a
  long-running stream, and nobody should "fix" it without reading this first.
- A soak that runs for months is also a soak whose timestamps are minutes out.
  That is worth knowing *before* the reliability work succeeds rather than after.

**Reviewed 2026-08-29:** the decision holds and the crystal figure above is what
the station still reports. [[TARGET_DIAGNOSTICS]] now carries −50 ppm as the
accepted value, replacing the pre-fix −43 ppm it used to headline. Two things
have changed since this was written. [[ADR-073 - Five capture SLOs|ADR-073]] budgets this drift as SLO C,
**absolute UTC error of a detection ≤ 60 s**, which is tighter than the monthly
restart sanctioned above: a month of unbroken capture is about 2 min 9 s and
would breach it. At 50 ppm, 60 s bounds a stream to roughly fourteen days, so a
restart implemented from this ADR should take its period from C rather than from
the paragraph above. And the `rate_offset_ppm` band check proposed above is
still not implemented — `problems` is assembled at
`src/open_observatory/api/app.py:997`-`1093` and no entry reads the rate — so it
remains proposed, not decided.

---
Part of the [[ADRS|Architecture Decision Record index]].
