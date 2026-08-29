# ADR-046: The frame deficit is 98% crystal drift, and "audio lost" must not show it
**Decision:** the debug UI's `audio lost` row no longer shows the raw frame
deficit `expected_frames - frames`. It shows `estimated_missing_seconds`, the
estimator's confirmed loss (ADR-039). The deficit is shown separately as
**`behind clock`**, with its crystal-drift term named inline and its
sampling-phase uncertainty stated. `describeDeficit` in
`web/src/components/Pipeline.tsx` performs the decomposition and is unit-tested
against the live readings below. Nothing on the station changed; this is a
presentation fix to a measurement that was already correct and mislabelled.

**Reason — the label was false, and the charter forbids that.** "A number shown
to a human must mean what its label says." `expected_frames - frames` is four
things added together, and on this station only one of them is lost audio:

1. **Sampling phase.** `frames` advances in whole 38,400-frame blocks (100 ms at
   384 kHz) while `expected_frames` advances continuously, so the raw deficit
   sawtooths across a full block *while nothing whatsoever is wrong*. Measured
   over 43 minutes with zero gaps, zero overruns and zero estimated loss, it
   ranged **−162 ms to +185 ms**. A single reading of this row therefore carried
   about **±50 ms of pure artefact** — the same order as the figures it was being
   read for, and most of what "the two measurements disagree" actually was.
2. **Crystal drift.** The AudioMoth's crystal runs about **50.4 ppm** slow
   against the host, so it legitimately delivers fewer frames than nominal wall
   time implies: **0.18 s per hour, 4.4 s per day**, forever, with nothing lost.
3. **Anchor bias**, sub-millisecond (0.34 ms measured), from where frame zero is
   pinned.
4. **Lost audio**, which is what `estimated_missing_frames` measures — being, by
   ADR-039's construction, exactly the part of the deficit that never came back.

Terms 1 and 2 are why the raw deficit read **0.104 s** on a station that had
lost nothing, and why over a night it would have reached 2 s and read as a slow
leak of audio that is not happening.

**The measurement that settles it** is recorded in full, with its windows and
its contamination, in `docs/delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md`. In
short: `/api/v1/health` sampled every 2 s for 43 minutes on one uninterrupted
stream (2026-08-09, UTC 10:31:32 → 11:14:13), with the deficit re-evaluated at
the last block's own start — which `block_age_s` publishes, and which removes
term 1, taking the scatter from ~100 ms to **0.3 ms**. A concurrent agent
saturated two cores from 10:42:03 to 10:52:03 UTC, so the run is reported in
three segments rather than through:

| segment (UTC) | | growth of the corrected deficit |
|---|---|---|
| 10:31:32 → 10:42:01 | control | **+51.17 ppm** [50.52, 51.81] |
| 10:42:03 → 10:52:01 | two cores saturated | **+48.25 ppm** [47.12, 48.75] |
| 10:52:03 → 11:14:13 | control | **+51.00 ppm** [50.74, 51.30] |

The two clean windows agree. **Under load the deficit grew *slower*, not
faster** — the opposite of the failure mode under suspicion, since uncredited
loss would get worse under scheduling pressure, not better. The growth is a
straight line to within 0.5 ms per-minute-median in every segment, and real loss
arrives as a step that stays up; there is no such step anywhere. Independently,
`rate_offset_ppm` converges to an asymptote of **−50.43 ppm** [−50.55, −50.20],
and a second agent measuring the same station in the same hour got **−49.96
ppm**. The deficit's growth and the crystal's rate agree to about **1 ppm — 3.6
ms/hour — and of ambiguous sign.** The deficit is drift. There is no loss the
estimator is missing, and **ADR-039's confirmation window is not too permissive**
on this evidence.

**The limit of the claim:** the longest *clean* window is 22.2 minutes. This
rules out a continuous leak, which was the specific worry. It does not rule out
a rare or long-period event, and a restart-free multi-hour run is still worth
taking — the method now costs 15 minutes.

**Status note, 2026-08-14: the claim holds at ≤ 1 hour and does not extend to
72 hours.** The 72-hour soak (2026-08-10 to 2026-08-13) gives the long run this
ADR asked for, and at that duration the crystal no longer explains the deficit.
All three detectors reported `lag_seconds` ≈ 185 s at 54.7 h into the run; at
this station's measured −51.62 ppm, drift accounts for only about 10 s of that
185 s deficit. The title and this ADR's index row state "98% crystal drift" as
a settled, duration-independent fact; it is settled only for the runs measured
here (minutes to about an hour). At 72 hours the residual is unexplained and
should be read as suspected real loss, not drift, until isolated. See
`HANDOVER.md`'s "Two things suspected, not established" note and
`MILESTONE_STATUS.md` §Milestone 4.5.

