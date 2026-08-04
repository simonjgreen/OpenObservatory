# Open Observatory

A local-first, modular, continuously operating passive acoustic observatory for a
Raspberry Pi 5 and AudioMoth USB microphone.

**Status: Milestones 0–3 running on real hardware.** The station captures a live
384 kHz stream from an AudioMoth, derives a 48 kHz audible stream, runs three
detectors over time-addressed windows, writes checksummed evidence clips, and
serves a real-time debug UI. It is *not* finished — see
[`docs/delivery/MILESTONE_STATUS.md`](docs/delivery/MILESTONE_STATUS.md) for an
honest account of what works and what does not, and do not describe it as complete
until the acceptance criteria pass a 72-hour soak.

## What it does today

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
- **Serves a real-time debug UI** with a scrolling spectrogram (audible and
  ultrasonic), live species/event list, low-latency listen button, and the pipeline's
  own internals.

## Quick start

On the Pi:

```bash
sudo apt install -y build-essential python3-dev python3-venv libasound2-dev \
                    alsa-utils ffmpeg libsndfile1
python3 -m venv .venv
.venv/bin/pip install -e '.[alsa,resample,birdnet,dev]'

.venv/bin/oo audio probe          # what is attached, and what it actually supports
.venv/bin/oo models fetch         # optional: BirdNET assets (CC BY-NC-SA 4.0)
.venv/bin/oo serve                # capture + detectors + API + UI on :8080
```

From a workstation, build the UI and deploy in one step:

```bash
HOST=station.example ./deploy/deploy.sh
```

Then open `http://<pi>:8080`.

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

- **Two live spectrograms.** Audible 80 Hz–15 kHz, and ultrasonic 15–150 kHz when
  the native rate supports it. Log-frequency, adjustable history, three palettes
  (including a Merlin-style greyscale), adjustable display range.
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

## Commands

| Command | What it does |
|---|---|
| `oo audio probe` | Enumerate capture devices; record formats, stable identity and native rate support |
| `oo audio test-capture` | Capture briefly and report frames delivered vs elapsed, levels and clipping |
| `oo audio resample-check` | Verify group delay, delivery-latency bounds and seam continuity |
| `oo audiomoth info` | Firmware identity over USB HID (switch in `USB/OFF`) |
| `oo models status` / `fetch` | Model asset state and checksummed acquisition |
| `oo system-report` | Host facts worth recording with a diagnostic |
| `oo serve` | Run the station |
| `oo config` | Print effective configuration |

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

Read in this order:

1. [`CLAUDE.md`](CLAUDE.md) — operating brief
2. [`docs/architecture/GAP_REPORT.md`](docs/architecture/GAP_REPORT.md) — where reality diverged from the spec
3. [`docs/architecture/ADRS.md`](docs/architecture/ADRS.md) — decisions, including every deviation
4. [`docs/operations/TARGET_DIAGNOSTICS.md`](docs/operations/TARGET_DIAGNOSTICS.md) — measured hardware facts
5. [`docs/delivery/MILESTONE_STATUS.md`](docs/delivery/MILESTONE_STATUS.md) — what is and is not done
6. [`docs/api/DEBUG_UI_TRANSPORT.md`](docs/api/DEBUG_UI_TRANSPORT.md) — the live protocol
7. [`docs/operations/AUDIOMOTH_FIRMWARE.md`](docs/operations/AUDIOMOTH_FIRMWARE.md) — switch positions, firmware, gain

Original product and architecture specifications are unchanged under `docs/product/`
and `docs/architecture/TECHNICAL_SPEC.md`.

## Tests

```bash
.venv/bin/python -m pytest        # 135 tests
```

They run without a microphone, against the mandated replay/synthetic sources.
`tests/test_api.py` drives the real FastAPI app over the real pipeline end to end.

## Honesty rules this codebase enforces in code, not just in prose

- A detector declared non-taxonomic **cannot** emit a species name; the normaliser
  raises and refuses the detection (ADR-010).
- A detector that has not declared calibration **cannot** report a calibrated
  probability.
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

## Hardware notes

- The AudioMoth's three-position switch matters: **`DEFAULT` streams audio**,
  `USB/OFF` is configuration only and produces no ALSA card at all.
- Card numbers are not stable. The AudioMoth moved from card 2 to card 0 across a
  reboot during commissioning. Nothing here addresses a device by index.
- Use a PSU that can actually supply the Pi 5's rated current; an underpowered one
  limits USB current and an intermittently-enumerating microphone is a plausible
  symptom.

## Licence

Apache-2.0 for this code. Third-party model assets carry their own terms — BirdNET's
released models are CC BY-NC-SA 4.0, which prohibits commercial use. See
`/api/v1/models` on a running station for what is installed and under what terms.
