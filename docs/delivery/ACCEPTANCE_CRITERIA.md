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

**Reviewed 2026-08-30:** the bold sentence above is now wrong on both counts.
[[ADR-073 - Five capture SLOs|ADR-073]] decomposed the continuity criterion on
2026-08-29, so there is no longer a box called "72-hour capture continuity", and
**two** boxes are ticked rather than one. Only SLO B inherits the 2026-08-25
soak. SLO A was ticked from a 9.8-day measurement taken while writing that ADR,
not from an acceptance run — which is precisely the standard this paragraph says
the project does not tick a box on. The rule was relaxed for A without being
restated; A passes its target by two orders of magnitude either way. Everything
else in the paragraph still holds.

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

**Reviewed 2026-08-30: a fifth restart-free window longer than 72 hours has
since occurred, and it is recorded here rather than as an attempt because nobody
ran it as one.** Stream `bdedab17-6010-4082-9a2a-fbb8d8564863`,
2026-08-25T07:35:20Z to 2026-08-28T14:16:55Z, **78.693 hours**, ended by a deploy
(`end_reason` `station_stopped`) and not by a fault. Continuity 99.9943%, two
discontinuities, **neither carrying loss** — no `capture_gap` row exists for the
whole window, so confirmed loss was **0.000 s** against attempt 4's 0.597 s. On
the criterion attempt 4 was judged by, it passes more cleanly than the run that
closed the box. There is no soak record for it; it is cited only in
[[ADR-073 - Five capture SLOs|ADR-073]] and inside SLO B below. No box was ticked
on it beyond B, and no deliberate fifth attempt has been staged since.

For what *is* delivered, with evidence, see
[`MILESTONE_STATUS.md`](MILESTONE_STATUS.md). For the measured figures the capture
criteria will be judged against, see
[`../operations/TARGET_DIAGNOSTICS.md`](../operations/TARGET_DIAGNOSTICS.md). For
what the soak should capture, see
[`../operations/DEPLOYMENT_AND_OPERATIONS.md`](../operations/DEPLOYMENT_AND_OPERATIONS.md)'s
"Soak testing" section. Note that a soak and a deploy are mutually exclusive —
deploying restarts capture and voids the run.

**Reviewed 2026-08-30: the unticked boxes below are not all unticked for the
same reason, and the opening paragraph reads as though they were.** Three kinds
are mixed together, and only the first is waiting on an acceptance exercise.

- **Believed met from ordinary use, awaiting a formal run.** Stable device
  identity, one ALSA owner, health checks, CSV/JSON export
  (`GET /api/v1/detections/export`), native rate and format displayed, licences
  and attributions visible (`GET /api/v1/detectors` carries `licence_name` per
  plugin), and scores never labelled calibrated (`"calibrated": false` on every
  detector).
- **Already demonstrated on the target, though not inside an acceptance
  window.** The bird fixture passes on ARM64 (`tests/test_birdnet_fixture.py`,
  on the Pi since 2026-08-08 per [[MILESTONE_STATUS]]), and services restarted
  after host reboot twice in the field — the mains cuts of 2026-08-17 and
  2026-08-22 each brought capture back unattended, and attempt 4 above *is* that
  recovery.
- **Not built at all, so no acceptance exercise could tick them.** There is no
  MCP module anywhere under `src/`, and no backup or restore command in the `oo`
  CLI; [[MILESTONE_STATUS]] lists "Milestone 7 entirely" as outstanding, along
  with the alert engine, environmental telemetry and HMAC webhooks. Nobody
  should read this file and infer that the code exists and only the ceremony is
  missing. Separately, "bat fixture passes if bat adapter is released" is
  conditional on a release [[ADR-017 - BatDetect2 as an optional adapter|ADR-017]]
  decided against, so as written it cannot be ticked at all.

## Capture

