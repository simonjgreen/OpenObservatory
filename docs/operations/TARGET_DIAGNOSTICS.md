# Target device diagnostics

Measured facts about the actual station, not assumptions. This file is the exit
gate for Milestone 0: "AudioMoth formats and stable device identity are recorded
from the actual Pi."

Recorded 2026-08-04 from `pi2` at `station.example`.
Regenerate with `oo audio probe --json --write docs/operations/probe.json`.

## Host

| | |
|---|---|
| Model | Raspberry Pi 5 Model B Rev 1.1 |
| OS | Ubuntu 24.04.3 LTS (Noble) — **not** Raspberry Pi OS |
| Kernel | `6.8.0-1060-raspi`, aarch64 |
| Python | 3.12.3 (system interpreter) |
| Memory | 7.8 GiB |
| Cores | 4 |
| Storage | 235 GB SD card, no USB SSD attached |
| CPU temperature at idle | 39 °C, `throttled=0x0` |

## AudioMoth

The device presents **two different USB identities** depending on the position of
its three-position side switch. Both matter, and confusing them wastes a lot of
time:

| Switch | USB ID | Interfaces | What it is |
|---|---|---|---|
| `USB/OFF` | `10c4:0002` | HID + vendor-specific | Configuration mode. No audio. This is where sample rate, gain and filters are set, and where firmware is flashed. |
| `DEFAULT` | `16d0:06f3` | USB Audio Class | **Streaming mode.** This is what the station captures from. |
| `CUSTOM` | `16d0:06f3` | USB Audio Class | Also streams, additionally applying the configured filter/advanced settings. |

The unit already shipped with the required firmware; no flashing was needed:

```
firmware description : AudioMoth-USB-Microphone
firmware version     : 1.3.2
device uid           : 2453800264933F8F
```

Read this at any time with `oo audiomoth info` (switch must be in `USB/OFF`).

### Negotiated capture profile

```
stable_device_key : usb-16d0:06f3:0384_2453800264933F8F
card_name         : 384kHz AudioMoth USB Microphone
alsa_address      : hw:CARD=Microphone,DEV=0
by_id_symlink     : /dev/snd/by-id/usb-openacousticdevices.info_384kHz_
                    AudioMoth_USB_Microphone_0384_2453800264933F8F-00
profile 0         : S16_LE, 1 channel, 384000 Hz, 16 bits, MONO,
                    endpoint 0x82 (2 IN) (SYNC)
```

**The device offers exactly one hardware profile: 384 kHz, mono, S16_LE.** That is
the technical spec's first-choice native profile, so no degraded mode is in use.

| Requested rate | Native support |
|---|---|
| 384000 | **supported** |
| 250000 | unsupported |
| 192000 | unsupported |
| 96000 | unsupported |
| 48000 | unsupported |

Note that `arecord -r 48000` *appears* to succeed on this device. It does not
capture at 48 kHz — ALSA's `plug` layer silently resamples, printing only a
warning. The station always opens `hw:` directly and refuses any rate the device
substitutes, because a stream whose true bandwidth we cannot state is worse than
no stream. Lower rates would have to be configured on the device itself through
the AudioMoth USB Microphone app, which also changes what the firmware reports.

### Card numbering is not stable — demonstrated

Across a single reboot during commissioning, the AudioMoth moved from **card 2**
to **card 0**, with the HDMI devices shuffling around it. Nothing in this project
addresses a device by card index; resolution is by `stable_device_key`
(USB vendor:product:serial) or ALSA card *id*, exactly as the technical spec
requires.

## Measured capture performance

`oo audio test-capture --seconds 8`, on the live device:

| Measurement | Result |
|---|---|
| Frames delivered | 3,072,000 |
| Frames implied by elapsed monotonic time | 3,072,005 |
| Discrepancy | 5 frames over 8 s (13 µs) |
| Discontinuities | 0 |
| ALSA overruns | 0 |

Sustained running as a systemd service:

| Measurement | Result |
|---|---|
| Capture continuity | 0.9990–0.9997 (frames captured ÷ frames elapsed time implies, from frame zero) |
| Gaps / overruns | 0 |
| Device clock offset from nominal | **−43 ppm** |
| Per-block hot-path CPU | **10.9 %** of one core, for capture + resample + both spectrograms + level telemetry. Was 9.5 % before the ultrasonic channel began max-combining four sub-windows per column; that 1.4 % buys full coverage of the audio instead of 45 %. |
| Whole-process CPU | ~29 % of the 4-core machine with all three detectors running |
| Native ring memory | 120 s at 384 kHz float32 ≈ 184 MB resident |

### The device runs on its own crystal

The measured −43 ppm offset is a real property of the hardware, not an error. It
means the AudioMoth presents frames slightly slower than 384000 Hz nominal, so
"frames captured" and "wall-clock elapsed" diverge steadily by about 0.15 s per
hour. This is why frame indices — not timestamps — are authoritative for
addressing audio, and why gap detection looks for a *step* in the
frames-behind-wall-clock figure rather than an absolute value. An earlier version
did not separate the two and reported a single overrun as a permanent −1439 ppm
clock offset.

## Input level and gain

The gain configured on the device is **hot for this environment**:

| Condition | RMS | Peak | Clipped samples |
|---|---|---|---|
| Initial 10 s test | −21.8 dBFS | 0.0 dBFS | 326 in 3,840,000 |
| Later 8 s test | −19.1 dBFS | 0.0 dBFS | 139 in 3,072,000 |
| Quiet evening, steady state | −45 dBFS | −31 dBFS | 0 |

Loud nearby events clip. Clipping is visible live in the debug UI's level meters
and is counted in `oo_audio_clipping_ratio`.

**Reducing gain requires the device, not this software.** Move the switch to
`USB/OFF` (which stops capture) and use the AudioMoth USB Microphone app. The HID
protocol for changing gain is not implemented here; `oo audiomoth info` reads
identity only. See `AUDIOMOTH_FIRMWARE.md`.

## Software environment

| Component | Version | Note |
|---|---|---|
| `soxr` | 0.5.0.post1 | aarch64/cp312 wheel available. **Measured group delay: 0 frames.** |
| `scipy` | 1.15.0 | Fallback resampler path only |
| `pyalsaaudio` | 0.11.0 | Builds from source; needs `libasound2-dev` |
| `ai-edge-litert` | 2.1.6 | TFLite runtime for BirdNET. `tflite-runtime` has **no** cp312 aarch64 wheel and cannot be used. |
| `numpy` | 2.2.1 | The older `tflite-runtime` requires NumPy 1.x, which is the other reason it is unusable here |

### Resampler timing, measured over 5 minutes of audio

| Property | Result |
|---|---|
| Backend | libsoxr, `HQ` |
| Ratio | 1/8 exactly (384000 → 48000) |
| Group delay | **0 output frames** — output frame *n* maps exactly to native frame *8n* |
| Delivery deficit band | 112–924 frames (2.3–19.3 ms), **bounded** |
| Deficit trend over 5 min | +2.1 frames — no cumulative drift |
| Seam continuity | no discontinuity at block boundaries |
| 1000 Hz tone in | 999.8 Hz out |

libsoxr emits ragged chunk sizes (4884 or 4070 frames per 38400-frame input block
rather than a constant 4800), so the *count* of frames produced oscillates behind
the exact ratio. That is delivery latency, not drift. Timestamps are therefore
derived from frame indices via `StreamClock`, never from a running output count —
otherwise the audible stream's time base would wander by up to 19 ms.

## Detector performance on this hardware

Measured while capturing live at 384 kHz, all three detectors running:

| Detector | Window | p95 runtime | Realtime factor | Notes |
|---|---|---|---|---|
| `activity-v1` | 1.0 s / 0.5 s stride, 48 kHz | 16 ms | ~95× | Fires on roughly 9 % of windows in a quiet garden |
| `birdnet-v2.4` | 3.0 s / 1.5 s stride, 48 kHz | 77–109 ms | ~40× | 6522 labels, XNNPACK delegate, 2 threads |
| `ultrasonic-pass-v1` | 2.0 s / 2.0 s stride, **384 kHz** | 54–104 ms | ~36–40× | Needs ≥96 kHz; reports unavailable below that |

All three keep up comfortably. No windows were dropped for a full queue or a
missed delivery deadline in any observed run.

BirdNET produced real identifications on this station within minutes of starting,
including *Columba palumbus* (Common Woodpigeon).

Re-measured on the Pi on 2026-08-05 after the night scheduler, buzz flagging and
sub-bin frequency interpolation were added: `ultrasonic-pass-v1` p95 runtime **75.7 ms**
per 2-second native window, still comfortably inside the 2 s budget. 197 Python tests
pass on the Pi, of which 3 BatDetect2 tests skip pending its assets being fetched, plus
49 frontend tests; BatDetect2 evaluation is in progress and nothing about it is proven
yet.

## Live channel delivery, measured from a real browser over Wi-Fi

| Property | Value |
|---|---|
| Columns delivered | 1246 per channel per 30 s wall = 29.9 s of audio — exactly real time |
| Gaps / out-of-order / overlaps / malformed | 0 / 0 / 0 / 0 |
| Inter-arrival | p50 100 ms (one capture block), p95 154 ms, worst 818 ms |
| Client queue depth | 0, with 0 dropped |
| Backfill on connect | ~1190 columns per channel (30 s), down from 2400 |
| Live audio latency | 131–173 ms jitter buffer + 42 ms browser device ≈ 180 ms |

Measuring this **in the browser** rather than from the Pi was essential. A probe on
loopback reported a flawless 100 ms cadence with zero gaps while the real client was
receiving one frame in thirty seconds, because the underlying bug — concurrent writes
to one WebSocket — only manifests when a send actually takes time.

The worst-case 818 ms inter-arrival is Wi-Fi jitter, not a pipeline stall. The client
interpolates scroll position between bursts and clamps that interpolation to about
one burst, so a stall parks the display rather than letting it drift out of step.

## Known limitations recorded honestly

- **Range model: off by default in the repository, on at this station.** The shipped
  defaults leave latitude and longitude unset and `birdnet_use_location_filter`
  `False`, so every species is judged on confidence alone. the development station's
  coordinates were written to the Pi's `config/runtime.env` — which is deliberately
  not in version control — and the model is enabled there: week 29, 139 species
  plausible, 7 suppressed as implausible. It matters more than it sounds: before it
  was enabled, "Great Bittern" and "Spotted Crake" were stored at 0.9 confidence in
  a the development area garden. To enable it on another station, set `OO_LATITUDE`,
  `OO_LONGITUDE` and `OO_BIRDNET_USE_LOCATION_FILTER=true`.
- **Ultrasonic detections are not species identifications** and, on a broadband-noisy
  evening, some "bat passes" are likely false positives from wind or handling noise.
  Peak frequency yields a coarse group hint only. Event titles now show a candidate
  group name and peak frequency, for example "36 kHz - Myotis / barbastelle?", but this
  is presentational — the stored record still carries no species label, and the
  question mark is mandatory. Seeing a candidate name in the UI does not mean this
  constraint has been relaxed.
- **72-hour soak test not run.** The acceptance criteria require it before the
  system may be described as complete.
- **The ultrasonic detector now has a night scheduler** (`src/open_observatory/schedule.py`),
  gating it to civil dusk through civil dawn plus configurable margins, computed from the
  station's coordinates. It is off by default (`ultrasonic_schedule = "always"`); Charter
  Alley's `runtime.env` sets `OO_ULTRASONIC_SCHEDULE=night`. If coordinates are unset the
  detector runs continuously rather than gating to nothing, by design — see
  `DETECTOR_STRATEGY.md`.
- **No authentication.** The API binds LAN-only with anonymous read enabled.
