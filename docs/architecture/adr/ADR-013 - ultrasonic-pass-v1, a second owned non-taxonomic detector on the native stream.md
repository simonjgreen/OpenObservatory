# ADR-013: `ultrasonic-pass-v1`, a second owned non-taxonomic detector on the native stream
**Decision:** Ship a pulse-train detector operating on the native 384 kHz stream, emitting
`bat pass` events with a measured frequency band, pulse count and SNR — never a species.
This brings native-rate window support forward from Milestone 5.

**Reason:** The ultrasonic band is the reason for capturing at 384 kHz; leaving it
uninspected until a third-party classifier existed would have left the most expensive
property of the pipeline unproven. A pass detector is fully owned, needs no model licence,
and is testable.

**Constraint:** It detects passes, not species, and the normaliser enforces that — a
non-taxonomic detector that emits a species name raises. Frequency band is evidence a human
can interpret, not an identification: 18–21 kHz is genuinely ambiguous between noctule and
bush-cricket. It has a known false-positive rate on broadband transients (wind, handling
noise) and no night scheduler, so it currently runs 24 hours a day. BatDetect2 remains
Milestone 5 and is not implemented.

> **Status 2026-08-05:** the night scheduler now exists
> (`src/open_observatory/schedule.py`, `ultrasonic_schedule`, default `always`;
> the live station sets `night`). The detector no longer necessarily runs 24
> hours a day. **The false-positive rate on broadband transients is unchanged** —
> scheduling reduces *when* it runs, not how often an individual pass is wrong.
> The detector also gained feeding-buzz flagging, sub-bin peak-frequency
> interpolation, and presentational candidate group titles carrying a mandatory
> `?`; the stored record still keeps `label = "bat pass"` with no species name,
> and the normaliser's guard is unchanged. See `docs/detectors/DETECTOR_STRATEGY.md`.
