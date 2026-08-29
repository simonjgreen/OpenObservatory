---
aliases:
  - ADR-057
tags:
  - adr
---
# ADR-057: A row that claims evidence must be checkable; reconcile the ones that lie, and keep "missing" distinct from "reclaimed"
**Status:** active. The command, `RetentionSweeper.audit_missing_files` and
the `missing_files` block are all in the running build, and the 8,067 rows
were reconciled on the station ([[HANDOVER]]). No schema change of its own —
the head was `0007_capture_pause` when this was written and is
`0011_retention_live_asset_indexes` today ([[ADR-062 - Retention walks live assets|ADR-062]]).

**Decision:** Three things, in the order they matter.

1. `oo clips reconcile-missing` finds `media_asset` rows with `reclaimed_at IS
   NULL` whose `storage_uri` does not exist, and marks them `reclaimed_at` with
   `reclaim_reason = "missing"` — deliberately **not** one of `retention.py`'s
   tier names. Dry-run by default, `--json`, `--apply`, `--yes`, in the shape
   `oo history reconcile-streams` and `oo detections reconcile-plausibility`
   already established. It deletes nothing: not a file, not a `media_asset`
   row, not a `detection`. What the row used to claim is preserved verbatim
   under `detail.missing_reconciliation`.
2. `RetentionSweeper.audit_missing_files()` stats a **bounded rolling slice**
   of live rows on the housekeeping tick that already runs the sweep, cursored
   on `(created_at, id)`, wrapping at the end. The station therefore notices
   this class of fault by itself, and reports it as a health **note**, a
   `/api/v1/retention/status` block, and `oo_media_missing_files`.
3. The consumers' arithmetic is corrected:
   `eligible_for_deletion.bytes_verified_present` is the reclaimable figure
   with known-missing bytes removed, and the storage panel renders the
   correction next to the numbers being corrected.

No schema change. `reclaim_reason` is already `String(32)` and `detail` is
already JSON, so the Alembic head stays `0007_capture_pause` ([[ADR-042 - Migrations run in deploy.sh|ADR-042]]).

**The measurement.** Taken read-only against the live station on 2026-08-10,
`data/openobservatory.sqlite` opened `mode=ro` through a URI:

| | rows | missing from disk |
|---|---|---|
| all `media_asset` | 48,989 | — |
| live (`reclaimed_at IS NULL`) | 48,941 | **8,067 (16.5%)** |
| properly reclaimed | 48 (`privacy_human_audio`) | 48, correctly |

20,588,388,416 bytes — 20.59 GB of the 116.95 GB the database claimed to be
holding. By clip day: 2026-08-04, 3,217 of 3,217; 2026-08-05, 4,850 of 8,289;
2026-08-06 onward, 0 of 37,435. Every missing row was created before
`2026-08-05T18:44:35.234715Z` and every surviving row at or after it, with no
overlap in either direction — a clean boundary in creation order, not a
scatter. The operator's 2026-08-09 figure of 8,067 is confirmed unchanged;
what changed is the denominator, because capture kept running.

**What actually caused it, and what did not.** [[ADR-021 - Clips on their own device|ADR-021]] was the obvious suspect
and is the wrong one. The cause is `ClipManager.enforce_retention()`
(`clips.py`), the pre-[[ADR-026 - Tiered clip retention|ADR-026]] sweep: it walks the clip tree, sorts by mtime and
unlinks oldest-first until the tree fits `max_total_bytes` — and it never
touches the database. Three independent lines of evidence agree:

- **Its own logs account for the whole loss.** `journalctl -u open-observatory
  | grep clips.retention`, between 2026-08-05 and 2026-08-08: 8,166 files
  deleted, `expired=0` and `over_budget=8,166` throughout, 20,838,470,172
  bytes. Against 8,067 rows and 20,588,388,416 bytes missing here. The ~99-file,
  ~250 MB difference is `.partial` files and clips with no row, which that
  sweep also deletes.
- **The boundary is an mtime prefix**, which is the signature of
  `sorted(entries)` unlinking oldest-first, and nothing else in the system
  produces it.
- **[[ADR-021 - Clips on their own device|ADR-021]] is where it stopped, not where it started.** The SSD raised the
  budget from 20 GB to 300 GB; the last `clips.retention` deletion is
  2026-08-08T13:07:32Z and `data/clips.sdcard-backup`'s copy finished at
  13:06:27Z. That is why the boundary *looks* like [[ADR-021 - Clips on their own device|ADR-021]] — the migration is
  the fix's timestamp. The 20 GB budget had been holding a rolling ~24-hour
  window all along, which is exactly the span that is missing.

