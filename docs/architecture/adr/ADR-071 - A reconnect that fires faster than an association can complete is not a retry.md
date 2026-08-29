# ADR-071: A reconnect that fires faster than an association can complete is not a retry
**Status:** accepted, 2026-08-24
**Component:** `firmware/inside-observer`
**Supersedes nothing.** Fixes behaviour introduced with the push transport
(ADR-038).

### Observation

After a WiFi drop the inside observer did not rejoin the network. It stayed
down until it was power-cycled, at which point it came back immediately and
behaved normally.

### What was actually wrong

The firmware retried constantly. That was the problem.

`loop()` asked for a reconnect from inside the block whose next-due time is
`PushStationSource::serviceIntervalMs()`:

```cpp
if (WiFi.status() != WL_CONNECTED) {
  Serial.println("[wifi] link lost; reconnecting");
  WiFi.reconnect();
}
```

That interval is **10 ms** — it services a socket, it does not fetch anything —
and it is reset unconditionally on every pass, so while the link was down the
call ran a hundred times a second.

On the pinned core (`framework-arduinoespressif32` 3.20017, the one
`platformio/espressif32@6.11.0` installs), `WiFi.reconnect()` is not a nudge:

```cpp
bool WiFiSTAClass::reconnect() {            // libraries/WiFi/src/WiFiSTA.cpp:329
    if(WiFi.getMode() & WIFI_MODE_STA) {
        if(esp_wifi_disconnect() == ESP_OK) {
            return esp_wifi_connect() == ESP_OK;
        }
    }
    return false;
}
```

It tears the association down and starts a new one. Association, authentication
and DHCP take seconds. Each attempt was therefore destroyed by its successor
roughly two hundred times before it could have finished. **Retrying at loop
speed reconnects strictly less often than retrying slowly** — the limit of
trying harder, here, is never succeeding at all.

A power cycle worked because boot takes a different path entirely:
`connectWifi()` calls `WiFi.begin()` once and then waits, patiently, for 25
seconds.

### Why `setAutoReconnect(true)` did not save it

`connectWifi()` sets it, and it is worth keeping, but it cannot be relied on for
the most likely disconnect of all. In the same pinned core:

```cpp
bool DoReconnect = false;                   // libraries/WiFi/src/WiFiGeneric.cpp:1077
if(reason == WIFI_REASON_ASSOC_LEAVE) {     // Voluntarily disconnected. Don't reconnect!
}
```

Reason 8 skips every reconnection path, `autoReconnect` included. A router that
reboots, drops a client, or moves it between bands deauthenticates with exactly
that reason. On that disconnect the firmware's own policy is the only thing that
will act — and it was the broken part.

### Decision

Reconnect decisions move into `src/model/wifi_policy.{h,cpp}`, pure and
host-tested, alongside `model/ota_policy` and for the same reason: this display
sits on a shelf in someone's house, so the rules that decide "try again now"
are tested on a laptop rather than discovered three days later.

- **Grace, 3 s.** A blip is ridden out rather than reconnected, and the stack's
  own `first_connect` retry — which fires immediately — is given room to work
  before being interrupted.
- **Backoff, 5 s doubling to a 60 s ceiling.** No two attempts can be closer
  together than an association needs. The ceiling is what keeps an outage that
  ends after two hours from being slept through.
- **It never gives up.** There is no exhausted state. Nobody is coming to press
  the button.
- **Recovery resets the backoff**, so the second outage of the day does not
  start at a one-minute interval it did nothing to earn.
- **Deadlines compare through a signed difference.** `millis()` wraps every
  49.7 days and this display is meant to outlast that; a plain `>=` across the
  wrap parks the next attempt seven weeks out, which is indistinguishable from
  the bug being fixed.

Recovery also moves **out of the feed-gated service block** into `serviceWifi()`,
called on every pass. The old placement meant a display left on the settings
screen when the WiFi dropped would never have attempted a reconnect at all.
`serviceWifi()` returns early while the provisioning portal is up: the radio is
in `AP_STA`, the operator is typing into it, and a submit restarts the device
anyway.

### Explicitly not decided: rebooting after a long outage

