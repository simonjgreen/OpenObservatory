# Deployment and Operations

This document describes the deployment that exists: `rsync` into a Python
virtualenv on the Pi, managed by a single `systemd` unit. It does not use
Docker, Compose, PostgreSQL or Redis. The Compose/PostgreSQL topology described
in the technical spec is kept further down as the unrealised production
target — see ADR-007 and ADR-008 in `docs/architecture/ADRS.md` for why the two
diverge and what would have to be true before the Compose path is real.

## Current deployment: rsync + venv + systemd

### Topology

One host, one process, one owner of the microphone. `oo serve` runs capture,
detectors, the API and the debug UI in a single Python process under
`systemd`. Storage is SQLite by default (ADR-007); there is no database
server. There is no message broker; the event bus is in-process (ADR-009).

### Layout on the target

```text
/home/<user>/open-observatory/        REMOTE_DIR, synced source + venv
  .venv/                             created by deploy.sh, not synced
  config/
    runtime.env                      operator-owned, NOT in the repo, NOT synced by rsync --delete
  data/                               ReadWritePaths for the systemd unit
    clips/                           evidence clips — a mounted USB SSD since 2026-08-08, see below
    (clips.sdcard-backup/ was deleted 2026-08-10, freeing 21 GB on the SD card;
     ADR-057 proved it held no clip that any live row still referenced)
    #  former location of the pre-migration clips left on the SD card, retained pending deletion
  web/dist/                          built UI assets, served by the API process
```

### Deploying

`deploy/deploy.sh` is the whole deployment mechanism. It:

1. builds the web UI locally with `npm` (the Pi has no Node toolchain and is
   not expected to get one);
2. `rsync -a --delete`s the repository to the target, with a fixed exclude
   list (see below);
3. creates `.venv` if absent and installs the package with
   `pip install -e '.[alsa,resample,birdnet,dev]'`;
4. runs `alembic upgrade head` **before** anything is restarted, against the
   database the still-running old version is using (ADR-042). A slow or failing
   migration therefore fails this script and leaves the previous, working
   service up, rather than failing inside the new version's startup path. A
   database already at head is a single read-only revision check, so this is
   safe on every deploy;
5. installs `deploy/open-observatory.service`, the refinement service and its
   timer (ADR-045), and `deploy/99-audiomoth.rules`; reloads `udev` and
   `systemd`; does `enable --now` then `restart` on the station, and
   `enable --now` on `open-observatory-refine.timer` — which arms the schedule
   without starting a pass, so deploying never puts the classifier on the CPU;
6. polls `http://127.0.0.1:8080/api/v1/health` over SSH for up to 60 seconds
   and prints recent `journalctl` output if the service does not come up
   healthy in that time.

The three systemd units are committed as templates: `deploy.sh` substitutes the
deploy user and install path at install time, so no station's paths are baked
into the repository (ADR-047).

It is idempotent — safe to run repeatedly. **`HOST` is required**, deliberately:
the repository ships no station address, so a deploy always says where it is
going.

| Invocation | Effect |
|---|---|
| `HOST=user@host ./deploy/deploy.sh` | full deploy |
| `HOST=user@host ./deploy/deploy.sh --no-web` | skip the `npm` build (use the UI assets already on the target) |
| `HOST=user@host ./deploy/deploy.sh --no-deps` | skip `pip install`, just resync source, migrate and restart |

`REMOTE_DIR` (default `open-observatory`, relative to the SSH login home) is
also overridable as an environment variable, though this is rarely needed.

### `config/runtime.env` is operator-owned state — read this before touching deploy

`config/runtime.env` holds the station name, coordinates and any device-path
override for a given physical installation. It is listed in `.gitignore`, so
it does not exist anywhere in the source tree, and `deploy.sh` carries an
explicit `--exclude 'config/runtime.env'` on its `rsync --delete` for exactly
one reason: `rsync --delete` removes anything on the target that is not in
the source, and gitignored files are by definition never in the source. This
file was destroyed by a deploy once, before the exclude existed. Do not
remove that exclude line, and do not assume any file under `config/` survives
a plain sync unless it is likewise excluded.

