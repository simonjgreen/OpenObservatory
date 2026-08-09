# BatDetect2 evaluation

Status: **evaluated on the target, and deliberately not adopted as a live
detector.**

The benchmark has been run: BatDetect2 measures **p95 968 ms per 0.5 s clip,
0.52× realtime, +459 MB RSS** on the Raspberry Pi 5, against 36–40× realtime for
the detectors that actually ship. `tests/test_batdetect2.py` passed on the target
on 2026-08-05 — but read what it asserts before treating that as a support claim:
it asserts the labelled species *appears among* the detections, not that it ranks
first, and the top-ranked detection matched the filename label on only one of the
three example clips. See "What these numbers close, and what they do not".

So: real-time inference on this hardware is **closed — not viable**. Accuracy is
**not closed**. There is no `open_observatory.detectors.batdetect2` adapter and
`oo models fetch` does not know about BatDetect2's assets. The only viable route,
per ADR-017, is the deferred/cascade path: `ultrasonic-pass-v1` decides *when*
something happened at 36–40× realtime, and the expensive classifier only ever
sees the few seconds already flagged. Measured on this station's own clips
trimmed to 1.5 s, that costs 2.1 s per pass — about 36 minutes of classifier work
for the 1015 passes of a whole night. `scripts/classify_clips_batdetect2.py`
implements that offline and writes nothing to the database; whether to promote it
into a live queued plugin is an undecided question, not a blocked one.

**This header previously said the fixture test "has not yet been run to a pass on
the Pi" and that this document held an "(currently empty) table". Both were false:
the table below has carried real 2026-08-05 figures for some time.** Corrected
2026-08-09.

See ADR-017 in `docs/architecture/ADRS.md` for the decision this evaluation is
answering.

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
- Accuracy is **not** evaluated. Three 0.5-second clips shipped for a library's
  own tests are not an accuracy benchmark, and this path resamples through the
  project's soxr stage rather than BatDetect2's own preprocessing, which is a
  genuine confound. See "What these numbers close, and what they do not".

## Measured results

Measured on the Raspberry Pi 5 on **2026-08-05**, by
`scripts/benchmark_batdetect2.py`.

> ⚠️ **Provenance is missing. Checked 2026-08-09.** Three documents cite
> `results/batdetect2-pi5.json` as the retained raw output of this run. **That
> file does not exist**: not in the working tree, not anywhere in git history
> (`git log --all --diff-filter=A -- results/` returns nothing), not on the live
> station (`~/open-observatory/results/` does not exist), and `results/` is not
> gitignored, so it was never committed rather than deliberately excluded. The
> benchmark script's `--json` flag writes it, so the run was probably done without
> that flag, or the file was written somewhere transient and lost.
>
> The figures below are **retained as recorded, not deleted** — they are internally
> consistent, they match the verdict ADR-017 was written against, and inventing a
> reason to distrust them would be as unfounded as inventing the numbers. But they
> are currently **unverifiable**: nobody can re-derive them from an artefact.
>
> `results/` is now gitignored (`.gitignore`), with a single deliberate exception
> for `results/batdetect2-pi5.json` — a small provenance record a milestone gate
> depends on is worth committing; ad-hoc benchmark output is not, and nothing else
> under `results/` will be tracked. **Nobody currently owns the live station** to
> re-run this — it needs BatDetect2 installed on the Pi (see "Install procedure"
> above) and exclusive access to a station that, at time of writing, another agent
> owns for an unrelated capture investigation. Whoever next has both, run exactly
> this from the project root on the Pi and commit what it writes:
>
> ```bash
> python scripts/benchmark_batdetect2.py --json results/batdetect2-pi5.json
> git add -f results/batdetect2-pi5.json   # -f: the directory is gitignored by default
> git commit -m "Add BatDetect2 Pi 5 benchmark provenance"
> ```
>
> That closes this gap permanently. Do not hand-write or otherwise fabricate this
> file — if the script cannot run, leave the gap open and say so.

