# BatDetect2 evaluation

Status: **evaluated, not supported**. Per `CLAUDE.md`, this project does not
claim a detector is supported until an automated fixture test passes on the
target architecture (Raspberry Pi 5). `tests/test_batdetect2.py` is that
gate; it skips rather than fails until BatDetect2 and its assets are present,
and it has not yet been run to a pass on the Pi. This document explains what
BatDetect2 is, how to install and benchmark it, and holds the (currently
empty) table the measured figures go into. See ADR-017 in
`docs/architecture/ADRS.md` for the decision this evaluation is answering.

## What it is

[BatDetect2](https://github.com/macaodha/batdetect2) is a deep-learning
detector and species classifier for UK/European bat echolocation calls,
built by **Oisín Mac Aodha** (University of Edinburgh) and **Santiago
Martinez Balvanera** (UCL), with contributions from the wider bioacoustics
group behind it. It takes ultrasonic audio, finds candidate echolocation
calls, and classifies each one against 17 UK bat species/genus classes. It
is the closest existing counterpart to what this project's BirdNET adapter
does for birds, but for the ultrasonic stream instead of the audible one.

## Licence — CC-BY-NC-4.0, and what that forbids

BatDetect2's own `pyproject.toml` declares `license = { text = "CC-by-nc-4" }`,
and this applies uniformly to the **code, the released model weights, and
the example recordings** — there is no split licence the way there is for
BirdNET (whose code is Apache-2.0 but whose weights are CC BY-NC-SA 4.0).

CC-BY-NC-4.0 permits copying, redistributing and adapting the work, with
attribution, **for non-commercial purposes only**. It does not define
"non-commercial" itself beyond "not primarily intended for or directed
toward commercial advantage or monetary compensation" — the authors state
plainly that commercial use is not permitted.

Consequences for this repository (ADR-006, ADR-017):

- No BatDetect2 code, weights, or example audio is committed to this
  repository, under any circumstances, regardless of how permissive that
  would technically be for a non-commercial deployment. Bundling it would
  silently bind every future user of this codebase — including commercial
  ones — to a restriction the rest of the project does not carry.
- Installing BatDetect2 and fetching its assets is a deliberate operator
  action, performed knowingly, the same way `oo models fetch` works for
  BirdNET. The licence terms are surfaced before download.
- Anyone deploying this project's BatDetect2 adapter commercially must
  either obtain a separate licence from the authors or not enable it.

## Install procedure (Raspberry Pi 5)

BatDetect2 requires PyTorch, which is the dominant cost of the install.

```bash
# From the project virtualenv on the Pi (Python 3.12):
pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cpu
pip install batdetect2==1.3.1
```

Notes:

- **Pin `1.3.1`.** BatDetect2's PyPI releases include a `2.0.0b2`
  pre-release; that is a beta and is not what this evaluation targets.
- `torch`, `torchaudio` and `torchvision` all publish `manylinux` wheels for
  `aarch64`/`cp312`, so this is a plain `pip install` on the Pi — no
  cross-compilation or build-from-source needed. Installed size is
  approximately **1-1.5 GB**, almost all of it PyTorch and its CPU BLAS
  backend; budget for that against the Pi's 8 GB shared with the rest of the
  pipeline (capture, BirdNET, Postgres, the API, the UI).
- BatDetect2's own dependency list additionally pulls in `librosa`,
  `matplotlib`, `pandas` and `scikit-learn`. None of these are otherwise
  used by this project; they arrive because BatDetect2 uses `librosa` for
  its own (unused-by-us) audio loading and resampling path.
- The PyPI wheel does **not** include `example_data/` — that directory
  exists only in the GitHub source tree. To get the three labelled example
  recordings the fixture test and benchmark script use by default:

  ```bash
  git clone --depth 1 --branch v1.3.1 https://github.com/macaodha/batdetect2 /tmp/batdetect2-src
  mkdir -p models/batdetect2
  cp -r /tmp/batdetect2-src/example_data models/batdetect2/example_data
  ```

  `models/batdetect2/` is already covered by this repository's convention of
  not committing fetched model/asset directories (see `models/manifest.tsv`
  for the equivalent BirdNET mechanism, which this does not yet formalise
  for BatDetect2 — see "Known gaps" below).

## The 256 kHz input requirement

BatDetect2's model expects **256 kHz mono** audio (`TARGET_SAMPLERATE_HZ` in
its own `parameters.py`). This station's native capture rate is **384 kHz**,
so the 384 kHz → 256 kHz conversion is a **1.5x ratio**, not an integer
decimation the way 384 kHz → 48 kHz for the audible/ultrasonic detectors is
close to.

BatDetect2 ships its own resampling inside `batdetect2.api.load_audio`
(via `librosa`), but this project does not use it. Per ADR-017, resampling
for BatDetect2 goes through the project's existing soxr-backed path —
`open_observatory.audio.resample.AudibleResampler` — the same stateful,
frame-mapped resampler the audible 48 kHz stream is derived through. This
matters for two reasons this project already treats as load-bearing
elsewhere: consistent filter behaviour across every detector (one resampler
implementation, not two with different characteristics), and an exact
native-frame mapping so a BatDetect2 detection can still be traced back to
the authoritative native audio for evidence clips.

