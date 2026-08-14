# Target device diagnostics

Measured facts about the actual station, not assumptions. This file is the exit
gate for Milestone 0: "AudioMoth formats and stable device identity are recorded
from the actual Pi."

Recorded 2026-08-04 from the target Pi (the development station), extended through 2026-08-08 and
re-checked against the live station on 2026-08-09.
Regenerate the machine-readable portion with
`oo audio probe --json --write docs/operations/probe.json`.

**This file is the authoritative home for measured figures.** Every number here
carries the date it was measured. Do not restate a figure from memory in another
document; link here. Do not add a figure that cannot be traced to a measurement —
mark it unverified instead.

## Host

| | |
|---|---|
| Model | Raspberry Pi 5 Model B Rev 1.1 |
| OS | Ubuntu 24.04.3 LTS (Noble) — **not** Raspberry Pi OS |
| Kernel | `6.8.0-1060-raspi`, aarch64 |
| Python | 3.12.3 (system interpreter) |
| Memory | 7.8 GiB |
| Cores | 4 |
| Storage | 235 GB SD card (OS, database, application) plus a 465.8 GB USB SSD mounted at `data/clips` for evidence (see below) |
| CPU temperature at idle | 39 °C, `throttled=0x0` |

## Evidence storage: USB SSD, mounted over `data/clips`

Added 2026-08-08 (ADR-021). A SanDisk Extreme Portable SSD, 465.8 GB, connected over
UAS as `/dev/sda`, replaces the SD card as the destination for evidence clips. The SD
card could not sustain a busy bat night's write load (roughly 15 MB per pass across
four clips, 15 GB in one night against a 20 GB budget already exceeded) while also
serving ALSA reads through the same shared thread pool.

| | |
|---|---|
| Device | `/dev/sda`, SanDisk Extreme Portable SSD, 465.8 GB, UAS |
| Partition | single ext4 partition, label `oo-clips` |
| UUID | `005ab10e-7a3b-4b7d-baa0-07b9aeacddc5` |
| Formatting | `-m 1` (1% reserved, not the ext4 default of 5%, since this volume holds no system files) |
| Mount point | `/home/<user>/open-observatory/data/clips` |
| `/etc/fstab` options | `defaults,noatime,nofail,x-systemd.device-timeout=10` |
| Database | stays on the SD card — small, and the SD card is the system disk that is always present |

The disk previously held an unrelated Ubuntu amd64 installer and was wiped before
use. 21 GB of existing clips were migrated across; the old directory, retained at
`data/clips.sdcard-backup`, was **deleted 2026-08-10**, freeing 21 GB on the SD
card. Before removal, ADR-057 confirmed no live `media_asset` row pointed into
it and that none of the 8,067 missing clips were recoverable from it. A clip recorded the day
before the migration was verified afterwards to still download as a valid 384 kHz WAV
through `GET /api/v1/media/{id}` — `media_asset.storage_uri` holds absolute paths, so
mounting the new device at the existing path made the migration a no-op for the
database rather than requiring 17,273 rows to be rewritten.

**Operational consequence: the mount must exist before the service starts.** The
systemd unit runs in a mount namespace (`ProtectHome=read-only`,
`ReadWritePaths=/home/<user>/open-observatory/data`), so a filesystem mounted on the
host *after* the service has started is not visible inside it — the service must be
restarted, not just have the mount appear, for the SSD to take effect.
`OO_CLIPS_REQUIRE_MOUNT=true` (`clips_require_mount` in `Settings`) makes
`/api/v1/health` report the problem by name when `data/clips` is not a mount point,
rather than silently falling back to writing evidence onto the SD card. See ADR-021
for the full reasoning, including why the database was deliberately left off the SSD.

With the SSD in place the throttles imposed to protect the SD card were lifted in
`config/runtime.env`: `OO_CLIP_MAX_PER_MINUTE` restored from 6 to 20,
`OO_ULTRASONIC_AUDIBLE_METHOD` restored from `heterodyne` to `both`, and
`OO_CLIP_MAX_TOTAL_GB` raised from 20 to 300 (against 458 GB usable). `OO_ULTRASONIC_SCHEDULE=night` remains set.

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
**`oo audiomoth info` still succeeds when the switch is in `USB/OFF`** — it talks to
the HID interface, which is present in that position — so a successful
`oo audiomoth info` is not evidence that streaming capture will work. The diagnostic
signal for "switch in the wrong position" is `oo audiomoth info` succeeding while
`oo audio probe`/live capture cannot find an ALSA card at all: `USB/OFF` presents no
ALSA card of any kind, only HID. See the incident below.

