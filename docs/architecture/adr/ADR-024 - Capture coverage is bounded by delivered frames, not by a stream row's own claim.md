# ADR-024: Capture coverage is bounded by delivered frames, not by a stream row's own claim
**Decision:** `history.coverage()` no longer trusts `audio_stream.start_utc`/`end_utc`
as the truth of how long a stream captured for. Each row's contribution is first
capped to the earlier of two independently-derived bounds — `frame_count` converted
to seconds at the stream's own `sample_rate`, laid down from `start_utc` (audio is
known to begin exactly there — `StreamClock` anchors to the first block actually
read), and `last_frame_at_utc`, a heartbeat written every ~10 s while the stream is
open — before the existing interval-merge (ADR-era fix for the 1302% incident, see
`HANDOVER.md` §7) ever runs. A row whose frame-derived duration falls below 90% of
its claimed wall-clock span (and whose claim is at least 60 s, to avoid noise on
trivial rows) is additionally marked `suspect` in the API response rather than
averaged away silently. `audio_stream` gained a `last_frame_at_utc` column to make
the heartbeat possible, and `Station` now tracks `frame_count`/`discontinuity_count`
per-stream instead of reading them off the process-lifetime `CaptureCounters` (see
below).

**Reason.** On 2026-08-08 the live database's most recent `alsa` stream row claimed
`start_utc` 2026-08-07 03:38:54 and `end_utc` 2026-08-08 11:36:36 — a 32 hour span,
closed with `AlsaCaptureError: ALSA read failed: File descriptor in bad state`. Its
`frame_count` was 3,852,212,352, which at 384 kHz is 2.79 hours, not 32. Querying the
live database directly (read-only, over SSH) settled which of the two numbers was
the lie: all 245 `capture_gap` rows for that stream fall between 03:38:54 and
06:24:45 — the first ~2h46m of the claimed span — and there are none after that,
all the way to the claimed close 29 hours later. There are also zero detections on
any live stream between 2026-08-07 20:00 and 2026-08-08 12:00, and no other `alsa`
stream opened in between (this was the only row spanning that period — not a case of
counters carried over from an earlier reopen). The frame count is correct; the claimed
end time is not. The capture read loop stopped delivering blocks around 06:25 UTC —
almost certainly wedged on the same file descriptor that eventually surfaced the
`AlsaCaptureError`, rather than erroring immediately — and nothing downstream
noticed for 29 hours, because nothing was watching for silence, only for an explicit
error. **This is a capture-side finding, not fixed here**: the read loop hanging
instead of failing fast is squarely `OPEN_INVESTIGATION_CAPTURE_GAPS.md`'s territory
(the capture-gap investigation), and is recorded there for reconciliation rather than
patched in this change. What is fixed here is that a row shaped like this one can no
longer inflate the coverage bar while the underlying hang is tracked down.

**Why frame count over wall clock.** `frame_count` is written by the capture hot
path counting bytes it has actually pulled off the device; `end_utc` is written by
whichever code path eventually notices the stream is over, which is only as
prompt as the failure that triggers it. A hang produces an honest frame count and
a dishonest end time. Deriving coverage from delivered frames makes that
asymmetry work in the operator's favour: the number can only be pulled down by
new evidence of a problem, never inflated by the absence of one.

**A second, related bug found while fixing this.** `CaptureCounters.frames` (and
`.discontinuities`) are process-lifetime — never reset when `_capture_supervisor`
reopens the device after a transient failure — but `Station._close_stream_row` was
writing them straight into `audio_stream.frame_count`/`discontinuity_count`, which
are per-*stream* columns. A process that reopens the device even once before a
stream closes would write that later stream's row with an earlier stream's frames
folded in. `Station` now tracks `_stream_frames`/`_stream_discontinuities`,
reset in `_on_stream_open`, and uses those for the row and for the live
`continuity_ratio` in `/api/v1/station` (which had the same mismatch, silently
absorbed by its own `min(1.0, …)` clamp). No evidence yet that this specific bug
produced the 32-hour row above — that stream was the only `alsa` row for its
process's lifetime — but it is the same shape of error and is now closed.

**Historical data.** The `oo history reconcile-streams` command scans already-closed
rows for this pattern and, in dry-run mode (the default), reports what it would
change without touching anything. `--apply` (plus a confirmation, or `--yes`)
corrects `end_utc` to the honest bound and records the original claim under
`detail.reconciliation` on the row, so the correction is auditable rather than a
silent rewrite of the operator's record. It intentionally never touches a row with
`end_utc IS NULL` — that might be the currently-running station's own stream, which
this offline command has no way to know about; closing those honestly is
`Station._close_orphaned_streams`'s job at that process's own next startup, which
now also prefers the `last_frame_at_utc` heartbeat over the coarser
detection/gap-timestamp heuristic it used before.

**Constraint.** Any future column added to `audio_stream` that a coverage or
continuity calculation depends on must be considered per-stream unless explicitly
documented otherwise — `CaptureCounters` exists for the genuinely
process-lifetime figures (CPU budget, open-failure count) and nothing else should
be read out of it into a stream row again.

**Not yet done, and out of scope for this change:** `PostgreSQL` has no Alembic
migration for `audio_stream.last_frame_at_utc` — no Alembic environment exists in
this repository at all yet (ADR-007 already flags this gap for the SQLite→Postgres
transition). `db.session.create_all()` now defensively `ALTER TABLE ADD COLUMN`s
any column missing from an existing SQLite file, which covers the developer and
on-device profiles this project currently runs, but a real migration is still owed
before the PostgreSQL profile is exercised for real.

> **Status 2026-08-08: superseded by ADR-035.** An Alembic environment now
> exists, and `audio_stream.last_frame_at_utc` is included in the `0001_initial`
> baseline. The `ALTER TABLE` patcher is deliberately retained for now; see
> ADR-035 for why, and for what still has to change before it can be retired.
