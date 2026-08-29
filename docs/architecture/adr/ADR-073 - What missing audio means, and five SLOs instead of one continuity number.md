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
**Relates to:** [[ADR-039]] (loss accounting), [[ADR-072]] (accepted drift)

### The problem: one number measuring three unrelated things

`continuity_ratio = frames / expected_frames`, where `expected_frames` is
elapsed monotonic time × the **nominal** sample rate. That single ratio silently
sums three phenomena with nothing in common:

| | what happened | is audio actually missing? |
|---|---|---|
| **coverage** | the capture process was not running | **yes — gone** |
| **integrity** | frames dropped while it *was* running | **yes — gone** |
| **drift** | the crystal ran slow ([[ADR-072]]) | **no — the audio exists and is fine** |

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
| before [[ADR-060]]/061 | 2,983 | 662.6 s |
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
| **A** | **Coverage** — wall-clock fraction with capture running | **≥ 99.5% / month** (≈3.6 h) | 99.986% | absorbs two power cuts, a reboot and weekly package restarts without a breach. This is a domestic device on unprotected mains ([[ADR-072]]); a target that a power cut breaches is a target that trains you to ignore it |
| **A2** | **Prime-hours coverage** — the same, restricted to civil twilight ±2 h and the ultrasonic night window | **≥ 99.9% / month** | ~100% | where the birds and bats are. Loss here is real loss; loss at 14:00 in December mostly is not |
| **B** | **Capture integrity** — of recorded time, the fraction not dropped | **≥ 99.99%** (≤ 8.6 s/day) | 0.06 s/day | 140× headroom on measured, which is the point: it catches a genuine regression and ignores noise. The pre-[[ADR-060]] era ran at ~9 s/day and would breach it |
| **C** | **Timestamp accuracy** — absolute UTC error of a detection | **≤ 60 s** | ≤4.3 s/day, resets each stream | at 50 ppm this bounds a stream to ~14 days. Restarts already deliver that (longest ever: 83.8 h) |
| **D** | **Detection coverage** — fraction of captured audio actually analysed | **≥ 99%** | not yet measured | audio captured but never analysed is invisible loss and nothing currently reports it |
| **E** | **Evidence sufficiency** — detections worth keeping that kept a clip | **≥ 95%** | see [[ADR-074]] | "worth keeping" changes meaning entirely under [[ADR-074]]'s value-based retention |

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
- **D is not currently measurable.** Nothing reports the fraction of captured
  audio the detectors actually consumed. Recording an SLO that cannot be
  measured is honest only if the gap is stated: it is stated here, and closing
  it is work, not a formality.
- A2 needs the solar scheduler's window, which already exists for ultrasonic
  gating, applied to a coverage calculation. Small.
- Nobody should tune capture against a single percentage again.

### What this ADR does not do

It does not change any capture code, and it does not lower a bar. B is
*stricter* than the old criterion in the dimension that matters (8.6 s/day
against an effective ~86 s/day once drift is removed from the 0.1% budget).
What it removes is the pretence that one number described a healthy device.

---
Part of the [[ADRS|Architecture Decision Record index]].
