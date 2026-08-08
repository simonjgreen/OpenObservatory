# Initial Architecture Decisions

## ADR-001: Single exclusive audio capture owner

**Decision:** Only `capture` opens the physical ALSA device.

**Reason:** Multiple detector processes opening one USB source is fragile, device/plugin dependent, and does not provide deterministic shared timing or retryable windows.

## ADR-002: Highest practical native capture rate with derived audible stream

**Decision:** Capture at the highest target-supported rate needed by ultrasonic analysis, then downsample for audible models.

**Reason:** Upsampling a 48 kHz stream cannot recover bat ultrasound. Downsampling a high-rate authoritative stream preserves both use cases.

**Caveat:** Actual AudioMoth/Linux mode and Pi stability must be measured. Audible-only fallback is required.

## ADR-003: Immutable time-addressed windows

**Decision:** Detectors consume immutable audio windows by reference.

**Reason:** Models have different window sizes, can run asynchronously, and may need retries without reading live audio directly.

## ADR-004: PostgreSQL canonical metadata, filesystem canonical clip bytes

**Decision:** Metadata belongs in PostgreSQL; large media belongs on SSD with checksummed database references.

## ADR-005: No scientifically authoritative composite biodiversity score in v1

**Decision:** Present transparent activity/diversity measurements individually.

**Reason:** A proprietary composite score would imply validation the project does not have.

## ADR-006: Separate model installation and licensing

**Decision:** Do not bundle third-party model binaries by default.

**Reason:** Code and model licences differ, notably for BirdNET model assets.

---

The following ADRs record deviations taken during the Milestone 0–3 debug slice on the
actual target device. See `GAP_REPORT.md` for the findings that motivated them.

## ADR-007: SQLAlchemy-mediated storage with SQLite in developer mode

**Decision:** Persist through SQLAlchemy 2 + Alembic against a DSN from configuration.
Developer/debug mode on the Pi defaults to SQLite at `data/openobservatory.sqlite`.
PostgreSQL 16 remains the production DSN and the only supported target for multi-process
deployment.

**Reason:** The target has no Docker and no PostgreSQL. Requiring a database server to
observe an audio pipeline would break the "repository runnable at every milestone" rule.
No raw SQL and no dialect-specific types are used, so the DSN swap is configuration-only.

**Constraint:** Any feature that requires concurrent writers, `LISTEN/NOTIFY`, or JSON
indexing must not be built on the SQLite profile. Retention and review workflows are
gated on the PostgreSQL profile. Query code must also stay dialect-portable: the history
aggregation layer (`history.py`) is the working example, and it is why bucket truncation
is written as `x - (x % n)` rather than with `FLOOR` or integer division — `/` on an
Integer column in SQLAlchemy 2 is *true* division and casts to NUMERIC.

**Status of Alembic (2026-08-05):** declared as a dependency but **not yet implemented**.
There is no `alembic/` migration environment in the repository; `create_all()` in
`db/session.py` is what actually builds the schema. That is adequate for the SQLite debug
profile and is *not* a migration path.

**Rollback:** setting `OO_DATABASE_DSN` to a PostgreSQL URL is the intended one-line
switch, but it cannot be exercised until the migration environment exists — writing it is
a prerequisite of the PostgreSQL profile, not a step within it.

## ADR-008: Native systemd deployment for the debug slice; Compose deferred

**Decision:** Run the debug slice as a single `systemd` unit inside a virtualenv on the Pi,
with the web UI built to static assets and served by the API process. The Compose topology
of the technical spec is retained as the production target and is not deleted.

**Reason:** Only the capture process may own the ALSA device, and getting `/dev/snd`,
USB hot-plug re-enumeration and real-time scheduling right through a container adds a
failure surface with no benefit while the microphone is still absent. Native execution also
gives honest CPU/latency measurements uncontended by container overhead.

**Constraint:** service boundaries stay explicit in code — capture, segmenter, detector
workers, normaliser and API communicate only through the `EventBus` and window references,
never by reaching into each other's state. The single process is a *deployment* choice.

## ADR-009: In-process event bus behind a transport-neutral protocol

**Decision:** Implement `EventBus` as an asyncio fan-out with bounded per-subscriber
queues and an explicit drop policy. Redis Streams becomes a second implementation of the
same protocol.

**Reason:** Explicitly permitted by `CLAUDE.md` for the first capture prototype. Bounded
queues and a recorded drop count keep the back-pressure behaviour that Redis Streams would
also have to provide, so the contract does not change when the transport does.

## ADR-010: An owned acoustic-activity detector is the first detector plugin

**Decision:** Ship `activity-v1`, a band-limited onset/energy segmentation detector with no
model dependency and no taxonomic output, as the reference implementation of
`DetectorPlugin`. BirdNET is an optional adapter that self-reports `unavailable` until its
model assets are installed by the operator.

**Reason:** ADR-006 forbids bundling BirdNET assets, so a build with no operator action must
still exercise the whole window → detector → normaliser → clip → event path. `activity-v1`
does that and is independently useful for diagnosing microphone and gain problems.

**Constraint:** `activity-v1` must never emit a species label, a scientific name, or a
canonical taxon id. Its `rank` is `null` and its group is `acoustic_event`.

## ADR-011: The debug UI is an observability surface, not the product dashboard

**Decision:** The real-time debug UI reads only from the API and the live WebSocket, shows
raw pipeline state alongside detections, and is allowed to expose internals (frame counts,
monotonic offsets, queue depth, drop counters, detector lag) that the Milestone 4 product
dashboard would hide.

**Reason:** It exists to prove and diagnose Milestones 1–3 on real hardware. Keeping it
separate stops diagnostic affordances leaking into the product surface and stops product
polish being mistaken for pipeline correctness.

**See also:** ADR-012 for the transport it reads from. **Partly superseded by ADR-016**,
which makes this UI the foundation of the Milestone 4 product dashboard rather than a
surface to be replaced by it. The separation of *concerns* below still holds; the
separation of *codebases* does not.

## ADR-016: The debug UI is the foundation of the product dashboard, not a throwaway

**Decision:** Milestone 4 promotes the existing UI rather than starting a second one.
Operator and diagnostic views become progressive disclosure within one application —
one component library, one design-token stylesheet, one transport client, one type set —
not two applications sharing a repository.

**Reason:** ADR-011 was written before the UI existed, when the risk was that debug
affordances would set the product's direction. The opposite happened: the surface that
got built is largely product-shaped. HISTORY mode already answers "what visited last
night" over persisted data, with named windows resolved in the station's timezone and
DST-aware, a stacked timeline over real aggregation SQL, species summaries with counts
and first/last-seen, focus-by-click, and capture coverage computed with correct interval
merging so an empty stretch is distinguishable from a quiet one. `Suggestions` is already
a species-grouped list a person can read. Discarding that to rebuild it would be waste,
and the second implementation would not be better — it would be untested.

