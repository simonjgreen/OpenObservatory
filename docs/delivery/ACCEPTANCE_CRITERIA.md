# Acceptance Criteria

The v1 gate. `CLAUDE.md`: *"Do not describe the system as complete until the
acceptance criteria in this file pass on the Raspberry Pi 5 for a continuous
72-hour soak test."*

**Exactly one box below is ticked: 72-hour capture continuity, passed
2026-08-25.** Everything else remains unticked, and that is accurate rather than
neglectful. Four 72-hour runs have been attempted — see "Attempts" below — and
the fourth produced a valid, restart-free 72-hour window that met the continuity
criterion with margin. **That closes one criterion, not the acceptance gate.** No
other box was formally exercised during that window, so all of them remain
unticked on the same "believed met from ordinary use is not passed an
acceptance run" basis as before. Several criteria are believed met from
day-to-day measurement — capture is addressed by stable identity, exactly one
process opens ALSA, CSV/JSON export works, health checks exist — but "believed met
from ordinary use" is not "passed an acceptance run", and this project does not
tick a box on the former. Ticking these is a deliberate acceptance exercise
somebody has to run, alongside a passing 72-hour soak, and record here with dates.

## Attempts

- **Attempt 1, 2026-08-10 to 2026-08-13. FAILED.** Continuity over the exact
  72-hour window was 99.865% against the ≥ 99.9% criterion below —
  349.3 s of audio lost out of 259,200 s. Restart-free for the whole window,
  which is itself a first. See [[MILESTONE_STATUS]] §Milestone 4.5 and
  [[TARGET_DIAGNOSTICS]] for the full figures. No other
  criterion below was formally exercised during this attempt. A re-run is
  needed once [[ADR-060 - A stalled read is a dead stream|ADR-060]] and [[ADR-061 - Operator keep flag|ADR-061]] are deployed and verified.

- **Attempt 2, 2026-08-14 to 2026-08-17. VOID.** Reached 62.7 hours restart-free
  at 99.9935% continuity — comfortably passing — and then the Pi restarted at
  2026-08-17 09:07 UTC, 8.9 hours short. Cause not established at the time;
  the signature is identical to attempt 3's, which was later confirmed as a
  mains cut. Nothing reported the restart, which is why [[ADR-065 - Unclean restart is reported|ADR-065]] exists. The run
  was also hiding two defects while passing ([[ADR-062 - Retention walks live assets|ADR-062]], [[ADR-063 - Clock re-anchor|ADR-063]]) — see
  [[SOAK_2026-08-14]].

- **Attempt 3, ended 2026-08-22T05:48:20Z. VOID.** Died 7h43m short. **Confirmed
  by the operator as a mains power cut** — the first *established* cause for any
  of these restarts.

- **Attempt 4, 2026-08-22T05:50:47Z to 2026-08-25T05:50:47Z. PASSED the
  continuity criterion.** 72.107 hours restart-free on one stream
  (`ef29e800-5c03-409d-a769-f4d1719c784c`), `stream_restarts` 0,
  `clock_reanchors` 0. Continuity **99.9948%** against ≥ 99.9%, with **0.597 s**
  of audio lost against a 259.2 s budget — 0.23% of the allowance, from two
  gaps. Full record and both gaps enumerated in
  [`../operations/SOAK_2026-08-22.md`](../operations/SOAK_2026-08-22.md).
  This run was not staged: it began as the reboot after attempt 3's power cut
  and was recognised as a soak 41 hours in. Only the continuity box is ticked
  on the strength of it; no other criterion was exercised in that window.

For what *is* delivered, with evidence, see
[`MILESTONE_STATUS.md`](MILESTONE_STATUS.md). For the measured figures the capture
criteria will be judged against, see
[`../operations/TARGET_DIAGNOSTICS.md`](../operations/TARGET_DIAGNOSTICS.md). For
what the soak should capture, see
[`../operations/DEPLOYMENT_AND_OPERATIONS.md`](../operations/DEPLOYMENT_AND_OPERATIONS.md)'s
"Soak testing" section. Note that a soak and a deploy are mutually exclusive —
deploying restarts capture and voids the run.

