---
aliases:
  - ADR-066
tags:
  - adr
---
# ADR-066: A graceful shutdown closes its own stream row
**Status:** accepted, 2026-08-19

### The problem

`Station.stop()` cancels the capture task. The supervisor loop catches
`asyncio.CancelledError` and re-raises it — correctly, cancellation must
propagate — which means it never reaches the `await self._on_stream_close(reason)`
at the bottom of the loop body. So a clean shutdown never closed its
`audio_stream` row.

Every row on the live station, without exception, carried
`end_reason='process_exited'`:

    2026-08-19 10:50:49 → 11:06:42   process_exited
    2026-08-17 09:07:30 → 10:48:52   process_exited
    2026-08-14 18:02:37 → 09:07:20   process_exited
    ...

That value is written by `_close_orphaned_streams`, the *repair* path, at the
following startup. The graceful close path had never once run in production.

### Why it stayed hidden for so long

Because the repair was good. [[ADR-024 - Coverage bounded by frames|ADR-024]] built `_close_orphaned_streams` to close
abandoned rows honestly from the `last_frame_at_utc` heartbeat, and it did:
history was right, coverage was right, `frame_count` was right. The only costs
were that `end_reason` was a fiction on every row and `end_utc` was up to one
heartbeat (~10 s) stale.

**A repair mechanism that works perfectly will hide the absence of the thing it
repairs.** It only surfaced because [[ADR-065 - Unclean restart is reported|ADR-065]] started reporting "the previous run
of this process ended without a graceful shutdown" — immediately, after a
completely graceful `systemctl restart`. The new signal was wrong, and it was
wrong because it was reading a true fact.

### Decision

`stop()` closes the row itself, before the executors are torn down and while
the stream-scoped frame counters still mean something, with
`end_reason="station_stopped"`.

`_close_stream_row` becomes a no-op on a row that already has `end_utc`, so the
supervisor's own call (still correct for an orderly source exhaustion) and this
one can both fire. **The first close wins**, deliberately: a second would
overwrite `end_utc` with a later wall-clock reading and `end_reason` with
whatever the supervisor happened to be unwinding at the time.

### Consequences

- `end_reason` now distinguishes a clean stop (`station_stopped`), an orderly
  source exhaustion (`source_exhausted`), a capture failure (the exception), and
  a genuine crash (`process_exited`, still written by the repair path). Until
  now every one of those looked identical.
- [[ADR-065 - Unclean restart is reported|ADR-065]]'s unclean-restart note becomes true rather than universal. Without
  this it would have fired on every deploy, which is precisely the "trains
  people to ignore the field" failure that ADR warns about — two hours after
  writing the warning.
- Historical rows are not rewritten. Their `end_reason` stays `process_exited`
  and is not to be read as evidence of a crash before 2026-08-19.

---
Part of the [[ADRS|Architecture Decision Record index]].