**What is honestly *not* foundation, measured rather than assumed:**

- `styles.css` is 692 lines with 18 custom properties covering colour, radius and font
  stacks. There is no spacing or typographic scale, and the bulk is component-specific
  selectors with hardcoded pixel values and off-token hex colours. It is a colour-token
  header over ad-hoc CSS. A product surface inherits the dark, dense, monospace debug
  aesthetic unless it is restyled, and restyling is real work.
- `geometry.ts` is pure and well tested, but it is spectrogram-canvas mathematics. A
  timeline, review queue or retention chart shares its discipline, not its code.
- `types.ts` has a split personality: `Detection`, `MediaRef` and `Envelope` are near
  product shape; `StationStatus` is almost entirely pipeline internals.
- `audio.ts` and `live.ts` are genuinely surface-agnostic and reusable as they stand,
  though `AudioTelemetry` bakes debug counters into its public interface.
- The frontend has **no component testing library installed**. `vitest` is present but
  only pure functions can be tested, which is why `geometry.test.ts` is the only test.
  Everything with behaviour — mode switching, WebSocket wiring, history focus logic,
  jitter-buffer resync — is untested.
- There is no router and no URL-driven state, so a refresh loses the view.

Milestone 4's own exit gate in `IMPLEMENTATION_PLAN.md` asks that a user can "operate
**and diagnose** the station entirely through the local UI". That is one surface with two
depths, which is what ADR-011 forbade. The plan's gate wins.

**Constraint — what ADR-011 got right and is retained:** a diagnostic number must never
be mistaken for a product claim. Queue depths, drop counters, frame offsets, resampler
deficits and detector lag stay behind an explicit diagnostics disclosure. Product
surfaces must not present uncalibrated levels as measurements, model scores as
probabilities, or a frequency band as a species identification. Polish on the operator
surface is never evidence that the pipeline is correct; only the measurements are.

**Constraint — the debug affordances are not deleted.** They are how the pipeline was
proven and how the next regression will be found. Promotion means reorganising them
behind disclosure, not removing them.

**Prerequisite:** `web/src/App.tsx` currently holds around twenty-five `useState` hooks
covering live transport, history fetching, spectrogram controls, audio monitoring and
mode switching in one 425-line component. A third surface cannot be added to that
cleanly. State extraction is the first task of Milestone 4, not an optional tidy-up.

## ADR-012: Two live channels, and exactly one writer per WebSocket

**Decision:** The live surface is two separate WebSockets — `/api/v1/live` carrying JSON
frames and binary spectrogram columns for the visual pipeline, and `/api/v1/live/audio`
carrying raw PCM for the listen channel. On each socket, exactly one task performs every
send; producers `offer()` into that task's bounded queue and never touch the socket.
`DEBUG_UI_TRANSPORT.md` holds the frame formats.

**Reason:** This is a correctness requirement, not tidiness. Concurrent writers to one
WebSocket silently destroyed spectrogram delivery — the channel froze after roughly one
frame while JSON kept flowing. Splitting audio from the visual channel keeps a slow or
absent listener from stalling the spectrogram, and the single-writer rule makes interleaved
sends structurally impossible rather than merely unlikely.

**Constraint:** The bug is invisible on loopback, where sends complete too fast to overlap.
Any change to these channels must be measured from a real browser over the real network
link before it is believed. Both queues are bounded and drop rather than block: capture
always wins.

## ADR-013: `ultrasonic-pass-v1`, a second owned non-taxonomic detector on the native stream

**Decision:** Ship a pulse-train detector operating on the native 384 kHz stream, emitting
`bat pass` events with a measured frequency band, pulse count and SNR — never a species.
This brings native-rate window support forward from Milestone 5.

**Reason:** The ultrasonic band is the reason for capturing at 384 kHz; leaving it
uninspected until a third-party classifier existed would have left the most expensive
property of the pipeline unproven. A pass detector is fully owned, needs no model licence,
and is testable.

**Constraint:** It detects passes, not species, and the normaliser enforces that — a
non-taxonomic detector that emits a species name raises. Frequency band is evidence a human
can interpret, not an identification: 18–21 kHz is genuinely ambiguous between noctule and
bush-cricket. It has a known false-positive rate on broadband transients (wind, handling
noise) and no night scheduler, so it currently runs 24 hours a day. BatDetect2 remains
Milestone 5 and is not implemented.

## ADR-014: Ultrasonic evidence is rendered into the audible band for human review

**Decision:** Alongside the native evidence clip, ultrasonic detections get an audible
derivative — time-expansion or heterodyne, configurable — stored as a distinct media kind
(`audible_ultrasonic`) and marked as not amplitude-comparable to the native recording.

**Reason:** An ultrasonic clip is unfalsifiable by ear as recorded. Rendering it audible is
what makes the detector's false positives *checkable* by a human, which is the only review
mechanism that exists until a classifier does.

**Constraint:** The rendering is peak-normalised and high-pass filtered, so its levels carry
no information about the original amplitude and must never be presented as if they did.
Levels anywhere in this system are uncalibrated; no sound-pressure calibration procedure
exists.

## ADR-015: Anonymous read access, with authentication deferred to Milestone 4

**Decision:** The debug slice serves the API and UI with no authentication and anonymous
read enabled, on a trusted LAN. This knowingly contradicts `TECHNICAL_SPEC.md` §9, which
requires anonymous read access disabled by default in the first release.

**Reason:** The debug slice's purpose is measurement on real hardware, and an auth layer
would have added a failure surface to every diagnostic without making any measurement more
truthful. Milestone 4 owns the authentication foundation.

**Constraint:** This is a deviation with a real security consequence, so it is recorded
rather than assumed: the station must not be exposed beyond the local network until
Milestone 4 lands, and station coordinates are readable by anyone who can reach the port.
Milestone 4 cannot be called complete while this ADR stands.

## ADR-017: BatDetect2 is evaluated as an optional adapter, and its weights are never bundled

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
attaches to the operator's deliberate choice, not to a `git clone`. This is ADR-006
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
therefore adopted behind the deferred-queue path of `DETECTOR_STRATEGY.md` unless a
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

## ADR-018: Live ultrasonic monitoring is a second heterodyne implementation, not a reuse of the clip renderer, sharing one oscillator per station

**Decision:** `/api/v1/live/audio` gains a `?channel=ultrasonic` option: a live,
real-time heterodyne of the native stream, tuned by the listener and reconfigurable
without reconnecting. It is implemented in `audio/heterodyne_stream.py`, a new module —
not by calling `audio/ultrasound.py`'s `heterodyne()` per chunk. That function processes
a fixed array in one shot; called repeatedly on live chunks it would regenerate the
oscillator's phase and the low-pass filter's memory from nothing at every chunk boundary,
producing an audible click at the join every time. `heterodyne_stream.StreamingHeterodyne`
carries both continuously: phase as a wrapped running accumulation, the filter as
overlap-save with a retained tail. `ultrasound.py`'s constants, wording and "for
listening, not measurement" framing are reused throughout; its code is not, because its
code's whole shape assumes a bounded clip.

