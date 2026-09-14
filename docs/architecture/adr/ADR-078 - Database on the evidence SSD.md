---
aliases:
  - ADR-078
tags:
  - adr
---
# ADR-078: The database lives on the evidence SSD, and the station refuses to fork the record onto the SD card
**Status:** accepted, 2026-09-14. Supersedes the paragraph *Why the database stays on the SD card* in [[ADR-021 - Clips on their own device|ADR-021]] and option **H** of [[ADR-037 - Prune the dead indexes|ADR-037]]. Amends the *Storage endurance* row of the [[CHARTER]]. The rest of ADR-021 — the mount over `data/clips`, `clips_require_mount`, no `RequiresMountsFor`, `nofail` — stands.
**Relates to:** [[ADR-007 - SQLite in developer mode|ADR-007]] (SQLite is what runs), [[ADR-042 - Migrations run in deploy.sh|ADR-042]] (the schema check at startup), [[CAPTURE_RUN_2026-08-31]] (the measurement that reopened the question).

### The problem, measured on 2026-09-14

The record is a 2.4 GB SQLite file at `data/openobservatory.sqlite`, in WAL mode, on the SD card (`/dev/mmcblk0p2`, a SanDisk `SD256`, 238 GB, no wear reporting). Clips went to the SSD on 2026-08-08; the database was left behind on purpose, and ADR-037 costed moving it as *"~0.3–0.7 GB/day of SD writes against 6 GB/day total"* and said no. The bytes have not changed much. What has changed is that the cost of the database being on that card has been measured in lost record rather than in bytes:

| What | Figure | Source |
|---|---|---|
| Detections discarded by `_persist_loop` on `database is locked`, 158 h run to 2026-09-07 | **2,188 (0.54%)**; 3 of 4 gap rows never written | [[CAPTURE_RUN_2026-08-31]] |
| `database is locked` in the journal, per day, 2026-09-01 → 09-08 | 248 – 1,555 | `journalctl -u open-observatory` |
| Same, 2026-09-09 → 09-14, after the 2026-09-11 deploy | 204 – 596 | same |
| `persistence.failures` since that deploy (2.80 days) | **82** of 168,873 offered (0.05%) | `GET /api/v1/station` |
| `fdatasync` of one random 4 KiB page, SD card, n=60 at idle priority | **p50 11.3 ms**, p90 13.8, max 15.6 | probe script, this session |
| Same on the SSD (`/dev/sda1`, USB 2.0) | **p50 2.2 ms**, p90 2.7, max 120.4 (one clip write) | same |
| SD card since boot (23.1 d): write requests / bytes / summed request time | 8.55 M / 129.5 GiB / 561,903 s → **65.7 ms mean per write request** | `/proc/diskstats`, `mmcblk0` |
| SSD since boot: same | 4.38 M / 819 GiB / 137,076 s → 31.3 ms, at a 400 KB mean request | `/proc/diskstats`, `sda` |
| Station service, since 2026-09-11 15:06 BST (2.80 d): bytes to the SD card | **2.36 GB, 486,674 requests (4.9 KB mean)** → 0.84 GB/day | cgroup `io.stat`, `179:0` |
| Same, bytes to the SSD | 94.4 GB → 33.7 GB/day (clips, at the 0.35 threshold experiment) | cgroup `io.stat`, `8:0` |
| `systemd-journald` since boot, bytes to the SD card | 1.67 GB | cgroup `io.stat` |

Two things follow.

**Wear was never the argument, and still is not.** ADR-037's arithmetic holds: the station puts about a gigabyte a day of small writes on the card, and even a poor card's endurance rating is years at that rate. Write amplification inside the card is real and unmeasured, but nothing above turns it into a deadline. The charter row that says the database lives on the SD card is amended by this ADR because it is no longer true, not because the card was about to fail.

**Latency is the argument.** In WAL mode a commit appends frames; every 1,000 pages the committing connection runs a checkpoint *inside its own commit*, which rewrites up to 4 MB of the main file as random 4 KiB pages and calls `fsync` on both files. On a card where one such sync is 11 ms and a random write request averages 66 ms in the queue, that checkpoint is a multi-second hold of the write lock. `busy_timeout` is 5 s ([[ADR-030 - ALSA ring and capture thread|ADR-030]]). The persist loop has one attempt and no retry (`src/open_observatory/station.py`, `_persist_loop`). So a checkpoint on the SD card is, directly, discarded detections — the 0.54%, and the 82. The numbers since 2026-09-11 are lower because that deploy shortened retention's held transactions ([[ADR-076 - The evidence bank is a column, not a recomputed set|ADR-076]]'s chunking), not because the mechanism went away.

This ADR does not claim the move ends the contention. It claims the same checkpoint on the SSD is about five times cheaper per sync and far cheaper per random page, and it names the measurement that decides: `database is locked` per day and `persistence.failures` per day, from the same journal and the same endpoint, over the week after the move, against the table above. If they do not fall, the next step is retries in `_persist_loop` and then the PostgreSQL move ADR-007 always anticipated — both of which remain worth doing regardless.

