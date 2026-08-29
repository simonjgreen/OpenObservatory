# ADR-038: The inside observer is pushed to over a lean WebSocket, and shows elapsed times
**Decision:** Replace the counter-top display's HTTP polling (ADR-023) with a push
channel served by the station itself — a new WebSocket at `GET /api/v1/display`
carrying detections only, in compact JSON sized to fit a single Ethernet MTU
several times over. The display renders **elapsed** times ("4s ago", "1m ago",
"1h ago") that tick once a second off a monotonic base, rather than clock times.
HTTP polling stays in the firmware as a real, exercised fallback.

Measured on the live station: **49 bytes for a named detection, 40 for a bat
pass, 43 for the heartbeat, 294 for the connect snapshot with six real species
names** — a 90-second sample over Wi-Fi cost **1,030 bytes, 11.4 B/s**.

### Why polling had to go

Per 20 s poll cycle, the display cost the station:

| Request | Server time | Payload |
|---|---|---|
| `/api/v1/health` | 18 ms | 2.7 kB |
| `/api/v1/detections?limit=40&identified_only=true&min_score=0.75` | 140 ms | 71.4 kB |
| `/api/v1/detections?limit=8&group=bat` | 44 ms | 23.4 kB |
| `/api/v1/history?window=today` | 112 ms | 29.5 kB |
| **total** | **~315 ms** | **~127 kB** |

Forty detection records, ~1,785 bytes each, fetched to render six rows. The
device already threw away almost all of it — ADR-023's ArduinoJson stream filters
exist precisely because it could not afford to hold it — so the waste was never
the ESP32's, it was the Pi's.

Making the display *feel* live by shortening the poll was the obvious move and
the wrong one. 315 ms of query work every 3 s is ~3x the event-loop duty cycle
this station has already been shown not to absorb: ADR-033's retention sweep
doing 0.3–0.4 s of synchronous work every 10 s starved the loop by 55–150 ms and
produced ~1.9 capture gaps a minute. Capture always wins, so the answer was to
stop asking rather than to ask harder. **11.4 B/s and one connection replaces
~6,350 B/s and 720 requests an hour** — and the display is now *more* live, not
less, because a detection reaches the glass when it happens instead of up to 20 s
later.

### Why a new endpoint rather than a mode of `/api/v1/live`

`/api/v1/live` is the debug UI's visual channel and almost none of its vocabulary
is usable here: binary spectrogram columns an ESP32 has no screen for, a `hello`
carrying forty full detection records and sixty events, a full station snapshot
every two seconds, and a 30-second spectrogram backfill on connect. A "filtered
subscription" would have had to replace every frame that socket sends — which is
not a filter, it is a different channel sharing a URL.

It would also have meant editing the one socket ADR-012 warns hardest about. That
bug — concurrent writers, flawless on loopback, near-total failure over Wi-Fi —
cost more to find than anything else in this project, and ADR-012's constraint is
that any change to those channels must be re-measured from a real browser over
the real network before it is believed. A separate endpoint leaves the debug UI's
transport bit-for-bit untouched and lets this one be exactly as small as it needs
to be. **The single-writer rule is not relaxed:** `DisplayClient.run()` is the
only code in the process that writes a display socket; the pump and the receive
loop only ever call `offer()` and `receive_text()`.

### Why not MQTT, when the publisher now exists

ADR-025 shipped the MQTT publisher, so routing the display through it was
available. It was rejected: the broker runs on the Home Assistant box, and the
station and the display are the same system in the same house. Publishing garden
detections to a third machine so a device on the same LAN can read them back
makes the counter-top display depend on a box neither of them needs, and CLAUDE.md's
local-first rule is not "cloud-free", it is "core function needs nothing else
running". The display talks to the Pi. The `MqttSettings` struct stays in NVS —
unused, and now documented as such.

### The wire format, and what is deliberately absent

Three frame types, compact JSON, whitespace stripped, keys of one or two
characters. Full field table in `docs/api/DEBUG_UI_TRANSPORT.md`.