**Do not extend this table from expectations, precedent, or extrapolation from
other detectors.** `CLAUDE.md` forbids claiming detector support without a fixture
test that has actually passed on the target architecture, and a fabricated number
here would be worse than a blank one. Re-run the script and paste real figures.

| Metric | Value | Notes |
|---|---|---|
| Device | Raspberry Pi 5, 8 GB, Ubuntu 24.04 aarch64 | kernel 6.8.0-1060-raspi |
| BatDetect2 version | 1.3.1 | model `Net2DFast`, 17 classes |
| Torch version | 2.13.0+cpu | aarch64/cp312 wheel, installed without incident |
| Threads used | 2 | matching BirdNET's allocation, not torch's all-core default |
| Model load time | 0.10 s | excluded from inference statistics |
| RSS before model load | 28.0 MB | |
| RSS after model load | 487.4 MB | +459 MB, against 8 GB shared with the whole pipeline |
| Inference p50 | 755 ms | 20 runs per clip, warm-up excluded |
| Inference p95 | 968 ms | |
| Inference max | 1009 ms | |
| Realtime factor (p50) | **0.66×** | slower than the audio it analyses |
| Realtime factor (p95) | **0.52×** | |
| MYOMYS example — detected species | *Pipistrellus pipistrellus* (0.780) | expected *Myotis myotis*; top Myotis call was *M. mystacinus* (0.744) |
| EPTSER example — detected species | *Pipistrellus pipistrellus* (0.777) | expected *Eptesicus serotinus*, present at 0.770 |
| RHIFER example — detected species | *Rhinolophus ferrumequinum* (0.759) | matches |
| `tests/test_batdetect2.py` result on target device | passes | asserts the labelled species is found, not that it ranks first — see below |
| Verdict (from the benchmark script) | **not sustainable** for real-time | p95 0.52× realtime, in isolation |

One figure in the table above could not be re-verified during the 2026-08-09
documentation pass and is left exactly as recorded: **Torch version `2.13.0+cpu`**.
BatDetect2 is not installed in the development environment, so it was not
re-checked; it is retained as measured rather than corrected or removed. Confirm
it from `results/batdetect2-pi5.json` or a fresh run before quoting it.

### What these numbers close, and what they do not

**Closed: real-time inference is not viable on this hardware.** At 0.52× realtime
*in isolation* — with no capture, no BirdNET and no ultrasonic detector competing —
BatDetect2 cannot keep up with the audio it is given, and the existing detectors run
at 36–40× realtime for comparison. This is not a tuning problem. Per ADR-017 the
deferred-queue path in `DETECTOR_STRATEGY.md` is the only viable route, and the night
scheduler is what bounds the queue.

**Not closed: accuracy.** The top-ranked detection matched the filename label for one
of three example recordings. That is a real observation and is recorded as such, but
it is *not* a fair verdict on the model, for three reasons:

1. This path resamples to 256 kHz through the project's own soxr stage rather than the
   library's internal preprocessing, per ADR-017. That is a genuine confound and would
   have to be eliminated before drawing any conclusion about accuracy.
2. The example clips are 0.5-second excerpts shipped for the library's own tests. They
   were not published as an accuracy benchmark.
3. In both mismatches the labelled species *was* detected, at a det_prob within 0.01 of
   the winner. The disagreement is about ranking, not about detection — except on the
   *Myotis myotis* clip, where the top Myotis call was identified as *M. mystacinus*,
   a different species in the same genus.

The fixture test therefore asserts that the labelled species appears among the
detections, not that it ranks first. Asserting top-1 would either fail permanently or
force the choice of a fixture that makes the test pass, and neither would be evidence.

ADR-017's question — "is real-time BatDetect2 inference viable on this hardware" —
is therefore **closed: no.** The question that replaces it is whether to promote
the offline cascade into a live queued plugin, and that one is open. See ADR-017's
2026-08-05 update and `DETECTOR_STRATEGY.md`'s "Deferred mode — as implemented".
