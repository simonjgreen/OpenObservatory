# Bill of materials

Everything needed to build a station like the reference one. The reference
station is the machine every measured figure in this repository came from; its
exact parts are listed in the README's [Hardware](README.md#hardware) section.
This document is the shopping list version of that table: what is genuinely
required, what is optional, what may be substituted, and the things that will
bite you if you buy the obvious alternative.

**On prices.** The figures below are ballpark retail prices in GBP, ex. VAT
treatment and ex. shipping, given only to convey the scale of the build. They
were not quoted from any retailer and will be wrong in detail. Check before you
buy.

**On requirement.** Nothing here is required *by the software*. The station
discovers what it is attached to and records what it actually negotiated
(`oo audio probe`). A USB audio device that is not an AudioMoth will capture at
whatever rate it negotiates; the ultrasonic detector simply has nothing to work
with below ~96 kHz.

---

## Core station — required

| # | Part | Qty | ~Price | Why this one |
|---|---|---|---|---|
| 1 | **Raspberry Pi 5 Model B, 8 GB** | 1 | £75 | Three detectors, a 384 kHz capture thread and the API share one process. 4 GB will probably run it; 8 GB is what was measured. A Pi 4 has not been tried and is not recommended — the capture path is CPU-bound at 384 kHz. |
| 2 | **AudioMoth USB Microphone** ([Open Acoustic Devices](https://www.openacousticdevices.info/product-page/audiomoth-usb-microphone)) | 1 | £70 | The whole point. A full-spectrum USB mic that negotiates **384 kHz mono `S16_LE`**, putting bats inside the band. USB ID `16d0:06f3`. Buy the *USB Microphone* variant, **not** an AudioMoth recorder — see [Substitutions](#substitutions-and-what-they-cost-you). |
| 3 | **AudioMoth USB Microphone case** ([official](https://www.openacousticdevices.info/product-page/audiomoth-usb-microphone-case)) | 1 | £10 | The board ships bare. It is going to hang outdoors under an eave. |
| 4 | **microSD card, 128 GB or larger, A2/V30** | 1 | £15 | OS, application, and the SQLite database. The reference station uses a SanDisk 256 GB. See the endurance warning below. |
| 5 | **USB SSD, 500 GB** | 1 | £45 | Evidence clips live here, mounted over `data/clips`, deliberately *not* the database (ADR-021). Reference part: SanDisk Extreme Portable (`0781:558c`), USB/UAS. A busy bat night writes gigabytes; size it for your retention policy, not your first week. |
| 6 | **USB-C power supply, 27 W (5 V 5 A)** | 1 | £12 | The official Raspberry Pi 27 W supply. **Buy this rather than copying the reference station**, which runs on a 5 V 3 A charger and therefore has `usb_max_current_enable=0` — a 600 mA *total* USB budget shared between the SSD and the microphone. It has never faulted here, but an underpowered supply is a genuinely plausible cause of an intermittently-enumerating microphone. 27 W removes the constraint. |
| 7 | **Micro-USB cable, 2 m** | 1 | £8 | Microphone to Pi. Long enough to reach from indoors to the eaves. Must be a **data** cable, not a charge-only one. Longer runs are untested; USB 2.0 tolerates 5 m in principle. |
| 8 | **Passive case with a metal body** | 1 | £15 | Reference part: [Flirc Raspberry Pi 5 case](https://thepihut.com/products/flirc-raspberry-pi-5-case). The aluminium body *is* the heatsink and there is **no fan** — which matters, because a fan a metre from an ultrasonic microphone is not a neutral choice. Measured: 39 °C idle, **~58 °C** sustained with capture and three detectors, `throttled=0x0` after nine days up. |

**Networking** is WiFi on the reference station; the Pi's Ethernet port is
unused. Either works. The station never needs the internet for capture,
detection, review or query — only for optional model downloads and NTP.

Core subtotal: **roughly £250.**

---

## Indoor display — optional

The "inside observer": an ambient counter-top screen showing what the garden is
hearing. The station is complete without it.

| # | Part | Qty | ~Price | Notes |
|---|---|---|---|---|
| 9 | **ESP32-2432S028R** ("Cheap Yellow Display", Sunton/DIYmalls) | 1 | £15 | 2.8" 240×320 **ILI9341** over SPI, XPT2046 resistive touch, CH340 USB serial, 4 MB flash, **no PSRAM**. The firmware probes the panel at boot; this board also ships in an ST7789 revision, and `firmware/inside-observer/README.md` covers that case. |
| 10 | **Case for it** | 1 | £2 filament | The reference build uses a [printed case](https://makerworld.com/models/1382304) inherited from the *Aura* project this board had previously been assembled for. |
| 11 | **USB cable + 5 V supply for the display** | 1 | £8 | Micro-USB. Flashing is over the same cable. |

Display subtotal: **roughly £25.**

---

## Tools and consumables

Not parts, but you will need them.

- **A computer to deploy from.** `deploy/deploy.sh` builds the web UI locally
  with `npm` and `rsync`s to the Pi — the Pi has no Node toolchain and is not
  expected to get one. Any Linux/macOS machine with Node and Python 3.12.
- **A microSD writer**, for the initial Ubuntu 24.04 LTS (`aarch64`) image.
- **A hook and 2 m of cable route** to get the microphone under an eave. The
  reference run goes out through a window jamb.
- **Nothing soldered.** There is no soldering, no GPIO wiring, no breadboard in
  this build. Everything is USB.

---

## Wanted later, not yet designed

A **lux sensor** and a **rain sensor** — to say whether a detection happened in
real darkness rather than calculated darkness, and to explain the hours where
rain lifts the noise floor and quietens the birds. How to attach them is
undecided; see Milestone 9 in
[`docs/delivery/IMPLEMENTATION_PLAN.md`](docs/delivery/IMPLEMENTATION_PLAN.md).
Do not buy these yet.

---

## Substitutions, and what they cost you

| Instead of | You get | Verdict |
|---|---|---|
| An **AudioMoth recorder** running USB-Microphone firmware | A different USB device that must be reflashed; identity and switch behaviour differ. See [`docs/operations/AUDIOMOTH_FIRMWARE.md`](docs/operations/AUDIOMOTH_FIRMWARE.md). | Workable, but you are doing firmware work before you have a station. |
| Any **48 kHz USB microphone** | Birds yes, bats **no**. The ultrasonic detector has nothing above Nyquist to detect. | Fine if you only want birds. Halves the point of the project. |
| **Ultramic / Dodotronic** or similar | Untested here. The software addresses devices by name, not index, and records what it negotiated — so it may well work. | Unverified. No fixture test has passed on it, and this project does not call a detector "supported" without one. |
| **microSD only**, no SSD | Clips and database on one card. | Works, wears the card out. The reference station moved clips to an SSD for exactly this reason. |
| **A case with a fan** | Lower temperatures, and a fan near an ultrasonic microphone. | Don't, unless the fan is far from the mic. |
| **Pi 4** | Untested. | Not recommended at 384 kHz. |

---

## Things worth knowing before you copy this

These are the ones that actually cost time here.

- **The AudioMoth's three-position switch matters.** `DEFAULT` streams audio;
  `USB/OFF` is configuration-only and produces **no ALSA card at all**. Leaving
  it in `USB/OFF` caused a 29-hour outage during commissioning.
- **ALSA card numbers are not stable.** The AudioMoth moved from card 2 to card
  0 across a reboot. Nothing in this codebase addresses a device by index, and
  nothing you write should either.
- **A microSD is not a good home for a continuously-writing database.** This one
  is, for now. Storage endurance is a standing constraint in the charter.
- **Microphone position affects the data more than any setting in this
  repository.** Moving it a few feet changed the noise floor materially. Budget
  more thought for where it hangs than for what you spend.
- **Run `oo audio probe` before believing anything.** The station records what
  it actually negotiated, not what the box claimed.

For the full commissioning path, read
[`docs/development/SETUP.md`](docs/development/SETUP.md) and
[`docs/operations/DEPLOYMENT_AND_OPERATIONS.md`](docs/operations/DEPLOYMENT_AND_OPERATIONS.md).