```
hello       {"t":"h","v":1,"now":1786263065,"hb":10,"st":"L","sp":30,
             "f":[{"n":"Common Woodpigeon","at":1786263056,"r":5}, ...]}
detection   {"t":"d","n":"Common Woodpigeon","at":1786263086}
heartbeat   {"t":"s","now":1786263075,"st":"L","sp":30}
```

What is **not** on this wire, and cannot be added without editing both
`display_channel.py` and `push_frame.h`: `native_result`, the media list with its
checksums and byte lengths, every UUID, detector plugin/model/licence metadata,
`rank`, `canonical_taxon_id`, `duration_s`, frame bounds, `stream_id` — and
**`score`**. ADR-023's rule that no number a person could read as a confidence
figure reaches the glass is now structural rather than behavioural: the threshold
is applied by the station, expressed in the URL the socket was opened with, and
the number never leaves the Pi. A bat pass carries no name at all, only `b:1` and
a peak frequency; the words "Bat pass" are supplied by the firmware, so no future
server change can put a species on a pass. `title_hint`'s frequency-band candidate
("45 kHz · common pipistrelle?") is deliberately *not* forwarded — it is a
legitimate hint in a browsing UI that can carry the sentence explaining it, and a
species claim on a counter top.

Server-side filtering is the point, not an optimisation. The device sends its
threshold and its bat switch as query parameters and receives nothing it would
discard. Changing either setting reconnects, because the filter lives in the URL;
the firmware restarts on Save to make that unambiguous.