There is exactly **one** oscillator per station, shared by every ultrasonic listener —
retuning is a broadcast, not a per-connection state. This mirrors how the spectrogram and
`live_audio` (audible) channels already work: one produced stream, fanned out to however
many browsers are watching. A second concurrent listener wanting a different tuning is
not supported; this is a debug/operator surface for one person on a LAN (ADR-011,
ADR-015), and multi-tenant tuning would be new architecture for a need nobody has yet.

**Reason for the CPU gate:** heterodyning 384 kHz continuously has a real, measurable
cost, and `station.py`'s ordering already treats "capture always wins" as non-negotiable
(ADR-001, ADR-002). The live ultrasonic path is therefore gated exactly like
`LiveAudioBroadcaster.publish`: `_handle_block` only calls
`StreamingHeterodyne.process()` when `live_audio_ultrasonic.listener_count > 0`. Skipping
calls while idle and resuming later is safe because all continuity state lives inside the
`StreamingHeterodyne` instance, not in the call pattern — it simply continues from
wherever it left off.

**Deviation from plan, recorded rather than silently made:** the implementation brief for
this feature assumed the live tuning frequency should default from the existing
`ultrasonic_target_hz` setting. That setting is not a tuning frequency — it is where a
*rendered clip's* time-expansion should land in the audible band (default 4 kHz, an
audible-range value), unrelated to where a live oscillator should be tuned in the
ultrasonic band. Reusing it would have defaulted live monitoring to 4 kHz, well outside
`ultrasonic_band_hz` (15–125 kHz) and useless for listening to anything. A new setting,
`ultrasonic_live_tune_hz` (default 45 kHz, the common pipistrelle range), was added
instead. `ultrasonic_heterodyne_bandwidth_hz` genuinely is the right shared default for
bandwidth and is reused unchanged, exactly as briefed.

**Constraint:** Only an integer native-rate-to-48 kHz decimation ratio is supported
(384 kHz -> 48 kHz is exactly 1/8). A native rate that does not divide evenly leaves the
channel unavailable (`hello.available: false`, with a reason) rather than silently
approximating a fractional ratio — AudioMoth's one hardware profile on this project's
target is 384 kHz, so this is a defensive fallback, not an expected path.

## ADR-019: Live playback moved off Web Audio to a chunked-WAV stream, because Web Audio was silent on the operator's own laptop

**Decision:** Add `GET /api/v1/live/audio.wav`, streaming a 44-byte WAV header with
both size fields set to `0xFFFFFFFF` — the conventional placeholder for a stream of
unknown, effectively endless length — followed by continuous 16-bit little-endian
mono PCM at the broadcaster's sample rate. The debug UI's GO LIVE button now points a
plain `<audio>` element at this endpoint by default. `/api/v1/live/audio`, the
WebSocket channel from ADR-012, is unchanged and still serves other clients — a phone
uses it and had no problem to fix.

**Reason:** Diagnosed empirically, not guessed, in the operator's own browser.
Listening worked on an iPhone and was completely silent on the laptop (Chrome 150,
Ubuntu, hardware output 44.1 kHz). The transport was proven fine first: the server
reported one listener connected, and the client's own Web Audio telemetry reported
`out -18 dBFS`, `buffer 112 ms`, `under 0` — a graph that believed it was producing
audible sound. The Web Audio graph was correctly wired
(`gain -> limiter -> analyser -> context.destination`) and its `AudioContext`, built
at 48 kHz, reported `state: "running"`. None of that mattered: an oscillator routed
through `context.destination` was inaudible, and so was the same graph routed through
`createMediaStreamDestination()` into an `<audio srcObject>`. Web Audio produced no
audible output by any route on that machine. A generated WAV played through a plain
`<audio>` element *was* audible, as was YouTube in the same browser — media-element
playback works there; Web Audio does not. The chunked-WAV endpoint routes around
Web Audio entirely rather than debugging it further, because there was no remaining
node in that graph left to suspect.

**Consequences, recorded rather than smoothed over:**

- The page is served over plain HTTP to a LAN IP, so `window.isSecureContext` is
  false, `navigator.mediaDevices` is undefined, and `AudioWorklet` was already
  unavailable before today (see `DEBUG_UI_TRANSPORT.md`'s discussion of why the
  original WebSocket client scheduled `AudioBuffer`s on an explicit cursor instead of
  using a worklet). That pre-existing constraint shaped the original design and is
  worth restating here, because it is adjacent to but not the cause of today's bug —
  the worklet was never in use on either path.
- **All client-side audio telemetry (`out dBFS`, `buffer ms`, underruns) is gone.**
  It was measured *inside* Web Audio nodes, which no longer exist in this path, and
  was not ported across because there is nothing genuine to port: an
  `HTMLMediaElement` exposes no per-sample level and no explicit jitter buffer. It
  was replaced with what the element actually reports —
  `readyState`/`networkState`, seconds buffered ahead of the play cursor, and counts
  of `stalled`/`waiting` events — in `web/src/audio.ts`'s `AudioTelemetry`. Nothing
  is fabricated to fill the gap.
- **The +24 dB monitor make-up gain is also gone**, and has no replacement yet. A
  calm garden sits near −45 dBFS, so that gain was doing real work, and the plain
  `<audio>` element has no gain stage of its own — only the browser's own volume
  control. If the WAV path proves too quiet in practice, the fix is server-side gain
  applied to the stream before it reaches the broadcaster, not a client-side node,
  because any Web Audio node reintroduces exactly the failure this ADR routes
  around.
- The response carries `Cache-Control: no-store` and `X-Live-Sample-Rate`, plus
  `X-Live-Tune-Hz`/`X-Live-Bandwidth-Hz` on the ultrasonic channel, since there is no
  JSON hello frame over plain HTTP to carry that information instead. See
  `DEBUG_UI_TRANSPORT.md` for the full header and lifecycle description.

**The lesson worth generalising, because it will recur:** every meter the old client
displayed was measured *inside* the failing subsystem, upstream of where the signal
was actually lost, so it confidently reported health while producing silence. A
transport-layer check (listener count, one) and a decoder-layer check
(`out dBFS`) both passed while the final hop — Web Audio's connection to this
machine's actual output device — was silently broken. A meter that cannot observe
the failure is worse than no meter, because it actively suggests there is nothing
left to check.

## ADR-020: Detections from non-live sources are excluded from browsing views by default

