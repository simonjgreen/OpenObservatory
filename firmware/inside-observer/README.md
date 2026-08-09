# Inside observer

A wall or desk display that shows, calmly, what the garden acoustic station is
hearing. Species name and how long ago, newest first, ticking once a second. A
footer line counting today's species. Nothing else.

It is an ambient object, not a dashboard. It is meant to be legible from the
other side of a room and unremarkable when nobody is looking at it.

## What it will and will not show

| | |
|---|---|
| Species name and how long ago ("4s ago", "1m ago") | yes |
| A score, a percentage, a confidence figure | **never** |
| A bat species | **never** — `ultrasonic-pass-v1` detects passes, not species |
| A bat pass with its peak frequency | yes, always, whatever the threshold |
| BirdNET's non-taxonomic classes (`Engine`, `Human vocal`, …) | no |
| Whether the station is offline, degraded, or genuinely quiet | yes, distinctly |

A single configurable threshold decides which named detections appear. The
number behind it never reaches the glass: on the device it is presented as a
named step ("Balanced", "Confident", …), and the numeric value is settable only
from the provisioning portal, where there is room to explain that a BirdNET
score is a model output and not a calibrated probability. See ADR-023 in
`docs/architecture/ADRS.md`.

Three failure states are visibly distinct, because a screen that looks silent
must not be confusable with a screen that is broken:

* **Empty** — "Nothing yet", with the reason underneath.
* **Offline** — red rule and "STATION UNREACHABLE"; any feed left on screen is
  labelled `(stale)` in the footer.
* **Degraded** — amber rule and the station's own reason, e.g.
  "NO MICROPHONE - SYNTHETIC SOURCE". The station keeps detecting against a
  synthetic source when the AudioMoth goes away (see ADR-020), and those rows
  are not observations of the garden.

## The board

**DIYmalls / Sunton ESP32-2432S028R**, the Sunton "Cheap Yellow Display" (CYD).

| | |
|---|---|
| SoC | ESP32-D0WD-V3 rev 3, 2 cores @ 240 MHz, **no PSRAM** |
| Flash | 4 MB — app0 3 MB @ 0x10000, SPIFFS 896 KB @ 0x310000, NVS 20 KB @ 0x9000 |
| Panel | 240x320 **ILI9341** over SPI, used in portrait |
| Touch | **XPT2046 resistive**, on a *separate* SPI bus from the display |
| USB serial | CH340, `/dev/ttyUSB0` |

Panel controller confirmed on the actual unit, not assumed — the firmware reads
it at boot:

```
[display] panel probe: RDDID(0x04)=0x000000 RDID4(0xD3)=0xFF934141 RDDST(0x09)=0xD2D2D2D2
```

`RDID4` returning `93 41` identifies an ILI9341. (This board also ships in an
ST7789 revision — see "If the display looks wrong" below.) `RDDID (0x04)` reads
back as zero on this unit; that is a known quirk of these panels, not a fault,
which is why the firmware reads `0xD3` as well.

### Pin map

Every value below comes from a published reference, not from inference. The two
sources agree on every one.

