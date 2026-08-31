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
**not closed**. There is no `open_observatory.detectors.batdetect2` *detector*
adapter and `oo models fetch` does not know about BatDetect2's assets. The only
viable route, per [[ADR-017 - BatDetect2 as an optional adapter|ADR-017]], is the cascade: `ultrasonic-pass-v1` decides *when*
something happened at 36–40× realtime, and the expensive classifier only ever
sees the few seconds already flagged. Measured on this station's own clips
trimmed to 1.5 s, that costs 2.1 s per pass — about 36 minutes of classifier work
for the 1015 passes of a whole night.

**Updated 2026-08-09: the cascade was promoted, and not in the way this document
originally anticipated.** [[ADR-045 - Refinement runner|ADR-045]] rules the deferred queue out explicitly — it
drops anything older than `max_delivery_latency_s`, which is precisely what a
six-hour-old stored clip is. The cascade ships instead as a separate CPU-fenced
process, `oo refine run` / `src/open_observatory/refinement/`, on a systemd timer
at 01:00 UTC, at **propose-only** authority: it writes append-only `refinement`
rows and never edits a detection's claim. `scripts/classify_clips_batdetect2.py`
still exists and still writes nothing to the database, but it is now the
experiment, not the implementation. See [[DETECTOR_STRATEGY]], "The refinement
runner", and [[ADR-045 - Refinement runner|ADR-045]].

**This header previously said the fixture test "has not yet been run to a pass on
the Pi" and that this document held an "(currently empty) table". Both were false:
the table below has carried real 2026-08-05 figures for some time.** Corrected
2026-08-09.

See [[ADR-017 - BatDetect2 as an optional adapter|ADR-017]] in [[ADRS]] for the decision this evaluation is
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

Consequences for this repository ([[ADR-006 - Model install and licensing|ADR-006]], [[ADR-017 - BatDetect2 as an optional adapter|ADR-017]]):

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
(via `librosa`), but this project does not use it. Per [[ADR-017 - BatDetect2 as an optional adapter|ADR-017]], resampling
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
BirdNET. When both are present, it asserts that the labelled species **appears
among** BatDetect2's detections (`expected_species in found`) — deliberately not
that it ranks first; see "What these numbers close, and what they do not" for
why top-1 would not be evidence. A pass here, on the
target device, is what would let this project describe BatDetect2 as
"supported" rather than merely "evaluated" — and even then, only for the
capability the passing test actually exercised.

## Known gaps in this evaluation

- There is no `open_observatory.detectors.batdetect2` *detector* adapter, and
  this work does not add one. [[ADR-017 - BatDetect2 as an optional adapter|ADR-017]] reserves that for once a measured
  benchmark shows real-time inference is viable. Since 2026-08-09 BatDetect2
  does ship as a **refiner** — `src/open_observatory/refinement/batdetect2.py`,
  [[ADR-045 - Refinement runner|ADR-045]] — which is a different thing in a different process, at propose-only
  authority, and does not make it a supported live detector.
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

  There is now one further piece of accuracy evidence, recorded because it is
  what set the refiner's authority: on this station's own 33–36 kHz cluster,
  BatDetect2 leaned *Myotis* on 6 of 8 clips at only 0.20–0.30, and returned
  **0.77 for *Pipistrellus pygmaeus* on a call measured at 34 kHz** — soprano
  pipistrelle peaks near 55 kHz. One confident contradiction on this station's
  own audio is why [[ADR-045 - Refinement runner|ADR-045]]'s runner may only propose. It is still not an
  accuracy benchmark; eight clips with no ground truth is not one.

## Measured results

Measured on the Raspberry Pi 5 on **2026-08-05**, by
`scripts/benchmark_batdetect2.py`.

