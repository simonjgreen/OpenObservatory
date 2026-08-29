---
aliases:
  - ADR-073
tags:
  - adr
---
# ADR-073: What "missing audio" means, and five SLOs instead of one continuity number
**Status:** accepted, 2026-08-29
**Supersedes:** the single "72-hour continuity ≥ 99.9%" criterion as the *only*
capture-completeness measure. That criterion is not deleted; it is decomposed.
**Relates to:** [[ADR-039 - Confirmed loss, not deficit|ADR-039]] (loss accounting), [[ADR-072 - Accepted crystal drift|ADR-072]] (accepted drift)
**Implementation plan:** [[2026-08-29-capture-slos]]

### The problem: one number measuring three unrelated things

`continuity_ratio = frames / expected_frames`, where `expected_frames` is
elapsed monotonic time × the **nominal** sample rate. That single ratio silently
sums three phenomena with nothing in common:

| | what happened | is audio actually missing? |
|---|---|---|
| **coverage** | the capture process was not running | **yes — gone** |
| **integrity** | frames dropped while it *was* running | **yes — gone** |
| **drift** | the crystal ran slow ([[ADR-072 - Accepted crystal drift\|ADR-072]]) | **no — the audio exists and is fine** |

Drift is not loss. Nothing is missing; the audio is merely labelled a few
seconds off. Yet it lands in the ratio, and it dominates it:

| soak | continuity | reported shortfall | **audio actually lost** | drift |
|---|---|---|---|---|
| 2026-08-22 → 25 (72.1 h) | 0.999948 | 13.5 s | **0.597 s** | ~12.9 s |
| 2026-08-25 → 28 (78.7 h) | 0.999943 | 16.0 s | **0.000 s** | 16.0 s |

**96% of the first soak's "loss", and 100% of the second's, was the crystal.**
The second soak lost no audio whatsoever and was still reported as 99.9943%
complete. We have been tuning a figure that mostly measures an oscillator.

There is also a latent absurdity: at about **1150 ppm the drift alone consumes
the entire 0.1% budget**, so a station with flawless capture would fail the
criterion because of a component this project has already decided to tolerate.

And the number is silent about the thing that costs most. Over the last 9.8
days the station lost **0.60 s** to in-stream gaps and **114 s** to being
switched off between streams. The criterion measures the former and ignores the
latter by two orders of magnitude.

### What the device actually does

Loss by era, every `capture_gap` row ever written:

| era | gap events | audio lost |
|---|---|---|
| before [[ADR-060 - A stalled read is a dead stream\|ADR-060]]/061 | 2,983 | 662.6 s |
| 2026-08-14 → 19 | 183 | 58.8 s |
| **since 2026-08-19 (10 days)** | **2** | **0.60 s** |

Coverage, measured by clipping every `audio_stream` row to the window:

| window | uptime | downtime | outages |
|---|---|---|---|
| since 2026-08-19 (9.8 d) | **99.986%** | 1.9 min | 3 (mains cut 105 s; deploy 4 s; unattended-upgrade 6 s) |
| last 7 days | **99.998%** | 0.2 min | 2 |

So: roughly **0.06 s of audio lost per day** and **12 s of downtime per day**.

### The decision

**Five SLOs, one per failure mode, measured and reported separately.** A number
that mixes failure modes cannot be acted on, because each mode has a different
cause, a different fix and a different tolerance.

The unit of value for this device is not the second. It is *did we miss a bird*.
A two-minute outage at dawn costs more than two hours at 14:00 in December. The
targets below are deliberately loose in wall-clock terms and tight where birds
actually are.

| | SLO | target | measured | derivation |
|---|---|---|---|---|
| **A** | **Coverage** — wall-clock fraction with capture running | **≥ 99.5% / month** (≈3.6 h) | 99.986% | absorbs two power cuts, a reboot and weekly package restarts without a breach. This is a domestic device on unprotected mains ([[ADR-072 - Accepted crystal drift\|ADR-072]]); a target that a power cut breaches is a target that trains you to ignore it |
| **A2** | **Prime-hours coverage** — the same, restricted to civil twilight ±2 h and the ultrasonic night window | **≥ 99.9% / month** | ~100% | where the birds and bats are. Loss here is real loss; loss at 14:00 in December mostly is not |
| **B** | **Capture integrity** — of recorded time, the fraction not dropped | **≥ 99.99%** (≤ 8.6 s/day) | 0.06 s/day | 140× headroom on measured, which is the point: it catches a genuine regression and ignores noise. The pre-[[ADR-060 - A stalled read is a dead stream\|ADR-060]] era ran at ~9 s/day and would breach it |
| **C** | **Timestamp accuracy** — absolute UTC error of a detection | **≤ 60 s** | ≤4.3 s/day, resets each stream | at 50 ppm this bounds a stream to ~14 days. Restarts already deliver that (longest ever: 83.8 h) |
| **D** | **Detection coverage** — fraction of captured audio actually analysed | **≥ 99%** | worst detector, from counters that already existed | audio captured but never analysed is invisible loss. Reported as the *worst* detector, never the mean: one starving detector is a hole in the record, and averaging lets a healthy one paper over it |
| **E** | **Evidence sufficiency** — detections worth keeping that kept a clip | **≥ 95%** | see [[ADR-074 - Evidence kept by value\|ADR-074]] | "worth keeping" changes meaning entirely under [[ADR-074 - Evidence kept by value\|ADR-074]]'s value-based retention |