**Decision:** Every endpoint that presents detections as observations —
`GET /api/v1/detections`, `GET /api/v1/detections/{id}`, `GET /api/v1/taxa/activity`,
and the timeline/species/unidentified sections of `GET /api/v1/history` — excludes
rows whose stream's `source_kind` is not `alsa` unless the caller passes
`include_synthetic=true`. Excluded list/aggregate responses report
`include_synthetic` and `excluded_synthetic_count` alongside the results, so an empty
result is distinguishable from a quiet night rather than looking identical to one.
`GET /api/v1/detections/{id}` on an excluded row returns `404` with an explanatory
detail (`include_synthetic=true` to retrieve it) rather than a silent `200`, because
the detail view is reached from a list that already excludes it by default, so the
honest answer to "why can't I find it" is the same 404 the list implied. `history`'s
`coverage` block is deliberately unaffected by this filter: it already separates
`seconds_from_microphone` from total coverage and is not a wildlife view, so
excluding synthetic rows from it would hide, not surface, the fact that the
microphone was absent.

Rows are still stored, not discarded — they are a true record of what the detector
did, and useful for testing — but they carry `source_kind` and a derived
`is_live_source` boolean so every consumer can make the same distinction without
re-deriving it. "Live" means `source_kind == "alsa"` specifically
(`history.LIVE_SOURCE_KIND`); "non-live" is everything else, which includes
`replay` as well as `synthetic` — a fixture WAV replayed for testing is exactly as
misleading in a browsing view as the synthetic tone generator is, and both are
excluded by the same predicate (`history.is_not_live`, which also treats a
`NULL`/missing `source_kind` as non-live rather than assuming it is genuine).

**Reason:** On 2026-08-08 the AudioMoth's mode switch was moved to `USB/OFF`, so it
stopped presenting an ALSA card. `OO_SOURCE=auto` correctly fell back to the
synthetic scene and correctly reported itself degraded in `/api/v1/health`, but
detectors kept running against synthetic audio and their detections were persisted
alongside genuine ones with no visible distinction. The live database gained 5 bird
detections attributed to *Grey-winged Inca-Finch* — a South American species with no
plausible presence at this station — plus 515 acoustic events, and both were
indistinguishable from real records in the history and species views. Deleting the
rows would have destroyed a true record of detector behaviour on synthetic input,
which is useful for exactly the kind of regression testing ADR-010's `activity-v1`
exists for; the fix is at the presentation layer, not the storage layer.

**Constraint:** Any new endpoint that lists or aggregates detections for a human to
read as observations must apply the same `is_live`/`is_not_live` predicate and
report `excluded_synthetic_count`. An endpoint that aggregates without this filter
and without the count is a regression of this ADR, not a stylistic choice.

## ADR-021: Evidence clips live on their own device, mounted over the clips directory

**Decision:** Evidence clips are stored on a dedicated USB SSD, mounted at
`data/clips` — the path they already occupied. The SQLite database stays on the SD
card. `clips_require_mount` makes the station report itself degraded, by name, when
that mount is absent, rather than quietly writing evidence to the system disk.

**Reason — the SD card could not sustain the write load.** A busy bat night writes
roughly 15 MB per pass across four clips: 15 GB in one night, against a 20 GB
budget already exceeded. Worse, it was competing with capture: ALSA reads go through
`asyncio.to_thread`, so clip writes and the capture read share the default thread
pool, and on **2026-08-05** that produced 11 gaps and 8 overruns in five minutes with
continuity down to 0.997. Note that moving evidence to the SSD on 2026-08-08 did
**not** eliminate overruns — see `docs/delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md`.
The device was a real constraint, but it was not the whole cause. Isolating evidence onto its own executor helped but could
not overcome the device.

**Why mounted over the existing path rather than relocated.** `media_asset.storage_uri`
holds **absolute** paths, across 17,273 assets. Moving the files anywhere else would
have orphaned every existing evidence link or required rewriting all of them.
Mounting the new device at the path the data already used made the migration a
no-op for the database: verified afterwards by fetching a clip recorded the previous
day through `/api/v1/media/{id}`, which returned a valid 384 kHz WAV.

**Why the database stays on the SD card.** It is small, and the SD card is the
system disk, which is always present. If the SSD is unplugged the station keeps
capturing, detecting, and serving history — it simply cannot write new evidence.
That confines the failure to the component that can best absorb it.

**Why the service does not depend on the mount.** A `RequiresMountsFor` dependency
would stop the station from starting at all if the SSD were missing, and capture
always wins. The station therefore starts regardless and reports the problem in
`/api/v1/health`, in the same spirit as the synthetic-source fallback: keep
recording, and say loudly what is wrong. `nofail` in `/etc/fstab` matches this.

**Constraint — the mount must exist before the service starts.** The unit runs in a
systemd mount namespace (`ProtectHome=read-only`, with `ReadWritePaths` covering
`data`), so a mount created on the host *after* the service starts is not visible
inside it. Mounting the SSD while the station is running requires a restart before
it takes effect, and the health check is what makes that state visible rather than
silent.

**Consequence:** the throttles imposed to protect the SD card were lifted — clips
per minute restored from 6 to 20, ultrasonic rendering restored from heterodyne-only
to both heterodyne and time expansion, and the budget raised from 20 GB to 300 GB
against 458 GB of storage. The full analysis view of every bat pass is available
again.

## ADR-022: Live ultrasonic retuning restored as a plain HTTP control call alongside the chunked-WAV stream

**Decision:** Add `POST /api/v1/live/tune?tune_hz=<value>`. It calls the same
`Station.set_ultrasonic_tune_hz` the WebSocket's `{"type": "tune", ...}` frame
already called, and returns the clamped value actually applied plus the kept
bandwidth and availability. The `audio.wav` stream itself is completely untouched
by this call — no reconnect, no new URL, no gap. `web/src/audio.ts`'s
`LiveAudioPlayer.setTuneHz` now POSTs here (throttled, see below) instead of
tearing the stream down and reopening it at a new `tune_hz` query parameter.

**Reason.** ADR-019 moved the debug UI's listen path from a WebSocket to chunked
WAV over plain HTTP to fix silent playback on the operator's laptop, and noted in
passing that retuning would have to mean reconnecting, "same as switching
channel" — a WAV response has no return channel of its own to carry a `tune`
frame the way the WebSocket did. That trade-off turned out to be worse in
practice than recorded at the time: the debug UI's frequency slider calls
`setTuneHz` on every `onChange` tick, which for a `<input type="range">` fires
continuously while dragging — often dozens of times a second during a sweep.
Each call tore down the `<audio>` element and reopened the stream at a new URL,
so sweeping the dial across the ultrasonic band repeatedly killed and restarted
playback, which is what the operator reported as "it now breaks the UI as you
move the slider." The fix is not a transport change — ADR-019's reasoning about
Web Audio being silent on this hardware is untouched and nothing here reintroduces
a Web Audio node — it is recognising that retuning never needed the stream's own
transport at all. The heterodyne oscillator lives on the server, station-wide
(ADR-018: one oscillator shared by every ultrasonic listener, last tuning request
wins for everyone), so any small side channel that can reach the station process
can retune it. A one-shot HTTP POST is the smallest one available over the
existing plain-HTTP surface.

