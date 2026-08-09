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
/home/observer/open-observatory/        REMOTE_DIR, synced source + venv
  .venv/                             created by deploy.sh, not synced
  config/
    runtime.env                      operator-owned, NOT in the repo, NOT synced by rsync --delete
  data/                               ReadWritePaths for the systemd unit
    clips/                           evidence clips — a mounted USB SSD since 2026-08-08, see below
    clips.sdcard-backup/             pre-migration clips left on the SD card, retained pending deletion
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
4. installs `deploy/open-observatory.service` and `deploy/99-audiomoth.rules`,
   reloads `udev` and `systemd`, and does `enable --now` followed by
   `restart`;
5. polls `http://127.0.0.1:8080/api/v1/health` over SSH for up to 60 seconds
   and prints recent `journalctl` output if the service does not come up
   healthy in that time.

It is idempotent — safe to run repeatedly — and takes flags:

| Invocation | Effect |
|---|---|
| `./deploy/deploy.sh` | full deploy |
| `./deploy/deploy.sh --no-web` | skip the `npm` build (use the UI assets already on the target) |
| `./deploy/deploy.sh --no-deps` | skip `pip install`, just resync source and restart |
| `HOST=user@host ./deploy/deploy.sh` | target a machine other than the default `station.example` |

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

The systemd unit loads it with `EnvironmentFile=-/home/observer/open-observatory/config/runtime.env`
— the leading `-` means a missing file is not an error, so a freshly
provisioned host with no `runtime.env` still starts (with defaults from
`config/example.env`/`Settings`, not with the real station's identity).

Because it is gitignored, `runtime.env` is never backed up by the deploy
mechanism and never travels with `git`. Operators are responsible for keeping
their own copy. There is no backup tooling for it in this repository at
present.

### Excludes applied on every sync

In addition to `config/runtime.env`, `deploy.sh` excludes: `.git`, `data`,
`.env`, `.venv`, `node_modules`, `web/node_modules`, `__pycache__`, `*.pyc`,
`.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `models/*.tflite` and
`models/*.txt`. The model exclusions matter operationally: model assets
fetched on the target with `oo models fetch` are not overwritten or deleted
by a later deploy, and are not shipped from the developer machine either
(ADR-006 — model licences differ from the code's and are not bundled).

### The systemd unit

`deploy/open-observatory.service` runs `ExecStart=.venv/bin/oo serve` as user
`observer`, group `observer`, with `SupplementaryGroups=audio plugdev` for `/dev/snd`
and AudioMoth `hidraw` access. Relevant settings and why they are there:

| Setting | Value | Purpose |
|---|---|---|
| `Nice=-5` | mild priority boost | capture must not be starved by detector inference; full `SCHED_FIFO` was judged unnecessary and riskier |
| `Restart=always`, `RestartSec=5` | | automatic recovery from crashes |
| `MemoryMax=6G` | hard cap | bounds the process on a machine with ~7.8 GiB total |
| `NoNewPrivileges=true` | | no privilege escalation |
| `ProtectSystem=full` | | most of the filesystem read-only to the service |
| `ProtectHome=read-only` | | home directories read-only, including the working directory's parent |
| `ReadWritePaths=/home/observer/open-observatory/data` | | the one path the service may write, explicitly carved out of `ProtectHome` |
| `PrivateTmp=true`, `ProtectKernelTunables=true`, `ProtectControlGroups=true`, `RestrictSUIDSGID=true` | | further privilege reduction per technical spec §13 |
| `StandardOutput=journal`, `StandardError=journal` | | logs go to the journal, not files, so log rotation is `journald`'s problem, not this project's |

### Operating commands

```bash
# deploy (from the developer machine, repo root)
./deploy/deploy.sh                     # full deploy to station.example
./deploy/deploy.sh --no-web            # skip the UI build
HOST=user@otherhost ./deploy/deploy.sh # different target

# service control (run these, not process signals, from an SSH session — see trap below)
ssh station.example sudo systemctl status open-observatory
ssh station.example sudo systemctl restart open-observatory
ssh station.example sudo systemctl stop open-observatory     # required before probing hardware directly; only one process may own the mic
ssh station.example sudo journalctl -u open-observatory -f
ssh station.example sudo journalctl -u open-observatory -n 100 --no-pager

# on-device diagnostics (see docs/operations/TARGET_DIAGNOSTICS.md for recorded output)
ssh station.example '~/open-observatory/.venv/bin/oo audio probe'
ssh station.example '~/open-observatory/.venv/bin/oo audio test-capture --seconds 8'
ssh station.example '~/open-observatory/.venv/bin/oo audio resample-check'
ssh station.example '~/open-observatory/.venv/bin/oo audiomoth info'   # switch must be in USB/OFF
ssh station.example '~/open-observatory/.venv/bin/oo models status'
ssh station.example '~/open-observatory/.venv/bin/oo system-report'
ssh station.example '~/open-observatory/.venv/bin/oo config'          # effective settings, resolved DSN

# test suite on the target
ssh station.example 'cd ~/open-observatory && .venv/bin/pytest'
```

The full operator CLI surface, verified against `src/open_observatory/cli.py`,
is: `oo audio probe`, `oo audio test-capture`, `oo audio resample-check`,
`oo models status`, `oo models fetch`, `oo audiomoth info`,
`oo system-report`, `oo serve`, `oo config`. There is no `oo preflight-upgrade`
command or anything like it; an earlier version of this document referred to
one, and it does not exist in the code.

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

1. `./deploy/deploy.sh` (add `--no-web` if only Python changed, `--no-deps`
   if only config or non-dependency source changed).
2. Watch the health poll in the script's own output; it prints recent
   `journalctl` on failure.
3. If something is wrong post-deploy, re-run `oo audio probe` and
   `oo system-report` on the target and compare against
   `docs/operations/TARGET_DIAGNOSTICS.md`.
4. **If this deploy changes `src/open_observatory/db/models.py`**, run the
   database migration first, before restarting the service — see "Database:
   SQLite by default, Alembic migrations exist" below. `deploy.sh` does not
   do this automatically yet (tracked as follow-up work in ADR-035); it is a
   manual step until it is wired into the script.
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

**Before restarting the service after any deploy that changed
`db/models.py`:**

- **First-ever migration on this database** (it was built by `create_all()`
  and has never seen Alembic — this is the live station's case today):
  `alembic stamp 0001_initial`, then `alembic upgrade head`. Never run
  `alembic upgrade head` from scratch against a database that already has
  the tables — the initial revision issues `CREATE TABLE` and will collide
  with what is already there.
- **A database already on a later Alembic revision:** `alembic upgrade head`
  directly.
- Check which case you're in and see the fuller creation/rollback workflow
  in `docs/data/DATA_MODEL.md` ("Migrations (Alembic, ADR-035)").
- Back up `data/openobservatory.sqlite` (and any `-wal`/`-shm` files next to
  it) before running either sequence against the station. No automated
  backup tool exists yet (below); this is a manual step.

`deploy.sh` does not run migrations automatically. Wiring an
`alembic upgrade head` step into it (after sync, before the service
restart) is the natural next step and is unimplemented — do it as a
follow-up, not silently inside an unrelated change, since it changes what a
routine deploy does to a live database.

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
`ReadWritePaths=/home/observer/open-observatory/data`), so a filesystem mounted on the
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

`config/example.env` is the checked-in template; copy it to
`config/runtime.env` on the target and edit in place (see the operator-owned
state section above — do not let a sync remove it). All settings are read by
`Settings` in `src/open_observatory/config.py` with an `OO_` prefix.

`oo config` prints the effective configuration on the target, including the
resolved database DSN, and is the authoritative way to check what a given
`runtime.env` actually produced.

### Unmapped keys in `config/example.env`

`Settings` is defined with `extra="ignore"`, so unrecognised `OO_*`
environment variables are silently accepted and do nothing rather than
raising an error. Checking `config/example.env` against the fields declared
on `Settings` in `src/open_observatory/config.py` shows four keys with no
corresponding field:

- `OO_POSTGRES_DSN`
- `OO_REDIS_URL`
- `OO_MQTT_ENABLED`
- `OO_MQTT_URL`

Setting any of these currently has no effect on the running service. This is
reported here as found, not fixed, per the scope of this document; the
Postgres/Redis/MQTT lines in the example file describe the deferred Compose
target above and appear to have been left in the template unintentionally.

### the development station station's `runtime.env`, as it now stands

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

**The ESP32 wall display's exemption.** `firmware/inside-observer` reads
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
that this has **not** been run as of the most recent diagnostic capture.
Capture the following over the run:

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
