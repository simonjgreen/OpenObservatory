# ADR-065: A restart that voids a measurement must say so
**Status:** accepted, 2026-08-19

### The problem

At 09:07 UTC on 2026-08-17 the station restarted. It was 62.7 hours into a
72-hour acceptance soak that was passing comfortably — 0.999935 continuity
against a 0.999 criterion, zero stream restarts, three capture gaps in as many
days. The restart put it 8.9 hours short, and reset the clock to zero.

Nothing said so. Capture reopened within 25 seconds, `/api/v1/health` returned
`status: "ok"`, and every counter that would have shown the discontinuity —
`stream_restarts`, `continuity_ratio`, `blocks`, `frames` — is process-scoped
and had reset to zero along with it. The station looked, by every number it
publishes, like a healthy run in progress. It was a healthy run in progress; it
just was not the *same* run, and only the person holding the start time could
know that.

It was found two days later by running `uptime`.

The cause of the restart is still not established: memory flat at 16%, load
0.20, no kernel error, no undervoltage record, no OOM, no thermal event, and a
single `LINUX RESTART` marker across eight days of retained sysstat. The
evidence is consistent with an external power interruption and there is nothing
in software to fix. **This ADR is not about preventing the restart. It is about
never again failing to notice one.**

### The evidence already existed

`Station._close_orphaned_streams` runs at every startup and closes
`audio_stream` rows that a previous process left open, because only a graceful
shutdown writes `end_utc`. An open row at startup *is* proof that the previous
run did not shut down cleanly. The method computed exactly that, logged it at
`info`, and threw it away.

### Decision

- `_close_orphaned_streams` records `unclean_restart`, the number of rows it
  recovered, and the last moment audio is known to have been delivered.
- `/api/v1/health` reports it as a **note**, not a problem. The station is
  genuinely healthy; what is not healthy is any measurement spanning the gap,
  and the note says exactly that: *"any soak or continuous measurement running
  at that moment is void and must be restarted from now."* Making it a problem
  would mean every station that has ever lost power reports itself degraded
  forever, which trains people to ignore the field.
- `oo_station_unclean_restart` exposes it for alerting.
- ADR-063's clock re-anchor gets a note on the same terms, for the same reason:
  it is a disclosure about data already written, not a current fault.

### Consequences

- The note persists for the life of the process, which is correct — the fact it
  reports does not stop being true, and a soak started before it appeared is
  void whether or not anyone has read the note yet.
- This does not detect a restart that took the *whole* database with it, or a
  station wiped and rebuilt. Nothing in-process can.
- Combined with ADR-063, the two failures found in this session share a shape
  worth naming: **the station's process-scoped counters all reset together, so a
  restart makes every instrument agree that everything is fine.** Anything that
  must survive a restart to stay true has to be derived from durable state — the
  `audio_stream` rows here, the wall clock there — not from a counter.
