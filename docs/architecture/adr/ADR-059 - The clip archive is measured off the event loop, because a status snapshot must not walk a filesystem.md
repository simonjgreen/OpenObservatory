# ADR-059: The clip archive is measured off the event loop, because a status snapshot must not walk a filesystem
**Decision:** `ClipManager.disk_usage()` no longer walks the clip tree. It reports
the last measurement and how old it is (`clip_usage_age_s`); the walk itself moves
to `ClipManager.refresh_disk_usage()`, an `async` method that yields to the event
loop every `USAGE_SCAN_CHUNK` (512) files and is driven from the housekeeping loop
on the same ~30 s cadence the old cache TTL provided. The walk also changes from
`Path.rglob` + `Path.stat` to `os.scandir` + `DirEntry.stat`. The cache is seeded
by one synchronous walk in `__init__`, at startup, before the microphone is open.

Nothing else changes: same cadence, same figures, same `disk_used_ratio` (which
comes from one `statvfs` and was never the expensive part).

**Reason — measured on the live station, 2026-08-10, in a clean 2.02 h
restart-free window (11:37:53Z → 13:40:00Z).** Every one of the **262**
`capture.late_read` events in that window was this walk. They arrive on a
**30.4 s** beat — 223 of 261 inter-arrival gaps within 2.5 s of 30 s — with
stalls of **254–370 ms** against a 500 ms ALSA ring, i.e. `late_read_max_frames`
of **142,137 of 192,000 (74%)**.

The attribution needs no inference, because the station's own `snapshot_phase_s`
instrument (added by ADR-033) names the phase in the same millisecond:

```
13:38:07.428665Z housekeeping.tick  blocking_total_s=0.4532 snapshot_phases={'storage': 0.4512}
13:38:07.433749Z loop.lag           lag_s=0.4275
13:38:07.782400Z capture.late_read  stall_ms=361.6 stall_frames=138862
```

Reproduced independently on the station, walking the same tree by hand, best of
three: **0.435 s for 40,888 files — 10.6 µs/file.**

**Why it is growing, which is the part that matters.** The cost is linear in the
size of the archive, and the archive grows by roughly **14,000 files a day** and
has deleted nothing yet (oldest clip 2026-08-05, native tier 7 days). Counting
the files that existed at each of this document's previous headroom readings:

| Reading (UTC) | files in tree | walk at 10.6 µs/file | `late_read_max_frames` |
|---|---|---|---|
| 2026-08-08 15:00 | 9,764 | 104 ms | 57,952 (30%) |
| 2026-08-09 11:14 | 20,468 | 218 ms | 114,362 (60%) |
| 2026-08-09 21:46 | 27,174 | 289 ms | 155,243 (81%) |
| 2026-08-10 13:40 | 40,888 | 435 ms | 142,137 (74%) |

The three consecutive readings "in the wrong direction" that prompted this
investigation are that column. At 14,000 files a day the walk reaches the 500 ms
ring at about **47,000 files — roughly half a day away** — and about **0.9 s** by
the end of a 72-hour soak. At the steady state the retention tiers imply (7 days
of native plus 30 days of playback derivatives) it is ~250,000 files and ~2.7 s,
every 30 seconds, against a 500 ms ring. This stops being headroom.

**Why chunking rather than a thread.** ADR-033 settled this and it is the same
mistake offered again: the walk is CPU-bound Python, so a dedicated executor
would still hold the GIL, and the event loop still has to issue each
`run_in_executor` capture read and consume its result. *An executor partitions
queueing, not scheduling, and nothing partitions the GIL.* Yielding is what
actually returns control: 512 files is ~2.4 ms at the 4.6 µs/file `scandir`
measures, inside one 10 ms ALSA period, and it stays bounded however large the
archive grows — which a faster walk would not.

**Why `scandir` as well.** Measured on the station's 40,888-file archive:
`rglob` + `path.stat()` 435 ms; `scandir` + `entry.stat()` **189 ms**; `scandir`
without sizes 48 ms. `rglob` builds a `Path` per entry and re-resolves it to
stat; `scandir` stats against the open directory handle. This is a 2.3× saving
on work that no longer blocks anything, so it is a bonus rather than the fix —
recorded because the next person will want to know where the remaining cost is.

**Why the cadence is deliberately unchanged.** Refreshing a *file count* every
30 s is more often than anyone needs, and 300 s would have been defensible on
exactly ADR-033's argument. It is left at 30 s so that this change moves one
thing. If the next measurement is ambiguous it should not be because two
variables moved at once.

**What this does *not* fix.** All 2.70 s of audio genuinely lost in that window
came from five ALSA overruns on a **~302 s** beat matching `retention_interval_s`,
not from this walk — see the 2026-08-10 section of
`docs/delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md`. This change widens the margin
those events land in; it does not remove them. That is a separate finding and
deliberately a separate decision.

**Consequences.**

- `clip_count` and `clip_bytes` are up to ~30 s stale, as they already were, and
  now say so via `clip_usage_age_s`. `disk_used_ratio`, `disk_free_bytes` and
  `disk_total_bytes` remain instantaneous.
- Constructing a `ClipManager` costs one synchronous walk. At startup, on a
  250,000-file archive, that is ~1.2 s before the microphone opens — chosen over
  reporting `0 clips` until the first refresh lands, which would have been a
  number that does not mean what its label says.
- `enforce_retention()` still clears the cache; `disk_usage()` re-measures
  synchronously if it finds it cleared. That path is unreachable in the station
  (nothing calls `enforce_retention`) and is exercised only by tests.

**Status: deployed 2026-08-10, before the 72-hour soak, and its verification
FAILED.** The pass criterion above was `late_read_max_frames` "well under
100,000". The post-deploy reading, taken from the 72-hour soak window, was
**188,982 of 192,000 (98.4%)** — worse than the 81% this change was written
to fix, not better. This does not mean chunking the walk was wrong; the
"What this does *not* fix" note above already named a separate cost on the
same beat, the retention sweep's own query cost, as the suspected remaining
cause. ADR-061 (2026-08-14) has since removed that cost — see its entry for
the re-verification.

**Verification, to run after deploying:**

```bash
rsync -a --delete --exclude __pycache__ ./src/ <user>@<station-host>:open-observatory/src/
ssh <user>@<station-host> sudo systemctl restart open-observatory
sleep 1800

# Remember: journalctl --since takes LOCAL time (BST = UTC+1); log lines are UTC.
ssh <user>@<station-host> "sudo journalctl -u open-observatory --since '-25 min' -o cat" \
  | grep -c capture.late_read          # was ~50 in 25 min; expect single digits
ssh <user>@<station-host> "sudo journalctl -u open-observatory --since '-25 min' -o cat" \
  | grep housekeeping.tick | grep -c storage   # expect 0: no tick should show a storage phase

curl -s http://<station-host>:8080/api/v1/health | python3 -c '
import json,sys; c=json.load(sys.stdin)["capture"]
print("late_read_max", c["late_read_max_frames"], "of", c["alsa_buffer_frames"])
print("late_reads   ", c["late_reads"], " loop_lag_max", c["loop_lag_max_s"])'
# Pass: late_read_max_frames well under 100,000, and the 30 s beat gone from the log.
# loop_lag_max_s is a run maximum and one retention sweep will still set it high.
```

**Rollback.** Confined to `src/` and one optional TypeScript field; no schema
change, no new dependency, no new setting.

```bash
git revert <this commit>
rsync -a --delete --exclude __pycache__ ./src/ <user>@<station-host>:open-observatory/src/
ssh <user>@<station-host> sudo systemctl restart open-observatory
```
