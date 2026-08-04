# Deployment and Operations

## Host preparation

- Raspberry Pi OS 64-bit current stable.
- USB SSD mounted at a stable path, e.g. `/srv/open-observatory`.
- Docker Engine and Compose plugin.
- Host timezone may be local, but containers and database operate in UTC.
- NTP enabled.
- Create a persistent udev symlink for the AudioMoth based on stable USB attributes where possible.
- Disable aggressive USB autosuspend for the device if testing shows disconnects.

## Directory layout

```text
/srv/open-observatory/
  config/
  postgres/
  redis/
  clips/
  transient/
  exports/
  backups/
  logs/
```

`transient/` may use tmpfs if memory permits, otherwise SSD with strict expiry.

## Setup workflow

1. Run audio probe on host.
2. Flash/configure AudioMoth USB microphone firmware separately if required.
3. Verify switch/mode and enumerate sample rates.
4. Run a 60-second test recording at selected high rate.
5. Inspect spectrogram, clipping and silence warnings.
6. Configure station location/timezone and retention.
7. Install model adapters and accept/display their licences.
8. Run fixture self-tests.
9. Start services.
10. Run 30-minute commissioning report.

## Backups

Daily database dump and configuration backup. Clips are optional in backup policy because they may dominate size. Include manifests and checksums. Test restore during v1 acceptance.

## Updates

- Pull versioned images only.
- Run `oo preflight-upgrade`.
- Back up database.
- Apply Compose update and Alembic migration.
- Run health and fixture tests.
- Roll back images if application check fails; database migrations must document reversibility.

## Soak testing

Capture the following over 72 hours:

- frame continuity and gaps;
- USB disconnect/reconnect behaviour;
- CPU temperature/throttling;
- memory high-water mark;
- SSD writes and free-space trend;
- detector queue lag;
- worker crash/restart count;
- evidence extraction misses;
- database growth;
- false health alarms.

## Commissioning output

Generate a Markdown/JSON report recording hardware, firmware notes, negotiated audio mode, model versions/hashes, resource benchmarks, detected faults and recommended operating profile.
