---
aliases:
  - ADR-013
tags:
  - adr
---
# ADR-013: `ultrasonic-pass-v1`, a second owned non-taxonomic detector on the native stream
**Status:** active; the "no night scheduler" constraint is closed — `schedule.py` shipped
in Milestone 5. **The Constraint below overstates the enforcement**: the normaliser's
`NON_TAXONOMIC_PLUGINS` holds only `activity-v1`, so what actually restrains this detector
is its own construction plus [[ADR-049 - Sound categories are not species|ADR-049]]'s shape check. See the dated addendum in the
status blockquote below.

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
> and the normaliser's guard is unchanged. See [[DETECTOR_STRATEGY]].
>
> **Verified 2026-08-29.** Re-read against the code. The decision stands and the
> note above holds, with three corrections.
>
> 1. **"the normaliser enforces that" overstates the guard, and always has.**
>    The plugin-keyed check is `NON_TAXONOMIC_PLUGINS = frozenset({"activity-v1"})`
>    (`src/open_observatory/normaliser.py`); `ultrasonic-pass-v1` has never been
>    in it, and its `taxonomic_group="bat"` is not in `NON_TAXONOMIC_GROUPS`
>    either. The only taxonomic check in the normaliser that reaches this
>    detector is [[ADR-049 - Sound categories are not species|ADR-049]]'s
>    per-detection backstop, which raises on a `rank="species"` claim whose
>    `scientific_name` is not *shaped* like a binomial — so a well-formed
>    binomial emitted by this plugin would pass unchallenged. What actually
>    holds the line today is the detector's own construction: it sets
>    `rank=None`, `label="bat pass"` and neither name field
>    (`detectors/ultrasonic.py`). That is discipline in one file, not the
>    contract this ADR claimed. Adding `ultrasonic-pass-v1` to
>    `NON_TAXONOMIC_PLUGINS`, with the test `activity-v1` already has
>    (`tests/test_pipeline.py`), would make the sentence true; it is **not done
>    here**.
> 2. **BatDetect2 is no longer unimplemented.** [[ADR-045 - Refinement runner|ADR-045]] ships it as a
>    propose-only refiner over stored ultrasonic clips
>    (`src/open_observatory/refinement/batdetect2.py`, `oo refine run`). It is
>    still not a live detector, still not a bundled dependency, and may never
>    move a stored claim — so nothing this ADR decided is disturbed — but the
>    Constraint's "BatDetect2 remains Milestone 5 and is not implemented" is
>    stale as written.
> 3. The bush-cricket ambiguity band is **17–21 kHz** in code (`FREQUENCY_HINTS`),
>    not the 18–21 kHz written in the Constraint above.
>
> **"The live station sets `night`" is confirmed, but not from this repository.**
> `config/example.env` does not carry the key at all; the `always` default is in
> `src/open_observatory/config.py:499`, and an operator's choice persists to
> `config/runtime.env`, which is deliberately untracked (`.gitignore:2`). The
> station's own read-only settings endpoint answers the question:
> `GET /api/v1/settings` returned `ultrasonic_schedule` default `always`,
> value `night` on 2026-08-29. Anyone re-checking this should ask the station,
> not the repository.

---
Part of the [[ADRS|Architecture Decision Record index]].
