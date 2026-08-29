---
aliases:
  - ADR-055
tags:
  - adr
---
# ADR-055: The operator can pause recording for a chosen time — and it expires, survives a restart, and is recorded as a pause
**Status:** active. The first implementation of the charter's privacy
constraint as something an operator *does*, rather than something the system
refrains from.

**Context.** The charter's privacy constraint says: *"A microphone in a garden
records neighbours, visitors and passers-by who never consented."* Everything
built for it so far is passive — no continuous speech retained, no clip for a
human sound class ([[ADR-049 - Sound categories are not species|ADR-049]]), bounded evidence retention ([[ADR-026 - Tiered clip retention|ADR-026]]). All of it is
about what the station *keeps* once something has already happened.

None of it helps the case that actually arises. In the operator's words:

> "We have a birthday party for the children at ours today and I would like to
> be able to disable the logging for a given amount of time."

A garden full of other people's children is exactly the population the
constraint names, and it is *known in advance*. The station had no way to be
told. The only available workaround was `systemctl stop open-observatory`,
which is the one thing this project has direct evidence against: closing the
capture device risks it not coming back, and that is what cost this station 29
hours of recording (HANDOVER §3a).

**Decision.** A pause is a first-class operator action with a deadline.

### What "paused" means

While paused the station **persists nothing and publishes nothing**: no
detection rows, no evidence clips, no MQTT, no display pushes of new
detections. **Live listening is refused** — both the WebSocket channel and the
chunked-WAV one, on both the audible and ultrasonic bands. That last one is not
an extra: a pause anyone with the URL can listen straight through is not a
pause. The garden is exactly as audible as it was, and the operator has been
told otherwise.

**Capture keeps running. This is the trade, stated plainly.** The ALSA device is
not closed, the ring buffers keep filling, frames keep being counted and
continuity is unbroken. The alternative — stopping capture — is what an operator
would naively expect and is the wrong engineering choice here, because a privacy
control that occasionally leaves the station unable to reopen its microphone is
worse than the exposure it prevents. The 29-hour outage is evidence, not a
hypothetical. What justifies keeping capture alive is that the ring is transient
process memory, continuously overwritten, that never leaves the process:
**keeping capture running retains nothing.** What a pause stops is every path by
which audio, or a claim about it, escapes.

The gate is in one place: `Station._on_detections`, at the mouth of the
detection path, before normalisation. Everything downstream of it — the row, the
clip, the event bus, and through the bus both MQTT and the counter-top display —
is a way out of the process, so gating once means a consumer added to the bus
next year is paused by construction rather than by whoever adds it remembering
to check. Live audio is gated separately in `_handle_block`, because that path
does not go through a detection at all.

Detectors themselves keep running. Their windows, queues and lag counters stay
honest, so a pause does not make the diagnostics look like a stalled pipeline,
and the detector state an operator reads after resuming is continuous with the
state before it.

**The live spectrogram is deliberately *not* gated**, and the line is worth
naming because it looks inconsistent. What the privacy constraint protects is
people's speech and presence, and the eavesdropping vector is audio: a
spectrogram is a picture of band energy, is not intelligible, and is already
computed only while somebody is watching ([[ADR-040 - Spectrograms only when watched|ADR-040]]) and never stored. Keeping it
is also what lets an operator see, at a glance, that the station is alive and
paused rather than dead. If a future reading of the constraint disagrees, the
gate goes in `_handle_block` next to the audio one and costs nothing extra.

### Four properties, each of them a failure mode

**It expires by itself.** The operator will forget; a pause that outlives the
party costs a night of bats, which is a worse outcome than the problem it
solved. Expiry is therefore *read-side and unconditional*:
`PauseController.active` is a float comparison against a stored deadline. There
is no timer to miss and no task to crash — past the deadline it is false and
every gate reopens in the same instant, even if the database, the housekeeping
loop and the API are all broken. The housekeeping loop's `sync()` does not end
pauses; it only closes the durable row of one that has already ended.

**It survives a restart.** What is persisted is the **deadline**, never a
countdown: a remaining-seconds figure written to disk is wrong the moment the
process stops, and would silently extend every pause by the length of the
outage. `Station.start` re-adopts an open row *before* capture starts, so a
process coming back mid-pause has its gates closed before the first block
arrives. A pause whose deadline passed while the station was down is closed at
its **deadline**, not at the moment anyone noticed.

