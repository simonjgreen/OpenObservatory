# Inside observer

A wall or desk display that shows, calmly, what the garden acoustic station is
hearing. Species name and time, newest first. A footer line counting today's
species. Nothing else.

It is an ambient object, not a dashboard. It is meant to be legible from the
other side of a room and unremarkable when nobody is looking at it.

## What it will and will not show

| | |
|---|---|
| Species name and local time | yes |
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
      time_utils.*          ISO 8601, local clock, peak frequency
    board_pins.h            the table above, in code, with its sources
    station_source.*        the transport seam; HTTP polling today
    display.*               all rendering
    touch.*                 XPT2046 on the second SPI bus
    portal.*                captive-portal provisioning
    config_store.*          NVS persistence
    main.cpp                boot, self-tests, screen state machine
  test/test_feed/           host unit tests for everything in src/model/
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
sensitivity (named steps), bat passes on/off, 12/24-hour clock, brightness, and
a route into the provisioning portal. Text fields that need letters — a
hostname rather than an IP, MQTT credentials — are portal-only; typing a
hostname on a resistive 240x320 panel is not a kindness.

## Where the data comes from

`GET http://<station>:8080/api/v1/...`, polled every 20 s by default:

| Request | Used for |
|---|---|
| `/health` | listening / degraded, and the reason |
| `/detections?limit=40&identified_only=true&min_score=<t>` | the named rows |
| `/detections?limit=8&group=bat` | bat passes (never score-filtered) |
| `/history?window=today` | species count, and the station's real UTC offset |

The station's local midnight, which `/history` reports as
`range.start_utc`, is how the display learns the station's UTC offset including
DST. There is no NTP client and no IANA database on the device; UTC is used
internally and converted only for presentation, as `CLAUDE.md` requires.

Responses are stream-parsed through ArduinoJson filters, so the ~70 kB
detections body never lands in the heap — only the six fields per row that get
rendered.

MQTT is **not** wired up. `StationSource` is the seam an `MqttStationSource`
drops into later; the broker settings are already stored so they survive that
firmware update.

## If the display looks wrong

`platformio.ini` configures TFT_eSPI for the single-USB-port, ILI9341 revision
of this board:

```
-D ILI9341_2_DRIVER  -D TFT_RGB_ORDER=TFT_BGR  -D TFT_INVERSION_ON
```

| Symptom | Fix |
|---|---|
| Colours photo-negative | remove `-D TFT_INVERSION_ON` |
| Red and blue swapped | remove `-D TFT_RGB_ORDER=TFT_BGR` |
| Garbled / no image, and `RDID4` is not `..93 41 ..` | you have the ST7789 revision (two USB ports): use `-D ST7789_DRIVER -D TFT_INVERSION_OFF -D TFT_RGB_ORDER=TFT_BGR` [3] |
| Touch lands in the wrong place | portal → *Touch orientation* → swap/flip. Raw coordinates are logged on every press: `[touch] raw=(x,y) z=.. -> screen=(x,y)` |

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

## Rollback note

This firmware lives entirely on the display. Nothing about it touches the
station, which it only ever reads from. Rolling back is either a re-flash of a
previous `firmware.bin` or the whole-image restore above; in both cases the
station is unaffected and no observation is at risk.
