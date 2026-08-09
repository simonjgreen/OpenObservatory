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

**Status of Alembic (2026-08-08, superseded by ADR-035):** a real `alembic/` migration
environment now exists (`alembic/env.py`, `alembic/versions/`), wired to `Settings` and
`Base.metadata`. `create_all()` in `db/session.py` still runs at every application/CLI
startup and still builds a correct fresh schema, but new columns should go through an
Alembic revision from here on, not the `create_all()` + `ALTER TABLE`-patcher path. See
ADR-035 for the initial baseline, the stamp path for existing databases (the live
station's `openobservatory.sqlite` included), and what still has to change (`api/app.py`
and `cli.py` startup calling Alembic instead of `create_all()`) before the patcher can be
retired.

**Rollback:** setting `OO_DATABASE_DSN` to a PostgreSQL URL is the intended one-line
switch. The migration environment (ADR-035) is a prerequisite for exercising that switch
honestly and now exists; it has not yet been run against a real PostgreSQL 16 instance in
this repository, so "the DSN swap is configuration-only" remains unverified beyond SQLite
until that happens.

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


## ADR-027: A spacing/type scale for the web UI, applied to new surfaces rather than migrated wholesale

**Decision:** `web/src/styles.css` gains a token layer — an 8-step spacing scale
(`--space-1`..`--space-8`, 4px base) and an 8-step type scale (`--text-2xs`..`--text-2xl`,
anchored on the existing 13px body size) — alongside the pre-existing 18 colour/radius/font
custom properties. Every surface built for Milestone 4 (`OperatorSummary`, the diagnostics
toggle, `ExportLinks`, `RetentionPanel`, the review controls, the mobile breakpoint) is
built exclusively from these tokens. The ~700 lines of component CSS that predate this
work — spectrogram, suggestions list, event log, history chart, drawer — are **not**
migrated to the scale in this change.

**Reason:** ADRS.md's own honest assessment of Milestone 4's starting point was "a
colour-token header over ad-hoc component CSS, no spacing or type scale," and fixing that
is explicitly named as a foundation task, not a tidy-up. But a wholesale migration of 700
lines of working, visually-tuned CSS — much of it density-tuned for a spectrogram and
timeline that read correctly at specific pixel values — is a large, high-risk, low-reward
diff to run alongside behavioural changes (state extraction, disclosure, export). The
scale exists and is proven correct on real new surfaces; retrofitting it onto the old
surfaces is real, separately-reviewable follow-up work, not a rename.

**Constraint:** No new CSS added after this ADR may introduce a one-off pixel value for
spacing or font size where a token fits. `--radius`, `--radius-sm`, `--radius-lg` and the
existing colour tokens are unchanged and continue to be the source of truth for colour.

**Deliberately out of scope: a light theme.** The UI stays dark-by-default — it is an
ambient instrument left open next to a spectrogram at dusk (the module comment on
`styles.css` predates this ADR and is still accurate), and a second palette was not asked
for. `color-scheme: dark` is set on `:root` so the browser's own chrome (scrollbars, native
form controls) matches rather than mismatching light-on-dark, which is the concrete gap a
light theme would otherwise be closing.

## ADR-028: Operator/diagnostic disclosure implemented as one depth toggle, not a second route

**Decision:** ADR-016 promoted the debug UI to be the product dashboard's foundation
rather than a surface to be replaced, and named the gap: no progressive disclosure existed
between "everything" and "nothing." This is implemented as a single `useViewMode` hook
holding one value, `'operate' | 'diagnose'`, synced to `?view=` via
`history.replaceState`. `operate` (the default) shows `OperatorSummary`'s four
plain-language cards, the spectrogram with its channel/window/overlay controls, the
species list, and `RetentionPanel`. `diagnose` additionally reveals `Header`'s raw stat
row, the spectrogram's tuning controls (palette/floor/ceiling/orientation) and level
meters, and the `CapturePanel`/`DetectorPanel`/`StoragePanel`/`EventLog` columns that used
to be the whole page.

**Reason:** A second route (e.g. `/diagnostics`) would duplicate the live WebSocket
connection, the spectrogram canvases and their registered sinks, and the detection list —
exactly the "two applications sharing a repository" ADR-016 rejected. A single boolean held
above one component tree, gating what renders, keeps one connection, one set of canvases,
one detection list; only the JSX branches on depth.

**What "diagnostic" means here, concretely:** the header's continuity/gaps/block-age/
hot-path/link stats, spectrogram palette and black/white point tuning, the native/audible
level meters, and the four pipeline-internals panels. Everything else — the synthetic-audio
warning, the spectrogram itself, the species list, storage headroom, review — is
operator-facing in both depths, per ADR-016's "promotion, not replacement."

**Constraint carried forward from ADR-011:** a diagnostic number is never the sole backing
for a product claim. `OperatorSummary`'s cards are computed independently in
`state/operatorHealth.ts` from the same `StationStatus` fields the diagnostics panels
read — not derived *from* the diagnostics panels' rendered output — so hiding diagnostics
never hides the reasoning behind a card's tone.

**Verified:** manually, in a real Chrome tab (`claude-in-chrome`) against a local station
running `oo serve --source synthetic`, at both a desktop and a 390×844 mobile viewport;
`?view=diagnose` survives a reload. Not verified: a second human operator's read of the
copy, or the same test over a real Wi-Fi link to the Pi (see the report's "not verified"
section).

## ADR-029: Retention UI built against an assumed API shape; review workflow shipped minimally

**Decision, retention:** `RetentionPanel` calls `GET /api/v1/retention/status` against a
shape documented in the component's own header comment (tiers by age, bytes/clip counts
per tier, an `eligible_for_deletion` total, `disk_reclaim_threshold`, `dry_run`), matching
the tiering already decided for this session (0–7d native+audible, 7–30d audible-only,
30–90d first/best-per-species, 90d+ deleted; continuous oldest-first reclaim above 85%
disk). The endpoint does not exist yet — another agent owns the retention backend this
session — so the component fetches, and on any non-2xx or network failure degrades to "Not
available yet," rather than throwing or showing stale/fabricated numbers. No control to
trigger a run is exposed: the recorded decision was a `--dry-run` **CLI** flag, an
operational affordance, not a button that could be mis-clicked on an always-on station
display.

**Decision, review:** the `review` table existed with nothing writing to it. This adds the
minimal round trip — `POST /api/v1/detections/{id}/review` (body: `status` ∈
`{confirmed, rejected}`, optional `note`) and `GET .../review` for the latest state —
wired to two buttons in `DetectionDrawer`. Every call **inserts**, matching `orm.Review`'s
own docstring ("append-only; current status is derived from the latest valid review");
`supersedes_review_id` is set to the prior row when one exists. `corrected_taxon_id` is
always written `None` — correcting a misidentified taxon is a materially different feature
(it implies a re-training or re-labelling pipeline downstream) and is left for a future
ADR rather than half-built here.