**It is recorded as a pause.** New table `capture_pause` (Alembic
`0007_capture_pause`): one row per pause, opened when it starts and closed when
it ends, carrying the deadline it was *set* to as well as when it actually
stopped, and why (`expired` / `resumed` / `superseded` / `unknown`). Charter
item 2 makes "a quiet night versus a dead microphone" a first-class
distinction; an operator pause is a third thing, and must not be mistaken for
either.

`history.coverage` reports pauses **beside** coverage and never subtracts them
from it. The station really was capturing throughout — deducting the time would
understate coverage and would look, in the record, exactly like the dead
microphone item 2 exists to distinguish. What a pause changes is whether
anything *could* have been detected, which is a different fact and gets its own
fields (`seconds_paused`, `pauses[]`). The history view draws them as amber
hatching over the coverage bar: hatched rather than solid, because the audio
underneath really was captured.

**It is obvious that it is on.** In the browser: a split button that changes
identity when active (amber, pulsing dot, countdown, "resume"), plus a
page-wide banner above everything including the first-run flow. On the
counter-top display: the pause banner, checked *ahead of every fault state* in
`display_channel.health_state`, because it is the only one of those states the
person standing in the kitchen can act on.

### Why the display gets `D` rather than a new state letter

The obvious design is a fourth `StationState` — `P` for paused — with its own
wording. It is wrong *today*, and the reason is worth recording. The firmware
already on the glass parses the state letter as
`state[0] == 'D' ? kDegraded : kListening`, so a new letter would make a paused
station read as **listening** on every display that has not yet been updated —
the exact failure this feature exists to prevent, introduced by the fix for it.
`D` puts the banner up with the pause's own wording ("PAUSED BY OPERATOR -
RECORDING RESUMES 18:30") on the firmware that is out there now, with no OTA on
the critical path of something the operator needs this afternoon.

The cost is stated rather than discovered: the *empty-state* text on a paused
display still reads "Not listening / no microphone audio is reaching it", which
is wrong. A dedicated `kPaused` presentation is deferred firmware work; until it
lands, the banner carries the meaning. The HTTP polling fallback (`parseHealth`)
also does not see the pause, because its JSON filter does not include the field,
so a display on that path shows a normal listening screen with no detections
arriving.

### Why an endpoint rather than a setting

[[ADR-048 - Web-configurable settings|ADR-048]] made every setting web-editable, so routing this through
`PUT /api/v1/settings` was available, and was rejected. A setting describes how
the station behaves indefinitely; this is an action with a deadline, taken
repeatedly, not persisted to `runtime.env`, and it must be one request from a
control on the main page. Wiring it through the settings writer would also mean
a privacy action waiting on a file write.

What *is* a setting, through [[ADR-048 - Web-configurable settings|ADR-048]]'s mechanism: `pause_presets` (which
durations the drop-down offers) and `pause_default_preset` (what is
pre-selected before this browser has chosen). Both live-tier, in a new
**Privacy** category. `POST /api/v1/pause` accepts any *known* preset rather
than only the currently offered ones — the setting decides the menu, and
refusing a key that a browser tab loaded ten minutes ago would turn a settings
edit into a failed privacy action at the moment it is least welcome.

`until-midnight` is resolved **on the station**, in the configured IANA zone.
The browser's zone is whichever laptop happens to be open; the station's is the
one every other time in this system is already presented in. Pressed at 23:58 it
is two minutes, which is correct — "until midnight" is the end of the operator's
day, not 24 hours.

### Honesty

Health reports the pause as a **note**, never a `problems` entry. It is a
deliberate act, not a fault, and `problems` would flip
`binary_sensor.<station>_station_healthy` and make every alerting rule in the
house treat a birthday party as an outage. Saying nothing at all would be the
quiet omission the honesty constraint forbids, so: a note, plus a top-level
`pause` object on `/api/v1/health` and `/api/v1/station`, plus `oo_paused`,
`oo_pause_remaining_seconds` and `oo_pause_detections_suppressed_total` in
Prometheus. `oo_capture_state` deliberately stays `1` — an alert that cannot
tell a paused station from a dead one is charter item 2's failure in Prometheus
form.

Suppressed detections are counted (`pause.detections_suppressed`) and surfaced
in the station snapshot, on [[ADR-049 - Sound categories are not species|ADR-049]]'s reasoning: a privacy control whose effect
nobody can see is a promise rather than a mechanism.

