# ADR-060: A read that never returns is a dead stream, not a slow one
**Status:** active. Bounds two loops, adds one backstop, and stops `capture.state` capping the health severity.

**The incident.** On 2026-08-14 the station was deaf from 02:12 UTC and nobody
knew until the operator looked at the indoor display at 05:47 and read `no audio
block for 12815.893s`. Full account in `HANDOVER.md` §1e; the decision-relevant
part is why nothing recovered and nothing shouted.

`alsa_source.py` treated any ALSA error containing `Input/output error` as a
transient xrun: count, log, `continue`. That `continue` sits **inside the
`while collected < self._block_frames` loop that assembles a block**. A permanent
error therefore never finishes a block, so `_read_blocking` never returns and
never raises; `_capture_loop` never returns; and `_capture_supervisor` — whose
docstring is *"Reopen the device with bounded backoff after any loss"* — is never
reached. It logged 23,135 errors at one every 0.576 s and `stream_restarts` never
left 0.

**The recovery architecture was already correct.** A plain `systemctl restart`
brought capture straight back, which proves the PCM was reopenable the whole time
and the supervisor would have fixed this within seconds of being given control.
The defect was not a missing mechanism; it was one `continue` below the mechanism,
denying it the chance to run. This is the same shape as ADR-020's incident (§3a),
where the error string did *not* match the swallowing branch, so it propagated and
the station degraded correctly. Whether the station recovered came down to which
words the driver happened to put in an error message.

**Decision, in four parts.**

1. **Bound the swallow, in both branches.** `AlsaSource` takes
   `stall_timeout_s` (default 5 s). Any run of read attempts that makes no
   progress — errors, or `length == 0` — raises `AlsaCaptureError` once it has
   made none for that long. A read that yields audio resets it, so an ordinary
   xrun still costs nothing. The `length == 0` branch carried a comment claiming
   it already did this ("treat a run of them as a stalled device rather than
   spinning") and did not; a fix to the `-EIO` branch alone would have left the
   identical trap one screen further down.
2. **A backstop that does not need to know why.** `_capture_loop` wraps a live
   read in `asyncio.wait_for(…, capture_read_timeout_s)` (default 15 s, longer
   than the source's own bound so the source names the fault first). This covers
   the branches we have not thought of yet. **ALSA sources only** — a `step`-mode
   replay source blocks deliberately, and timing that out would break the
   fixture-driven tests it exists for.
3. **Silence is critical, whatever `state` says.** The severity expression read
   `"degraded" if capture["state"] == "capturing" else "critical"`, and `state`
   stayed `capturing` for three and a half hours, because it describes what the
   capture task was *asked* to do rather than whether audio is arriving. A
   station that has heard nothing for longer than `capture_silence_critical_s`
   (default 30 s, comfortably past a reopen plus backoff) is now `critical`, so
   `/api/v1/health` answers 503 and Home Assistant's healthy sensor flips.
4. **Left deliberately unchanged: `capture.state`.** It stays a description of
   the task's intent. Making it mean "audio is arriving" would overload one field
   with two facts; `block_age_s` already carries the second one honestly, and the
   fix is to stop *deriving* severity from the wrong one.

**Consequences.** A wedged device now costs seconds instead of hours, and costs a
`stream_restarts` increment, a `critical` health event and a 503 — all things
that were available during the incident and none of which fired. The cost is that
a genuine 5 s device stall now tears the stream down and rebuilds it, losing the
in-flight block. That is the right trade: the audio was already lost, and the
alternative is what happened on the 14th.

**What this does not fix.** The wedge itself is unexplained. The AudioMoth never
left the USB bus — no `dmesg` event since 8 August, autosuspend off — and
`/proc/asound/…/status` read `state: RUNNING` with `hw_ptr` frozen at 768. This
ADR makes the station survive it, not prevent it. Related and also open: gap rows
arrive in pairs exactly `retention_interval_s` apart, so the retention sweep is
costing about 7 s of audio an hour (§1e).