**Reason both are minimal:** the brief ranked these below the design-system/state-
extraction/disclosure foundation work, and said so explicitly for retention ("do NOT build
the retention backend... leave a clean seam") and implicitly for review ("lower priority
... if you have capacity"). Both are real, tested, working code — not stubs — scoped
tightly to what a seam and a minimal workflow require.

**Confirm before relying on this:** the retention response shape above is this agent's
best-effort prediction from the recorded tiering decision, not a contract the other agent
agreed to. Whoever lands the retention backend should either match it or tell the UI's next
maintainer what actually shipped; `RetentionPanel`'s degrade-gracefully path means a shape
mismatch fails safe (shows "not available") rather than silently, but it will still need a
one-time reconciliation pass.


## ADR-030: The ALSA ring is sized for scheduling jitter, and the capture read owns its own thread

**Decision:** The kernel-side ALSA capture ring is sized from
`capture_buffer_ms` (default **500 ms**) rather than a fixed eight periods, and
`AlsaSource` performs its open, reads and close on a private single-thread
executor instead of `asyncio.to_thread`.

**Reason — the ring was shallower than one capture block.** `AlsaSource` requested
`periods=8` at a 10 ms period: a **80 ms** ring behind a **100 ms** block. Ring depth
is the only slack the capture path has — it is how much audio the kernel may
accumulate while nothing is reading it — so the station could not absorb a stall
shorter than a tenth of a second, and it read one block at a time with the loop
doing resampling, two spectrograms and window dispatch between reads. Measured on
the station on 2026-08-08: 24 `capture.gap` records in 45 minutes, of which 9 lost
real audio, every one of them between 40,467 and 90,910 frames — that is 0.11 s to
0.24 s, or **about one block each**, exactly the signature of a ring that overflowed
because nobody drained it in time.

**Why this costs nothing.** A deeper ring does not delay anything. A read still
returns as soon as `block_frames` exist, so detection latency, live-audio latency and
gap granularity are all unchanged. 500 ms at 384 kHz mono 16-bit is 384 kB of kernel
memory. The only thing that grows is the length of stall the station survives.

**Why the capture read gets its own thread.** `asyncio.to_thread` is the default
executor: eight workers on a 4-core Pi, shared with database inserts, health-event
writes, gap-row writes, device probes and every FastAPI `def` endpoint — and SQLite
is configured with `busy_timeout=5000`, so one contended write can hold a worker for
seconds. Evidence extraction and the retention sweep were given their own executor on
2026-08-05 for exactly this reason (ADR-021); this extends the same rule to the reader
itself. "Capture always wins" was already the queue policy; this makes it the thread
policy too, so the read cannot queue behind anything.

**Why not a free-running reader thread with its own internal queue.** That is the
textbook shape and it would decouple the read from the event loop completely, but it
adds a second buffering stage with its own drop policy in front of the ring buffer
that already exists, and duplicates the frame accounting that `_read_blocking` owns.
Deepening the kernel ring achieves the same tolerance with no new state. If a 500 ms
ring still proves insufficient, that is the next step, and it should be taken then —
with a measurement, not before one.

**Consequence:** `/api/v1/health` reports `alsa_buffer_frames`, and capture logs the
negotiated ring at open. A ring that ALSA clamps below one block is a warning
(`capture.buffer_shallower_than_block`) rather than a silent property, because that
condition is invisible from every other counter the station publishes.

## ADR-032: A near-zero occurrence prior suppresses a BirdNET candidate outright; a missing one gets the strictest bar, not the easiest

**Decision:** `BirdNetDetector._band_for` (`detectors/birdnet.py`) now takes two
arguments that were previously conflated into one: `occurrence` (what the range
model said for this species, or `None`) and `range_model_loaded` (whether a range
model was consulted at all). It adds a fourth band, `implausible`, with an
unreachable (`math.inf`) confidence bar, for any species at or below a new
`birdnet_plausibility_floor` setting (default `0.0005`) — no score admits a
candidate in that band, rather than the previous behaviour of merely raising its
bar to `threshold_out_of_range` (0.90) and letting an uncalibrated score clear it
anyway. Separately, a species the range model is *loaded but silent about*
(`occurrence is None` with `range_model_loaded=True`) is now sorted into a
`no_prior` band using `threshold_out_of_range`, the strictest bar available,
rather than `threshold_in_range` (0.55), the easiest — that case is distinct from
"no range model at all" (`range_model_loaded=False`), which still gets the
pre-existing `unfiltered`/`threshold_in_range` treatment, since there genuinely is
no plausibility information to act on in that case.

The banding logic itself moved to a module-level free function, `band_for`, so
both the live detector and a new historical-repair CLI command,
`oo detections reconcile-plausibility` (`cli.py`, logic in the new
`plausibility_repair.py`), apply exactly one definition of "implausible" rather
than two that could drift apart. The repair command is dry-run by default,
follows the exact shape of ADR-024's `oo history reconcile-streams`: it never
deletes a detection row or overwrites its `native_result`, only adds a
`native_result.plausibility_review` block recording the recomputed band,
occurrence and reason, and requires `--apply` plus a confirmation (or `--yes`) to
write anything.

**Reason.** Measured on the live station's own database, 2026-08-08, with the
location filter enabled and coordinates correct (the development station — the
range model itself works: Common Woodpigeon 0.995, European Goldfinch 0.781,
"Engine" 4e-06, so this was never a misconfiguration): a *Flammulated Owl*, a
North American species with no plausible presence in the UK, scored 0.959 at
`occurrence_probability` 8e-06 and was admitted under the old `out_of_range` band,
because BirdNET scores are not calibrated probabilities and nothing stopped an
uncalibrated 0.96 from outweighing a range-model verdict of "essentially
impossible here". A Eurasian Jackdaw at 0.617 and the Flammulated Owl at 0.959 are
not separable by any single score cutoff, so raising 0.90 to 0.97 would only move
the boundary, not fix the conflict — the fix has to act in prior-space, not
score-space, which is why `implausible` suppresses outright rather than raising
the bar further. Separately, 202 of 5833 named detections (3.5%) on the live
station had `occurrence=None` and were judged against the *easiest* available
threshold — a species the range model has nothing to say about is not the same
as a species the model actively endorses, and the pre-existing single-argument
`_band_for(occurrence)` had no way to tell "no range model" and "no prior for
this species" apart, since both produced `occurrence is None`.

**Floor derivation.** `birdnet_plausibility_floor` defaults to `0.0005`, derived
from measured data rather than picked arbitrarily: implausible North American
owls on the live station sit at occurrence 8e-06–1.6e-04, while a genuine,
seasonally-uncommon Tawny Owl sits at 0.019253. The default sits roughly two
orders of magnitude below the Tawny Owl and well above the owls, with margin on
both sides — `tests/test_detectors.py::TestBirdNetAdapter::test_tawny_owl_survives_the_floor`
and `::test_near_zero_prior_is_suppressed_outright_flammulated_owl` assert both
ends of that discriminating case by name, using the exact measured scores.

**Counters.** `_suppressed_out_of_range` previously incremented for every
candidate that fell below *any* of the three original bands' thresholds,
including `uncommon` ones — so it was never actually a count of suppressed
*out-of-range* species, despite the name, and was misleading anyone reading
`GET /api/v1/detectors`. It is now one of four counters, each scoped to exactly
its own band: `_suppressed_implausible_prior`, `_suppressed_no_prior`,
`_suppressed_uncommon`, `_suppressed_out_of_range`. They are surfaced in
`BirdNetDetector.health()`'s detail string and, split by `reason` label, as the
new `oo_birdnet_suppressed_total{plugin_id, reason}` Prometheus gauge in
`api/metrics.py`.

**Suppress at the detector, not hide at the presentation layer, for new
detections — and why that differs from ADR-020's precedent.** ADR-020 keeps
non-live rows in the database but excludes them from browsing views by default,
specifically because they are "a true record of detector behaviour" worth
keeping for regression testing (a synthetic tone generator's output is legitimate
fixture material). That reasoning does not transfer here: an `implausible`-band
candidate is not a structurally different *kind* of input the way synthetic audio
is — it is an ordinary live-audio candidate that failed a judgement this
detector already makes internally for every other band, the same way a
below-`min_confidence` candidate is silently never turned into a row today. Going
forward, `implausible` and `no_prior` candidates that fail their band's threshold
are suppressed by `BirdNetDetector.analyse` before a `NativeDetection` is ever
created — consistent with the detector's own existing behaviour, and with the
useful side effect that the API, the MQTT publisher and the ESP32 wall display
are automatically consistent with each other, since none of them has anything
new to filter.

**What this decision does not solve: the historical rows, and three consumers
this agent's territory excluded.** The ~5833 detections already in the live
database — including the ~202 `occurrence=None` rows and however many owls
cleared the old 0.90 bar — were written under the old logic and are not retroactively
suppressed by a code change; `oo detections reconcile-plausibility` finds and
flags them (dry-run by default) but does not delete or hide them, per this
project's rule against silently rewriting an operator's historical record.
Making the API, the MQTT publisher, or the ESP32 firmware actually check
`native_result.plausibility_review.implausible` and exclude a flagged row from
presentation is *not implemented in this change* — this agent's territory was
`detectors/birdnet.py`, the BirdNET section of `config.py`, the repair CLI in
`cli.py`, metrics, and docs, explicitly excluding the API, the MQTT publisher and
firmware (owned by a concurrent agent on `web/**` and by the ESP32 work
elsewhere). Until that follow-up lands, a flagged historical row — including
whatever North American owls remain unflagged until an operator runs the repair
command with `--apply` — is still visible everywhere it was before, just
auditable as reviewed in the database once flagged. Tracked in `HANDOVER.md`
section 6.3 item 0.

**What was verified and what was not.** The exact measured priors and scores
above were independently re-queried against the live station's own
`openobservatory.sqlite` (read-only, `mode=ro`) during this work, not merely
trusted from the handover document — Tawny Owl at occurrence 0.019253 with scores
up to 0.995, Flammulated Owl at 8e-06/1e-05 with scores up to 0.988, Eurasian
Jackdaw at 0.772293 with a low-score example at 0.617, Great Horned Owl at
occurrence 0.000159 (below the new floor) and other rows genuinely at
`occurrence=None`. This ADR's code changes were exercised only against unit and
integration tests using stubbed range models and a real local SQLite database —
**not deployed to the Pi**, per this session's hard rule against touching the live
station, and not run against the live database at all (read-only queries only).
Whether `oo detections reconcile-plausibility --apply` behaves correctly against
the live station's actual 5833-row database, under its actual BirdNET model
assets, has not been verified and must be checked — ideally with `--json` piped
to a file first, without `--apply` — before it is ever run there.

## ADR-033: Retention is paced, because a dedicated thread is not the same as out of the way

**Decision:** `RetentionSweeper.sweep()` runs every `retention_interval_s`
(default **300 s**), rounded to the nearest 10 s housekeeping tick, not on every
tick as ADR-026's implementation did. Nothing else about retention changes: the
sweep still runs in the evidence executor's dedicated thread, still bounds itself
by `retention_batch_size` and `retention_batch_budget_s`, and still resumes where
it left off. Two permanent instruments are added: an event-loop lag watchdog
(`loop.lag`) and a per-contributor cost breakdown of `status_snapshot()`
(`snapshot_phase_s`).

**Reason — measured on the live station, 2026-08-08.** After seven branches were
merged, `capture.gap` came back at ~1.6 records per minute against zero over the
preceding 73 minutes. The gaps were spaced at multiples of ~10.4 s, the
housekeeping period, so the tick was the suspect. Single-variable experiment,
same code, same settings, `OO_RETENTION_ENABLED` the only thing changed:

| Window | Retention cadence | `capture.gap` | `estimated_missing_frames` | `rate_offset_ppm` |
|---|---|---|---|---|
| 18:01–18:06Z (5 min) | every 10 s | **8** | 252,495 | +2,680 |
| 18:06–18:14Z (7 min) | disabled | **0** | **0** | **−51.75** |

The event-loop lag watchdog, deployed before the experiment, separates cause from
coincidence. With the sweep on a 10 s cadence the loop was starved for 55–150 ms
about five times a minute, on that same ~10.4 s beat. With the sweep disabled the
lag events fell to ~1.6/min on an unrelated 30 s beat and no gap was produced.

**Why a dedicated thread was not enough.** ADR-021 moved retention off the default
pool so it could not queue in front of the ALSA read, and ADR-030 gave the read its
own executor as well. Both were right, and neither addresses this: a ~0.30 s sweep
is SQLAlchemy ORM work in Python, so it holds the GIL, and CPython hands the GIL
back to a waiting I/O-bound thread reluctantly. The event loop is the I/O-bound
thread here, and it still has to *issue* each `run_in_executor` read and consume
its result. A read that is issued 130 ms late is a read that starts 130 ms late,
however private its thread. **The GIL is a shared resource that no executor
partitions**, and that is the general lesson: "give it its own thread" bounds
queueing delay, not scheduling delay.

**Why pacing rather than making the sweep cheaper.** Retention deletes clips whose
age crossed a boundary measured in *days*, plus a watermark reclaim that triggers
at 85% of a filesystem currently 6.3% full. Nothing about it is urgent at a
ten-second granularity; the 10 s cadence bought no property the operator can
observe. Five minutes is what the pre-merge code did (`ticks % 30`) and it restores
that behaviour behind a setting rather than a literal, so a station that genuinely
fills its disk fast can be tuned without a code change. The bounded-batch design
from ADR-026 is what makes a longer interval safe: a backlog still drains
incrementally, just on a slower beat.

**What this does not fix, and is deliberately left open.** The stall did not lose
any audio, and the station said it did. Over the affected window the capture path
was 21,300 frames (0.055 s) behind what elapsed time implied, while
`estimated_missing_frames` claimed 252,495 (0.657 s) and `overruns` was **0** —
ALSA never reported a ring overflow, because the 500 ms ring from ADR-030 absorbed
the stall exactly as intended. The deficit-step estimator in `_read_blocking`
credits a timing step of more than one block as lost audio immediately, which was
correct against an 80 ms ring where such a step really did mean an overflow, and
is not correct against a ring deep enough to recover. Every one of those phantom
frames is then added to `presented` in the observed-rate calculation, which is the
whole of the nonsense `rate_offset_ppm` of +2,680 against a true device offset near
−43 ppm: 252,495/92,505,600 is 2,729 ppm. **`capture.gap lost_audio=True` currently
means "the read was late", not "recording was lost".** Confirming a deficit step
against the following blocks before crediting it is the fix, and it belongs to
whoever next owns the estimator; it is recorded in
`OPEN_INVESTIGATION_CAPTURE_GAPS.md` with the numbers above.

**Consequence:** `/api/v1/station` and `/api/v1/health` report `loop_lag_max_s`,
`loop_lag_events`, `housekeeping_blocking_s` and `snapshot_phase_s`. A future
"capture is stalling" report should start there — it distinguishes a blocked event
loop from a blocked device in one reading, which nothing the station published
before could do. `retention.enabled` in the snapshot now reflects the station's
setting rather than always claiming `true`.


## ADR-035: A real Alembic migration environment, written before the PostgreSQL move

**Decision:** `alembic/` (`env.py`, `script.py.mako`, `versions/`) and
`alembic.ini` now exist at the repository root, wired to the project's own
`Settings.resolved_database_dsn` (the same `OO_DATABASE_DSN` resolution the
application uses) rather than a hardcoded DSN, and to `Base.metadata` from
`db/models.py` so `alembic revision --autogenerate` works. Batch mode
(`render_as_batch=True`) is on unconditionally in `env.py`, required for
SQLite (which cannot `ALTER`/`DROP` most columns in place) and a harmless
no-op wrapper on PostgreSQL, so one migration file is honest on both dialects
per ADR-007.

Revision `0001_initial` is the baseline: autogenerated from an empty
database against current `Base.metadata`, then verified by stamping a
`create_all()`-built database at `0001_initial` and running `alembic check`
— it reports no drift. That comparison, not code review, is what makes the
baseline honest. It includes every column added today under time pressure
via the `ALTER TABLE` patcher in `db/session.py`
(`media_asset.reclaimed_at`, `media_asset.reclaim_reason`,
`audio_stream.last_frame_at_utc`).

Revision `0002_media_asset_reclaimed_at_index` is not a demonstration
migration — it fixes a real gap found while building this environment:
`ALTER TABLE ... ADD COLUMN` (what the patcher uses) adds a column but never
an index, so the live station's `media_asset.reclaimed_at` column had no
index despite the model declaring one. Confirmed against a local read-only
copy of the live station's `openobservatory.sqlite`
(`ssh observer@station.example`, then `scp` the file — never opened for writing).
Written as `CREATE INDEX IF NOT EXISTS` / `DROP INDEX IF EXISTS` so it is a
safe no-op on a database that reached `0001_initial` through a normal
`upgrade` (which already creates the index) and a real fix on one that
reached it by adoption/stamping a patched SQLite file.

**Reason:** `HANDOVER.md` §6.3 item 10 asked for this before the PostgreSQL
move, not during it, and it bit twice in one day — two agents needed to add
columns to a live database with the only tool available being a defensive
`ALTER TABLE` patcher that cannot express a rename, a type change, a
backfill, or a downgrade, and silently does nothing on a schema it does not
recognise.

**Adoption path for an existing database (the live station's case):**
`alembic stamp 0001_initial` (never `upgrade` from scratch against a
database `create_all()` already built — that would try to `CREATE TABLE`
over live data), then `alembic upgrade head` to pick up anything after the
baseline (currently just `0002`, the missing index). Verified end-to-end
against a local copy of the live station's actual database: 48,067
`detection` rows and 19,205 `media_asset` rows, both counts unchanged after
stamp + upgrade, `alembic check` clean afterward. A database with no history
at all (a fresh developer checkout) instead runs `alembic upgrade head`
directly — `docs/data/DATA_MODEL.md` spells out which case an operator is
in and how to tell.

**The `_patch_sqlite_columns` patcher in `db/session.py` stays, deliberately,
for now.** Every application and CLI entry point still calls `create_all()`
directly on startup; none of them run a migration yet. Deleting the patcher
today, before that bootstrap path is changed to call (or require) Alembic,
would silently strand any SQLite database that reaches a future model change
through `git pull` + restart rather than an explicit `alembic upgrade head`
first — including the live station, whose two 2026-08-08 columns it added.
The correct fix is to change what bootstraps the schema, not to delete the
one thing currently keeping an un-migrated database working; that bootstrap
change is out of this agent's territory (it touches `api/app.py` and
`cli.py` startup, shared surface, and cannot be verified against the live
station this session under the no-deploy rule) and is recorded as follow-up
work below. From now on, a new column belongs in an Alembic revision, not a
new field the patcher happens to also cover — the patcher's docstring says
so directly.

**Constraint / what a follow-up must not skip:** `alembic upgrade head`
must run (or `alembic stamp` for a first adoption) before any deploy that
changes `models.py` lands on the station, and before `api/app.py` /
`cli.py` are changed to call Alembic instead of `create_all()` on startup —
that ordering change, and updating `deploy/deploy.sh` to run migrations as
an explicit step, is unimplemented and is the concrete next piece of this
work, not a redesign this ADR performs.

**What a follow-up migration for the concurrent authentication work needs:**
this ADR's baseline was built against `db/models.py` as it stood on `main`
at merge time today, before the authentication agent's tables/columns
existed in this branch. It intentionally does not guess that schema. A
follow-up revision — `alembic revision --autogenerate -m "..."` once the
auth models land — will need to be generated fresh against a `create_all()`
database that includes them, then verified the same way `0001_initial` was:
stamp a `create_all()` build at the new revision and confirm `alembic check`
reports no drift.

**Rollback:** `alembic downgrade base` on a throwaway/test database round-trips
cleanly (verified: `tests/test_migrations.py::test_upgrade_downgrade_roundtrip`).
There is deliberately no documented `downgrade` path for a real deployed
database — `0001_initial.downgrade()` drops every table, which is correct
for a scratch database and destructive for a real one. The rollback for a
bad deploy against a real database is what `DEPLOYMENT_AND_OPERATIONS.md`
already says for the pre-Alembic era: check out the previous commit, restore
`data/` from a backup taken before the migration ran, and restart. Backups
remain the operator's own responsibility (unchanged gap, tracked separately)
— Alembic gives reversibility for schema-only mistakes on a database nobody
minds losing, not a substitute for backing up `data/` before a real upgrade.


## ADR-034: An authentication foundation closes ADR-015, deliberately off by default, with one exemption for a display that cannot be reflashed

**Decision:** Local operator accounts (Argon2id-hashed passwords), `HttpOnly`
session cookies for the browser UI, and revocable, hashed, long-lived API
tokens for machine clients. A single `auth_enabled` setting (default **off**)
gates enforcement; a configurable `auth_public_read_paths` allow-list
(default: `/api/v1/detections`, GET only) plus two hardcoded always-public
paths (`/api/v1/health`, and `/metrics` — which never matches the gate's
`/api/v1/*` prefix at all) stay reachable with no credential regardless of
that setting. Implemented in `src/open_observatory/auth.py` (the service:
hashing, tokens, sessions, rate limiting), `db/models.py` (`User`,
`AuthSession`, `ApiToken`), the auth section of `config.py`, and wired
through `api/app.py` as a blanket Starlette middleware plus
`/api/v1/auth/{login,logout,me,password,tokens}`. The web UI gets a login
view, a forced-password-change view for the bootstrap account, and honest
401 handling (`web/src/api.ts`, `hooks/useAuth.ts`, `state/authState.ts`,
`components/Login.tsx`).

**Reason this is a foundation, not an IAM system.** This is a single-operator
LAN appliance (CLAUDE.md forbids requiring cloud connectivity for core
capture/detection/review/query), not a multi-tenant product. There are no
roles, no groups, no org model, no password reset flow (an operator who loses
their password regains access by clearing the `user` table or disabling
`auth_enabled` and creating a fresh one — undignified, but honest about what
a one-account local appliance needs). Session tokens and API tokens are
stored only as SHA-256 hashes (fast, deliberately not Argon2 — the token
already carries ~256 bits of entropy from `secrets.token_urlsafe`, and this
hash runs on every authenticated request, where an Argon2id cost would be a
real per-request tax); passwords are Argon2id (`argon2-cffi==23.1.0`, cost
parameters pinned in `config.py` rather than left at the library's shipped
default, exactly like every other dependency in this project) and are never
logged — `AuthService.authenticate`'s failure path is covered by a test that
asserts the plaintext never reaches a log call.

**Reason for defaulting `auth_enabled` to off.** CLAUDE.md: "Add structured
logs, metrics, health checks and graceful degradation with every service" —
and the single worst outcome this feature could produce is an operator
running `git pull && systemctl restart` and finding their own station
unreachable, with no way back in short of SSH and a database edit, because a
new default silently started requiring a login nobody set up. Off-by-default
means an upgrade changes nothing until the operator deliberately opts in.
The gap this leaves is real and is not hidden: `/api/v1/health` never flags
`auth_enabled: false` as a `problems` entry (that would make a freshly
upgraded station report itself "degraded" out of the box, for doing exactly
what it has always done), but a structured warning
(`auth.disabled`) is logged once at every startup, and `GET
/api/v1/health`'s `auth` object always reports `{"enabled": false}` plainly
rather than omitting the key. What **is** a `problems` entry, because it is a
genuine lockout rather than an unconfigured default: `auth_enabled: true`
with zero active user accounts (`active_users: 0`) — a state that should be
unreachable in normal operation (bootstrap always creates one) but is
surfaced loudly if an operator's own account-management ever produces it,
per CLAUDE.md's "graceful degradation ... with every service."

**Reason for the ESP32 exemption, stated as a trade-off rather than papered
over.** `firmware/inside-observer` polls `GET /api/v1/detections` and
`GET /api/v1/health` every `pollSeconds` (default 20s) with no way to carry a
credential — it is a 4 MB-flash ESP32 with no PSRAM, its HTTP client sends a
bare GET, and this agent's territory explicitly excludes `firmware/`, so
adding bearer-token support there is not a same-session option; doing it
anyway would need a physical USB reflash of a device mounted on the
operator's wall. Two paths were available: leave those two endpoints
reachable with no credential even when `auth_enabled` is true (chosen), or
require a reflash before `auth_enabled` could ever be turned on for a station
running this display. The second option effectively vetoes closing ADR-015
at all for the one station this session has real telemetry from, which is
worse than a scoped, documented exemption. **What the exemption actually
costs:** with `auth_enabled: true`, anything on the LAN can still read the
station's recent detections (species, timestamps, scores) and coarse health
without a credential — not station coordinates or clip audio (`/api/v1/media`
and the export/history/token/review endpoints are all gated normally), but a
real reduction from "everything requires a login." `/api/v1/health` is
additionally hardcoded (not merely defaulted) into the always-public set,
independent of `auth_public_read_paths`, because `deploy/deploy.sh` polls it
after every restart with no credential and no login flow of its own — making
it authenticate-only would turn every future deploy into a hang.
**Follow-up, not built here:** `firmware/inside-observer` already has an
NVS-persisted `MqttSettings` struct with `username`/`password` fields
(`model/settings.h`) that are unused today (ADR-023 shipped HTTP polling
first, MQTT is "not wired up yet"); the natural home for a future bearer
token is a new field alongside them, sent as `Authorization: Bearer <token>`
on both polled requests, provisioned through the same captive-portal config
page the MQTT fields already use. Once that firmware update ships and is
deployed, `auth_public_read_paths` can be set to `()` (or the operator can
simply stop configuring it) and the exemption closes completely without any
further backend change — the allow-list is already the only thing standing
between "today" and "closed."

**Reason `Secure` is off by default on the session cookie, stated honestly
rather than quietly making login not work.** The station is served over
plain HTTP with no TLS component anywhere in this codebase (ADR-015's
LAN-trust premise is unchanged, not revisited here). A cookie marked `Secure`
is refused by the browser on a non-HTTPS origin — not "less safe", simply
never sent — which would make `POST /api/v1/auth/login` appear to succeed
(200, a `Set-Cookie` header goes out) while every subsequent request silently
carries no credential at all. That is strictly worse than the status quo: a
login page that appears to work and does not is a worse trap than no login
page. `auth_cookie_secure` defaults to `false` and is documented, in both
this ADR and `docs/operations/DEPLOYMENT_AND_OPERATIONS.md`, as something to
flip to `true` only once a reverse proxy or similar terminates TLS in front
of this station — at which point the browser's own protections start doing
real work. `HttpOnly` and `SameSite=Lax` are unconditional regardless of
`auth_cookie_secure`, since both are free of that HTTP-only failure mode and
narrow real attack surface (script-readable cookie theft; naive
cross-site-request forgery) independent of TLS.

**What this protects against, and what it explicitly does not — say this
plainly rather than claim "secure".** It protects against another device or
person on the same LAN reading or changing station state with zero
credential, which is the exact gap ADR-015 recorded. It does **not**
protect the session cookie or a bearer token from anything that can observe
LAN traffic (no TLS exists in this codebase to prevent that), does not
protect against a compromised device that already has a valid credential
(no device binding, no anomaly detection), does not implement CSRF tokens
(mitigated only by `SameSite=Lax` plus this API's near-total lack of
state-changing `GET` routes — a narrower guarantee than a dedicated token),
and does not rate-limit anything except the login endpoint itself (an
authenticated client can still call any other endpoint as fast as it likes,
unchanged from before this feature). The rate limiter itself is coarse and
in-process (`auth.RateLimiter`): it resets on restart and does not share
state across workers, which is judged acceptable for a login form on a
single-appliance home LAN process (this project runs one worker) but would
under-count in front of a load balancer this project does not run.

**Bootstrap.** On first startup with `auth_enabled: true` and an empty `user`
table, one account (`auth_bootstrap_username`, default `operator`) is created
with a `secrets.token_urlsafe(18)`-generated password, printed once to
stdout (`flush=True` — block-buffering under uvicorn/systemd was observed
during this work to hold the banner in an ~8 KiB pipe buffer indefinitely
without it) and logged at WARNING, with `must_change_password: true`. The
generated password is never the default in code, config, or documentation —
grep this repository for it and find nothing, by construction. The web UI
enforces the change-password step before showing the rest of the app; the
API enforces it structurally the same way any other principal's
`must_change_password` flag would be honoured by a client that checks it
(the flag is returned from `/auth/login` and `/auth/me`, but is **not**
itself enforced as a second gate on other endpoints in this change — an
operator who ignores the UI's prompt and calls the API directly with a
machine client stays logged in on the generated password. Recorded as a
scope gap rather than silently claimed closed.)

**Verification.** `tests/test_auth.py` (26 cases): hashing never returns or
logs plaintext; sessions and tokens round-trip and reject forged/expired/
revoked credentials; the blanket gate is a no-op with `auth_enabled: false`
and refuses reads and writes alike once true; the ESP32-dependent paths
(`/api/v1/health`, `/api/v1/detections` GET, `/metrics`) stay reachable
under the gate by a dedicated regression test named for exactly that risk;
the login endpoint rate-limits and returns `Retry-After`; a locked-out
station (`auth_enabled: true`, zero active users) is flagged in `/health`.
`web/src/state/authState.test.ts`, `hooks/useAuth.test.tsx`, and
`components/Login.test.tsx` (19 cases) cover the client-side state machine,
including that a stray 401 during the initial `/me` probe cannot flash a
login form on a station where auth turns out to be off. The full login →
forced-password-change → authenticated dashboard → sign-out → re-login cycle
was additionally driven through a real Chromium instance against a locally
running `oo serve` (synthetic source, `OO_AUTH_ENABLED=true`) during this
work, screenshots retained in the session transcript, confirming the cookie,
the WebSocket's own auth check (`/api/v1/live` closes with code 4401 and no
credential once enabled, verified by the same session going dark on logout
until sign-in), and the UI's gating all agree with each other. Not run: the
72-hour soak, and anything against the live Pi station, per this session's
hard rule against touching it.