* **[1]** witnessmenow/ESP32-Cheap-Yellow-Display —
  [`PINS.md`](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display/blob/main/PINS.md),
  [`DisplayConfig/User_Setup.h`](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display/blob/main/DisplayConfig/User_Setup.h),
  [`Examples/Basics/2-TouchTest`](https://github.com/witnessmenow/ESP32-Cheap-Yellow-Display/blob/main/Examples/Basics/2-TouchTest/2-TouchTest.ino)
* **[2]** Random Nerd Tutorials —
  [CYD pinout](https://randomnerdtutorials.com/esp32-cheap-yellow-display-cyd-pinout-esp32-2432s028r/),
  [CYD getting started](https://randomnerdtutorials.com/cheap-yellow-display-esp32-2432s028r/),
  [display + touch + microSD](https://randomnerdtutorials.com/esp32-cyd-display-touchscreen-microsd-card/)
* **[3]** [TFT_eSPI discussion #3018](https://github.com/Bodmer/TFT_eSPI/discussions/3018) — panel revisions and colour settings

| Function | GPIO | Bus | Source |
|---|---|---|---|
| TFT_DC | 2 | HSPI | [1][2] |
| TFT_MISO | 12 | HSPI | [1][2] |
| TFT_MOSI | 13 | HSPI | [1][2] |
| TFT_SCLK | 14 | HSPI | [1][2] |
| TFT_CS | 15 | HSPI | [1][2] |
| TFT_RST | -1 (board reset) | — | [1][2] |
| TFT_BL | 21, active HIGH | — | [1][2] |
| XPT2046 CLK | 25 | VSPI | [1][2] |
| XPT2046 MOSI | 32 | VSPI | [1][2] |
| XPT2046 MISO | 39 | VSPI | [1][2] |
| XPT2046 CS | 33 | VSPI | [1][2] |
| XPT2046 IRQ | 36 | VSPI | [1][2] |
| microSD SCK / MISO / MOSI / CS | 18 / 19 / 23 / 5 | VSPI (default pins) | [1][2] |
| RGB LED R / G / B | 4 / 16 / 17, **active LOW** | — | [1][2] |
| LDR | 34 (ADC) | — | [1][2] |
| Speaker / amp | 26 (DAC) | — | [1][2] |

### Two traps this board sets

1. **The display and the touch controller are on different SPI buses.** The
   ILI9341 is on HSPI (`USE_HSPI_PORT`), the XPT2046 is on VSPI remapped off
   its default pins. TFT_eSPI's built-in touch support (`TOUCH_CS`) cannot
   drive it, so `TOUCH_CS` is deliberately **not** defined in
   `platformio.ini`; the resulting `#warning` from `TFT_eSPI.h` is expected.
2. **The microSD slot occupies VSPI's default pins** (5/18/19/23), which
   collide with touch-on-remapped-VSPI. This firmware never touches the SD
   card, which sidesteps it. If a successor wants SD, read
   [RNT's note][2] first — they use a bit-banged XPT2046 driver to separate
   the two.

A third, subtler one: the pinned release of `XPT2046_Touchscreen` calls a bare
`SPI.begin()` internally, which would bring VSPI up on the *microSD* pins. The
firmware initialises the global `SPI` object with the touch pins first;
Arduino-ESP32 2.0.17's `SPIClass::begin()` returns immediately when the bus is
already started, so the library's call is a no-op and the correct mapping
stands. See the comment at the top of `src/touch.cpp`.

## Layout of the project

```
firmware/inside-observer/
  platformio.ini            every dependency pinned to an exact version
  partitions/               a byte-for-byte copy of the stock partition table
  src/
    model/                  PURE logic - no Arduino, no WiFi, no TFT_eSPI
      detection_feed.*      threshold filtering, ordering, collapsing
      station_health.*      offline / degraded / listening
      settings.*            configuration and its clamping
      time_utils.*          ISO 8601, peak frequency
      relative_time.*       "4s ago" / "1m ago" / "99d+ ago", and its boundaries
      station_clock.*       the monotonic base those are counted from
      push_frame.*          the /api/v1/display wire format, parsed
    board_pins.h            the table above, in code, with its sources
    station_source.*        the transport seam, and the HTTP fallback
    push_station_source.*   the WebSocket push transport (ADR-038, the default)
    display.*               all rendering
    touch.*                 XPT2046 on the second SPI bus
    portal.*                captive-portal provisioning
    config_store.*          NVS persistence
    main.cpp                boot, self-tests, screen state machine
  test/test_feed/           host unit tests: filtering, ordering, health
  test/test_push/           host unit tests: relative times, clock, wire format
```

Everything under `src/model/` is deliberately free of hardware dependencies so
the rules that matter — what appears, in what order, at what time — are tested
on a laptop rather than by flashing the board and squinting at it.

## Build

PlatformIO Core 6.1.18 or later. No Arduino IDE.

```bash
cd firmware/inside-observer
pio run -e cyd            # build for the board
pio test -e native        # host unit tests, no hardware needed
```

Current figures on this build:

```
RAM:   [=         ]  14.9% (used 48820 bytes from 327680 bytes)
Flash: [===       ]  30.6% (used 962437 bytes from 3145728 bytes)
```

Measured free heap on the device, steady state: ~220 kB.

## Flash

The operator is not in the `dialout` group on the build host, so either add
them (`sudo usermod -aG dialout $USER`, then log out and in) or loosen the
device node for the session:

```bash
sudo chmod 666 /dev/ttyUSB0
pio run -e cyd -t upload
```

A normal upload writes only the bootloader (0x1000), the partition table
(0x8000), `boot_app0` (0xe000) and the application (0x10000). **NVS at 0x9000
is not touched**, which is deliberate — see the next section.

### Reading the serial monitor

`pio device monitor` needs a real TTY. From a non-interactive shell, use
pyserial directly:

```python
import serial, time, sys
s = serial.Serial("/dev/ttyUSB0", 115200, timeout=0.2)
s.setDTR(False); s.setRTS(True); time.sleep(0.15); s.setRTS(False)   # reset
while True:
    sys.stdout.write(s.read(4096).decode("utf-8", "replace"))
```

## First boot and WiFi provisioning

**No credential is ever hardcoded, committed, or asked for.** There are two
ways the display gets onto WiFi, in this order:

1. **Inherited from the stock firmware.** The original DIYmalls weather
   firmware provisioned WiFi through its own "Aura" captive portal, and the
   ESP32 WiFi stack stored the SSID and passphrase in its own NVS namespace
   (`nvs.net80211`) at 0x9000. Because this firmware keeps the stock partition
   table and a normal upload does not erase NVS, `WiFi.begin()` with no
   arguments reconnects using those credentials. This firmware never reads,
   copies, logs or serialises them.

2. **The provisioning portal.** If there are no usable credentials, or the
   operator taps *WiFi + MQTT → set up* in settings, the display raises an
   **open access point called `Aura`** — the same name the stock firmware used,
   so it is recognisable — and answers every DNS query with its own address, so
   a phone opens the setup page by itself. Otherwise browse to
   `http://192.168.4.1/`.

   The portal form carries: WiFi SSID and password; station host/IP, port and
   poll interval; the numeric naming threshold with its caveat text; bat
   passes, clock format, brightness, fallback UTC offset; touch orientation
   flags; and the MQTT broker settings. Saving reboots the display.

   The AP is open, as the stock firmware's was. The WiFi password crosses it in
   the clear on a link that exists for about a minute. If that matters in your
   setting, provision somewhere without neighbours.

### On-device settings

Tap the three dots in the bottom-right corner of the feed. If the touch axes
are mapped wrongly for your unit the affordance will be unreachable — **tap
anywhere three times within three seconds** and the settings page opens
regardless, which is how you get to the touch-orientation controls in the
portal.

The settings page offers: station host and port (numeric keypad), naming
sensitivity (named steps), bat passes on/off, a read-only line reporting which
transport is actually feeding the glass, brightness, and a route into the
provisioning portal. Text fields that need letters — a hostname rather than an
IP, MQTT credentials — are portal-only; typing a hostname on a resistive 240x320
panel is not a kindness.

Saving **restarts the device**. The naming sensitivity and the bat switch are
applied by the station, in the URL the socket was opened with, so changing either
has to reopen the socket; restarting re-runs the whole connect handshake, which
means the screen is repopulated from the station's answer rather than from rows
that were filtered under the old rule.

The 12/24-hour clock choice is gone: ADR-038 replaced clock times with elapsed
ones, so there is nothing left for it to decide. The `use24hClock` field survives
in NVS so no stored configuration needs migrating.

## Where the data comes from

**Directly from the Pi, over a WebSocket the station pushes down** (ADR-038):

```
ws://<station>:8080/api/v1/display?min_score=0.7500&bats=true&rows=6
```

The filter is in the URL, so the station applies it and the device never receives
a detection it would throw away. Measured on the wire against the live station:

| Frame | Bytes |
|---|---|
| connect snapshot, six real species names | 294 |
| a named detection | 49 |
| a bat pass | 40 |
| the 10 s heartbeat | 43 |

**11.4 B/s in a 90-second sample.** The polled transport this replaced cost the
station ~315 ms of query time and ~127 kB every 20 s (~6,350 B/s) to render the
same six rows, most of it evidence checksums the display never used.

There is **no score field on this wire at all**. The threshold decides what is
sent; the number stays on the Pi. A bat pass carries only a marker and a peak
frequency — the words "Bat pass" are supplied by this firmware, so no station
change can put a species on a pass.

### Times

Elapsed, not clock: `now`, `4s ago`, `1m ago`, `1h ago`, `3d ago`, saturating at
`99d+ ago`. They tick once a second without waiting for new data, and only the
rows whose words actually changed are repainted — a 72x18 sprite in a reserved
column, never a screen redraw.

The board has no RTC and does not run NTP, so the count comes from `millis()`,
which only moves forward, and the station's `now` is used *only* to anchor that to
a real epoch. Anchoring directly to a wall clock would let an NTP step or a late
frame move every row on the screen at once. The anchor is re-taken only when it is
out by two seconds or more.

### The fallback

`HttpStationSource` is still here, still wired up, and still runs: if the socket
has been down for 60 s the display starts polling `/health`, `/detections` and
`/history` again exactly as it did before, and stands the poller back down the
moment the socket returns. The settings page reports which one is feeding the
glass. A fallback nobody ever runs is not a fallback.

Sixty seconds, not immediately, because a Wi-Fi hiccup or a station restart would
otherwise put 127 kB per 20 s straight back on the wire. A station restart is
absorbed by the socket's own reconnect in a few seconds and never reaches the
poller.

### Not MQTT

The station's MQTT publisher exists (ADR-025), and this display deliberately does
not use it: the broker runs on the Home Assistant box, and the station and the
display are the same system in the same house. The `MqttSettings` struct stays in
NVS, unused.

## If the display looks wrong

`platformio.ini` configures TFT_eSPI for the single-USB-port, ILI9341 revision
of this board:

```
-D ILI9341_2_DRIVER  -D TFT_RGB_ORDER=TFT_BGR  -D TFT_INVERSION_OFF
```

| Symptom | Fix |
|---|---|
| Colours photo-negative | flip the inversion flag in `platformio.ini` (`TFT_INVERSION_OFF` ⇄ `TFT_INVERSION_ON`) |
| Red and blue swapped | remove `-D TFT_RGB_ORDER=TFT_BGR` |
| Garbled / no image, and `RDID4` is not `..93 41 ..` | you have the ST7789 revision (two USB ports): use `-D ST7789_DRIVER -D TFT_INVERSION_OFF -D TFT_RGB_ORDER=TFT_BGR` [3] |
| Touch lands in the wrong place | portal → *Touch orientation* → swap/flip. Raw coordinates are logged on every press: `[touch] raw=(x,y) z=.. -> screen=(x,y)` |

### Verified on the operator's own unit, 2026-08-08

Both of the things the boot self-test *cannot* prove have now been confirmed by
a human looking at and touching the screen:

* **Colour.** The unit is **not** inverted. The build initially trusted TFT_eSPI
  discussion #3018, which reports inversion for some ESP32-2432S028R revisions,
  and rendered dark navy text on pale blue — precisely the intended near-black
  background and warm off-white text inverted. `TFT_INVERSION_OFF` is correct
  here. BGR order was already right: had it also been wrong, the inverted text
  would have read dark brown/red rather than navy.
* **Touch.** The raw-to-screen mapping is correct as shipped, using the
  published calibration for this board. No swap or flip is needed on this unit.

The GRAM readback self-test proves the SPI link works in both directions and
that the controller stores what it is sent; it says nothing about how the
controller drives the glass, which is why the colour question needed eyes.
Panel *identity*, by contrast, is measured, not assumed: `RDID4` (0xD3) returns
`0x..9341`.

The boot self-test writes five known colours into the panel's GRAM and reads
them back:

```
[selftest] gram red   wrote=0xF800 read=0xF800 MATCH
...
[selftest] panel readback PASSED
```

This proves the SPI link works both ways and the controller stores what it is
sent. It **cannot** prove the colours look right to a human — inversion and
channel order change how the controller drives the glass, not what is in GRAM.
That check needs eyes.

## Restoring the original weather firmware

A complete 4 MB flash image of the stock DIYmalls firmware was taken before any
of this was written:

```
/home/observer/.claude/jobs/JOBID/tmp/firmware-backup.bin   (4,194,304 bytes)
```

Keep a copy somewhere durable — that path is a scratch directory.

```bash
esptool --port /dev/ttyUSB0 --baud 460800 \
    write_flash 0x0 firmware-backup.bin
```

That restores everything including NVS, so the stock firmware finds its own
WiFi credentials and settings exactly as it left them. To take a fresh backup
of whatever is on the board right now, before overwriting it:

```bash
esptool --port /dev/ttyUSB0 --baud 460800 \
    read_flash 0x0 0x400000 firmware-backup-$(date +%F).bin
```

## Target-device smoke test

Three commands, in this order, all of which must be run against the real board
and the real station — nothing here can be believed from a host build.

```bash
# 1. The pure logic, on a laptop. 53 cases: filtering, ordering, health parsing,
#    relative-time boundaries, the millis() wrap, and the wire format.
pio test -e native

# 2. What the push channel actually costs on the wire, against the live station.
#    Expect ~11 B/s: a 49-byte detection, a 43-byte heartbeat every 10 s.
python scripts/probe_display_channel.py station.example 90

# 3. Flash, then read the board's own account of itself for two minutes.
sudo pio run -e cyd -t upload
sudo python scripts/serial_capture.py /dev/ttyUSB0 130
```

From step 3, the lines that constitute a pass:

```
[selftest] panel readback PASSED
[push] connected to /api/v1/display?min_score=0.7500&bats=true&rows=6
[push] hello: 6 rows, 30 species today, hb 10s
[push] +Common Woodpigeon (49 B)
[push] frames=35 bytes=1817 mean=51 B/frame dropped=0 reconnects=0 ticks=183 …
```

`ticks` is the one to read carefully: it counts *partial* repaints of the elapsed
time and should rise by roughly 60 a minute while the newest row is under a minute
old. Zero means the clock is not ticking, or — the trap that actually happened —
that something else is calling `showFeed` often enough to be updating the ages as a
side effect. `dropped` must stay 0, and free heap must be flat.

Then confirm the station is unharmed:

```bash
curl -s http://station.example:8080/api/v1/station | jq '.capture, .display_channel'
```

Judge continuity by `frames` against `expected_frames`, not by
`estimated_missing_seconds`, which over-reports by roughly 12.9x.
`display_channel.per_client[].mean_frame_bytes` is the station's own view of the
same number the device reports, and the two should agree.

## Rollback note

This firmware lives entirely on the display. Nothing about it touches the
station, which it only ever reads from. Rolling back is either a re-flash of a
previous `firmware.bin` or the whole-image restore above; in both cases the
station is unaffected and no observation is at risk.

The station half of ADR-038 rolls back independently and just as cheaply:
`/api/v1/display` is an additive endpoint that costs nothing with no client
connected, so reverting the firmware alone is sufficient — a display running the
polled build works against a station that has the push channel, and a display
running the push build falls back to polling against a station that does not
(after 60 s, with the banner honest in the meantime).