### The rule that makes this work

**Drift is excluded from every loss SLO.** It is measured, reported, and
budgeted under C as a *labelling* error, never under A or B as a *loss*. A
report that says "we lost 16 seconds" when nothing was lost is not conservative,
it is wrong, and it sends somebody hunting for a fault that does not exist.

Restated under these SLOs, the 2026-08-25 soak reads: **coverage 100%, capture
integrity 100%, zero audio lost, timestamp error 16 s.** That is what actually
happened.

### Consequences

- [[ACCEPTANCE_CRITERIA]]'s continuity box is replaced by A and B. The box
  ticked on 2026-08-25 stands: it passed under the old criterion and passes more
  comfortably under the new one.
- **D was recorded here as unmeasurable, and that was wrong.** The counters
  already existed — `windows_analysed`, `windows_dropped_queue_full` and
  `windows_dropped_stale` in `detectors/base.py` — and had simply never been
  divided or surfaced. Corrected 2026-08-29 while implementing this ADR, which
  is the useful kind of correction: writing the SLO down is what made somebody
  go looking for the counters.
- A2 needs the solar scheduler's window, which already exists for ultrasonic
  gating, applied to a coverage calculation. Small.
- Nobody should tune capture against a single percentage again.

### What this ADR does not do

It does not change any capture code, and it does not lower a bar. B is
*stricter* than the old criterion in the dimension that matters (8.6 s/day
against an effective ~86 s/day once drift is removed from the 0.1% budget).
What it removes is the pretence that one number described a healthy device.

**Reviewed 2026-08-29, during implementation.** The decision holds. Five things
qualify it.

- **The residual definition of drift inherits [[ADR-039 - Confirmed loss, not deficit|ADR-039]]'s under-report bound.**
  B takes confirmed loss as authoritative and calls the rest of the deficit
  drift, so a real loss the estimator never confirmed is reclassified as the
  crystal. [[ADR-039 - Confirmed loss, not deficit|ADR-039]] bounds that at one ALSA period — 3,840 frames, 10 ms —
  per event, and that is therefore also the bound on how much real loss B can
  hide. It does not threaten an 8.6 s/day target, but B is an estimate and
  should not be quoted as though it were exact.
- **A must be fed intervals that are already frame-bounded ([[ADR-024 - Coverage bounded by frames|ADR-024]]).**
  `slo.coverage()` clips and merges whatever intervals it is handed and carries
  no honest-frame bound of its own, so given raw `audio_stream.end_utc` values
  it would report the coverage a stream *claimed* rather than the coverage the
  audio supports — exactly what [[ADR-024 - Coverage bounded by frames|ADR-024]] exists to prevent.
  `history._honest_stream_end()` is that bound, and A is only ADR-024-compliant
  if it runs first.
- **That bound charges crystal drift as downtime, and it is the one place drift
  still leaks into a coverage SLO.** The frame-derived cap trims each stream's
  end by roughly its accumulated drift — 11.8 s, 17.2 s and 16.0 s on the three
  streams closed since 2026-08-19 — so `GET /api/v1/history` reports 99.981%
  and 159.8 s of downtime for the same 9.8-day window recorded above as 99.986%
  and 114 s. The 45 s difference is the crystal, not an outage. A passes by two
  orders of magnitude either way and no target moves, but the rule stated above
  is honoured by B and not yet by A.
- **B's "140× headroom" was measured on a quieter fortnight than the one this
  ADR was written in.** Two further confirmed gap events cost 0.534 s in the
  24.7 h to 2026-08-29 15:00 — 0.52 s/day against the 0.06 s/day cited above,
  and in one day nearly double the loss recorded in the previous ten. Integrity
  was still 99.9994% against the 99.99% target, so B passes; the headroom on
  that rate is about 17×, not 140×.
- **The ppm figure is wrong.** 0.1% is 1,000 ppm, not 1,150, so drift alone
  exhausts the old budget slightly sooner than stated. The argument is unchanged
  and marginally stronger.

---
Part of the [[ADRS|Architecture Decision Record index]].
