# Open Observatory

A passive acoustic observatory for a garden. A Raspberry Pi 5 and an AudioMoth
USB microphone listen continuously, and the record they keep is honest about its
own uncertainty.

Capture runs at 384 kHz, so bats are inside the band. A 48 kHz audible stream is
derived from the same frames, three detectors run over immutable time-addressed
windows, and every detection that earns one is stored with a checksummed evidence
clip cut from the native ring buffer. It is local-first: capture, detection,
review and query never need the internet.

A station is configured from its own web UI, watched from a browser or from an
ESP32 counter-top display it can update over the air, and read through a
REST/WebSocket API, an optional MQTT feed into Home Assistant, and Prometheus.

It runs unattended, as a systemd service. The reference station has been
recording since it was commissioned on 4 August 2026: 74,969 detections, 93
named bird species and 61 GB of evidence clips as of 2026-08-09. It has not yet
run the continuous 72-hour soak the acceptance criteria require, so nothing here
is called complete or verified —
[`docs/delivery/MILESTONE_STATUS.md`](docs/delivery/MILESTONE_STATUS.md) is the
ledger of what is delivered and what is outstanding.

**New here?** [`docs/README.md`](docs/README.md) is the map of all the
documentation. If you are about to write code,
[`docs/development/SETUP.md`](docs/development/SETUP.md) first — it lists the
setup traps that will otherwise cost you an hour.

## What it looks like

All three are a real station running on real hardware, not mockups. The station
name is the only thing edited.

### Live

![The live view: an ultrasonic spectrogram above an audible one, ranked species
suggestions below, and the storage budget below that](docs/screenshots/live.png)

Two spectrograms, stacked so their frequency axes form one continuous run from
100 Hz to 150 kHz. Each panel states the parameters it is actually drawing with
— `15 kHz–150 kHz`, `128 bins`, `24 ms/col`, `FFT 4096` — because a spectrogram
with undeclared settings is a picture, not a measurement.