Persistence is best-effort and deliberately cannot block the pause: the
in-memory deadline is set before anything is written, and every database call in
`pause.py` swallows and logs. Losing the *record* of a pause is a documentation
loss; failing to *engage* a pause because a disk was full is a privacy failure.

### Cost

Two comparisons against a cached float: one per capture block (10 Hz) in
`_handle_block`, one per detection batch. No lock, no database, no allocation on
either path. Charter item 1 is untouched — nothing here can cost a frame of
audio, and `tests/test_api.py::TestOperatorPause` asserts that frames keep
arriving throughout a pause.

### Migration

Alembic head moves `0006_refinement` → `0007_capture_pause`. Purely additive:
one new table, no column added to and no data read from any existing one.

### Rollback and smoke test (ADR-055)

`git revert` removes the endpoint, the control and the gates; the station
returns to recording continuously. The `capture_pause` table can be left in
place (nothing else refers to it) or dropped with
`alembic downgrade 0006_refinement`, which discards the record of every pause
ever taken — after which those windows read as ordinary capture with nothing
detected in them. `pause_presets` / `pause_default_preset` in an operator's
`config/runtime.env` become unread keys, which `RuntimeEnvStore` preserves.

**Reviewed 2026-08-29:** the decision holds, but the downgrade above is no
longer safe as written. `0007_capture_pause` was the head when this was
written; the head is now `0011_retention_live_asset_indexes`, so
`alembic downgrade 0006_refinement` would unwind four later revisions as well
and drop `detection.kept_at` and `detection.kept_by` — every operator keep flag
([[ADR-061 - Operator keep flag|ADR-061]]) with them. To discard only the pause records, drop the
`capture_pause` table by hand; the rest of the rollback is unaffected.

**If a station is ever stuck paused with the API unreachable**, the pause can be
cleared directly and the service restarted:

```bash
sqlite3 <data_dir>/openobservatory.sqlite \
  "UPDATE capture_pause SET ended_utc = CURRENT_TIMESTAMP, end_reason = 'resumed'
   WHERE ended_utc IS NULL;"
```

```bash
# 1. The menu and the state, in one request.
curl -s http://<station-host>:8080/api/v1/pause | python3 -m json.tool

# 2. Pause for fifteen minutes, and read back the deadline.
curl -s -X POST http://<station-host>:8080/api/v1/pause \
  -H 'content-type: application/json' -d '{"preset": "15m"}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["active"], d["ends_utc"], d["banner"])'

# 3. Live listening is refused (expect 503, and the pause banner as the detail).
curl -s -o /dev/null -w '%{http_code}\n' http://<station-host>:8080/api/v1/live/audio.wav
curl -s http://<station-host>:8080/api/v1/live/audio.wav | head -c 200; echo

# 4. Capture is still running -- frames must keep climbing.
for i in 1 2 3; do
  curl -s http://<station-host>:8080/api/v1/station \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["capture"]["frames"])'
  sleep 2
done

# 5. Paused, still capturing, and persisting nothing new.
curl -s http://<station-host>:8080/metrics \
  | grep -E '^oo_(paused|capture_state|detections_persisted_total) '

# 6. Health says so as a note, not a problem (expect no pause entry in problems).
curl -s http://<station-host>:8080/api/v1/health \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["problems"], d["notes"])'

# 7. The counter-top display. Watch the glass: it should read
#    PAUSED BY OPERATOR - RECORDING RESUMES HH:MM.

# 8. Resume early, in one request, and confirm listening comes back.
curl -s -X DELETE http://<station-host>:8080/api/v1/pause \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["active"])'
curl -s -o /dev/null -w '%{http_code}\n' http://<station-host>:8080/api/v1/live/audio.wav

# 9. Restart survival, on the real device.
curl -s -X POST http://<station-host>:8080/api/v1/pause \
  -H 'content-type: application/json' -d '{"preset": "1h"}' > /dev/null
ssh <user>@<station-host> 'sudo systemctl restart open-observatory'
sleep 15
curl -s http://<station-host>:8080/api/v1/pause \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["active"], d["ends_utc"])'
curl -s -X DELETE http://<station-host>:8080/api/v1/pause > /dev/null

# 10. The gap is recorded as a pause, not left as an unexplained hole.
curl -s 'http://<station-host>:8080/api/v1/history?window=today' \
  | python3 -c 'import json,sys; c=json.load(sys.stdin)["coverage"]; print(c["seconds_paused"], c["pauses"])'
```

---
Part of the [[ADRS|Architecture Decision Record index]].
