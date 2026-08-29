# Development setup

Everything needed to get a working checkout, run the station without hardware,
and run the test suites. **Read the "Traps" section before you start** — every
item in it has cost somebody an hour.

Verified on 2026-08-09 on the project's own development laptop (Ubuntu, no
microphone) and against the live development station.

---

## What you need

| | |
|---|---|
| **Python** | 3.12 exactly. `pyproject.toml` declares `>=3.12,<3.14`. |
| **Node** | For the web UI and its tests only. Verified with Node 22 / npm 10. Not needed on the Pi. |
| **A microphone** | **No.** Replay and synthetic sources are mandatory in the audio spec and fully implemented. |
| **A Raspberry Pi** | **No**, for everything except live capture, ALSA and the target-device smoke tests. |
| OS packages (Linux) | `libasound2-dev` only if you install the `alsa` extra. `ffmpeg`/`libsndfile1` for some audio work. |

## Python environment

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -e ".[dev,resample]"
```

`resample` is **not optional in practice** — see trap 2. Add more extras as you
need them:

| Extra | Gives you | Needed when |
|---|---|---|
| `dev` | pytest, ruff, mypy, hypothesis, jsonschema, httpx | always |
| `resample` | `soxr` + `scipy` | always in practice — many tests fail or hang without it |
| `alsa` | `pyalsaaudio` | only on a machine with the real microphone; needs `libasound2-dev` |
| `birdnet` | `ai-edge-litert` | only to run real BirdNET inference |
| `postgres` | `psycopg[binary]` | only for the (unexercised) PostgreSQL profile |

On the Pi, `deploy/deploy.sh` installs `.[alsa,resample,birdnet,dev]`.

**BatDetect2 is deliberately not an extra.** The overnight refinement runner
([[ADR-045]]) uses it if it is there and reports itself unavailable if it is not,
because its repository is CC-BY-NC-4.0 and that is the operator's licence
decision to make, not a transitive install. `pip install batdetect2==1.3.1` plus
a CPU build of torch, on the machine that will run it. Three tests skip without
it.

### If `python3.12` is not on your PATH

On a recent Ubuntu the system `python3` may already be 3.13 or 3.14, which
`pyproject.toml` excludes. Check first:

```bash
python3 --version
```

If it is out of range, find a 3.12 elsewhere before installing one — a GNOME
snap ships one, for example:

```bash
ls /snap/gnome-46-2404/current/usr/bin/python3.12
/snap/gnome-46-2404/current/usr/bin/python3.12 -m venv .venv
```

That interpreter is fine for the venv: `venv` copies what it needs and the
resulting `.venv/bin/python` does not depend on the snap staying mounted for
ordinary use. `deadsnakes` or `pyenv` work equally well; the point is only that
**the system interpreter is not automatically the right one.**

## Web UI

```bash
cd web
npm ci          # `npm test` fails with "vitest: not found" on a fresh checkout without this
npm test        # vitest, no browser needed
npm run build   # produces web/dist, which the API process serves
```

## Running the station with no hardware

```bash
./.venv/bin/oo serve --source synthetic     # generated dawn chorus / bat passes / sweeps
OO_REPLAY_PATH=recording.wav ./.venv/bin/oo serve --source replay
```

Then open <http://127.0.0.1:8080>. The UI shows a loud red **NOT LIVE AUDIO**
banner whenever the stream is not a real microphone, because a synthetic stream
looks entirely normal in a spectrogram. Synthetic and replay detections are also
excluded from browsing views by default ([[ADR-020]]).

Useful during development:

```bash
./.venv/bin/oo config                # effective settings, with the resolved database DSN
./.venv/bin/oo audio probe           # enumerate capture devices (works with zero devices)
./.venv/bin/oo system-report         # host facts worth attaching to a diagnostic
./.venv/bin/oo audio window-dump     # inspect a segmenter window with ground-truth frame numbers
./.venv/bin/oo refine status         # what the overnight refiner has and has not examined
```

The near-miss ledger ([[ADR-052]]) has no CLI — the `oo` command line has no HTTP
client — so read it from the running station:

```bash
curl -s 'http://127.0.0.1:8080/api/v1/detectors/near-misses' | python3 -m json.tool
```

It is also the **Rejected candidates** panel in the UI's `diagnostics` view. It
lives in memory, so a restart empties it.

The full CLI surface is listed in
[`../operations/DEPLOYMENT_AND_OPERATIONS.md`](../operations/DEPLOYMENT_AND_OPERATIONS.md).

## Configuring a station: the browser, not a text editor (ADR-048)

A station is configured from its own web UI. Open it, press `settings`, and
every operator-editable field is there with its help text, unit, valid range
and shipped default. A first run additionally offers a short guided flow —
where the station is, what it is called and what time it is there, whether the
microphone is actually being recorded from, whether you want MQTT — driven by
`GET /api/v1/setup`.

The UI writes `config/runtime.env` on the device: atomically, mode 0600,
preserving your comments and any keys it does not manage. **A hand-edited file
and a UI edit are one configuration, not two.** `oo config` prints the merged
result either way.

### What used to need a text editor and no longer does

Before [[ADR-048]], exactly five things were web-editable — station name, timezone,
latitude, longitude and the MQTT block — and *everything else* meant an SSH
session, `nano config/runtime.env`, and a service restart. That included every
knob an operator actually reaches for after the station is running:

| Was `runtime.env` only, now in the browser | Why it matters |
|---|---|
| `spectrogram_floor_db` / `ceiling_db`, and the ultrasonic pair ([[ADR-041]]) | contrast is what makes a noisy site readable; takes effect live |
| `ultrasonic_min_snr_db`, `ultrasonic_min_pulses_per_pass`, `ultrasonic_band_hz`, the pulse and buzz bounds | tuning out false bat passes from a noisy mounting; live |
| `activity_min_snr_db`, `activity_min_duration_ms`, `activity_band_hz` | the same for the audible detector; live |
| `birdnet_min_confidence`, `birdnet_plausibility_floor` | rejecting implausible species ([[ADR-032]]); live |
| `birdnet_common_prior`, `birdnet_range_threshold`, and the three band thresholds | **new fields** — these existed only as Python constructor defaults and had no environment surface at all |
| the whole clip block: pre/post-roll, maximum length, minimum score, rate limit, size budget, free-space floor, which detectors clip | live |
| the whole retention ladder and its sweep pacing ([[ADR-026]]/033) | live |
| the ultrasonic rendering block: method, expansion factor, target, high-pass, heterodyne bandwidth | live |
| every refinement setting ([[ADR-045]]) | read by the next `oo refine run`, so in force tonight without restarting the station |
| capture: source, device key, preferred rates and formats, block size, ring depth, ring seconds | saved now, applied at the next restart — a form submission never tears down capture |
| logging, metrics, queue depths, the counter-top display channel | restart-pinned |

### What is deliberately *not* a setting: the pause (ADR-055)

The privacy pause is a button in the UI header and an endpoint
(`POST`/`DELETE /api/v1/pause`), not a settings field. A setting describes how
the station behaves indefinitely; a pause is an action with a deadline, taken
repeatedly, and it is never written to `runtime.env`. Only the *menu* is a
setting: `pause_presets` and `pause_default_preset`, in the new **Privacy**
category.

While paused the station persists, publishes and streams nothing — including
refusing live listening, which is what makes it a pause rather than a label —
but **capture keeps running**, deliberately, so the ALSA device is never closed.
The pause expires by itself, survives a restart (the deadline is persisted, not
a countdown) and is recorded in `capture_pause` so history shows it as
deliberate rather than as a hole.

`config/example.env` and `runtime.env` still work exactly as before, and remain
the only route for the settings that are deliberately not browser-editable
(authentication, bind address, storage paths, `replay_path`, `web_dist`,
`birdnet_model_dir`). The settings page lists those at the bottom, each with the
hazard that earns the exclusion, so a knob you cannot find is explained rather
than merely absent.

The complete field-by-field reference, with tiers and defaults, is in
[`../operations/DEPLOYMENT_AND_OPERATIONS.md`](../operations/DEPLOYMENT_AND_OPERATIONS.md#the-full-settings-reference);
regenerate it with `PYTHONPATH=src python scripts/settings_table.py` after
adding a field.

**If you add a field to `Settings`,** you must also record a decision for it in
`src/open_observatory/site_settings.py` — either an `EditableSetting` with a
tier, or an entry in `NON_EDITABLE` with the reason.
`tests/test_site_settings.py::TestTheAuditIsComplete` fails until you do. If it
is live-tier and some long-lived object holds its value, map it in
`src/open_observatory/tuning.py` as well, or it will report itself applied
while doing nothing.

## Tests and quality gates

```bash
# Python — a bare run is safe now, see trap 3
./.venv/bin/python -m pytest -q
./.venv/bin/ruff check .
./.venv/bin/mypy src            # NOT clean — see trap 5