**A correction to how these two numbers were being reasoned about.** They were
treated as independent measurements that had to be reconciled. They are not
independent: `rate_offset_ppm` is computed inside `AlsaSource` as
`-(deficit − missing_frames) / expected × 1e6`, so *algebraically*, subtracting
the drift term from the deficit returns `estimated_missing_frames` and nothing
else. Drift-correcting the deficit and displaying the result would therefore
have been a rename of the estimator's own figure, dressed as a second opinion.
The check above is still evidence, because the two are built on different
anchors and one is a slope while the other is a cumulative average — but the
station has **one** measurement of lost audio, not two, and future work must not
assume otherwise.

**Consequence:** `CaptureStatus` in `web/src/types.ts` gained the fields the API
has published since ADR-039 and the UI ignored — `estimated_missing_seconds`,
`gaps_with_loss`, `gaps_without_loss`, `late_reads`, `late_read_max_frames`,
`alsa_buffer_frames`. `late_reads` is now shown beside `overruns`, because a
stall the ring absorbed is the thing `capture.gap` used to impersonate and the
operator had no way to see it.

**What was deliberately not done.** The sampling-phase artefact could be removed
at source, by evaluating `expected_frames` at the last block's start rather than
at snapshot time — a few lines in `Station.status_snapshot`. That is the better
fix and it would stabilise `continuity_ratio` too. It is not taken here because
it changes a measured quantity in the capture path, which on this project has
twice needed a deploy and a soak to trust, and the deploy would have voided
concurrent measurements. The UI states the ±50 ms instead of hiding it. This is
recorded as the recommended next change, not as a gap.

**Rollback:** confined to `web/`. `git revert` the commit and rebuild the UI
(`./deploy/deploy.sh`). No station code, schema, setting or dependency changed,
so a rollback cannot affect capture.

**Smoke test on the target** — the row must read `none` while the station is
clean, and `behind clock` must grow at roughly 0.18 s/hour without `audio lost`
moving:

A heredoc, not `python3 -c '...'`: the f-strings need double quotes inside a
single-quoted argument, and escaping them is how the first draft of this block
failed with `SyntaxError: unexpected character after line continuation
character`.

```bash
# Note: this reads UTC. `journalctl --since` below takes LOCAL time (BST = UTC+1).
curl -s http://<station-host>:8080/api/v1/station | python3 - <<'PY'
import json, sys
c = json.load(sys.stdin)["capture"]
r = c["sample_rate"]
d = c["expected_frames"] - c["frames"]
up = c["expected_frames"] / r
drift = -c["rate_offset_ppm"] * 1e-6 * up
lost = c["estimated_missing_seconds"]
print(f"uptime       {up:8.0f} s")
print(f"audio lost   {lost:8.4f} s   <- the row labelled 'audio lost'")
print(f"behind clock {d / r:8.4f} s   of which {drift:.4f} s drift, +-0.05 s phase")
print(f"residual     {d / r - drift - lost:8.4f} s   must stay within +-0.1 s")
print("gaps", c["gaps_with_loss"], c["gaps_without_loss"], "overruns", c["overruns"],
      "late_reads", c["late_reads"], "of ring", c["alsa_buffer_frames"])
PY

# Every late read must say it cost nothing; nothing may say it did.
ssh <user>@<station-host> 'sudo journalctl -u open-observatory --since "-30 min" \
  | grep -cE "capture.late_read"'
ssh <user>@<station-host> 'sudo journalctl -u open-observatory --since "-30 min" \
  | grep -E "loss_confirmed|lost_audio=True"'   # must print nothing
```

Run against the live station at 47 minutes' uptime, 2026-08-09 11:15:59 UTC,
same uninterrupted stream:

```
uptime           2830 s   stream 728ac7be
audio lost     0.0000 s
behind clock   0.2193 s   of which 0.1428 s drift, +-0.05 s phase
residual       0.0766 s   must stay within +-0.1 s
gaps 0 0 overruns 0 late_reads 41 of ring 192000 ppm -50.44
```

Note what that says: the raw deficit is now **0.219 s**, more than twice the
0.104 s that prompted this whole investigation, and the station has still lost
nothing. Under the old label that would have read as the leak getting worse.