**Why not target a specific listener/session.** ADR-018 already established that
there is exactly one heterodyne per station, not one per listener — retuning is
inherently a broadcast operation, not a per-connection one. `POST
/api/v1/live/tune` has no listener/session identifier for the same reason the
WebSocket's `tune` frame never needed one: the last request wins for every
connected ultrasonic listener, on whichever transport. This is a pre-existing
constraint restated, not a new one introduced here.

**Client-side throttling.** A range input's `onChange` fires far faster than a
human needs a new tuning frequency applied, and far faster than is polite to a
Raspberry Pi doing continuous capture. `LiveAudioPlayer.setTuneHz` now sends the
first tick immediately (so the dial feels responsive) and throttles subsequent
ticks to at most one in-flight request per 80 ms, trailing-edge — the last value
the slider settles on is always the one actually sent, even though intermediate
values during a fast sweep are coalesced and never reach the server. `stop()`
cancels a pending throttled send, so a tune request never lands after the
listener has torn down.

**What this does not restore.** The `+24 dB` monitor make-up gain lost in ADR-019
is still gone; this ADR is only about retuning. The WebSocket path
(`/api/v1/live/audio?channel=ultrasonic`) is unaffected and still supports its own
in-place `tune` frame for clients that use it (a phone).

**Consequence, recorded rather than smoothed over:** the debug UI now issues an
HTTP request per throttle window while the operator drags the slider, on top of
the continuous WAV stream already flowing. Each is a tiny, stateless GET-shaped
POST against a plain FastAPI route with no body to parse — negligible next to the
~96 kB/s the audio stream itself already costs — but it is still one more request
class hitting the station during a sweep, worth knowing about if a future
regression looks like periodic latency spikes correlated with slider use.


## ADR-023: The inside observer is an ESP32 wall display that polls HTTP and never shows a score

**Decision:** The "inside observer" — the ambient display that lives in the house and
shows what the garden station is hearing — is firmware for a **DIYmalls / Sunton
ESP32-2432S028R** ("Cheap Yellow Display"), written against PlatformIO with every
dependency pinned, at `firmware/inside-observer/`. It reads the station over
**HTTP polling** of the existing read-only REST API, not MQTT. It renders species
names and local times only: **no score, no percentage and no confidence figure of
any kind reaches the glass**, and bat passes are never given a species name.

### Why this device

The board was already in the house running DIYmalls' stock weather firmware, and the
operator asked for it to be repurposed. It is adequate and it is present, which beats
a better board that is not: 240x320 ILI9341 in portrait is enough for six large rows,
a title and a footer; a resistive touch panel is enough for a settings page with
finger-sized targets.

Its two real constraints shaped the implementation. There is **no PSRAM**, so a full
240x320x16bpp framebuffer (150 kB) is impossible and rendering goes through a single
reused 240x40 sprite (19 kB) instead. And the station's detection payload is large —
about 1.8 kB per row, mostly evidence-media checksums — so responses are stream-parsed
through ArduinoJson filters and only the six rendered fields are ever materialised.
Measured result: 48.8 kB static RAM, 962 kB of the 3 MB app partition, ~219 kB free
heap in steady state, flat across a five-minute soak.

The partition table is a byte-for-byte copy of the stock firmware's, read back from a
full-flash backup image. That is not tidiness: it keeps NVS at 0x9000, which is where
the ESP32 WiFi stack stored the credentials the operator provisioned under the stock
firmware. A normal upload does not erase NVS, so `WiFi.begin()` with no arguments
reconnects on first boot without anyone typing a password and **without this firmware
ever reading, copying or logging one**. Where that inheritance is absent, the device
raises its own open AP named `Aura` — the same name the stock firmware used — and
serves a captive portal. Credentials are never hardcoded, never committed, and never
requested out of band.

### Why HTTP polling rather than MQTT

The MQTT publisher specified in `API_AND_INTEGRATIONS.md` does not exist. No code in
this repository publishes to a broker. Building the display against a transport that
has not been written would have made the display's correctness untestable and its
delivery contingent on someone else's milestone.

The REST API, by contrast, is live, read-only, documented and already carries
everything the display needs — including the `include_synthetic` exclusion of ADR-020,
which matters here more than anywhere: a wall display is exactly the "browsing view"
that must not present a test scene as an observation.

Polling is therefore an interim transport, not a rejection of MQTT. `StationSource` is
an abstract seam with one implementation today; an `MqttStationSource` satisfies the
same interface without the UI changing. The broker settings (host, port, credentials,
topic prefix) are already in the settings model, already in the provisioning portal,
and already persisted, so the operator's configuration survives the firmware update
that switches the feed over.

One consequence worth recording: the display has no clock and no IANA timezone
database, and does not run NTP. It gets the station's true local offset — DST included
— from `range.start_utc` of the `today` window in `GET /api/v1/history`, which is the
station's own local midnight expressed in UTC. UTC internally, local for presentation,
with the station remaining the single authority on what local means.

### Why no numeric score is displayed

**A BirdNET score is not a calibrated probability.** `detector.calibrated` is `false`
and `calibrated_probability` is `null` on every row the station emits. Rendering 0.92
as "92%" would state a confidence that the identification is correct, which is not what
the number means and not something this system can currently claim. The normaliser
already refuses to let a non-taxonomic detector emit a species name; this is the same
rule carried to the presentation layer, where the misreading actually happens — nobody
misreads a JSON field, but everybody misreads a percentage on a wall.

So a single configurable threshold decides which named detections appear, and the
number itself does not reach the screen. On the device it is presented as a named step
— "Only the clearest", "Confident", "Balanced" (the 0.75 default), "Inclusive",
"Everything named" — with no figure attached. The raw value remains settable from the
provisioning portal, which is a page with room for the sentence that has to accompany
it: that this filters what gets named, that it is not a probability, and that it does
not apply to bats.

**Bat passes bypass the threshold entirely and are never named.** `ultrasonic-pass-v1`
detects passes, not species (ADR-013). Its score is a pulse-train confidence about
whether *something* passed, not about *what*, so filtering passes by it would hide real
observations on the strength of an irrelevant number, and naming them would invent an
identification the detector cannot make. A pass therefore renders as "Bat pass" with
its peak frequency — "36.2 kHz" — which is a measurement rather than a claim.

Two smaller honesty rules fall out of the same principle. BirdNET's non-taxonomic
classes (`Engine`, `Siren`, `Human vocal`) arrive from the station with `rank` of
`species` but with the scientific name equal to the common name; they are not species,
they are not shown as species, and they are not counted in "species today". And the
footer count is computed under the same threshold as the feed, so the number of species
claimed always agrees with what a person can actually see listed.