( cd web && npm test )
```

Measured on 2026-08-09 on this branch:

| Gate | Result |
|---|---|
| `pytest -q` | **826 passed, 11 skipped** in ~186 s. Figures move fast on this repository — a count measured before a parallel branch merged will not match one measured after, so re-measure rather than trusting this line |
| `npm test` (web) | **235 passed** in 22 files |
| `ruff check .` | clean |
| `mypy src` | **22 errors in 11 files** — all pre-existing, see trap 5 |

Of the 11 skips: 8 are the BatDetect2 and BirdNET fixture tests, which skip
cleanly when the (deliberately unbundled) model assets and the optional
`batdetect2` package are absent — that is the designed behaviour, not a broken
environment. The other 3 are
`tests/test_api.py::TestLiveChannels` cases that are marked `skip` outright
(not conditionally), for the starlette limitation described in trap 3 below.

`tests/test_api.py` drives the real FastAPI app over the real pipeline end to
end against a synthetic source, so it is a genuine integration test rather than
a mock harness.

`CLAUDE.md` requires every milestone to add: unit tests, integration tests, a
target-device smoke-test command, a rollback note, updated docs, and measured
CPU/memory/dropped-audio figures where applicable.

## Traps

These are the ones that waste time. None of them is a bug in your setup.

1. **The system `python3` is probably the wrong version.** `pyproject.toml`
   excludes 3.14, which is what a current Ubuntu may give you. See above.

2. **`pip install -e ".[dev]"` alone is not enough.** Without the `resample`
   extra (`soxr`, `scipy`) a substantial number of tests fail, and some of the
   failures look like a hang rather than an error. Always install
   `".[dev,resample]"`.

3. **Three `tests/test_api.py::TestLiveChannels` cases would hang forever if run.**
   Starlette 0.41.3's synchronous `TestClient` (`_TestClientTransport.handle_request`)
   blocks until the ASGI app coroutine returns, so `client.stream(...)` cannot
   represent a still-open connection to `live_audio_wav`'s genuinely infinite
   generator. This is pre-existing and not a regression. The three affected
   tests — `test_audio_wav_streams_a_valid_riff_header_then_pcm`,
   `test_audio_wav_ultrasonic_channel_honours_tune_hz`, and
   `test_audio_wav_disconnect_releases_the_broadcaster_listener` — are marked
   `@pytest.mark.skip` with this reason, so a bare `pytest` no longer needs a
   `--deselect` and completes normally. They are not deleted: they document
   intent and should run again if a dependency upgrade fixes the underlying
   limitation. To exercise that code path for real today, use the WebSocket
   `TestClient` (genuinely concurrent — see
   `test_live_tune_endpoint_retunes_the_shared_oscillator_without_disturbing_an_open_listener`
   in the same file for the pattern) or `httpx.AsyncClient` with `ASGITransport`,
   which supports real async streaming.

4. **`pytest` escalates `DeprecationWarning` to an error**, by configuration and
   on purpose — it is what forced the FastAPI lifespan migration. A dependency
   deprecation will therefore fail the suite rather than warn.

5. **`mypy src` has never been clean.** It reports **22 errors in 11 files** as
   of 2026-08-09, all pre-existing. (An earlier figure of "29 in 12" is stale;
   this one was re-measured by stashing a change and re-running, which is the
   only way to know a baseline honestly.) Judge your change by whether it *adds*
   errors, not by whether the run is green. No document may imply mypy is clean.

6. **`npm test` fails with `vitest: not found` on a fresh checkout.** Run
   `npm ci` in `web/` first.

7. **Pin every dependency to an exact version**, matching the style already in
   `pyproject.toml`. There is a worked example of why in the `click==8.1.8`
   comment there: click 8.2 changed a signature typer still calls the old way,
   and an unpinned resolve broke every `oo --help` on the laptop *and* on the
   live station.

8. **Do not deploy to the live station casually.** `deploy/deploy.sh` restarts
   the systemd unit, which restarts capture and voids any measurement or soak in
   progress. It also `rsync --delete`s, which has destroyed the station's
   gitignored `config/runtime.env` once, and it runs `alembic upgrade head`
   against the live database before restarting.
   `HOST=<user>@<station-host>` is mandatory, so there is no way to deploy
   somewhere by accident. See
   [`../operations/DEPLOYMENT_AND_OPERATIONS.md`](../operations/DEPLOYMENT_AND_OPERATIONS.md).

9. **The station logs UTC; `journalctl --since` takes local time.** BST is
   UTC+1. This has produced an exactly-opposite conclusion once. Print both.

10. **`expected_frames - frames` is not lost audio, and a single reading of it is
    mostly artefact.** It is sampling phase (±50 ms), the AudioMoth's ~50.4 ppm
    slow crystal (4.4 s a day, forever, with nothing lost), anchor bias, and only
    then real loss. Read `estimated_missing_seconds` for loss; the UI shows the
    deficit separately as `behind clock`. An earlier version of this trap said
    the exact opposite — [[ADR-046]] measured it and settled it. See
    [`../delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md`](../delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md).

11. **Loopback is not a test of a network path.** The concurrent-WebSocket-writer
    bug was flawless on loopback and near-total failure over Wi-Fi. Anything
    touching the live channels must be measured from a real client over the real
    link ([[ADR-012]]).

12. **Model assets are never committed**, and never will be. BirdNET's weights
    are CC BY-NC-SA 4.0 and BatDetect2's whole repository is CC-BY-NC-4.0.
    `oo models fetch` is the acquisition path; tests that need assets skip rather
    than fail when they are absent.

## Repository layout

```
src/open_observatory/     the station
  api/                    FastAPI app, routes, Prometheus metrics
  audio/                  ALSA/replay/synthetic sources, ring, resample,
                          spectrogram, heterodyne, ultrasound rendering
  detectors/              activity-v1, birdnet-v2.4, ultrasonic-pass-v1,
                          the deferred-worker mechanism
  refinement/             charter item 5 (ADR-045): the refinement runner, which
                          runs in its OWN process on a systemd timer, not here.
                          Nothing under `station.py` imports it.
  db/                     SQLAlchemy models and session/bootstrap
  mqtt/                   publisher + Home Assistant Discovery (off by default)
  hardware/               AudioMoth USB HID
  station.py              wires everything together; owns the housekeeping loop
  segmenter.py            WindowSpec/AudioWindow, the immutable window contract
  normaliser.py           enforces the honesty rules; raises ClaimViolation
  clips.py / retention.py evidence writing and tiered age-out
  auth.py                 the authentication foundation (off by default)
  cli.py / config.py      the `oo` CLI and every OO_* setting