### Incident, 2026-08-08: switch left in `USB/OFF`, no automatic recovery

The mode switch was moved to `USB/OFF` (most likely while adjusting gain — see
"Input level and gain" below), which drops the ALSA card entirely and switches the USB
identity from `16d0:06f3` (streaming) to `10c4:0002` (HID only). Live capture failed at
`AlsaCaptureError: ALSA read failed: File descriptor in bad state` — the device
changing USB identity under a file descriptor the capture process still held open.

`OO_SOURCE=auto` correctly fell back to the synthetic source and correctly reported
itself degraded in `/api/v1/health`, but it **never recovered on its own**:
`SyntheticSource` never ends, and the capture supervisor only rebuilds a source once
the current one ends, so a reattached or corrected microphone went unnoticed until
someone restarted the service by hand. Roughly a day of recording was lost. Detectors
kept running against the synthetic scene throughout and persisted detections — 5 bird
detections labelled *Grey-winged Inca-Finch* and 515 acoustic events — into the live
database, indistinguishable from genuine ones until ADR-020's view-level filter.

**Fix:** `hardware_recheck_s` (default 30 s, `src/open_observatory/config.py`) makes
the station periodically re-probe for the real device while running on the *fallback*
synthetic source specifically — never when synthetic was chosen deliberately (e.g.
`OO_SOURCE=synthetic` for a demo) — so a corrected switch position or reattached cable
is picked up without a manual restart. This recovery behaviour is not itself recorded
as an ADR (it is a bugfix, not an architectural deviation); ADR-020 covers the
separate, related decision to exclude synthetic-source detections from browsing
views by default (`source_kind`/`is_live_source` on every detection).

`hardware_recheck_s` only covers this fallback-synthetic path — it did nothing for
the 2026-08-14 wedge, where the real device was still open but stopped answering
reads. See ADR-060.

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
| Capture continuity | 0.9990–0.9997 (frames captured ÷ frames elapsed time implies, from frame zero); **0.999945** over 44 min after the ring was deepened, 2026-08-08 |
| Gaps / overruns | 0 |
| ALSA ring | 192,000 frames = 500 ms (50 periods of 3840), negotiated. Was 30,720 = 80 ms behind a 100 ms block until 2026-08-08; see ADR-030 |
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

A second version of the same mistake survived until 2026-08-08: frames lost during an
ALSA overrun were never estimated at all, because the estimator was skipped whenever a
discontinuity reason had already been set. Those uncredited frames looked exactly like
a slow crystal, and the station reported **−245 to −270 ppm** through the afternoon of
2026-08-08. After the fix, and with no losses to credit, it reads **−52 ppm**. Treat any
`rate_offset_ppm` logged before 2026-08-08 15:00 UTC as contaminated.

### USB topology, measured 2026-08-08

The AudioMoth and the evidence SSD are on **different xHCI host controllers**, so
they do not compete for bus scheduling:

| Device | Bus | Controller | Negotiated speed |
|---|---|---|---|
| 384kHz AudioMoth USB Microphone | 002, port 1 | `1f00200000.usb` / `xhci-hcd.0` | **12 Mbit/s (full speed)** |
| SanDisk Extreme Portable SSD | 004, port 2 | `1f00300000.usb` / `xhci-hcd.1` | **480 Mbit/s (high speed)** |

Two things follow.

**The AudioMoth runs at USB full speed and is near its ceiling.** Its isochronous IN
endpoint declares `wMaxPacketSize 768 bytes` with `bInterval 1` — 768 bytes in every
1 ms frame, which is exactly 384000 × 2 bytes per second. Full speed allows at most
1023 bytes per isochronous endpoint per frame, so at 384 kHz this one device reserves
**75%** of the entire full-speed isochronous budget. That is fine as long as nothing
else shares the bus (nothing does), but it means there is no bus-side headroom to
find: the device is full-speed only and cannot be moved to a faster port. Host-side
slack — the depth of the ALSA ring — is the only lever available.

**The SSD is in a USB 2.0 port.** It negotiated 480 Mbit/s on `usb4`, not 5 Gbit/s on
`usb5`, so it is running at roughly a tenth of its capability. This does **not** affect
capture — different controller, and the measured write rate of ~1.5 MB/s is trivial
either way — but moving it to the blue USB 3.0 port **on the same side of the board**
(the one that enumerates as `usb5`, still `xhci-hcd.1`) would give it its full speed at
no risk. Do not move it to the other blue port: that one is `usb3` on `xhci-hcd.0`,
which is the AudioMoth's controller, and would introduce exactly the contention that
currently does not exist.