**Territory note for reconciliation.** Two new tables (`user`,
`auth_session`) and one (`api_token`) are added to `db/models.py`. No
Alembic migration is written here — `alembic/` and `db/session.py` are a
concurrent agent's territory this session — but SQLite's existing
`create_all()` + `_patch_sqlite_columns()` path in `db/session.py` already
creates any new table on next startup with no migration needed for the
SQLite developer/on-device profile (ADR-007); the PostgreSQL profile will
need these three tables added to whatever Alembic revision that agent's work
produces.


## ADR-037 (**PROPOSED — not accepted, nothing implemented**): Detection-table growth is real but slow; prune the five indexes nothing reads, and defer the rest

**Status: Proposed.** No schema change, no migration, no index drop and no model
edit was made for this ADR. It exists so the operator can accept or reject each
option separately. Every number below was measured on **2026-08-09** against a
read-only copy of the live station's `openobservatory.sqlite`
(`ssh observer@station.example`, `scp`, opened `mode=ro` or on a local copy; the
station's own file was never opened for writing and the station was never
deployed to or restarted).

### The question

The operator looked at `source_start_frame` / `source_end_frame` /
`audio_stream.frame_count` — `BigInteger` columns counting frames at 384,000 Hz,
so eleven digits after half a day — and said *"that index is going to get
insanely large, one to give some consideration to."*