### Decision 1: the file moves under the evidence mount, via the setting that already exists

`OO_DATABASE_DSN=sqlite+pysqlite:////home/<user>/open-observatory/data/clips/database/openobservatory.sqlite`, in `config/runtime.env`, plus `OO_DATABASE_REQUIRE_MOUNT=true` (Decision 2). Nothing else on the host changes: no `/etc/fstab` edit, no remount, no new unit.

Why *under* `data/clips` rather than a mount of its own:

- **The knob exists and is exercised.** `database_dsn` has been independently settable since ADR-007 (`config.py`, `resolved_database_dsn`), the settings UI already refuses to edit it because changing it is a *"stop, move, migrate, start operation"* (`site_settings.py`), and every test in the suite already runs with a database outside `data_dir` (`tests/conftest.py`). The one gap — `init_engine` created `data_dir` but not the DSN's own directory, so a relocated database died at first start with SQLite's pathless *unable to open database file* — is fixed in this ADR.
- **Both units already have it writable.** `ReadWritePaths=@DEPLOY_ROOT@/data` in `open-observatory.service` and `open-observatory-refine.service` covers the mount beneath it; the refinement runner opens the same database nightly and is the process people forget. `tests/test_systemd_unit.py` now pins that `data/clips/database` stays under an entry in *both* units.
- **Deploy never touches it.** `deploy.sh` excludes `data` from its `rsync --delete`.
- **The clip code cannot mistake it for evidence.** Every walker over `data/clips` selects `*.wav` or `*.partial` and removes only *empty* day directories (`clips.py`, `retention.py`), and the usage scan sums `.wav` sizes only, so `clip_bytes` on the storage panel stays a count of clips.
- **Rollback is a rename and a setting.** The old file stays on the SD card, renamed, until the operator deletes it.

| | Alternative | Why not |
|---|---|---|
| A | Remount the SSD at `data/` with `clips/` beneath it | The cleanest layout on paper — everything the station writes on one volume, the default DSN lands on it unconfigured — but it is an `/etc/fstab` edit on a live host, a same-filesystem move of 41 day directories, a change to what `clips_require_mount` checks, and a rollback that has to move the directories back. Every one of those is a place to get it wrong at 01:00. Revisit at the next fresh commissioning, where none of that history exists. |
| B | A bind mount of the SSD's `database/` at `data/db` | Two fstab lines describing one fact, for a path that is prettier by one segment. |
| C | PostgreSQL, as ADR-007 intends | The real answer to contention, and the place the banked UUIDv7/epoch-timestamp wins go ([[ADR-037 - Prune the dead indexes|ADR-037]]). It is a milestone, not a Sunday, and it would also live on this SSD. Unchanged: still the production target, still unexercised. |
| D | Leave the file, add retries to `_persist_loop` | Treats the symptom the ADR can see and leaves the 66 ms queue for the retention sweep, the refinement runner and the API to keep colliding in. Retries are still owed (queue item), on top of this. |

### Decision 2: no fallback — the station refuses to open a database on the system disk

ADR-021 kept the database on the SD card so that *"if the SSD is unplugged the station keeps capturing, detecting, and serving history."* That property is given up here, and the trade is stated plainly: **before, a missing SSD cost new clips; after, it costs the station until the SSD is back.**

The alternative — start anyway, and let SQLite create a fresh, empty `openobservatory.sqlite` on the SD card under the unmounted path — is worse than downtime. It produces two records that both look real, must be merged by hand, and would sit under the mount point invisible once the SSD returned. The charter's item 3 outranks its item 2 exactly here: an inaccurate permanent record is worse than a gap that is visible as a gap.

So `database_require_mount=true` makes `init_engine` **raise** when the database's directory resolves to the root filesystem, before anything is created. The mechanism that recovers is the one already in the unit: `Restart=always`, `RestartSec=5`. Each retry is a fresh process with a fresh mount namespace, so a mount that arrived late — a slowly enumerating SSD, or an operator's `mount -a` — is seen on the next attempt without anyone restarting anything. The journal names the path and the mount point; `/api/v1/health` carries a `database` block (`path`, `mount_point`, `on_system_disk`, `require_mount`) so the same facts are one `curl` away; `oo db status` prints them on the host.