Concretely, both `scripts/benchmark_batdetect2.py` and
`tests/test_batdetect2.py` read raw audio, resample it themselves with
`AudibleResampler`, and call `batdetect2.api.process_audio(...)` directly —
never `process_file`, which would silently resample internally and bypass
this path.

## Running the benchmark

```bash
# Default: BatDetect2's own three labelled example recordings, if fetched
# per "Install procedure" above.
python scripts/benchmark_batdetect2.py

# Point at other audio (a single .wav or a directory of them):
python scripts/benchmark_batdetect2.py --audio /path/to/clips

# Control CPU threads (default: 2, matching BirdNET's default so the two
# can coexist on a 4-core Pi 5). Torch defaults to all cores if unset —
# not the condition this station actually runs under, so the benchmark
# makes the value explicit rather than inheriting the default silently.
python scripts/benchmark_batdetect2.py --threads 2

# Write the full results as JSON, so figures can be copied into the table
# below without manual transcription:
python scripts/benchmark_batdetect2.py --json results/batdetect2-pi5.json
```

The script:

- exits with a clear message (never a traceback) if `batdetect2`, `torch`,
  or the model weights are missing;
- reports process RSS before and after model load;
- reports model load time separately from inference time;
- runs one untimed warm-up inference, then `--runs` (default 20) timed
  inferences per clip, and reports **p50, p95 and max in milliseconds**;
- reports a **realtime factor** (clip duration ÷ inference time) at p50 and
  p95, which is the figure this project compares detectors on — BirdNET
  measures p95 77-109 ms at ~40x realtime, and the ultrasonic pass detector
  measures p95 54-104 ms at ~36-40x realtime;
- prints what it actually detected (species and score) on each clip,
  because a fast wrong answer is not a pass;
- prints a verdict line on whether real-time inference alongside the
  existing detectors looks sustainable, with the numbers that justify it.

## Running the fixture test

```bash
pytest tests/test_batdetect2.py -v
```

This skips (not fails) when `batdetect2` or the example recordings are
absent — the same discipline `tests/test_detectors.py` already applies to
BirdNET. When both are present, it asserts that BatDetect2's top detection
on a known recording matches the labelled species. A pass here, on the
target device, is what would let this project describe BatDetect2 as
"supported" rather than merely "evaluated" — and even then, only for the
capability the passing test actually exercised.

## Known gaps in this evaluation

- There is no `open_observatory.detectors.batdetect2` adapter yet, and this
  work does not add one. ADR-017 reserves that for once a measured benchmark
  shows real-time inference is viable; until then BatDetect2 sits behind the
  deferred-queue path described in `docs/detectors/DETECTOR_STRATEGY.md`, if
  it is adopted at all.
- BatDetect2 asset acquisition is not yet wired into `oo models fetch` /
  `models/manifest.tsv` the way BirdNET's is. This evaluation deliberately
  keeps its own fetch instructions self-contained (a `git clone` for the
  example data, `pip install` for the code and weights) rather than
  extending that CLI, since `cli.py` and `models.py` are outside this
  deliverable's scope.
- No measured figures exist yet — see the table below.

## Measured results

**Not yet measured.** Do not fill this in from expectations, precedent, or
extrapolation from other detectors — `CLAUDE.md` forbids claiming detector
support without a fixture test that has actually passed on the target
architecture, and a fabricated number here would be worse than a blank one.
Run `scripts/benchmark_batdetect2.py` on the Raspberry Pi 5 and paste the
real figures in.

| Metric | Value | Notes |
|---|---|---|
| Device | _(not yet run)_ | e.g. Raspberry Pi 5, 8 GB |
| BatDetect2 version | _(not yet run)_ | expect 1.3.1 |
| Torch version | _(not yet run)_ | |
| Threads used | _(not yet run)_ | |
| Model load time | _(not yet run)_ | seconds |
| RSS before model load | _(not yet run)_ | MB |
| RSS after model load | _(not yet run)_ | MB |
| Inference p50 | _(not yet run)_ | ms |
| Inference p95 | _(not yet run)_ | ms |
| Inference max | _(not yet run)_ | ms |
| Realtime factor (p50) | _(not yet run)_ | clip duration ÷ p50 inference time |
| Realtime factor (p95) | _(not yet run)_ | clip duration ÷ p95 inference time |
| MYOMYS example — detected species | _(not yet run)_ | expected: Myotis myotis |
| MYOMYS example — score | _(not yet run)_ | det_prob |
| EPTSER example — detected species | _(not yet run)_ | expected: Eptesicus serotinus |
| EPTSER example — score | _(not yet run)_ | det_prob |
| RHIFER example — detected species | _(not yet run)_ | expected: Rhinolophus ferrumequinum |
| RHIFER example — score | _(not yet run)_ | det_prob |
| `tests/test_batdetect2.py` result on target device | _(not yet run)_ | pass/skip/fail |
| Verdict (from the benchmark script) | _(not yet run)_ | sustainable / marginal / not sustainable |

Once this table is filled in from a real run, the fixture test result and
the verdict line together are what let ADR-017's open question — "is
real-time BatDetect2 inference viable on this hardware" — actually be
closed, one way or the other.
