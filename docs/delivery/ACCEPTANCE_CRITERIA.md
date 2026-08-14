# Acceptance Criteria

The v1 gate. `CLAUDE.md`: *"Do not describe the system as complete until the
acceptance criteria in this file pass on the Raspberry Pi 5 for a continuous
72-hour soak test."*

**No box below is ticked, and that is accurate rather than neglectful: no formal
acceptance run has passed.** One has been attempted — see "Attempts" below —
and it failed on the capture-continuity criterion, so every other box remains
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
  which is itself a first. See `MILESTONE_STATUS.md` §Milestone 4.5 and
  `../operations/TARGET_DIAGNOSTICS.md` for the full figures. No other
  criterion below was formally exercised during this attempt. A re-run is
  needed once ADR-060 and ADR-061 are deployed and verified.

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
- [ ] 72-hour continuity is at least 99.9%, excluding explicit hardware-disconnect windows.
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
  evidence queue dropping under load, `HANDOVER.md` §1b), and retention had
  never deleted anything until today's ADR-061 deploy — see
  `../operations/TARGET_DIAGNOSTICS.md`.
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
- [ ] Services restart after host reboot. (There are no containers — ADR-008
  chose native systemd deployment for this project. `WantedBy=multi-user.target`
  is the thing to verify here, not a container runtime.)
- [ ] Disk-full behaviour preserves database integrity.
- [ ] Thermal throttling triggers visible degradation policy.
- [ ] Upgrade preflight and rollback instructions exist.
- [ ] Third-party licences and attributions are visible.