**Nothing is recoverable, and this was checked properly.**
`data/clips.sdcard-backup` holds 9,202 files: 9,186 match rows whose files are
*present*, 14 match no row, and **0 match any missing row** — compared by
relative path and again by basename. Its oldest file mtime is
2026-08-05T18:54:02Z, nine minutes *after* the boundary, so the pre-boundary
clips were never in it. `sudo find / -xdev` across both filesystems for
`20260804T*.wav` and pre-boundary `20260805T*.wav` returns nothing, and there
are no archives. This matters more than an accounting error would, because a
clip is the one thing in this system that cannot be regenerated: charter item 5
ranks evidence above refinement *because* "a better classifier can be run over
stored clips later." For these events it cannot.

**What it already cost, beyond the wrong numbers.**

- **The refinement runner cannot work on bats.** `find_candidates`
  (`refinement/store.py`) selects oldest-unrefined-first and filters
  `reclaimed_at IS NULL`, so it draws its whole batch from exactly this
  population: 1,200 candidates considered, 0 examined, 1,200 `unavailable`,
  every night, with no `Refinement` row written to move it past them. 1,290 of
  6,049 bat detections have lost every native clip they had; 4,759 still have
  audio and the runner has never reached one of them. Reconciling the rows is
  what unblocks it.
- **61 held Spotted Crake detections cannot be listened to.** All 122 of their
  media rows are in the missing set, event times 2026-08-04T18:47Z to 20:55Z,
  inside the deleted band. A human hold ([[ADR-043 - Taxon correction|ADR-043]]) exempts a detection from
  three retention tiers; it could not exempt it from a sweep that never read
  the database. The command names held detections in its output, because an
  operator writing off a hold should be told that is what they are doing.
- The storage panel over-reported, `/api/v1/media/{id}` answered 410 for a
  sixth of its rows, and retention's disk budget counted 20.59 GB as
  reclaimable that reclaiming could not free.

**Why `"missing"` is not a tier name.** Setting `reclaim_reason` to `native` or
`expired` would record that a policy decided to give this clip up. Nothing
decided anything; the file went. The charter's "withdraw, not delete" rule is
about a record the system got wrong being evidence about the system, and the
same applies to the system's account of its own storage: an operator, and the
refiner, should be able to tell evidence aged out on purpose from evidence that
vanished. `"missing"` joins `"privacy_human_audio"` ([[ADR-049 - Sound categories are not species|ADR-049]]) as a reason that
is a fact about *why*, not a tier. Reconciliation is honest bookkeeping, not a
way of pretending the clips were never there.