Why still no `RequiresMountsFor=` (ADR-021's reasoning, kept): a *dependency* failure does not retry, a crash loop does. And `nofail` stays in `/etc/fstab` so the host still boots to SSH without the SSD.

### Decision 3: `oo db copy` is the copy method, and it is verified on a live station

[[HANDOVER]] queue item 26 records that *"backup has no working method"*: `sqlite3.Connection.backup` never converged against the live database (it restarts whenever the source is written, and the station writes continuously) and a file copy of a live WAL database is not a consistent snapshot. Both findings stand. The method that does work is SQLite's own `VACUUM INTO`: it reads the whole database inside a single read transaction and writes a compact copy, so the copy is exactly the database as of the moment the transaction opened, whatever is written meanwhile. It never restarts. WAL readers do not block the writer, so capture is unaffected; the only cost is that the WAL cannot be reset for the duration.

Three commands, all in `src/open_observatory/db/admin.py` behind `oo db`, none of which writes to the source:

- `oo db status [--json]` — path, mount point, `on_system_disk`, file and WAL sizes, page geometry, journal mode, Alembic revision, row counts of the six tables an operator compares.
- `oo db copy DEST [--force]` — the `VACUUM INTO` copy. Run under `nice -n 19 ionice -c3` on a station that is capturing.
- `oo db check [PATH] [--full]` — `quick_check`, or `integrity_check` with `--full`, plus the facts; exit 1 when not `ok`, so a runbook stops on it.

The copy was taken against the **live, writing** station as the first step of the migration below, timed, and checked — see *Measured during the migration*.

### What this ADR does not do

- It does not move `data/firmware`, `data/numba-cache` or `data/transient`; they are a few megabytes and are written rarely.
- It does not change the SSD's USB port. The faster port shares the AudioMoth's host controller ([[OPEN_INVESTIGATION_CAPTURE_GAPS]], [[TARGET_DIAGNOSTICS]]); the database's I/O is small.
- It does not add retries to `_persist_loop` or `_insert_gap_row`. Still owed; still the right next step if the numbers below do not fall.
- It does not touch the schema. `alembic upgrade head` runs on the copy exactly as it would have on the original, because the copy *is* the original.

### The migration, as run

The full runbook is in [[DEPLOYMENT_AND_OPERATIONS]] under *Database on the evidence SSD*. In outline, and in this order:

1. Deploy this code (`deploy.sh --no-web`) so the station understands the new setting and creates the directory.
2. `oo db copy` from the live database to `data/clips/database/openobservatory.sqlite`, at idle priority. `oo db check --full` on the copy.
3. `systemctl stop open-observatory open-observatory-refine.timer`. A second `oo db copy --force`, now against a quiet database, so nothing written between step 2 and the stop is lost. `oo db check` again; compare `row_counts` with `oo db status` on the original.
4. Add the two lines to `config/runtime.env`; rename the original to `openobservatory.sqlite.moved-2026-09-14` (with its `-wal`/`-shm` if present) so the old path cannot be opened by accident.
5. `systemctl start`; `oo db status` and `GET /api/v1/health` must both say `on_system_disk: false`; `GET /api/v1/history?window=last-hour` must return the last hour; the journal must show `db.engine_ready` with the new path.

### Rollback

Stop the service and the refine timer. Remove the two lines from `config/runtime.env`. Rename `openobservatory.sqlite.moved-2026-09-14` back. Start. Anything recorded between the move and the rollback stays in the SSD copy and is not merged — say so in the journal of whatever incident forced the rollback. The code is safe to leave deployed: with the setting off and the DSN empty it behaves exactly as before.

### Smoke test

    oo db status --json | jq '{path, mount_point, on_system_disk, row_counts}'
    curl -s http://127.0.0.1:8080/api/v1/health | jq '.database'
    journalctl -u open-observatory --since "1 hour ago" | grep -c "database is locked"

### Measured during the migration

Recorded on 2026-09-14, in the order the steps ran; see the runbook for the commands.

| Step | Measurement |
|---|---|
| `VACUUM INTO` against the live, writing station, at `nice -n 19 ionice -c3` (the exact statement `oo db copy` runs, before that command was deployed) | **2,471,563,264 bytes in 458.8 s** (source file 2,567,311,360 bytes: the copy is compact), 10:34–10:42 BST |
| `PRAGMA quick_check` on that copy | `ok` in **150.1 s**; 1,826,349 detections, 600,479 media assets, 113 streams, 3,222 gaps, 68 reviews, 42,986 refinements; revision `0012_detection_banked_at` |
| Capture during the copy | unaffected: `continuity_ratio` 0.999949 and `audio_lost_seconds` 0.0 on `GET /api/v1/station` afterwards, the same as before |
| Second copy with the service stopped, and row counts against the original | **not yet run** — the deploy and the switch are the operator's to perform; see [[HANDOVER]] §6.0 item 0a |
| Service downtime, stop to healthy | **not yet run** |

### Revisit when

| Trigger | Action |
|---|---|
| `database is locked` per day, or `persistence.failures`, has not fallen a week after the move | Retries in `_persist_loop` and `_insert_gap_row`; then the PostgreSQL move |
| The SSD is replaced or the station is re-commissioned | Take alternative A: mount the volume at `data/`, and let the default DSN land on it |
| The PostgreSQL move happens | This ADR's file location becomes the data directory of that server; Decision 2 still applies to it |

---
Part of the [[ADRS|Architecture Decision Record index]].