Check both with:

```bash
lsusb -t                      # tree, with negotiated speed per device
for d in /sys/bus/usb/devices/*/; do
  [ -f "$d/speed" ] && printf '%s speed=%s product=%s\n' \
    "$(basename "$d")" "$(cat "$d/speed")" "$(cat "$d/product" 2>/dev/null)"
done
readlink -f /sys/bus/usb/devices/usb2   # which controller a bus belongs to
```

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
identity only. See `AUDIOMOTH_FIRMWARE.md`. **This is the same switch position that
caused the 2026-08-08 incident above** — moving it to adjust gain is a plausible
explanation for how the switch was left there. Anyone doing this should expect
capture to stop for the duration and should confirm the switch is back in `DEFAULT`
(streaming) afterwards, not rely on the automatic recovery alone.

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
per 2-second native window, still comfortably inside the 2 s budget.

**Test counts are not a target-device measurement and do not belong in this file.**
The last full run *on the Pi* was 197 Python tests on 2026-08-08. For the current
suite size see `docs/development/SETUP.md`, measured on the development laptop.
BatDetect2's own benchmark on this hardware *is* a target measurement and is
recorded in `docs/detectors/BATDETECT2_EVALUATION.md`.

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

**Live audio playback moved off this WebSocket path by default, 2026-08-08
(ADR-019).** The figures above are for the WebSocket channel, `/api/v1/live/audio`,
which is unchanged and still used by other clients (a phone, without issue). The
debug UI's GO LIVE button now instead points a plain `<audio>` element at
`GET /api/v1/live/audio.wav`, a chunked-WAV HTTP stream, because Web Audio produced
no audible output on the operator's laptop by any route tried, while a plain
`<audio>` element and YouTube in the same browser both worked. See ADR-019 for the
full diagnosis. One consequence: the +24 dB monitor make-up gain the old client
applied has no replacement on this path, and a calm garden sits near −45 dBFS (see
"Input level and gain" above) — if this proves too quiet in practice, the fix is
server-side gain on the stream, not a client-side audio node.

## Known limitations recorded honestly

- **Range model: off by default in the repository, on at this station.** The shipped
  defaults leave latitude and longitude unset and `birdnet_use_location_filter`
  `False`, so every species is judged on confidence alone. The development
  station's coordinates were written to the Pi's `config/runtime.env` — which is deliberately
  not in version control — and the model is enabled there. This figure changes
  weekly with BirdNET's own seasonal range model; as measured for ISO week 29,
  2026 (2026-07-13 to 07-19): 139 species plausible, 7 suppressed as
  implausible. It matters more than it sounds: before it
  was enabled, "Great Bittern" and "Spotted Crake" were stored at 0.9 confidence in
  an ordinary inland garden. To enable it on another station, set `OO_LATITUDE`,
  `OO_LONGITUDE` and `OO_BIRDNET_USE_LOCATION_FILTER=true`.
- **Ultrasonic detections are not species identifications** and, on a broadband-noisy
  evening, some "bat passes" are likely false positives from wind or handling noise.
  Peak frequency yields a coarse group hint only. Event titles now show a candidate
  group name and peak frequency, for example "36 kHz - Myotis / barbastelle?", but this
  is presentational — the stored record still carries no species label, and the
  question mark is mandatory. Seeing a candidate name in the UI does not mean this
  constraint has been relaxed.
- **Whether this station's 33-36 kHz cluster is genuinely Myotis is unresolved.**
  Offline BatDetect2 classification of the station's own clips (see
  `scripts/classify_clips_batdetect2.py`, ADR-017) leaned Myotis on 6 of 8 clips, but
  at low confidence (0.20-0.30), and produced one confident contradiction: 0.77 for
  *Pipistrellus pygmaeus* on a 34 kHz call, when soprano pipistrelle actually peaks
  near 55 kHz. The AudioMoth's hot gain (see above) is a plausible confound. This
  needs more clips and a human ear on the audible renderings, not a code change.
- **72-hour soak test run 2026-08-10 to 2026-08-13, and it failed.** See the
  dated section below for the measured figures. The system may not be
  described as complete until a soak passes.