> ✅ **Provenance closed, 2026-08-25.** `results/batdetect2-pi5.json` now exists
> and is committed. It was produced by running exactly the command this note used
> to ask for, on the Pi 5, and it is the raw output of that run rather than
> anything transcribed by hand.
>
> **The re-run does not reproduce the 2026-08-05 timings, and the gap is not
> explained.** Both sets are kept below. Same BatDetect2 1.3.1, same
> `Net2DFast`, same 2 threads, same 20 runs per clip, and — now verified from the
> artefact rather than trusted — the same **torch 2.13.0+cpu**, which was the one
> figure this document flagged as unverifiable. Kernel moved 6.8.0-1060 →
> 6.8.0-1061. The 2026-08-25 run is roughly **1.6–1.85× faster**.
>
> No cause is offered, because none was established. The obvious candidate is
> competing load during one run or the other — the station captures continuously
> in both cases, and "in isolation" below has always meant "no other *detector*",
> never "an idle machine". That is a hypothesis, not a finding.
>
> **Resolved 2026-08-29: 0.52× is the reproducible figure, and the 2026-08-25 run
> is the outlier.** The example corpus had been deleted from the Pi, so it was
> re-fetched from BatDetect2's own repository and the benchmark run three times on
> the target, same clips, same `--threads 2 --runs 20`, same torch 2.13.0+cpu,
> same kernel 6.8.0-1061-raspi, governor `ondemand` at 2.4 GHz and
> `vcgencmd get_throttled` = `0x0` throughout:
>
> | Run, 2026-08-29 | p50 | p95 | realtime p50 / p95 |
> |---|---|---|---|
> | station capturing and detecting, unfenced | 763.1 ms | 977.2 ms | 0.66× / 0.51× |
> | fenced to cores 2–3, `nice 19` — the refiner's own systemd conditions | 793.9 ms | 1320.5 ms | 0.63× / 0.38× |
> | recording paused, so no detector competed | 817.8 ms | 1060.9 ms | 0.61× / 0.47× |
>
> The first row reproduces 2026-08-05 (755 / 968 ms) to within about 1%. Nothing
> tried reproduces 2026-08-25.
>
> **Two hypotheses were tested and both failed.** Detector contention does not
> explain it: pausing recording under [[ADR-055 - Timed recording pause|ADR-055]]
> removed every competing detector and the run came out *slower*, not faster. Nor
> does the refiner's CPU fence flatter it — fencing is the worst of the three, as
> it should be. Run-to-run spread at this sample size (3 clips × 20 runs) is
> wider than the effect either hypothesis predicted, which is itself worth
> knowing before anyone designs a fourth run.
>
> So the honest reading inverts what the two-column table below implies: the newer
> run is not the better measurement, it is the one that has never happened again.
> Treat **0.52×** as the figure, keep 0.96× recorded, and do not quietly average
> them. The verdict was never in doubt at either value.
>
> The re-fetched clips live at `models/batdetect2/example_data/` on the station
> and are gitignored, as [[ADR-006 - Model install and licensing|ADR-006]] requires.
> A future re-run should record the corpus alongside the timings, since its absence
> is what made this question expensive to answer.
>
> **The verdict is unchanged and does not depend on which set is right.** At
> p95 the 2026-08-25 run measures a realtime factor of **0.96×** — still slower
> than the audio it analyses, in a run with no other detector competing, against
> 36–40× for the detectors that do run live. [[ADR-017 - BatDetect2 as an optional adapter|ADR-017]]'s deferred-queue cascade
> remains the only viable route on this hardware.

**Do not extend this table from expectations, precedent, or extrapolation from
other detectors.** `CLAUDE.md` forbids claiming detector support without a fixture
test that has actually passed on the target architecture, and a fabricated number
here would be worse than a blank one. Re-run the script and paste real figures.

