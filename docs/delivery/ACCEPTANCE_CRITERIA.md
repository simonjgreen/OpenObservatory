# Acceptance Criteria

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
- [ ] Evidence clips obey maximum duration and retention.
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
- [ ] Containers restart after host reboot.
- [ ] Disk-full behaviour preserves database integrity.
- [ ] Thermal throttling triggers visible degradation policy.
- [ ] Upgrade preflight and rollback instructions exist.
- [ ] Third-party licences and attributions are visible.
