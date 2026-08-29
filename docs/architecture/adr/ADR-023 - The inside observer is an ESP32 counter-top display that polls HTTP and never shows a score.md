---
aliases:
  - ADR-023
tags:
  - adr
---
# ADR-023: The inside observer is an ESP32 counter-top display that polls HTTP and never shows a score
> **Transport superseded by [[ADR-038]] (2026-08-09).** The display is now pushed to
> over a lean WebSocket (`GET /api/v1/display`) served by the Pi itself. HTTP
> polling remains in the firmware as a real, exercised fallback, so the "why HTTP
> polling rather than MQTT" reasoning below still describes running code — but it
> is no longer the primary path, and the cost figures it was chosen under turned
> out to be the reason to move: ~315 ms of station query time and ~127 kB of
> payload every 20 s, to render six rows. A detection now costs 49 bytes and
> arrives when it happens. The presentation of times also changed: elapsed
> ("4s ago"), ticking once a second, not clock times. **Everything else in this
> ADR stands unchanged** — the board, the partition table and its NVS
> inheritance, the no-PSRAM sprite strategy, and every honesty rule: no score, no
> percentage, no confidence figure, bat passes never named, and three visibly
> distinct failure states. [[ADR-038]] tightens the score rule rather than relaxing
> it: the field no longer exists on the wire at all.

**Decision:** The "inside observer" — the ambient display that lives in the house and
shows what the garden station is hearing — is firmware for a **DIYmalls / Sunton
ESP32-2432S028R** ("Cheap Yellow Display"), written against PlatformIO with every
dependency pinned, at `firmware/inside-observer/`. It reads the station over
**HTTP polling** of the existing read-only REST API, not MQTT. It renders species
names and local times only: **no score, no percentage and no confidence figure of
any kind reaches the glass**, and bat passes are never given a species name.

### Why this device

The board was already in the house running *Aura*, a weather-forecast display
project it was originally built for, and the
operator asked for it to be repurposed. It is adequate and it is present, which beats
a better board that is not: 240x320 ILI9341 in portrait is enough for six large rows,
a title and a footer; a resistive touch panel is enough for a settings page with
finger-sized targets.

Its two real constraints shaped the implementation. There is **no PSRAM**, so a full
240x320x16bpp framebuffer (150 kB) is impossible and rendering goes through a single
reused 240x40 sprite (19 kB) instead. And the station's detection payload is large —
about 1.8 kB per row, mostly evidence-media checksums — so responses are stream-parsed
through ArduinoJson filters and only the six rendered fields are ever materialised.
Measured result: 48.8 kB static RAM, 962 kB of the 3 MB app partition, ~219 kB free
heap in steady state, flat across a five-minute soak.

The partition table is a byte-for-byte copy of the stock firmware's, read back from a
full-flash backup image. That is not tidiness: it keeps NVS at 0x9000, which is where
the ESP32 WiFi stack stored the credentials the operator provisioned under the stock
firmware. A normal upload does not erase NVS, so `WiFi.begin()` with no arguments
reconnects on first boot without anyone typing a password and **without this firmware
ever reading, copying or logging one**. Where that inheritance is absent, the device
raises its own open AP named `Aura` — the same name the stock firmware used — and
serves a captive portal. Credentials are never hardcoded, never committed, and never
requested out of band.

### Why HTTP polling rather than MQTT

The MQTT publisher specified in [[API_AND_INTEGRATIONS]] does not exist. No code in
this repository publishes to a broker. Building the display against a transport that
has not been written would have made the display's correctness untestable and its
delivery contingent on someone else's milestone.

> **Status 2026-08-08:** that premise no longer holds — [[ADR-025]]'s MQTT publisher
> shipped later the same day and is live against the operator's real broker. The
> *decision* stands unchanged: the display still polls HTTP, `StationSource`
> remains the seam an `MqttStationSource` would drop into, and the broker settings
> are already persisted in NVS so switching the feed needs no reprovisioning.
> Nothing about this ADR needs undoing; only this paragraph's premise is dated.

The REST API, by contrast, is live, read-only, documented and already carries
everything the display needs — including the `include_synthetic` exclusion of [[ADR-020]],
which matters here more than anywhere: a counter-top display is exactly the "browsing view"
that must not present a test scene as an observation.

