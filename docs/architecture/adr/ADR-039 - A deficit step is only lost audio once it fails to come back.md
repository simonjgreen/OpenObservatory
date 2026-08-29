# ADR-039: A deficit step is only lost audio once it fails to come back
**Decision:** `AlsaSource`'s frame-deficit estimator no longer credits a timing
step as lost audio at the moment it sees it. A step larger than one block — or an
EPIPE from ALSA — opens a *suspicion*, which is held for `_confirm_frames`
(one ring plus two blocks; 268,800 frames, 0.7 s, on the target) while the lowest
deficit seen is tracked. The part of the step that never came back is what gets
credited to `estimated_missing_frames`; the part that did come back was never lost
and is not reported as a gap at all. Three consequences follow:

- `reason=overrun` is now attached only to an event ALSA actually reported. A
  confirmed loss the driver never raised EPIPE for is reported as the new
  `DiscontinuityReason.FRAME_DEFICIT`, because "the stream clock says frames are
  gone" and "the kernel ring overflowed" are different claims and the station was
  making the second when it only had evidence for the first.
- A late read that cost nothing is not a `capture.gap`. It increments a new
  `late_reads` counter, logs `capture.late_read` at info with the stall size, and
  records `late_read_max_frames` — which against `alsa_buffer_frames` is the
  ring headroom that no counter reported before.
- The verdict arrives a few blocks after the event, so `CaptureBlock` gained
  `discontinuity_at_frame` and the gap row and event now record where the loss
  happened rather than where it was confirmed.

An ALSA overrun that is confirmed to have cost nothing is still reported as a gap
(`gaps_without_loss`), because a ring that came within a hair of overflowing is not
nothing and ALSA is the authority on that. An ALSA overrun that did cost audio
still has its cost estimated — that is ADR-030's regression and it is asserted by
name in `tests/test_alsa_source.py::test_a_genuine_overrun_still_has_its_cost_estimated`.

**Reason — the estimator was answering a question the ring had already made
obsolete.** Against the 80 ms ring the station shipped with, a read that arrived
more than a block late really had lost audio: there was nowhere for the frames to
wait. ADR-030 widened the ring to 500 ms precisely so that stalls would be
absorbed, and the estimator was not revisited, so every absorbed stall minted a
phantom gap. Measured on the live station, mid-regression on 2026-08-08:
`frames` 92,505,600 against `expected_frames` 92,526,900 — a real deficit of
21,300 frames (0.055 s) — while `estimated_missing_frames` claimed 252,495
(0.657 s) and ALSA's own `overruns` counter sat at **0**. Over 11.9 h the same
comparison was 4.06 s real against 52.4 s claimed, **12.9x**.

**The second number it contaminated.** Phantom frames are added back into
`presented` in the observed-rate calculation, so the station read
`rate_offset_ppm` of +2,680 to +3,600 against a device whose true crystal offset
is about **-43 ppm** (`TARGET_DIAGNOSTICS.md`). 252,495/92,505,600 = 2,729 ppm,
which is the whole of the error. No separate fix was needed for this and none was
made: crediting only confirmed loss fixes it as a consequence, which is the
measurement below.

**Why confirmation rather than a rule about ring depth.** "Only credit a step
larger than the ring" would also work for the absorbed-stall case and is simpler,
but it is a rule about the mechanism rather than a measurement of the property,
and it fails the case where the ring overflows by a little: a 520 ms stall behind
a 500 ms ring loses 20 ms, which such a rule would round to nothing. Waiting for
the deficit to settle measures what was actually lost in every case, and it makes
`estimated_missing_frames` a *decomposition of* `expected_frames - frames` rather
than a second, independent, disagreeing estimate. That reconciliation is the
property asserted by
`tests/test_alsa_source.py::test_estimated_missing_frames_agrees_with_the_frame_deficit`.

**Cost:** a gap is reported up to 0.7 s after it happened, and a loss smaller than
one ALSA period (3,840 frames, 10 ms) is absorbed into the drift baseline rather
than credited — so the estimate can under-report by up to 10 ms per event. Both
are stated rather than discovered later. Nothing about detection latency, live
audio or window dispatch changes: the estimator does not gate the data path.

**Measured, off-target, against a device that drops frames on cue.** The
instrument is `RingedDevice` in `tests/test_alsa_source.py`: a fake capture device
with a real kernel-style ring that produces frames at its own crystal rate, holds
them, drops what will not fit, and raises the same `Input/output error` ALSA does.
`device.dropped` is therefore ground truth injected by the test, not another
estimate. Same trace, same device, old estimator against new:

*Eight stalls of 250-400 ms behind a 500 ms ring — the live station's exact
signature, where nothing is lost:*

| | Old | New | Truth |
|---|---|---|---|
| Frames the device dropped | — | — | **0** |
| `estimated_missing_frames` | 259,596 (0.676 s) | **0** | 0 |
| `expected_frames - frames` | 741 | 741 | — |
| `gaps_with_loss` | 5 | **0** | — |
| `late_reads` | *(did not exist)* | 8 | 8 |
| ALSA `overruns` | 0 | 0 | 0 |
| `rate_offset_ppm` | **+15,013** | **-43.0** | -43.0 |

The old column reproduces the live defect closely: 259,596 phantom frames against
the station's 252,495, with the device having lost nothing and ALSA having said
nothing.

*Ten stalls of 150-1200 ms, so the ring genuinely overflows twice:*

| | Old | New | Truth |
|---|---|---|---|
| Frames the device dropped | — | — | **422,365** |
| `estimated_missing_frames` | 779,406 | **422,444** | 422,365 |
| Error against truth | +84.5% | **+0.019%** | — |
| `expected_frames - frames` | 423,289 | 423,289 | — |
| Estimate vs deficit | disagrees by 356,117 | **agrees to 845 frames (2.2 ms)** | — |
| `gaps_with_loss` | 5 | **2** | 2 |
| `rate_offset_ppm` | +16,560 | **-39.3** | -43.0 |

**Corroborated on the live station, read-only, 2026-08-09 08:43Z**, on a build that
does *not* yet contain this change — this is the defect still running, not the fix
verified:

| `frames` | `expected_frames` | Real deficit | `estimated_missing_frames` | `overruns` | `rate_offset_ppm` |
|---|---|---|---|---|---|
| 376,089,600 | 376,133,372 | 43,772 (0.114 s) | 348,786 (0.908 s) | **0** | **+878** |

An **8.0x** over-report over 16 minutes, all seven gaps labelled as having lost
audio with ALSA reporting no overrun at all, and 348,786/376,089,600 = 927 ppm
against an observed +878 with a true crystal offset of -43 — 927 - 43 = 884, which
is the observed figure. The arithmetic of the contamination is exact.

**What is not verified.** This change has **not been deployed to the Pi** and no
on-target before/after exists, because another agent owned the station this
session. The reproduction commands are in `OPEN_INVESTIGATION_CAPTURE_GAPS.md`.
Everything above is either an off-target measurement against a simulated device or
a read-only reading of the *unfixed* station.