Three separate concerns are tangled in that sentence. They have three different
answers.

---

### 1. Frame-value magnitude: **unfounded**, and there is no such index

Largest frame value anywhere in the live database: **24,830,943,552**
(`detection.source_end_frame`, on the 2026-08-06 stream that the orphan-recovery
path closed). Signed 64-bit tops out at **9,223,372,036,854,775,807**. The
station is using **0.00000027%** of the range — a factor of **3.7 × 10⁸** of
headroom, which at a continuous 384 kHz is **about 761,000 years** of unbroken
capture.

Frames also do **not** accumulate across the station's life. They are per-stream:
`min(source_start_frame)` across all 61,453 detections is **0**, and each stream's
maximum detection frame tracks its own `frame_count` (the largest live stream:
`frame_count` 16,613,836,800, max detection frame 16,614,944,704). `StreamClock`
anchors to the first block of each stream, so a restart resets the counter. Even
without the reset the answer would be the same.

**And there is no index on any frame column.** `SELECT name FROM sqlite_master
WHERE type='index' AND sql LIKE '%frame%'` returns nothing. `source_start_frame`
and `source_end_frame` are plain unindexed payload; SQLite stores them as
varints, measured at **9.7 bytes each on average**, 1.19 MB for the pair across
the whole table — 1.3% of the file.