web/                      React + TypeScript + Vite debug/operator UI
firmware/inside-observer/ ESP32 counter-top display (PlatformIO)
alembic/                  migration environment, five revisions
deploy/                   deploy.sh, the two systemd units (station + the
                          separate refinement runner and its timer), udev rules
schemas/                  the published event envelope
tests/                    pytest suites, including fixture audio
scripts/                  offline benchmarks and the BatDetect2 cascade tool
```

## Working method

- Prefer boring, observable, testable components over clever abstractions.
- Match the surrounding code's style, comment density and naming. This codebase
  comments *why*, at length, and that is deliberate.
- Add structured logs, metrics, health checks and graceful degradation with any
  new service.
- Use UTC internally; present local time only through the configured IANA
  timezone.
- Record any material architectural deviation as a new ADR in
  [`../architecture/ADRS.md`](../architecture/ADRS.md). Never delete or renumber
  an existing one — they are referenced by number from comments across `src/`,
  `web/` and `firmware/`.
- Evidence before assertions. Do not write "tests pass" without having run them.

## PlatformIO needs its own venv

`~/.platformio` holds the packages, but there is no `pio` binary on `PATH`, so
`pio run` and `pio test` fail with "command not found" rather than anything that
points at the cause. Put it in a venv, somewhere durable:

```bash
python3 -m venv ~/piovenv && ~/piovenv/bin/pip install -q platformio==6.1.19
~/piovenv/bin/pio run -e cyd -t upload      # or: -e native for host tests
```

`~/piovenv` already exists on this laptop, running PlatformIO Core 6.1.19, and
is the path the firmware documentation and smoke tests assume. An earlier
version of this section said `/tmp/piovenv`, which does not survive a reboot.