Polling is therefore an interim transport, not a rejection of MQTT. `StationSource` is
an abstract seam with one implementation today; another implementation satisfies the
same interface without the UI changing. ([[ADR-038]] is what that seam was for: a
`PushStationSource` was dropped in alongside this one. It did **not** turn out to be
MQTT — the broker lives on the Home Assistant box, and making two devices that are
the same system depend on a third is the opposite of local-first.) The broker settings (host, port, credentials,
topic prefix) are already in the settings model, already in the provisioning portal,
and already persisted, so the operator's configuration survives the firmware update
that switches the feed over.

One consequence worth recording: the display has no clock and no IANA timezone
database, and does not run NTP. It gets the station's true local offset — DST included
— from `range.start_utc` of the `today` window in `GET /api/v1/history`, which is the
station's own local midnight expressed in UTC. UTC internally, local for presentation,
with the station remaining the single authority on what local means. **[[ADR-038]]
removed the need for this on the primary path**: elapsed times need no timezone at
all, only a monotonic base and an epoch anchor, and the derived offset is now unused
by the feed.

### Why no numeric score is displayed

**A BirdNET score is not a calibrated probability.** `detector.calibrated` is `false`
and `calibrated_probability` is `null` on every row the station emits. Rendering 0.92
as "92%" would state a confidence that the identification is correct, which is not what
the number means and not something this system can currently claim. The normaliser
already refuses to let a non-taxonomic detector emit a species name; this is the same
rule carried to the presentation layer, where the misreading actually happens — nobody
misreads a JSON field, but everybody misreads a percentage on a counter top.

So a single configurable threshold decides which named detections appear, and the
number itself does not reach the screen. On the device it is presented as a named step
— "Only the clearest", "Confident", "Balanced" (the 0.75 default), "Inclusive",
"Everything named" — with no figure attached. The raw value remains settable from the
provisioning portal, which is a page with room for the sentence that has to accompany
it: that this filters what gets named, that it is not a probability, and that it does
not apply to bats.

**Bat passes bypass the threshold entirely and are never named.** `ultrasonic-pass-v1`
detects passes, not species ([[ADR-013]]). Its score is a pulse-train confidence about
whether *something* passed, not about *what*, so filtering passes by it would hide real
observations on the strength of an irrelevant number, and naming them would invent an
identification the detector cannot make. A pass therefore renders as "Bat pass" with
its peak frequency — "36.2 kHz" — which is a measurement rather than a claim.

Two smaller honesty rules fall out of the same principle. BirdNET's non-taxonomic
classes (`Engine`, `Siren`, `Human vocal`) arrive from the station with `rank` of
`species` but with the scientific name equal to the common name; they are not species,
they are not shown as species, and they are not counted in "species today". And the
footer count is computed under the same threshold as the feed, so the number of species
claimed always agrees with what a person can actually see listed.

### Why a silent screen must not look like a broken one

The failure that motivates this is the same one behind [[ADR-020]]. When the AudioMoth's
mode switch was moved [date corrected 2026-08-09: **2026-08-08**, matching [[ADR-020]]
and [[TARGET_DIAGNOSTICS]]; this ADR said 2026-08-05] the station fell back to a synthetic source,
correctly reported itself degraded, and kept detecting — and every browsing view that
existed at the time showed the results as observations. An ambient display is worse
than a debug UI here, because the whole point of it is to be glanced at rather than
read.

The display therefore renders three distinct states and never an ambiguous blank:
**offline** (red rule, "STATION UNREACHABLE", and any surviving feed labelled `(stale)`
in the footer), **degraded** (amber rule carrying the station's own reason, e.g.
"NO MICROPHONE - SYNTHETIC SOURCE"), and **empty but listening** ("Nothing yet", with
the reason underneath). All three are visible from across the room.

### Constraints this ADR imposes

* Any future transport for this display implements `StationSource`; the UI must not
  learn about a protocol.
* No rendering path may display a score, a probability, a percentage or the threshold
  value. The host test `test_no_feed_item_ever_carries_a_score` is the regression
  guard, and `FeedItem` deliberately has no field to carry one.
* A bat pass must never acquire a species name or a score in any view.
* Anything that lists detections as observations honours [[ADR-020]]'s synthetic
  exclusion. The display requests `include_synthetic=false` explicitly rather than
  relying on the default.

---
Part of the [[ADRS|Architecture Decision Record index]].