### Why a silent screen must not look like a broken one

The failure that motivates this is the same one behind ADR-020. When the AudioMoth's
mode switch was moved on 2026-08-05 the station fell back to a synthetic source,
correctly reported itself degraded, and kept detecting — and every browsing view that
existed at the time showed the results as observations. An ambient display is worse
than a debug UI here, because the whole point of it is to be glanced at rather than
read.

The display therefore renders three distinct states and never an ambiguous blank:
**offline** (red rule, "STATION UNREACHABLE", and any surviving feed labelled `(stale)`
in the footer), **degraded** (amber rule carrying the station's own reason, e.g.
"NO MICROPHONE - SYNTHETIC SOURCE"), and **empty but listening** ("Nothing yet", with
the reason underneath). All three are visible from across the room.

### Constraints this ADR imposes

* Any future transport for this display implements `StationSource`; the UI must not
  learn about a protocol.
* No rendering path may display a score, a probability, a percentage or the threshold
  value. The host test `test_no_feed_item_ever_carries_a_score` is the regression
  guard, and `FeedItem` deliberately has no field to carry one.
* A bat pass must never acquire a species name or a score in any view.
* Anything that lists detections as observations honours ADR-020's synthetic
  exclusion. The display requests `include_synthetic=false` explicitly rather than
  relying on the default.


## ADR-024: Capture coverage is bounded by delivered frames, not by a stream row's own claim

**Decision:** `history.coverage()` no longer trusts `audio_stream.start_utc`/`end_utc`
as the truth of how long a stream captured for. Each row's contribution is first
capped to the earlier of two independently-derived bounds — `frame_count` converted
to seconds at the stream's own `sample_rate`, laid down from `start_utc` (audio is
known to begin exactly there — `StreamClock` anchors to the first block actually
read), and `last_frame_at_utc`, a heartbeat written every ~10 s while the stream is
open — before the existing interval-merge (ADR-era fix for the 1302% incident, see
`HANDOVER.md` §7) ever runs. A row whose frame-derived duration falls below 90% of
its claimed wall-clock span (and whose claim is at least 60 s, to avoid noise on
trivial rows) is additionally marked `suspect` in the API response rather than
averaged away silently. `audio_stream` gained a `last_frame_at_utc` column to make
the heartbeat possible, and `Station` now tracks `frame_count`/`discontinuity_count`
per-stream instead of reading them off the process-lifetime `CaptureCounters` (see
below).

**Reason.** On 2026-08-08 the live database's most recent `alsa` stream row claimed
`start_utc` 2026-08-07 03:38:54 and `end_utc` 2026-08-08 11:36:36 — a 32 hour span,
closed with `AlsaCaptureError: ALSA read failed: File descriptor in bad state`. Its
`frame_count` was 3,852,212,352, which at 384 kHz is 2.79 hours, not 32. Querying the
live database directly (read-only, over SSH) settled which of the two numbers was
the lie: all 245 `capture_gap` rows for that stream fall between 03:38:54 and
06:24:45 — the first ~2h46m of the claimed span — and there are none after that,
all the way to the claimed close 29 hours later. There are also zero detections on
any live stream between 2026-08-07 20:00 and 2026-08-08 12:00, and no other `alsa`
stream opened in between (this was the only row spanning that period — not a case of
counters carried over from an earlier reopen). The frame count is correct; the claimed
end time is not. The capture read loop stopped delivering blocks around 06:25 UTC —
almost certainly wedged on the same file descriptor that eventually surfaced the
`AlsaCaptureError`, rather than erroring immediately — and nothing downstream
noticed for 29 hours, because nothing was watching for silence, only for an explicit
error. **This is a capture-side finding, not fixed here**: the read loop hanging
instead of failing fast is squarely `OPEN_INVESTIGATION_CAPTURE_GAPS.md`'s territory
(the capture-gap investigation), and is recorded there for reconciliation rather than
patched in this change. What is fixed here is that a row shaped like this one can no
longer inflate the coverage bar while the underlying hang is tracked down.

**Why frame count over wall clock.** `frame_count` is written by the capture hot
path counting bytes it has actually pulled off the device; `end_utc` is written by
whichever code path eventually notices the stream is over, which is only as
prompt as the failure that triggers it. A hang produces an honest frame count and
a dishonest end time. Deriving coverage from delivered frames makes that
asymmetry work in the operator's favour: the number can only be pulled down by
new evidence of a problem, never inflated by the absence of one.

**A second, related bug found while fixing this.** `CaptureCounters.frames` (and
`.discontinuities`) are process-lifetime — never reset when `_capture_supervisor`
reopens the device after a transient failure — but `Station._close_stream_row` was
writing them straight into `audio_stream.frame_count`/`discontinuity_count`, which
are per-*stream* columns. A process that reopens the device even once before a
stream closes would write that later stream's row with an earlier stream's frames
folded in. `Station` now tracks `_stream_frames`/`_stream_discontinuities`,
reset in `_on_stream_open`, and uses those for the row and for the live
`continuity_ratio` in `/api/v1/station` (which had the same mismatch, silently
absorbed by its own `min(1.0, …)` clamp). No evidence yet that this specific bug
produced the 32-hour row above — that stream was the only `alsa` row for its
process's lifetime — but it is the same shape of error and is now closed.

**Historical data.** The `oo history reconcile-streams` command scans already-closed
rows for this pattern and, in dry-run mode (the default), reports what it would
change without touching anything. `--apply` (plus a confirmation, or `--yes`)
corrects `end_utc` to the honest bound and records the original claim under
`detail.reconciliation` on the row, so the correction is auditable rather than a
silent rewrite of the operator's record. It intentionally never touches a row with
`end_utc IS NULL` — that might be the currently-running station's own stream, which
this offline command has no way to know about; closing those honestly is
`Station._close_orphaned_streams`'s job at that process's own next startup, which
now also prefers the `last_frame_at_utc` heartbeat over the coarser
detection/gap-timestamp heuristic it used before.

**Constraint.** Any future column added to `audio_stream` that a coverage or
continuity calculation depends on must be considered per-stream unless explicitly
documented otherwise — `CaptureCounters` exists for the genuinely
process-lifetime figures (CPU budget, open-failure count) and nothing else should
be read out of it into a stream row again.

**Not yet done, and out of scope for this change:** `PostgreSQL` has no Alembic
migration for `audio_stream.last_frame_at_utc` — no Alembic environment exists in
this repository at all yet (ADR-007 already flags this gap for the SQLite→Postgres
transition). `db.session.create_all()` now defensively `ALTER TABLE ADD COLUMN`s
any column missing from an existing SQLite file, which covers the developer and
on-device profiles this project currently runs, but a real migration is still owed
before the PostgreSQL profile is exercised for real.