- [ ] AudioMoth is addressed by stable identity, not volatile card number alone.
- [ ] Exactly one service opens the ALSA hardware source.
- [ ] Unplug/replug resumes capture with a new stream ID and logged gap.
- [ ] Overruns and short reads create visible health events.
- [x] **SLO A — coverage ≥ 99.5%/month.** Measured **99.986%** over the 9.8 days to 2026-08-29: 1.9 min of downtime in three outages (a mains cut, a deploy, an unattended-upgrade restart).
- [x] **SLO B — capture integrity ≥ 99.99%.** Measured **100%** over the 78.7 h stream ending 2026-08-28: **zero** audio lost. The 2026-08-25 soak lost 0.597 s of a 259.2 s budget.
- [ ] SLO A2 — prime-hours coverage ≥ 99.9% (civil twilight ±2 h and the bat window). Not yet measured over a full month.
- [ ] SLO C — timestamp error ≤ 60 s. Bounded by stream age today ([[ADR-072 - Accepted crystal drift|ADR-072]]); needs a month of evidence.
- [ ] SLO D — detection coverage ≥ 99%. Measurable as of 2026-08-29; not yet observed over a month.
- [ ] SLO E — evidence sufficiency ≥ 95%. Blocked on [[ADR-074 - Evidence kept by value|ADR-074]]'s policy actually running — see the 2026-08-30 note below.

  These six replace the single "72-hour continuity ≥ 99.9%" box, which was
  ticked on 2026-08-25 and is inherited by **B alone** ([[ADR-073 - Five capture SLOs|ADR-073]]). That old
  figure summed lost audio and crystal drift: of the 13.5 s it reported,
  0.597 s was audio and ~12.9 s was the crystal. The 78.7 h run that followed
  lost **nothing at all** and still scored 99.9943%. A and B are measured
  apart so that never reads as a fault again.

  **Reviewed 2026-08-30, against the station and the code. Both ticks still
  pass; both cite evidence that has since been overtaken, and two of the four
  unticked reasons need correcting.**

  - **A still passes.** `GET /api/v1/history` reads `fraction_captured`
    **0.99987** for the 7 days to 2026-08-30 — 79.5 s uncovered across seven
    inter-stream outages, of which only about 29 s is wall-clock downtime
    (deploys, plus the one unattended-upgrade restart); the balance is the
    frame-derived bound charging crystal drift as downtime, exactly as
    [[ADR-073 - Five capture SLOs|ADR-073]]'s review note describes. Two cautions
    on the ticked figure, neither of which moves the verdict: it is 9.8 days
    measured against a target written *per month* — the same shortfall A2, C and
    D are unticked for — and 99.986% is the raw-interval figure, where the
    frame-bounded, [[ADR-024 - Coverage bounded by frames|ADR-024]]-compliant
    number for that window is **99.981%** with 159.8 s of downtime.
  - **B still passes, but "zero" was a property of one stream and not of the
    station.** The 7 days to 2026-08-30 hold **3.35 s** of confirmed loss across
    five gap events (`GET /api/v1/gaps`), of which **2.75 s in three events fell
    after that stream closed** — 0.479 s/day against the 8.6 s/day target,
    integrity **99.99946%**. The largest single item, 2.218 s on 2026-08-29, is
    an overrun caused by running drift gate (c) beside the live station
    ([[DRIFT_GATE_C_2026-08-29]]) — a self-inflicted cost rather than a capture
    regression. Headroom on the daily rate is about 18×, not the 140× first
    cited.
  - **C's stated reason bounds the crystal, and only the crystal.** Stream age
    does not bound an anchoring error: [[ADR-063 - Clock re-anchor|ADR-063]]
    recorded every UTC timestamp across 49 hours being **106 s early**, which
    breaches this SLO outright on a stream hours old. `clock_reanchors` reads 0
    on the station today, so read this box as "no bound has been demonstrated"
    rather than "bounded".
  - **E's reason is stale as written.** [[ADR-074 - Evidence kept by value|ADR-074]]
    shipped on 2026-08-29 — as rarity only, with the plausibility half unwired,
    five defects recorded against its bank, and its mechanism since replaced by
    [[ADR-076 - The evidence bank is a column, not a recomputed set|ADR-076]].
    `evidence_value_enabled` reads **false** on the station
    (`GET /api/v1/settings`) and must stay off until [[ADR-076 - The evidence bank is a column, not a recomputed set|ADR-076]]'s
    backfill dry-run has been run and read by a human. E is therefore blocked
    not on the ADR being written but on its policy being live: the denominator
    it needs — *detections worth keeping* — does not exist yet.
  - **A2 and D are not merely unmeasured over a month; nothing measures them at
    all.** `slo.coverage`, `slo.prime_intervals` and `slo.detection_coverage`
    are implemented and tested (`tests/test_slo.py`) but are called nowhere in
    `src/` — only `split_deficit` is wired, into the health payload and
    `/metrics`. `GET /api/v1/history`'s coverage is `history.coverage`, a
    different and older function. The SLO plan records this as a deliberate
    stopping point, so A was ticked, and A2 and D wait, on arithmetic the
    station does not serve.
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

  **Reviewed 2026-08-30: still not met, and the reason has moved.** Retention now
  runs and is instrumented ([[ADR-062 - Retention walks live assets|ADR-062]],
  [[ADR-064 - Watermark tier first|ADR-064]]), and `oo_clips_failed_total` reads
  0 on the current stream against 1,909 misses in the 2026-08-14 window. But
  `GET /api/v1/retention/status` reports 237,709 live clips holding about 380 GB,
  nothing eligible for deletion, the watermark tier skipped, and
  `oo_retention_sweep_complete` **0.0** — the sweep does not finish inside its
  batch budget, and `retention_sweep_keeping_up` is `false` with the disk at
  81.8% against an 85% watermark. Value-based retention
  ([[ADR-074 - Evidence kept by value|ADR-074]],
  [[ADR-076 - The evidence bank is a column, not a recomputed set|ADR-076]]) is
  shipped but off, and both ADRs require it to stay off until a backfill dry-run
  has been read. So the bound this box asks for is still not demonstrated.
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