| Metric | 2026-08-05 (as recorded) | **2026-08-25 (from the artefact)** |
|---|---|---|
| Device | Raspberry Pi 5, 8 GB, Ubuntu 24.04 aarch64 | same, kernel 6.8.0-1061-raspi |
| BatDetect2 / model | 1.3.1, `Net2DFast`, 17 classes | same |
| Torch version | 2.13.0+cpu *(was unverifiable)* | **2.13.0+cpu — verified** |
| Threads | 2 | 2 |
| Model load time | 0.10 s | **0.047 s** |
| RSS before → after load | 28.0 → 487.4 MB | **29.4 → 441.7 MB** (+412.3) |
| Inference p50 | 755 ms | **474.8 ms** |
| Inference p95 | 968 ms | **522.8 ms** |
| Inference max | 1009 ms | **528.1 ms** |
| Realtime factor (p50) | 0.66× | **1.05×** |
| Realtime factor (p95) | **0.52×** | **0.96×** |
| MYOMYS — top detection | *P. pipistrellus* (0.780), expected *M. myotis* | *P. pipistrellus* (0.777) — still no match |
| EPTSER — top detection | *P. pipistrellus* (0.777), expected *E. serotinus* at 0.770 | same pattern; *E. serotinus* present at 0.770 |
| RHIFER — top detection | *R. ferrumequinum* (0.759) — matches | *R. ferrumequinum* (0.759) — **matches** |
| Species matched | 1 of 3 | **1 of 3** |
| `tests/test_batdetect2.py` on target | passes | not re-run in this pass |
| Verdict (from the script) | **not sustainable** for real-time | **not sustainable** for real-time |

The accuracy result is **identical across three weeks and two runs**: one of
three example recordings ranked its labelled species first, and the same one.
That reproducibility is worth more than either set of timings — it means the
"not closed: accuracy" caveat below rests on a repeatable observation rather
than a single run.

<details><summary>The original single-column table, as it stood before the re-run</summary>

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

</details>

~~One figure in the table above could not be re-verified...~~ **Resolved
2026-08-25.** Torch `2.13.0+cpu` is confirmed from
`results/batdetect2-pi5.json`, which now exists. The figure was recorded
correctly.

### What these numbers close, and what they do not

**Closed: real-time inference is not viable on this hardware.** At 0.52× realtime
*in isolation* — with no capture, no BirdNET and no ultrasonic detector competing —
BatDetect2 cannot keep up with the audio it is given, and the existing detectors run
at 36–40× realtime for comparison. This is not a tuning problem. Per [[ADR-017 - BatDetect2 as an optional adapter|ADR-017]] the cascade
is the only viable route. **What bounds the work is not the ultrasonic night scheduler**
— that bounds live detection — but [[ADR-045 - Refinement runner|ADR-045]]'s UTC window
(`refinement_window_start_hour_utc` 1 to `refinement_window_end_hour_utc` 3), its item and
time budgets (`refinement_max_items` 1200, `refinement_max_seconds` 5400) and its systemd
CPU fence.

**Not closed: accuracy.** The top-ranked detection matched the filename label for one
of three example recordings. That is a real observation and is recorded as such, but
it is *not* a fair verdict on the model, for three reasons:

1. This path resamples to 256 kHz through the project's own soxr stage rather than the
   library's internal preprocessing, per [[ADR-017 - BatDetect2 as an optional adapter|ADR-017]]. That is a genuine confound and would
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

[[ADR-017 - BatDetect2 as an optional adapter|ADR-017]]'s question — "is real-time BatDetect2 inference viable on this hardware" —
is therefore **closed: no.** The question that replaced it, whether to promote the
offline cascade, is **also now closed** ([[ADR-045 - Refinement runner|ADR-045]], 2026-08-09): promoted, as a
separate CPU-fenced process at propose-only authority, not as a queued plugin. What
remains open is accuracy, and only a human ear closes that. See [[ADR-017 - BatDetect2 as an optional adapter|ADR-017]]'s
2026-08-05 update, [[ADR-045 - Refinement runner|ADR-045]], and [[DETECTOR_STRATEGY]]'s "The refinement runner".