**Why the recurring check is a rolling sample and not a census.** Statting
every live row is cheap in isolation — 48,989 `os.path.exists` calls measured
at **0.27 s** on the target — but 0.27 s is the same order as the ~0.30 s ORM
sweep [[ADR-033 - Retention is paced|ADR-033]] had to pace to 300 s after it starved the event loop for
55–150 ms at a time and cost ~1.9 `capture.gap` records per minute. Capture
always wins, and this is the third time on this project that sustained I/O next
to the ALSA read has had to be refused. So: `batch_size` rows per 300 s tick
(default 200, ~1 ms of the same measurement), a full pass over ~50k live rows
in about 20 hours, on the evidence executor rather than the pool the capture
read shares ([[ADR-021 - Clips on their own device|ADR-021]]'s fix, still the load-bearing one).

The cursor is `(created_at, id)`, not `created_at`. Three or four assets are
written per detection within microseconds of each other, so a bare `>` would
silently skip every row sharing a timestamp with the last one read — precisely
the quiet omission this audit exists to catch, and it would have made the audit
lie about its own coverage.

**Why the honest answer needs two numbers.** A completed pass gives an exact
count; a partial pass gives a floor. Reporting the second as though it were the
first is the failure the charter names by example — a coverage figure that read
1302%, an "audio lost" figure over by 12.9x, both sincere and both believed. So
`known_missing` is the last *completed* pass's figure, `exact` and
`passes_completed` say which kind of claim it is, and the panel renders "8,067
of 48,941 rows" or "8,067 so far (the audit is still on its first pass)"
accordingly. `missing_files` is *absent*, not zero, from a station that does not
report it, and the panel then renders nothing rather than a confident zero.

**Reviewed 2026-08-29:** the mechanism is unchanged; the population it walks
is not. The live set has grown from 48,941 rows to 234,011 (99,106 + 134,905
across the two tiers, `/api/v1/retention/status`), so 200 rows per 300 s is a
full pass in about four days, not the 20 hours estimated here — and the cursor
and tallies are in memory only (`retention.py:313`), so a restart starts the
pass over. After 24 hours of uptime the station reports `passes_completed: 0`,
`in_progress_scanned: 55000`, `exact: false`. That is the floor, correctly
labelled, but it will stay a floor: `known_missing` reading 0 currently means
"none in the oldest 55,000 live rows", not "none in the table". The last exact
figure is [[HANDOVER]]'s completed pass over 65,556 rows.

**A note, not a `problems` entry.** Capture, detection and history are all
correct; what is wrong is the station's account of what it holds. Degrading
would flip `binary_sensor.<station>_station_healthy` in Home Assistant and train
the operator to ignore it — the same reasoning [[ADR-055 - Timed recording pause|ADR-055]] applied to an operator
pause. Saying nothing was not an option either: "8,067 clips" meaning "8,067
rows, 16.5% of which have no file" is exactly a number that does not mean what
its label says.

**What this ADR does not do.**

- It does not delete `ClipManager.enforce_retention()`. The station has not
  called it since [[ADR-026 - Tiered clip retention|ADR-026]] — housekeeping drives `RetentionSweeper.sweep()` —
  and it is still tested. Removing it is a separate change with its own risk;
  what this ADR adds is the check that would have caught it, and would catch
  the next thing that unlinks a clip behind the database's back.
  `ClipManager.admits()`'s write-time `min_free_bytes` reserve is a different
  concern and is untouched.
- It does not delete `data/clips.sdcard-backup` (~21 GB). **The operator
  removed that directory by hand on 2026-08-10**, after this ADR established
  that no live row pointed into it and that it held none of the 8,067 missing
  clips; that freed 21 GB on the SD card, where the database lives and write
  endurance is a standing constraint. [[ADR-026 - Tiered clip retention|ADR-026]] already made
  that an explicit operator-triggered cleanup, and it is now *also* the only
  independent copy of 9,186 live clips.
- It does not add a filesystem check to `/api/v1/retention/status`. That
  endpoint is polled by an always-on panel, and a census per poll is the exact
  pattern [[ADR-021 - Clips on their own device|ADR-021]] and [[ADR-033 - Retention is paced|ADR-033]] both had to undo.
- It does not attempt recovery. There is nothing to recover from; see above.

**Rollback.** Revert the commit. `--apply` has no undo of its own, but nothing
it wrote is destructive: clearing `reclaimed_at`/`reclaim_reason` on rows
carrying `detail.missing_reconciliation` restores the previous (wrong) state
exactly, and that block records what to restore it to. No migration to reverse.

**Verification on the target, read-only first.**

```sh
# 1. The extent, from the database the station is running on. Read-only.
ssh <user>@<station-host> 'cd ~/open-observatory && python3 -c "
import sqlite3, os
db = sqlite3.connect(\"file:data/openobservatory.sqlite?mode=ro\", uri=True)
rows = db.execute(\"select storage_uri, byte_length from media_asset where reclaimed_at is null\").fetchall()
gone = [(u, b) for u, b in rows if not os.path.exists(u)]
print(len(gone), \"of\", len(rows), \"live rows missing;\", sum(b for _, b in gone), \"bytes\")
"'

# 2. What the station itself now says, before anything is applied.
curl -s http://<station-host>:8080/api/v1/retention/status \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["missing_files"])'
curl -s http://<station-host>:8080/api/v1/health \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["notes"])'
curl -s http://<station-host>:8080/metrics | grep -E '^oo_media_missing'

# 3. The dry run, kept. Read it before applying: it is the only record of what
#    these rows claimed that will exist outside the rows themselves.
ssh <user>@<station-host> 'cd ~/open-observatory && .venv/bin/oo clips reconcile-missing --json' \
  > missing-$(date -u +%Y%m%dT%H%M%SZ).json

# 4. Apply, on the station, with the confirmation prompt.
ssh -t <user>@<station-host> 'cd ~/open-observatory && .venv/bin/oo clips reconcile-missing --apply'

# 5. Confirm the accounting corrected itself, with no restart.
curl -s http://<station-host>:8080/api/v1/retention/status \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["eligible_for_deletion"], d["missing_files"])'

# 6. Confirm the refinement runner can now reach evidence that exists.
ssh <user>@<station-host> 'cd ~/open-observatory && .venv/bin/oo refine run --dry-run --json' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["candidates_considered"], d["examined"], d["outcomes"])'

# 7. Confirm nothing was destroyed.
ssh <user>@<station-host> 'cd ~/open-observatory && python3 -c "
import sqlite3
db = sqlite3.connect(\"file:data/openobservatory.sqlite?mode=ro\", uri=True)
print(db.execute(\"select count(*) from media_asset\").fetchone(),
      db.execute(\"select count(*) from detection\").fetchone(),
      db.execute(\"select reclaim_reason, count(*) from media_asset where reclaimed_at is not null group by 1\").fetchall())
"'
```

---
Part of the [[ADRS|Architecture Decision Record index]].
