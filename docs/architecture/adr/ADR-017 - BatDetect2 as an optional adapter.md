---
aliases:
  - ADR-017
tags:
  - adr
---
# ADR-017: BatDetect2 is evaluated as an optional adapter, and its weights are never bundled
**Status:** active. The licence reasoning and the no-bundling rule hold, and BatDetect2 is
still "evaluated", never "supported" — though not for the reason given below, since the
fixture test passed on the target on 2026-08-05. Two *mechanisms* named below have been
overtaken: acquisition was never wired into `oo models fetch`, and
[[ADR-045 - Refinement runner|ADR-045]] deliberately declined `DeferredDetectorWorker` for
the cascade. See the review notes at the foot of this ADR.

**Decision:** BatDetect2 may be evaluated and adapted, but its model weights and example
recordings are **not committed to this repository**. They are acquired through the same
documented, attributable operator step as BirdNET (`oo models fetch`), and their licence
is surfaced in `/api/v1/models` and in the UI before download.

**Reason:** BatDetect2 is licensed **CC-BY-NC-4.0** — code, weights and example audio
alike, under a single repository licence. The authors state plainly that commercial use
is not permitted. That licence *does* permit non-commercial redistribution with
attribution, so bundling would be lawful for a non-commercial deployment — but it would
silently bind every future user of this repository to a non-commercial restriction that
the rest of the codebase does not carry. Keeping the weights out means the restriction
attaches to the operator's deliberate choice, not to a `git clone`. This is [[ADR-006 - Model install and licensing|ADR-006]]
applied to a model whose licence is more restrictive than BirdNET's, not a new principle.

**Consequence for the fixture gate:** BatDetect2 ships three labelled UK recordings
(*Myotis myotis*, *Eptesicus serotinus*, *Rhinolophus ferrumequinum*). They are a
legitimate basis for the Milestone 5 fixture test *once fetched*, but the test must skip
rather than fail when the assets are absent, exactly as the BirdNET tests do.

