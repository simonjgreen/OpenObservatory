# Development setup

Everything needed to get a working checkout, run the station without hardware,
and run the test suites. **Read the "Traps" section before you start** — every
item in it has cost somebody an hour.

Verified on 2026-08-09 on the project's own development laptop (Ubuntu, no
microphone) and against the live station at `station.example`.

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
excluded from browsing views by default (ADR-020).

Useful during development:

```bash
./.venv/bin/oo config                # effective settings, with the resolved database DSN
./.venv/bin/oo audio probe           # enumerate capture devices (works with zero devices)
./.venv/bin/oo system-report         # host facts worth attaching to a diagnostic
```

The full CLI surface is listed in
[`../operations/DEPLOYMENT_AND_OPERATIONS.md`](../operations/DEPLOYMENT_AND_OPERATIONS.md).

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
| `pytest -q` | **557 passed, 9 skipped** in ~154 s (baseline immediately before the ADR-042 refinement work: **498 passed, 9 skipped**, re-measured the same day from a stash) |
| `npm test` (web) | **140 passed** |
| `ruff check .` | clean |
| `mypy src` | **22 errors in 11 files** — all pre-existing, see trap 5 |

Of the 9 skips: 6 are the BatDetect2 and BirdNET fixture tests, which skip
cleanly when the (deliberately unbundled) model assets are absent — that is
the designed behaviour, not a broken environment. The other 3 are
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
   gitignored `config/runtime.env` once and will delete `web/dist` if you pass
   `--no-web`. See
   [`../operations/DEPLOYMENT_AND_OPERATIONS.md`](../operations/DEPLOYMENT_AND_OPERATIONS.md).

9. **The station logs UTC; `journalctl --since` takes local time.** BST is
   UTC+1. This has produced an exactly-opposite conclusion once. Print both.

10. **`estimated_missing_seconds` over-reports lost audio.** Judge real loss by
    `frames` vs `expected_frames` in `/api/v1/health`, never by that field. See
    [`../delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md`](../delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md)
    and ADR-033.

11. **Loopback is not a test of a network path.** The concurrent-WebSocket-writer
    bug was flawless on loopback and near-total failure over Wi-Fi. Anything
    touching the live channels must be measured from a real client over the real
    link (ADR-012).

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
  refinement/             charter item 5 (ADR-042): the refinement runner, which
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
firmware/inside-observer/ ESP32 wall display (PlatformIO)
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