**What the operator probably actually saw** is the *number*, not an index: an
eleven-digit integer in `/api/v1/detections` JSON or in a `sqlite3` dump, which
looks alarming and is not. The concern was worth raising and it is worth
retiring: the frame columns are among the cheapest things in the row, and their
magnitude will never matter.

Do not "fix" this by narrowing the type or resetting the counter. Frame identity
is what makes evidence reproducible (ADR invariant: frames, not timestamps,
address audio), and `BigInteger` is also what makes the PostgreSQL profile
identical.

---

### 2. Row and index growth: **real, slow, and a quarter of it is waste**

#### Measured size attribution (dbstat, whole file, 2026-08-09)

The file is 94.0 MB (89.6 MiB; `page_size` 4096 × `page_count` 22,953,
freelist 0), holding 61,453 detections, 26,853 media assets and 26,853
detection-media links accumulated over **4.53 days**.

| Object | MB | % of file | Notes |
|---|---:|---:|---|
| `detection` (table) | 44.77 | 47.6% | 728 B/row |
| `media_asset` (table) | 16.83 | 17.9% | 627 B/row |
| `ix_detection_station_start` | 4.71 | 5.0% | **never used** |
| `ix_detection_group_start` | 3.34 | 3.6% | used |
| `ix_detection_station_id` | 2.85 | 3.0% | **never used** |
| `ix_detection_stream_id` | 2.83 | 3.0% | used |
| `sqlite_autoindex_detection_1` (PK) | 2.77 | 2.9% | unavoidable |
| `ix_detection_detector_id` | 2.73 | 2.9% | **never used** |
| `ix_detection_event_start_utc` | 2.44 | 2.6% | used — the workhorse |
| `detection_media` (table) | 2.31 | 2.5% | |
| `sqlite_autoindex_detection_media_1` | 2.23 | 2.4% | covering, used |
| `ix_detection_taxonomic_group` | 1.44 | 1.5% | **never used** (prefix of `group_start`) |
| `sqlite_autoindex_media_asset_1` | 1.22 | 1.3% | used |
| `ix_media_asset_created_at` | 1.05 | 1.1% | used |
| `ix_detection_canonical_taxon_id` | 0.76 | 0.8% | **never used** |
| `ix_media_asset_kind` | 0.64 | 0.7% | used |
| `capture_gap` + its 3 indexes | 0.51 | 0.5% | |
| `ix_media_asset_reclaimed_at` | 0.23 | 0.2% | used (added by ADR-035's `0002`) |
| everything else (audio_stream, health_event, auth, schema) | 0.26 | 0.3% | |

Indexes are **27.0 MB, 28.7% of the file**. Detection indexes alone are 23.87 MB
— **more than half the size of the table they index**.

#### Which indexes actually earn their keep

Two independent checks agree.

`EXPLAIN QUERY PLAN` over the real queries in `history.py`, `api/app.py`,
`retention.py` and `station.py`:

```
timeline (12 h bucketed)      SEARCH detection USING INDEX ix_detection_event_start_utc
species_summary               SEARCH detection USING INDEX ix_detection_group_start
GET /detections?limit=200     SEARCH detection USING INDEX ix_detection_event_start_utc
GET /detections&group=bird    SEARCH detection USING INDEX ix_detection_group_start
station: max(event_end) /stream SEARCH detection USING INDEX ix_detection_stream_id
retention tier scan           SEARCH detection USING INDEX ix_detection_event_start_utc
retention exemplar scan       SEARCH detection USING INDEX sqlite_autoindex_detection_1
```

And a grep: no code anywhere filters or orders by `Detection.station_id`,
`Detection.canonical_taxon_id`, or `Detection.detector_id` (the only reference to
`detector_id` is a join *driven from* `detection` into `detector`'s primary key,
which uses `detector`'s index, not `detection`'s). `ix_detection_taxonomic_group`
is a strict prefix of `ix_detection_group_start`, so the planner never has a
reason to choose it.

**Five of the nine detection indexes are dead**: `ix_detection_station_start`
(4.71 MB), `ix_detection_station_id` (2.85), `ix_detection_detector_id` (2.73),
`ix_detection_taxonomic_group` (1.44), `ix_detection_canonical_taxon_id` (0.76)
— **12.49 MB, 13.3% of the whole database**, growing forever, written on every
one of ~14,000 inserts a day, and read by nothing.

`station_id` deserves a specific note: it holds **one distinct value** in 61,453
rows. It costs 32 B/row inline plus two indexes totalling 7.56 MB, for a column
whose entire information content today is a constant.

#### What insert throughput and SD-card writes actually cost

Measured on a 15.3 M-row copy, inserting 21,500 rows one commit at a time (the
pattern `Station._insert_detection` actually uses — one `session_scope()`, one
transaction, per detection), with `wal_autocheckpoint=0` so the WAL records every
page image written:

| configuration | µs/row | WAL B/row | ≈ card writes/day at 21,500/day |
|---|---:|---:|---:|
| as shipped: 9 indexes, random UUID4 PK | 44.7 | 17,355 | ~746 MB |
| 5 dead indexes dropped | 13.4 | 13,162 | ~566 MB |
| dropped + time-ordered (UUIDv7-style) PK | 11.3 | 6,852 | ~295 MB |

(The "card writes" column doubles the WAL figure, because a checkpoint writes the
same pages again into the main file. It is an upper bound: real commits are
smaller than the benchmark's and some pages are re-dirtied within one checkpoint
window. It is measured on x86; the *ratios* transfer, the absolute µs do not.)

Two things fall out. Dropping the dead indexes makes an insert **3.3× cheaper in
CPU** and cuts its write volume by **24%**. And the single biggest source of
write amplification that remains is the **random UUID4 primary key**: every
insert dirties a different random leaf page of a 15 M-entry b-tree. A
time-ordered key halves the writes again. Neither is urgent (see §3), but the
second is free at the PostgreSQL move and expensive afterwards.

#### Growth projection, with the range stated honestly

The clean measurement is the file itself: **94.0 MB over 4.53 days = 20.8 MB/day
= 7.6 GB/year**, which is consistent with the per-row costs
(14,000 detections × 1,116 B + 5,967 media assets × 913 B ≈ 21 MB/day).

Per-day detection counts actually observed:

| day | activity-v1 | birdnet | ultrasonic | total |
|---|---:|---:|---:|---:|
| 2026-08-05 | 12,058 | 1,590 | 2,616 | 16,264 |
| 2026-08-06 | 11,746 | 2,761 | 502 | 15,009 |
| 2026-08-07 | 1,628 | 426 | 35 | 2,089 (capture outage) |
| 2026-08-08 | 10,018 | 1,370 | 1,489 | 12,877 |

The brief's **~21,500/day is an over-estimate**: it was extrapolated from an 11.9 h
window containing a busy bat night. Measured full days are **12,900–16,300**.
This is also the **annual maximum**, not the mean — early August is peak bird and
peak bat. Bats disappear entirely from roughly November to March (2,616 → 35 rows
between two days here shows how volatile that term already is), and dawn chorus
collapses. `activity-v1` is the term that will *not* fall much, because wind and
rain are acoustic events too.

| horizon | low (8,000/day) | central (14,000/day) | high (21,500/day) |
|---|---:|---:|---:|
| detection rows, 1 yr | 2.9 M | 5.1 M | 7.9 M |
| database, 1 yr | 4.3 GB | 7.6 GB | 11.7 GB |
| database, 3 yr | 13 GB | 23 GB | 35 GB |
| database, 10 yr | 43 GB | 76 GB | 117 GB |

Assumptions: current schema and current index set; media assets keep accruing at
the observed 5,967/day (their rows are never deleted either — ADR-026 only sets
`reclaimed_at`); no roll-up; SQLite page size 4096.

#### Does SQLite actually get slow? No.

Built two grown copies from the live rows (5.10 M and 15.30 M detections, 5.76 GB
and 17.29 GB files) and timed the real queries with a deliberately small 2 MB page
cache:

| query | today (61 k) | 1 year (5.1 M) | 3 years (15.3 M) |
|---|---:|---:|---:|
| timeline, last night, 10 min buckets | 13.7 ms | 7.6 ms | 7.8 ms |
| species_summary, last night | 4.3 ms | 2.9 ms | 2.6 ms |
| `GET /detections?limit=200` | 0.5 ms | 0.3 ms | 0.3 ms |
| recent-species panel, 24 h | 21.0 ms | 11.3 ms | 11.5 ms |
| retention exemplar scan | 94.9 ms | 64.4 ms | 66.1 ms |
| **full-history species list (no time bound)** | 13.3 ms | **743.6 ms** | **2,310.8 ms** |

Every query the station actually runs is **flat in database size**, because every
one of them is a bounded range scan on `event_start_utc` (or on `taxonomic_group,
event_start_utc`). They get *faster* on the grown copies only because the b-tree
was rebuilt densely. The retention exemplar scan — the one unbounded read
ADR-026 deliberately allowed — is bounded in practice by *live* media assets, not
by history, and did not grow.

The only thing that degrades is a whole-database aggregation with no time
predicate. **No endpoint does one today.** That is the thing to protect.

Calibration to the real hardware: `GET /api/v1/history?range=last-night` on the
live Pi measured **128 ms** end to end (against ~20 ms of equivalent SQL on the
benchmark machine), so the Pi is roughly **5–6×** slower. `GET
/api/v1/detections?limit=200` measured **454 ms** on the Pi against 0.3 ms of
SQL — that endpoint's cost is **JSON serialisation of 200 full rows including
`native_result`**, not the index, and no amount of schema work will change it.

**Conclusion for §2:** the growth is real and permanent, but at 7.6 GB/year with
flat query latency it is a *housekeeping* problem, not an architectural one. The
one genuinely indefensible thing is 12.49 MB (13.3%) of indexes that no query
reads.

---

### 3. Where the data lives: **not the emergency it was in August**

The database is on the SD card by ADR-021's deliberate choice, and heavy SD I/O
caused capture overruns on 2026-08-05. But:

- **Capacity is a non-issue.** `/dev/mmcblk0p2` is 237.8 GB with **194 GB free**
  (14% used). At the central 7.6 GB/year that is 25 years; at the pessimistic
  11.7 GB/year, 16 years.
- **The database is a small fraction of the card's write load.** Measured from
  `/proc/diskstats` over 120 s on the live station: **16,328 sectors = 8.36 MB
  written to `mmcblk0` in two minutes = 6.0 GB/day** from all causes. The
  database's *net* growth is 20.8 MB/day; its amplified write volume is on the
  order of 0.3–0.7 GB/day by the table in §2. journald alone is already holding
  665.6 MB. I could not isolate the database's exact share — that would need
  per-file instrumentation the station does not have — so treat 0.3–0.7 GB/day as
  modelled, not measured.
- **6 GB/day is ~2.2 TB/year.** A 256 GB card's TBW rating is the number that
  matters here and it is not printed on the station's card, but even a poor 20 TBW
  gives ~9 years and a high-endurance 100+ TBW gives decades.
- The `-wal` file has been steady at **4,288,952 bytes** across half an hour of
  observation — SQLite's default `wal_autocheckpoint` of 1000 pages is holding it
  at ~4 MB and reusing the space in place. Nothing is running away.

For contrast, the **SSD** took **146,616 sectors = 75 MB in the same 120 s
(~54 GB/day)** and holds 43 GB of clips after five days, with **zero** assets
reclaimed so far because nothing has aged past ADR-026's 7-day native tier yet.
Clip storage is two to three orders of magnitude more data than the database and
is where the storage engineering actually is.

---

### Per-record representation: measured, not modelled

Every variant below was materialised from the live station's own 61,453 rows and
measured with dbstat, each with the same three indexes that queries actually use
(`event_start`, `group_start`, `stream_id`):

| variant | table MB | idx MB | total MB | B/row | vs shipped |
|---|---:|---:|---:|---:|---:|
| as shipped (32-char hex UUID text, ISO timestamp text, inline strings, full JSON) | 44.77 | 7.59 | 52.36 | 852.0 | — |
| + UUIDs as `BLOB(16)` | 39.77 | 6.59 | 46.36 | 754.4 | −11.5% |
| + timestamps as integer epoch-µs | 36.73 | 4.36 | 41.10 | 668.7 | −21.5% |
| + species/label strings normalised to a `taxon` lookup | 32.76 | 3.64 | 36.44 | 592.9 | −30.4% |
| + `native_result` stripped of constant/duplicated keys | 21.25 | 3.64 | 24.93 | 405.6 | **−52.4%** |
| + `native_result` dropped for `acoustic_event` rows | 12.46 | 3.64 | 16.13 | 262.5 | **−69.2%** |

Per-column payload in the shipped schema (sum over all rows / 61,453):

| column | avg B | total MB | comment |
|---|---:|---:|---|
| `native_result` | **356.6** | **21.91** | the single largest cost, 53% of row payload |
| 5 × UUID columns | 32.0 each | 9.86 | SQLAlchemy `Uuid` on SQLite persists 32-char hex **text** |
| 3 × timestamp columns | 26.0 each | 4.81 | ISO text `2026-08-04 18:44:08.972733` |
| `score` | 16.5 | 1.01 | IEEE double |
| `detector_label` | 15.8 | 0.97 | repeats heavily |
| `taxonomic_group` | 11.8 | 0.72 | 3 distinct values in 61,453 rows |
| `source_start/end_frame` | 9.7 each | 1.19 | the operator's original worry |
| `common_name`+`scientific_name`+`canonical_taxon_id`+`rank` | 10.9 combined | 0.67 | 95 distinct species |
| `calibrated_probability` | 1.0 | 0.06 | **100% NULL** — 61,453 of 61,453 |

Findings worth acting on, and worth *not* acting on:

- **`native_result` is the prize.** 356.6 B/row average. Roughly half of it is
  constant or already duplicated elsewhere: `detector` (implied by `detector_id`),
  `model_id` (on the `detector` row), `score_definition` /
  `confidence_definition` (constant per detector version), `band_hz` (constant per
  configuration), `hint_is_not_identification` (a constant `true`), `label`
  (duplicated in `detector_label`), `confidence` (duplicated in `score`),
  `peak_frequency_hz` (duplicated in the typed column). Stripping exactly those
  keys takes the average to **178.7 B** and the row to 405.6 B — a **52%**
  reduction in the whole detection footprint, with **no measurement lost**: every
  removed key is either a constant recoverable from the detector version or a
  copy of a column that is already there. This is the highest-value change on the
  list and it is not a schema change at all; it is what `normaliser.py` chooses to
  persist.
- **UUID-as-text costs 16 B/column × 5 columns inline, plus the same again in every
  index that covers one** — measured at 97.6 B/row, 11.5% of the table+index
  footprint. Free on PostgreSQL, where `uuid` is natively 16 bytes. On SQLite it
  needs a `TypeDecorator` and it makes `sqlite3` output unreadable, which on a
  station where the operator debugs by hand is a real cost.
- **Timestamps as ISO text cost 26 B where an integer epoch costs ~7**, measured at
  85.7 B/row across three columns and the two timestamp indexes. Note this is the
  *only* change here that touches the "UTC internally, local for presentation"
  rule, and it honours it more strictly, not less — an integer epoch has no
  timezone to get wrong.
- **String normalisation is worth less than it looks.** 95 distinct species and 3
  taxonomic groups across 61,453 rows sounds like a huge win, but SQLite already
  stores NULL in one byte and the species strings only appear on the 12.5% of rows
  that have a species at all. A full `taxon` lookup table is **97 rows and 32.8 kB**
  and saves 75.8 B/row (8.9%) — real, but it adds a join to `species_summary` and
  `taxa/activity` and an interning cache to the write path, for the third-smallest
  saving on this list. **Not worth doing for its own sake**; worth doing if the
  schema is being rewritten anyway.
- **`calibrated_probability` is 100% NULL** and costs one byte per row (61 kB
  total). It is the contract that says a score is not a probability. **Keep it.**
  `rank`, `canonical_taxon_id`, `common_name`, `scientific_name` are NULL on the
  53,753 unidentified rows and cost one byte each there — already efficient.

---

### The `acoustic_event` roll-up question

48,221 of 61,453 rows (**78.5%**) are `activity-v1` `acoustic_event` rows with no
species. Two facts decide this:

1. **Not one of them has an evidence clip.** `SELECT ... FROM detection d WHERE
   taxonomic_group='acoustic_event' AND EXISTS(...detection_media...)` returns
   **0 of 48,221**. Their frame bounds address audio that no longer exists once the
   capture ring wraps, so the "exactly reproducible evidence" property those
   columns exist for is, for these rows specifically, already unrealisable.
2. **They arrive at 18.4 rows per active minute** (48,221 rows across 2,616
   distinct minutes, 67 distinct hours). A per-minute roll-up would compress them
   **18.4×**: ~2,616 rows instead of 48,221 for this sample, ~1,440 rows/day
   instead of ~11,000.

What a per-minute roll-up would preserve: that the detector was firing, how often,
how loud (min/max/mean SNR), the peak-frequency distribution (there are only 209
distinct `peak_frequency_hz` values and 101 distinct 2-dp scores in the whole
population — these are already quantised), and therefore everything the
synthetic-source incident actually needed, which was *"activity-v1 was producing
detections at a normal rate against a scene that was not real."*

What would be lost, and it is not nothing: the exact second of an individual
event; the ability to say "there was a sound at 03:41:07.2 lasting 981 ms with a
peak at 1218.8 Hz"; and the ability to retrospectively re-attribute an individual
acoustic event to a species if a future detector is run over archived audio.
Given that no archived audio for these rows exists, the last one is theoretical.

Combined with dropping `native_result` on these rows, a roll-up would take the
database from 20.8 MB/day to roughly **5 MB/day** — a 4× reduction. It is the
largest single lever available. It is also the only one on this list that
**destroys information**, and it needs the operator's judgement, not an
engineer's.

---

### Evidence-clip encoding (in scope by the operator's request; ADR-026 territory)

Measured on two real clips pulled from the station, encoded with the Pi's own
`ffmpeg` build locally:

| clip | source | FLAC -0 | FLAC -5 | FLAC -8 | lossless round-trip |
|---|---:|---:|---:|---:|---|
| native 384 kHz mono s16, 6.01 s | 4,615,852 B | 0.557 | **0.531** | 0.530 | byte-identical PCM |
| 48 kHz playback rendering, 6.00 s | 576,356 B | 0.660 | **0.560** | 0.560 | byte-identical PCM |

Clips are **43 GB after five days (~8.6 GB/day)**, three orders of magnitude more
than the database. FLAC at `-5` would take that to **~4.6 GB/day** and is provably
lossless (verified: the decoded PCM hashes identically to the source), which is
the only acceptable property for evidence.

Why this is **not** recommended now, despite being by far the largest byte saving
available anywhere in the system:

- Encoding ~10 hours of clip audio a day is real CPU on a machine whose first rule
  is that capture always wins. It would land on `_evidence_executor` — correctly,
  *not* the default pool the ALSA read shares (ADR-021) — but that executor is
  already the busiest non-capture thread, and 2026-08-05 is on record for what
  happens when evidence I/O contends with capture. **I did not measure FLAC encode
  time on the Pi 5 itself** (that would have meant putting load on a live station),
  only on a desktop, so the CPU figure is unquantified and must be measured before
  anyone acts on this.
- The SSD is **10% full (43 GB of 458 GB)** and ADR-026's tiers have not fired once
  yet — nothing is 7 days old. The compression would be solving a problem the
  tiering has not been given a chance to solve.
- It changes `media_asset.mime_type`, the `/api/v1/media/{id}` response, the web
  UI's audio element and the checksum semantics. That is a feature, not a tune-up.

**Revisit when the SSD passes 250 GB or the retention watermark starts reclaiming
clips younger than 30 days** — at that point 47% off every native clip is worth
the CPU, and a Pi-side encode benchmark is the first step.

---

### Options, honestly

| # | Option | Saving | Cost / risk | Verdict |
|---|---|---|---|---|
| A | **Do nothing** | 0 | Database reaches ~23 GB in 3 years on a card with 194 GB free; every real query stays flat; ~12.5 MB of dead index grows forever and triples insert CPU | Defensible. Nothing here is on fire. |
| B | **Drop the 5 unused detection indexes** | 12.49 MB now (13.3%), ~1.0 GB/yr, 3.3× cheaper inserts, −24% WAL writes | One Alembic revision (`0003`); reversible in one `CREATE INDEX`; needs a deploy. Risk: a *future* multi-station or per-species query would want two of them back | **Recommended** |
| C | **Stop persisting constant/duplicated `native_result` keys** | 52% of the detection footprint, ~3.5 GB/yr | `normaliser.py` change; no schema change; old rows keep their verbose JSON so nothing historical is rewritten. Needs care: it is an audit record and a successor must be able to reconstruct what was dropped from the detector version | **Recommended next**, after B, as a separate reviewed change |
| D | **Roll up `acoustic_event` rows per minute** | 4× overall (20.8 → ~5 MB/day) | Destroys per-event detail permanently. New table, new write path, new query path, and the history UI has to learn about two row shapes | Operator's call. Defer until C is done and re-measured. |
| E | **Re-encode UUIDs/timestamps** (`BLOB(16)`, integer epoch) | 21.5% | Breaking schema change, rewrite of every row, `TypeDecorator`, unreadable in `sqlite3` | **Do it at the PostgreSQL move or not at all.** Free there, expensive later. |
| F | **Time-ordered primary key (UUIDv7)** | halves insert write amplification (13,162 → 6,852 WAL B/row) | Breaking key change; UUIDs stay UUIDs so the contract is unchanged | **Do it at the PostgreSQL move.** Cheapest big win available *at that moment*, unavailable cheaply after. |
| G | **`taxon` lookup table** | 8.9% | Join on two hot query paths, interning cache on the write path | Only if E is happening anyway. |
| H | **Move the database to the SSD** | 0 bytes; removes DB writes from the SD card | Contradicts ADR-021's explicit reasoning: the station keeps capturing, detecting and serving history with the SSD unplugged *because* the database is on the always-present system disk. Buys ~0.3–0.7 GB/day of SD writes against 6 GB/day total | **No.** ADR-021 already decided this and the numbers do not overturn it. |
| I | **PostgreSQL** (ADR-007's DSN swap, now that ADR-035 exists) | 0 bytes by itself | The migration environment exists but has never been run against a real PostgreSQL 16; adds a server to a machine that has none | Not for size. Do it when concurrent writers or `LISTEN/NOTIFY` are needed, and take E+F+G with it. |
| J | **PostgreSQL + TimescaleDB compression** | plausibly 5–10× on a columnar-compressed hypertable | Everything in I, plus a feature flag the spec allows but nothing has exercised, plus compressed chunks are effectively read-mostly | Premature. Revisit only if D is rejected *and* the trigger below fires. |
| K | **FLAC for evidence clips** | ~47% of 8.6 GB/day — the biggest saving in the system | CPU on the evidence executor, unmeasured on the Pi; touches ADR-026 | Defer; see trigger above. |

---

### Recommendation

**Take B now. Take C next, as its own reviewed change. Leave everything else
alone, and bank E, F and G for the PostgreSQL move.**

B is the only change that is pure profit: it removes 13.3% of the database, makes
every detection insert 3.3× cheaper, cuts SD-card write volume by a quarter, and
cannot make any query slower, because `EXPLAIN QUERY PLAN` shows no query uses
the indexes being removed. It is one Alembic revision, reversible in five
`CREATE INDEX` statements.

C is larger in bytes and slightly larger in judgement, which is why it should not
ride along with B.

Everything else is either premature (the database will not be uncomfortable for
years, and query latency is flat), destructive (D), or much cheaper if bundled
with a migration that is going to happen anyway (E, F, G).

**Scheduling:** B requires a deploy and the operator has a soak planned for the
coming working week. Land it **before** the soak starts or **after** it finishes,
never during — a restart resets every counter the soak is measuring. The soak is
also the ideal place to confirm the insert-cost improvement on real hardware.

**What would change this recommendation:**

- If a query that scans the *whole* detection history is added to the UI or API —
  a lifetime species list, an all-time chart — then flat latency stops being true
  (2.3 s at 3 years on a fast desktop, so ~12 s on the Pi) and the answer becomes
  D or J rather than B.
- If `activity-v1`'s rate rises materially (a noisier site, a lowered threshold),
  D moves from "operator's call" to "necessary".
- If the SD card turns out to be a low-endurance consumer part, the write-
  amplification numbers stop being academic and F becomes urgent rather than
  opportunistic.
- If PostgreSQL is adopted for an unrelated reason, E+F+G should be taken in the
  same migration and this ADR's cost/benefit is rewritten.

---

### Trigger — revisit this ADR when any of these is true

| Metric | Threshold | Why that number |
|---|---|---|
| `openobservatory.sqlite` file size | **> 10 GB** | ~1.3 years at the central rate; still 5% of the card, but the point where a 3-year projection starts to matter |
| SD card free space | **< 50 GB** | 21% remaining; well before anything is at risk, and the database will not be the cause |
| p95 of `GET /api/v1/history` on the Pi | **> 1 s** | 8× today's measured 128 ms; would mean the flat-latency finding has stopped holding |
| Sustained detections/day, 7-day mean | **> 40,000** | ~2.5× the observed maximum; halves every time-to-threshold above |
| SSD usage | **> 250 GB** | The FLAC decision (K), which is a bigger lever than anything in the database |

None of these is close today. The file is 94 MB, the card has 194 GB free,
`/history` answers in 128 ms, the busiest measured day was 16,264 detections, and
the SSD is at 43 GB.

### What was not verified

- FLAC encode cost **on the Pi 5** — measured only on a desktop, so option K's CPU
  figure is unquantified.
- The database's exact share of the card's 6.0 GB/day of writes; only the total was
  measured, the split is modelled from the WAL benchmark.
- The SD card's TBW rating, which is what turns write-amplification numbers into a
  lifetime.
- Whether recent main-branch changes (`68f4234`, the acoustic-event filter fix) have
  reached the running station, which would lower the `activity-v1` rate the
  projections use.
- Nothing here has been run on PostgreSQL; ADR-007's "the DSN swap is
  configuration-only" is still unverified beyond SQLite, exactly as ADR-035 says.