Tempting, because it is what the operator did and it worked. **Rejected for
now, because the boot path is not safe to re-enter unattended.** `setup()` gives
up after 25 s and calls `enterPortal()`, and nothing in `loop()` retries STA
while the portal is running — so a display that reboots during a router outage
lands in provisioning mode and stays there until a person touches the screen.
That is worse than waiting, and it is a live risk on this installation: the
station's router shares the mains that has now failed twice this month, so
"display reboots while the router is still down" is the expected case, not the
exotic one.

**Follow-up, not done here:** make the boot-failure path keep retrying STA in
the background while the portal is up, and leave the portal by itself if the
link comes back. Only once that holds is an outage-triggered reboot worth
revisiting.

### What the display shows while it is trying

Requested by the operator after the fix landed: the screen should say that
something is being done about it, without becoming a status readout.

A struck-out WiFi glyph and a countdown to the next attempt, in a reserved
52 px column in the footer, immediately left of the settings dots. Absent
entirely while the link is up — there is no "WiFi OK" state, because a working
network is the normal condition of this object and does not need announcing.

Three details that are not arbitrary:

- **The countdown is read from the same `WifiPolicy` that drives the radio**,
  published onto `StationSnapshot` once per pass. An indicator keeping its own
  copy of the schedule would drift out of step, and a countdown that lies about
  when something will happen is worse than no countdown.
- **It has its own column and its own 52x18 sprite**, and the footer key
  deliberately excludes the seconds. The whole feed screen repaints only when a
  region's *content* changes; folding a ticking number into the footer key
  would repaint the species count and the settings dots once a second, which is
  precisely the flicker that mechanism exists to prevent. Same bargain
  `tickRelativeTimes` makes for the row ages.
- **Zero renders as "now", not "0s".** Zero means an attempt is in flight. A
  countdown sitting at "0s" reads as one that has stalled.

The footer is a 30 px strip that also carries the species count and the
settings affordance, so two things give way while the indicator is up. The
`(stale)` suffix is dropped — it and the struck-out glyph say the same thing,
and the glyph says it better. And `waiting for the station` is suppressed: when
the missing thing is the network the station may be perfectly happy, and
blaming it sends whoever reads it to the wrong end of the house. With no count
yet to show, the left side stays empty and the indicator carries the whole
message. The remaining text is truncated against a measured width rather than
an assumed one, so no future string can overprint the glyph.

`logRenderedScreen()` prints the indicator in words. Without a camera on the
glass that log is the only record of what the footer actually showed, and "was
the display telling anyone it was still trying" is the question the indicator
exists to answer.

### Consequences

- A drop now costs at most 3 s plus one association, instead of forever.
- The serial console becomes readable during a WiFi fault. The old code printed
  `[wifi] link lost; reconnecting` a hundred times a second, burying every other
  message during exactly the fault you were trying to read about. Transitions
  now get one line each, and attempts one line per attempt.
- Nothing changes while the link is healthy: `evaluate()` returns
  `kNothing` and touches no radio state.

### Verification

`pio test -e native` — 98 cases, was 91. The seven new ones are
`test/test_wifi/test_wifi.cpp`. Confirmed red first: reverting `evaluate()` to
the old always-attempt-while-down behaviour fails three of them, including the
regression proper, which reports `Expected 10 to be greater than or equal to
3000` — the 10 ms spacing, measured.

`pio run -e cyd` builds: flash 55.6% of the app slot, RAM 15.6%.

**Not verified on hardware.** This has not been flashed to the display. The
fix is a cadence change in a state machine that has no hardware dependency, but
the claim "the display now recovers from a WiFi drop" is not evidence until
someone drops the WiFi and watches it come back.

### Rollback

Revert `serviceWifi()` and its call, restore the reconnect block inside the feed
service block, and delete `src/model/wifi_policy.*` and `test/test_wifi/`.
Nothing is persisted, no NVS key changes, and the firmware version bump is the
only thing an already-flashed display would notice.

### Smoke test

On the bench, with the display running and joined:

    # watch the console, then take the AP down for two minutes
    pio device monitor -e cyd

Expect one `[wifi] link lost` line, then attempts at roughly 5, 10, 20, 40 and
60 s, then `[wifi] link restored after Nms` within a minute of the AP returning.
Anything printing faster than once a second is this bug back again.