**Snapshot then deltas.** A display that connects at four in the afternoon must
not be blank, so connect costs one short, column-limited SQL read (six rows after
run-collapsing, plus one `DISTINCT` for today's species) run off the event loop —
once per connection, not once per 20 s. `species today` is then tracked
incrementally in a set: a species counts exactly when one of its detections
cleared the threshold, which is exactly when a frame was sent, so the number stays
identical to what the 112 ms history query returned, without the query.

**Bounded, with an explicit drop policy**, like every queue here: 64 frames, and
the *oldest detection* is shed first. Never a status frame — losing the banner to
a burst of woodpigeons would make a broken station look merely quiet, which is the
exact failure ADR-023 exists to prevent. Counters surface in `/api/v1/station`
under `display_channel`, including `mean_frame_bytes`, so this ADR's headline
number is checkable at any time rather than only at the moment it was written.

### Why elapsed times, and what happens past a day

Clock times answer a question nobody asks an ambient object. "21:04" requires the
reader to know what time it is and do arithmetic; "4s ago" answers *is this
happening now?* directly, which is the only question a glance is asking. Ticking
matters as much as the format: without it, a screen showing "12s ago" is lying
within a second of being painted.

Thresholds and rounding, chosen and stated: `< 1 s` reads "now"; seconds to 59;
minutes to 59; hours to 23; then days, saturating at "99d+ ago". Rounding is
**floor** throughout, so "1m ago" means at least a minute and less than two — the
weaker claim, and the one that makes the second-by-second count read as a count
rather than as a value jittering across a boundary. A **negative** age renders as
"now", not "-1s ago": the anchor is only re-taken on a heartbeat so fractional
negatives are routine, and a minus sign on a counter top reads as a fault.

Past a day the unit deliberately stops getting coarser. Weeks and months were
considered and rejected: a feed row older than a day means the garden has been
silent for a day, which is a fault report rather than an observation, and "3d ago"
says that plainly where "last week" would soften it. The 99-day cap exists so the
string can never outgrow its column.

### The clock problem, and the trap avoided

This board has no RTC and does not run NTP. Before this change that did not
matter — the display asked the station when something happened and printed it, so
it never needed to know what time it was. An elapsed time needs to know every
second, and to keep knowing while the feed is down.

The trap: subtracting an event's timestamp from a wall clock that can jump. Anchor
directly to the station's epoch and an NTP step, a reconnect, or a late heartbeat
would move every row on the screen at once — rows ageing backwards, or leaping
forward by however far the clock moved. So `StationClock` counts from `millis()`,
which only ever advances at one second per second, and the station's `now` is used
solely to establish the offset between the two. The offset is re-taken only when
it is out by ≥ 2 s, because re-anchoring on every heartbeat would let sub-second
network jitter push a row back and forth across a whole-second boundary. Uptime
accumulates from *deltas*, so it survives the 32-bit `millis()` wrap at 49.7 days
— which an object left on a shelf actually reaches. All of it is host-tested,
including the wrap.

The HTTP fallback anchors from `checked_at` in `/api/v1/health`, which is
generated as the response is built and is the closest thing that transport has to
a "now".

### Why the tick does not redraw the screen

Repainting 240x320 once a second would flicker and would burn CPU on a device
whose job is to sit still. The elapsed time therefore gets a reserved,
fixed-width 72 px column at the left of each row's second line — fixed so the
detail that follows does not shuffle sideways as "9s ago" becomes "10s ago" — and
its own 72x18 sprite (2.6 kB, against the row sprite's 19 kB).
`tickRelativeTimes` compares each row's rendered string against what is on the
glass and pushes the small sprite only where the words changed. A row whose age is
measured in minutes costs nothing at all, 59 seconds out of 60. Species names are
on a separate cache key and are never repainted by the clock. Measured on the
device: **~60 partial repaints per minute** — one per second, for the one row that
is changing — with free heap flat at 213–215 kB.

One trap found while measuring this, worth recording because it hid the whole
mechanism: the socket service block runs every 10 ms, and calling `showFeed` from
it unconditionally meant the ages were in fact being updated there, at 100 Hz,
with the one-second tick finding nothing left to do. It was invisible from the
glass (the output is identical) and visible only in the repaint counter, which is
why the counter is now permanent and in the log. `showFeed` is called only when
the frame count or the station state actually moved.

### Honesty properties, all preserved

No score anywhere, now enforced by the absence of the field rather than by the
client's restraint. Bat passes always sent, never scored, never named. The three
distinct states survive: **degraded** carries the station's own words, mapped
server-side by the same decision tree the firmware runs for the fallback (so the
two transports cannot describe one station differently); **offline** is a fact
only the device can know, so the station never sends it. Detections are **not**
pushed at all while the station is not on the real microphone (ADR-020): the
banner explains why, and the feed does not quietly fill with a test scene.

**A stale feed must look stale, not merely quiet**, and on this device silence is
the normal state — the absence of detections proves nothing. Only the 10 s
heartbeat does. Three missed beats (30 s) puts the red rule and "STATION
UNREACHABLE" up and marks the surviving feed `(stale)` in the footer, while the
elapsed times keep counting, which is itself honest: a top row reading "23m ago"
in a garden that is usually noisy says something a blank screen would not.

### Consequences and what a successor should know

- The firmware's `use24hClock` setting is now vestigial. It survives in NVS (so no
  config migration is needed) but decides nothing, and its row on the settings
  page has been replaced with a read-only report of which transport is actually
  feeding the glass — because "it is running on the fallback" should be a visible
  fact, not a guess.
- Falling back is deliberately slow (60 s of a dead socket) so that a Wi-Fi hiccup
  or a station restart does not put 127 kB per 20 s straight back on the wire.
- `links2004/WebSockets@2.7.3` is a new pinned firmware dependency. It needs
  explicit `-I` paths to the framework's WiFi libraries: its network class is
  chosen by a macro chain the library dependency finder cannot evaluate, so LDF
  never records WiFi as a dependency *of that library* and it compiles without it
  on its include path. Recorded in `platformio.ini` where it bites.
- `/api/v1/display` is in `auth_public_read_paths` by default, for the same reason
  `/api/v1/detections` is: an ESP32 has no login flow. It is not a GET, so the
  HTTP gate never sees it; the WebSocket handler consults the same list so the
  display's two transports are exempt together or not at all.
- `history.is_live`/`is_not_live` were annotated `ColumnElement` while every call
  site passes an `InstrumentedAttribute`. Widened rather than silenced while
  adding the ninth call site; `mypy src` drops from 29 pre-existing errors to 22.
- Firmware cost: 1,122,736 bytes of the 3 MB app partition (was 962 kB) and 50,708
  bytes of static RAM (was 48.8 kB).
- **Not verified:** the 72-hour soak, and the display's behaviour across a
  multi-day silence — nothing has yet aged past a few hours on the real device, so
  the "1d ago" path is host-tested only. The Wi-Fi-loss path was not exercised on
  hardware either.