Below them, candidates carry the score **as a number**, the detector that said
it, and the time. Note what the footer says: *levels are dBFS relative to
digital full scale, not calibrated SPL; scores are model outputs, not
probabilities, unless a detector declares calibration.* Those two sentences are
load-bearing — see [Honesty rules](#honesty-rules-this-codebase-enforces-in-code-not-just-in-prose).

### History

![The history view: a capture-coverage bar above a 24-hour timeline split into
bat and bird detections, with a species table beneath](docs/screenshots/history.png)

**Capture coverage sits above the timeline, not beside it.** `99.8% captured ·
23h 56m from the microphone · 545 gaps · 22 streams` is the first thing you
read, because an empty hour means something completely different depending on
whether nothing called or nothing was recording. Distinguishing a quiet night
from a dead microphone is a first-class requirement here, not a diagnostic
nicety.

The purple/green split is bats against birds, and it shows the thing you would
hope to see: bats confined to the dark hours, birds bracketing them with a dawn
peak. The caption under the chart — *counts of detections, not of animals* —
exists because one woodpigeon calling repeatedly produces 2,467 of them.
`Engine` appears in the species table as a non-taxonomic class, which is the
system declining to call a passing car a bird.

### The indoor display

![A small 3D-printed portrait display standing on a counter top, showing LIVE IN
THE GARDEN and a list of recent species with times](docs/screenshots/indoor-monitor.jpg)

An ESP32 with a 2.8" touchscreen, on the same WiFi as the station, showing what
is in the garden right now. **This is the everyday face of the system**: the
normal state of a working observatory is nobody at a browser, so the counter-top
display is a first-class surface and the web UI is the one you open when you
want to dig in.

It deliberately shows no scores and only identifications above a confidence
threshold, because a number sitting in a room invites a reading it cannot
support. It
must also look *unreachable* when it cannot reach the station, never merely
quiet — a stale list that looks fresh is the one failure this surface must not
have.

## What it does

- **Captures once, at the highest rate the device offers.** One process owns the
  microphone; detectors never open it. On the reference station that is 384 kHz
  mono, giving a 192 kHz Nyquist — enough for every UK bat.
- **Derives an audible 48 kHz stream** with libsoxr, verified to have zero group
  delay so audible detections keep native-stream timing.
- **Cuts immutable, time-addressed windows** to each detector's own specification,
  so a 3-second BirdNET window and a 1-second onset window coexist over one
  microphone and a slow detector falls behind as *lag* rather than stalling capture.
- **Runs three detectors:**
  - `activity-v1` — band-limited onset detection. No model, no downloads, no
    taxonomic claim. Works out of the box.
  - `birdnet-v2.4` — BirdNET GLOBAL 6K V2.4, ~40× realtime on a Pi 5. Model assets
    are *not* bundled; `oo models fetch` installs them with checksums and licences shown.
  - `ultrasonic-pass-v1` — bat *pass* detection on the native stream. Pulse trains
    and peak frequency, explicitly not a species identification.
- **Writes evidence clips** at the authoritative rate with a browser-playable
  derivative, inside a rate limit, size budget and disk reserve.
- **Makes ultrasound audible.** A 48 kHz bat call is inaudible and undecodable by a
  browser, so ultrasonic detections also get time-expanded (slowed, so frequencies
  divide — preserves everything) and heterodyned (mixed down like a handheld
  detector — preserves real time) renderings, each labelled with what it changed.
- **Does not call a car a bird, and does not keep recordings of people.**
  BirdNET's eleven non-bird sound categories — `Engine`, `Human vocal`, `Dog`, …
  — are stored as acoustic events with no rank and no scientific name, and a
  human-voice detection gets a row and no audio at all (ADR-049).
  `clip_human_audio` turns the second half off; it defaults to false and makes
  you acknowledge a warning first.
- **Serves a real-time debug UI** with a scrolling spectrogram (audible and
  ultrasonic), live species/event list, low-latency listen button, and the pipeline's
  own internals.
- **Configures itself from the browser.** 132 settings, in three declared tiers —
  live, restart-pinned, and the twenty deliberately not editable from a browser,
  each listed with the hazard that excludes it (ADR-047/048). A first run offers a
  guided flow. The UI writes `config/runtime.env` on the device, atomically,
  preserving your comments; a hand edit and a UI edit are one configuration.
- **Refines the record overnight, and only ever proposes** (ADR-045). A second,
  CPU-fenced process on cores 2–3 runs a BatDetect2 cascade over stored bat clips
  at 01:00 UTC. It writes append-only `refinement` rows and can never rewrite a
  detection's claim. Capture keeps cores 0–1 to itself.
- **Records what BirdNET refused, not just how many** (ADR-052) — per-species
  near misses with the score, the occurrence prior and the bar they fell short
  of, so a threshold can be moved on evidence. Metadata only: no audio is kept
  for a rejected candidate.
- **Publishes to Home Assistant** over MQTT with Discovery, off by default
  (ADR-025), and **pushes to a counter-top ESP32 display** over a WebSocket that
  costs about 11 B/s — and can update that display's firmware over the air, with
  a checksum before install and a rollback the display owns (ADR-050).

## Quick start

On the Pi:

```bash
sudo apt install -y build-essential python3-dev python3-venv libasound2-dev \
                    alsa-utils ffmpeg libsndfile1
python3.12 -m venv .venv        # 3.12 exactly; pyproject requires >=3.12,<3.14
.venv/bin/pip install -e '.[alsa,resample,birdnet,dev]'

.venv/bin/oo audio probe          # what is attached, and what it actually supports
.venv/bin/oo models fetch         # optional: BirdNET assets (CC BY-NC-SA 4.0)
.venv/bin/oo serve                # capture + detectors + API + UI on :8080
```

From a workstation, build the UI, sync, migrate and restart in one step:

```bash
HOST=<user>@<station-host> ./deploy/deploy.sh
```

`HOST` is required — this repository ships no station address (ADR-047). The
script runs `alembic upgrade head` against the still-running old version before
it restarts anything, so a failing migration leaves the working service up.

Then open `http://<station-host>:8080` and press `settings`. Everything an
operator tunes lives there; a terminal is not part of the loop.

**No microphone?** That is a supported mode, not a failure — the audio pipeline
spec makes replay mandatory:

```bash
oo serve --source synthetic          # generated dawn chorus / bat passes / sweeps
OO_REPLAY_PATH=recording.wav oo serve --source replay
```

The UI shows a loud red **NOT LIVE AUDIO** banner whenever the stream is not the
real microphone, because a synthetic stream looks entirely normal in a spectrogram.

## The debug UI

Design inspiration is the Merlin Bird ID app — spectrogram on top, ranked
candidates below it, the current one highlighted — with the extra screen space
spent on what a diagnostic surface needs and a product dashboard would hide
(ADR-011).

- **Two live spectrograms**, in either of two views. Audible 80 Hz–15 kHz, and
  ultrasonic 15–150 kHz when the native rate supports it. Log-frequency, adjustable
  history, three palettes (including a Merlin-style greyscale), adjustable range.
  - **scroll** — time across the page with now at the right, frequency vertical.
    Reads rhythm and the shape of a call well.
  - **waterfall** — frequency across the page, time down it with now at the top.
    Reads where energy sits across the band well.

  The panels are ordered so their frequency axes form one continuous run either way:
  ultrasound above audible in scroll, audible left of ultrasound in waterfall.
- **Best suggestions.** Species and events grouped or as a timeline, with the score
  as a number, which detector said so, and a clip to check it against. A score is
  never called a probability unless the detector declares itself calibrated.
- **GO LIVE.** Low-latency listening, measured at ~180 ms end to end, with monitor
  gain and a limiter, plus buffer/underrun/latency telemetry on display.
- **Pipeline panels.** Capture continuity, device clock offset, hot-path CPU,
  resampler timing, ring-buffer fill and extraction misses, per-detector queue depth
  and lag, clip policy decisions, disk budget, lease balance, bus drops.
- **Event stream.** Every `capture.*`, `window.*`, `detection.*`, `clip.*` and
  `health.*` event, filterable and pausable.
- **HISTORY mode.** The live channel only knows the session it is connected for, so
  there is a second mode that reads what was persisted: named windows (last night,
  dawn chorus, yesterday, …) resolved in the station's own timezone, a timeline of
  detections per bucket split by group, what was identified and when it called, and
  clips playable from any of it. Click a bucket or a species to focus the list on it.

  **Capture coverage is shown above the timeline**, because an empty window means
  something completely different depending on whether nothing called or nothing was
  recording. Aggregation happens in SQL — a night holds around 170,000 activity
  detections, and the browser is sent a few hundred numbers rather than all of them.

## Commands

| Command | What it does |
|---|---|
| `oo audio probe` | Enumerate capture devices; record formats, stable identity and native rate support |
| `oo audio test-capture` | Capture briefly and report frames delivered vs elapsed, levels and clipping |
| `oo audio resample-check` | Verify group delay, delivery-latency bounds and seam continuity |
| `oo audio window-dump` | Inspect one segmenter window against ground-truth frame numbers, with optional injected gaps |
| `oo audiomoth info` | Firmware identity over USB HID (switch in `USB/OFF`) |
| `oo models status` / `fetch` | Model asset state and checksummed acquisition |
| `oo history reconcile-streams` | Repair stream rows whose claimed span the frame count contradicts (ADR-024). Dry-run by default |
| `oo detections reconcile-plausibility` | Re-check stored BirdNET rows against the current range model (ADR-032). Dry-run by default |
| `oo detections reconcile-taxonomy` | Stop stored sound categories claiming to be birds at species rank (ADR-049). Dry-run by default |
| `oo clips purge-human-audio` | Delete stored clips of human speech and mark the assets reclaimed (ADR-049). Dry-run by default |
| `oo clips retention` | Run the tiered clip retention sweep by hand (ADR-026) |
| `oo refine run` / `status` | One overnight refinement pass, and what the refiner has and has not examined (ADR-045) |
| `oo system-report` | Host facts worth recording with a diagnostic |
| `oo serve` | Run the station |
| `oo config` | Print effective configuration |

The four repair commands — three `reconcile-*` and `purge-human-audio` — are
dry-run by default and need both `--apply` and a confirmation. None has been run
with `--apply` against the live station.

## Architecture

Only the capture service opens the ALSA device. It publishes immutable
time-addressed windows; audible detectors get the derived 48 kHz stream, ultrasonic
detectors get the native high-rate stream. Evidence is always cut from the native
ring buffer.

```
AudioMoth 384 kHz ──▶ capture ──▶ native ring (120 s) ──▶ evidence clips
                         │                    ▲
                         ├──▶ soxr 1/8 ──▶ audible ring
                         │         │
                         │         ├──▶ spectrogram ──┐
                         ├──▶ spectrogram (ultrasonic)┤──▶ WebSocket ──▶ debug UI
                         │         └──▶ live audio ───┘
                         │
                         └──▶ segmenter ──▶ windows ──▶ detector workers
                                                            │
                                          normaliser ◀──────┘
                                               │
                                    SQLite/PostgreSQL + clips ──▶ REST API
```

Beyond the debug UI, the same API feeds an **ESP32 counter-top display** in the
house (`firmware/inside-observer/` — a pushed WebSocket at ~11 B/s with an HTTP
poller as fallback, never a score on the wire, and its own firmware updates over
the air; ADR-023/038/050) and an optional **MQTT publisher** with Home Assistant
Discovery (`src/open_observatory/mqtt/`, off by default — ADR-025). An
**authentication foundation** exists and is also off by default (ADR-034).

Two things run outside `oo serve`, on purpose. The **refinement runner**
(`src/open_observatory/refinement/`, ADR-045) is its own systemd service on a
timer, fenced to cores 2–3, so a 2-second inference pass can never starve the
capture loop; the station process does not import it. The **web build** happens
on the workstation, because the Pi has no Node toolchain and does not need one.

### Where to read next

[`docs/README.md`](docs/README.md) is the full map. If you want a reading order:

1. [`docs/development/SETUP.md`](docs/development/SETUP.md) — get it running, and the traps
2. [`CLAUDE.md`](CLAUDE.md) — the operating brief
3. [`docs/delivery/MILESTONE_STATUS.md`](docs/delivery/MILESTONE_STATUS.md) — what is and is not done
4. [`docs/architecture/ADRS.md`](docs/architecture/ADRS.md) — every decision and deviation, indexed with status
5. [`docs/operations/TARGET_DIAGNOSTICS.md`](docs/operations/TARGET_DIAGNOSTICS.md) — measured hardware facts
6. [`docs/delivery/HANDOVER.md`](docs/delivery/HANDOVER.md) — operational traps and the next-steps list
7. [`docs/api/DEBUG_UI_TRANSPORT.md`](docs/api/DEBUG_UI_TRANSPORT.md) — the live protocol
8. [`docs/operations/AUDIOMOTH_FIRMWARE.md`](docs/operations/AUDIOMOTH_FIRMWARE.md) — switch positions, firmware, gain

The original product and architecture specifications are kept unedited under
`docs/product/` and `docs/architecture/TECHNICAL_SPEC.md`, each with a header
naming where the built system diverges from it.

## Tests

```bash
.venv/bin/python -m pytest -q
( cd web && npm ci && npm test )
```

Measured on this branch on 2026-08-09: **826 Python tests pass, 11 skip** (8 are
the fixture tests for the deliberately-unbundled BirdNET and BatDetect2 model
assets, which skip rather than fail by design; 3 are
`tests/test_api.py::TestLiveChannels` cases that starlette 0.41.3's synchronous
`TestClient` cannot represent — see
[`docs/development/SETUP.md`](docs/development/SETUP.md) trap 3), and **235
frontend tests pass**. `ruff check .` is clean.
`mypy src` reports 22 pre-existing errors and has never been clean.

The Python tests run without a microphone, against the mandated replay/synthetic
sources; `tests/test_api.py` drives the real FastAPI app over the real pipeline end
to end. See [`docs/development/SETUP.md`](docs/development/SETUP.md) for the full
list of setup traps.

The frontend tests cover the display geometry, which is where a bug is most
dangerous: a view that puts a sound at the wrong frequency or time produces
confident, wrong conclusions. Both orientations are asserted against the same
properties, so adding the second view cannot silently break the first.

## How this was built

**The code here is almost entirely AI-authored. The thinking behind it is not.**

That division is deliberate and worth stating plainly, because it changes how
you should read the repository.

**Human** — the concept and why it exists; the product design and what the
thing is *for*; the system architecture and how the pieces divide; the
priorities and what wins when they conflict (see
[`docs/CHARTER.md`](docs/CHARTER.md)); what "tested" has to mean
([`docs/development/TEST_PLAN.md`](docs/development/TEST_PLAN.md)); the hardware
choices; and continual review, direction and correction throughout. Every
significant decision was made, or accepted, by a person who understood the
system as a whole.

**AI** — nearly all of the implementation. The Python, the TypeScript, the
firmware, the tests, and most of the prose in `docs/`, written under direction
and reviewed.

### Why this is stated rather than hidden

Two reasons, both practical.

The first is that it explains the shape of the repository. The unusual density
of Architecture Decision Records, the measured figures attached to almost every
claim, and the explicit lists of what is *not* verified are not stylistic
choices. They exist because AI-written code is confidently plausible by default,
and plausibility is not correctness. The discipline throughout has been to
require evidence — a measurement, a test against ground truth, a reading from
the real device — before a claim is allowed to stand. `HANDOVER.md` documents
several occasions where that discipline was the only thing that caught a bug
which had already passed review and its own tests.

The second is that a reader deserves to know. Code written this way needs a
different kind of scepticism than code written by hand: it fails less often at
syntax and more often at assumptions — a test that asserts an invented value
rather than the one the system really emits, a metric that measures something
adjacent to what its name claims. Those failures are quiet and they look like
success.

### What this means if you contribute

The bar is evidence, not authorship. It does not matter whether a change was
written by a person or a model; it matters whether the claims attached to it
were verified, and whether the things that could not be verified are stated as
such. [`docs/development/TEST_PLAN.md`](docs/development/TEST_PLAN.md) sets out
what that requires, and it opens with the bugs that passed their tests first,
because that is the failure mode this project is built to resist.

## Honesty rules this codebase enforces in code, not just in prose

- A detector declared non-taxonomic **cannot** emit a species name; the normaliser
  raises and refuses the detection (ADR-010).
- A detector that has not declared calibration **cannot** report a calibrated
  probability.
- BirdNET's eleven non-bird sound categories are stored with no rank, no
  scientific name and `taxonomic_group: acoustic_event`, because a classifier
  saying `Engine` is not the classifier identifying a bird (ADR-049).
- A detection of a human voice is written down and its audio is not, by default.
  The microphone records neighbours and passers-by who never consented; that is a
  charter constraint, not a setting with a sensible other value (ADR-049).
- Levels are labelled dBFS relative to digital full scale, never as calibrated SPL,
  because no calibration procedure exists yet.
- Audible renderings of ultrasound are filtered and normalised, so they record
  `amplitudes_comparable_to_native: false` and the UI marks them "processed". Only
  the native clip is evidence of level.
- ALSA rate substitution is refused rather than accepted, so the stream's true
  bandwidth is always known.
- A synthetic or replayed source is stated loudly everywhere it appears, including
  in `/api/v1/health`.
- Detection thresholds are calibrated against measured noise, not guessed. The
  activity detector's threshold sits above where stationary noise actually reaches
  on its own statistic; an earlier guessed value sat below it and fired on every
  window.
- Model assets are never bundled; their licences differ from this code's and are
  displayed before download and in the UI.

## Hardware

This is the reference station — the one every measured figure in these documents
came from. Nothing here is required by the software: the station discovers what
it is attached to and records what it actually negotiated (`oo audio probe`).
It is listed because "what did you build it out of" is the first question
anybody asks, and because a figure means more when you know what produced it.

| | What | Notes |
|---|---|---|
| Computer | Raspberry Pi 5 Model B Rev 1.1, 8 GB | Ubuntu 24.04 LTS, `aarch64` |
| Case | [Flirc Raspberry Pi 5 case](https://thepihut.com/products/flirc-raspberry-pi-5-case) | Passive; the aluminium body is the heatsink. No fan. Idles around 39 °C with capture and three detectors running |
| Power | 5 V 3 A USB charger | An iPad charger, chosen as a known-good supply. **See the note below — this is under the Pi 5's rated 5 A** |
| System storage | SanDisk 256 GB microSD | OS, application and the SQLite database |
| Evidence storage | SanDisk Extreme Portable SSD, 500 GB (`0781:558c`) | USB, UAS. Mounted over `data/clips`; carries clips only, deliberately not the database (ADR-021) |
| Microphone | [AudioMoth USB Microphone](https://www.openacousticdevices.info/product-page/audiomoth-usb-microphone) (`16d0:06f3`) | A dedicated variant of the AudioMoth 1.2.0 design rather than a recorder running different firmware. Negotiates 384 kHz mono `S16_LE` here |
| Microphone case | [Official AudioMoth USB Microphone case](https://www.openacousticdevices.info/product-page/audiomoth-usb-microphone-case) | |
| Microphone cable | Anker 2 m micro-USB | Long enough to reach the eaves from indoors |
| Display | ESP32-2432S028R ("Cheap Yellow Display") | 2.8" 240×320 ILI9341, XPT2046 resistive touch. See [`firmware/inside-observer/`](firmware/inside-observer/) |
| Display case | [Printed case](https://makerworld.com/models/1382304) | From the *Aura* project's own build, which this board was assembled for |
| Network | WiFi | The Pi's Ethernet port is unused |

**Almost all of this was already lying around**, which is worth saying plainly.
The Pi and its case were spares from an abandoned project; the SSD came out of a
retired k3s cluster of Pi 4s; the display had been built as a weather
forecaster; the power supply is an iPad charger, picked because it was known to
be good. The microphone is the only part bought for the job.

That is not incidental. A passive acoustic station does not need purpose-built
hardware, and the interesting constraints here — a 600 mA USB budget, a database
on an SD card, a display with somebody else's WiFi credentials in NVS — are all
consequences of building it out of what was to hand rather than out of a parts
list.

The display's history in particular shapes the firmware. The board was assembled as
[**Aura**](https://surrey-homeware.github.io/aura-installer/) — an open-source
smart weather-forecast display for this exact board, not a project of ours — and
went spare when a TRMNL replaced it. The case, the assembly and the WiFi
credentials sitting in NVS all predate this repository.

That inheritance explains three decisions that would otherwise look arbitrary:
the partition table preserves the original NVS region byte for byte, because it
holds credentials nobody here has ever seen and cannot retype; a complete 4 MB
image was taken before anything was written, so the board can be returned to
Aura; and the provisioning access point was called `Aura` until it was renamed
to something per-device (ADR-050).

**Siting.** The Pi lives indoors in a summer house at the end of the garden. The
microphone hangs on a hook under the eaves, on the 2 m cable run out through the
window jamb. That arrangement is why the station hears the garden and not the
room, and why the microphone's exact position has a larger effect on the data
than any setting in this repository — moving it a few feet changed the noise
floor materially.

### Notes worth knowing before you copy it

- **The AudioMoth's three-position switch matters.** `DEFAULT` streams audio;
  `USB/OFF` is configuration only and produces no ALSA card at all. Setting it to
  `USB/OFF` is exactly what caused a 29-hour outage during commissioning.
- **Card numbers are not stable.** The AudioMoth moved from card 2 to card 0
  across a reboot. Nothing in this codebase addresses a device by index.
- **The Pi 5 wants 5 V 5 A, and this station runs on 3 A.** The consequence is
  visible in firmware: `usb_max_current_enable=0`, which caps *total* USB current
  at 600 mA — shared, here, between an SSD and a microphone. It has not caused a
  fault: `vcgencmd get_throttled` reads `0x0`, meaning no undervoltage has ever
  been recorded on this station. It is listed because an underpowered supply is a
  genuinely plausible cause of an intermittently-enumerating microphone, and
  because anyone reproducing this should make the choice knowingly rather than
  inherit it. A 27 W supply removes the constraint.
- **A microSD is not a good home for a database that writes continuously.** This
  one is, for now; the charter treats storage endurance as a standing constraint
  and the evidence clips were moved to the SSD for exactly this reason.

## Licence

Apache-2.0 for this code. Third-party model assets carry their own terms — BirdNET's
released models are CC BY-NC-SA 4.0, which prohibits commercial use. See
`/api/v1/models` on a running station for what is installed and under what terms.