- **`estimated_missing_seconds` is the figure to read for lost audio, and
  `expected_frames - frames` is not** — settled by ADR-046 on 2026-08-09, which
  reverses the guidance an earlier version of this file gave. The raw deficit is
  four terms added together: block-sampling phase (±50 ms of pure artefact on a
  single reading), the crystal's ~50.4 ppm slow rate (0.18 s/hour, 4.4 s/day,
  forever, with nothing lost), sub-millisecond anchor bias, and only then real
  loss. Sampled every 2 s for 43 minutes on one uninterrupted stream, the
  corrected deficit grew at **+51.0 to +51.2 ppm** in both clean windows and
  *more slowly* under two saturated cores — a straight line, with none of the
  step that real loss produces. The station has **one** measurement of lost
  audio, not two: `rate_offset_ppm` is computed from the deficit and the
  estimator, so drift-correcting the deficit returns the estimator's own number
  rather than a second opinion. The debug UI now shows the deficit as
  **`behind clock`**, separately from `audio lost`. Longest clean window: 22.2
  minutes — enough to rule out a continuous leak, not a rare one. See
  `docs/delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md` and ADR-046.
- **The ultrasonic detector now has a night scheduler** (`src/open_observatory/schedule.py`),
  gating it to civil dusk through civil dawn plus configurable margins, computed from the
  station's coordinates. It is off by default (`ultrasonic_schedule = "always"`); the
  development station's `runtime.env` sets `OO_ULTRASONIC_SCHEDULE=night`. If coordinates are unset the
  detector runs continuously rather than gating to nothing, by design — see
  `DETECTOR_STRATEGY.md`.
- **Authentication exists but is off by default.** ADR-034 shipped Argon2id
  passwords, session cookies and revocable API tokens on 2026-08-08, closing
  ADR-015. `auth_enabled` defaults to `false`, and **it is not enabled on this
  station**, so the API here still binds LAN-only with anonymous read. Even with
  it enabled, `GET /api/v1/detections`, `GET /api/v1/health` and `/metrics` stay
  reachable with no credential by design (the ESP32 display cannot carry one, and
  `deploy.sh` polls health with none). There is no TLS anywhere in this codebase.

## 2026-08-10 to 2026-08-13: the 72-hour soak, measured

The single formal acceptance run to date. Continuity over the exact 72-hour
window was **99.865%** against a ≥ 99.9% criterion — **349.3 s of audio lost
out of 259,200 s**. Reconstructed from the `capture_gap` table and cross-checked
against the live `continuity_ratio` counter over an identical window (184.4 s vs
175.0 s, within 5%), so two independent instruments agree the loss is real, not
an artefact of either one. Other figures from the same window:

| Property | Value |
|---|---|
| Continuity (exact 72 h) | 99.865% (criterion ≥ 99.9%) |
| Audio lost | 349.3 s of 259,200 s |
| `late_read_max_frames` | 188,982 of a 192,000-frame ring (98.4%) |
| RSS | 1.37–1.72 GB over the run |
| SoC temperature | 61.7 °C |

The run was restart-free for the whole 72 hours, which is itself a first, but
the loss rate stepped up mid-run; see `HANDOVER.md` §1e and
`MILESTONE_STATUS.md` §Milestone 4.5 for the full account, including why
`late_read_max_frames` at 98.4% is worse than the 81% ADR-059 was written to
fix — ADR-059's own verification, below, failed.

## 2026-08-14 02:18:22 UTC: the capture wedge

The microphone wedged for 3 h 35 min. ALSA returned `-EIO` on every read,
roughly 0.576 s apart, until a manual service restart. Root cause:
`alsa_source.py` swallowed the error as a transient xrun inside the
block-assembly loop, so `_read_blocking` never returned or raised, and
`_capture_supervisor` — which exists to reopen the device and would have fixed
this in seconds — was never reached. Fixed by ADR-060. The device itself was
never at fault: no USB event since 8 August, autosuspend off, and a plain
restart recovered it instantly.

## 2026-08-14: retention sweep fix, verified on the station

ADR-061 replaces the retention sweep's unbounded exemplar query — the deeper
cause of both the soak's continuity failure and the wedge above, which was
starving the capture event loop and forcing roughly 12 device restarts an
hour. Verified on the station today:

| Property | Value |
|---|---|
| Retention deletions | 800 files, 3.5 GB, draining |
| Sweep duration | 0.696 s (budget 1.5 s) |
| Preamble | 0.0027 s |
| `capture_gap` rows, 30 min post-deploy | 0 (was 22–24/hour before) |
