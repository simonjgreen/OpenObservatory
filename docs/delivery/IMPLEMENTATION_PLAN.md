# Implementation Plan

> **This is the plan, not the status.** It describes what each milestone was
> scoped to deliver and, in the later milestones, why it was sequenced that way.
> Several sections describe the code as it stood when the milestone was *written*
> — Milestone 4's "not foundation, despite appearances" list and Milestone 5's
> item 1 are both descriptions of problems that have since been fixed.
>
> **For what is actually delivered, read
> [`MILESTONE_STATUS.md`](MILESTONE_STATUS.md), which is the authority.** In
> summary as of 2026-08-09: Milestones 0–3 and 5 complete; Milestone 4 delivered,
> including the review workflow, which [[ADR-043]] closed; Milestone 4.5 nearly
> closed as of 2026-08-25 (species fixture and `oo audio window-dump` done; the
> 72-hour soak **passed** on continuity; drift gate (a) **passed**; only drift
> gate (b) is still open, having run and failed on linearity); Milestone 6's publisher live but its alert
> engine unbuilt; Milestone 7 not started; Milestone 8 has one of its six
> deliverables done ([[ADR-050]]'s display OTA, flashed and verified on hardware);
> Milestone 9 not started, by design.
>
> **The 72-hour soak ran 2026-08-10 to 2026-08-13 and failed** its continuity
> criterion (99.865% against ≥ 99.9%; see [[MILESTONE_STATUS]] §Milestone
> 4.5), and `CLAUDE.md` forbids describing the system as complete until a soak
> passes.

## Milestone 0 — Repository and target diagnostics

Deliver:

- Python/TypeScript monorepo scaffolding;
- CI, linting, typing and test commands;
- CLI `oo` skeleton;
- `oo audio probe` and `oo system report`;
- Docker Compose development baseline;
- target-device diagnostic report.

Exit gate: AudioMoth formats and stable device identity are recorded from the actual Pi.

## Milestone 1 — Deterministic capture and replay

Deliver:

- exclusive ALSA capture service;
- capture block contract;
- monotonic/UTC correlation;
- rolling memory buffer;
- WAV fixture replay source;
- gap/overrun detection;
- audio-level metrics;
- Prometheus health endpoint.

Exit gate: one-hour generated/replayed stream shows no timestamp drift or unexplained gaps.

## Milestone 2 — Derivation, windows and job transport

Deliver:

- 48 kHz resampling;
- source-frame mapping;
- window specification and segmenter;
- transient asset lease manager;
- Redis Streams job contract;
- bounded queue policies;
- window inspection CLI.

Exit gate: synthetic impulse appears in native and derived windows with documented timing error under 100 ms, target under 10 ms.

## Milestone 3 — Bird detector vertical slice

Deliver:

- BirdNET adapter installed through a documented model acquisition path;
- detector fixture self-test;
- normalised detection persistence;
- evidence clip manager;
- minimal FastAPI list/detail endpoints;
- simple server-rendered or temporary UI acceptable.

Exit gate: known bird fixture produces expected candidate label and an aligned playable clip.

## Milestone 4 — Product dashboard and review

Revised 2026-08-05 against what Milestone 3 actually built. Per [[ADR-016]], this milestone
**promotes the existing UI** rather than starting a second one.

Already delivered, and not to be rebuilt:

- the React application shell and its component set;
- timeline, filters and detail — HISTORY mode's named windows, stacked timeline,
  species summary, click-to-focus and capture-coverage bar, over real aggregation SQL;
- a species-grouped detection list (`Suggestions`) that reads as product already;
- spectrogram and playback — two orientations, live listening and per-detection clips,
  including audible renderings of ultrasound;
- surface-agnostic infrastructure: the reconnecting WebSocket client and the audio
  playback engine;
- the diagnostic half of the health/system page.

Not foundation, despite appearances — see [[ADR-016]] for the measurements:

- `styles.css` is a colour-token header over ad-hoc component CSS, with no spacing or
  type scale. A non-technical surface needs restyling, not just recomposition.
- the frontend has no component testing library, so everything with behaviour is
  untested and cannot currently be tested.

Deliver:

- a component testing library and tests for the behaviour being promoted, before
  promoting it;
- extraction of `App.tsx` state into hooks or a store — the prerequisite, not a tidy-up;
- URL-driven state, so a view survives a refresh and can be linked to;
- progressive disclosure separating the operator view from the diagnostic view;
- review workflow, end to end: the `review` table has no writer, no endpoint and no UI;
- derivation of a detection's current status from its latest valid review;
- retention job, plus a UI for the clip budget and what retention has removed;
- CSV/JSON export, which the acceptance criteria require and this plan had omitted;
- API token and authentication foundation, closing [[ADR-015]].

Exit gate: a user can operate **and** diagnose the station entirely through the local UI,
with anonymous read access disabled by default.

## Milestone 4.5 — Close the Milestone 1–3 exit gates

Split out because these are unfinished gates, not new scope, and two of them are bounded
by wall clock rather than effort.

Deliver:

- the 72-hour soak on the target device;
- a committed fixture test proving a known species from a known recording, which needs a
  reference recording whose own licence permits redistribution;
- the drift test at its full one-hour duration;
- `oo audio window-dump`, the window inspection CLI Milestone 2 asked for.

**Sequencing note.** `deploy.sh` restarts the systemd unit, which resets capture and
voids a soak in progress. A soak and a deployment are therefore mutually exclusive:
decide what build is to be frozen before starting one, and do UI work that needs no
deploy while it runs.

Exit gate: the acceptance criteria for capture continuity pass over 72 continuous hours,
and a detector fixture test passes in CI on the target architecture.

## Milestone 5 — Ultrasonic and bat support

Revised 2026-08-05. Parts of this milestone were brought forward into Milestone 3
because the AudioMoth captures at 384 kHz, which made the ultrasonic band real rather
than theoretical.

Already delivered:

- native high-rate window profile;
- native-rate evidence and audible playback rendering — time expansion and heterodyne,
  per [[ADR-014]], which is what makes a bat detection checkable by ear;
- `ultrasonic-pass-v1`, a pulse-train detector per [[ADR-013]]. It was never in this plan.
  It detects passes, claims no species, and does **not** discharge the BatDetect2
  deliverable below.

Deliver, in this order. Scheduling comes first deliberately: it removes the daytime
false positives that would otherwise be the noise against which buzz thresholds are
tuned.

**1. Ultrasonic detector configuration.** `station.py:424` constructs the detector as
`UltrasonicDetector(native_sample_rate=native_rate)` with no configuration wiring at
all, so `min_snr_db`, `min_pulses_per_pass`, the band and `pass_gap_s` cannot be set
from `runtime.env` — despite [[HANDOVER]] instructing a successor to tune exactly
those. Everything below needs this path to exist. Defaults must equal the current
constructor defaults exactly, so behaviour is unchanged until someone sets one.

**2. Night scheduler.** Gate the ultrasonic detector to civil dusk through civil dawn
plus a configurable margin either side, per [[TECHNICAL_SPEC]] §184.

- *Why it is not merely tidiness.* A detector that runs at two in the afternoon reports
  bat passes from wind, machinery and handling noise, and no threshold tuning can
  identify those as false, because a broadband transient genuinely resembles a pulse
  train. The clock carries information the signal does not. It also returns roughly half
  the detector's CPU — measured at p95 54–104 ms per window — which is the same budget
  BatDetect2 would have to fit inside.
- *Solar computation.* Sunrise/sunset from the station's latitude, longitude and date,
  with civil twilight at the standard −6° solar elevation. The `solar.py` approach from
  the earlier OutdoorAcousticEvents prototype is a reasonable starting point and avoids a
  dependency, but it must be tested against known dusk and dawn times for this latitude
  before it is trusted, including across a BST boundary.
- *Failure mode, chosen explicitly.* If coordinates are unset there is no schedule to
  compute. The detector then runs continuously and the UI says why. It must never
  silently detect nothing all night because a guessed schedule was wrong — a station
  that records nothing looks identical to a quiet night, and that is the exact confusion
  the coverage bar exists to prevent.
- *Configuration.* `ultrasonic_schedule` (`always` | `night`), plus dusk and dawn margin
  minutes. Default `always`, so upgrading changes nothing until it is set.
- *Observability.* The current schedule state, the computed dusk and dawn for tonight,
  and whether the detector is gated must be visible in the API and the UI, and the
  transition logged. A detector that is off must be visibly off, not absent.

**3. Deferred mode.** Specified at `DETECTOR_STRATEGY.md:32` for when real-time
inference is not sustainable: queue night windows to a bounded queue and process them
after capture, reporting lag honestly rather than dropping silently. The scheduler is
what makes this tractable, because it bounds what can enter the queue. Build it when
BatDetect2's benchmark shows whether it is needed, not before.

> **Outcome, 2026-08-09:** `DeferredDetectorWorker` was built and is **unused**, and
> [[ADR-045]] decided it is the *wrong* mechanism for the BatDetect2 cascade — its
> central safety property is dropping anything older than
> `max_delivery_latency_s`, which is exactly what a six-hour-old stored clip is.
> The cascade ships instead as a separate CPU-fenced process at propose-only
> authority. The deferred worker remains the right mechanism for a *live* detector
> too slow to run inline, and that case has not arisen.

**4. Feeding-buzz flagging.** Specified in
[[2026-08-05-bat-feeding-buzz-and-frequency-titles-design]].
The pulse timing is already computed and discarded; a buzz is a terminal collapse in
inter-pulse interval. Emits `min_interval_ms` on every pass so a wrong threshold can be
re-judged from stored data rather than from audio that no longer exists.

**5. Frequency-band candidate titles.** Peak frequency and a candidate name in the event
title. The candidate is presentational only: the stored record keeps `label = "bat
pass"` and no species name, and the normaliser's guard continues to hold.

**6. False-positive review**, using the buzz figures and the audible renderings as
evidence, against detections gathered under the night schedule. 18–21 kHz remains
genuinely ambiguous between noctule, serotine and bush-cricket, which is an insect; no
amount of tuning resolves that from frequency alone.
**7. BatDetect2 evaluation harness and Pi 5 benchmark**, then a bat adapter only if it
meets the acceptance threshold. The benchmark must be run under the night schedule, since
that is the profile it would actually run in, and measured alongside BirdNET rather than
alone.

Exit gate: a known bat fixture is processed, provenance retained, and capture continuity
unaffected under the operating profile. A species claim requires a classifier that
declares itself taxonomic; the pass detector cannot satisfy that clause and is not
intended to.

## Milestone 6 — MQTT, Home Assistant and alerts

Deliver:

- MQTT state/event publisher;
- Home Assistant discovery;
- environmental telemetry ingestion;
- alert rule engine with repetition and cooldown;
- HMAC webhooks.

Exit gate: Home Assistant shows station health and receives a test detection/alert.

## Milestone 7 — MCP, export and hardening

Deliver:

- read-only MCP tools;
- export bundles;
- backup/restore commands;
- setup wizard/commissioning report;
- privilege reduction and vulnerability scan;
- model licence screen;
- 72-hour soak test.

Exit gate: all v1 acceptance criteria pass.

## Milestone 8 — Distribution: somebody else's station

Everything to this point assumes the person running the station is the person
who built it. This milestone breaks that assumption. The test is a stranger
with a Pi, an AudioMoth and no terminal.

It comes *after* Milestone 7 deliberately: shipping an image to other people
makes every remaining rough edge someone else's problem, and an update
mechanism you cannot roll back is worse than no update mechanism at all.

**One deliverable landed early and that is not a violation of the sequencing.**
The display OTA ([[ADR-050]]) was justified on its own terms — every firmware change
was otherwise a physical trip and a USB cable, forever — and it carries the
rollback this milestone's own reasoning demands rather than deferring it. Nothing
else here should be started before Milestone 7, and in particular not the
published image.

Deliver:

- **a prebuilt Raspberry Pi image**, published as a GitHub release, that boots
  to a working station and a reachable web UI with no SSH;
- **first-boot provisioning over the network** — WiFi, hostname, location,
  timezone — with no keyboard or monitor attached;
- **no configuration step that requires a terminal or a text editor.** [[ADR-047]]
  made site parameters runtime state and [[ADR-048]] put them in the browser; this
  milestone makes that a *guarantee* rather than a coverage level;
- **remote update of the station after deployment**, versioned, staged and
  rolled back on failure. Capture must survive a failed update: the charter's
  first item does not pause for a release;
- ~~**over-the-air update of the counter-top display, triggered from the Pi**~~ —
  **DONE, 2026-08-09 ([[ADR-050]]).** Two OTA app slots, a digest verified before
  anything becomes bootable, and a rollback the display owns. Flashed and verified
  on the operator's own unit, including a deliberate rollback drill; the station
  reports it at firmware `0.2.4` and up to date. The drill is what found that
  `arduino-esp32` was disarming the rollback net before `setup()` ran, making the
  whole mechanism unreachable code — which is the argument for doing these on
  hardware rather than in a test suite;
- **backup and restore of a station's identity and history**, so replacing the
  hardware does not lose the record;
- a signed, checksummed release process for images and firmware, with model
  assets still fetched separately under their own licences.

Exit gate: a person who has never seen this repository takes a published image,
writes it to a card, and reaches a capturing station with a named location and a
working display, using only a browser — then receives and applies an update to
both the station and the display without physical access to either.

**Not in scope, and deliberately:** fleet management, a cloud account, remote
access that depends on a third-party relay, or telemetry leaving the station.
Local-first is not negotiated away for convenience of distribution.

## Milestone 9 — Nice to have, once the core is settled

Enhancements that are genuinely wanted and genuinely not urgent. Nothing here
blocks an exit gate, and nothing here should displace the 72-hour soak. The
point of the section is that "worth doing later" is a decision with a home,
rather than an idea that evaporates.

- **Taxonomic grouping above species ([[ADR-053]]).** Browse and aggregate by family
  or order — "show me the corvids" — instead of only by species. The detector
  offers no such layer: BirdNET's label file is 6,522 species binomials with no
  hierarchy, and our `taxonomic_group` field says bird/bat/acoustic-event, not
  rank. Genus is free (it is the first token of the binomial, 1,843 of them) but
  is *not* family: grouping the station's corvids by genus captures 1,044
  *Corvus* detections and silently drops the Magpie and the Jay. Family needs a
  checksummed, separately-licensed taxonomy acquired like the model assets.
  [[ADR-053]] records the reasoning, and refuses the hardcoded-species-list shortcut
  in writing.

- **Environmental sensors: lux and rain.** Both would make the acoustic record
  easier to interpret. A lux reading distinguishes a genuinely dark night from an
  overcast dusk, where the station currently has only an almanac calculation from
  its coordinates. A rain reading explains a whole class of confusing data: rain
  lifts the broadband noise floor, suppresses bird activity, and produces
  transients that `ultrasonic-pass-v1` can read as a pass.

  **How to attach them is the open question, and is the reason this is a note
  rather than a plan.** Three routes, none chosen: the Pi's GPIO, closest to the
  audio but adding wiring to an enclosure that currently has none and sitting
  indoors in the summer house; the ESP32 display, already networked and already
  in conversation with the station, but indoors and therefore blind to the
  weather; or ingestion from Home Assistant, which Milestone 6 already scopes as
  environmental telemetry ingestion and which would reduce this to subscribing to
  sensors that may already exist. The third is the cheapest and the least
  self-contained, which is the trade to think about — the charter's local-first
  constraint means the station must still work when Home Assistant is not there.

- **Watch the model assets for updates.** Dependabot covers pip, npm, Docker and
  GitHub Actions, but not the detector models. BirdNET and BatDetect2 are fetched
  by `oo models fetch` against the checksummed `models/manifest.tsv` ([[ADR-006]])
  rather than from a package index, so no Dependabot ecosystem understands them,
  and a new BirdNET release would go unnoticed indefinitely.

  The manifest already records a URL, a version and a SHA-256 for every asset,
  which is most of what a checker needs. A scheduled job could fetch the upstream
  release metadata, compare it against the manifest, and open an issue when they
  diverge. Deliberately an issue and not a pull request: a model change alters
  what the station reports rather than how it runs, so it needs the fixture test
  re-run on the target and a decision about the existing detections, which is not
  something to merge automatically.

  The same job could check that the recorded checksums still match what upstream
  serves, which would catch an asset being replaced in place.

Exit gate: none. Items graduate out of this section into a numbered milestone
when someone decides to do them.

## Explicitly deferred

- frog/insect production detectors;
- scientific biodiversity index;
- fleet management;
- mobile app;
- continuous raw audio archive;
- cloud-hosted service.