## Capture

- [ ] AudioMoth is addressed by stable identity, not volatile card number alone.
- [ ] Exactly one service opens the ALSA hardware source.
- [ ] Unplug/replug resumes capture with a new stream ID and logged gap.
- [ ] Overruns and short reads create visible health events.
- [x] **SLO A — coverage ≥ 99.5%/month.** Measured **99.986%** over the 9.8 days to 2026-08-29: 1.9 min of downtime in three outages (a mains cut, a deploy, an unattended-upgrade restart).
- [x] **SLO B — capture integrity ≥ 99.99%.** Measured **100%** over the 78.7 h stream ending 2026-08-28: **zero** audio lost. The 2026-08-25 soak lost 0.597 s of a 259.2 s budget.
- [ ] SLO A2 — prime-hours coverage ≥ 99.9% (civil twilight ±2 h and the bat window). Not yet measured over a full month.
- [ ] SLO C — timestamp error ≤ 60 s. Bounded by stream age today (ADR-072); needs a month of evidence.
- [ ] SLO D — detection coverage ≥ 99%. Measurable as of 2026-08-29; not yet observed over a month.
- [ ] SLO E — evidence sufficiency ≥ 95%. Blocked on ADR-074.

  These six replace the single "72-hour continuity ≥ 99.9%" box, which was
  ticked on 2026-08-25 and is inherited by **B alone** (ADR-073). That old
  figure summed lost audio and crystal drift: of the 13.5 s it reported,
  0.597 s was audio and ~12.9 s was the crystal. The 78.7 h run that followed
  lost **nothing at all** and still scored 99.9943%. A and B are measured
  apart so that never reads as a fault again.
- [ ] NTP/timezone changes do not reorder source frames.

## Audio processing

- [ ] Native sample rate and format are displayed.
- [ ] 48 kHz derived stream passes sweep/alias tests.
- [ ] Window-to-source timing mapping is tested.
- [ ] Rolling buffer cannot grow beyond configured memory.
- [ ] Detector slowdown cannot block capture.

## Detection

- [ ] Every detection records plugin/model/version/hash and native output.
- [ ] Bird fixture passes on ARM64 target.
- [ ] Bat fixture passes if bat adapter is released.
- [ ] Model scores are not labelled calibrated probabilities without evidence.
- [ ] Failed detectors are obvious in UI and metrics.

## Evidence and privacy

- [ ] Continuous raw disk recording is off by default.
- [ ] Evidence clips obey maximum duration and retention. **Known not met as of
  2026-08-14**: 2,743 detections were published with no clip at all (the bounded
  evidence queue dropping under load, [[HANDOVER]] §1b), and retention had
  never deleted anything until today's [[ADR-061 - Operator keep flag|ADR-061]] deploy — see
  [[TARGET_DIAGNOSTICS]].
- [ ] Clip checksum and source-frame provenance are stored.
- [ ] Publishing integrations are off by default.
- [ ] Location precision for outbound integrations is configurable.

## Product

- [ ] Dashboard works without internet.
- [ ] Detection filtering, playback and review work.
- [ ] Capture gaps and detector lag are visible.
- [ ] CSV/JSON export works.
- [ ] Backup and restore are tested.
- [ ] API and MQTT contracts are versioned.
- [ ] Home Assistant discovery works against a test broker.
- [ ] MCP tools enforce bounded queries and token scopes.

## Operations

- [ ] All services have health checks.
- [ ] Services restart after host reboot. (There are no containers — [[ADR-008 - systemd, not Compose|ADR-008]]
  chose native systemd deployment for this project. `WantedBy=multi-user.target`
  is the thing to verify here, not a container runtime.)
- [ ] Disk-full behaviour preserves database integrity.
- [ ] Thermal throttling triggers visible degradation policy.
- [ ] Upgrade preflight and rollback instructions exist.
- [ ] Third-party licences and attributions are visible.