## ADR-025: MQTT publisher and Home Assistant Discovery, off by default, on its own bus subscription

**Decision:** Milestone 6 adds `src/open_observatory/mqtt/` — `publisher.py` (the
runtime) and `discovery.py` (pure Home Assistant Discovery payload builders) — as a
consumer of the existing `EventBus` (ADR-009), the same seam the debug UI's
WebSocket already uses. `Settings.mqtt_enabled` defaults to `false`; an operator who
upgrades an existing station gets no new network traffic and no new failure mode
until they opt in. Alongside it, `schemas/detection-event.schema.json` moves from
`schema_version` `1.0` to `1.1`, fixing a gap recorded but not closed since
Milestone 3 (HANDOVER.md section 6.3 item 9): the schema had `additionalProperties:
false` and omitted `rank` and `taxonomic_group`, fields every internal detection
record actually carries. MQTT is what the handover note predicted would force this:
it is the first thing in this project that publishes the wire envelope to a
consumer outside this repository (Home Assistant), so a schema that quietly
disagreed with reality stops being an internal inconsistency and starts being a
contract break nobody would notice until an external validator rejected a message.

**Why `aiomqtt` and not raw `paho-mqtt`.** This codebase is asyncio throughout —
FastAPI, the capture loop, every worker in `station.py`. `aiomqtt` (pinned
`==2.3.0`) wraps paho's connection handling in `async with`/`async for`, matching
every other I/O boundary in the project. Raw paho runs its network loop on a
callback/thread model, which would mean either giving it its own thread (a second
thing, besides ALSA, needing deliberate isolation from the default executor) or
hand-rolling an asyncio bridge `aiomqtt` already provides. Version 3 of `aiomqtt`
adds built-in `reconnect=True`, but is not released to PyPI at the time of writing
(latest is `2.5.1`); this module implements manual reconnect with bounded
exponential backoff instead, the same pattern `aiomqtt`'s own migration guide shows
for v2.

**Why a bus subscription instead of a new dedicated queue.** `EventBus.subscribe`
already returns a bounded `asyncio.Queue` with drop-oldest-and-count semantics
(`events.py: Subscription.offer`) — this is precisely "a bounded queue with an
explicit drop policy" the brief asks for, already implemented and exercised by
every other bus consumer. `MqttPublisher` subscribes with
`maxsize=settings.mqtt_queue_depth` and reads the subscription's own `.dropped`
counter for its `dropped_total` metric rather than inventing a second bounded
queue and a second drop-counting mechanism that could disagree with the first.

**Why publishing can never block capture.** Nothing in `station.py`, the capture
loop, or any detector worker calls into `mqtt/`. The only coupling is one direction:
`EventBus.publish()` is synchronous and non-blocking (`events.py`), and offering an
event to a full subscriber queue drops the oldest queued item rather than waiting.
A broker that is down, slow, or rejects credentials therefore cannot propagate any
back-pressure to capture — there is no path for it to do so. All MQTT network I/O
happens through `aiomqtt`'s native asyncio driver, never through
`asyncio.to_thread`/`run_in_executor`, so it never contends with the ALSA blocking
read for the default thread pool the way a naive synchronous client would (see the
module docstring in `mqtt/publisher.py`, and ADR-021's own incident for what
sharing that pool with sustained I/O actually costs).

**Why ADR-020's synthetic-exclusion rule is re-implemented here rather than
inherited.** The bus event for `detection.created` carries the full detection
payload but not `source_kind` — that field lives on the `AudioStream` row and is
joined in at the API/DB layer, not carried on the in-process event. MQTT is exactly
the kind of "browsing/notification surface" ADR-020 was written for, so
`MqttPublisher._handle_detection` reads `capture_status_provider()["is_live_hardware"]`
(sourced from `station.status_snapshot()`, the same field `GET /api/v1/health`
already uses) and withholds publication for a synthetic or replay detection,
counting it in `suppressed_synthetic_total` rather than silently dropping it. The
row is still written to the database regardless, unaffected by this — only the
Home Assistant notification is withheld.

**Why the health sensor shares its logic with `GET /api/v1/health` instead of
recomputing it.** `api/app.py`'s `get_health` route body was extracted into
`_health_payload()`, called both by the route and passed as
`health_provider` to `MqttPublisher`. Two independently-maintained definitions of
"healthy" — one for the API, one for MQTT — would eventually disagree, most
plausibly exactly when it matters: the synthetic-source degradation case ADR-020
and the 2026-08-05 incident (see `docs/delivery/HANDOVER.md` section 3a) both turn
on. `binary_sensor.<station>_station_healthy` in Home Assistant means exactly what
`GET /api/v1/health`'s `status` field means, by construction, not by convention.

**Consequence — entity design.** See `docs/operations/HOME_ASSISTANT.md` for the
full entity table and setup instructions. In summary: one HA device per station,
`sensor.<slug>_last_detection` / `_species_today` / `_bat_passes_tonight`,
`binary_sensor.<slug>_bat_activity` / `_station_healthy`, and one `event` entity
(`event.<slug>_detection`) carrying coarse `event_types`
(`bird_detection`/`bat_pass_detection`/`other_detection`) with species and score as
attributes, so a per-species automation ("notify me when a tawny owl is heard") is
possible without HA discovery ever declaring one entity per species — deliberately
avoided, per the original design note this ADR's discovery code implements, because
the species set is open-world and cannot be a static discovery config, and a
permanent entity per species becomes unwieldy on a station with a busy bird list.
BirdNET's score is published as a plain `score` attribute, never
`device_class: probability` and never named "confidence" (CLAUDE.md's honesty
rule); a bat pass never carries a species field or a score at all, matching the
operator's decision that bat passes are always shown and never scored.

**What this explicitly does not implement.** Environmental telemetry ingestion, the
alert rule engine with repetition/cooldown, and HMAC webhooks remain unimplemented,
as scoped in `IMPLEMENTATION_PLAN.md`'s Milestone 6 description. `bat_passes_tonight`
resets at local midnight in the station's configured timezone, not at true civil
dawn — an approximation documented in `docs/operations/HOME_ASSISTANT.md` and in
`publisher.py`'s `_roll_day_counters_if_needed`, chosen because precise dawn
rollover would pull `schedule.py`'s solar geometry (and the station's optional
coordinates) into a module that otherwise has no dependency on them; the database
remains the authoritative record regardless of what the HA sensor shows.


## ADR-026: NVR-style tiered clip retention; detection metadata is kept forever

**Decision:** Evidence clip *bytes* age out in four tiers; detection *metadata* never
does. `retention.py` (`RetentionSweeper`) implements this, driven by the database
(kind, species, score, age), not a filesystem walk:

| Age | What survives |
|---|---|
| 0–7 days | native (full-rate) clip + audible rendering |
| 7–30 days | audible rendering only — native clip deleted |
| 30–90 days | only the first-ever and best-of-species clip survive |
| 90+ days | deleted, including the exemplars |
| always | oldest-first reclaim above `retention_watermark_ratio` (default 85%), ignoring tier and exemplar status |

Every threshold is a `Settings` field (`retention_native_days`,
`retention_audible_only_days`, `retention_exemplar_only_days`,
`retention_watermark_ratio`, `retention_batch_size`, `retention_batch_budget_s`),
defaulting to exactly the values above.

**Reason.** This is the operator's own framing: "I do not need recordings going
back forever — the logs of bird species etc over time is the most interesting data,
not the noise they made." `DATA_MODEL.md` already recorded detections as
indefinite by default; this ADR makes clip storage match that intent explicitly,
as a CCTV NVR ages out footage while keeping its incident log.

**Why the operator's own thresholds, unreviewed by us.** The tier boundaries and
the 85% watermark were specified directly, not derived here. They are defaults
precisely so they can be revisited: `clip_max_total_gb` (from ADR-021, now 300 GB
against 465.8 GB) already showed one GB-budget knob was a coarse enough proxy that
it needed lifting once; a ratio-based watermark tracks the disk's real remaining
headroom instead of a number someone will eventually have to update by hand.

**Detection rows are never deleted, by any tier, including the 90+ day one.**
`RetentionSweeper` never issues a `DELETE` against `detection`, `detector`, or any
other metadata table, and never mutates a `Detection` row's columns. It only ever
sets `media_asset.reclaimed_at` / `reclaim_reason` after successfully unlinking a
file — the association (`detection_media`), the species/score/timestamp, and the
capture provenance (frame bounds, stream id) all survive. `/api/v1/media/{id}`
already returned `410 Gone` for a row whose file is missing (written before this
ADR, for the ordinary case of a clip lost to disk trouble); a reclaimed asset now
reaches that same, already-tested path deliberately instead of by accident.

**Why age is measured from `detection.event_start_utc`, not `media_asset.created_at`.**
The two are milliseconds apart in practice, but the detection is the entity the
operator's policy is actually about ("first-of-species", "best-of-species"); using
its timestamp keeps every clip belonging to one detection in the same tier
together, rather than letting a slow-to-render ultrasonic derivative drift into a
different age bracket than its own detection's native clip.

**Defining "best-per-species".** "Species" is `canonical_taxon_id` when one
exists (species-rank birds), else `common_name`, else the taxonomic group itself.
That last fallback is deliberate: `ultrasonic-pass-v1` identifies passes, not
species (the honesty rule `normaliser.py` enforces in code, not just docs), so
every bat pass collapses into one group — there is no finer species key to exempt
by without inventing an identification the detector never made. In practice this
keeps exactly one first-ever and one best-ever bat-pass clip, not one per
(non-existent) bat species.

"Best" is highest `score` for anything with a real, cross-comparable score. Bats
are the deliberate exception: `ultrasonic-pass-v1`'s `score` is a composite
(`0.4*min(1, pulses/8) + 0.6*min(1, (peak_snr_db-12)/24)`, see
`detectors/ultrasonic.py`) invented to rank passes against each other, not a
calibrated quality measure comparable to BirdNET's. Ranking bats by it anyway
would be exactly the kind of unearned precision the honesty rules exist to
prevent, so bats are ranked by `peak_snr_db` from `native_result` instead — the
detector's own physical measurement — with `pulse_count` as a tiebreaker.

**Why this is a new module rather than extending `ClipManager.enforce_retention`
in place.** `clips.py` already had a retention sweep, a size budget and a disk
reserve before this ADR, and they are not removed: `ClipManager.admits()` and its
`min_free_bytes` write-time reserve are untouched (a different concern — refusing
to *write* a new clip when the disk is nearly full, evaluated on the capture
path), and `ClipManager.enforce_retention()` still exists and is still tested.
What changed is what `station.py`'s housekeeping loop calls: it now drives
`RetentionSweeper.sweep()` instead, because tiering requires information
`enforce_retention`'s plain filesystem walk never had — a clip's `kind`, its
detection's species and score — so a walk keyed on file mtime cannot express
"delete the native file but keep the audible one" or "keep this one clip out of
a thousand." A rewrite in place would have entangled a filesystem-only algorithm
with a database-driven one for no benefit; the old method is simply unused by the
running station now.

**I/O discipline (ADR-021's lesson applied again).** `sweep()` never walks the
clip directory tree. Every tier is one bounded `LIMIT`-ed SQL query plus, at most,
`retention_batch_size` file `unlink()` calls (default 200), and the whole call
bails out at `retention_batch_budget_s` (default 1.5s) of wall-clock time even if
the batch isn't exhausted — a large backlog drains across many housekeeping ticks
rather than stalling once. It runs in the same single-thread `_evidence_executor`
that evidence extraction already uses, never the default pool the ALSA read
shares (ADR-021's fix, still the load-bearing one). The one read that is not
batch-limited is the first-of/best-of-species computation, which is a plain read
over the `detection` table filtered to rows with at least one live media asset —
justified because detection metadata is, in the operator's own words, kilobytes
per day; it is a small, growing-slowly table, not clip storage. `find_orphans()`
(files on disk with no database row) *is* a tree walk, and is therefore
deliberately excluded from the automatic sweep entirely — it exists only for the
CLI's manual diagnostic use, and it never deletes anything.

**Dry run.** `RetentionSweeper.sweep(dry_run=True)` runs every query and every
decision unchanged, logs `retention.would_delete` instead of `retention.delete`,
and returns the same `RetentionReport` shape — but never calls `unlink()` and
never mutates an ORM object, so `session.commit()` at the end of a dry run is a
guaranteed no-op. `oo clips retention --dry-run` exposes this on the CLI. Given
that this code deletes irreversibly, the dry run is not a convenience feature; it
is treated as one of the module's primary contracts and is tested as such
(`tests/test_retention.py::test_dry_run_matches_real_run`).

**What this ADR does not do.** It does not delete
`data/clips.sdcard-backup` (~21 GB, pre-migration copy noted in
`OPEN_INVESTIGATION_CAPTURE_GAPS.md`) — that stays an explicit, operator-triggered
cleanup outside any automatic sweep, per instruction. It does not add an Alembic
migration for the two new `media_asset` columns
(`reclaimed_at`, `reclaim_reason`): this project has no Alembic migrations yet
anywhere (`db/session.create_all` is still how every profile gets its schema, per
its own docstring), so a fresh SQLite database picks the columns up automatically;
a live deployment with an existing database needs an explicit `ALTER TABLE` before
this code runs against it, which is a deploy-time concern for whoever performs
that deploy, not something decided here.