**Constraint — real-time inference is not assumed.** BatDetect2 requires PyTorch, expects
256 kHz mono input (not an integer ratio from this station's 384 kHz, so resampling goes
through the existing soxr stage rather than the library's internal path), and no primary
source establishes a CPU inference time on ARM. The nearest precedent, `acoupi_batdetect2`
on a Pi 4B, treats edge-CPU inference as a known bottleneck needing quantisation. It is
therefore adopted behind the deferred-queue path of [[DETECTOR_STRATEGY]] unless a
measured on-device benchmark shows otherwise. A benchmark, not an expectation, decides.

**Constraint — no claim without a passing fixture test on the target architecture**, per
`CLAUDE.md`. Until that test passes on the Pi, BatDetect2 is "evaluated", never
"supported".

**Update 2026-08-05, after measurement.** The operator confirmed no commercial use is
intended, so CC-BY-NC-4.0 is not a bar to using BatDetect2 on this station. The weights
still are **not** bundled: the licence would otherwise attach to everyone who clones this
repository, and that should remain a deliberate choice rather than a side effect of
`git clone`. `oo models fetch` is the seam.

**The cascade is the viable shape, and it is now measured.** Real-time inference is not
possible at 0.52x realtime, but the expensive classifier never needs to see the live
stream: `ultrasonic-pass-v1` runs at 36-40x realtime and decides *when* something
happened, and BatDetect2 only ever sees audio that has already been flagged. Measured on
stored clips from this station, trimmed to 1.5 s centred on the pass: **2.1 s of inference
per pass**. Against 1015 passes on the night of 2026-08-05, that is about 36 minutes of
classifier work for a whole night — roughly 20% of one core spread over the dark hours,
against four cores available. Classifying untrimmed 6 s clips costs four times as much for
no benefit, because an evidence clip is mostly pre-roll silence.

This is what `DeferredDetectorWorker` was built for, and it is the only route by which
BatDetect2 could become supported here.

**Reviewed 2026-08-29.** The core decision holds and the weights are still unbundled: no
BatDetect2 code, weights or example audio is in this repository, [[ADR-045 - Refinement runner|ADR-045]] declined to add
it even as an optional extra, and `BatDetect2Refiner.prepare()`
(`src/open_observatory/refinement/batdetect2.py`) raises `RefinerUnavailable` rather than
degrading quietly when the library is absent. The fixture gate behaves as specified —
`tests/test_batdetect2.py` skips via `pytest.importorskip` rather than failing — and the "no
claim without a passing fixture test" constraint is still honoured: there is no
`open_observatory.detectors.batdetect2` adapter, and `Settings.deferred_enabled` is `False`
precisely because BatDetect2 "remains evaluated, not adopted". Three corrections follow.

**`oo models fetch` is not the seam, and never became one.** The Decision above, and the
2026-08-05 update, state that the weights are acquired through the same operator step as
BirdNET and that their licence is surfaced in `/api/v1/models` and in the UI before
download. Neither is true of the implementation. `models/manifest.tsv` carries only the
three BirdNET assets, and `/api/v1/models` (`src/open_observatory/api/app.py:1518`) returns
`model_registry.licence_summary()` over exactly that manifest — so BatDetect2's
CC-BY-NC-4.0 terms are surfaced *nowhere* in the API or the UI. The real acquisition step is
`pip install batdetect2==1.3.1` plus CPU torch, which brings code and weights down together,
documented in [[BATDETECT2_EVALUATION]] — which records the divergence itself ("not yet wired
into `oo models fetch` / `models/manifest.tsv` the way BirdNET's is"). The *principle* is
intact: installation remains a deliberate, attributable operator act rather than a side
effect of `git clone`. The named mechanism was aspirational, and asserting it as fact
overstates a licence disclosure that does not exist. Either wire the manifest up or say
`pip` — do not leave this ADR claiming a UI disclosure the code does not make.

**`DeferredDetectorWorker` was not the route, and was not the only one.** The closing line
above is superseded by [[ADR-045 - Refinement runner|ADR-045]], which considered that mechanism for this exact cascade
and rejected it deliberately: it is an in-process queue of *live* windows whose central
safety property is dropping anything older than `max_delivery_latency_s`, so a clip written
six hours ago is precisely what it would correctly discard. The cascade instead ships as a
separate, CPU-fenced process (`oo refine run`, `open-observatory-refine.timer`) with its own
smaller contract, at `propose` authority only. `DeferredDetectorWorker` remains the right
mechanism for a *live* detector too slow to run inline — which is what `deferred_enabled`
still reserves it for — but it is not how BatDetect2 got here.

**The fixture test has already passed, and it is not what keeps BatDetect2 "evaluated".**
The constraint above — "until that test passes on the Pi" — reads as though that gate were
still shut. It is not: Milestone 5's exit gate was met on 2026-08-05 with `tests/test_batdetect2.py` passing on the Pi
([[MILESTONE_STATUS]]), and BatDetect2 is still not supported — because what the test
asserts is narrower than the constraint implies. It asserts that the labelled species
appears somewhere among the detections, not that it ranks first, and the top-ranked
detection matched the filename label on one of the three example clips, in both runs
([[BATDETECT2_EVALUATION]]). The 0.52x realtime figure above is likewise one of two
measurements: a re-run on 2026-08-25, same library and the same two threads, measured p95
0.96x, and the gap is unexplained. Neither number changes the verdict — both are slower
than the audio, against 36-40x for the detectors that run live — but 0.52x should not be
read as settled.

**Closed 2026-08-29, the licence disclosure:** the Decision's claim that the
weights arrive "through the same documented, attributable operator step as BirdNET
(`oo models fetch`)" was never true and is not made true now — the acquisition step
is `pip install batdetect2==1.3.1`. What has changed is the disclosure the claim was
really about: BatDetect2 is listed as a package-acquired model with its
CC-BY-NC-4.0 terms in `/api/v1/models`, in `oo models status`, and in the web UI at
diagnose depth (see [[ADR-006 - Model install and licensing|ADR-006]]) — **in the
repository. It is not on the station**: that work is uncommitted and undeployed, and
the running build still returns the three BirdNET assets alone. This paragraph said
"is now listed" without that distinction when it was written on 2026-08-29, which is
the same overstatement it exists to correct. The sentence above it stands as written.

**Reviewed 2026-08-29, second correction to the timings.** The 0.52× headline is
sound and is now the reproducible figure: the benchmark was run three times on the
target that day — unfenced, fenced to cores 2–3 at `nice 19` as the refiner really
runs, and with recording paused so no detector competed — and all three land at
0.38–0.51× p95, the unfenced run reproducing 2026-08-05 to within about 1%. The
faster 2026-08-25 artefact could not be reproduced under any condition, so the
correction added above ("one of two measurements") now has an answer: keep both
recorded, quote 0.52×, and do not average them. Detail and the negative results in
[[BATDETECT2_EVALUATION]].

**Reviewed 2026-08-30: the licence disclosure is in the working tree and not on
the station.** The note above says BatDetect2 "is now listed" with its
CC-BY-NC-4.0 terms in `/api/v1/models`, in `oo models status` and in the web UI.
That is true of this checkout — `PACKAGE_MODELS` in
`src/open_observatory/models.py`, the `kind`-aware `list_models` route, the
package table in `oo models status`, `web/src/components/ModelsPanel.tsx` — and
none of it is committed: three of those files are modified but unstaged and the
panel, its test and `tests/test_models.py` are untracked. `GET /api/v1/models`
on the live station, queried on 2026-08-30, returned the three BirdNET assets,
no BatDetect2 entry, and the pre-change `note` string. So the disclosure this
ADR now claims is not yet anywhere a person can read it. `deploy/deploy.sh`
rsyncs the working tree rather than a git ref, so a deploy would carry the
change as it stands; what is outstanding is the commit and the deploy, not the
code. The sentence above stands; this is the correction beneath it.

**Reviewed 2026-08-30: the artefact still quotes the withdrawn number.**
`results/batdetect2-pi5.json` is the 2026-08-25 run and is the only benchmark
JSON in `results/`. It carries `"realtime_factor_p95": 0.96` and
`"verdict": "NOT SUSTAINABLE"`, with nothing inside the file recording that it
is the run nothing could reproduce — a reader who opens the artefact instead of
[[BATDETECT2_EVALUATION]] gets the number this ADR withdrew. Its three
`matched_expected_species` flags do bear out the fixture reading above: one of
the three clips ranked the labelled species first (*Rhinolophus ferrumequinum*,
det_prob 0.759), and the other two both topped out as *Pipistrellus
pipistrellus* at 0.78 and 0.777 — including the *Eptesicus serotinus* clip,
where the correct species was present in the detections but not first.

---
Part of the [[ADRS|Architecture Decision Record index]].