The systemd unit loads it with `EnvironmentFile=-/home/<user>/open-observatory/config/runtime.env`
— the leading `-` means a missing file is not an error, so a freshly
provisioned host with no `runtime.env` still starts (with defaults from
`config/example.env`/`Settings`, not with the real station's identity).

Because it is gitignored, `runtime.env` is never backed up by the deploy
mechanism and never travels with `git`. Operators are responsible for keeping
their own copy. There is no backup tooling for it in this repository at
present.

### Excludes applied on every sync

In addition to `config/runtime.env`, `deploy.sh` excludes: `.git`, `.claude`,
`data`, `.env`, `.venv`, `node_modules`, `web/node_modules`, `__pycache__`,
`*.pyc`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `models/*.tflite` and
`models/*.txt`. The model exclusions matter operationally: model assets
fetched on the target with `oo models fetch` are not overwritten or deleted
by a later deploy, and are not shipped from the developer machine either
(ADR-006 — model licences differ from the code's and are not bundled).
`.claude` is excluded because agent worktrees live there and are full checkouts
with their own virtualenvs; syncing them would push gigabytes to the Pi.

**`web/dist` is excluded only when `--no-web` is passed.** It is gitignored, so
in a fresh clone or a worktree it does not exist locally at all, and a plain
`--delete` would remove the working UI from the station. A code-only redeploy
therefore leaves whatever UI is already on the target alone.

### The systemd unit

`deploy/open-observatory.service` runs `ExecStart=.venv/bin/oo serve` as user
the deploy user (substituted by `deploy.sh` at install time), with
`SupplementaryGroups=audio plugdev` for `/dev/snd`
and AudioMoth `hidraw` access. Relevant settings and why they are there:

| Setting | Value | Purpose |
|---|---|---|
| `Nice=-5` | mild priority boost | capture must not be starved by detector inference; full `SCHED_FIFO` was judged unnecessary and riskier |
| `Restart=always`, `RestartSec=5` | | automatic recovery from crashes |
| `MemoryMax=6G` | hard cap | bounds the process on a machine with ~7.8 GiB total |
| `NoNewPrivileges=true` | | no privilege escalation |
| `ProtectSystem=full` | | most of the filesystem read-only to the service |
| `ProtectHome=read-only` | | home directories read-only, including the working directory's parent |
| `ReadWritePaths=/home/<user>/open-observatory/data` | | the one path the service may write, explicitly carved out of `ProtectHome` |
| `PrivateTmp=true`, `ProtectKernelTunables=true`, `ProtectControlGroups=true`, `RestrictSUIDSGID=true` | | further privilege reduction per technical spec §13 |
| `StandardOutput=journal`, `StandardError=journal` | | logs go to the journal, not files, so log rotation is `journald`'s problem, not this project's |

### The refinement unit and its timer (ADR-045)

`deploy/open-observatory-refine.service` + `.timer` are a **second, separate**
service. They run `oo refine run` at **01:00 UTC** nightly — the measured quiet
window — and are installed and armed by `deploy.sh` alongside the station.
`enable --now` on a `.timer` arms the schedule without starting a pass, so
deploying never puts the classifier on the CPU.

It is a separate process on purpose, and the fence is the point. ADR-033
measured a 0.30 s retention sweep, on its own thread *inside* the station,
starving the capture event loop 55–150 ms and producing ~1.9 false
`capture.gap` records a minute. A BatDetect2 pass is 2.1 s of inference.

| Setting | Value | Purpose |
|---|---|---|
| `AllowedCPUs=2-3` | cores 2 and 3 only | the Pi 5 is quad-core; **cores 0-1 stay clear for capture**. Verified on the target (systemd 255): the unit's cgroup reports `cpuset.cpus.effective=2-3` and the process reports `Cpus_allowed_list: 2-3` |
| `Nice=19` | lowest ordinary priority | against the station's `Nice=-5` |
| `MemoryMax=1G` | hard cap | verified as `memory.max=1073741824`. `Type=oneshot`, so an OOM kill is a visibly failed unit, not a silent restart loop |
| `CPUWeight=1`, `IOWeight=1`, `IOSchedulingClass=idle` | lowest | yields even within its own cores; clip reads never contend with evidence writing |
| `PrivateDevices=true` | | this process has no business near `/dev/snd` |
| `TimeoutStartSec=7500` | backstop | the runner has its own budget (`refinement_max_seconds`, 5400 s) and stops cleanly; this catches a run wedged in inference |

**Two things worth knowing before you touch it.**

- **The first activation enables the `cpuset` controller in the root cgroup
  subtree**, which was not previously enabled on this station (it was
  `cpu memory pids`). That constrains nothing by itself — `open-observatory.service`
  still reports an empty `AllowedCPUs` and runs on all four cores — but it is a
  permanent, previously-absent thing in the cgroup tree.
- **`user.slice` does not have the `cpuset` controller**, so `systemd-run`
  *without* `sudo` would silently fence nothing. The refiner must be a system
  unit.

```bash
ssh <station-host> sudo systemctl list-timers open-observatory-refine
ssh <station-host> sudo systemctl status open-observatory-refine
ssh <station-host> sudo journalctl -u open-observatory-refine -n 100 --no-pager
ssh <station-host> sudo systemctl start open-observatory-refine   # runs one pass now

# Disable it entirely. No deploy needed; the station does not import the
# refinement package at all, so this is a complete rollback of ADR-045's
# runtime behaviour.
ssh <station-host> sudo systemctl disable --now open-observatory-refine.timer
```

The runner also refuses to start outside `01:00–03:00 UTC` on its own
(`OO_REFINEMENT_WINDOW_START_HOUR_UTC` / `..._END_HOUR_UTC`), independently of
the timer, so a manual `systemctl start` in daylight skips with a reason rather
than classifying. `oo refine run --force` is the deliberate override.

### Operating commands

```bash
# deploy (from the developer machine, repo root)
HOST=<user>@<station-host> ./deploy/deploy.sh   # full deploy
./deploy/deploy.sh --no-web            # skip the UI build
HOST=user@otherhost ./deploy/deploy.sh # different target

# service control (run these, not process signals, from an SSH session — see trap below)
ssh <station-host> sudo systemctl status open-observatory
ssh <station-host> sudo systemctl restart open-observatory
ssh <station-host> sudo systemctl stop open-observatory     # required before probing hardware directly; only one process may own the mic
ssh <station-host> sudo journalctl -u open-observatory -f
ssh <station-host> sudo journalctl -u open-observatory -n 100 --no-pager

# on-device diagnostics (see docs/operations/TARGET_DIAGNOSTICS.md for recorded output)
ssh <station-host> '~/open-observatory/.venv/bin/oo audio probe'
ssh <station-host> '~/open-observatory/.venv/bin/oo audio test-capture --seconds 8'
ssh <station-host> '~/open-observatory/.venv/bin/oo audio resample-check'
ssh <station-host> '~/open-observatory/.venv/bin/oo audiomoth info'   # switch must be in USB/OFF
ssh <station-host> '~/open-observatory/.venv/bin/oo models status'
ssh <station-host> '~/open-observatory/.venv/bin/oo system-report'
ssh <station-host> '~/open-observatory/.venv/bin/oo config'          # effective settings, resolved DSN

# test suite on the target
ssh <station-host> 'cd ~/open-observatory && .venv/bin/pytest'
```

### The full operator CLI surface

Regenerated from `src/open_observatory/cli.py` on **2026-08-09**. An earlier
version of this list omitted the three repair/maintenance commands.

| Command | What it does |
|---|---|
| `oo audio probe` | enumerate capture devices; record formats, stable identity and native rate support. `--json`, `--write PATH`, `--test-rates` |
| `oo audio test-capture` | capture briefly and report frames delivered vs elapsed, levels and clipping. `--seconds`, `--out` |
| `oo audio resample-check` | verify group delay, delivery-latency bounds and seam continuity. `--source-rate`, `--target-rate`, `--seconds`, `--block-ms` |
| `oo audio window-dump` | inspect a specific segmenter window with ground truth: actual frame bounds, actual sample count cross-checked against an independent `RingBuffer` read, UTC and local-time rendering, and `--gap-at-s`/`--gap-frames` to show a capture gap's real effect on the segmenter. Runs against a replayed WAV (`--source`) or a synthetic scene, never the live station — see its own `--help` for why. `--stream-kind native\|audible48`, `--duration-s`, `--stride-s`, `--index`, `--write-wav PATH`, `--timezone`, `--json` |
| `oo models status` | what model assets are installed |
| `oo models fetch` | checksummed acquisition, licences shown before download. `--force`, `--yes` |
| `oo audiomoth info` | firmware identity over USB HID (switch must be in `USB/OFF`) |
| `oo history reconcile-streams` | repair `audio_stream` rows whose `end_utc` is a claim the frame count contradicts (ADR-024). **Dry-run by default**; `--apply`, `--yes`, `--json`, `--ratio-threshold` |
| `oo detections reconcile-plausibility` | re-evaluate stored BirdNET detections against the current range model and plausibility floor (ADR-032). **Dry-run by default**; `--apply`, `--yes`, `--json`, `--limit`. Never deletes a row or overwrites `native_result`; adds a `native_result.plausibility_review` block. Since ADR-044 `--apply` takes effect immediately with no restart: flagged rows are marked `withdrawn` by the API, dropped from species tallies, and shown by neither MQTT nor the counter-top display |
| `oo detections reconcile-taxonomy` | stop stored sound categories (Engine, Human vocal, Dog, …) claiming to be birds at species rank (ADR-049). **Dry-run by default**; `--apply`, `--yes`, `--json`, `--limit`. Clears `rank`, `scientific_name` and `canonical_taxon_id`, sets `taxonomic_group` to `acoustic_event`, keeps `common_name`, never deletes a row; the originals are preserved under `native_result.taxonomy_review` |
| `oo clips purge-human-audio` | delete evidence clips of human speech and mark their `media_asset` rows reclaimed (ADR-049). **Dry-run by default**; `--apply`, `--yes`, `--json`. Detection rows are never touched |
| `oo clips reconcile-missing` | reconcile `media_asset` rows that claim a clip the disk does not have (ADR-057). **Dry-run by default**; `--apply`, `--yes`, `--json`, `--limit`. Sets `reclaimed_at` and `reclaim_reason = "missing"` — never a retention tier name, because nothing decided to give these clips up — and preserves what the row claimed under `detail.missing_reconciliation`. Deletes no file, no `media_asset` row and no `detection`. The station reports the same condition on its own as a health note and as `oo_media_missing_files`; a non-zero figure there is what should prompt this command |
| `oo clips retention` | run the tiered retention sweep manually (ADR-026). `--dry-run`, `--limit` |
| `oo refine run` | one refinement pass over stored evidence clips (ADR-045). Normally started by `open-observatory-refine.timer`, not by hand. Refuses outside 01:00–03:00 UTC unless `--force`. `--dry-run`, `--limit`, `--json`. **Writes only append-only `refinement` rows plus three bookkeeping columns; never edits a detection's species, score or `native_result`** |
| `oo refine status` | what the refiner has done, what is waiting for a human ear, and — the number the charter's retention safeguard needs — how many bat detections have **never been examined**. `--limit`, `--json` |
| `oo system-report` | host facts worth recording with a diagnostic. `--json` |
| `oo serve` | run the station. `--host`, `--port`, `--source auto\|alsa\|replay\|synthetic`, `--reload` |
| `oo config` | print effective configuration, with the resolved database DSN |

There is no `oo preflight-upgrade` command or anything like it; a much earlier
version of this document referred to one, and it does not exist in the code.

**The two `reconcile-*` commands write to the database when given `--apply`.**
Both default to dry-run, both require a confirmation, and neither has ever been
run with `--apply` against the live station. Run them with `--json` piped to a
file and read the output first.

### Updating the counter-top display, without a cable (ADR-050)

The ESP32 display carries two OTA app slots and fetches new firmware from the
station over the WebSocket it is already connected on. The station is the
distribution point: it stores one image at a time under `data/firmware/`.

From the browser: *settings* → **Display firmware** → upload
`firmware/inside-observer/.pio/build/cyd/firmware.bin`, give it the version from
`platformio.ini`, press *publish*. Displays take it as they reconnect;
*roll out now* tells the ones already connected.

From a terminal:

```bash
curl -s -X POST --data-binary @firmware/inside-observer/.pio/build/cyd/firmware.bin \
  -H 'content-type: application/octet-stream' \
  'http://<station-host>:8080/api/v1/firmware?version=0.2.1&notes=what%20changed'
curl -s http://<station-host>:8080/api/v1/firmware        # what is published
curl -s -X POST http://<station-host>:8080/api/v1/firmware/rollout
curl -s -X DELETE http://<station-host>:8080/api/v1/firmware   # withdraw it
```

Four things worth knowing before you publish:

- **The version must be strictly newer**, and dotted digits only — `0.2.1`, not
  `0.2.1-rc1`. Both ends refuse a version they cannot order rather than guessing.
  An unbumped build is a rollout that quietly does nothing.
- **The display chooses when.** It defers while someone is using it, and while
  the newest row on the feed is under a minute old.
- **It rolls itself back.** SHA-256 is checked before the image becomes bootable;
  a crash loop is undone by the bootloader; a build that runs but cannot reach
  the station within ten minutes reverts itself.
- **The image travels over plain HTTP and the digest comes from whoever supplied
  the image.** This defends against corruption, not against someone who can
  already impersonate the station on your LAN. Image signing is not implemented.

A display running pre-ADR-050 firmware reports its version as `unknown`. That is
not the same as "out of date", and the UI does not present it as such. Getting
such a display onto the two-slot partition table needs one cable flash — see
[`../../firmware/inside-observer/README.md`](../../firmware/inside-observer/README.md).

### Known operational trap: killing the service by pattern match

Do not run `pkill -f "oo serve"` (or any `pkill -f`/`pgrep -f` pattern match
on the same string) from an interactive SSH command. The SSH command itself
— `ssh host 'pkill -f "oo serve" ...'` — contains the literal text `oo
serve` in its own process argv on the target, so `pkill -f` matches and kills
the SSH session's own remote command, not just the intended service process,
with unpredictable results for which process actually dies. Use
`sudo systemctl stop open-observatory` / `restart open-observatory` instead;
systemd tracks the unit's cgroup, not a string match against argv.

### Health check

`GET /api/v1/health` on port 8080 is what `deploy.sh` polls after a restart.
It is also the right first check after any manual `systemctl restart`.

### Pausing recording for visitors (ADR-055)

When the garden is about to be full of people who never consented to a
microphone, use the **pause** control in the web UI header — not
`systemctl stop`. Stopping the service closes the ALSA device, and this station
has already lost 29 hours of recording to a device that did not come back
(HANDOVER §3a).

The split button pauses for whatever duration is selected; the caret changes the
selection (15 minutes, 1 hour, 3 hours, 6 hours, until midnight in the station's
configured timezone) and remembers it. While paused:

- no detection rows, no evidence clips, no MQTT, no display pushes;
- **live listening is refused** on both channels — `GET /api/v1/live/audio.wav`
  answers `503` with the pause banner as its detail;
- **capture keeps running.** Frames keep arriving, continuity is unbroken, the
  device is never closed.

It ends by itself at the deadline, and it survives a restart — the deadline is
persisted, so a Pi rebooted mid-pause comes back paused. The counter-top display
shows `PAUSED BY OPERATOR - RECORDING RESUMES HH:MM`, and the window appears in
`GET /api/v1/history`'s coverage as `pauses[]` / `seconds_paused` so a silent
afternoon reads as deliberate rather than as a dead microphone.

From the command line:

```bash
curl -s -X POST http://<station-host>:8080/api/v1/pause \
  -H 'content-type: application/json' -d '{"preset": "3h"}'
curl -s -X DELETE http://<station-host>:8080/api/v1/pause     # resume now
curl -s http://<station-host>:8080/api/v1/pause                # what is it doing
```

If a station is ever stuck paused with the API unreachable, clear it in the
database and restart — see the ADR-055 rollback section in
[`../architecture/ADRS.md`](../architecture/ADRS.md).

### Live listening: two transports, since ADR-019

The debug UI's GO LIVE button plays audio from `GET /api/v1/live/audio.wav` by
default — a plain `<audio>` element against a chunked WAV stream (44-byte header,
size fields `0xFFFFFFFF`, continuous 16-bit PCM). This replaced a Web Audio graph
that was silent on the operator's own laptop for reasons diagnosed but not fully
explained (ADR-019) — media-element playback worked on the same machine where Web
Audio, by any routing, did not. The original WebSocket channel,
`/api/v1/live/audio`, is unchanged and still used by other clients (a phone, without
issue). If a quiet garden (around −45 dBFS) proves too quiet over the new path,
the fix is server-side gain applied to the stream before broadcast, not a
client-side Web Audio node — the +24 dB monitor make-up gain the old client applied
is gone and has no replacement yet.

### Updating

1. `HOST=<user>@<station-host> ./deploy/deploy.sh` (add `--no-web` if only
   Python changed, `--no-deps` if only config or non-dependency source changed).
2. Watch the health poll in the script's own output; it prints recent
   `journalctl` on failure.
3. If something is wrong post-deploy, re-run `oo audio probe` and
   `oo system-report` on the target and compare against
   `docs/operations/TARGET_DIAGNOSTICS.md`.
4. **Schema changes need nothing extra.** `deploy.sh` runs `alembic upgrade head`
   itself, after the sync and before the restart. Back up
   `data/openobservatory.sqlite` first if the revision is one you have not run
   before — see "Database: SQLite by default, Alembic migrations exist" below.
5. There is no automated rollback for the SQLite schema. Rollback in
   practice is: check out the previous commit locally, re-run `deploy.sh`,
   and restart. Back up `data/` first if the previous commit's schema
   differs — `alembic downgrade` exists for development databases
   (see below) but is not a substitute for a backup on the live station.

### Backups

No backup tooling exists in this repository at present. Operators are on
their own for backing up `data/` (SQLite database, clips) and their private
`config/runtime.env`. This should be treated as a gap, not a documented
procedure, until something is built and tested.

## Database: SQLite by default, Alembic migrations exist

Per ADR-007, the default and only database in the current deployment is
SQLite at `data/openobservatory.sqlite`, selected via `OO_DATABASE_DSN` (empty
by default, which resolves to the SQLite path). PostgreSQL 16 remains the
documented production DSN target. As of ADR-035:

- `alembic/` (`env.py`, `versions/`) and `alembic.ini` at the repository root
  are a real migration environment, wired to `Settings` (the same
  `OO_DATABASE_DSN` resolution the application uses) and to the SQLAlchemy
  metadata in `db/models.py`;
- `src/open_observatory/db/session.py: create_all()` still runs at every
  application/CLI startup and still builds a correct schema for a database
  that has never been touched at all — it has **not** been removed, and new
  columns should go through an Alembic revision from here on rather than
  relying on it (full reasoning in ADR-035);
- switching `OO_DATABASE_DSN` to a PostgreSQL URL remains the intended
  one-line configuration change; the migration environment is dialect-portable
  by construction (batch mode, no dialect-specific types) but has only been
  exercised against SQLite so far, not against a real PostgreSQL 16 instance.

**`deploy.sh` runs `alembic upgrade head` on every deploy** (ADR-042), after the
sync and before the service restart, so an ordinary schema change needs no
manual step. The live station's database is at head today.

Two things still need a person:

- **A database that has never seen Alembic** — one built by `create_all()` and
  never stamped. Run `alembic stamp 0001_initial` once, by hand, before the
  first deploy that would migrate it. Never run `alembic upgrade head` from
  scratch against a database that already has the tables: the initial revision
  issues `CREATE TABLE` and will collide with what is there. The fuller
  creation/rollback workflow is in `docs/data/DATA_MODEL.md`
  ("Migrations (Alembic, ADR-035)").
- **A backup, when the revision is one you have not run before.** Copy
  `data/openobservatory.sqlite` and any `-wal`/`-shm` files next to it. No
  automated backup tool exists (below).

## Production target (unrealised): Docker Compose, PostgreSQL, Redis

`docker-compose.yml` at the repository root is explicitly labelled in its own
first line as a "specification-level Compose skeleton" that the implementation
"must not claim... is runnable yet." Its `capture` and `api` services carry
`profiles: ["implementation-required"]`, which keeps them out of any default
`docker compose up` (Compose does not start a service that only appears under
a non-active profile). The skeleton wires:

- `postgres:16` with a hardcoded development password (`change-me`) and a
  health check;
- `redis:7-alpine` with append-only persistence, matching the "Redis Streams
  for the internal job/event bus" line in the required stack, not yet
  implemented (ADR-009 describes only the in-process `EventBus`, with Redis
  Streams as "a second implementation of the same protocol" that has not been
  built);
- `capture` and `api` services referencing `services/capture/Dockerfile` and
  `services/api/Dockerfile`, neither of which exists in this repository at
  the time of writing.

ADR-008 records why this was deferred rather than built first: only the
capture process may own the ALSA device, and getting `/dev/snd`, USB
hot-plug re-enumeration and real-time scheduling right through a container
adds failure surface with no benefit while the target device work was still
being commissioned. Native execution also gives CPU/latency measurements
uncontended by container overhead — the figures in
`docs/operations/TARGET_DIAGNOSTICS.md` are only trustworthy because nothing
sits between the process and the hardware.

Do not deploy this Compose file. It will not run as checked in.

## Host preparation (current deployment)

- The commissioned target is Ubuntu 24.04.3 LTS (Noble) on a Raspberry Pi 5,
  **not** Raspberry Pi OS — see `docs/operations/TARGET_DIAGNOSTICS.md`. This
  document does not assume Raspberry Pi OS.
- Python 3.12 available as the system interpreter.
- `node`/`npm` on the *developer* machine only, for the web build; not
  required on the Pi.
- `libasound2-dev` on the Pi for `pyalsaaudio` to build from source.
- A stable ALSA device identity via `by-id` symlink or USB
  vendor:product:serial — never card index, which is demonstrated unstable
  across reboots in `TARGET_DIAGNOSTICS.md`. `deploy/99-audiomoth.rules` is
  the udev rule installed by `deploy.sh` for this.
- NTP enabled; the station operates internally in UTC and presents local time
  using the configured IANA timezone.
- The USB SSD at `data/clips` must be mounted **before** `systemctl start
  open-observatory` runs — see "Evidence storage" below. There is no
  provisioning script for a fresh host's `/etc/fstab` entry; it was added by
  hand during commissioning and is not idempotent tooling.

### Evidence storage: the USB SSD must be mounted before the service starts

Since 2026-08-08 (ADR-021), evidence clips are written to a USB SSD mounted at
`data/clips`, not the SD card — see `docs/operations/TARGET_DIAGNOSTICS.md` for the
device details (partition, UUID, `fstab` line). The database stays on the SD card.

This has one operational consequence that is easy to get wrong: **the systemd unit
runs in a mount namespace** (`ProtectHome=read-only` with
`ReadWritePaths=/home/<user>/open-observatory/data`), so a filesystem mounted on the
host *while the service is already running* is not visible inside it. Mounting (or
remounting, or replugging) the SSD always requires
`sudo systemctl restart open-observatory` afterwards to take effect — the mount
appearing in `mount`/`df` on the host is not sufficient.

`OO_CLIPS_REQUIRE_MOUNT=true` (`clips_require_mount` in `Settings`, off by default)
makes `/api/v1/health` report degraded, by name, when `data/clips` is not currently a
mount point, instead of silently falling back to writing evidence onto the SD card.
Set it once the SSD is commissioned on a given host. The service itself never
refuses to start over a missing mount — capture always wins, per the existing
synthetic-source fallback pattern — it only reports the problem loudly.

## Configuration

**Since ADR-048, the browser is the primary way to configure a station.**
Open the station's UI, click `settings`, and every field below is there, with
its help text, units, bounds and shipped default. You do not need an SSH
session, and you do not need to know the `OO_*` spelling of anything. The
sections that follow are the reference and the escape hatch, not the
recommended route.

`config/example.env` is the checked-in template; copy it to
`config/runtime.env` on the target and edit in place (see the operator-owned
state section above — do not let a sync remove it). All settings are read by
`Settings` in `src/open_observatory/config.py` with an `OO_` prefix. The web UI
writes that same file, atomically and preserving your comments, so a hand edit
and a UI edit are one configuration rather than two that can disagree.

### Which settings take effect immediately, and which need a restart

Three tiers, declared in `src/open_observatory/site_settings.py` and enforced
by tests:

- **live** — in force the moment you press save. Most of these the station
  re-reads from its settings object on every use; the rest
  (`src/open_observatory/tuning.py`) are pushed into the object that holds
  them — a spectrogram encoder's dB window, a detector's thresholds, the clip
  manager, the retention sweeper.
- **restart-pinned** — saved and reported immediately, applied at the next
  start. Coordinates, because they bind into the BirdNET range filter and the
  night schedule when detectors start; and capture geometry, because
  re-negotiating a rate or a ring depth means tearing capture down, and capture
  always wins. The UI, `GET /api/v1/settings` and `/api/v1/health` all name
  these as "saved, not yet in force" until you restart.
- **not editable from a browser** — listed at the bottom of the settings page
  with the hazard, and at the end of this section. These are `runtime.env` and
  a restart.

A live-tier setting whose target does not exist — an ultrasonic spectrogram
floor on a station capturing at 48 kHz — is reported as pending too. The
station will not claim something is in force when nothing is using it.

### Restarting to apply a pinned change

```bash
sudo systemctl restart open-observatory
curl -s http://<station-host>:8080/api/v1/health \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["notes"])'
# expect the "settings saved but not yet in force" note to be gone
```

Note the obvious cost: a restart interrupts capture for as long as it takes the
device to be reopened. Batch pinned changes into one save and one restart.

### Tuning a noisy site

The knobs, in the order to reach for them, all live-tier:

1. `ultrasonic_min_snr_db` — how far above the tracked band noise floor a pulse
   must sit. The first thing to raise when a mechanical noise source (a plant
   against a shed, a fan, rain on a roof) is producing false bat passes.
2. `ultrasonic_min_pulses_per_pass` — periodic mechanical noise rarely produces
   a long pulse train; a genuine pass does.
3. `ultrasonic_band_hz` — raise the low edge to ignore a source that only
   reaches the bottom of the band.
4. `activity_min_snr_db` / `activity_band_hz` — the same two moves on the
   audible detector.
5. `spectrogram_floor_db` / `ultrasonic_spectrogram_floor_db` — *seeing*
   rather than detecting. Raising the floor pushes a noisy background to black.
   These change the picture only; they change nothing about what is detected.
6. `birdnet_threshold_out_of_range` and `birdnet_plausibility_floor` — for
   implausible species rather than noise (ADR-032).

Every one of these carries its measured default in the UI as a one-click
reset, so an experiment always has a way back. Note that none of this fixes a
mounting problem: it makes the station's *reports* survive one. Move the
microphone.

### Tuning BirdNET on evidence: what it rejected (ADR-052)

The opposite complaint — birds you can *hear* and no detections for them — is
not answered by any of the above, because the suppression counters say how
many candidates were refused and never which. `GET
/api/v1/detectors/near-misses` says which, at what score, with what occurrence
prior, in which band, against which bar; the same thing appears in the web UI
under `diagnostics` as **Rejected candidates**, directly under the detector
panel. Metadata only: no audio is kept for a rejected candidate and nothing is
persisted — the record is in memory and dies with the process.

```bash
# The whole picture, as an operator reads it.
curl -s 'http://<station-host>:8080/api/v1/detectors/near-misses' | python3 -m json.tool

# The one-line version: what got thrown away most, and how close it came.
curl -s 'http://<station-host>:8080/api/v1/detectors/near-misses' | python3 -c '
import json,sys
for d in json.load(sys.stdin)["detectors"]:
    print(d["plugin_id"], d["rejected_total"], "rejected /", d["admitted_total"], "kept")
    for s in d["species"][:15]:
        print(f"  {s[\"common_name\"]:34.34} {s[\"rejected\"]:5d}  best {s[\"best_score\"]:.3f}"
              f"  short by {s[\"shortfall\"]}  {s[\"band\"]}  prior {s[\"occurrence_probability\"]}")'

# Where the rejected scores sit, per band -- the histogram that actually
# decides whether moving a bar buys anything.
curl -s 'http://<station-host>:8080/api/v1/detectors/near-misses?limit=0&species_limit=0' \
  | python3 -c '
import json,sys
for d in json.load(sys.stdin)["detectors"]:
    for b in d["bands"]:
        if b["rejected"]:
            print(f"{b[\"band\"]:16.16} bar {b[\"threshold\"]} ", b["histogram"]["counts"])'
```

Read it as: everything to the *right* of the bar's bin is what a lower bar
would admit. If the mass is all at 0.15 and the bar is 0.55, lowering the bar
buys noise, not birds — the model is not nearly identifying those. If there is
a pile between 0.45 and 0.55 and it is named `Eurasian Blackbird` with a prior
of 0.93, that is a bar set too high for this garden.

`birdnet_near_miss_ring` (live-tier, in the browser) sets how many individual
rejections are held. Raise it for a tuning session; 0 keeps the histograms and
the per-species table and stops keeping individual rows.

### The full settings reference

Generated from the code — regenerate with
`PYTHONPATH=src python scripts/settings_table.py` after adding a field.

#### Station

| setting | tier | default | notes |
|---|---|---|---|
| `station_name` | live | `Garden Observatory` | station name |
| `timezone` | live | `UTC` | timezone |
| `latitude` | restart-pinned | `None` ° | latitude |
| `longitude` | restart-pinned | `None` ° | longitude |
| `clips_require_mount` | live | `False` | evidence storage must be its own mount |

#### Capture

| setting | tier | default | notes |
|---|---|---|---|
| `source` | restart-pinned | `auto` | **warns before saving.** audio source |
| `audio_device` | restart-pinned | `None` | **warns before saving.** capture device key |
| `preferred_sample_rates` | restart-pinned | `384000,250000,192000,96000,48000` Hz | preferred sample rates |
| `preferred_formats` | restart-pinned | `S16_LE,S32_LE` | preferred sample formats |
| `capture_channels` | restart-pinned | `1` | channels |
| `capture_block_ms` | restart-pinned | `100` ms | capture block |
| `capture_buffer_ms` | restart-pinned | `500.0` ms | ALSA ring depth |
| `native_ring_seconds` | restart-pinned | `120` s | native ring buffer |
| `audible_ring_seconds` | restart-pinned | `120` s | audible ring buffer |
| `audible_sample_rate` | restart-pinned | `48000` Hz | derived audible rate |
| `hardware_recheck_s` | live | `30.0` s | recheck for the microphone every |
| `reopen_backoff_min_s` | live | `1.0` s | reopen backoff, minimum |
| `reopen_backoff_max_s` | live | `30.0` s | reopen backoff, maximum |

#### Live view

| setting | tier | default | notes |
|---|---|---|---|
| `spectrogram_floor_db` | live (pushed) | `-95.0` dB | audible floor |
| `spectrogram_ceiling_db` | live (pushed) | `-15.0` dB | audible ceiling |
| `ultrasonic_spectrogram_floor_db` | live (pushed) | `-85.0` dB | ultrasonic floor |
| `ultrasonic_spectrogram_ceiling_db` | live (pushed) | `-30.0` dB | ultrasonic ceiling |
| `spectrogram_fft` | restart-pinned | `2048` | FFT size |
| `spectrogram_hop_ms` | restart-pinned | `24.0` ms | column hop |
| `spectrogram_bins` | restart-pinned | `192` | frequency bins |
| `spectrogram_min_hz` | restart-pinned | `80.0` Hz | lowest frequency shown |
| `spectrogram_max_hz` | restart-pinned | `15000.0` Hz | highest frequency shown |
| `spectrogram_history_columns` | restart-pinned | `2400` | retained columns |
| `spectrogram_backfill_s` | live | `30.0` s | history sent on connect |
| `spectrogram_encode_min_viewers` | live | `1` | viewers required before encoding |
| `spectrogram_keep_audible_warm` | live | `False` | keep the audible encoder warm |

#### Audible detection

| setting | tier | default | notes |
|---|---|---|---|
| `activity_enabled` | restart-pinned | `True` | activity detector |
| `activity_min_snr_db` | live (pushed) | `18.0` dB | activity SNR threshold |
| `activity_min_duration_ms` | live (pushed) | `60.0` ms | minimum event duration |
| `activity_band_hz` | live (pushed) | `1200.0,11000.0` Hz | activity band |
| `birdnet_enabled` | restart-pinned | `True` | BirdNET |
| `birdnet_min_confidence` | live (pushed) | `0.12` | BirdNET minimum confidence |
| `birdnet_plausibility_floor` | live (pushed) | `0.0005` | plausibility floor |
| `birdnet_common_prior` | live (pushed) | `0.15` | common-species prior |
| `birdnet_range_threshold` | live (pushed) | `0.03` | out-of-range prior |
| `birdnet_threshold_in_range` | live (pushed) | `0.55` | confidence bar: in range |
| `birdnet_threshold_uncommon` | live (pushed) | `0.75` | confidence bar: uncommon |
| `birdnet_threshold_out_of_range` | live (pushed) | `0.9` | confidence bar: out of range |
| `birdnet_near_miss_ring` | live (pushed) | `200` | near-miss records kept |
| `birdnet_window_stride_s` | restart-pinned | `1.5` s | BirdNET window stride |
| `birdnet_use_location_filter` | restart-pinned | `False` | use the range model |

#### Ultrasonic detection

| setting | tier | default | notes |
|---|---|---|---|
| `ultrasonic_enabled` | restart-pinned | `True` | ultrasonic pass detector |
| `ultrasonic_min_snr_db` | live (pushed) | `12.0` dB | pulse SNR threshold |
| `ultrasonic_min_pulses_per_pass` | live (pushed) | `3` | minimum pulses per pass |
| `ultrasonic_band_hz` | live (pushed) | `15000.0,125000.0` Hz | ultrasonic band |
| `ultrasonic_min_pulse_ms` | live (pushed) | `1.5` ms | minimum pulse length |
| `ultrasonic_max_pulse_ms` | live (pushed) | `40.0` ms | maximum pulse length |
| `ultrasonic_merge_gap_ms` | live (pushed) | `2.0` ms | pulse merge gap |
| `ultrasonic_pass_gap_s` | live (pushed) | `1.5` s | pass gap |
| `ultrasonic_buzz_max_interval_ms` | live (pushed) | `12.0` ms | feeding buzz: maximum interval |
| `ultrasonic_buzz_min_pulses` | live (pushed) | `5` | feeding buzz: minimum pulses |
| `ultrasonic_buzz_interval_ratio` | live (pushed) | `0.4` | feeding buzz: interval collapse ratio |
| `ultrasonic_schedule` | restart-pinned | `always` | when to run |
| `ultrasonic_schedule_dusk_margin_min` | restart-pinned | `30.0` min | start before dusk |
| `ultrasonic_schedule_dawn_margin_min` | restart-pinned | `30.0` min | stop after dawn |

#### Making ultrasound listenable

| setting | tier | default | notes |
|---|---|---|---|
| `ultrasonic_audible_method` | live (pushed) | `both` | rendering method |
| `ultrasonic_time_expansion_factor` | live (pushed) | `0.0` | time-expansion factor |
| `ultrasonic_target_hz` | live (pushed) | `4000.0` Hz | target frequency |
| `ultrasonic_highpass_hz` | live (pushed) | `12000.0` Hz | high-pass before rendering |
| `ultrasonic_heterodyne_bandwidth_hz` | live (pushed) | `5000.0` Hz | heterodyne bandwidth |
| `ultrasonic_audible_max_s` | live (pushed) | `60.0` s | maximum rendered length |
| `ultrasonic_audible_min_peak_hz` | live (pushed) | `15000.0` Hz | render anything peaking above |
| `ultrasonic_live_tune_hz` | live | `45000.0` Hz | live monitor default tuning |

#### Evidence clips

| setting | tier | default | notes |
|---|---|---|---|
| `clips_enabled` | live | `True` | write evidence clips |
| `clip_pre_roll_s` | live (pushed) | `3.0` s | pre-roll |
| `clip_post_roll_s` | live (pushed) | `3.0` s | post-roll |
| `clip_max_s` | live (pushed) | `12.0` s | maximum clip length |
| `clip_min_score` | live (pushed) | `0.25` | minimum score to clip |
| `clip_plugins` | live (pushed) | `birdnet-v2.4,ultrasonic-pass-v1` | **warns before saving.** detectors that produce clips |
| `clip_human_audio` | live (pushed) | `False` | **warns before saving.** keep audio of human voices |
| `clip_max_per_minute` | live (pushed) | `20` /min | clip rate limit |
| `clip_max_total_gb` | live (pushed) | `20.0` GB | clip directory budget |
| `clip_min_free_gb` | live (pushed) | `5.0` GB | stop clipping below free space |
| `clip_retention_days` | live (pushed) | `30` days | clip manager retention |

#### Privacy

| setting | tier | default | notes |
|---|---|---|---|
| `pause_presets` | live | `15m,1h,3h,6h,until-midnight` | pause durations offered |
| `pause_default_preset` | live | `1h` | pre-selected duration |

#### Retention

| setting | tier | default | notes |
|---|---|---|---|
| `retention_enabled` | live | `True` | tiered retention |
| `retention_native_days` | live (pushed) | `7` days | keep full-rate audio for |
| `retention_audible_only_days` | live (pushed) | `30` days | keep every audible clip for, unless kept (ADR-061) |
| `retention_watermark_ratio` | live (pushed) | `0.85` | reclaim above disk usage |
| `retention_batch_size` | live (pushed) | `200` | assets per sweep |
| `retention_batch_budget_s` | live (pushed) | `1.5` s | sweep time budget |
| `retention_interval_s` | live | `300.0` s | sweep interval |

#### Overnight refinement

| setting | tier | default | notes |
|---|---|---|---|
| `refinement_enabled` | live | `True` | overnight refinement |
| `refinement_window_start_hour_utc` | live | `1` | window start (UTC hour) |
| `refinement_window_end_hour_utc` | live | `3` | window end (UTC hour) |
| `refinement_max_items` | live | `1200` | maximum items per pass |
| `refinement_max_seconds` | live | `5400.0` s | wall-clock budget per pass |
| `refinement_trim_s` | live | `1.5` s | seconds classified per clip |
| `refinement_min_det_prob` | live | `0.05` | minimum detection probability |
| `refinement_threads` | live | `2` | inference threads |
| `refinement_refiner` | live | `batdetect2-cascade` | refiner |

#### Counter-top display

| setting | tier | default | notes |
|---|---|---|---|
| `display_channel_heartbeat_s` | live | `10.0` s | heartbeat interval |
| `display_channel_snapshot_rows` | live | `6` | rows sent on connect |
| `display_channel_queue_max` | live | `64` | queue depth |
| `display_ota_offer_on_connect` | live | `True` | offer firmware updates on connect |

#### MQTT / Home Assistant

| setting | tier | default | notes |
|---|---|---|---|
| `mqtt_enabled` | live | `False` | publish to MQTT |
| `mqtt_host` | live | `localhost` | broker host |
| `mqtt_port` | live | `1883` | broker port |
| `mqtt_tls` | live | `False` | TLS |
| `mqtt_tls_insecure` | live | `False` | **warns before saving.** skip TLS certificate verification |
| `mqtt_username` | live | `None` | username |
| `mqtt_password` | live | `None` | password |
| `mqtt_client_id` | live | `open-observatory` | client id |
| `mqtt_topic_prefix` | live | `openobservatory` | topic prefix |
| `mqtt_qos` | live | `1` | QoS |
| `mqtt_retain_state` | live | `True` | retain state topics |
| `mqtt_discovery_enabled` | live | `True` | Home Assistant discovery |
| `mqtt_discovery_prefix` | live | `homeassistant` | discovery prefix |
| `mqtt_publish_unidentified` | live | `False` | publish unidentified events |
| `mqtt_bat_activity_window_s` | live | `900.0` s | bat activity sensor window |
| `mqtt_health_publish_interval_s` | live | `15.0` s | health republish interval |
| `mqtt_queue_depth` | live | `256` | publisher queue depth |
| `mqtt_reconnect_min_s` | live | `1.0` s | reconnect backoff, minimum |
| `mqtt_reconnect_max_s` | live | `60.0` s | reconnect backoff, maximum |
| `mqtt_keepalive_s` | live | `30` s | keepalive |

#### Advanced

| setting | tier | default | notes |
|---|---|---|---|
| `detector_queue_depth` | restart-pinned | `16` | live detector queue depth |
| `deferred_enabled` | restart-pinned | `False` | deferred detector queue |
| `deferred_queue_depth` | restart-pinned | `512` | deferred queue depth |
| `deferred_shutdown_drain_timeout_s` | live | `5.0` s | deferred drain timeout on shutdown |
| `replay_loop` | restart-pinned | `True` | loop the replay file |
| `replay_speed` | restart-pinned | `1.0` | replay speed |
| `synthetic_scene` | restart-pinned | `dawn-chorus` | synthetic scene |
| `synthetic_sample_rate` | restart-pinned | `48000` Hz | synthetic sample rate |
| `log_level` | restart-pinned | `INFO` | log level |
| `log_json` | restart-pinned | `False` | JSON logs |
| `metrics_enabled` | restart-pinned | `True` | Prometheus /metrics |

#### Setup

| setting | tier | default | notes |
|---|---|---|---|
| `setup_completed` | live | `False` | first-run flow completed |

#### Not editable from a browser

| setting | why |
|---|---|
| `auth_argon2_memory_cost_kib` | part of the authentication configuration (see auth_enabled). |
| `auth_argon2_parallelism` | part of the authentication configuration (see auth_enabled). |
| `auth_argon2_time_cost` | part of the authentication configuration (see auth_enabled). |
| `auth_bootstrap_username` | part of the authentication configuration (see auth_enabled). |
| `auth_cookie_secure` | part of the authentication configuration (see auth_enabled). |
| `auth_enabled` | authentication must not be editable through the surface it protects: an unauthenticated session could disable the gate, and a half-configured one could lock every operator out with no recovery path but SSH. Set OO_AUTH_ENABLED in config/runtime.env. |
| `auth_login_rate_limit_attempts` | part of the authentication configuration (see auth_enabled). |
| `auth_login_rate_limit_window_s` | part of the authentication configuration (see auth_enabled). |
| `auth_password_min_length` | part of the authentication configuration (see auth_enabled). |
| `auth_public_read_paths` | part of the authentication configuration (see auth_enabled). |
| `auth_session_cookie_name` | part of the authentication configuration (see auth_enabled). |
| `auth_session_ttl_hours` | part of the authentication configuration (see auth_enabled). |
| `bind_host` | changing where the API listens, from the API, is a remote-hands lockout: the next request goes to an address that no longer answers and there is no way back except SSH. |
| `bind_port` | same lockout as bind_host: the browser cannot follow the station to a new port. |
| `birdnet_model_dir` | chooses which model binary the station loads. Selecting the file a process loads and executes is not a settings decision made from a form; use 'oo models fetch', which records provenance and the licence acceptance. |
| `data_dir` | repointing storage under a running station orphans the database mid-write and strands every existing clip; this is a stop, move, migrate, start operation. |
| `database_dsn` | the same shutdown-and-migrate operation as data_dir, plus a DSN can carry credentials for a host this station has no business reaching. |
| `replay_path` | the replay source plays a file of the operator's choosing into the live audio stream and the spectrogram. From a browser -- on a station whose shipped default is anonymous LAN access -- that is an arbitrary-file-read tool wearing a settings field. The 'replay' source is likewise not offered in the source picker. |
| `runtime_env_path` | this is the settings store itself. Repointing it makes the UI write to a file the process does not read, which is exactly the two-configurations-that-disagree failure the whole mechanism exists to prevent. |
| `web_dist` | the API serves this directory's contents over HTTP. Pointing it at an arbitrary path publishes that path to anyone on the LAN. |

`oo config` prints the effective configuration on the target, including the
resolved database DSN, and is the authoritative way to check what a given
`runtime.env` actually produced.

### A misspelled `OO_*` key does nothing, silently

`Settings` is defined with `extra="ignore"`, so an unrecognised `OO_*`
environment variable is accepted and has no effect rather than raising. `oo
config` on the target is the only way to confirm what a given `runtime.env`
actually produced.

**Previously recorded here and now resolved:** `config/example.env` used to
carry four keys with no corresponding `Settings` field — `OO_POSTGRES_DSN`,
`OO_REDIS_URL`, `OO_MQTT_ENABLED` and `OO_MQTT_URL`. Three have since been
removed from the template and `OO_MQTT_ENABLED` became a real field when the
MQTT publisher shipped (ADR-025). Re-checked mechanically on 2026-08-09: **every
key in `config/example.env` now maps to a declared `Settings` field.**

`config/example.env` is a curated subset, not the full surface —
`src/open_observatory/config.py` declares well over a hundred fields and is the
complete reference.

### Resolved: the tuple-typed settings no longer crash the station

**Re-verified on 2026-08-09**, because the web UI can now write these keys and
an operator reading the historical warning below would reasonably avoid a
feature that is safe:

```
$ OO_PREFERRED_SAMPLE_RATES=384000,48000 OO_CLIP_PLUGINS=birdnet-v2.4 \
  OO_ACTIVITY_BAND_HZ=800,9000 python -c \
  'from open_observatory.config import Settings; s=Settings(_env_file=None); \
   print(s.preferred_sample_rates, s.clip_plugins, s.activity_band_hz)'
(384000, 48000) ('birdnet-v2.4',) (800.0, 9000.0)
```

All five tuple-typed fields (`preferred_sample_rates`, `preferred_formats`,
`clip_plugins`, `activity_band_hz`, `ultrasonic_band_hz`) now carry `NoDecode`
so this project's comma-separated parsing runs instead of `pydantic-settings`'
JSON decode, and `tests/test_config.py` covers them. The settings page writes
the same comma-separated form. **The historical account is retained below**,
unchanged, because the failure mode is worth recognising if it ever returns.

### The historical trap: three tuple-typed settings crashed the station if you set them

`OO_PREFERRED_SAMPLE_RATES`, `OO_PREFERRED_FORMATS` and `OO_CLIP_PLUGINS` are
tuple-typed. `pydantic-settings` tries to JSON-decode a tuple-typed field's raw
env value before this project's plain comma-separated parsing is ever reached,
so **setting** one of them (as opposed to leaving it at its default) raises a
`SettingsError` at startup and the station does not come up at all. Reproduced
on 2026-08-09:

```
$ OO_PREFERRED_SAMPLE_RATES=384000,48000 python -c 'from open_observatory.config import Settings; Settings()'
SettingsError: error parsing value for field "preferred_sample_rates" from source "EnvSettingsSource"
```

Was pre-existing and unfixed at the time of writing; `OO_AUTH_PUBLIC_READ_PATHS`
never had the problem because it declared `NoDecode`. The fix was to give the
other four the same annotation — see the re-verification above.

### The development station's `runtime.env`, as it now stands

Because `runtime.env` is gitignored (see above), its actual contents are not visible
from the repository. As of the 2026-08-08 storage work, the station's copy sets, in
addition to its identity and coordinates:

| Key | Value | Why |
|---|---|---|
| `OO_CLIPS_REQUIRE_MOUNT` | `true` | report degraded rather than silently write evidence to the SD card if the SSD is ever unmounted |
| `OO_CLIP_MAX_PER_MINUTE` | `20` | restored to the `Settings` default now the SSD can sustain it; was temporarily `6` while still on the SD card |
| `OO_ULTRASONIC_AUDIBLE_METHOD` | `both` | restored to the `Settings` default (time-expansion and heterodyne); was temporarily `heterodyne` only |
| `OO_CLIP_MAX_TOTAL_GB` | `300` | raised from the `Settings` default of `20` against 458 GB of usable SSD space |
| `OO_ULTRASONIC_SCHEDULE` | `night` | unchanged by this work — gates the ultrasonic detector to civil dusk-dawn |

If these are ever reduced again, it will be for the same reason they were reduced
before: the storage device underneath `data/clips` cannot sustain the write rate.
Check `du -sh data/clips` and the SSD's free space before assuming that.

## Authentication (ADR-034, closes ADR-015)

**Off by default.** `OO_AUTH_ENABLED` (`auth_enabled` in `Settings`) defaults
to `false`. On a fresh install or an upgrade of an existing station, nothing
changes until an operator sets it explicitly — the station keeps the
anonymous read/write behaviour it has always had. `GET /api/v1/health`'s
`auth` object always reports `{"enabled": false}` while it is off (never
omitted), and a structured `auth.disabled` warning is logged once at every
startup, so this is visible rather than merely documented.

**Turning it on.**

```
OO_AUTH_ENABLED=true
```

in `config/runtime.env`, then restart the service. On the next startup with
no existing accounts, one is created (`OO_AUTH_BOOTSTRAP_USERNAME`, default
`operator`) with a random password **printed once** to the service's stdout
— `journalctl -u open-observatory -n 60` immediately after the restart that
enables auth is the only place to find it. It is never written to this
repository, `config/example.env`, or any other file. The web UI forces a
password change on that account's first login; a machine client that logs in
via `POST /api/v1/auth/login` directly and ignores `must_change_password` in
the response is not currently blocked from continuing to use the generated
password (see ADR-034's bootstrap note).

**What this does and does not protect against — stated exactly, not
generously.** It stops another device or person on the same LAN from
reading or changing station state with no credential at all, which is the
gap ADR-015 recorded as a "real security consequence." It does **not**
protect a session cookie or an API token from anything that can observe LAN
traffic: this station is served over plain HTTP, and nothing in this
codebase terminates TLS. `auth_cookie_secure` (`OO_AUTH_COOKIE_SECURE`)
defaults to `false` for exactly that reason — marking the session cookie
`Secure` on a non-HTTPS origin makes the browser silently refuse to ever
send it back, which turns a working login into one that appears to succeed
and then authenticates nothing. Only set `OO_AUTH_COOKIE_SECURE=true` once a
reverse proxy or similar is terminating TLS in front of this station on this
network path. Until then, treat a session cookie or API token exactly like
the plaintext HTTP that carries it: readable by anything already positioned
on the LAN, same as ADR-015 always implied.

**The ESP32 counter-top display's exemption.** `firmware/inside-observer` reads
`/api/v1/display` (its WebSocket push channel since ADR-038) and, when that is
down, polls `GET /api/v1/detections` and `GET /api/v1/health` — with no way to
carry a credential, and it cannot be reflashed as part of an ordinary station
upgrade. Those paths (plus `GET /metrics`, scraped by Prometheus with no
login flow of its own) stay reachable with **no credential** even when auth
is enabled — `/api/v1/health` and `/metrics` unconditionally, and
`GET /api/v1/detections` plus `/api/v1/display` via the configurable
`auth_public_read_paths` setting (default: exactly those two). `/api/v1/display`
is a WebSocket upgrade rather than a GET, so the HTTP gate never sees it; its
handler consults the same list, so the display's two transports are exempt
together or not at all. Note that the push channel leaks *less* than the polled
one it replaced: it carries species names and timestamps but **no scores**, no
media, no UUIDs and no detector metadata. This means recent
detections (species, timestamps, scores — not clip audio, not station
coordinates, not history/export/anything else) remain readable by anything
on the LAN even with auth turned on, until a future firmware update adds
bearer-token support and `auth_public_read_paths` is cleared. See ADR-034
for the full trade-off and the firmware follow-up this implies.

**`deploy/deploy.sh` is unaffected.** Its health-check loop polls
`http://127.0.0.1:8080/api/v1/health` with no credential; that endpoint is
hardcoded into the auth gate's always-public set independent of any
setting, specifically so this script keeps working unchanged whether or not
`auth_enabled` is on.

**API tokens for other machine clients** (scripts, a future firmware
revision, monitoring) are created by an authenticated operator via
`POST /api/v1/auth/tokens` (through the web UI once implemented there, or
directly against the API with a valid session cookie) and sent as
`Authorization: Bearer <token>` on subsequent requests. Each token is shown
in full exactly once at creation and stored server-side only as a SHA-256
hash; `DELETE /api/v1/auth/tokens/{id}` revokes one immediately.

**Rollback.** Set `OO_AUTH_ENABLED=false` and restart — the station returns
to anonymous access immediately; no data is lost (the `user`/`auth_session`/
`api_token` tables are simply no longer consulted). If an operator manages
to lock themselves out while auth is on (lost password, no working session),
the same rollback restores access; `oo config` on the target confirms the
setting took effect.

## Soak testing

The acceptance criteria require a continuous 72-hour soak test on the target
device before the system may be described as complete (see
`docs/delivery/ACCEPTANCE_CRITERIA.md`). `TARGET_DIAGNOSTICS.md` records
that one was run 2026-08-10 to 2026-08-13 and **failed** its continuity
criterion (99.865% against ≥ 99.9%); a re-run is needed once ADR-060 and
ADR-061 are deployed and verified. Capture the following over the run:

- frame continuity and gaps (frames captured ÷ frames elapsed time implies);
- USB disconnect/reconnect behaviour;
- CPU temperature/throttling;
- memory high-water mark against `MemoryMax=6G`;
- SD card/SSD writes and free-space trend;
- detector queue lag and dropped-window counts;
- worker crash/restart count (`Restart=always` will mask a crash loop unless
  explicitly counted);
- evidence extraction misses;
- database growth (`data/openobservatory.sqlite` size);
- false health-check failures.

## Commissioning output

`docs/operations/TARGET_DIAGNOSTICS.md` is the working example of a
commissioning report: hardware identity, negotiated audio profile, firmware
version, measured resampler and capture timing, per-detector runtime, live
channel delivery over Wi-Fi, and a plainly stated list of known limitations.
Regenerate the machine-readable portion with
`oo audio probe --json --write docs/operations/probe.json` and update the
prose sections by hand against a fresh run of `oo audio test-capture`,
`oo audio resample-check` and `oo system-report`.
