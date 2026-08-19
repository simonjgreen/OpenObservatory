# Initial Architecture Decisions

Every material deviation from `TECHNICAL_SPEC.md` is recorded here, numbered.
**ADR numbers are referenced by number from source comments across `src/`,
`web/`, `firmware/`, `tests/`, `alembic/` and `config/example.env`.** Therefore:

- **Never delete an ADR**, including a superseded one. Mark it, keep it.
- **Never renumber an ADR.** The gaps at 031 and 036 are numbers that were
  reserved and not needed; a gap is harmless, a broken cross-reference is not.
- Several ADRs appear out of numeric order in this file, because each was
  appended where it was written and because the 2026-08-09 fan-out renumbered
  three of them by hand across rebases. As of 2026-08-09 the out-of-order pairs
  are: **016** between 011 and 012; **034** after 035; **043** after 044;
  **046** after 047; and **053** between 050 and 051.
  The index below is the authoritative ordering. Do not reorder the file to fix
  it — that would produce a large diff for no gain and conflict with appends.
- New ADRs are **appended** to the end of this file, and added to the index.
  **Adding a body without adding an index row is the failure mode that actually
  happens** — 037, 038 and 040 were each missing from this index until
  2026-08-09. If you add an ADR, `grep '^## ADR-' ADRS.md` and check the count
  against the index before you commit.

## Index

Status as verified on 2026-08-09. "Superseded" never means deleted: the original
reasoning is retained and is often the most useful part.

| # | Decision | Status |
|---|---|---|
| 001 | Single exclusive audio capture owner | active |
| 002 | Highest practical native rate, with a derived audible stream | active |
| 003 | Immutable time-addressed windows | active |
| 004 | PostgreSQL canonical metadata, filesystem canonical clip bytes | **partly superseded by ADR-007** — SQLite is the only database actually exercised |
| 005 | No authoritative composite biodiversity score in v1 | active |
| 006 | Separate model installation and licensing | active |
| 007 | SQLAlchemy storage with SQLite in developer mode | active; its "no Alembic environment" position is **superseded by ADR-035** |
| 008 | Native systemd deployment for the debug slice; Compose deferred | active |
| 009 | In-process event bus behind a transport-neutral protocol | active |
| 010 | An owned acoustic-activity detector is the first plugin | active |
| 011 | The debug UI is an observability surface, not the product dashboard | **partly superseded by ADR-016** |
| 012 | Two live channels, and exactly one writer per WebSocket | active |
| 013 | `ultrasonic-pass-v1`, a non-taxonomic detector on the native stream | active; its "no night scheduler" constraint is **closed** — a scheduler shipped in Milestone 5 (`schedule.py`) |
| 014 | Ultrasonic evidence rendered into the audible band | active |
| 015 | Anonymous read access, authentication deferred to Milestone 4 | **closed by ADR-034** |
| 016 | The debug UI is the foundation of the product dashboard | active; supersedes part of ADR-011. Its measurements of the *starting* state (styles.css line count, no component test library, `App.tsx` size) are historical — all three were addressed in Milestone 4 |
| 017 | BatDetect2 is evaluated as an optional adapter; weights never bundled | active |
| 018 | Live ultrasonic monitoring is a second heterodyne, one oscillator per station | active |
| 019 | Live playback moved off Web Audio to a chunked-WAV stream | active |
| 020 | Non-live-source detections excluded from browsing views by default | active |
| 021 | Evidence clips live on their own device, mounted over the clips directory | active |
| 022 | Live ultrasonic retuning as a plain HTTP control call | active |
| 023 | The inside observer is an ESP32 counter-top display that never shows a score | active on the no-score rule and the device choice; its premise that "no code in this repository publishes to a broker" is **superseded by ADR-025**, its **polling decision is superseded by ADR-038** (push, with polling retained as an exercised fallback), and the **single-app-slot partition table it froze is superseded by ADR-050** |
| 024 | Capture coverage is bounded by delivered frames, not a stream row's claim | active; its "no Alembic environment exists" note is **superseded by ADR-035** |
| 025 | MQTT publisher and Home Assistant Discovery, off by default | active |
| 026 | NVR-style tiered clip retention; detection metadata kept forever | active; **amended by ADR-033** (sweep cadence), and its "no Alembic migrations anywhere" note is **superseded by ADR-035** |
| 027 | A spacing/type scale for the web UI, applied to new surfaces | active |
| 028 | Operator/diagnostic disclosure as one depth toggle | active |
| 029 | Retention UI built against an assumed API shape; review shipped minimally | active; the assumed endpoint now exists (`GET /api/v1/retention/status`) and the two were reconciled; its "`corrected_taxon_id` left for a future ADR" note is **closed by ADR-043** |
| 030 | The ALSA ring is sized for scheduling jitter; the capture read owns its thread | active |
| 031 | *(number reserved and not used — do not reuse)* | — |
| 032 | A near-zero occurrence prior suppresses a BirdNET candidate outright | active |
| 033 | Retention is paced, because a dedicated thread is not the same as out of the way | active; amends ADR-026 |
| 034 | An authentication foundation closes ADR-015, off by default | active; its "no Alembic migration is written here" note is **closed** — revision `0003_auth_tables` was added afterwards |
| 035 | A real Alembic migration environment, written before the PostgreSQL move | active; its startup-wiring follow-up ("`api/app.py` and `cli.py` calling Alembic instead of `create_all()`") is **closed by ADR-042** |
| 036 | *(number reserved and not used — do not reuse)* | — |
| 037 | Detection-table growth is real but slow; prune the five indexes nothing reads, and defer the rest | **options B and C accepted and implemented** 2026-08-09 (revision `0004`); options A and D–K remain open and unchosen. The research above the "B and C" section is preserved verbatim as the reasoning, not as current schema truth |
| 038 | The inside observer is pushed to over a lean WebSocket, and shows elapsed times | active; **supersedes ADR-023's polling decision**, though polling stays in the firmware as an exercised fallback. Extended by ADR-050, which adds one frame to this wire |
| 039 | A deficit step is only lost audio once it fails to come back | active; closes the open item in ADR-033 and amends ADR-030's estimator. **Confirmed on target** 2026-08-09 against its own pass criteria; its remaining question is resolved by ADR-046 |
| 040 | The live view's pictures are drawn only while somebody is looking | active; the charter's "item 8 loses to items 1–2" precedent |
| 041 | The ultrasonic spectrogram gets its own floor/ceiling, measured on the live station | active |
| 042 | `alembic upgrade head` wired into `deploy/deploy.sh`; `create_all()`/the ALTER TABLE patcher retired from startup | active; closes ADR-035's remaining follow-up |
| 043 | Taxon correction closes the review workflow; a human's ear outranks a machine's | active; closes the open item in ADR-029 |
| 044 | A withdrawn detection is marked in the record and suppressed on the claim surfaces; the BirdNET week index is confirmed correct | active; completes ADR-032 |
| 045 | The refinement runner is a separate, CPU-fenced process, and the BatDetect2 cascade may only propose | active; promotes ADR-017's offline cascade to a scheduled job without adopting it as a source of station claims |
| 046 | The frame deficit is 98% crystal drift, and "audio lost" must not show it | active; resolves the open question in ADR-039 and amends ADR-030's presentation. **Holds at ≤ 1 h; at the 72-hour soak the residual (~175 s of ~185 s) is unexplained — see the 2026-08-14 status note in the ADR body** |
| 047 | Site parameters are runtime state, managed through the web UI; the repository ships no site | active; widened by ADR-048 from site identity to the whole of `Settings` |
| 048 | Every setting is web-configurable, in three declared tiers, with the exclusions named | active; extends ADR-047's mechanism rather than replacing it |
| 049 | BirdNET's eleven sound categories are not species: no clip for human speech, no bird claim, no plausibility floor | active; corrects ADR-032's floor and ADR-044's flag for non-taxonomic classes |
| 050 | The counter-top display gets two OTA app slots and updates itself from the station, with rollback | active, **flashed and verified on hardware 2026-08-09**, including a deliberate rollback drill; changes the partition table ADR-023 froze, and adds one frame to ADR-038's wire |
| 051 | The spectrogram says where the sound you are hearing is, as a measured interval rather than a line | active; frontend only, offset measured against a ground-truth rig rather than assumed |
| 052 | A counter is not a diagnostic — record what BirdNET proposed and refused, in a bounded ring with per-band score histograms | active; the tuning diagnostic ADR-032's counters could not provide, on by default at 2 us per candidate |
| 053 | Taxonomic grouping above species: genus is free, family is a data dependency | **proposed, not implemented** — recorded so the tempting wrong answer is refused in writing |
| 054 | Responsive layout is intrinsic first, breakpoints last, and overflow is never hidden | active; frontend only; fixes an unreachable GO LIVE button and removes the `overflow-x: hidden` that concealed it |
| 055 | The operator can pause recording for a chosen time — and it expires, survives a restart, and is recorded as a pause | active; makes the charter's privacy constraint operable rather than aspirational |
| 056 | History longer than a day is a different question, a different shape and a different table | active |
| 057 | A row that claims evidence must be checkable; reconcile the ones that lie, and keep "missing" distinct from "reclaimed" | active. 8,067 of 48,941 live `media_asset` rows (16.5%, 20.59 GB) claimed clips that `ClipManager.enforce_retention` had unlinked without marking them — **not** ADR-021, which is where it stopped. None are recoverable. No schema change; head stays `0007_capture_pause` |
| 058 | The spectrogram's detection labels are placed, not drawn where they fall | active; a truncated species name can read as a different species, so a dropped label is the honest failure |
| 059 | The clip archive is measured off the event loop, because a status snapshot must not walk a filesystem | active |
| 060 | A read that never returns is a dead stream, not a slow one | active; the wedge itself remains unexplained, and its cost is attributed by ADR-061 |
| 061 | An operator-set keep flag replaces the computed exemplar rule | active; supersedes ADR-026's first/best-of-species exemption; deployment and on-station verification pending |

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

> **Status 2026-08-08: the "not foundation" list above is historical, and all of
> it was addressed.** Measured on 2026-08-09: `App.tsx` is 323 lines with 3
> `useState` hooks, decomposed into `web/src/hooks/*` and `web/src/state/*`;
> `@testing-library/react` is installed and there are **235 frontend tests across
> 22 files** — re-measured on merged `main` late on 2026-08-09; it read 140 across
> 15 files earlier the same day — not one; `?view=operate|diagnose` gives URL-driven state that
> survives a refresh (ADR-028); and `styles.css` gained spacing and type scales
> (ADR-027 — which also records that ~700 lines of older component CSS were
> deliberately *not* migrated, so that part of the assessment still stands). The
> line counts and hook counts quoted above are kept as the measurement that
> justified the decision, not as a description of the code today.

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

> **Status 2026-08-05:** the night scheduler now exists
> (`src/open_observatory/schedule.py`, `ultrasonic_schedule`, default `always`;
> the live station sets `night`). The detector no longer necessarily runs 24
> hours a day. **The false-positive rate on broadband transients is unchanged** —
> scheduling reduces *when* it runs, not how often an individual pass is wrong.
> The detector also gained feeding-buzz flagging, sub-bin peak-frequency
> interpolation, and presentational candidate group titles carrying a mandatory
> `?`; the stored record still keeps `label = "bat pass"` with no species name,
> and the normaliser's guard is unchanged. See `docs/detectors/DETECTOR_STRATEGY.md`.

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

> **Status 2026-08-08: CLOSED by ADR-034.** An authentication foundation shipped —
> Argon2id passwords, session cookies, revocable API tokens. It is deliberately
> **off by default** (`auth_enabled=false`), so a station that has not opted in
> still behaves exactly as this ADR describes, and the constraint above still
> applies to it. ADR-034 also records one deliberate exemption: with auth enabled,
> `GET /api/v1/detections`, `GET /api/v1/health` and `/metrics` stay reachable with
> no credential, for the ESP32 display and `deploy.sh`.

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

> **Not the cause of the missing clips (ADR-057).** 8,067 `media_asset` rows
> claim files that are gone, and the boundary lines up with this ADR closely
> enough to look damning. It is the opposite: the SSD raised the clip budget
> from 20 GB to 300 GB and is what *stopped* `ClipManager.enforce_retention`
> deleting a rolling 24-hour window off the SD card without telling the
> database. The migration verified in the paragraph above did exactly what it
> claims; the files it did not copy had already been unlinked days earlier.

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


## ADR-023: The inside observer is an ESP32 counter-top display that polls HTTP and never shows a score

> **Transport superseded by ADR-038 (2026-08-09).** The display is now pushed to
> over a lean WebSocket (`GET /api/v1/display`) served by the Pi itself. HTTP
> polling remains in the firmware as a real, exercised fallback, so the "why HTTP
> polling rather than MQTT" reasoning below still describes running code — but it
> is no longer the primary path, and the cost figures it was chosen under turned
> out to be the reason to move: ~315 ms of station query time and ~127 kB of
> payload every 20 s, to render six rows. A detection now costs 49 bytes and
> arrives when it happens. The presentation of times also changed: elapsed
> ("4s ago"), ticking once a second, not clock times. **Everything else in this
> ADR stands unchanged** — the board, the partition table and its NVS
> inheritance, the no-PSRAM sprite strategy, and every honesty rule: no score, no
> percentage, no confidence figure, bat passes never named, and three visibly
> distinct failure states. ADR-038 tightens the score rule rather than relaxing
> it: the field no longer exists on the wire at all.

**Decision:** The "inside observer" — the ambient display that lives in the house and
shows what the garden station is hearing — is firmware for a **DIYmalls / Sunton
ESP32-2432S028R** ("Cheap Yellow Display"), written against PlatformIO with every
dependency pinned, at `firmware/inside-observer/`. It reads the station over
**HTTP polling** of the existing read-only REST API, not MQTT. It renders species
names and local times only: **no score, no percentage and no confidence figure of
any kind reaches the glass**, and bat passes are never given a species name.

### Why this device

The board was already in the house running *Aura*, a weather-forecast display
project it was originally built for, and the
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

> **Status 2026-08-08:** that premise no longer holds — ADR-025's MQTT publisher
> shipped later the same day and is live against the operator's real broker. The
> *decision* stands unchanged: the display still polls HTTP, `StationSource`
> remains the seam an `MqttStationSource` would drop into, and the broker settings
> are already persisted in NVS so switching the feed needs no reprovisioning.
> Nothing about this ADR needs undoing; only this paragraph's premise is dated.

The REST API, by contrast, is live, read-only, documented and already carries
everything the display needs — including the `include_synthetic` exclusion of ADR-020,
which matters here more than anywhere: a counter-top display is exactly the "browsing view"
that must not present a test scene as an observation.

Polling is therefore an interim transport, not a rejection of MQTT. `StationSource` is
an abstract seam with one implementation today; another implementation satisfies the
same interface without the UI changing. (ADR-038 is what that seam was for: a
`PushStationSource` was dropped in alongside this one. It did **not** turn out to be
MQTT — the broker lives on the Home Assistant box, and making two devices that are
the same system depend on a third is the opposite of local-first.) The broker settings (host, port, credentials,
topic prefix) are already in the settings model, already in the provisioning portal,
and already persisted, so the operator's configuration survives the firmware update
that switches the feed over.

One consequence worth recording: the display has no clock and no IANA timezone
database, and does not run NTP. It gets the station's true local offset — DST included
— from `range.start_utc` of the `today` window in `GET /api/v1/history`, which is the
station's own local midnight expressed in UTC. UTC internally, local for presentation,
with the station remaining the single authority on what local means. **ADR-038
removed the need for this on the primary path**: elapsed times need no timezone at
all, only a monotonic base and an epoch anchor, and the derived offset is now unused
by the feed.

### Why no numeric score is displayed

**A BirdNET score is not a calibrated probability.** `detector.calibrated` is `false`
and `calibrated_probability` is `null` on every row the station emits. Rendering 0.92
as "92%" would state a confidence that the identification is correct, which is not what
the number means and not something this system can currently claim. The normaliser
already refuses to let a non-taxonomic detector emit a species name; this is the same
rule carried to the presentation layer, where the misreading actually happens — nobody
misreads a JSON field, but everybody misreads a percentage on a counter top.

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
mode switch was moved [date corrected 2026-08-09: **2026-08-08**, matching ADR-020
and `TARGET_DIAGNOSTICS.md`; this ADR said 2026-08-05] the station fell back to a synthetic source,
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

> **Status 2026-08-08: superseded by ADR-035.** An Alembic environment now
> exists, and `audio_stream.last_frame_at_utc` is included in the `0001_initial`
> baseline. The `ALTER TABLE` patcher is deliberately retained for now; see
> ADR-035 for why, and for what still has to change before it can be retired.


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

> **What that old method had already done, found 2026-08-10 (ADR-057).**
> Between 2026-08-05 and 2026-08-08 `enforce_retention` unlinked 8,166 clips
> (20.84 GB) to stay inside its 20 GB budget and marked not one row, leaving
> 8,067 `media_asset` rows asserting evidence that no longer exists. "Unused by
> the running station" was true and was not enough: nothing ever checked whether
> the rows and the disk still agreed, so the divergence was invisible for five
> days. `RetentionSweeper.audit_missing_files()` is now that check.

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

> **Status 2026-08-08.** Two later changes amend this ADR without replacing it.
> **ADR-035** built the Alembic environment: both `media_asset` columns are in the
> `0001_initial` baseline, and revision `0002` adds the `reclaimed_at` index that
> `ALTER TABLE ADD COLUMN` could never have created. **ADR-033** paced the sweep:
> it runs every `retention_interval_s` (default 300 s), not on every housekeeping
> tick, because a ~0.30 s ORM sweep holds the GIL and starved the event loop. The
> tiers, thresholds and "detection metadata is kept forever" rule are unchanged.


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
useful side effect that the API, the MQTT publisher and the ESP32 counter-top display
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

**What this did not fix, and left open — now CLOSED by ADR-039 (2026-08-09).** The
paragraph below is kept as written because it is the diagnosis ADR-039 acts on. It
is no longer an open item: the estimator now confirms a deficit step against the
following blocks before crediting it, and `reason=overrun` is no longer attached to
an event ALSA never reported. The stall did not lose
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
(`ssh <user>@<station-host>`, then `scp` the file — never opened for writing).
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

**Done, by ADR-042 (2026-08-09):** `deploy/deploy.sh` now runs `alembic
upgrade head` as an explicit step before every restart, and `api/app.py` /
`cli.py` call `ensure_schema_at_head()` instead of `create_all()`. The
`_patch_sqlite_columns` ALTER TABLE patcher this ADR deliberately kept is
deleted. See ADR-042 for why the migration itself runs in the deploy script
rather than application startup, and for the idempotency/data-safety
verification against a copy of the live station's database.

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
anyway would need a physical USB reflash of a device sitting on the
operator's counter top. Two paths were available: leave those two endpoints
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

> **Status 2026-08-08: that follow-up landed.** Revision
> `0003_auth_tables` adds `user`, `auth_session` and `api_token`. The live
> station reports `0003_auth_tables (head)`.


## ADR-037: Detection-table growth is real but slow; prune the five indexes nothing reads, and defer the rest

**Status: options B and C Accepted and implemented, 2026-08-09. Options A and
D–K remain open, exactly as originally proposed — the operator has not chosen
among them and this update does not relitigate them.** No schema change, no
migration, no index drop and no model edit was made for this ADR *when it was
first written*; the original research below is preserved verbatim except for
this status line and the title. What was actually built, and how it differs
from the research's predictions, is recorded in **"B and C: what was
implemented"** at the end of this entry — read that section for the current
truth about the schema; everything above it is the research that led there.
Every number below was measured on **2026-08-09** against a
read-only copy of the live station's `openobservatory.sqlite`
(`ssh <user>@<station-host>`, `scp`, opened `mode=ro` or on a local copy; the
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

---

### B and C: what was implemented (2026-08-09)

**Options A and D–K above are unchanged and still open** — this section
covers only B and C, which the operator accepted. Both were re-verified
against `main` as it stood on 2026-08-09 (which had moved since the research
above was written — in particular, ADR-032's `plausibility_repair.py` did not
exist yet when the original indexes-nothing-reads grep was run) and against a
fresh read-only copy of the live database (63,234 detections, up from 61,453),
not merely re-trusted from the research above.

#### B: four indexes dropped, one kept after re-verification found it in use

Re-running the two-part check (`EXPLAIN QUERY PLAN` over every query in
`history.py`, `api/app.py`, `plausibility_repair.py`, `retention.py`,
`station.py`, `mqtt/publisher.py`, `mqtt/discovery.py`, plus a grep for any
filter/order-by touching the five candidate columns) turned up exactly the
case this ADR asked a successor to watch for:
`plausibility_repair.reconcile_plausibility` (ADR-032, landed on `main` after
this ADR's research) joins **from** `detector` (filtered by
`Detector.plugin_id == BIRDNET_PLUGIN_ID`) **into** `detection`, and
`EXPLAIN QUERY PLAN` against the live database shows SQLite satisfies that
join with `SEARCH detection USING INDEX ix_detection_detector_id` — not the
`detector` table's own primary key, and not a full scan of `detection`. That
is the reverse join direction from the one the original research checked (it
looked for filters *on* `Detection.detector_id`, which indeed does not exist
anywhere; it did not anticipate a filter on `Detector.plugin_id` driving the
join the other way). **`ix_detection_detector_id` is kept.**

The other four were reconfirmed dead by the same method and dropped:

| Index | Live DB size | Re-verified dead because |
|---|---:|---|
| `ix_detection_station_start` | 4.71 MB | No query filters or orders by `station_id`; one distinct value in 63,234 rows |
| `ix_detection_station_id` | 2.85 MB | Same column, same finding, duplicates the composite above |
| `ix_detection_taxonomic_group` | 1.44 MB | Strict prefix of `ix_detection_group_start`; `EXPLAIN QUERY PLAN` on a bare `taxonomic_group = ?` filter still chooses `ix_detection_group_start` |
| `ix_detection_canonical_taxon_id` | 0.76 MB | Only ever appears in a `SELECT` list (`retention.py`'s exemplar scan reads it into Python after the query runs), never in `WHERE` or `ORDER BY` |

**9.76 MB dropped, not the originally estimated 12.49 MB** — the difference
is entirely `ix_detection_detector_id` (2.73 MB), kept because it is in
active use. This is a smaller number than the research predicted, for exactly
the reason the research itself flagged as the risk to watch.

Implemented as Alembic revision `0004_drop_dead_detection_indexes`
(`alembic/versions/20260809_0003_000000000004_drop_dead_detection_indexes.py`
— ADR-035's baseline used `0001`–`0003`, so `0004` is the next free number;
the `0003` this ADR originally said to use had already been taken by the
authentication tables). Plain `DROP INDEX` / `CREATE INDEX`, following
revision `0002`'s precedent — no batch mode needed for a bare index change on
SQLite, and both statements are standard SQL on PostgreSQL 16 too (ADR-007).
`alembic check` reports no drift after upgrading to head. `downgrade -1`
recreates all four indexes in one command;
`tests/test_migrations.py::test_0004_drops_and_restores_the_four_dead_detection_indexes`
asserts both directions, and the existing
`test_initial_revision_matches_create_all` / `test_upgrade_head_from_empty_matches_create_all_tables`
tests independently confirm no drift between `create_all()` and `alembic
upgrade head`. `db/models.py`'s `Detection.station_id`, `.canonical_taxon_id`
and `.taxonomic_group` columns had their `index=True` removed, and
`ix_detection_station_start` was removed from `__table_args__`, to keep
`create_all()` and Alembic in agreement. `detector_id` keeps `index=True`,
annotated in the model with why.

**Measured insert/WAL cost was not re-benchmarked on real hardware this
session** — ADR-037's original 44.7→13.4 µs/row and 17,355→13,162 WAL B/row
figures were measured with all five indexes dropped, not four; dropping four
instead of five should land close to but not exactly on that number, and the
actual figure needs a fresh benchmark (ideally during the deploy, per the
scheduling note below, since the soak is "the ideal place to confirm the
insert-cost improvement on real hardware" the original research already
named).

#### C: `native_result` stripped of provably redundant keys, not the full ADR-predicted set

`normaliser.py` now calls `_strip_redundant_native_result` before persisting
`native_result` (wired into `Normaliser.normalise`). It drops a key only when
its **value**, not merely its name, is proven to duplicate something already
persisted elsewhere on the same row:

| Key | Duplicates | Verified on live data (63,234 rows) |
|---|---|---|
| `detector` | `Detector.plugin_id`, reachable via `detection.detector_id` | 0 mismatches across all 3 plugins |
| `model_id` | `Detector.model_id`, same join | 0 mismatches, 7,976/7,976 birdnet-v2.4 rows that carry the key |
| `label` | `Detection.detector_label` | 0 mismatches, 7,976/7,976 birdnet-v2.4 rows |
| `confidence` | `Detection.score` | matches to float32 rounding (≤ ~5e-7) on 7,976/7,976 birdnet-v2.4 rows; compared with `math.isclose(rel_tol=1e-4, abs_tol=1e-6)` |
| `peak_frequency_hz` | `Detection.peak_frequency_hz` | matches to the native result's own 1 dp rounding (≤ 0.05 Hz observed) on 49,726/49,726 activity-v1 rows; compared with `math.isclose(rel_tol=1e-3, abs_tol=0.1)` |

**`occurrence_probability` and `plausibility_band` are never touched** — they
are ADR-032's audit trail for why a candidate was or was not admitted, not a
duplicate of anything, exactly as this work was instructed to preserve.

**This is a materially smaller set than the original research proposed, and
the reason is a real finding, not caution for its own sake.** The research
above listed `model_id`, `score_definition`, `confidence_definition`,
`band_hz` and `hint_is_not_identification` together as "constant per detector
version" and safe to drop. Re-querying the live database found that is true
for `model_id` (persisted on the `Detector` row, so it is safe regardless)
but **false for `score_definition`**: `activity-v1`'s `native_result` carries
two distinct `score_definition` strings —
`"clamp((snr_db - min_snr_db) / 25 dB, 0, 1)"` on 5,010 rows from
2026-08-04T18:44–19:26, and `"clamp((snr_db - min_snr_db) / 30 dB, 0, 1)"` on
44,716 rows from 2026-08-04T19:26 onward — **under the exact same
`detector_id`, with no `plugin_version` or `model_version` bump between
them.** `config.py` confirms why: `activity_band_hz` and
`ultrasonic_band_hz`, and by extension the `min_snr_db`-derived formula text,
are operator-configurable settings, not baked into the versioned model
identity, and `Detector.configuration` (which records only `stream_kind`,
`sample_rate`, `duration_s`, `stride_s`) does not capture them. So "the
detector version recorded on the row" — this work's own recoverability
requirement — **does not reliably reconstruct `score_definition`,
`confidence_definition`, `band_hz` or `hint_is_not_identification`** on this
station's real history: a successor reading only current source code for
`activity-v1`'s current `plugin_version` would recover the *current* formula
and silently misattribute it to rows written under the old one. Those four
keys are therefore **kept**, unconditionally, for every plugin — not just
where a second value was actually observed — because the failure mode (a
config change with no version bump) is structural, not specific to
`activity-v1`, and nothing rules it out for `birdnet-v2.4` or
`ultrasonic-pass-v1` in the future.

**Measured saving, real data, JSON-text bytes (`len(json.dumps(...,
separators=(",", ":")))`), before vs. after, weighted by the live row
counts per plugin:**

| Plugin | Rows | Before (avg B/row) | After (avg B/row) | Reduction |
|---|---:|---:|---:|---:|
| `activity-v1` | 49,726 | 314.1 | 262.1 | 16.6% |
| `birdnet-v2.4` | 7,976 | 354.4 | 226.7 | 36.0% |
| `ultrasonic-pass-v1` | 5,532 | 513.9 | 481.9 | 6.2% |
| **All detections** | **63,234** | **336.6** | **276.8** | **17.8%** |

That is **17.8%, not the originally estimated 52%.** The gap is exactly the
keys withheld above: `score_definition`/`confidence_definition`/`band_hz`/
`hint_is_not_identification` were the largest byte contributors in the
original 356.6 B/row estimate, and none of them turned out to be safely
droppable on real data. `label_index`, `logit`, `week`,
`plausibility_band`, `threshold_applied`, `range_model_used`, `snr_db`,
`snr_statistic`, `spectral_centroid_hz`, `duration_ms`,
`noise_floor_db_median`, `pulse_count`, `median_peak_hz`, and the rest of
`ultrasonic-pass-v1`'s per-pulse measurements are not duplicates of anything
and were never candidates for removal. Applied to the whole database, 17.8%
of 21.9 MB (the measured `native_result` total from the original research,
now larger at 63,234 rows) is on the order of **3.9 MB today, growing at
roughly 17.8% of whatever `native_result` would otherwise cost per year** —
a real saving, materially smaller than "roughly 3.5 GB/yr" and worth stating
plainly rather than letting the original estimate stand uncorrected.

**Historical rows are untouched, as required.** `_strip_redundant_native_result`
only runs inside `Normaliser.normalise`, on the path that builds a new row;
nothing rewrites `native_result` on any of the 63,234 rows already in any
database. A successor reading an old row still sees every key it was written
with.

**Recovering a dropped key.** For `detector` and `model_id`: join
`detection.detector_id` to `detector.id` and read `plugin_id` /
`model_id` directly — this is exact, for every row, forever, because those
are persisted database columns, not code. For `label`: read
`detection.detector_label`. For `confidence`: read `detection.score` (exact
value; `native_result`'s copy was only ever a rounded display value in the
first place, so nothing is lost by using the typed column instead). For
`peak_frequency_hz`: read `detection.peak_frequency_hz` (same — the typed
column has strictly *more* precision than the dropped copy did).

Tests: `tests/test_pipeline.py::TestNormaliser` —
`test_native_result_drops_keys_that_duplicate_persisted_columns` (positive
case, plus asserting `occurrence_probability`/`plausibility_band`/an
unrelated key like `week` survive untouched),
`test_native_result_keeps_a_key_whose_value_does_not_actually_match` (a
same-named key with a different value is not a false-positive duplicate),
`test_native_result_peak_frequency_hz_duplicate_is_dropped_within_rounding`
(the rounding-tolerance case measured on real data), and
`test_native_result_keeps_configurable_and_formula_fields` (`band_hz` and
`score_definition` are never stripped — the regression test for the finding
above).

#### What is still required before this reaches the station

**Not deployed.** Per this session's rule, the Pi is owned by a concurrent
agent building a push channel and must not be restarted. Both changes are
committed on this branch only. **A deploy is required**, and per this ADR's
own scheduling note: land it **before or after** the operator's planned soak,
never during — a restart resets every counter the soak measures. The soak
remains the right moment to take the real insert/WAL measurement B's
implementation did not get to.

The full test suite (389 baseline + 5 new = 394 passed, 6 skipped),
`ruff check .` (clean) and `mypy src` (29 pre-existing errors, none added)
were run locally against a Python 3.12 venv; none of this was exercised
against PostgreSQL or on the Pi 5 itself.


## ADR-038: The inside observer is pushed to over a lean WebSocket, and shows elapsed times

**Decision:** Replace the counter-top display's HTTP polling (ADR-023) with a push
channel served by the station itself — a new WebSocket at `GET /api/v1/display`
carrying detections only, in compact JSON sized to fit a single Ethernet MTU
several times over. The display renders **elapsed** times ("4s ago", "1m ago",
"1h ago") that tick once a second off a monotonic base, rather than clock times.
HTTP polling stays in the firmware as a real, exercised fallback.

Measured on the live station: **49 bytes for a named detection, 40 for a bat
pass, 43 for the heartbeat, 294 for the connect snapshot with six real species
names** — a 90-second sample over Wi-Fi cost **1,030 bytes, 11.4 B/s**.

### Why polling had to go

Per 20 s poll cycle, the display cost the station:

| Request | Server time | Payload |
|---|---|---|
| `/api/v1/health` | 18 ms | 2.7 kB |
| `/api/v1/detections?limit=40&identified_only=true&min_score=0.75` | 140 ms | 71.4 kB |
| `/api/v1/detections?limit=8&group=bat` | 44 ms | 23.4 kB |
| `/api/v1/history?window=today` | 112 ms | 29.5 kB |
| **total** | **~315 ms** | **~127 kB** |

Forty detection records, ~1,785 bytes each, fetched to render six rows. The
device already threw away almost all of it — ADR-023's ArduinoJson stream filters
exist precisely because it could not afford to hold it — so the waste was never
the ESP32's, it was the Pi's.

Making the display *feel* live by shortening the poll was the obvious move and
the wrong one. 315 ms of query work every 3 s is ~3x the event-loop duty cycle
this station has already been shown not to absorb: ADR-033's retention sweep
doing 0.3–0.4 s of synchronous work every 10 s starved the loop by 55–150 ms and
produced ~1.9 capture gaps a minute. Capture always wins, so the answer was to
stop asking rather than to ask harder. **11.4 B/s and one connection replaces
~6,350 B/s and 720 requests an hour** — and the display is now *more* live, not
less, because a detection reaches the glass when it happens instead of up to 20 s
later.

### Why a new endpoint rather than a mode of `/api/v1/live`

`/api/v1/live` is the debug UI's visual channel and almost none of its vocabulary
is usable here: binary spectrogram columns an ESP32 has no screen for, a `hello`
carrying forty full detection records and sixty events, a full station snapshot
every two seconds, and a 30-second spectrogram backfill on connect. A "filtered
subscription" would have had to replace every frame that socket sends — which is
not a filter, it is a different channel sharing a URL.

It would also have meant editing the one socket ADR-012 warns hardest about. That
bug — concurrent writers, flawless on loopback, near-total failure over Wi-Fi —
cost more to find than anything else in this project, and ADR-012's constraint is
that any change to those channels must be re-measured from a real browser over
the real network before it is believed. A separate endpoint leaves the debug UI's
transport bit-for-bit untouched and lets this one be exactly as small as it needs
to be. **The single-writer rule is not relaxed:** `DisplayClient.run()` is the
only code in the process that writes a display socket; the pump and the receive
loop only ever call `offer()` and `receive_text()`.

### Why not MQTT, when the publisher now exists

ADR-025 shipped the MQTT publisher, so routing the display through it was
available. It was rejected: the broker runs on the Home Assistant box, and the
station and the display are the same system in the same house. Publishing garden
detections to a third machine so a device on the same LAN can read them back
makes the counter-top display depend on a box neither of them needs, and CLAUDE.md's
local-first rule is not "cloud-free", it is "core function needs nothing else
running". The display talks to the Pi. The `MqttSettings` struct stays in NVS —
unused, and now documented as such.

### The wire format, and what is deliberately absent

Three frame types, compact JSON, whitespace stripped, keys of one or two
characters. Full field table in `docs/api/DEBUG_UI_TRANSPORT.md`.

```
hello       {"t":"h","v":1,"now":1786263065,"hb":10,"st":"L","sp":30,
             "f":[{"n":"Common Woodpigeon","at":1786263056,"r":5}, ...]}
detection   {"t":"d","n":"Common Woodpigeon","at":1786263086}
heartbeat   {"t":"s","now":1786263075,"st":"L","sp":30}
```

What is **not** on this wire, and cannot be added without editing both
`display_channel.py` and `push_frame.h`: `native_result`, the media list with its
checksums and byte lengths, every UUID, detector plugin/model/licence metadata,
`rank`, `canonical_taxon_id`, `duration_s`, frame bounds, `stream_id` — and
**`score`**. ADR-023's rule that no number a person could read as a confidence
figure reaches the glass is now structural rather than behavioural: the threshold
is applied by the station, expressed in the URL the socket was opened with, and
the number never leaves the Pi. A bat pass carries no name at all, only `b:1` and
a peak frequency; the words "Bat pass" are supplied by the firmware, so no future
server change can put a species on a pass. `title_hint`'s frequency-band candidate
("45 kHz · common pipistrelle?") is deliberately *not* forwarded — it is a
legitimate hint in a browsing UI that can carry the sentence explaining it, and a
species claim on a counter top.

Server-side filtering is the point, not an optimisation. The device sends its
threshold and its bat switch as query parameters and receives nothing it would
discard. Changing either setting reconnects, because the filter lives in the URL;
the firmware restarts on Save to make that unambiguous.

**Snapshot then deltas.** A display that connects at four in the afternoon must
not be blank, so connect costs one short, column-limited SQL read (six rows after
run-collapsing, plus one `DISTINCT` for today's species) run off the event loop —
once per connection, not once per 20 s. `species today` is then tracked
incrementally in a set: a species counts exactly when one of its detections
cleared the threshold, which is exactly when a frame was sent, so the number stays
identical to what the 112 ms history query returned, without the query.

**Bounded, with an explicit drop policy**, like every queue here: 64 frames, and
the *oldest detection* is shed first. Never a status frame — losing the banner to
a burst of woodpigeons would make a broken station look merely quiet, which is the
exact failure ADR-023 exists to prevent. Counters surface in `/api/v1/station`
under `display_channel`, including `mean_frame_bytes`, so this ADR's headline
number is checkable at any time rather than only at the moment it was written.

### Why elapsed times, and what happens past a day

Clock times answer a question nobody asks an ambient object. "21:04" requires the
reader to know what time it is and do arithmetic; "4s ago" answers *is this
happening now?* directly, which is the only question a glance is asking. Ticking
matters as much as the format: without it, a screen showing "12s ago" is lying
within a second of being painted.

Thresholds and rounding, chosen and stated: `< 1 s` reads "now"; seconds to 59;
minutes to 59; hours to 23; then days, saturating at "99d+ ago". Rounding is
**floor** throughout, so "1m ago" means at least a minute and less than two — the
weaker claim, and the one that makes the second-by-second count read as a count
rather than as a value jittering across a boundary. A **negative** age renders as
"now", not "-1s ago": the anchor is only re-taken on a heartbeat so fractional
negatives are routine, and a minus sign on a counter top reads as a fault.

Past a day the unit deliberately stops getting coarser. Weeks and months were
considered and rejected: a feed row older than a day means the garden has been
silent for a day, which is a fault report rather than an observation, and "3d ago"
says that plainly where "last week" would soften it. The 99-day cap exists so the
string can never outgrow its column.

### The clock problem, and the trap avoided

This board has no RTC and does not run NTP. Before this change that did not
matter — the display asked the station when something happened and printed it, so
it never needed to know what time it was. An elapsed time needs to know every
second, and to keep knowing while the feed is down.

The trap: subtracting an event's timestamp from a wall clock that can jump. Anchor
directly to the station's epoch and an NTP step, a reconnect, or a late heartbeat
would move every row on the screen at once — rows ageing backwards, or leaping
forward by however far the clock moved. So `StationClock` counts from `millis()`,
which only ever advances at one second per second, and the station's `now` is used
solely to establish the offset between the two. The offset is re-taken only when
it is out by ≥ 2 s, because re-anchoring on every heartbeat would let sub-second
network jitter push a row back and forth across a whole-second boundary. Uptime
accumulates from *deltas*, so it survives the 32-bit `millis()` wrap at 49.7 days
— which an object left on a shelf actually reaches. All of it is host-tested,
including the wrap.

The HTTP fallback anchors from `checked_at` in `/api/v1/health`, which is
generated as the response is built and is the closest thing that transport has to
a "now".

### Why the tick does not redraw the screen

Repainting 240x320 once a second would flicker and would burn CPU on a device
whose job is to sit still. The elapsed time therefore gets a reserved,
fixed-width 72 px column at the left of each row's second line — fixed so the
detail that follows does not shuffle sideways as "9s ago" becomes "10s ago" — and
its own 72x18 sprite (2.6 kB, against the row sprite's 19 kB).
`tickRelativeTimes` compares each row's rendered string against what is on the
glass and pushes the small sprite only where the words changed. A row whose age is
measured in minutes costs nothing at all, 59 seconds out of 60. Species names are
on a separate cache key and are never repainted by the clock. Measured on the
device: **~60 partial repaints per minute** — one per second, for the one row that
is changing — with free heap flat at 213–215 kB.

One trap found while measuring this, worth recording because it hid the whole
mechanism: the socket service block runs every 10 ms, and calling `showFeed` from
it unconditionally meant the ages were in fact being updated there, at 100 Hz,
with the one-second tick finding nothing left to do. It was invisible from the
glass (the output is identical) and visible only in the repaint counter, which is
why the counter is now permanent and in the log. `showFeed` is called only when
the frame count or the station state actually moved.

### Honesty properties, all preserved

No score anywhere, now enforced by the absence of the field rather than by the
client's restraint. Bat passes always sent, never scored, never named. The three
distinct states survive: **degraded** carries the station's own words, mapped
server-side by the same decision tree the firmware runs for the fallback (so the
two transports cannot describe one station differently); **offline** is a fact
only the device can know, so the station never sends it. Detections are **not**
pushed at all while the station is not on the real microphone (ADR-020): the
banner explains why, and the feed does not quietly fill with a test scene.

**A stale feed must look stale, not merely quiet**, and on this device silence is
the normal state — the absence of detections proves nothing. Only the 10 s
heartbeat does. Three missed beats (30 s) puts the red rule and "STATION
UNREACHABLE" up and marks the surviving feed `(stale)` in the footer, while the
elapsed times keep counting, which is itself honest: a top row reading "23m ago"
in a garden that is usually noisy says something a blank screen would not.

### Consequences and what a successor should know

- The firmware's `use24hClock` setting is now vestigial. It survives in NVS (so no
  config migration is needed) but decides nothing, and its row on the settings
  page has been replaced with a read-only report of which transport is actually
  feeding the glass — because "it is running on the fallback" should be a visible
  fact, not a guess.
- Falling back is deliberately slow (60 s of a dead socket) so that a Wi-Fi hiccup
  or a station restart does not put 127 kB per 20 s straight back on the wire.
- `links2004/WebSockets@2.7.3` is a new pinned firmware dependency. It needs
  explicit `-I` paths to the framework's WiFi libraries: its network class is
  chosen by a macro chain the library dependency finder cannot evaluate, so LDF
  never records WiFi as a dependency *of that library* and it compiles without it
  on its include path. Recorded in `platformio.ini` where it bites.
- `/api/v1/display` is in `auth_public_read_paths` by default, for the same reason
  `/api/v1/detections` is: an ESP32 has no login flow. It is not a GET, so the
  HTTP gate never sees it; the WebSocket handler consults the same list so the
  display's two transports are exempt together or not at all.
- `history.is_live`/`is_not_live` were annotated `ColumnElement` while every call
  site passes an `InstrumentedAttribute`. Widened rather than silenced while
  adding the ninth call site; `mypy src` drops from 29 pre-existing errors to 22.
- Firmware cost: 1,122,736 bytes of the 3 MB app partition (was 962 kB) and 50,708
  bytes of static RAM (was 48.8 kB).
- **Not verified:** the 72-hour soak, and the display's behaviour across a
  multi-day silence — nothing has yet aged past a few hours on the real device, so
  the "1d ago" path is host-tested only. The Wi-Fi-loss path was not exercised on
  hardware either.


## ADR-039: A deficit step is only lost audio once it fails to come back

**Decision:** `AlsaSource`'s frame-deficit estimator no longer credits a timing
step as lost audio at the moment it sees it. A step larger than one block — or an
EPIPE from ALSA — opens a *suspicion*, which is held for `_confirm_frames`
(one ring plus two blocks; 268,800 frames, 0.7 s, on the target) while the lowest
deficit seen is tracked. The part of the step that never came back is what gets
credited to `estimated_missing_frames`; the part that did come back was never lost
and is not reported as a gap at all. Three consequences follow:

- `reason=overrun` is now attached only to an event ALSA actually reported. A
  confirmed loss the driver never raised EPIPE for is reported as the new
  `DiscontinuityReason.FRAME_DEFICIT`, because "the stream clock says frames are
  gone" and "the kernel ring overflowed" are different claims and the station was
  making the second when it only had evidence for the first.
- A late read that cost nothing is not a `capture.gap`. It increments a new
  `late_reads` counter, logs `capture.late_read` at info with the stall size, and
  records `late_read_max_frames` — which against `alsa_buffer_frames` is the
  ring headroom that no counter reported before.
- The verdict arrives a few blocks after the event, so `CaptureBlock` gained
  `discontinuity_at_frame` and the gap row and event now record where the loss
  happened rather than where it was confirmed.

An ALSA overrun that is confirmed to have cost nothing is still reported as a gap
(`gaps_without_loss`), because a ring that came within a hair of overflowing is not
nothing and ALSA is the authority on that. An ALSA overrun that did cost audio
still has its cost estimated — that is ADR-030's regression and it is asserted by
name in `tests/test_alsa_source.py::test_a_genuine_overrun_still_has_its_cost_estimated`.

**Reason — the estimator was answering a question the ring had already made
obsolete.** Against the 80 ms ring the station shipped with, a read that arrived
more than a block late really had lost audio: there was nowhere for the frames to
wait. ADR-030 widened the ring to 500 ms precisely so that stalls would be
absorbed, and the estimator was not revisited, so every absorbed stall minted a
phantom gap. Measured on the live station, mid-regression on 2026-08-08:
`frames` 92,505,600 against `expected_frames` 92,526,900 — a real deficit of
21,300 frames (0.055 s) — while `estimated_missing_frames` claimed 252,495
(0.657 s) and ALSA's own `overruns` counter sat at **0**. Over 11.9 h the same
comparison was 4.06 s real against 52.4 s claimed, **12.9x**.

**The second number it contaminated.** Phantom frames are added back into
`presented` in the observed-rate calculation, so the station read
`rate_offset_ppm` of +2,680 to +3,600 against a device whose true crystal offset
is about **-43 ppm** (`TARGET_DIAGNOSTICS.md`). 252,495/92,505,600 = 2,729 ppm,
which is the whole of the error. No separate fix was needed for this and none was
made: crediting only confirmed loss fixes it as a consequence, which is the
measurement below.

**Why confirmation rather than a rule about ring depth.** "Only credit a step
larger than the ring" would also work for the absorbed-stall case and is simpler,
but it is a rule about the mechanism rather than a measurement of the property,
and it fails the case where the ring overflows by a little: a 520 ms stall behind
a 500 ms ring loses 20 ms, which such a rule would round to nothing. Waiting for
the deficit to settle measures what was actually lost in every case, and it makes
`estimated_missing_frames` a *decomposition of* `expected_frames - frames` rather
than a second, independent, disagreeing estimate. That reconciliation is the
property asserted by
`tests/test_alsa_source.py::test_estimated_missing_frames_agrees_with_the_frame_deficit`.

**Cost:** a gap is reported up to 0.7 s after it happened, and a loss smaller than
one ALSA period (3,840 frames, 10 ms) is absorbed into the drift baseline rather
than credited — so the estimate can under-report by up to 10 ms per event. Both
are stated rather than discovered later. Nothing about detection latency, live
audio or window dispatch changes: the estimator does not gate the data path.

**Measured, off-target, against a device that drops frames on cue.** The
instrument is `RingedDevice` in `tests/test_alsa_source.py`: a fake capture device
with a real kernel-style ring that produces frames at its own crystal rate, holds
them, drops what will not fit, and raises the same `Input/output error` ALSA does.
`device.dropped` is therefore ground truth injected by the test, not another
estimate. Same trace, same device, old estimator against new:

*Eight stalls of 250-400 ms behind a 500 ms ring — the live station's exact
signature, where nothing is lost:*

| | Old | New | Truth |
|---|---|---|---|
| Frames the device dropped | — | — | **0** |
| `estimated_missing_frames` | 259,596 (0.676 s) | **0** | 0 |
| `expected_frames - frames` | 741 | 741 | — |
| `gaps_with_loss` | 5 | **0** | — |
| `late_reads` | *(did not exist)* | 8 | 8 |
| ALSA `overruns` | 0 | 0 | 0 |
| `rate_offset_ppm` | **+15,013** | **-43.0** | -43.0 |

The old column reproduces the live defect closely: 259,596 phantom frames against
the station's 252,495, with the device having lost nothing and ALSA having said
nothing.

*Ten stalls of 150-1200 ms, so the ring genuinely overflows twice:*

| | Old | New | Truth |
|---|---|---|---|
| Frames the device dropped | — | — | **422,365** |
| `estimated_missing_frames` | 779,406 | **422,444** | 422,365 |
| Error against truth | +84.5% | **+0.019%** | — |
| `expected_frames - frames` | 423,289 | 423,289 | — |
| Estimate vs deficit | disagrees by 356,117 | **agrees to 845 frames (2.2 ms)** | — |
| `gaps_with_loss` | 5 | **2** | 2 |
| `rate_offset_ppm` | +16,560 | **-39.3** | -43.0 |

**Corroborated on the live station, read-only, 2026-08-09 08:43Z**, on a build that
does *not* yet contain this change — this is the defect still running, not the fix
verified:

| `frames` | `expected_frames` | Real deficit | `estimated_missing_frames` | `overruns` | `rate_offset_ppm` |
|---|---|---|---|---|---|
| 376,089,600 | 376,133,372 | 43,772 (0.114 s) | 348,786 (0.908 s) | **0** | **+878** |

An **8.0x** over-report over 16 minutes, all seven gaps labelled as having lost
audio with ALSA reporting no overrun at all, and 348,786/376,089,600 = 927 ppm
against an observed +878 with a true crystal offset of -43 — 927 - 43 = 884, which
is the observed figure. The arithmetic of the contamination is exact.

**What is not verified.** This change has **not been deployed to the Pi** and no
on-target before/after exists, because another agent owned the station this
session. The reproduction commands are in `OPEN_INVESTIGATION_CAPTURE_GAPS.md`.
Everything above is either an off-target measurement against a simulated device or
a read-only reading of the *unfixed* station.

## ADR-040: The live view's pictures are drawn only while somebody is looking

**Decision:** `Station._handle_block` gates both spectrogram encoders on there
being a live viewer, exactly as it already gated the heterodyne. Two settings
control it: `spectrogram_encode_min_viewers` (default **1**; `0` restores the
old always-on behaviour) and `spectrogram_keep_audible_warm` (default
**false**). The API layer supplies the count via
`Station.set_spectrogram_consumer_count(lambda: hub.count)` — the *live* hub, not
the display clients, because the counter-top display has no canvas and would never make
the work worth doing.

Because encoding stops, the retained history is discarded the moment the gate
closes (`SpectrogramEncoder.reset(clear_history=True)`), and the station now
publishes `viewer_gated` and `history_seconds` per channel so the UI can label a
deliberately blank canvas as *filling* rather than let it read as failure.

**Reason — the steady state of this station is nobody watching.** The operator's
framing is that the counter-top display is the first-class surface and "first class BAU
experience is no web browser open"; the web UI must be fully functional while it
is open, but it is not expected to be open. Work done for an absent browser is
therefore not merely inefficient, it is charged against the event loop whose
stalls ADR-033 showed produce capture gaps. The heterodyne already carried this
reasoning in a comment — "continuously heterodyning 384 kHz for nobody would
waste real CPU on a device that must never be starved of it" — and the
spectrograms simply had not been held to it.

**Measured on the live station, 2026-08-09**, five-minute windows, AudioMoth at
384 kHz, counter-top display connected throughout, `hot_path_cpu_ratio` differenced
across each window rather than read cumulatively:

| Window | Build | Live sockets | Encoders | `hot_path_cpu_ratio` | loop-lag events/min | `gaps_with_loss` |
|---|---|---|---|---|---|---|
| 1 browser | main | 1 | on | **0.1067** | 2.75 | 0 |
| 2 browsers | main | 2 | on | **0.1066** | 2.20 | 0 |
| 2 browsers | ADR-040 | 2 | on | **0.1060** | 2.20 | 0 |
| gate held shut | ADR-040 | 2 | **off** | **0.0159** | 2.00 | 0 |

**The saving is 0.0901 of a core per second of audio — 85% of the per-block hot
path — and it is paid in the state the station is in almost all the time.**
Whole-process CPU with encoders running measures 23.7% of one core's worth on a
four-core Pi.

**The second measurement, which changed the design.** The brief expected the
ultrasonic encoder to dominate: FFT 4096, four sub-windows per hop, ~167 FFTs a
second against the audible channel's ~42 of size 2048. On the target it does not
(`scripts/bench_spectrogram.py`, run on the Pi):

| Encoder | ms per 100 ms block | cpu_ratio |
|---|---|---|
| audible (48 kHz, FFT 2048, 192 bins) | 2.611 | 0.0261 |
| ultrasonic (384 kHz, FFT 4096, 128 bins) | 2.931 | 0.0293 |

Eight times the FFT work for 12% more time, because the FFT is not the expensive
part — the int16→float conversion of a whole block and the per-column max
reduction over 192 log-spaced bins are. So the proposed asymmetry, "keep the
cheap audible one warm and gate only the expensive one", would have bought back
half the history for 47% of the saving. It is implemented and configurable
(`spectrogram_keep_audible_warm`) because the trade is a matter of the operator's
taste, but it is **off** by default: on the evidence there is no cheap channel.

**Why the in-station saving (0.0901) exceeds the bench figure (0.0554).**
`hot_path_seconds` is wall time on the event-loop thread, not CPU time, so it
includes time the thread spent descheduled mid-block. That is not an error to
correct for: the quantity ADR-033 cares about is exactly how long the loop is
unavailable to issue the next ALSA read, and that is what this measures. The
bench measures pure CPU in an uncontended process, and the gap between the two is
the contention the encoders were adding.

**The tension this had to resolve, and did not get to dodge.** `LiveHub`'s
docstring argues snapshot-on-connect matters because "a viewer opening the page
mid-flight would otherwise stare at an empty canvas for a minute, which looks
exactly like a broken pipeline". Gating makes that blank canvas *normal*: with no
encoding there is no history, so a browser opens empty and fills over ~30 s.
Re-introducing the confusion the original design existed to prevent was not
acceptable, so three things are true instead:

- The history is discarded when the gate **closes**, not when it reopens, so the
  invariant is simply "whatever an encoder holds is contiguous and recent". A
  client connecting during an idle period finds nothing to back-fill, rather than
  finding hour-old columns that `history_frame` would date as the last thirty
  seconds. Serving those would not be unhelpful, it would be the pipeline lying
  about what it heard and when.
- The station says which channels are gated and how much they hold, so the UI can
  distinguish "deliberately empty" from "broken" without inferring it.
- The canvas carries the sentence *"filling · this view starts when you open it.
  Detections are recorded continuously."* until it has the selected window of
  data. A brief honest label costs nothing, and it is the whole difference
  between the two states.

  **Corrected 2026-08-09.** The label first read *"history is recorded only
  while the live view is open"*, and the operator immediately read it as the
  station having stopped recording. It had not: detections, evidence clips and
  capture coverage are written continuously and are the durable record; what is
  gated is only this picture, drawn from a memory-only ring that was never
  persisted whether or not a browser was open. Conflating "the spectrogram
  image" with "the history" is precisely the sincere, believable, wrong
  statement the charter's honesty constraint exists to catch, and it survived
  review, a test and an ADR before a human read it in situ.

A second viewer joining a watched station still gets the full backfill: nothing
about snapshot-on-connect is removed, it simply has nothing to send when nothing
has been recorded.

**Why not encode at a reduced rate while idle**, keeping coarse history for
nothing much? Because a canvas whose columns are one second apart on the left and
24 ms apart on the right is a time-warped picture presented as a spectrogram,
which is a worse failure than a blank one. Blank is honest.

**The display is not collateral damage, and this was measured rather than
assumed.** ADR-038's channel and the debug UI's live channel share one process
and one event loop, so a browser connecting *could* have cost the first-class
surface. Ninety-second windows on the live station either side of a real browser
connecting over Wi-Fi:

| | Frames to display | Dropped | Queue depth | Mean frame bytes |
|---|---|---|---|---|
| one browser | 10 | **0** | 0 | 54.9 |
| second browser connects | 9 | **0** | 0 | 51.4 |

`display_channel.per_client` reported zero drops and a zero queue in every window
of this session, including the two five-minute windows with encoders running.
No change to either transport was made, because the evidence did not ask for one:
ADR-012's single-writer rule and ADR-038's separate endpoint are what already
make this hold, and restructuring them speculatively would have put the project's
most expensive bug back in play.

**Cost, stated rather than discovered later:** opening the live view now shows an
empty, labelled canvas that fills over ~30 s, once, per idle period. `columns_emitted`
on an unwatched station is 0, which any future check must expect —
`tests/test_api.py::test_both_spectrogram_channels_exist_at_high_rate` used to
assert the opposite and now connects a viewer first.

**Not verified:** no 72-hour soak has run, and these are five-minute windows.
The saving is measured on the event loop's own clock; the effect on capture gaps
is inferred from ADR-033's mechanism rather than demonstrated, because zero gaps
occurred in any window here — before or after.

### Rollback and smoke test (ADR-040)

No schema change, no new dependency. The behaviour reverts with **no deploy**:
set `OO_SPECTROGRAM_ENCODE_MIN_VIEWERS=0` in `config/runtime.env` and restart,
which restores always-on encoding exactly. To revert the code, `git revert` the
commit; the UI's "filling" label is inert on an ungated station because
`viewer_gated` is then `false` and the label only appears while a canvas is
genuinely short of data.

Target smoke test — with no browser open, columns must not advance, and a
browser must start them within a couple of blocks:

```bash
curl -s http://<station-host>:8080/api/v1/station \
  | python3 -c 'import json,sys; print([(s["name"], s["columns_emitted"], s["viewer_gated"], s["history_seconds"]) for s in json.load(sys.stdin)["spectrograms"]])'
# then open http://<station-host>:8080/ and run it again: columns_emitted rises,
# history_seconds climbs towards spectrogram_backfill_s, and the canvas carries
# "filling ..." until it does.

python scripts/measure_live_cost.py --seconds 300 --label "browser open"
python scripts/bench_spectrogram.py     # run ON the Pi; laptop figures are not evidence
python scripts/watch_display_channel.py --seconds 90 --label "browser connecting"
```

## ADR-041: The ultrasonic spectrogram gets its own floor/ceiling, measured on the live station

**Decision:** `Settings` gains `ultrasonic_spectrogram_floor_db` (default
**-85.0**) and `ultrasonic_spectrogram_ceiling_db` (default **-30.0**),
independent of `spectrogram_floor_db`/`spectrogram_ceiling_db` (unchanged,
-95.0/-15.0, and still the audible channel's only range).
`Station._build_spectrograms` passes the new pair to the ultrasonic encoder
instead of the two-value literal (`-105.0`/`-25.0`) that had sat there,
unmeasured, since the channel was first built (`13c5ba6`). Both values are
published per-channel by `describe_spectrograms()` exactly as before, so the
UI's badges keep showing the mapping that is actually in effect rather than a
hidden constant.

**Symptom.** The operator: *"the ultrasonic spectrograph visual is suffering a
contrast problem at the low end where background noise is largely showing as
orange."* Confirmed from a station screenshot: the 15-45 kHz region rendered
saturated bright orange throughout, with the picture only falling back to dark
purple above ~50 kHz. The audible panel looked correct, so this was specific
to the ultrasonic channel, not the colour map (`web/src/components/Spectrogram.tsx`'s
`observatory` ramp is a standard perceptually-ordered inferno-style ramp,
unchanged by this ADR).

**Measured, not guessed.** The two channels were sharing one floor/ceiling
pair chosen for 48 kHz audio, and the ultrasonic channel's own hardcoded pair
(-105/-25) had never been checked against a real recording either. Rather than
pick new numbers by eye, `scripts/measure_ultrasonic_contrast.py` was written
and run against the live station (2026-08-09, AudioMoth capturing at 384 kHz,
30 s sample, a live-view WebSocket connection held open for the duration --
required, because ADR-040 gates the encoders on there being a viewer):

```
columns sampled: 1250 (~30.0 s)
current encoder range: floor=-105.0 ceiling=-25.0
15-45 kHz (bat band) (61 bins): p1=-72.1  p50=-66.7  p95=-61.1  p99=-59.2  max=-53.5 dBFS
>=50 kHz (quiet band) (61 bins): p1=-82.1  p50=-76.1  p95=-69.9  p99=-58.9  max=-55.1 dBFS
```

Against the old -105..-25 dB (span 80) ramp, that noise floor sat at roughly
**48%-58%** of the way up -- squarely inside the ramp's orange-to-yellow top
third (the `observatory` ramp turns orange at t~0.6-0.7) once ordinary
variance (p95, p99) is accounted for. That is the saturation the operator
saw, and it is daytime background: no bats, and the AudioMoth's gain is
documented as too hot (`HANDOVER.md` sec6.3 item 4, still unresolved -- it
needs a physical switch change nobody has made), so this noise floor is
higher than a well-gained capture would show and the display has to cope with
that rather than wait for the hardware fix.

**Numbers chosen from the measurement, not by eye:**

- **Floor -85.0 dB** -- roughly 3 dB below the lowest p1 observed across
  either band (-82.1, the quiet band above 50 kHz), so genuine quiet renders
  close to black rather than pinned at zero, and the floor is not so tight
  that a slightly quieter night clips into "digitally silent".
- **Ceiling -30.0 dB** -- the measured bat-band p50 (-66.7) plus 36 dB, chosen
  because `ultrasonic-pass-v1`'s own score already saturates to 1.0 at a peak
  SNR of noise-floor+36 dB (`detectors/ultrasonic.py`, the score expression
  `0.4*min(1, pulses/8) + 0.6*min(1, (peak_snr_db - 12)/24)` maxes its second
  term once `peak_snr_db - 12 >= 24`, i.e. `peak_snr_db >= 36`). A call the
  detector already calls "as strong as it gets" now reads as visually
  near-white, and the p50-p99 noise band (-66.7 to -59.2, roughly 33-51% up
  the new 55 dB span) stays in the ramp's dark purple/magenta lower half
  instead of its saturated top.
- The detector's own detection threshold (noise+12 dB, ~-54.7 here) lands
  around the ramp's 50-55% mark -- visibly brighter/redder than the ambient
  noise sitting below it, which is the contrast the operator asked for: *"a
  bat pass at 35 kHz should be clearly brighter than the noise around it."*

**Why not per-frame auto-scaling.** The charter's honesty constraint governs
this: the display is a scientific instrument, not a photograph, and levels
are dBFS relative to digital full scale, never calibrated SPL -- the UI
footer already says so and this change does not touch that wording. Per-frame
normalisation would make every night look identical regardless of how much
was actually happening, destroying the one thing the operator uses this
picture for -- telling a loud night from a quiet one. No adaptive behaviour
was added. The floor and ceiling are fixed constants, chosen from a real
distribution, and visible in the API exactly as `spectrogram_floor_db`/
`spectrogram_ceiling_db` already were -- there is no hidden auto-scale to
disclose because none was built.

**Verified in a real browser, 2026-08-09.** It is daytime; no live bat pass
was available, so the synthetic bat scene (`--source synthetic --scene
dawn-chorus`-equivalent ultrasonic content) was used against a second,
disposable station instance to confirm a strong ultrasonic transient renders
clearly brighter than the surrounding noise under the new range, while the
live station's actual noise floor (measured above) now renders in the dark
purple/magenta band rather than orange. Screenshots taken before and after.

**Not verified:** no 72-hour soak; the measurement is one 30 s daytime sample,
not a range across seasons, temperatures or the AudioMoth's eventual
lower-gain setting. If the hardware gain fix in HANDOVER.md sec6.3 item 4
lands, the noise floor will drop and these defaults should be re-measured --
they are a configured default an operator can override
(`OO_ULTRASONIC_SPECTROGRAM_FLOOR_DB` / `OO_ULTRASONIC_SPECTROGRAM_CEILING_DB`
in `config/runtime.env`), not a hardcoded fact about the hardware.

**And a confound learned after the fact (2026-08-09).** The microphone was, at
the time of this measurement, sitting next to a plant rubbing against a shed —
loud, periodic, mechanical noise that the operator intends to remove by moving
the microphone, not by changing any setting. The −85 dBFS floor here was
therefore derived from a noise floor that includes a temporary physical fault.

That does not invalidate the change: the noise the operator complained about was
saturating the ramp, and it no longer does. But **these numbers describe a
microphone in the wrong place.** When it moves, re-run
`scripts/measure_ultrasonic_contrast.py` and expect the floor to want lowering.
Do not treat −85/−30 as a characterisation of the hardware, and do not derive
any detector threshold from them in the meantime — see HANDOVER.md §6.3 item 4.

### Rollback and smoke test (ADR-041)

No schema change, no new dependency (`websockets` is already pinned). Revert
by setting `OO_ULTRASONIC_SPECTROGRAM_FLOOR_DB=-105` and
`OO_ULTRASONIC_SPECTROGRAM_CEILING_DB=-25` in `config/runtime.env` and
restarting -- no deploy needed -- or `git revert` the commit.

```bash
python scripts/measure_ultrasonic_contrast.py --host <station-host> --seconds 30
curl -s http://<station-host>:8080/api/v1/station \
  | python3 -c 'import json,sys; print([(s["name"], s["floor_db"], s["ceiling_db"]) for s in json.load(sys.stdin)["spectrograms"]])'
# expect [("audible", -95.0, -15.0), ("ultrasonic", -85.0, -30.0)]
# then open http://<station-host>:8080/ with the ultrasonic panel selected and
# confirm the noise floor reads dark rather than saturated orange.
```

## ADR-042: `alembic upgrade head` runs in `deploy/deploy.sh`, not application startup; `create_all()`/the ALTER TABLE patcher are retired from production use

**Decision:** `deploy/deploy.sh` runs `alembic upgrade head` against the
station as an explicit step — after syncing source and installing Python
dependencies, before installing/restarting the systemd unit. `api/app.py:
create_app()` and the three `cli.py` maintenance commands that touch the
database (`history reconcile-streams`, `detections reconcile-plausibility`,
`clips retention`) no longer call `db/session.py: create_all()`; they call a
new `db/session.py: ensure_schema_at_head()` instead. The old
`_patch_sqlite_columns` ALTER TABLE patcher is deleted outright, not just
unused. `create_all()` itself is kept, but only as a test helper (see
below) — it is no longer reachable from any production code path.

**Reason:** ADR-035 built a real Alembic migration environment but left it
decorative — nothing called `alembic upgrade head`, so `create_all()` and the
ALTER TABLE patcher in `db/session.py` remained the schema's actual authors
in practice. That is exactly the situation that let `media_asset.reclaimed_at`
ship on the live station with no index (ADR-035's own motivating gap): two
things describing the schema, free to disagree, with nothing checking that
they didn't. Wiring Alembic into the path that actually runs, and then
deleting the alternate path, is what closes that off for good rather than
adding a third description on top of the first two.

### Startup or deploy — the actual decision, and why

Two places could plausibly run `alembic upgrade head`: application/CLI
startup, or the deploy script. **This ADR puts it in `deploy/deploy.sh`, not
startup**, for a reason specific to this project rather than a general
preference:

- `CLAUDE.md` and the charter are explicit that **audio capture correctness
  outranks UI progress** and capture must have exactly one owning process.
  `oo serve` (`api/app.py: create_app()`) is the process that starts both the
  API and, through `Station`, the capture pipeline — there is no separate
  "control plane" process to isolate a slow migration from. A `CREATE TABLE`
  or batch-mode index rebuild embedded in that same startup path is squarely
  on capture's critical path: a slow migration (SQLite batch mode rebuilds
  the whole table on affected schema changes — `DATA_MODEL.md`'s "SQLite vs.
  PostgreSQL" section already flags this as measurably slower on a large
  table) or a failing one would delay or crash the very process that is
  supposed to start listening to the microphone.
- Deploying is already a distinct, operator-initiated step
  (`deploy/deploy.sh`) that syncs code, installs dependencies, and only then
  restarts the service. Running the migration there means a slow or failing
  migration is caught **before** the working service is touched at all:
  `set -euo pipefail` makes a failing `alembic upgrade head` abort the script
  before the systemd unit is reinstalled or restarted, so the previous,
  working version keeps running capture uninterrupted. A migration failure
  becomes a failed deploy an operator sees immediately, not a crash-looping
  service discovered later.
- This is also more predictable in the sense the task asks for: the DSN,
  the code version, and the database are all in a known, stationary state
  during a deploy (nothing else is racing to use the database), which is not
  true of "whatever moment the service happens to start" — a systemd restart
  after a crash, a reboot, or a manual `systemctl restart` for an unrelated
  reason would otherwise re-run a migration check against a running system
  for no reason connected to a code change.

The trade-off accepted: a developer or operator who starts the service
directly (`oo serve`) without having gone through `deploy/deploy.sh` first
does not get migrations run for them against an existing, out-of-date
database — they get a clear, actionable refusal instead (see
`ensure_schema_at_head()` below). That is judged better than the
alternative of quietly running unreviewed DDL on a process whose job is to
start capturing audio.

### What `ensure_schema_at_head()` actually does

Replacing `create_all()` with nothing was not viable: every test that spins
up `create_app()` or invokes a CLI command against a fresh `tmp_path` SQLite
file needs *some* schema-bootstrap to happen, and a fresh developer checkout
or a brand-new station needs the same thing on its very first run, before
any deploy has ever touched it. `ensure_schema_at_head()`
(`src/open_observatory/db/session.py`) draws the same distinction
`docs/data/DATA_MODEL.md`'s "Which case are you in?" already documented for
a human operator, and automates only the safe half of it:

- **A completely empty database** (no tables at all) is bootstrapped by
  running `alembic upgrade head` directly. Starting from nothing this *is*
  revision `0001_initial`'s `CREATE TABLE` — fast, and by construction
  identical to what `create_all()` used to build, which is exactly what
  `tests/test_migrations.py::test_upgrade_head_from_empty_matches_create_all_tables`
  asserts. This is a read (`PRAGMA`/catalog query to see there are zero
  tables) followed by DDL only in the case where there is nothing to lose —
  fine on both a test's throwaway file and a station's very first boot.
- **A database that already has tables** is never touched by DDL here. Its
  Alembic revision is read (a single fast query against `alembic_version`)
  and compared to the code's own migration head:
  - no `alembic_version` row at all → a pre-Alembic database that was never
    adopted (`create_all()`-built, or a database that predates ADR-035
    entirely) — raises, naming the exact adoption sequence
    (`alembic stamp 0001_initial && alembic upgrade head`).
  - `alembic_version` present but not equal to `head` → a migration is owed
    — raises, naming `alembic upgrade head` and noting `deploy/deploy.sh`
    normally does this automatically.
  - `alembic_version` present and equal to `head` → proceeds; this is the
    live station's case on every normal startup after a normal deploy, and
    it is a single read, not a migration run.

### Verification

- **Migrations vs. models agree at `head`, the most valuable check in this
  work:** `tests/test_migrations.py::test_initial_revision_matches_create_all`
  already existed from ADR-035 and still passes with all four revisions
  applied — stamping a `create_all()`-built database at `head` and running
  `alembic check` reports no drift. This is re-run on every test invocation,
  not a one-time claim: models and migrations are not free to diverge again
  without a test failing first.
- **`ensure_schema_at_head()` itself** is covered by four new cases in
  `tests/test_migrations.py`: bootstrapping a genuinely empty database;
  idempotency at `head` against a database seeded with 2,000 detection rows
  (run twice, row count and revision unchanged both times); refusing an
  unstamped `create_all()`-built database without touching its data;
  refusing a database stamped at an old revision (`0001_initial` while
  `head` is `0004`).
- **Idempotency and safety against the live station's actual data:**
  verified against a fresh backup of the live station's real database
  (`sqlite3`-equivalent hot backup via Python's `sqlite3.Connection.backup`,
  taken over SSH, never opened for writing on the station itself — the
  original file was never touched). The copy holds 65,515 `detection` rows
  and 28,183 `media_asset` rows. `alembic current` on the copy already
  reports `0004_drop_dead_detection_indexes (head)` — the live station is
  current. Running `alembic upgrade head` against the copy is a true no-op
  (no DDL emitted, confirmed by `alembic check` reporting no drift
  afterward), and running it a second time changes nothing further; row
  counts for both tables are unchanged throughout. This was run against the
  copy only — the live database was never opened for writing during this
  work.
- Full test suite, `ruff check .`, and `mypy src` all pass (the two `mypy`
  findings that remain — `tests/test_migrations.py`'s pre-existing set-
  comprehension typing note and `cli.py:211`'s unrelated `CaptureBlock |
  None` narrowing — both predate this change and are untouched by it;
  `docs/development/SETUP.md`'s "NOT clean" trap already documents that
  `mypy src` has pre-existing findings).

### What changes for an operator or a developer

- **Deploying the station:** unchanged in practice — `./deploy/deploy.sh`
  now migrates automatically as part of the same command. Nothing new to
  run by hand for a normal deploy.
- **A fresh developer checkout:** unchanged — `oo serve` (or any `oo`
  command) against a database that does not exist yet still works with no
  extra step, because `ensure_schema_at_head()` bootstraps it.
- **A developer with an old, pre-Alembic SQLite file** (built before this
  work, or before ADR-035): the next `oo serve` now refuses to start,
  with the exact adoption command in the error message, instead of silently
  patching columns in with `ALTER TABLE`.
- **Adding a new column or table:** unchanged from ADR-035/`DATA_MODEL.md`
  — write an Alembic revision, not a model change relying on the patcher,
  which no longer exists to fall back on.

### Rollback

**If the migration step in `deploy/deploy.sh` fails:** `set -euo pipefail`
stops the script before the systemd unit is reinstalled or restarted, so the
previous, working version of the service keeps running under the old
schema — there is no window where a running process is pointed at a schema
its own code does not expect. Read the `alembic` error, restore
`data/openobservatory.sqlite` from a pre-deploy backup if the database was
left mid-migration (there is still no automated backup tool — take one
manually before a deploy you are unsure about), fix the migration or the
data it does not like, and re-run `deploy/deploy.sh`. Full detail and the
one window that *is* possible on a successful deploy (old process, new
schema, for the few seconds between the migration step and the restart —
tolerated because every migration here is additive) is in
`docs/data/DATA_MODEL.md` under "Rollback".

**To revert this change entirely:** `git revert` the commit. `create_all()`
and `ensure_schema_at_head()` both remain in `db/session.py` regardless (the
former as a test helper, the latter as the reverted call sites' replacement
disappearing), so reverting is a plain code rollback with no data migration
of its own — the schema itself does not change, only what checks it on the
way in.

### Smoke test

```bash
# Confirms deploy.sh's new step runs and the station is already at head
# (idempotent no-op expected — this is not a migration, just a check).
ssh <user>@<station-host> "cd open-observatory && .venv/bin/python -m alembic current"
# -> 0004_drop_dead_detection_indexes (head)

./deploy/deploy.sh --no-web --no-deps
# -> "==> running database migrations" step prints, exits 0, and the rest of
#    the deploy proceeds; the station's /api/v1/health check at the end
#    confirms the service came back up.
```

## ADR-044: A withdrawn detection is marked in the record and suppressed on the claim surfaces; and the BirdNET week index is correct

**Decision.** ADR-032 stopped the detector from ever writing another implausible
identification, and shipped `oo detections reconcile-plausibility` to flag the
ones already stored under `native_result.plausibility_review`. Nothing read that
flag. This ADR makes the consumers read it, through one shared definition
(`plausibility.py`: `REVIEW_KEY`, `is_withdrawn`, `withdrawal`), and splits
their treatment along one line:

| Surface | Treatment | Why |
|---|---|---|
| `GET /api/v1/detections`, `/detections/{id}` | **Kept, marked** `withdrawn: true` plus a `withdrawal` block | A record, with room for nuance |
| `GET /api/v1/detections/export` (CSV/JSON) | **Kept, marked** — new `withdrawn` column | Same, and a spreadsheet gets cited |
| `GET /api/v1/history` → `species` | **Excluded**, `excluded_withdrawn_count` reported | Names a species; an aggregate has no row to mark |
| `GET /api/v1/taxa/activity` | **Excluded**, `excluded_withdrawn_count` reported | Same |
| `GET /api/v1/history` → `timeline` | **Unchanged** | Counts detections; names nothing |
| MQTT publisher | **Suppressed**, counted | A Home Assistant state is a bare claim |
| `/api/v1/display` (ESP32 push) | **Suppressed** in SQL *and* on the wire | No score, no marker, no room in an MTU |
| ESP32 HTTP fallback (`detection_feed.cpp`) | **Refused** | It reads `/api/v1/detections`, which still returns the row |
| Web UI | **Marked** everywhere, explained in the drawer | `formatDetectionTitle` is the one composition point |

**Reason: the charter draws this line, not taste.** Item 5 is explicit —
*"Withdraw", not "delete". Preserve the original claim. The prior verdict stays
visible and attributable* — and a record the system got wrong is evidence about
the system. Deleting a row, or hiding it from the API, was never available. But
item 6 is equally explicit that *an answer that is wrong is worse than no
answer, because it will be believed*, and the honesty constraint requires that
"unverified" stay available **all the way to the surface**. On the two surfaces
where it cannot — a Home Assistant entity state, and a counter-top display that shows a
name and an elapsed time with no score at all by ADR-023's rule — carrying the
row with an unrenderable caveat *is* presenting it as fact. Suppression there is
the honest reading of the same constraint, not an exception to it.

The species-tally endpoints fall on the suppression side for a mechanical reason
rather than a philosophical one: they `GROUP BY` species, so there is no row left
to attach a marker to. "Western Screech-Owl, 4 detections, best score 0.96" is a
claim with nowhere to put a retraction. They therefore follow ADR-020's existing
precedent exactly — exclude by default, expose an `include_*` escape hatch, and
**report the count of what was excluded** (`excluded_withdrawn_count`), because a
filter on a wildlife-facing view is only honest if the exclusion is
discoverable. `timeline` is deliberately left alone: it counts detections per
bucket per group and names nothing, and the withdrawn detection genuinely did
occur.

**The operator's instinct, and where the code corrected it.** The brief proposed
"visible in the API/history with a withdrawn marker, suppressed on the counter-top
display and MQTT". That is what shipped, with one correction: *history* turned
out to be two different things. `/api/v1/history` returns a `timeline` (counts —
nothing to mark, and nothing needing marking) and a `species` list (an aggregate
that names species, with nothing to mark it *with*). Marking was not available
for the second, so it is excluded and counted instead.

**Implementation notes a successor will need.**

* `plausibility.py` is deliberately dependency-free — no SQLAlchemy, no numpy,
  no FastAPI — because `display_channel.py` is documented as free of the
  database and of FastAPI and has to import it. The one thing that genuinely
  needs SQLAlchemy, the predicate, lives in `history.py` next to
  `is_live`/`is_not_live` instead.
* `history.is_not_withdrawn()` is NULL-safe by construction and this is not
  optional. `native_result` is a `JSON` column, almost no row has a review
  block, and the extracted value is SQL `NULL` for all of them; a plain
  `= false` would have hidden the entire database rather than one owl. Same
  three-valued-logic trap `is_not_live` documents, with a far worse failure
  mode. It compiles to `json_extract` on SQLite and `->>`/`CAST` on PostgreSQL
  with no dialect branch (ADR-007).
* The display's connect snapshot filters in **SQL**, not in Python. ADR-038's
  whole point is that this query reads six narrow columns and never touches the
  ~1.8 kB `native_result` blob; reading the blob back just to test a flag would
  have undone that. `display_channel.wire_item` then checks the flag again for
  the live delta path, which does carry `native_result` on the bus. Two
  independent barriers, deliberately.
* The MQTT check is expected to be dead code on a healthy station: a withdrawal
  is written by a repair CLI long after capture, so no live bus event should
  ever carry one. It exists, and is counted as
  `oo_mqtt_suppressed_withdrawn_total`, because if that counter ever moves,
  something is republishing historical rows onto the bus and that is worth
  knowing rather than quietly forwarding.
* The ESP32's *streaming JSON filter* had to learn the field too
  (`buildDetectionsFilter`). Without that, `withdrawn` is discarded during the
  parse and every row reads as standing — a silent failure that a test of the
  parsed model alone would sail straight through, which is why
  `test_the_streaming_filter_keeps_the_withdrawn_flag` asserts the filter
  itself.

**Consequence: `--apply` now has teeth.** Before this change, running
`oo detections reconcile-plausibility --apply` changed nothing anybody could
see. It now takes effect immediately, with no restart, on every surface. The
command's help and its post-apply message were updated to say so. It has still
**never been run against the live station**, per ADR-032 and this session's
instruction; that remains the operator's call, dry-run and `--json` first.

---

### The second half: the week index passed to the range model is **correct**

ADR-032 left this explicitly unverified, and it was the higher-stakes of its two
open items: a wrong week makes every occurrence prior wrong *globally*, which
would silently invalidate the plausibility floor ADR-032 built on top of it.

**Derived independently from the code.** `birdnet_week` computes
`(month - 1) * 4 + min(4, int(day / 7.25) + 1)`. That form obscures what it
does, so it was checked against the convention as normally stated — four weeks
per calendar month, week 1 being days 1-7, the fourth absorbing days 22-31,
i.e. `(month - 1) * 4 + min(4, (day - 1) // 7 + 1)` — for **every day of a
common year and a leap year**. Zero mismatches, 48 distinct values, range
exactly [1, 48], 29 February included (week 8). Locked in
`tests/test_detectors.py::TestBirdNetAdapter::test_every_day_of_a_leap_and_a_common_year_lands_in_1_to_48`.

This is **not** an ISO week and must never become one. For 2026-08-08 the
BirdNET week is 30 and the ISO week is 32; on 2026-12-31 they are 48 and 53.

**Verified empirically against the real model, not only against arithmetic.**
Self-consistent arithmetic would not catch a formula that is coherent but off by
a fortnight, so the real V2.4 MData model was run at the station's configured coordinates
for all 48 weeks and checked against known UK phenology
(`scripts/birdnet_week_audit.py`, 2026-08-09):

| Species | Prior by week | Reality |
|---|---|---|
| Common Swift | ~0 to w11, rises w15-17, peaks **w22** (0.863), gone by w37 | Arrives late April/early May, leaves early August ✔ |
| Barn Swallow | rises w13, 0.98+ through w33, falls away w37-39 | April to September ✔ |
| Common Cuckoo | peaks **w17** (0.448), ~0 from w29 | Late April to July ✔ |
| Fieldfare | 0.28 in w1-5 and w45-48, 0.02 mid-summer | Winter visitor ✔ |
| Common Woodpigeon | 0.96-1.00 all year | Resident ✔ |
| Tawny Owl | 0.01-0.036 all year | Resident, and consistent with ADR-032's measured 0.019253 ✔ |
| Western Screech-Owl, Flammulated Owl | **0.000 in every one of the 48 weeks** | North America ✔ |

Week 30 — the week the owls were measured in — returns Common Woodpigeon 1.00,
Barn Swallow 0.98, European Robin 0.83, matching the sane priors ADR-032
reported from the live database, from an independent run here.

**Verdict: the week index is right.** The seasons land on the right calendar
dates to within a quarter-month, which is this convention's entire resolution.
The North American owls were never a week problem: their prior is zero in every
week of the year.

**One thing found along the way, worth writing down.** A week outside [1, 48] is
not rejected by the MData model — it returns the *year-round* prior. Measured:
Common Swift 0.913 at weeks 52, 53, 0 and −1, against 0.000 in January and 0.863
at its June peak. So an ISO week reaching this model would not error; it would
quietly disable seasonality and inflate the prior for every migrant, weakening
the filter rather than breaking it visibly. `birdnet_week` cannot produce such a
value by construction and the new test asserts that for every date, which is why
no runtime guard was added.

The station's local timezone is used for the conversion
(`datetime.fromtimestamp(window.utc_start_ns / 1e9, self._timezone)`) rather
than UTC. That is correct and immaterial: at quarter-month resolution it can
only matter for a detection within an hour of local midnight on the 7th, 14th or
21st of a month, and local time is the right choice for a seasonal index.

### Rollback and smoke test (ADR-044)

Nothing here has a runtime setting to turn off, because a flag consumers can be
configured to ignore is the bug this fixes. `git revert` the commit to restore
the previous behaviour; the stored `plausibility_review` blocks are untouched by
that and would simply go unread again, exactly as before.

No station currently has a single flagged row, so this change is a no-op on the
live station until an operator runs the repair command with `--apply`.

```bash
# 1. Dry run first, on the station, and read it. Never --apply blind.
oo detections reconcile-plausibility --json > /tmp/plausibility.json

# 2. After --apply: the marker must be present, the row must still be there,
#    and the species tally must have dropped it and said how many.
curl -s 'http://<station-host>:8080/api/v1/detections?limit=200' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["detections"]; print([(r["common_name"], r["withdrawn"]) for r in d if r["withdrawn"]])'
curl -s 'http://<station-host>:8080/api/v1/history?window=last-24h' \
  | python3 -c 'import json,sys; h=json.load(sys.stdin); print(h["excluded_withdrawn_count"], [s["common_name"] for s in h["species"]])'

# 3. The counter-top display's own feed, which is the point of the exercise.
python scripts/watch_display_channel.py --seconds 60 --label "post-withdrawal"

# 4. The week audit, re-runnable whenever the model assets change.
python scripts/birdnet_week_audit.py
```
## ADR-043: Taxon correction closes the review workflow; a human's ear outranks a machine's

**Status:** active. Closes the item ADR-029 deliberately left open ("correcting a
misidentified taxon... is left for a future ADR") and `HANDOVER.md` §6.4 item 11
("`corrected_taxon_id` is always written `None`").

**Decision.** A reviewer can now replace a wrong identification with the correct
one, and the system treats that correction as the highest-quality information it
will ever hold about the event — visible everywhere, never overwritten by a later
machine refinement, and (for evidence specifically) exempt from the retention
sweeper on request.

1. **`Review.status` gains two values.** Was `{confirmed, rejected}`; is now
   `{confirmed, rejected, corrected, held}` (`db/models.py`, `review.STATUSES`).
   * `corrected` — the original identification was wrong; `corrected_taxon_id`
     says what it actually was. `ReviewIn` (`api/app.py`) requires it exactly
     when `status == "corrected"` and rejects it otherwise (a Pydantic
     `model_validator`, so a malformed request 422s before touching the
     database).
   * `held` — no verdict yet, but keep the evidence. See point 4.
2. **The original claim is never edited.** `Detection.common_name` /
   `scientific_name` / `canonical_taxon_id` are written once, by the detector
   pipeline, and nothing added by this ADR ever updates them. A correction is a
   new `Review` row — same append-only shape the table already had
   ("current status is derived from the latest valid review") — carrying
   `corrected_taxon_id` plus two new denormalised columns,
   `corrected_common_name` and `corrected_scientific_name`, captured once at
   write time from whichever of the station's own past detections the taxon id
   matched (`review.resolve_taxon`). `actor` records who: the logged-in
   operator's username when auth is enabled and a session/token is presented,
   else the existing anonymous default `"local"` (ADR-034).
3. **Every consumer that shows an identification shows the correction
   alongside it, not instead of it.** `_detection_payload` (`api/app.py`) adds,
   next to the untouched `common_name`/`scientific_name`:
   `review` (the full annotation — status, actor, note, timestamp, and the
   corrected names/id when present), `identification_source`
   (`"human"` only when the latest review is a correction, else `"model"`),
   and `effective_common_name`/`effective_scientific_name` (the corrected name
   when corrected, else the original). This lands in `GET /detections/{id}`,
   `GET /detections` (list), and `GET /detections/export` (JSON and CSV — the
   CSV gains `identification_source`, `effective_common_name`,
   `effective_scientific_name`, `review_status`, `reviewed_by`, `reviewed_at`
   columns, alongside the untouched `common_name`/`scientific_name`). List and
   export batch-fetch the latest review per detection in one query
   (`review.latest_reviews_by_detection`, a `GROUP BY MAX(created_at)` join)
   rather than one query per row.
4. **An explicit hold exempts a detection's evidence from the retention
   sweeper's age-based tiers.** `retention.py`'s `_strip_native`,
   `_strip_non_exemplar` and `_strip_expired` all now skip any detection whose
   *current* review status is `held` (`review.held_detection_ids`, computed
   once per sweep, same shape as the existing exemplar-id set). Deliberately
   narrower than an unconditional hold: the watermark reclaim tier does
   **not** check it — see "Known limitations" below.
5. **A human's ear outranks a later machine refinement, enforced in code, not
   by convention.** `plausibility_repair.find_implausible_detections` now
   skips any detection with *any* human review at all — confirmed, rejected,
   corrected or held — via `review.reviewed_detection_ids`, and
   `apply_plausibility_flag` re-checks the same thing defensively (real time
   passes between an interactive CLI's find and confirm steps). Before this
   ADR the precedence was implicit: nothing stopped a repair pass from
   re-flagging a detection a human had already looked at, because the repair
   pass had no way to know a human had looked at it at all.
6. **Taxon lookup is built from the station's own detection history, not a
   new dependency.** `GET /api/v1/taxa/search?q=` (`review.search_taxa`)
   matches `q` against `common_name`/`scientific_name` (case-insensitive
   substring) among detections with `rank == "species"` and a
   `canonical_taxon_id` — i.e. every species any detector has ever actually
   identified here, keyed the same way `normaliser._canonical_taxon_id`
   already keys them (`sci:<scientific_name>`). `POST .../review` resolves
   `corrected_taxon_id` against the same table and 400s with a pointer to the
   search endpoint if it does not match anything the station has produced.
7. **MQTT does not republish a correction.** `mqtt/publisher.py`'s
   `_SUBSCRIBED_TYPES` is unchanged; there is no bus event for a review at
   all, because `post_detection_review` writes straight to the database. This
   is a decision, not an oversight (documented at length in that module): a
   correction typically lands well after the originating detection, about a
   clip a listener may already be finished with, and every entity this
   publisher creates models "what the station is hearing right now." Fabricating
   a fresh MQTT/HA event for a retrospective annotation would misrepresent it
   as a new acoustic occurrence. The correction is still fully visible through
   the API, the review drawer and the export — just not on the live bus.

**Why species come from the station's own history rather than BirdNET's label
list or a bundled/fetched taxonomy database.** Three options existed:
`birdnet_labels.txt` (6,522 entries, exact matches for anything BirdNET could
in principle say); a bundled or network-fetched taxonomy database (GBIF,
eBird's taxonomy, etc.); or the station's own `detection` table. The first two
were rejected. `birdnet_labels.txt` is model data under a separate
non-commercial/share-alike licence (ADR-006) that this repository never
bundles and that is only present on disk after an operator has run
`oo models fetch` — a lookup source that might silently not exist is not a
foundation for a core review action. A bundled or fetched taxonomy database is
a new dependency this brief explicitly asked to avoid reaching for without
checking what the station already holds, would need its own licensing story,
and (fetched) would violate "never require cloud connectivity for core
capture, detection, review or query" (`CLAUDE.md`). The detection table is
data the station unconditionally already has, requires nothing new, is never
itself a fabricated claim (`canonical_taxon_id` is only ever set for a
species-rank identification a real detector actually made — see
`normaliser._canonical_taxon_id` and the "do not fabricate classifier
support" rule this extends naturally to "do not fabricate a taxonomy"), and
needs no online access.

**Migration.** `alembic/versions/20260809_0004_000000000005_add_review_correction_names.py`
(`0005_review_correction_names`, down-revision `0004_drop_dead_detection_indexes`)
adds `review.corrected_common_name` and `review.corrected_scientific_name` as
nullable `VARCHAR(240)` columns, following revision 0003's idempotent-skip
precedent so an operator who restarts before migrating (and so already has the
columns via `db/session.py`'s SQLite patcher) does not hit "duplicate column
name." Verified with a from-scratch `alembic upgrade head` (all five
revisions apply cleanly), `alembic check` (no drift against `models.py`), and a
downgrade/upgrade roundtrip. `corrected_taxon_id` itself needed no migration —
it shipped with the initial schema (revision `0001_initial`) and was simply
unused until now.

**Known limitations, stated rather than discovered later:**

* **A correction can only name a taxon this station has already identified at
  least once, by any detector.** A first-ever, genuinely-correct-by-ear
  identification of a species the station has never itself detected has no
  match in `GET /api/v1/taxa/search` and cannot currently be entered as a
  structured correction. Fixing this needs a real taxonomy source (option two
  above), deliberately not taken here.
* **The retention hold does not survive a full disk.** `_watermark_reclaim`
  (the one hard safety valve in `retention.py`: "disk space always wins over
  any retention preference") is unchanged and does not check `held_ids`. An
  operator who needs a hold that survives disk pressure should export the
  clip rather than rely on the hold alone.
* **The aggregate `GET /api/v1/history` endpoint (`history.timeline`,
  `history.species_summary`) does not fold corrections in.** Its SQL groups
  and counts by the *original* stored `taxonomic_group`/`common_name`/
  `scientific_name` columns; a corrected detection still counts toward its
  original (wrong) species' tally in the night's species list and timeline.
  Every place a single detection is displayed — detail, list, CSV/JSON export,
  the review drawer — is correct per point 3 above; the aggregate view is not,
  and folding a per-detection correction into a `GROUP BY` over tens of
  thousands of rows per night is real work, not a "half-do the sweep logic"
  shortcut this pass could responsibly take. Flagged here rather than left
  silently wrong.
* **No confirmation UI for "are you sure"** on a correction: picking a search
  result submits immediately. Consistent with the existing confirm/reject
  buttons (also immediate), but worth naming since a correction is a stronger
  claim than either.

**Tests.** `tests/test_review.py` (unit, `review.py`'s query functions against a
hand-seeded database); `tests/test_api.py::TestReviewWorkflow` (HTTP-level,
through the real FastAPI app — confirm/reject supersession, taxon search,
correcting a detection and verifying the original is untouched everywhere the
correction must show, validation 422s, unknown-taxon 400, hold); new cases in
`tests/test_retention.py::TestHumanHold` (hold survives the native/expired
tiers, a later review releases a hold, the watermark reclaim ignores a hold);
new cases in `tests/test_plausibility_repair.py` (a human-reviewed detection is
never flagged; `apply_plausibility_flag` is a no-op if reviewed since the
finding was computed). `web/src/components/DetectionDrawer.test.tsx` covers
the hold button, taxon search-then-correct, and rendering an existing
correction.
## ADR-045: The refinement runner is a separate, CPU-fenced process, and the BatDetect2 cascade may only propose

**Decision:** Charter item 5 — "refine the record later, when better information
exists — and never silently" — ships as a **second process**, `oo refine run`,
started by `open-observatory-refine.timer` at 01:00 UTC and fenced with
`AllowedCPUs=2-3`, `Nice=19`, `MemoryMax=1G`, `CPUWeight=1`, `IOWeight=1`,
`IOSchedulingClass=idle`. Its first job is the ADR-017 BatDetect2 cascade over
stored `evidence_native` ultrasonic clips.

Four things are enforced in code rather than left to discipline
(`src/open_observatory/refinement/`), because every honesty failure on this
project so far has been sincere:

1. **Only from new information.** `EvidenceIdentity` hashes refiner, model,
   weights *and configuration* into a fingerprint. `record_refinement` refuses
   (`RefinementViolation`) when the refiner's `(model_id, model_version)` is the
   pair that made the original claim, and `ix_refinement_evidence` is unique on
   `(detection_id, evidence_fingerprint)`, so the same instrument under the same
   settings cannot bank a second, more optimistic answer about the same event.
   A re-run returns `None`, idempotently — not a new claim. Configuration is in
   the hash deliberately: without it, the only way to get a second answer out of
   one model would be to bump a version string, which is exactly the quiet,
   sincere workaround this project keeps finding after the fact.
2. **The original claim is preserved.** The `refinement` row snapshots
   `original_common_name` / `original_scientific_name` /
   `original_taxonomic_group` / `original_score` verbatim, read from the live row
   at write time. The writer never touches the detection's claim columns and
   *proves* it: nine columns (including a deep copy of `native_result`, since a
   shallow snapshot would compare a dict with itself and pass) are compared
   before and after, and any movement raises.
3. **A refined record is distinguishable.** `detection.refined_at`,
   `refinement_version` and `refinement_outcome` say on the event itself that
   refinement ran, at what version, with what outcome — which is exactly what the
   charter's retention decision asks each event to carry.
4. **The cascade may only propose.** `Refiner.authority` is `"propose"` for
   BatDetect2 and `record_refinement` raises if a propose-authority refiner
   reports an `applied` outcome. No shipped refiner has `apply` authority.

**Reason — the fence.** ADR-033 measured what "expensive work, isolated on its
own thread, inside the capture process" is actually worth: a 0.30 s retention
sweep starved the event loop 55–150 ms and produced **~1.9 false `capture.gap`
records a minute**, because an executor partitions queueing and nothing
partitions the GIL. A BatDetect2 pass is 2.1 s of inference — the same defect,
two orders of magnitude larger. `DeferredDetectorWorker` (`detectors/deferred.py`)
is the mechanism `HANDOVER.md` §1a nominated for this, and it is deliberately
**not** used: it is an in-process `asyncio.Queue` of live `AudioWindow`s whose
central safety property is dropping anything older than
`max_delivery_latency_s`, and a clip written six hours ago is exactly what it
would correctly reject. Reusing it here would have meant disabling the one thing
it exists to do. It remains the right mechanism for a *live* detector too slow to
run inline; a refiner over stored evidence is a different problem and gets its
own, smaller contract (`refinement/contracts.py`).

### The fence directives were verified on the target, not assumed

Measured on the station (Pi 5, `systemd 255 (255.4-1ubuntu8.16)`, cgroup v2,
`nproc` 4) on 2026-08-09, with a transient system unit carrying the exact
directives:

```
$ sudo systemd-run --unit=oo-fence-probe3 -p AllowedCPUs=2-3 -p Nice=19 -p MemoryMax=1G ...
/proc/self/status:Cpus_allowed_list:  2-3
EFFECTIVE=2-3           # cpuset.cpus.effective of the unit's own cgroup
MEMMAX=1073741824       # memory.max
NICE_FIELD=19           # field 19 of /proc/self/stat
```

**One real side effect, found by checking rather than assuming.** Before the
first such unit ran, the station's root `cgroup.subtree_control` was
`cpu memory pids` — the `cpuset` controller was available but *not enabled*.
systemd enabled `cpuset` (and `io`) in the root subtree on demand, permanently.
That does not constrain anything by itself: `open-observatory.service` continues
to report an empty `AllowedCPUs` and is scheduled on all four cores. It is
recorded because "a controller appeared in the root cgroup" is the kind of change
that is invisible until it is the explanation for something else. Note also that
`user.slice` does **not** carry `cpuset` on this host, so `systemd-run` *without*
`sudo` would silently fence nothing — the refiner must be a system unit.

### Capture impact, measured against a control window of equal length

Two consecutive ~10-minute windows on the live station, 2026-08-09, AudioMoth at
384 kHz, `started_utc` identical in all three snapshots so no restart voided the
comparison. The load window ran two busy-loop processes pinned to cores 2-3 under
the exact production fence — deliberately a **worse** load than the real refiner,
which alternates inference with clip reads rather than spinning both cores flat
out for ten minutes.

| Window | From (UTC) | Length | Frames delivered | Expected | **Deficit** | `capture.gap` | loop-lag events/min | worst loop lag |
|---|---|---|---|---|---|---|---|---|
| Control, cores 2-3 idle | 10:32:02 | 601 s | 230,745,600 | 230,757,589 | 11,989 frames = 0.031 s | **0** | 2.10 | ≤ 0.214 s |
| Fenced load on cores 2-3 | 10:42:03 | 621 s | 238,425,600 | 238,435,472 | 9,872 frames = 0.026 s | **0** | 2.22 | **0.293 s** |

**The frame deficit under load was *lower* than the control window's**, and
neither is evidence of lost audio. Two corrections have to be applied before that
column means anything, both already recorded on this project:

- **Crystal drift.** `expected_frames` is derived from elapsed time at the
  *nominal* rate, and this AudioMoth runs about 50 ppm slow
  (`observed_rate_hz` 383,980.8 → **−49.96 ppm**), so it legitimately delivers
  fewer frames than nominal with nothing lost — see
  `OPEN_INVESTIGATION_CAPTURE_GAPS.md`, "the deficit has a bias of its own"
  (2026-08-09). At −50 ppm, pure drift accounts for **11,538 frames** of the
  control window's 11,989 and **11,922** of the load window's 9,872. The load
  window's deficit is *smaller than drift alone predicts*.
- **Block quantisation.** Frames arrive in 38,400-frame blocks (100 ms), so a
  single instantaneous read of `frames` vs `expected_frames` carries up to one
  whole block of sampling error — **38,400 frames**, which is more than three
  times the drift term and eighteen times the 2,117-frame difference between the
  two windows.

So the honest reading is: **both windows are consistent with zero lost audio, and
the difference between them is inside the measurement's own resolution.** Which
is the finding — not "the fence made capture better".

Zero capture gaps in either window, against ADR-033's ~1.9 per minute when the
same class of work ran inside the capture process. Loop-lag *events* rose by
0.12/min (+6%), which is not distinguishable from run-to-run variation at these
counts.

**What did move, and is reported rather than buried:** the worst single
event-loop stall in the load window was **293 ms**, exceeding the 214 ms high
water mark accumulated over the whole preceding 13 minutes. `loop_lag_max_s` is
cumulative over the process lifetime, so the control row can only be stated as an
upper bound. The fence does not eliminate scheduling excursions on a 4-core box;
it stops them turning into lost audio. On this evidence the cost is a longer tail
on a metric nobody consumes, and no measurable cost to the thing that matters.

**Cross-checked against ground truth, per the standing rule that a counter on
this project is not evidence until it agrees with something else** (four
instruments on this project were found lying on 2026-08-08/09). A direct query of
the station's own `capture_gap` table returns `(0 rows, 0 estimated_missing_frames)`
for both windows and for the whole session, agreeing with `gaps_with_loss` /
`gaps_without_loss`. Detection writing continued throughout — 89 detection rows
in the control window, 122 in the load window — so the pipeline was doing real
work, not idling through the comparison. `estimated_missing_seconds` was
deliberately not used: it over-reported by 12.9× (ADR-039, SETUP.md trap 10).

**What this measurement is not.** The refiner itself was not deployed to the
station and no BatDetect2 pass was timed on target in this session; the 2.1 s per
pass figure is ADR-017's, from 2026-08-05. What is measured here is the *fence* —
that two cores can be saturated under it without capture noticing — which is the
claim the design rests on.

### The accuracy decision: propose, do not apply

The speed case for the cascade was settled by ADR-017's 2026-08-05 update — 2.1 s
of inference per pass, so 1015 passes is ~36 minutes for a whole night, and
trimming to 1.5 s centred on the loudest sample is where three quarters of that
saving comes from. **The accuracy case is not settled, and this ADR does not
pretend otherwise.** On this station's own 33–36 kHz cluster (`HANDOVER.md` §6.3
item 6):

- 6 of 8 clips leaned *Myotis*, at det_prob **0.20–0.30** — a lean, not an
  identification;
- one clip returned *Pipistrellus pygmaeus* at **0.77** on a call whose measured
  peak was **34 kHz**, when soprano pipistrelle peaks near 55 kHz;
- the AudioMoth gain is hot and still clips on loud nearby events (`HANDOVER.md`
  §6.3 item 4), an unresolved confound for all of the above.

That middle item is the decisive one. It is the same shape as the 0.96 BirdNET
score on a species absent from the continent that ADR-032 ruled on: *a confident
answer contradicted by a physical fact the station measured itself is evidence
that the score is meaningless for that species, not evidence of the animal.* A
classifier that fails that test on this station's audio has not earned authority
over this station's record, and the honesty constraint — "never claim more than
the evidence supports" — puts the ceiling at `propose`.

This is not a placeholder for "apply, once we are braver". Charter item 5 lists
*a human ear* as a basis for refinement in its own right, the `review` table is
the mechanism, and the honest sequence is: proposals first, a human listening to
the audible renderings second, and only then any argument *from evidence* that
this model has earned more. Three supporting choices follow from the same
reasoning:

- **No species-frequency plausibility filter**, tempting as one is after the
  *pygmaeus* contradiction. This station has no calibrated, sourced reference for
  UK species peak frequencies, and reconstructing one from memory is the class of
  plausible fabrication this project avoids elsewhere (see the favicon note in
  `HANDOVER.md` §6.3a for the same reasoning applied to an icon). Instead every
  proposal carries the station's *own* `peak_frequency_hz`, `peak_snr_db` and
  `pulse_count` next to the model's species and det_prob — the pairing that
  exposed the contradiction in the first place — plus a `caution` string
  assembled only from measured facts.
- **The 0.05 det_prob floor is a noise floor, not a truth threshold.** It exists
  so one pass does not emit a dozen near-zero species rows. The measured
  0.20–0.30 leans must survive it: a low-confidence lean is precisely what a
  human ear should arbitrate, not what we hide.
- **Only `evidence_native` clips are classified.** A heterodyne rendering has
  discarded everything outside its tuned band and a time-expanded one is no
  longer at its original rate; classifying either is classifying the renderer.

### `unavailable` is not `no_change`

The outcome vocabulary separates "the refiner examined this and could not improve
it" (`no_change`, `confirmed`) from "the refiner never saw it" (`unavailable`,
`failed`). `EXAMINED_OUTCOMES` is the set that counts as examined. This exists
because the charter's retention safeguard is aimed at exactly that confusion:
*"the risk is not old data, it is data the refiner never actually saw — a failed
timer, missing model assets, a station that was down."* A pipeline that recorded
a missing clip as `no_change` would make the safeguard useless while looking
correct. For the same reason, missing model assets make a run **skipped, with a
reason** (`RefinerUnavailable`), never a pass that silently found nothing.

### What this does not do

- **It does not change `retention.py`.** The sweeper still deletes on age alone:
  `_strip_native`, `_strip_non_exemplar` and `_strip_expired` filter only on
  `event_start_utc`, and nothing anywhere reads `refined_at`. So today a clip can
  be reclaimed at 7, 30 or 90 days having never been examined once — the failure
  the charter's first safeguard names. The schema now makes the fix cheap and
  indexed (`ix_detection_refined_at`); the guard is one predicate per tier:

  ```sql
  AND detection.refinement_outcome IN ('proposed','no_change','confirmed','applied')
  ```

  It is deliberately **not** applied here. Turning it on before the refiner has
  completed a full cycle would freeze all deletion on a station whose disk is a
  real constraint, and changing a live station's deletion policy is the
  operator's call, not a side effect of adding a column. `oo refine status`
  reports how many events have never been examined, which is the number that
  decision needs. `tests/test_refinement.py::TestRetentionGap` and
  `tests/test_refinement_integration.py::TestRetentionInteraction` pin the
  current behaviour so whoever changes it changes those too.
- **It does not implement the second safeguard**, the explicit human hold, and it
  does not connect proposals to the review workflow. `review` *is* written to —
  `POST /api/v1/detections/{id}/review` has inserted rows since 2026-08-08
  (ADR-029), which corrects the charter's "nothing writes to it yet" — but that
  endpoint knows nothing about proposals, so `refinement.resolved_at` and
  `resolved_review_id` stay NULL. Wiring "accept this proposal" to a `review` row
  (and deciding whether accepting one may finally move the detection's claim) is
  the next piece of charter item 5, and it is the piece where a human ear is the
  new information.
- **It does not surface refinement in the API, the UI, the MQTT publisher or the
  counter-top display.** Nothing a person sees on those surfaces changes, which is
  correct while every refinement is a proposal: a proposal is a question, not an
  observation, and putting one on a counter-top display would be precisely the
  over-claiming this ADR exists to prevent. `oo refine status` is the surface.
- **It does not add BatDetect2 as a dependency or an extra.** Its whole
  repository is CC-BY-NC-4.0 (ADR-006, ADR-017); the operator installs it
  (`pip install batdetect2==1.3.1` plus CPU torch, see
  `docs/detectors/BATDETECT2_EVALUATION.md`), and the tests stub the library at
  its own boundary rather than requiring it.

### Rollback and smoke test (ADR-045)

**Rollback is a single command and needs no deploy**, because the runner is a
separate unit and the station process never imports `refinement/`:

```bash
sudo systemctl disable --now open-observatory-refine.timer
```

That leaves capture, detection, retention and every surface byte-identical to
pre-ADR-045 behaviour. `OO_REFINEMENT_ENABLED=false` in `config/runtime.env` is
the equivalent for a station that keeps the timer armed.

To roll the *schema* back — only necessary if the `refinement` table itself is a
problem, which is unlikely since nothing else reads it:

```bash
.venv/bin/alembic downgrade 0004_drop_dead_detection_indexes   # drops the table and the three columns
```

Note that `db/session.py`'s `create_all()` + ALTER TABLE patcher re-adds the
columns on the next start (ADR-035's known coupling), so a full schema rollback
also means reverting the code.

Target smoke test — run **on the Pi**, in this order, checking capture at each
step:

```bash
# 1. The fence really is a fence, on this systemd.
systemctl show open-observatory-refine -p AllowedCPUs -p Nice -p MemoryMax
sudo systemctl start open-observatory-refine
cat /sys/fs/cgroup/system.slice/open-observatory-refine.service/cpuset.cpus.effective  # 2-3

# 2. A dry run makes no claim and writes nothing.
.venv/bin/oo refine run --force --dry-run

# 3. What has never been examined -- the number the retention safeguard needs.
.venv/bin/oo refine status --json

# 4. Capture, before and after. NOTE: this instruction was written before ADR-046
#    and had it backwards. `expected_frames - frames` is ~98% crystal drift plus
#    block-sampling phase (+-50 ms on a single reading), NOT lost audio; it grows
#    ~0.18 s/hour on this device while nothing is lost. Judge loss by
#    estimated_missing_seconds, which ADR-039 made a decomposition of the deficit
#    rather than a second number, and cross-check the gap counters against the
#    database rather than believing either alone.
#    2026-08-14: the "~98% crystal drift" figure holds at this run's duration
#    (<=1h) only. At the 72-hour soak the residual deficit was unexplained by
#    drift -- see ADR-046's 2026-08-14 status note.
curl -s localhost:8080/api/v1/health | python3 -c \
  'import json,sys; c=json.load(sys.stdin)["capture"]; print(c["frames"], c["expected_frames"], c["gaps_with_loss"], c["gaps_without_loss"], c["loop_lag_max_s"], c["loop_lag_events"])'
python3 -c "import sqlite3; print(sqlite3.connect('data/openobservatory.sqlite').execute(
  \"select count(*), coalesce(sum(estimated_missing_frames),0) from capture_gap where start_utc >= datetime('now','-1 hour')\").fetchall())"
```

## ADR-047: Site parameters are runtime state, managed through the web UI; the repository ships no site

**Decision:** Anything true of exactly one installation — coordinates, place
names, LAN addresses, hostnames, account names, filesystem homes — is **site
state**, not repository content. It lives in untracked runtime configuration
(`config/runtime.env`, NVS on the ESP32) and is editable through the web UI's
settings page (`GET`/`PUT /api/v1/settings`, `site_settings.py`) or the
firmware's provisioning portal. The repository describes a *system*; a
deployment describes a *site*. Committed defaults must be universally
applicable, and where no universal value exists the default is **unset, and
the system says so** — never a plausible-looking value that is silently
somebody else's.

**Context.** This repository began as one garden's observatory and was
saturated with that garden: the operator's home coordinates at ~11 m precision
in docs and four test files, the station and broker LAN addresses in scripts,
docs, web tests and the counter-top display firmware, and the operator's username and
home directory in the systemd unit. Publishing the repository makes every one
of those a permanent public disclosure. The deeper problem is behavioural,
not cosmetic: a cloned station that silently inherited the original site's
coordinates would run BirdNET's range-based plausibility filtering against
*someone else's garden* — confidently wrong output, which is the charter's
honesty constraint violated by omission.

**Mechanism.**

- `site_settings.py` holds the whitelist of operator-editable settings in
  three explicit tiers: **live** (station name, timezone; MQTT, applied by
  restarting the publisher — the same code path a process restart takes, so
  there is no second reconfigure path to drift), **restart-pinned**
  (latitude/longitude: bound into the BirdNET range filter and the night
  schedule when detectors start, and deliberately never swapped under a
  running range model — the station row records the operator's declaration,
  `Station.applied_site` records what the detectors are actually using, and
  the API, `/api/v1/health` and the UI all report any difference as "saved,
  in force after restart"), and **never browser-editable** (auth, bind
  address, storage paths: each exclusion reasoned in the module).
- Persistence is `config/runtime.env` itself — gitignored, operator-owned,
  written atomically with comments and unknown keys preserved, mode 0600. UI
  edits and hand edits are one configuration, not two.
- First-run honesty: latitude/longitude default to unset; `/api/v1/health`
  carries a `notes` list naming the consequence (no plausibility filtering,
  night schedule always-on), the station snapshot carries
  `location_configured`, and the web UI banners it with a link to settings.
  The default timezone is UTC — the only zone that is not somebody's local
  assumption. Empty env values for optional settings now mean "unset"
  (`OO_LATITUDE=`, as shipped by `config/example.env`, previously crashed
  startup).
- The inside-observer firmware ships no station address at all: an empty
  `stationHost` survives clamping, refuses to build a URL, and raises the
  existing provisioning portal, instead of a fresh unit silently polling one
  particular installation's LAN.
- Tests that need a location use a **neutral published reference** — the
  Royal Observatory, Greenwich (51.4769 N, 0.0005 W) — with every
  externally-derived expected value re-derived for it (sunrise-sunset.org
  civil twilight times; the BirdNET range model's Robin occurrence,
  re-measured at 0.8099 for week 30 with the real shipped model), never
  relabelled.

**For successors.** Do not hardcode a location, address or identity back in,
however convenient during debugging — not in a default, not in a test, not in
a doc example. Tests take locations as inputs or use the Greenwich reference;
doc examples use placeholder hosts (`<station-host>`) or RFC 5737 / RFC 2606
example addresses; historical measurement notes say "the development station"
rather than naming where it stands. If a new component needs a site
parameter, add it to the `site_settings.py` whitelist (choosing its tier
deliberately) rather than inventing a parallel mechanism.

### Rollback and smoke test (ADR-047)

No schema change, no new dependency. The settings endpoints and panel are
additive; revert the commits to remove them. Site values already present in a
station's `config/runtime.env` are untouched by either direction.

```bash
curl -s http://<station-host>:8080/api/v1/settings | python3 -m json.tool | head -30
curl -s -X PUT http://<station-host>:8080/api/v1/settings \
  -H 'content-type: application/json' -d '{"latitude": 51.4769, "longitude": -0.0005}'
curl -s http://<station-host>:8080/api/v1/health | python3 -c 'import json,sys; print(json.load(sys.stdin)["notes"])'
# expect the "restart required: latitude, longitude" note; restart, and expect it gone.
```

## ADR-046: The frame deficit is 98% crystal drift, and "audio lost" must not show it

**Decision:** the debug UI's `audio lost` row no longer shows the raw frame
deficit `expected_frames - frames`. It shows `estimated_missing_seconds`, the
estimator's confirmed loss (ADR-039). The deficit is shown separately as
**`behind clock`**, with its crystal-drift term named inline and its
sampling-phase uncertainty stated. `describeDeficit` in
`web/src/components/Pipeline.tsx` performs the decomposition and is unit-tested
against the live readings below. Nothing on the station changed; this is a
presentation fix to a measurement that was already correct and mislabelled.

**Reason — the label was false, and the charter forbids that.** "A number shown
to a human must mean what its label says." `expected_frames - frames` is four
things added together, and on this station only one of them is lost audio:

1. **Sampling phase.** `frames` advances in whole 38,400-frame blocks (100 ms at
   384 kHz) while `expected_frames` advances continuously, so the raw deficit
   sawtooths across a full block *while nothing whatsoever is wrong*. Measured
   over 43 minutes with zero gaps, zero overruns and zero estimated loss, it
   ranged **−162 ms to +185 ms**. A single reading of this row therefore carried
   about **±50 ms of pure artefact** — the same order as the figures it was being
   read for, and most of what "the two measurements disagree" actually was.
2. **Crystal drift.** The AudioMoth's crystal runs about **50.4 ppm** slow
   against the host, so it legitimately delivers fewer frames than nominal wall
   time implies: **0.18 s per hour, 4.4 s per day**, forever, with nothing lost.
3. **Anchor bias**, sub-millisecond (0.34 ms measured), from where frame zero is
   pinned.
4. **Lost audio**, which is what `estimated_missing_frames` measures — being, by
   ADR-039's construction, exactly the part of the deficit that never came back.

Terms 1 and 2 are why the raw deficit read **0.104 s** on a station that had
lost nothing, and why over a night it would have reached 2 s and read as a slow
leak of audio that is not happening.

**The measurement that settles it** is recorded in full, with its windows and
its contamination, in `docs/delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md`. In
short: `/api/v1/health` sampled every 2 s for 43 minutes on one uninterrupted
stream (2026-08-09, UTC 10:31:32 → 11:14:13), with the deficit re-evaluated at
the last block's own start — which `block_age_s` publishes, and which removes
term 1, taking the scatter from ~100 ms to **0.3 ms**. A concurrent agent
saturated two cores from 10:42:03 to 10:52:03 UTC, so the run is reported in
three segments rather than through:

| segment (UTC) | | growth of the corrected deficit |
|---|---|---|
| 10:31:32 → 10:42:01 | control | **+51.17 ppm** [50.52, 51.81] |
| 10:42:03 → 10:52:01 | two cores saturated | **+48.25 ppm** [47.12, 48.75] |
| 10:52:03 → 11:14:13 | control | **+51.00 ppm** [50.74, 51.30] |

The two clean windows agree. **Under load the deficit grew *slower*, not
faster** — the opposite of the failure mode under suspicion, since uncredited
loss would get worse under scheduling pressure, not better. The growth is a
straight line to within 0.5 ms per-minute-median in every segment, and real loss
arrives as a step that stays up; there is no such step anywhere. Independently,
`rate_offset_ppm` converges to an asymptote of **−50.43 ppm** [−50.55, −50.20],
and a second agent measuring the same station in the same hour got **−49.96
ppm**. The deficit's growth and the crystal's rate agree to about **1 ppm — 3.6
ms/hour — and of ambiguous sign.** The deficit is drift. There is no loss the
estimator is missing, and **ADR-039's confirmation window is not too permissive**
on this evidence.

**The limit of the claim:** the longest *clean* window is 22.2 minutes. This
rules out a continuous leak, which was the specific worry. It does not rule out
a rare or long-period event, and a restart-free multi-hour run is still worth
taking — the method now costs 15 minutes.

**Status note, 2026-08-14: the claim holds at ≤ 1 hour and does not extend to
72 hours.** The 72-hour soak (2026-08-10 to 2026-08-13) gives the long run this
ADR asked for, and at that duration the crystal no longer explains the deficit.
All three detectors reported `lag_seconds` ≈ 185 s at 54.7 h into the run; at
this station's measured −51.62 ppm, drift accounts for only about 10 s of that
185 s deficit. The title and this ADR's index row state "98% crystal drift" as
a settled, duration-independent fact; it is settled only for the runs measured
here (minutes to about an hour). At 72 hours the residual is unexplained and
should be read as suspected real loss, not drift, until isolated. See
`HANDOVER.md`'s "Two things suspected, not established" note and
`MILESTONE_STATUS.md` §Milestone 4.5.

**A correction to how these two numbers were being reasoned about.** They were
treated as independent measurements that had to be reconciled. They are not
independent: `rate_offset_ppm` is computed inside `AlsaSource` as
`-(deficit − missing_frames) / expected × 1e6`, so *algebraically*, subtracting
the drift term from the deficit returns `estimated_missing_frames` and nothing
else. Drift-correcting the deficit and displaying the result would therefore
have been a rename of the estimator's own figure, dressed as a second opinion.
The check above is still evidence, because the two are built on different
anchors and one is a slope while the other is a cumulative average — but the
station has **one** measurement of lost audio, not two, and future work must not
assume otherwise.

**Consequence:** `CaptureStatus` in `web/src/types.ts` gained the fields the API
has published since ADR-039 and the UI ignored — `estimated_missing_seconds`,
`gaps_with_loss`, `gaps_without_loss`, `late_reads`, `late_read_max_frames`,
`alsa_buffer_frames`. `late_reads` is now shown beside `overruns`, because a
stall the ring absorbed is the thing `capture.gap` used to impersonate and the
operator had no way to see it.

**What was deliberately not done.** The sampling-phase artefact could be removed
at source, by evaluating `expected_frames` at the last block's start rather than
at snapshot time — a few lines in `Station.status_snapshot`. That is the better
fix and it would stabilise `continuity_ratio` too. It is not taken here because
it changes a measured quantity in the capture path, which on this project has
twice needed a deploy and a soak to trust, and the deploy would have voided
concurrent measurements. The UI states the ±50 ms instead of hiding it. This is
recorded as the recommended next change, not as a gap.

**Rollback:** confined to `web/`. `git revert` the commit and rebuild the UI
(`./deploy/deploy.sh`). No station code, schema, setting or dependency changed,
so a rollback cannot affect capture.

**Smoke test on the target** — the row must read `none` while the station is
clean, and `behind clock` must grow at roughly 0.18 s/hour without `audio lost`
moving:

A heredoc, not `python3 -c '...'`: the f-strings need double quotes inside a
single-quoted argument, and escaping them is how the first draft of this block
failed with `SyntaxError: unexpected character after line continuation
character`.

```bash
# Note: this reads UTC. `journalctl --since` below takes LOCAL time (BST = UTC+1).
curl -s http://<station-host>:8080/api/v1/station | python3 - <<'PY'
import json, sys
c = json.load(sys.stdin)["capture"]
r = c["sample_rate"]
d = c["expected_frames"] - c["frames"]
up = c["expected_frames"] / r
drift = -c["rate_offset_ppm"] * 1e-6 * up
lost = c["estimated_missing_seconds"]
print(f"uptime       {up:8.0f} s")
print(f"audio lost   {lost:8.4f} s   <- the row labelled 'audio lost'")
print(f"behind clock {d / r:8.4f} s   of which {drift:.4f} s drift, +-0.05 s phase")
print(f"residual     {d / r - drift - lost:8.4f} s   must stay within +-0.1 s")
print("gaps", c["gaps_with_loss"], c["gaps_without_loss"], "overruns", c["overruns"],
      "late_reads", c["late_reads"], "of ring", c["alsa_buffer_frames"])
PY

# Every late read must say it cost nothing; nothing may say it did.
ssh <user>@<station-host> 'sudo journalctl -u open-observatory --since "-30 min" \
  | grep -cE "capture.late_read"'
ssh <user>@<station-host> 'sudo journalctl -u open-observatory --since "-30 min" \
  | grep -E "loss_confirmed|lost_audio=True"'   # must print nothing
```

Run against the live station at 47 minutes' uptime, 2026-08-09 11:15:59 UTC,
same uninterrupted stream:

```
uptime           2830 s   stream 728ac7be
audio lost     0.0000 s
behind clock   0.2193 s   of which 0.1428 s drift, +-0.05 s phase
residual       0.0766 s   must stay within +-0.1 s
gaps 0 0 overruns 0 late_reads 41 of ring 192000 ppm -50.44
```

Note what that says: the raw deficit is now **0.219 s**, more than twice the
0.104 s that prompted this whole investigation, and the station has still lost
nothing. Under the old label that would have read as the leak getting worse.

## ADR-048: Every setting is web-configurable, in three declared tiers, with the exclusions named

**Decision:** A new operator gets from a freshly imaged Pi to a working, tuned
station **without opening a terminal or a text editor**. Every field of
`Settings` is either editable from the web UI — in a declared tier, **live** or
**restart-pinned** — or listed in `site_settings.NON_EDITABLE` with a concrete
hazard that earns the exclusion. The default is editable; the bar for "never"
is a named outcome with no recovery path from the browser, not tidiness and
not "an operator might get it wrong".

**Context.** ADR-047 established the mechanism — `config/runtime.env` as the
one store, written atomically with comments and unknown keys preserved, mode
0600, with `Station.applied_site` keeping "saved" and "in force" honestly
apart — but applied it to a whitelist of 16 site-identity fields. Everything
else, including every detector threshold and the ADR-041 spectrogram floors
and ceilings, meant SSH and a text editor. That is the wrong shape for two
reasons. It makes commissioning a developer task, when the product is an
appliance. And it makes *tuning* — the thing an operator does repeatedly, in
response to what they see — the most awkward operation the system offers, when
it should be the easiest.

The immediate case was a microphone mounted next to a plant rubbing against a
shed: loud, periodic background noise. That is a mounting problem, to be fixed
physically, and this ADR deliberately does not chase the noise floor. But an
operator living with it for a week needs `ultrasonic_min_snr_db`,
`ultrasonic_min_pulses_per_pass`, the band edges and the spectrogram contrast
to hand, and a restart-and-SSH per attempt turns a minute's work into a day's.

**Mechanism.**

- **The audit is code, not prose.** `EDITABLE_SETTINGS` (129 entries) and
  `NON_EDITABLE` (20 entries) between them name every field of `Settings`
  exactly once. `tests/test_site_settings.py::TestTheAuditIsComplete` fails if
  a field is added without a recorded decision, so "not editable, no reason
  given" cannot happen by omission.
- **Tiers.** *Live* means in force now. Most live settings are free — the
  station reads them from `Settings` on every use. The rest are mapped in
  `tuning.py` to the object that holds their value: a `SpectrogramEncoder`
  floor/ceiling, a detector's thresholds via a new `retune()`, a `ClipManager`
  or `RetentionSweeper` attribute. *Restart-pinned* means saved, reported, and
  not injected: coordinates (ADR-047's original reasoning — a live swap under a
  running range model changes what "plausible" means mid-stream), and capture
  geometry, because re-negotiating a rate or a ring depth means tearing down
  capture, and **charter item 1 forbids that as a side effect of a form
  submission**.
- **Nothing here can cost a frame of audio.** Every live application is an
  attribute rebind or a threshold swap read per column or per window. No
  device is reopened, no thread joined, no queue drained. The settings write
  itself goes to a thread only for the file I/O, exactly as before.
- **The honesty constraint applies to the settings surface itself.**
  `Station.applied_site` now records what *every* tracked component was built
  with or last retuned to, not only coordinates, and any daylight is reported
  as `pending_restart` — through `GET /api/v1/settings`, the station snapshot,
  `/api/v1/health` notes and the UI banner. Crucially this covers live-tier
  fields: a live setting whose target object does not exist (no ultrasonic
  encoder at 48 kHz) is recorded with an `UNAPPLIED` sentinel that compares
  equal to nothing, so it reads as pending until a restart binds it. Saved is
  never displayed as in force.
- **Validation is the API's job, in two layers.** Per-field: type via Pydantic
  v2, plus declared bounds, enum choices and semantics, reported by field name
  with the limit in the message. Cross-field (`validate_merged`): floor below
  ceiling, ring at least two capture blocks (ADR-030), retention ladder in
  order, pre-roll inside both the maximum clip length and the native ring,
  plausibility bands in order, at least one sample rate offered. Every rule
  runs against the *merged* configuration and only fires when one of its own
  fields is being changed, so an operator is never trapped by a pre-existing
  inconsistency they are not touching. Validation runs before anything is
  written: a rejected request leaves the file and the process exactly as they
  were, and cannot leave the station unable to start capture.
- **Defaults travel with the fields.** Every field carries its shipped value in
  the payload and a one-click reset in the panel, and clearing a field restores
  it. The ADR-041 measurements are only a reference point if an operator who
  has wandered away from them can get back.
- **Dangerous but legitimate is a warning, not an exclusion.** `source`,
  `audio_device`, `clip_plugins` and `mqtt_tls_insecure` each carry a `danger`
  string the UI requires the operator to acknowledge before saving. Hiding a
  setting does not make it safe; it makes it an SSH session.
- **First run guides rather than fails.** `GET /api/v1/setup` answers the four
  questions a person has on day one — where am I, what is this called and what
  time is it here, is my microphone working, do I want MQTT — and the
  microphone step reads *live capture state*, so a station on the synthetic
  fallback says so instead of ticking a box. Dismissal is a setting
  (`setup_completed`), not browser storage: whether a station has been
  commissioned is a fact about the station. This is a guided flow, not the
  commissioning wizard of Milestone 7; it probes nothing and calibrates
  nothing.

**The exclusions, and why each one earns it.**

- `auth_*` (12 fields) — authentication must not be editable through the
  surface it protects. An unauthenticated session could disable the gate; a
  half-configured one could lock every operator out with no way back but SSH.
- `bind_host`, `bind_port` — a remote-hands lockout. The next request goes to
  an address that no longer answers and the browser cannot follow.
- `data_dir`, `database_dsn` — repointing storage under a running station
  orphans the database mid-write and strands existing clips. This is a stop,
  move, migrate, start operation, and a DSN can additionally carry credentials
  for a host this station has no business reaching.
- `runtime_env_path` — the settings store itself. Repointing it makes the UI
  write to a file the process does not read, which is precisely the
  two-configurations-that-disagree failure the mechanism exists to prevent.
- `replay_path` — the replay source plays a file of the operator's choosing
  into the live audio stream and the spectrogram. From a browser, on a station
  whose shipped default is anonymous LAN access, that is an arbitrary-file-read
  tool wearing a settings field. `source` is editable but its choices are
  narrowed to `auto`/`alsa`/`synthetic` for the same reason.
- `web_dist` — the API serves this directory over HTTP; pointing it anywhere
  publishes that path to the LAN.
- `birdnet_model_dir` — chooses which model binary the process loads. Selecting
  the file a process loads is not a settings decision made from a form; `oo
  models fetch` records provenance and licence acceptance.

**Consequences.** The settings page grew from two sections to thirteen
categories with search, collapse, per-field help, units, bounds and defaults —
all served from the API, so there is no second copy of the catalogue in the
frontend to drift. Five new `Settings` fields were added
(`birdnet_common_prior`, `birdnet_range_threshold` and the three BirdNET band
thresholds) that previously existed only as constructor defaults and had no
environment surface at all; and `setup_completed`. `SpectrogramEncoder.retune`
clears retained history, because columns already quantised through the old dB
window cannot be remapped and rendering two contrasts in one picture would be
worse than losing thirty seconds of backfill.

**For successors.** Adding a field to `Settings` now has a second obligation:
decide its tier and record it, or record why it is excluded. The test will tell
you. If it is live and something long-lived holds its value, map it in
`tuning.py`; pin the restart-tier equivalents in one of the `PINNED_AT_*`
snapshots. A "live" setting that saves and does nothing while reporting itself
applied is the exact dishonesty this ADR exists to prevent, and it is the easy
mistake to make.

### Rollback and smoke test (ADR-048)

No schema change, no new dependency, no migration. Additive to ADR-047: revert
the commits and the page returns to the 16-field site whitelist. Values already
written to a station's `config/runtime.env` are untouched by either direction —
they are read by `Settings` regardless of whether the UI can edit them.

```bash
# The catalogue, with tiers and defaults:
curl -s http://<station-host>:8080/api/v1/settings \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["fields"]), "fields", len(d["non_editable"]), "excluded")'

# A live tuning change takes effect without a restart:
curl -s -X PUT http://<station-host>:8080/api/v1/settings \
  -H 'content-type: application/json' -d '{"ultrasonic_min_snr_db": 20}'
curl -s http://<station-host>:8080/api/v1/settings \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["pending_restart"])'   # expect []

# A restart-pinned change is saved and says so:
curl -s -X PUT http://<station-host>:8080/api/v1/settings \
  -H 'content-type: application/json' -d '{"native_ring_seconds": 180}'
curl -s http://<station-host>:8080/api/v1/health \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["notes"])'

# A bad value is refused by name, and nothing is written:
curl -s -X PUT http://<station-host>:8080/api/v1/settings \
  -H 'content-type: application/json' -d '{"spectrogram_floor_db": 0}'

# First run:
curl -s http://<station-host>:8080/api/v1/setup | python3 -m json.tool
```

## ADR-049: BirdNET's eleven sound categories are not species — no clip for human speech, no bird claim, no plausibility floor

**Status:** active. Corrects ADR-032's plausibility floor and ADR-044's
withdrawal flag where they meet a non-taxonomic class, and adds the first
implementation of the charter's privacy constraint beyond "we do not record
continuously".

**Context.** The first dry run of `oo detections reconcile-plausibility`
against the live station's real database — 67,679 detections, 2026-08-09,
read-only — proposed flagging 114 rows as implausible. Inspecting them turned
up three problems of increasing seriousness, all measured rather than inferred:

| common | rank | group | n | best score |
|---|---|---|---|---|
| Engine | species | bird | 203 | 0.997 |
| Human vocal | species | bird | 25 | 0.984 |
| Dog | species | bird | 18 | 0.990 |
| Gray Wolf | species | bird | 1 | 0.964 |

1. **91 of the 114 findings were correct detections.** 62 `Engine`, 24 `Human
   vocal`, 5 `Dog`. The range model returns 4e-06 for "Engine" at this station
   — not because engines are absent from the garden but because a car is not a
   taxon with a distribution — and ADR-032's floor reads that as "essentially
   impossible here". A passing car detected at 0.99 is very probably a passing
   car, and withdrawing it also costs the operator the honest "that was
   traffic, not a bird" signal.
2. **The taxonomy was wrong at the source.** Every one of those 247 rows is
   stored `rank='species'`, `taxonomic_group='bird'`, with `scientific_name`
   repeating the common name and a fabricated `canonical_taxon_id` of
   `sci:engine`. The system asserts that a car engine is a bird, at species
   rank. That is an honesty-constraint failure on the *live pipeline*, not
   merely in history — the normaliser accepts these claims from BirdNET today,
   so they keep arriving.

   **Corrected at merge.** An earlier draft of this ADR read the stored
   `plausibility_band: 'out_of_range'` / `threshold_applied: 0.9` on those rows
   as evidence that ADR-032 was never deployed to the station. It was not: those
   are historical rows written before the deploy of 2026-08-09 14:04 UTC.
   Checked afterwards, all 141 banded detections written since carry `in_range`
   at `0.55` and none carry `out_of_range` at `0.9`, and
   `oo_birdnet_suppressed_total{reason="suppressed_implausible_prior"}` — a
   counter that exists only under ADR-032 — was already at 19. ADR-032 is live
   and suppressing. The taxonomy defect described here is real and independent
   of it; the deployment claim was not.
3. **24 `Human vocal` detections held 48 evidence clips and 125 MB** of
   neighbours and passers-by talking in a garden.

**Decision, in charter order.**

### Privacy first: no clip is written for human sound, by default

New setting `clip_human_audio`, default **False**, live-tier, editable in the
browser behind a `danger` acknowledgement (ADR-048). With it off, a detection
of `Human vocal`, `Human non-vocal` or `Human whistle` gets its detection row
and **no audio at all**.

Three options were available: never write the clip; write it with a much
shorter retention; write it and rely on the existing tiers. The charter
settles it rather than taste. The privacy constraint is not a priority that
can be traded — *"No efficiency, accuracy or feature gain justifies relaxing
this"* — and its stated concern is people "who never consented". A shorter
retention still retains, and still requires the operator to have understood
and accepted a window during which a neighbour's conversation is on an SD card
in the garden. Not writing it is the only option that needs no such
explanation. The detection row is kept because it contains no speech: "somebody
was talking at 18:55" is a fact about the soundscape, and deleting it would
trade a privacy gain for an item-3 loss that buys nothing.

The gate is the **first** check in `ClipManager.admits`, ahead of the plugin
filter, the score bar, the rate limit and the disk guard, and
`test_the_privacy_gate_is_checked_before_every_resource_rule` asserts that
ordering. Every other rule there is a resource decision an operator may tune;
a gate placed after one of them stops applying whenever that one
short-circuits first. Refusals are counted (`clips.skipped_human_audio`,
surfaced in the clip snapshot) and logged — a privacy control whose effect
nobody can see is a promise rather than a mechanism.

`oo clips purge-human-audio` deals with what a station already has. It deletes
the files and marks the `media_asset` rows `reclaimed_at` /
`reclaim_reason='privacy_human_audio'`, exactly the shape `retention.py` uses
when a clip ages out (ADR-026), so `/api/v1/media/{id}` keeps answering 410
rather than 500. **Detection rows are never touched.** This is a delete rather
than a withdrawal because the charter's "withdraw, not delete" rule is about
*records*; clip bytes have always been deletable, and this is an existing
operation with a new reason for it, not a new kind of operation.

### Honesty second: a sound category is not a taxon

`detectors/birdnet_classes.py` is the catalogue: eleven labels — `Dog`,
`Engine`, `Environmental`, `Fireworks`, `Gun`, `Human non-vocal`, `Human
vocal`, `Human whistle`, `Noise`, `Power tools`, `Siren` — each with a kind,
of which only `human` changes behaviour.

**How they were identified, and why the list is curated rather than computed.**
Read from the real shipped `birdnet_labels.txt` on the live station
(6,522 lines, en_uk, sha256 `487937b6…` per `models/manifest.tsv`): exactly
thirteen entries have `scientific == common`, and two of those thirteen —
`Gryllus assimilis` and `Miogryllus saussurei` — are genuine binomials for
real crickets with no vernacular name in this list. The remaining eleven are
the sound categories. Seven are single words and so cannot be binomials by
shape; four (`Human non-vocal`, `Human vocal`, `Human whistle`, `Power tools`)
are a capitalised word followed by a lowercase word and are shaped **exactly**
like `Turdus merula`. There is no string rule that keeps the crickets and
rejects "Power tools", which is why this is a list and not a regular
expression. `tests/test_birdnet_classes.py::TestAgainstTheShippedLabels`
re-derives the eleven from the real file and skips when the file is absent
(ADR-006 — the labels are never committed); it was run against the station's
own copy on 2026-08-09 and the derivation matches exactly.

These classes now emit `rank=None`, `scientific_name=None` and
`taxonomic_group='acoustic_event'`, plus `native_result.sound_kind`.
`acoustic_event` is deliberately the **existing** sentinel — it is already in
`normaliser.NON_TAXONOMIC_GROUPS`, which is what stops `_canonical_taxon_id`
minting `sci:engine`, and `detectors/activity.py` has emitted it since
Milestone 1. `common_name` is kept: "Engine" is an honest description of what
was heard, and it is exactly the signal the operator's history view wants.

**Should the normaliser's existing guard have caught this? Partly, and the
gap is instructive.** `_check_claims` asks whether this *plugin* may make
taxonomic claims at all, keyed on `NON_TAXONOMIC_PLUGINS = {activity-v1}`.
BirdNET may, so it was exempt from any scrutiny of whether an individual claim
was well formed — nothing anywhere looked at the claim itself. A second,
per-detection, detector-agnostic check now runs: `rank == "species"` requires a
`scientific_name` shaped like a binomial, and raises `ClaimViolation`
otherwise. It is a backstop and should never fire now that the detector
classifies its own output; it exists so a future adapter with the same bug is
caught without anyone remembering to add it. It catches seven of the eleven
(the single-word ones) and, for the reason above, **cannot** catch the other
four. Stating that plainly is the point: the shape check is not the fix, the
catalogue is.

### The repair third

`band_for` gains `non_taxonomic`, checked before the range model is consulted
at all, returning a new `non_biological` band at the ordinary in-range bar. The
detector and `plausibility_repair` share it, as ADR-032 intended — one
definition of "implausible", not two.

**Measured on the live station's database, read-only, 2026-08-09** (68,023
rows by then; the command's default `--limit 5000` reproduces the operator's
original figure exactly):

| | without the exemption | with it |
|---|---|---|
| `--limit 5000` (the default) | **114** | **23** |
| whole table (`--limit 200000`) | **369** | **123** |

The 246 rows the exemption removes are precisely 203 `Engine`, 25 `Human
vocal` and 18 `Dog` — every one of them a correct detection. What remains at
the default limit is 23 genuinely implausible species: Flammulated Owl,
Grey-winged Inca-Finch, Great Horned Owl, Barred Owl, both Screech-Owls,
Buff-bellied Pipit, Northern Rough-winged Swallow, Gray Wolf and others.
Across the whole table the largest group is 62 `Spotted Crake` at occurrence
4.14e-04 with scores to 0.973 — below the 5e-04 floor, and older than the most
recent 5,000 rows, which is why the operator's first dry run never saw them.

`oo detections reconcile-taxonomy` corrects the historical rows: `rank` to
NULL, `taxonomic_group` to `acoustic_event`, `scientific_name` and
`canonical_taxon_id` cleared, `common_name` and everything else untouched,
**no row deleted**, and the original four values preserved verbatim under
`native_result.taxonomy_review` with a timestamp and a reason. Dry-run by
default, `--json`, confirmation before `--apply`, idempotent.

**Why this one rewrites typed columns when ADR-043 says the original claim is
never edited and ADR-044 marks rather than rewrites.** Both precedents are
about a *claim*: which species this was. Nobody is proposing that "Engine" was
really a wren. `rank` and `taxonomic_group` say what *kind* of statement the
row is; they were set by this pipeline rather than by the detector, and they
are false. A marker alone cannot fix what they do: `/api/v1/history`'s species
list and `/api/v1/taxa/activity` `GROUP BY` those columns and would keep
counting engines among the garden's birds, and `GET /api/v1/taxa/search`
(ADR-043 point 6) offers `sci:engine` as a taxon a reviewer can correct a real
bird *into*. So ADR-044's binding rules are kept — nothing deleted, the
original preserved and attributable — while declining to leave a knowingly
false category assertion in a column four consumers aggregate over. That is
also why this command does *not* skip human-reviewed rows the way
`find_implausible_detections` does: the review workflow has no field in which
a human could have endorsed a rank, so skipping would leave the false claim
standing on precisely the rows somebody cared enough to look at. No `Review`
row is read or written.

**Metrics.** `oo_birdnet_non_biological_total{plugin_id}` is a **separate**
series, not another `reason` label on `oo_birdnet_suppressed_total`: these
detections were admitted, not suppressed, and a number shown to a human must
mean what its label says.

**Known limitations, stated rather than discovered later.**

* **`Gray Wolf` is still filed under `taxonomic_group='bird'`.** `Canis lupus`
  is a real binomial and a real species, so it is correctly outside this
  catalogue and correctly flagged as implausible in a UK garden — but BirdNET
  GLOBAL 6K contains mammals, amphibians and insects as well as birds, and
  this adapter has no way to tell which is which. Fixing that needs a real
  taxonomy source, which ADR-043 argued at length against introducing. One row
  on the live station today.
* **Nothing reads `sound_kind` on a presentation surface.** The web UI, MQTT
  and the counter-top display treat a corrected row as any other
  `acoustic_event`, which is honest but plain. Rendering an engine differently
  from an unidentified acoustic event would be a genuine improvement and is
  not done here.
* **The purge is by label, so it cannot find human speech BirdNET did not
  label as human.** A conversation recorded incidentally inside a clip of a
  blackbird is still on the disk. Bounding that is what the retention ladder
  is for, not this command.

### Migration

**None.** No schema change: `sound_kind` and both audit blocks live inside the
existing `native_result` JSON column, and the purge uses `media_asset`'s
existing `reclaimed_at`/`reclaim_reason`. Alembic head stays `0006_refinement`.

### Rollback and smoke test (ADR-049)

`git revert` restores the previous behaviour. Values already written by the
two repair commands are unaffected by the revert: a `taxonomy_review` block
would simply go unread, and the corrected columns would stay corrected (which
is the safe direction — they would merely be repopulated wrongly for *new*
detections). `clip_human_audio` reverts to not existing, which means clips of
human speech resume; that is the one thing a reverter must know.

```bash
# 1. What the plausibility repair proposes now. Compare with the pre-change
#    figure: 114 -> 23 at the default limit on this station.
oo detections reconcile-plausibility --json > /tmp/plausibility.json

# 2. What the taxonomy repair proposes. Expect Engine / Human vocal / Dog only.
oo detections reconcile-taxonomy --json > /tmp/taxonomy.json
python3 -c 'import json,collections;print(collections.Counter(r["common_name"] for r in json.load(open("/tmp/taxonomy.json"))))'

# 3. What human audio is stored. Expect 48 assets / 24 detections / ~125 MB
#    on the development station.
oo clips purge-human-audio --json > /tmp/human-audio.json

# 4. After applying: the engine is still in the record and is no longer a bird.
curl -s 'http://<station-host>:8080/api/v1/detections?limit=200' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin)["detections"]; print([(r["common_name"], r["rank"], r["taxonomic_group"]) for r in d if r["common_name"]=="Engine"][:3])'

# 5. And it is no longer in the species tally or the taxon search.
curl -s 'http://<station-host>:8080/api/v1/taxa/search?q=Engine'

# 6. No new clip of human speech is written.
curl -s http://<station-host>:8080/api/v1/status \
  | python3 -c 'import json,sys; c=json.load(sys.stdin)["clips"]; print(c["skipped_human_audio"], c["policy"]["clip_human_audio"])'
```

## ADR-050: The counter-top display gets two OTA app slots and updates itself from the station, with its own rollback

**Status:** active. **Written unflashed, then flashed and verified on hardware
the same day, 2026-08-09** — including a deliberate rollback drill. The
"What is unverified" section near the end is kept as it was written, with the
verification appended after it, because what a cable found that a test suite
could not is the transferable part.

**Decision:** Replace the inside observer's single-app-slot partition table with
two equal OTA slots, and give the station a firmware image to serve and a button
to roll it out. The display fetches the image over the WebSocket connection it
already has (ADR-038), verifies a SHA-256 before anything becomes bootable, and
puts the previous build back by itself if the new one cannot reach the station.
**NVS stays exactly where it is**, so the WiFi credentials inherited from the
the board's previous *Aura* firmware — which nobody has ever seen and nobody can retype —
survive.

### Why this is a change worth making on a working display

ADR-023 kept the stock partition table byte for byte, for two reasons that are
both still good: NVS at `0x9000` holds credentials this project never captured,
and a whole-image restore of the stock firmware stays a plain `write_flash 0x0`.
What that ADR did not weigh — because the display was then on a bench next to a
laptop — is that the stock table has **one** app partition. There is no `ota_1`.
`esp_ota_get_next_update_partition` has nowhere to write and `otadata` has
nothing to arbitrate, so ESP32 OTA cannot work at all, and every future firmware
change is a physical trip and a USB cable, forever.

Changing a partition table requires exactly the physical access such a trip
provides. So the cost of *not* doing it during one is not "we do it next time";
it is "every future change costs another trip, until one of them happens to be
the trip where we remember". That asymmetry is the whole argument.

### The layout, and every number in it

```
0x000000  bootloader                       written by upload
0x008000  partition table       0x1000     this table
0x009000  nvs                   0x5000     UNCHANGED - see below
0x00e000  otadata               0x2000     unchanged; now has two slots to arbitrate
0x010000  app0 / ota_0        0x1F0000     1,984 KB
0x200000  app1 / ota_1        0x1F0000     1,984 KB
0x3F0000  coredump             0x10000     unchanged offset and size
                              --------
                              0x400000     4 MB exactly, nothing spare
```

**NVS is not negotiable and is not moved.** Same offset, same size, same
position in the table. `esptool write_flash` writes only the offsets it is
given, and a normal upload writes `0x1000`, `0x8000`, `0xe000` and `0x10000` —
so NVS is untouched by the flash that installs this table. It stays untouched
only while it occupies exactly these bytes: moving or resizing it invalidates
every entry, including `nvs.net80211`, and there is no copy of those credentials
anywhere.

**The stock restore is unaffected.** `write_flash 0x0 firmware-backup.bin`
writes all 4 MB including the stock partition table at `0x8000`, so it
overwrites this table along with everything else. The restore does not need to
know this table exists, and the README's restore command is unchanged.

**Where the space comes from.** The stock 3 MB `app0` and the 896 KB `spiffs`
partition. SPIFFS was reclaimed rather than kept because nothing in `src/` ever
opened a filesystem — unused flash on a device you cannot reach is worth
nothing, whereas headroom on it is worth a journey. `coredump` keeps its stock
offset so a panic dump still comes from the address the old notes name.

**Why 1,984 KB and not 1,536 KB.** Both fit. The build that motivated this
change was 962,437 bytes; adding the OTA client itself — `HTTPClient`, `Update`
and mbedtls' SHA-256 — took it to **1,127,649 bytes, 55.5% of a slot**. At
1.5 MB slots that would already be 75%, and the feature that makes a slot
necessary is also the feature that fills a sixth of it. The remaining 883 KB is
the point of the size, not slack: a slot the firmware outgrows in six months
converts a software update back into a car journey, which is the cost this whole
ADR exists to buy out.

### Push, or check on connect? Both, and they catch different displays

- **Push over the existing channel.** A new frame type on ADR-038's socket
  (`{"t":"u","fv":…,"sha":…,"sz":…,"p":…}`, under 200 bytes). The station is
  already connected to every display it would need to tell, so the alternative —
  the display polling for an update — would put periodic requests back on a wire
  whose entire justification was 11.4 B/s. Push costs nothing when there is
  nothing to say.
- **A version check on connect.** The display appends `&fw=0.2.0` to the socket
  URL and the station offers the published image if it is strictly newer. Not
  redundant with push: push reaches a display that has been connected for a week
  and would never ask; the connect check reaches one that was unplugged,
  rebooting, or on the far side of a Wi-Fi outage while the rollout ran. Neither
  sends a byte when the versions already agree.

`fw` is also how `/api/v1/station` and the settings page can say which build is
on the glass — and say **"unknown"** for a display predating this ADR, which
does not report one. Unknown is not the same claim as out of date, and the UI
says so.

### Safety is the requirement, not a feature

A bricked display is a physical trip, which is the exact cost being bought out.
So the mechanism is designed around its failures rather than its successes:

- **The digest is checked before anything is committed.** SHA-256 is computed
  over every byte as it arrives and compared *before* `Update.end()`. A truncated
  or corrupted download costs ninety seconds and nothing else. The size is
  checked twice — against the offer, and against the response's `Content-Length`
  — and a chunked response is refused outright, because an image of unknown
  length cannot be checked before it is committed.
- **Rollback is the bootloader's, so it does not depend on this firmware being
  correct.** `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y` in
  `framework-arduinoespressif32` 3.20017 (read out of that package's
  `tools/sdk/esp32/sdkconfig`, not assumed), so a freshly written image boots
  once in `ESP_OTA_IMG_PENDING_VERIFY`. If it reboots without being marked
  valid, the previous slot comes back with no help from the application. **A
  crash loop therefore fixes itself on the second boot.**
- **The case the bootloader cannot see is a build that runs happily and cannot
  reach the station.** Nothing reboots it, so nothing rolls it back. Hence a
  ten-minute deadline: no hello frame and no completed portal by then, and the
  firmware calls `esp_ota_mark_app_invalid_rollback_and_reboot()` itself. Ten
  minutes rather than one because a station restarting at the same moment, a DHCP
  lease and a domestic 2.4 GHz band having a bad afternoon are each several
  minutes of not-the-firmware's-fault.
- **What counts as proof is doing the job, not being alive.** Not "WiFi
  associated" and not "socket opened" — a *hello frame understood*. That is what
  this firmware exists to render.
- **Never while somebody is looking at it.** An update is deferred while the
  settings, keypad or portal screens are up, while the glass has been touched in
  the last two minutes, and while the newest row on the feed is under a minute
  old. An ambient display's one time-critical moment is a detection appearing;
  going black a second after a barn owl lands is the worst possible ninety
  seconds to choose.
- **A refusal and a deferral are different.** A malformed offer, an older version
  or an oversized image is dropped and logged with its reason; a deferral keeps
  the offer and re-evaluates it a minute later. Getting this backwards either
  retries a hopeless offer forever or throws away a good one because somebody
  walked past.

**Power loss, specifically, because it is a question with a real answer:**

| When | What happens |
|---|---|
| Mid-download | `Update.write()` fills the *inactive* slot; `otadata` is untouched until `Update.end()`. The board reboots into the image it was already running. The half-written spare slot is simply overwritten next time. Nothing is lost and nothing is decided. |
| Between the digest check and `Update.end()` | The same: nothing has been committed. |
| Between `Update.end()` and the reboot | `otadata` points at the new slot and the new image boots in `PENDING_VERIFY` — exactly where it would have been anyway. Probation proceeds normally. |
| During the *first* boot of a new image | Still `PENDING_VERIFY`. The bootloader rolls back on the next boot. |

### The provisioning portal is not broken, and is treated as a pass

A display that has lost its WiFi must still be recoverable without a cable, so
the portal path is untouched: an update is never started while the portal is
running, and OTA is only attempted with a station host configured and a socket
that has spoken.

One trap this raised, and how it is answered: if the operator reprovisions WiFi
through the portal on a probationary build, the restart that follows would make
the bootloader roll back — throwing away credentials they had just typed. So **a
completed portal submission counts as proof and marks the image valid.** Not for
convenience: the portal is the recovery path that needs no cable, the build
served it, and a build that gets a human out of a WiFi hole is by definition not
bricked.

The converse is deliberate too. A *settings save* on a probationary build that
has never reached the station does roll back, because an image that renders a
settings page but cannot reach the station is precisely the failure this exists
to undo.

### What this does not defend against, said plainly

The image is fetched over plain HTTP on the LAN, and the digest that proves
integrity is supplied by the same party that supplies the image. So this defends
against **corruption**, not **substitution**: anyone who can already answer for
the station's address on this LAN can offer a display a binary, and the digest
they supply will match the binary they supply. The station's own upload
validation (0xE9 magic, chip id, `esp_app_desc_t`) is about the likely *mistake*
— uploading `firmware.elf`, a whole-flash backup, or an ESP32-S3 build — not
about intent.

Signing the image with a key baked into the firmware is the fix, and it is
deliberately **not** in this change: it needs key custody the operator has not
been asked about, and a signing scheme with a lost key turns every future update
back into a cable. It is recorded here as the next thing, not as an oversight.
Mitigating in the meantime: the offer frame carries a *path*, never a host, and
the firmware refuses a `p` that does not begin with `/`, so a frame cannot
redirect the fetch off the station it is already talking to.

### Two implementations of one rule, on purpose

Version ordering exists in C++ (`model/ota_policy.cpp`) and in Python
(`firmware_store.py`), with the same cases asserted on both sides. They are
different processes on different machines, so this is not duplication to be
refactored away — but it *is* a drift hazard, so the rule was made as small as it
can be: 1–4 dot-separated runs of digits, and a refusal to parse anything else.
A suffix scheme ("0.2.0-rc1") is rejected rather than ordered by guesswork, on
both sides, because guessing which of two images is newer is how a display
installs a release candidate over a release and then declines to take the
release back. A station that accepts a version the display refuses to parse is a
rollout that silently never lands — which is also why the Python side rejects
the non-ASCII digits `str.isdigit()` would otherwise accept.

### What was unverified when this was written, and it was not a footnote

> **Superseded the same day — see "Verified on hardware" immediately below.**
> Kept verbatim because it named exactly the right risks, and because three of
> the things it said only a cable could verify turned out to be defects.

**Nothing here has been flashed.** The device was not connected while this was
written. Verified: the host test suite (89 cases, up from 53), the `cyd` build
and its size against the new table, the station's 50 new tests, and that
`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y` in the pinned framework package. **Not**
verified, and only a cable can verify it: that the new partition table installs
cleanly, that NVS survives it in practice, that a display downloads and installs
an image from the station, that the SHA-256 path passes on real bytes, that a
deliberately broken build rolls back, and that the update screen renders.

This matters more than usual because the *first* flash is the one that installs
the new table, and it is the one that must be right — after it, a mistake is
recoverable over the air; before it, nothing is.

### Verified on hardware, 2026-08-09 — and what only a cable could find

Every item the section above said only a cable could settle was settled the same
day, in this order:

1. **The partition table took.** Flashed over the cable and then *read back off
   the device* rather than inferred from the upload: `app1` at `0x200000`,
   subtype `0x11`, two slots. NVS at `0x9000` was never inside an erase range,
   and the display rejoined WiFi on its inherited credentials with no
   reprovisioning — the property this ADR was most careful about, confirmed in
   practice rather than by reading offsets.
2. **Two images installed over the air from the Pi.** `0.2.1` then `0.2.2`:
   digest verified against real bytes before anything became bootable, slots
   alternating `app1` → `app0`, ending
   `[ota] running image marked valid (ESP_OK)`. The update screen rendered.
3. **A deliberate rollback drill, with no cable.** A build that could never reach
   the station booted `(PENDING VERIFY - on probation)` at 17:51:41 and rolled
   *itself* back at 18:01:40 — exactly the 600 s deadline — returning to the
   previous known-good image, which rejoined WiFi six seconds later.
4. `0.2.4` shipped over the air afterwards and is what the device runs now.

**Two real defects were found by doing this, and neither could have been found
any other way.** They are recorded here rather than quietly fixed, because an ADR
that reads as though it worked first time teaches nothing:

1. **The rollback net was disarmed by the framework, and the whole probation
   machinery was unreachable code.** `arduino-esp32` declares
   `verifyRollbackLater()` as a weak symbol returning `false` and acts on it
   inside `initArduino()`, *before* `setup()` runs — so a `PENDING_VERIFY` image
   was marked valid immediately, and `evaluateProbation` could only ever return
   `kNotOnProbation`. Overriding the weak symbol hands the decision back. This is
   the most important sentence in this ADR: **a safety net that never arms is
   indistinguishable, from the outside, from one that never needs to fire.** The
   89 host tests all passed against it.
2. **This ADR's own verification step would have reported failure on success.**
   It told the operator to read `sketch <n> of 2031616` and treat `3145728` as
   the failure signature. The banner did not print that — it printed
   `ESP.getFreeSketchSpace() + ESP.getSketchSize()`, and `getFreeSketchSpace()`
   returns the size of the *next* OTA partition, so a correctly repartitioned
   board read `1134224 of 3165840`. An operator following these instructions
   would have concluded a successful flash had failed and reflashed. The banner
   now prints the running slot's own size and states the two-slot question
   outright. Separately, `scripts/serial_capture.py` — which the firmware README
   told the operator to run at the one moment that matters — did not exist. It
   does now.
3. **Two smaller findings from the drill itself.** The provisioning AP was named
   `Aura` -- the name of the weather-display project this board previously ran,
   not the manufacturer's: two boards in one house would raise two
   identically named open networks. It is now
   `Observatory-<last three MAC bytes>`. And the WiFi failure path printed "no
   usable credentials" for *any* association failure including a plain timeout —
   it fired on a transient radio failure mid-drill and sent the operator hunting
   for wiped NVS when the credentials were fine and the device rejoined by itself
   two minutes later. It now reports the actual `wl_status_t` and says outright
   that this path clears nothing.

**Three defects surfaced that the 89 host tests, the size check and the
framework-config check could not have surfaced.** They are the argument for the
"and this is not a footnote" heading above, so they are recorded rather than
quietly fixed:

1. **The entire rollback machinery was unreachable code.** `arduino-esp32`
   declares `verifyRollbackLater()` as a weak symbol returning `false` and acts
   on it inside `initArduino()`, *before* `setup()` runs — so a pending image was
   marked valid immediately, `evaluateProbation` could only ever return
   `kNotOnProbation`, and nothing this ADR designed could fire. Overriding the
   weak symbol hands the decision back. Confirmed with `0.2.2`: the display boots
   `(PENDING VERIFY - on probation)`, alternates `app1` → `app0`, logs
   `this build reached the station (hello frame); confirming` and then
   `running image marked valid (ESP_OK)`, and `otadata` reads VALID afterwards.
   **The first flash is not the only thing a cable is needed for; a safety net
   that never arms looks exactly like one that never fires.**
2. **The check meant to verify the flash would have reported failure on
   success.** The boot banner printed
   `ESP.getFreeSketchSpace() + ESP.getSketchSize()`, and `getFreeSketchSpace()`
   returns the size of the *next* OTA partition — so a correctly repartitioned
   board read `1134224 of 3165840`, and an operator following this ADR's own
   instructions would have concluded the flash had failed. It now prints the
   running slot's own size and states the two-slot question outright. Also,
   `scripts/serial_capture.py`, which the firmware README told the operator to
   run at the one moment that matters, did not exist. It does now.
3. **Two findings from the drill itself.** The provisioning AP was named `Aura`
   (the previous *Aura* firmware's name, chosen for recognisability), which gives two boards
   in one house two identically named open networks; it is now
   `Observatory-<last three MAC bytes>`. And the WiFi failure path printed
   "no usable credentials" for *any* association failure including a plain
   timeout — it fired on a transient radio failure mid-drill and sent the
   operator hunting for wiped NVS when the credentials were fine and the device
   rejoined by itself two minutes later. It now reports the actual `wl_status_t`
   and says outright that this path clears nothing.

### Rollback and smoke test (ADR-050)

The station half reverts cleanly: `git revert` removes the endpoints, and the
display never receives an offer. `data/firmware/` can be deleted by hand or with
`DELETE /api/v1/firmware`; nothing else reads it. No schema change, no migration,
no new dependency on either side.

The firmware half is asymmetric, in the safe direction. Once the new partition
table is installed, reverting the *application* is an over-the-air update or a
cable flash exactly as before; reverting the *table* means a cable, and there is
no reason to — the old table is a strict subset of what this one can do, and the
whole-image stock restore works either way.

```bash
# 1. Host logic, on a laptop. 89 cases now: the 53 from ADR-038 plus version
#    ordering, the digest rule, the deferral gate and the rollback deadline.
cd firmware/inside-observer && pio test -e native

# 2. The station's half, without a display.
pytest -q tests/test_firmware.py

# 3. Publish a build and see who is behind.
curl -s -X POST --data-binary @firmware/inside-observer/.pio/build/cyd/firmware.bin \
  -H 'content-type: application/octet-stream' \
  'http://<station-host>:8080/api/v1/firmware?version=0.2.1'
curl -s http://<station-host>:8080/api/v1/firmware | python3 -m json.tool

# 4. Roll out. "offered" is how many were *told*; it is not "installed".
curl -s -X POST http://<station-host>:8080/api/v1/firmware/rollout

# 5. What actually landed, which only the display can say.
curl -s http://<station-host>:8080/api/v1/station \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["display_channel"]["per_client"])'
```

On the serial monitor, the lines that constitute a pass:

```
app slot   : app1  (PENDING VERIFY - on probation)
[ota] station offers 0.2.1 (1127649 bytes)
[ota] 1127649 bytes verified against the offered digest
[ota] this build reached the station (hello frame); confirming
[ota] running image marked valid (ESP_OK)
```

And the one that constitutes a *successful failure*, worth provoking deliberately
once with a build pointed at a nonexistent station host:

```
[ota] this build never reached the station; rolling back
```


## ADR-053: Taxonomic grouping above species — genus is free, family is a data dependency

**Status: proposed. Nothing is implemented.** Recorded because the question has
a tempting wrong answer, and because the cheap option and the correct option are
different things that are easy to conflate.

**The question.** Does the detector expose any layer of classification between a
species name and nothing at all — families, for instance? Can it group corvids?

**The answer, measured rather than assumed.** No.

`models/birdnet_labels.txt` is 6,522 lines of `Scientific name_Common name` and
nothing else. It contains zero occurrences of "family", "order" or "Corvidae".
BirdNET GLOBAL 6K V2.4 emits one score per label at species level; there is no
hierarchy in the asset.

Two fields in our own schema look like they might help and do not. Values as
they actually stand in the live database:

| field | values |
|---|---|
| `rank` | `species` (9,620) or `None` (61,582) |
| `taxonomic_group` | `bird` (9,620), `bat` (5,532), `acoustic_event` (56,050) |

`taxonomic_group` answers "what kind of claim is this" — bird, bat, or a sound
that is not an animal (ADR-049). It is not a taxonomic rank and must not be
pressed into service as one.

**What is free.** The genus is the first token of the binomial, so grouping by
genus needs no new data, no dependency and no licence — 1,843 distinct genera
across the label file, derived exactly from what we already store.

**Why that is not sufficient, using the operator's own example.** Corvids
recorded by this station:

| n | species | genus |
|---|---|---|
| 785 | Eurasian Jackdaw | *Corvus* |
| 240 | Rook | *Corvus* |
| 12 | Carrion Crow | *Corvus* |
| 7 | Common Raven | *Corvus* |
| 25 | Common Magpie | *Pica* |
| 1 | Eurasian Jay | *Garrulus* |

Grouping by genus captures 1,044 of those and **silently drops the Magpie and
the Jay**, which are Corvidae. A "corvids" view built on genus would be
confidently incomplete, which the honesty constraint forbids more strongly than
it forbids having no such view at all. Worse, some authorities place the
Jackdaw in *Coloeus* rather than *Corvus*, so even the largest group depends on
which taxonomy the label list happened to follow.

**Decision (proposed).**

1. **Genus grouping may ship as genus**, labelled as genus, never as family. It
   is exact, free, and honest about its own scope.
2. **Family and order require a real taxonomic reference** — a checksummed,
   versioned, separately-licensed data file acquired the way model assets are
   (ADR-006), not a table typed into the source. eBird/Clements is the natural
   choice because BirdNET's own labels derive from it, so the join is clean
   rather than fuzzy-matched.
3. **A hardcoded list of corvid species is refused.** It is the tempting answer,
   it works for the example that prompted the question, and it rots silently the
   first time a species is added or a genus is revised. Naming it here so a
   successor under time pressure has to argue with this paragraph first.
4. **Whatever ships must handle labels that do not resolve.** A species absent
   from the reference is reported as ungrouped, never assigned to a plausible
   family by prefix matching.

**Relationship to ADR-043.** ADR-043 argued against introducing a taxonomy
dependency when the payoff was a single mislabelled `Gray Wolf` row. That
reasoning stands for that case. The payoff here is larger — family-level
browsing and history for a general user — so this is a fairer trade than it was
then, and is why this is recorded as a proposal rather than a refusal.

**Cost, honestly.** Genus is hours. Family is a day or more once acquisition,
checksums, licence documentation, the unresolved-label path and the UI are
counted. Neither should displace the 72-hour soak.
## ADR-051: The spectrogram says where the sound you are hearing is, as a measured interval rather than a line

**Decision:** The live spectrogram draws a playhead marker: an amber band across
the frequency axis at the point on its own time axis corresponding to the audio
currently leaving the speakers, with a hairline through the middle of it and a
badge reading `hearing 2.4 s ago ±0.3 s`. It is drawn from the same
`newestUtc + elapsedColumns()` anchor and the same `spanRect` the detection
overlay uses, so it cannot drift out of agreement with the boxes beside it, and
it is correct in both `scroll` and `waterfall` (`playhead.test.ts` asserts both).
Entirely client-side: no new endpoint, no new header, no server change of any
kind. `web/src/components/playhead.ts` holds the whole estimate as pure
functions.

**Reason:** The operator listens live while watching the scrolling display, and
what he hears is seconds behind what he sees. Nothing on screen said by how
much, so the two surfaces silently contradicted each other. On this station
that is not a cosmetic problem: he uses the picture to decide whether the call
he just heard is the shape in front of him.

**The reason this is an ADR and not a line of canvas code** is the charter's
honesty constraint. A marker confidently in the wrong place is worse than no
marker, because it will be believed and acted on. So the offset had to be
*established*, not assumed, and what could not be established had to be shown as
width rather than rounded away.

### What is knowable, and how

Everything is in station UTC — the clock the spectrogram's columns and the
detections already carry — so the marker is a subtraction on one timeline rather
than a parallel wall-clock estimate. `LiveConnection.clockSkewS`, already
measured at the live socket's `hello`, does the conversion. The visual
pipeline's own lag cancels exactly instead of being modelled.

Two independent estimators of the station-UTC of the sound at the speakers:

- **B, buffer-anchored:** `now − bufferedAhead − outputLatency`. The newest
  sample the element holds arrived a moment ago; the play cursor is
  `bufferedAhead` behind it.
- **A, epoch-anchored:** `streamOpened + currentTime − outputLatency`. Media
  time 0 is the first sample the server sent, and `live_audio_wav` drains its
  queue before streaming, so that is the audio which was live when the element
  opened the stream.

On a stream that behaves these are algebraically identical. They part company
for exactly two reasons — a browser reporting `buffered` staler than what it
holds (B too new), and the server shedding the oldest chunk from this listener's
bounded queue (A too old) — and **from inside the browser those two are
indistinguishable, with the same sign**. That is the real limit on this
measurement. So the pair is treated as a *bracket*: the reported value is the
midpoint, and half the gap is added to the claimed uncertainty. Neither
estimator is ever trusted alone.

Exactly one term is estimated rather than read: the browser/OS output buffer
between `currentTime` and the speaker. A media element exposes no equivalent of
`AudioContext.outputLatency`, and Web Audio is unavailable to this UI at all
(ADR-019), so it is carried as an interval, 0.02–0.20 s, half of which is
charged to the uncertainty along with 0.05 s for the skew measurement's own
round trip. That gives a floor of **±0.14 s** on a healthy stream, and the
observed working figure is **±0.26 s**.

### Measured, against ground truth

`scripts/measure_playhead_offset.py` is the rig, and it is in the repository so
the claim is re-checkable rather than remembered. It streams the same
endless-header WAV shape at the same 48 kHz in the same 40 ms real-time chunks,
and **records the wall-clock instant it wrote every chunk** — so for any media
time the browser reports, it knows exactly when that audio was live. The page
posts its readings back and imports the *real* `playhead.ts`, bundled, not a
copy of the arithmetic.

Run against headless Chromium 150 (the operator's browser family), 169 samples
over 45 s:

| | median | worst |
|---|---|---|
| True lag of the playhead behind live | 2.47 s | 2.49 s |
| Error of the reported centre | **+0.03 s** | **0.13 s** |
| Claimed half-width | 0.26 s | 0.35 s |
| Gap between the two estimators | 0.24 s | 0.42 s |
| **True playhead inside the claimed band** | **169 / 169** | |

The 2.5 s is the operator's "a few seconds", now a number: it is the browser's
own media buffering, not the station.

An earlier design reported B alone and widened by the whole gap. The same rig
rejected it: 804 samples, centre error median +0.05 s and worst 0.25 s, with a
band nearly twice as wide (±0.48 s median). The bracket midpoint is both tighter
and more accurate, and the measurement is what chose it.

**What this rig could not measure, stated plainly rather than glossed:**

- **The output buffer.** Headless has no DAC, so the one estimated term is the
  one term still untested. It is why a band is drawn at all.
- **The network hop and the station's own block staleness.** Loopback has
  neither. Both push the truth *older*, in the same direction the output-latency
  correction already moves the estimate, so the residual error on the real
  station is expected to be smaller than the +0.03 s bias measured here, not
  larger — but that is an argument, not a measurement.
- Nothing was measured on the station itself, deliberately: the operator was
  tuning detector thresholds on it and a deploy would have voided the run.

**The honest summary, which is what the badge says:** the marker is good to
roughly **a quarter of a second** on a healthy stream, against a lag of two to
three seconds. That is comfortably enough to tell which call you are hearing and
nowhere near enough to time an onset, and the band is drawn at true scale so the
display makes that distinction itself.

### Where it says nothing

No marker at all — never a stale one — when the element is paused, when
`readyState` is below `HAVE_FUTURE_DATA`, when `currentTime` did not advance
since the last sample (rebuffering), or before playback has started. A frozen
playhead is not where the sound is; it is where the sound stopped. When the
whole interval falls outside the selected history window the band is not drawn
at all rather than pinned to an edge — pinning asserts a position, and the
position it asserts is wrong — while the badge still reports the number. Past
±1 s the hairline is dropped and only the band is drawn, because at that width a
single line overclaims.

**Cost when nobody is listening: nothing.** `playhead` is `null`, no interval
runs, and the overlay's only expense is one ref read per frame. ADR-040's
premise — the steady state of this station is nobody watching — is not weakened.

**Rollback:** revert the commit. The marker is additive; `playhead` is an
optional prop and the four new `AudioTelemetry` fields are genuine readings that
nothing else depends on.
## ADR-052: A counter is not a diagnostic — record what BirdNET proposed and refused, in a bounded ring with per-band score histograms

**Status:** active. Extends ADR-032's four suppression counters with the
evidence behind them, and is wired through ADR-048's settings mechanism.

**Context, measured on the live station, 2026-08-09.** The operator was
listening to the live stream, could hear bird calls, and saw no detections for
them. BirdNET was demonstrably healthy: 998 windows analysed, **zero dropped**,
0.26 s lag — the audio was reaching the model. `oo_birdnet_suppressed_total`
reported **152 suppressed as `implausible_prior`** and 35 as `out_of_range`
inside an hour, and every named detection since 16:40 scored **0.553–0.974**,
piled hard against `birdnet_threshold_in_range = 0.55`.

And that was the entire body of evidence available. `birdnet.py` incremented a
counter and dropped the candidate on the floor: no log line, no table, no
endpoint, nothing anywhere recording **which species** was suppressed, at what
score, with what occurrence prior. So the operator could see that 152 things
were rejected and had no way to learn whether those were 152 correct
rejections of American owls or 152 wrongly-binned garden birds.

That is this project's own recurring failure mode, and the charter names it: a
coverage figure capable of reading 1302%, an "audio lost" figure over-reporting
by 12.9x, four counters that were confidently lying. *A number shown to a human
must mean what its label says* — and `152` means nothing at all on its own.

**Two of the four counters' scope is also wrong for this question.** By
ADR-032's design, `_count_suppressed` counts only the *plausibility* bands. A
candidate in the `in_range` or `unfiltered` band that scores 0.54 against a
0.55 bar is counted **nowhere** — and that is precisely the case an operator
hits first when they can hear a blackbird and the station reports nothing.

**Decision.** A new `detectors/near_miss.py` holds a `NearMissLedger`, which
every `BirdNetDetector` now owns and writes to for every rejected *and* every
admitted candidate, in every band. Three shapes, cheapest first:

1. **Per-band score histograms and counts** — twenty 0.05-wide bins of
   rejected score per band, plus the admitted count so the rejection figure has
   a denominator. This is the summary that actually chooses a threshold: "you
   rejected 400 candidates, 380 of them below 0.2 and 20 between 0.45 and 0.55"
   answers the question; "400" does not. A few integer increments.
2. **A per-species tally** — count, best rejected score, shortfall, band,
   prior, last seen, keyed by label index. This is the table a person tunes
   from, because it names the bird.
3. **A bounded ring of individual near misses** with timestamps, so the last
   few minutes can be lined up against what a person actually heard.

Served at `GET /api/v1/detectors/near-misses` (duck-typed on
`near_miss_snapshot`, exactly as `plausibility_snapshot` is — a detector with
no plausibility bands is absent rather than carrying an empty stub), rendered
in the web UI's `?view=diagnose` depth as **Rejected candidates**, directly
under the detector panel where "998 windows, 0 dropped" is read, and exported
as `oo_birdnet_candidates_{rejected,admitted}_total{plugin_id, band}` — a
different series from `oo_birdnet_suppressed_total`, because it covers bands
that metric deliberately does not.

**Measured cost, on the hardware, before anything else.** ADR-033 is the
precedent that makes this non-negotiable: a 10-second retention sweep cost
~1.9 capture gaps/minute. Measured with `scripts/bench_near_miss.py` on the
Raspberry Pi 5 itself (the module and a driver piped to the station's own
interpreter over stdin — roughly 60 ms of CPU in total, less than one BirdNET
window, nothing written to the station and nothing restarted):

| | Pi 5 | dev laptop |
|---|---|---|
| `record_rejected`, ring + histogram + species | **2.00 µs** | 0.73 µs |
| `record_rejected`, histogram + species only (ring off) | **0.97 µs** | 0.28 µs |
| `record_admitted` | — | 0.09 µs |
| `snapshot()` (API read, off the hot path) | — | 0.065 ms |

At ten rejected candidates per window that is **20 µs per window against
BirdNET's own 72.36 ms p95 — 0.028%**, or 1.3e-5 CPU-seconds per second of
audio at the 1.5 s stride, against a station `hot_path_cpu_ratio` measured at
**0.105** the same day. It is also not on the capture path at all:
`DetectorWorker._analyse_sync` runs the plugin on its own worker thread, so
this is spent where BirdNET's 72 ms is already spent, not where capture's
frames are.

**On by default, and the justification for that rather than for a flag.** A
diagnostic you must remember to enable *before* the thing you want to diagnose
happens is not there when you need it — the operator's whole problem was
retrospective. At 0.028% of the detector's own budget there is no version of
this worth switching off, and a fifth-decimal saving is a worse trade than the
certainty that the record exists when someone asks. What *is* a setting is the
ring depth: `birdnet_near_miss_ring`, default 200, live-tier, web-editable
(ADR-048), mapped in `tuning.py` so it genuinely takes effect on a running
detector. 0 keeps the histograms and the species tally — the part that decides
a threshold — and stops keeping individual rows. A resize keeps the cumulative
record, because comparing it either side of a threshold change is the entire
use.

**Bounded, by construction, at three points.** The ring is a
`deque(maxlen=...)`, which discards in C. The species table stops admitting
*new* species at `max_species` (512) and counts the ones it turned away, so the
omission is visible rather than silent — and is bounded above by the label
catalogue regardless. The histograms are seven bands × twenty ints, fixed for
the life of the process. There is no growth term anywhere. Nothing is written
to the database, so the SD card's write budget is untouched.

**Privacy: metadata, not evidence.** No audio is extracted for a rejected
candidate, no clip is written, no row is persisted; the whole structure lives
in memory and dies with the process. This does not reopen ADR-049's decision
that human sound gets a detection row and no clip: a near-miss record carries
the same class of fact ADR-049 already permits a detection row to carry
("something the model thought was a human vocal, at 18:55"), contains no
speech, and cannot be exported to a file. It is deliberately *less* durable
than the rows ADR-049 allows.

**Honesty details that are not decoration.**

- The `implausible` band's bar is `math.inf` (ADR-032). Its `threshold` and
  `shortfall` are reported as `null`, never as a large number: a distance would
  imply the candidate was merely a long way off rather than refused on
  principle, and "not applicable" stays available to the surface.
- Every band appears in the payload even having seen nothing, as an explicit
  zero. "We rejected none of these" and "we have no idea" must not look alike.
- The thresholds on the payload come from the **detector instance**, not from
  `Settings`. A settings write and a detector retune are two steps; reporting
  the saved value next to rejections judged under the old one is exactly the
  saved-versus-in-force dishonesty ADR-048 exists to prevent.
- The payload states that nothing below `min_confidence` is ever a candidate,
  so the histogram's lowest bins are structurally empty and are not evidence
  that the model is quiet down there.
- A species' band and prior are re-stamped on every rejection rather than kept
  from first sight: the operator retunes mid-session, and a stale band would
  describe a decision no longer being made.

**Thread safety, stated rather than locked.** One writer (the detector's
analysis thread) and any number of readers (the API event loop). Every mutation
is a single operation on a built-in container, so no lock is taken on a path
adjacent to the detector's budget. A reader can see a histogram one candidate
ahead of a species tally. That is acceptable for a diagnostic and is written
down instead of being papered over.

**Known limitations.**

- **In-memory only: a restart loses the record.** That is the deliberate
  privacy and SD-endurance trade (no rows, no bytes, no writes), and it means
  the ledger cannot answer "what did you reject last Tuesday". For that,
  `oo detections reconcile-plausibility` still reads the durable table.
- **No `oo` subcommand.** The CLI has no HTTP client and inventing one for this
  would be a larger change than the feature; the `curl` recipes in
  `DEPLOYMENT_AND_OPERATIONS.md` cover the terminal case exactly.
- **The score histograms are not in Prometheus.** Twenty buckets × seven bands
  is a scrape's worth of series for something read interactively; the per-band
  totals are exported and the distribution is served on demand.
- **Only BirdNET has one.** The activity and ultrasonic detectors have no
  plausibility bands and are correctly absent from the endpoint rather than
  carrying an empty stub.
- **Not yet run against real BirdNET inference.** The model assets are
  unbundled (ADR-006) and this was never deployed — the operator was tuning on
  the station throughout. Every test here uses a stub interpreter with the
  exact measured scores and priors from the live database, and the cost figure
  is from the Pi. Whether the species table's 512-entry bound is generous or
  tight against a real dawn chorus has not been observed and should be checked
  on the first deploy.

### Migration

**None.** No schema change, no new dependency, no Alembic revision. One new
`Settings` field with a shipped default; Alembic head stays `0006_refinement`.

### Rollback and smoke test (ADR-052)

`git revert` restores the previous behaviour exactly: the endpoint disappears,
the panel degrades to its "not available" state rather than breaking the page
(the fetch 404s and is caught), and `birdnet_near_miss_ring` in an operator's
`config/runtime.env` becomes an unread key, which `RuntimeEnvStore` preserves.
Nothing was written to the database, so there is nothing to unwind.

```bash
# 1. The cost, on the target device, before anything else.
PYTHONPATH=src ./.venv/bin/python scripts/bench_near_miss.py

# 2. What was refused, named.
curl -s http://<station-host>:8080/api/v1/detectors/near-misses \
  | python3 -c '
import json,sys
d = json.load(sys.stdin)["detectors"][0]
print(d["rejected_total"], "rejected /", d["admitted_total"], "kept")
for s in d["species"][:10]:
    print(" ", s["common_name"], s["rejected"], s["best_score"], s["band"], s["shortfall"])'

# 3. The histogram that chooses the bar.
curl -s 'http://<station-host>:8080/api/v1/detectors/near-misses?limit=0&species_limit=0' \
  | python3 -c '
import json,sys
for b in json.load(sys.stdin)["detectors"][0]["bands"]:
    if b["rejected"]: print(b["band"], b["threshold"], b["histogram"]["counts"])'

# 4. An unreachable bar reports no distance at all (expect None/true).
curl -s http://<station-host>:8080/api/v1/detectors/near-misses \
  | python3 -c '
import json,sys
print([(b["band"], b["threshold"], b["threshold_unreachable"])
       for b in json.load(sys.stdin)["detectors"][0]["bands"]
       if b["band"] == "implausible"])'

# 5. The ring depth is genuinely live (expect [] pending, then capacity 40).
curl -s -X PUT http://<station-host>:8080/api/v1/settings \
  -H 'content-type: application/json' -d '{"birdnet_near_miss_ring": 40}'
curl -s http://<station-host>:8080/api/v1/settings \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["pending_restart"])'
curl -s http://<station-host>:8080/api/v1/detectors/near-misses \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["detectors"][0]["capacity"])'

# 6. Per-band accounting reaches Prometheus, including the in_range band that
#    oo_birdnet_suppressed_total deliberately does not cover.
curl -s http://<station-host>:8080/metrics | grep oo_birdnet_candidates

# 7. Nothing new is persisted: this feature must not move the detection count.
#    Compare across a few minutes of rejections.
curl -s http://<station-host>:8080/metrics | grep oo_detections_persisted_total
```

## ADR-054: Responsive layout is intrinsic first, breakpoints last — and overflow is fixed, never hidden

**Decision:** Every row of controls in the web UI wraps and carries `min-width: 0` at
*every* width, rather than being rearranged by a media query at one. Media queries are
reserved for genuine changes of arrangement — a column count, an overlay becoming
ordinary flow — and the whole ladder is four widths, written down in `styles.css`:

| | |
|---|---|
| 640px | phone: one column; the spectrogram badge strip leaves the plot; touch targets grow |
| 950px | the three panel columns collapse to one |
| 1100px | waterfall spectrograms sit side by side |
| 1500px | three panel columns instead of two |

And: **no `overflow-x: hidden` on `html`/`body`, ever.**

**Reason:** The operator reported that on his phone the `GO LIVE` button could not be
reached. Reproduced in headless Chromium against a station at 360, 390, 414 and 430 px:
at 430 px the channel toggle and the button were cut off mid-word, and at 390 px they
and the clock were gone entirely, the button's right edge sitting at x=487 in a 390 px
viewport.

Two lines caused it, and the second is the more instructive:

1. `.topbar` had `flex-wrap: wrap`, so the *outer* header wrapped — but
   `.topbar-right`, which holds the mode switch, settings, diagnostics, the entire
   listen control and the clock, had none. The overflow happened *inside* that box,
   where the outer wrapping could not help. The mobile block even set
   `.topbar-right { width: 100% }`, which reads as though wrapping had been intended
   and simply never enabled.
2. The mobile block set `html, body { overflow-x: hidden }`. That did not fix the
   overflow; it **hid** it. The page reported a clean `document.scrollWidth` — 390 of
   390 — while a control the operator wanted could not even be scrolled to. A layout
   bug was converted into a measurement that lied about it, which is the same failure
   mode the charter's honesty constraint names elsewhere: sincere, believable, wrong.

This is why the rule is "intrinsic first". A breakpoint fixes one width; there were
only four media queries in the entire 1145-line stylesheet, so every width between them
was unconsidered. `flex-wrap` plus `min-width: 0` degrades continuously and needs no
one to have thought about 414 px specifically.

**Constraint — nothing is hidden to make it fit (ADR-016, ADR-028).** A stat that
disappears at 400 px is a stat an operator cannot check from the garden, which is
exactly where they are standing when they want it. So:

- The six spectrogram badges (`audible`, `80 Hz–15 kHz`, `192 bins`, `24 ms/col`,
  `FFT 2048`, `scroll`) were overlapping the plot and wrapping mid-word at phone
  widths, with detection labels drawn on top of them. The strip is now a **sibling** of
  the plot rather than a child: CSS floats it back over the plot's top-right corner on
  a wide screen — pixel-identical to before — and below 640 px it becomes an ordinary
  wrapping row underneath, where it can neither be clipped nor be drawn over. All six
  badges survive.
- The history window picker has six named windows and cannot fit them on one
  phone-width row. It gets an opt-in `segmented-wrap` variant that breaks into a chip
  group instead of dropping options: "what came through at dawn?" stays answerable
  from a phone.
- The `diagnose` depth on a 390 px screen shows all six header stats, both level
  meters and every spectrogram tuning control. Nothing was moved behind a second
  toggle.

**Constraint — charter item 8 pays nothing.** This is layout only: no polling, no
`ResizeObserver`, no `matchMedia` listener, no additional React state and no re-render
on resize. The only JS-visible change is one extra wrapper `<div>` per spectrogram.
The hover geometry still reads `.spectrogram`'s own `getBoundingClientRect`, so the
readout and the plot cannot disagree.

**Verified, by measurement rather than by reading CSS.** A headless Chromium driven
over CDP loaded the production bundle (`npm run build`, served by the station's own
FastAPI process) at 360, 390, 414, 430, 1280 and 1920 px, in five states each — live,
diagnose, history, settings, and a detection drawer open — and in each asserted that
**no element's bounding rect extends past `document.documentElement.clientWidth`**.
Result: 30 of 30 states clean, from 30 of 30 failing before. At 390 px and 360 px
`GO LIVE` additionally passes a hit test — `document.elementFromPoint` at its centre
returns the button itself, its target is 35 px tall, and clicking it flips the label to
`STOP` and opens the stream. Desktop is unchanged: `GO LIVE`'s bounding box at 1280 px
and 1920 px is pixel-identical before and after. The first-run flow was measured
separately, with `setup_completed` false.

**Not verified:** a real iOS or Android browser (Chromium's device emulation is not
Safari), and the same test over the real Wi-Fi link to the Pi. Nothing was deployed to
the station; the measurements are against a local station on a synthetic source.

**Regression cover:** `web/src/responsive.test.tsx`. jsdom has no layout engine, so it
cannot re-measure a bounding box — but it asserts the two things that actually
regressed silently: the stylesheet's own text (`.topbar-right` and `.listen` carry
`flex-wrap: wrap` and `min-width: 0`; no rule on `html` or `body` clips horizontal
overflow) and the DOM structure the stylesheet depends on (the badge strip is a sibling
of `.spectrogram`, and still contains all six badges). Seven of its eight assertions
fail against the pre-fix code, which was checked rather than assumed.

### Rollback and smoke test (ADR-054)

Rollback is `git revert` of the frontend commit and `npm run build`; the change is
CSS, one JSX wrapper and one class name, and touches no Python, no schema and no
persisted state.

```bash
# 1. Build and serve the bundle locally against a synthetic source.
cd web && npm ci && npm test -- --run && npx tsc --noEmit && npm run build
OO_WEB_DIST=$PWD/dist ./.venv/bin/oo serve --source synthetic

# 2. Nothing may extend past the viewport, at a phone width.
#    (`--mute-audio` matters: this page can start playing the live stream.)
chromium --headless --disable-gpu --no-sandbox --mute-audio --hide-scrollbars \
  --window-size=390,844 --virtual-time-budget=18000 \
  --screenshot=$HOME/shots/after390.png http://127.0.0.1:8080/

# 3. The assertion that matters, run in the page rather than judged by eye:
#    every element's right edge inside document.documentElement.clientWidth,
#    and .go-live topmost at its own centre.
```

## ADR-055: The operator can pause recording for a chosen time — and it expires, survives a restart, and is recorded as a pause

**Status:** active. The first implementation of the charter's privacy
constraint as something an operator *does*, rather than something the system
refrains from.

**Context.** The charter's privacy constraint says: *"A microphone in a garden
records neighbours, visitors and passers-by who never consented."* Everything
built for it so far is passive — no continuous speech retained, no clip for a
human sound class (ADR-049), bounded evidence retention (ADR-026). All of it is
about what the station *keeps* once something has already happened.

None of it helps the case that actually arises. In the operator's words:

> "We have a birthday party for the children at ours today and I would like to
> be able to disable the logging for a given amount of time."

A garden full of other people's children is exactly the population the
constraint names, and it is *known in advance*. The station had no way to be
told. The only available workaround was `systemctl stop open-observatory`,
which is the one thing this project has direct evidence against: closing the
capture device risks it not coming back, and that is what cost this station 29
hours of recording (HANDOVER §3a).

**Decision.** A pause is a first-class operator action with a deadline.

### What "paused" means

While paused the station **persists nothing and publishes nothing**: no
detection rows, no evidence clips, no MQTT, no display pushes of new
detections. **Live listening is refused** — both the WebSocket channel and the
chunked-WAV one, on both the audible and ultrasonic bands. That last one is not
an extra: a pause anyone with the URL can listen straight through is not a
pause. The garden is exactly as audible as it was, and the operator has been
told otherwise.

**Capture keeps running. This is the trade, stated plainly.** The ALSA device is
not closed, the ring buffers keep filling, frames keep being counted and
continuity is unbroken. The alternative — stopping capture — is what an operator
would naively expect and is the wrong engineering choice here, because a privacy
control that occasionally leaves the station unable to reopen its microphone is
worse than the exposure it prevents. The 29-hour outage is evidence, not a
hypothetical. What justifies keeping capture alive is that the ring is transient
process memory, continuously overwritten, that never leaves the process:
**keeping capture running retains nothing.** What a pause stops is every path by
which audio, or a claim about it, escapes.

The gate is in one place: `Station._on_detections`, at the mouth of the
detection path, before normalisation. Everything downstream of it — the row, the
clip, the event bus, and through the bus both MQTT and the counter-top display —
is a way out of the process, so gating once means a consumer added to the bus
next year is paused by construction rather than by whoever adds it remembering
to check. Live audio is gated separately in `_handle_block`, because that path
does not go through a detection at all.

Detectors themselves keep running. Their windows, queues and lag counters stay
honest, so a pause does not make the diagnostics look like a stalled pipeline,
and the detector state an operator reads after resuming is continuous with the
state before it.

**The live spectrogram is deliberately *not* gated**, and the line is worth
naming because it looks inconsistent. What the privacy constraint protects is
people's speech and presence, and the eavesdropping vector is audio: a
spectrogram is a picture of band energy, is not intelligible, and is already
computed only while somebody is watching (ADR-040) and never stored. Keeping it
is also what lets an operator see, at a glance, that the station is alive and
paused rather than dead. If a future reading of the constraint disagrees, the
gate goes in `_handle_block` next to the audio one and costs nothing extra.

### Four properties, each of them a failure mode

**It expires by itself.** The operator will forget; a pause that outlives the
party costs a night of bats, which is a worse outcome than the problem it
solved. Expiry is therefore *read-side and unconditional*:
`PauseController.active` is a float comparison against a stored deadline. There
is no timer to miss and no task to crash — past the deadline it is false and
every gate reopens in the same instant, even if the database, the housekeeping
loop and the API are all broken. The housekeeping loop's `sync()` does not end
pauses; it only closes the durable row of one that has already ended.

**It survives a restart.** What is persisted is the **deadline**, never a
countdown: a remaining-seconds figure written to disk is wrong the moment the
process stops, and would silently extend every pause by the length of the
outage. `Station.start` re-adopts an open row *before* capture starts, so a
process coming back mid-pause has its gates closed before the first block
arrives. A pause whose deadline passed while the station was down is closed at
its **deadline**, not at the moment anyone noticed.

**It is recorded as a pause.** New table `capture_pause` (Alembic
`0007_capture_pause`): one row per pause, opened when it starts and closed when
it ends, carrying the deadline it was *set* to as well as when it actually
stopped, and why (`expired` / `resumed` / `superseded` / `unknown`). Charter
item 2 makes "a quiet night versus a dead microphone" a first-class
distinction; an operator pause is a third thing, and must not be mistaken for
either.

`history.coverage` reports pauses **beside** coverage and never subtracts them
from it. The station really was capturing throughout — deducting the time would
understate coverage and would look, in the record, exactly like the dead
microphone item 2 exists to distinguish. What a pause changes is whether
anything *could* have been detected, which is a different fact and gets its own
fields (`seconds_paused`, `pauses[]`). The history view draws them as amber
hatching over the coverage bar: hatched rather than solid, because the audio
underneath really was captured.

**It is obvious that it is on.** In the browser: a split button that changes
identity when active (amber, pulsing dot, countdown, "resume"), plus a
page-wide banner above everything including the first-run flow. On the
counter-top display: the pause banner, checked *ahead of every fault state* in
`display_channel.health_state`, because it is the only one of those states the
person standing in the kitchen can act on.

### Why the display gets `D` rather than a new state letter

The obvious design is a fourth `StationState` — `P` for paused — with its own
wording. It is wrong *today*, and the reason is worth recording. The firmware
already on the glass parses the state letter as
`state[0] == 'D' ? kDegraded : kListening`, so a new letter would make a paused
station read as **listening** on every display that has not yet been updated —
the exact failure this feature exists to prevent, introduced by the fix for it.
`D` puts the banner up with the pause's own wording ("PAUSED BY OPERATOR -
RECORDING RESUMES 18:30") on the firmware that is out there now, with no OTA on
the critical path of something the operator needs this afternoon.

The cost is stated rather than discovered: the *empty-state* text on a paused
display still reads "Not listening / no microphone audio is reaching it", which
is wrong. A dedicated `kPaused` presentation is deferred firmware work; until it
lands, the banner carries the meaning. The HTTP polling fallback (`parseHealth`)
also does not see the pause, because its JSON filter does not include the field,
so a display on that path shows a normal listening screen with no detections
arriving.

### Why an endpoint rather than a setting

ADR-048 made every setting web-editable, so routing this through
`PUT /api/v1/settings` was available, and was rejected. A setting describes how
the station behaves indefinitely; this is an action with a deadline, taken
repeatedly, not persisted to `runtime.env`, and it must be one request from a
control on the main page. Wiring it through the settings writer would also mean
a privacy action waiting on a file write.

What *is* a setting, through ADR-048's mechanism: `pause_presets` (which
durations the drop-down offers) and `pause_default_preset` (what is
pre-selected before this browser has chosen). Both live-tier, in a new
**Privacy** category. `POST /api/v1/pause` accepts any *known* preset rather
than only the currently offered ones — the setting decides the menu, and
refusing a key that a browser tab loaded ten minutes ago would turn a settings
edit into a failed privacy action at the moment it is least welcome.

`until-midnight` is resolved **on the station**, in the configured IANA zone.
The browser's zone is whichever laptop happens to be open; the station's is the
one every other time in this system is already presented in. Pressed at 23:58 it
is two minutes, which is correct — "until midnight" is the end of the operator's
day, not 24 hours.

### Honesty

Health reports the pause as a **note**, never a `problems` entry. It is a
deliberate act, not a fault, and `problems` would flip
`binary_sensor.<station>_station_healthy` and make every alerting rule in the
house treat a birthday party as an outage. Saying nothing at all would be the
quiet omission the honesty constraint forbids, so: a note, plus a top-level
`pause` object on `/api/v1/health` and `/api/v1/station`, plus `oo_paused`,
`oo_pause_remaining_seconds` and `oo_pause_detections_suppressed_total` in
Prometheus. `oo_capture_state` deliberately stays `1` — an alert that cannot
tell a paused station from a dead one is charter item 2's failure in Prometheus
form.

Suppressed detections are counted (`pause.detections_suppressed`) and surfaced
in the station snapshot, on ADR-049's reasoning: a privacy control whose effect
nobody can see is a promise rather than a mechanism.

Persistence is best-effort and deliberately cannot block the pause: the
in-memory deadline is set before anything is written, and every database call in
`pause.py` swallows and logs. Losing the *record* of a pause is a documentation
loss; failing to *engage* a pause because a disk was full is a privacy failure.

### Cost

Two comparisons against a cached float: one per capture block (10 Hz) in
`_handle_block`, one per detection batch. No lock, no database, no allocation on
either path. Charter item 1 is untouched — nothing here can cost a frame of
audio, and `tests/test_api.py::TestOperatorPause` asserts that frames keep
arriving throughout a pause.

### Migration

Alembic head moves `0006_refinement` → `0007_capture_pause`. Purely additive:
one new table, no column added to and no data read from any existing one.

### Rollback and smoke test (ADR-055)

`git revert` removes the endpoint, the control and the gates; the station
returns to recording continuously. The `capture_pause` table can be left in
place (nothing else refers to it) or dropped with
`alembic downgrade 0006_refinement`, which discards the record of every pause
ever taken — after which those windows read as ordinary capture with nothing
detected in them. `pause_presets` / `pause_default_preset` in an operator's
`config/runtime.env` become unread keys, which `RuntimeEnvStore` preserves.

**If a station is ever stuck paused with the API unreachable**, the pause can be
cleared directly and the service restarted:

```bash
sqlite3 <data_dir>/openobservatory.sqlite \
  "UPDATE capture_pause SET ended_utc = CURRENT_TIMESTAMP, end_reason = 'resumed'
   WHERE ended_utc IS NULL;"
```

```bash
# 1. The menu and the state, in one request.
curl -s http://<station-host>:8080/api/v1/pause | python3 -m json.tool

# 2. Pause for fifteen minutes, and read back the deadline.
curl -s -X POST http://<station-host>:8080/api/v1/pause \
  -H 'content-type: application/json' -d '{"preset": "15m"}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["active"], d["ends_utc"], d["banner"])'

# 3. Live listening is refused (expect 503, and the pause banner as the detail).
curl -s -o /dev/null -w '%{http_code}\n' http://<station-host>:8080/api/v1/live/audio.wav
curl -s http://<station-host>:8080/api/v1/live/audio.wav | head -c 200; echo

# 4. Capture is still running -- frames must keep climbing.
for i in 1 2 3; do
  curl -s http://<station-host>:8080/api/v1/station \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["capture"]["frames"])'
  sleep 2
done

# 5. Paused, still capturing, and persisting nothing new.
curl -s http://<station-host>:8080/metrics \
  | grep -E '^oo_(paused|capture_state|detections_persisted_total) '

# 6. Health says so as a note, not a problem (expect no pause entry in problems).
curl -s http://<station-host>:8080/api/v1/health \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["problems"], d["notes"])'

# 7. The counter-top display. Watch the glass: it should read
#    PAUSED BY OPERATOR - RECORDING RESUMES HH:MM.

# 8. Resume early, in one request, and confirm listening comes back.
curl -s -X DELETE http://<station-host>:8080/api/v1/pause \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["active"])'
curl -s -o /dev/null -w '%{http_code}\n' http://<station-host>:8080/api/v1/live/audio.wav

# 9. Restart survival, on the real device.
curl -s -X POST http://<station-host>:8080/api/v1/pause \
  -H 'content-type: application/json' -d '{"preset": "1h"}' > /dev/null
ssh <user>@<station-host> 'sudo systemctl restart open-observatory'
sleep 15
curl -s http://<station-host>:8080/api/v1/pause \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["active"], d["ends_utc"])'
curl -s -X DELETE http://<station-host>:8080/api/v1/pause > /dev/null

# 10. The gap is recorded as a pause, not left as an unexplained hole.
curl -s 'http://<station-host>:8080/api/v1/history?window=today' \
  | python3 -c 'import json,sys; c=json.load(sys.stdin)["coverage"]; print(c["seconds_paused"], c["pauses"])'
```

## ADR-058: The spectrogram's detection labels are placed, not drawn where they fall

**Status:** active. Frontend only: one new pure module, one draw-loop change.

**The bug, as the operator saw it.** On his phone, the live spectrogram's overlay
read:

> **Eurasia Eurasian Jackdaw 95%**

Two Eurasian Jackdaw detections a couple of seconds apart, each label drawn at
its own box's left edge, the second painted over the first. The visible string is
not a species, not a score and not a claim the station holds — it is two claims
sheared together. That is the honesty constraint, not a cosmetic defect: *a
number or name shown to a human must mean what its label says.* It is also not an
edge case. At 390 px the plot is a few hundred pixels wide, a label is ~140 px of
it, and one woodpigeon calling twice is enough.

**Why the tests did not catch it.** jsdom has no canvas: `Spectrogram.test.tsx`
renders the overlay against a `getContext` proxy that records nothing and returns
`{ width: 10 }` for every measurement. Every assertion about this overlay that
could ever have been written in that environment would have passed. The fix is
therefore split so that the part that can be wrong is testable without a canvas.

**Decision.** Labels are laid out over the whole set before any of them is drawn,
by a pure function (`web/src/components/overlayLabels.ts`), under three rules in
priority order:

1. **Never two labels overlapping.** A placement that would intersect one already
   made is not made.
2. **Never a truncated species name.** No ellipsis and no clipping. A name cut
   short reads as a different, real bird — "Great Spotted Woodpecker" clipped to
   "Great" is a word the reader finishes themselves, wrongly. The *score* and the
   *count* may be shed to make a name fit, because each is a separately labelled
   fact whose absence claims nothing; the name may not be shortened.
3. **Dropping a label is honest; overlapping two is not.** When neither of the
   above can be satisfied the label is not drawn. The box stays, so the detection
   is still visible and still countable — it has simply not been named in a place
   where naming it would lie.

Before any of that, the common case is collapsed rather than fought: a run of the
same title close together in time becomes one label with a count —
`Eurasian Jackdaw ×3 · best 95%` — which is both what actually happened (one bird
calling repeatedly) and the form the operator already reads in the suggestions
list.

Four details are load-bearing:

- **The merge key is the rendered title, not a taxon id.** Two detections merge
  only if their words would have been identical anyway, so a withdrawn claim
  (ADR-044 renders it `… · withdrawn`) can never be counted into a standing one,
  and a 45 kHz bat pass never into a 55 kHz one. If the key matches, the merged
  label is literally correct for every member.
- **A run's score is labelled `best`.** `Eurasian Jackdaw ×3 95%` would be a
  number that does not mean what its label says: the run holds a 95%, a 91% and a
  60%. Naming which one it is costs six characters.
- **A run collapses when its labels would collide, and not before.** The
  threshold is the label's own extent along the time axis, not a fixed pixel gap.
  Two jackdaws two seconds apart are 30 px apart on the phone, where the count is
  the only honest option, and 120 px apart on a 1440 px desktop — where, if the
  name is short enough, both fit and both are shown. Same audio, two pictures,
  both true; a count on a desktop with room for the detail would be hiding it for
  nothing.
- **Labels move only along the frequency axis** — y in `scroll`, x in
  `waterfall` — never along time. Displacement across frequency costs nothing
  because a label was never a claim about frequency (its box is). Moving one
  along time would put a name at a moment nothing happened, which is the same
  class of error this ADR exists to prevent.

**Rejected: eliding text to fit.** It is the obvious answer and it is the wrong
one here. The failure mode being fixed *is* a partial species name; `Northern
Rough-winged Swal…` is a smaller version of the same lie, and on a narrow plot it
would be the normal rendering rather than the exception.

**Rejected: dropping every label below a width threshold and relying on tap.**
The station's normal state is nobody at a browser and, when there is somebody,
often a phone in a garden. A picture that names nothing at the width it is most
often read at fails charter item 8 without buying any honesty that rule 3 does
not already buy in the cases that actually need it.

**Charter item 8 pays nothing.** No polling, no `ResizeObserver`, no `matchMedia`,
no new React state, and nothing added to the transport: the layout runs inside the
overlay's existing `requestAnimationFrame` loop, from the detections it already
had through a ref. Its working set — the runs, the ordering, the placements and
the inputs — is pooled in module-level and ref-held arrays and reused frame to
frame rather than reallocated sixty times a second, and text widths are cached by
string, which *removes* a `measureText` per detection per frame that the old code
paid. The placement search is O(n²) over at most a dozen labels with a bounded
lane count. Ordering is an insertion sort over indices rather than
`Array.prototype.sort`, whose comparator would be a fresh closure each frame.

**Verified by rendering, because unit tests structurally cannot see this.**

- `web/src/components/overlayLabels.test.ts`: 36 assertions, each run for both
  orientations — the reported two-jackdaw case, a name wider than its box, a name
  wider than the whole plot, both plot edges, eight species crowded into a phone,
  the desktop case where everything fits, and the buffer-reuse contract. Removing
  the collision test from the implementation fails four of them, which was checked
  rather than assumed.
- Real Chromium, real canvas, real text metrics, at 390 px and 1440 px in both
  orientations, with the pre-change component rendered directly above the new one
  from the same fabricated detections. The 390 px "before" panel reproduces the
  operator's string exactly — `Eur Eurasian Jackdaw 91%` — and, in the crowded
  scene, `Northern Rough-winged Swallo` cut off by `European Robin 70%`. The
  "after" panel shows `Eurasian Jackdaw ×2 · best 95%`, both boxes intact, and no
  overlap anywhere in any scene at either width.
- Desktop is unchanged where there is room: at 1440 px the four well-separated
  labels in the dawn-chorus scene are at identical positions before and after.
  Only the ones that were overlapping moved.

**Not verified:** a real iOS or Android browser, and the live station (nothing was
deployed). The harness feeds fabricated detections, deliberately: the reported
collision is a two-second coincidence in a garden and waiting for one is not a
test.

**Known and not addressed here.** Above the 640 px breakpoint ADR-054 floats the
spectrogram's badge strip back over the plot's top-right corner, and a detection
label placed there is partly hidden behind it. That is a DOM element over the
canvas rather than two canvas labels colliding, it predates this change, and
reserving the corner would mean measuring the strip's box in the draw loop —
a forced layout every frame, which is exactly the cost item 8 may not impose.

### Rollback and smoke test (ADR-058)

Rollback is `git revert` of the frontend commit and `npm run build`. The change
touches no Python, no schema, no persisted state and no transport; the only
runtime effect is which pixels the overlay canvas paints.

```bash
cd web && npm ci && npm test -- --run && npx tsc --noEmit && npm run build
OO_WEB_DIST=$PWD/dist ../.venv/bin/oo serve --source synthetic

# Watch the overlay while two detections of one species land within a few
# seconds of each other. `--mute-audio` matters: this page can start playing
# the live stream.
chromium --headless --disable-gpu --no-sandbox --mute-audio --hide-scrollbars \
  --window-size=390,844 --virtual-time-budget=18000 \
  --screenshot=$HOME/shots/labels390.png http://127.0.0.1:8080/

# What must be true in the picture: no two labels overlapping, no species name
# cut short, nothing crossing the canvas edge, and a repeated species reading
# "<name> ×N · best NN%" rather than N labels in one place.
```

## ADR-057: A row that claims evidence must be checkable; reconcile the ones that lie, and keep "missing" distinct from "reclaimed"

**Decision:** Three things, in the order they matter.

1. `oo clips reconcile-missing` finds `media_asset` rows with `reclaimed_at IS
   NULL` whose `storage_uri` does not exist, and marks them `reclaimed_at` with
   `reclaim_reason = "missing"` — deliberately **not** one of `retention.py`'s
   tier names. Dry-run by default, `--json`, `--apply`, `--yes`, in the shape
   `oo history reconcile-streams` and `oo detections reconcile-plausibility`
   already established. It deletes nothing: not a file, not a `media_asset`
   row, not a `detection`. What the row used to claim is preserved verbatim
   under `detail.missing_reconciliation`.
2. `RetentionSweeper.audit_missing_files()` stats a **bounded rolling slice**
   of live rows on the housekeeping tick that already runs the sweep, cursored
   on `(created_at, id)`, wrapping at the end. The station therefore notices
   this class of fault by itself, and reports it as a health **note**, a
   `/api/v1/retention/status` block, and `oo_media_missing_files`.
3. The consumers' arithmetic is corrected:
   `eligible_for_deletion.bytes_verified_present` is the reclaimable figure
   with known-missing bytes removed, and the storage panel renders the
   correction next to the numbers being corrected.

No schema change. `reclaim_reason` is already `String(32)` and `detail` is
already JSON, so the Alembic head stays `0007_capture_pause` (ADR-042).

**The measurement.** Taken read-only against the live station on 2026-08-10,
`data/openobservatory.sqlite` opened `mode=ro` through a URI:

| | rows | missing from disk |
|---|---|---|
| all `media_asset` | 48,989 | — |
| live (`reclaimed_at IS NULL`) | 48,941 | **8,067 (16.5%)** |
| properly reclaimed | 48 (`privacy_human_audio`) | 48, correctly |

20,588,388,416 bytes — 20.59 GB of the 116.95 GB the database claimed to be
holding. By clip day: 2026-08-04, 3,217 of 3,217; 2026-08-05, 4,850 of 8,289;
2026-08-06 onward, 0 of 37,435. Every missing row was created before
`2026-08-05T18:44:35.234715Z` and every surviving row at or after it, with no
overlap in either direction — a clean boundary in creation order, not a
scatter. The operator's 2026-08-09 figure of 8,067 is confirmed unchanged;
what changed is the denominator, because capture kept running.

**What actually caused it, and what did not.** ADR-021 was the obvious suspect
and is the wrong one. The cause is `ClipManager.enforce_retention()`
(`clips.py`), the pre-ADR-026 sweep: it walks the clip tree, sorts by mtime and
unlinks oldest-first until the tree fits `max_total_bytes` — and it never
touches the database. Three independent lines of evidence agree:

- **Its own logs account for the whole loss.** `journalctl -u open-observatory
  | grep clips.retention`, between 2026-08-05 and 2026-08-08: 8,166 files
  deleted, `expired=0` and `over_budget=8,166` throughout, 20,838,470,172
  bytes. Against 8,067 rows and 20,588,388,416 bytes missing here. The ~99-file,
  ~250 MB difference is `.partial` files and clips with no row, which that
  sweep also deletes.
- **The boundary is an mtime prefix**, which is the signature of
  `sorted(entries)` unlinking oldest-first, and nothing else in the system
  produces it.
- **ADR-021 is where it stopped, not where it started.** The SSD raised the
  budget from 20 GB to 300 GB; the last `clips.retention` deletion is
  2026-08-08T13:07:32Z and `data/clips.sdcard-backup`'s copy finished at
  13:06:27Z. That is why the boundary *looks* like ADR-021 — the migration is
  the fix's timestamp. The 20 GB budget had been holding a rolling ~24-hour
  window all along, which is exactly the span that is missing.

**Nothing is recoverable, and this was checked properly.**
`data/clips.sdcard-backup` holds 9,202 files: 9,186 match rows whose files are
*present*, 14 match no row, and **0 match any missing row** — compared by
relative path and again by basename. Its oldest file mtime is
2026-08-05T18:54:02Z, nine minutes *after* the boundary, so the pre-boundary
clips were never in it. `sudo find / -xdev` across both filesystems for
`20260804T*.wav` and pre-boundary `20260805T*.wav` returns nothing, and there
are no archives. This matters more than an accounting error would, because a
clip is the one thing in this system that cannot be regenerated: charter item 5
ranks evidence above refinement *because* "a better classifier can be run over
stored clips later." For these events it cannot.

**What it already cost, beyond the wrong numbers.**

- **The refinement runner cannot work on bats.** `find_candidates`
  (`refinement/store.py`) selects oldest-unrefined-first and filters
  `reclaimed_at IS NULL`, so it draws its whole batch from exactly this
  population: 1,200 candidates considered, 0 examined, 1,200 `unavailable`,
  every night, with no `Refinement` row written to move it past them. 1,290 of
  6,049 bat detections have lost every native clip they had; 4,759 still have
  audio and the runner has never reached one of them. Reconciling the rows is
  what unblocks it.
- **61 held Spotted Crake detections cannot be listened to.** All 122 of their
  media rows are in the missing set, event times 2026-08-04T18:47Z to 20:55Z,
  inside the deleted band. A human hold (ADR-043) exempts a detection from
  three retention tiers; it could not exempt it from a sweep that never read
  the database. The command names held detections in its output, because an
  operator writing off a hold should be told that is what they are doing.
- The storage panel over-reported, `/api/v1/media/{id}` answered 410 for a
  sixth of its rows, and retention's disk budget counted 20.59 GB as
  reclaimable that reclaiming could not free.

**Why `"missing"` is not a tier name.** Setting `reclaim_reason` to `native` or
`expired` would record that a policy decided to give this clip up. Nothing
decided anything; the file went. The charter's "withdraw, not delete" rule is
about a record the system got wrong being evidence about the system, and the
same applies to the system's account of its own storage: an operator, and the
refiner, should be able to tell evidence aged out on purpose from evidence that
vanished. `"missing"` joins `"privacy_human_audio"` (ADR-049) as a reason that
is a fact about *why*, not a tier. Reconciliation is honest bookkeeping, not a
way of pretending the clips were never there.

**Why the recurring check is a rolling sample and not a census.** Statting
every live row is cheap in isolation — 48,989 `os.path.exists` calls measured
at **0.27 s** on the target — but 0.27 s is the same order as the ~0.30 s ORM
sweep ADR-033 had to pace to 300 s after it starved the event loop for
55–150 ms at a time and cost ~1.9 `capture.gap` records per minute. Capture
always wins, and this is the third time on this project that sustained I/O next
to the ALSA read has had to be refused. So: `batch_size` rows per 300 s tick
(default 200, ~1 ms of the same measurement), a full pass over ~50k live rows
in about 20 hours, on the evidence executor rather than the pool the capture
read shares (ADR-021's fix, still the load-bearing one).

The cursor is `(created_at, id)`, not `created_at`. Three or four assets are
written per detection within microseconds of each other, so a bare `>` would
silently skip every row sharing a timestamp with the last one read — precisely
the quiet omission this audit exists to catch, and it would have made the audit
lie about its own coverage.

**Why the honest answer needs two numbers.** A completed pass gives an exact
count; a partial pass gives a floor. Reporting the second as though it were the
first is the failure the charter names by example — a coverage figure that read
1302%, an "audio lost" figure over by 12.9x, both sincere and both believed. So
`known_missing` is the last *completed* pass's figure, `exact` and
`passes_completed` say which kind of claim it is, and the panel renders "8,067
of 48,941 rows" or "8,067 so far (the audit is still on its first pass)"
accordingly. `missing_files` is *absent*, not zero, from a station that does not
report it, and the panel then renders nothing rather than a confident zero.

**A note, not a `problems` entry.** Capture, detection and history are all
correct; what is wrong is the station's account of what it holds. Degrading
would flip `binary_sensor.<station>_station_healthy` in Home Assistant and train
the operator to ignore it — the same reasoning ADR-055 applied to an operator
pause. Saying nothing was not an option either: "8,067 clips" meaning "8,067
rows, 16.5% of which have no file" is exactly a number that does not mean what
its label says.

**What this ADR does not do.**

- It does not delete `ClipManager.enforce_retention()`. The station has not
  called it since ADR-026 — housekeeping drives `RetentionSweeper.sweep()` —
  and it is still tested. Removing it is a separate change with its own risk;
  what this ADR adds is the check that would have caught it, and would catch
  the next thing that unlinks a clip behind the database's back.
  `ClipManager.admits()`'s write-time `min_free_bytes` reserve is a different
  concern and is untouched.
- It does not delete `data/clips.sdcard-backup` (~21 GB). **The operator
  removed that directory by hand on 2026-08-10**, after this ADR established
  that no live row pointed into it and that it held none of the 8,067 missing
  clips; that freed 21 GB on the SD card, where the database lives and write
  endurance is a standing constraint. ADR-026 already made
  that an explicit operator-triggered cleanup, and it is now *also* the only
  independent copy of 9,186 live clips.
- It does not add a filesystem check to `/api/v1/retention/status`. That
  endpoint is polled by an always-on panel, and a census per poll is the exact
  pattern ADR-021 and ADR-033 both had to undo.
- It does not attempt recovery. There is nothing to recover from; see above.

**Rollback.** Revert the commit. `--apply` has no undo of its own, but nothing
it wrote is destructive: clearing `reclaimed_at`/`reclaim_reason` on rows
carrying `detail.missing_reconciliation` restores the previous (wrong) state
exactly, and that block records what to restore it to. No migration to reverse.

**Verification on the target, read-only first.**

```sh
# 1. The extent, from the database the station is running on. Read-only.
ssh <user>@<station-host> 'cd ~/open-observatory && python3 -c "
import sqlite3, os
db = sqlite3.connect(\"file:data/openobservatory.sqlite?mode=ro\", uri=True)
rows = db.execute(\"select storage_uri, byte_length from media_asset where reclaimed_at is null\").fetchall()
gone = [(u, b) for u, b in rows if not os.path.exists(u)]
print(len(gone), \"of\", len(rows), \"live rows missing;\", sum(b for _, b in gone), \"bytes\")
"'

# 2. What the station itself now says, before anything is applied.
curl -s http://<station-host>:8080/api/v1/retention/status \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["missing_files"])'
curl -s http://<station-host>:8080/api/v1/health \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["notes"])'
curl -s http://<station-host>:8080/metrics | grep -E '^oo_media_missing'

# 3. The dry run, kept. Read it before applying: it is the only record of what
#    these rows claimed that will exist outside the rows themselves.
ssh <user>@<station-host> 'cd ~/open-observatory && .venv/bin/oo clips reconcile-missing --json' \
  > missing-$(date -u +%Y%m%dT%H%M%SZ).json

# 4. Apply, on the station, with the confirmation prompt.
ssh -t <user>@<station-host> 'cd ~/open-observatory && .venv/bin/oo clips reconcile-missing --apply'

# 5. Confirm the accounting corrected itself, with no restart.
curl -s http://<station-host>:8080/api/v1/retention/status \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["eligible_for_deletion"], d["missing_files"])'

# 6. Confirm the refinement runner can now reach evidence that exists.
ssh <user>@<station-host> 'cd ~/open-observatory && .venv/bin/oo refine run --dry-run --json' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["candidates_considered"], d["examined"], d["outcomes"])'

# 7. Confirm nothing was destroyed.
ssh <user>@<station-host> 'cd ~/open-observatory && python3 -c "
import sqlite3
db = sqlite3.connect(\"file:data/openobservatory.sqlite?mode=ro\", uri=True)
print(db.execute(\"select count(*) from media_asset\").fetchone(),
      db.execute(\"select count(*) from detection\").fetchone(),
      db.execute(\"select reclaim_reason, count(*) from media_asset where reclaimed_at is not null group by 1\").fetchall())
"'
```

---

## ADR-059: The clip archive is measured off the event loop, because a status snapshot must not walk a filesystem

**Decision:** `ClipManager.disk_usage()` no longer walks the clip tree. It reports
the last measurement and how old it is (`clip_usage_age_s`); the walk itself moves
to `ClipManager.refresh_disk_usage()`, an `async` method that yields to the event
loop every `USAGE_SCAN_CHUNK` (512) files and is driven from the housekeeping loop
on the same ~30 s cadence the old cache TTL provided. The walk also changes from
`Path.rglob` + `Path.stat` to `os.scandir` + `DirEntry.stat`. The cache is seeded
by one synchronous walk in `__init__`, at startup, before the microphone is open.

Nothing else changes: same cadence, same figures, same `disk_used_ratio` (which
comes from one `statvfs` and was never the expensive part).

**Reason — measured on the live station, 2026-08-10, in a clean 2.02 h
restart-free window (11:37:53Z → 13:40:00Z).** Every one of the **262**
`capture.late_read` events in that window was this walk. They arrive on a
**30.4 s** beat — 223 of 261 inter-arrival gaps within 2.5 s of 30 s — with
stalls of **254–370 ms** against a 500 ms ALSA ring, i.e. `late_read_max_frames`
of **142,137 of 192,000 (74%)**.

The attribution needs no inference, because the station's own `snapshot_phase_s`
instrument (added by ADR-033) names the phase in the same millisecond:

```
13:38:07.428665Z housekeeping.tick  blocking_total_s=0.4532 snapshot_phases={'storage': 0.4512}
13:38:07.433749Z loop.lag           lag_s=0.4275
13:38:07.782400Z capture.late_read  stall_ms=361.6 stall_frames=138862
```

Reproduced independently on the station, walking the same tree by hand, best of
three: **0.435 s for 40,888 files — 10.6 µs/file.**

**Why it is growing, which is the part that matters.** The cost is linear in the
size of the archive, and the archive grows by roughly **14,000 files a day** and
has deleted nothing yet (oldest clip 2026-08-05, native tier 7 days). Counting
the files that existed at each of this document's previous headroom readings:

| Reading (UTC) | files in tree | walk at 10.6 µs/file | `late_read_max_frames` |
|---|---|---|---|
| 2026-08-08 15:00 | 9,764 | 104 ms | 57,952 (30%) |
| 2026-08-09 11:14 | 20,468 | 218 ms | 114,362 (60%) |
| 2026-08-09 21:46 | 27,174 | 289 ms | 155,243 (81%) |
| 2026-08-10 13:40 | 40,888 | 435 ms | 142,137 (74%) |

The three consecutive readings "in the wrong direction" that prompted this
investigation are that column. At 14,000 files a day the walk reaches the 500 ms
ring at about **47,000 files — roughly half a day away** — and about **0.9 s** by
the end of a 72-hour soak. At the steady state the retention tiers imply (7 days
of native plus 30 days of playback derivatives) it is ~250,000 files and ~2.7 s,
every 30 seconds, against a 500 ms ring. This stops being headroom.

**Why chunking rather than a thread.** ADR-033 settled this and it is the same
mistake offered again: the walk is CPU-bound Python, so a dedicated executor
would still hold the GIL, and the event loop still has to issue each
`run_in_executor` capture read and consume its result. *An executor partitions
queueing, not scheduling, and nothing partitions the GIL.* Yielding is what
actually returns control: 512 files is ~2.4 ms at the 4.6 µs/file `scandir`
measures, inside one 10 ms ALSA period, and it stays bounded however large the
archive grows — which a faster walk would not.

**Why `scandir` as well.** Measured on the station's 40,888-file archive:
`rglob` + `path.stat()` 435 ms; `scandir` + `entry.stat()` **189 ms**; `scandir`
without sizes 48 ms. `rglob` builds a `Path` per entry and re-resolves it to
stat; `scandir` stats against the open directory handle. This is a 2.3× saving
on work that no longer blocks anything, so it is a bonus rather than the fix —
recorded because the next person will want to know where the remaining cost is.

**Why the cadence is deliberately unchanged.** Refreshing a *file count* every
30 s is more often than anyone needs, and 300 s would have been defensible on
exactly ADR-033's argument. It is left at 30 s so that this change moves one
thing. If the next measurement is ambiguous it should not be because two
variables moved at once.

**What this does *not* fix.** All 2.70 s of audio genuinely lost in that window
came from five ALSA overruns on a **~302 s** beat matching `retention_interval_s`,
not from this walk — see the 2026-08-10 section of
`docs/delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md`. This change widens the margin
those events land in; it does not remove them. That is a separate finding and
deliberately a separate decision.

**Consequences.**

- `clip_count` and `clip_bytes` are up to ~30 s stale, as they already were, and
  now say so via `clip_usage_age_s`. `disk_used_ratio`, `disk_free_bytes` and
  `disk_total_bytes` remain instantaneous.
- Constructing a `ClipManager` costs one synchronous walk. At startup, on a
  250,000-file archive, that is ~1.2 s before the microphone opens — chosen over
  reporting `0 clips` until the first refresh lands, which would have been a
  number that does not mean what its label says.
- `enforce_retention()` still clears the cache; `disk_usage()` re-measures
  synchronously if it finds it cleared. That path is unreachable in the station
  (nothing calls `enforce_retention`) and is exercised only by tests.

**Status: deployed 2026-08-10, before the 72-hour soak, and its verification
FAILED.** The pass criterion above was `late_read_max_frames` "well under
100,000". The post-deploy reading, taken from the 72-hour soak window, was
**188,982 of 192,000 (98.4%)** — worse than the 81% this change was written
to fix, not better. This does not mean chunking the walk was wrong; the
"What this does *not* fix" note above already named a separate cost on the
same beat, the retention sweep's own query cost, as the suspected remaining
cause. ADR-061 (2026-08-14) has since removed that cost — see its entry for
the re-verification.

**Verification, to run after deploying:**

```bash
rsync -a --delete --exclude __pycache__ ./src/ <user>@<station-host>:open-observatory/src/
ssh <user>@<station-host> sudo systemctl restart open-observatory
sleep 1800

# Remember: journalctl --since takes LOCAL time (BST = UTC+1); log lines are UTC.
ssh <user>@<station-host> "sudo journalctl -u open-observatory --since '-25 min' -o cat" \
  | grep -c capture.late_read          # was ~50 in 25 min; expect single digits
ssh <user>@<station-host> "sudo journalctl -u open-observatory --since '-25 min' -o cat" \
  | grep housekeeping.tick | grep -c storage   # expect 0: no tick should show a storage phase

curl -s http://<station-host>:8080/api/v1/health | python3 -c '
import json,sys; c=json.load(sys.stdin)["capture"]
print("late_read_max", c["late_read_max_frames"], "of", c["alsa_buffer_frames"])
print("late_reads   ", c["late_reads"], " loop_lag_max", c["loop_lag_max_s"])'
# Pass: late_read_max_frames well under 100,000, and the 30 s beat gone from the log.
# loop_lag_max_s is a run maximum and one retention sweep will still set it high.
```

**Rollback.** Confined to `src/` and one optional TypeScript field; no schema
change, no new dependency, no new setting.

```bash
git revert <this commit>
rsync -a --delete --exclude __pycache__ ./src/ <user>@<station-host>:open-observatory/src/
ssh <user>@<station-host> sudo systemctl restart open-observatory
```

## ADR-056: History longer than a day is a different question, a different shape and a different table

**Status: design spike. The range grammar is implemented; everything else is a
proposal.** What was built and what was only proposed is separated explicitly in
"What this change actually did" at the end. Nothing here was deployed: the
station was read read-only and every measurement was taken against a copy.

**Decision (proposed).** Browsing history beyond about a day is served from a
small, rebuildable **roll-up** rather than from the `detection` table; the
**server** keeps sole authority over bucket size and aligns buckets to the
station's **local** calendar rather than to UTC epoch multiples; the **view
changes shape** as the window grows, from the live-shaped timeline to a
per-day period view to a species-by-week season view; and coverage over a long
window is presented as an explicit, complete **list of outages** rather than as
a bar whose informative parts are narrower than a pixel. The window itself
stays what it already is — **one opaque string** — so a range grammar was all
that was needed to make `/history`, `/detections` and `/detections/export`
answer "every bat pass in July".

---

### The question the operator asked

> "I also realise as a broad thing there is no custom time period browser or
> anything longer than 24 hours. I think this wants a design spike to consider."

He is right, and the gap is charter item 6 — *present an accurate history when
someone digs in*, "the long-run log of what lived in this garden". The station
has been recording since 4 August and holds **107,801 detections** as of
2026-08-10 13:39Z. The windows on offer were `last hour`, `dawn chorus`,
`last night`, `today`, `yesterday`, `24 hours`. Questions the system could not
answer at all: how did this week compare with last, when did the swifts arrive,
show me every bat pass in July, what changed after the microphone moved.

---

### How everything below was measured

A copy of the live database (150.6 MB, 107,801 detections, 77 audio streams,
1,788 capture gaps, 48,989 media assets, spanning 2026-08-04 18:44Z to
2026-08-10 13:39Z) was pulled with `scp` and verified with
`PRAGMA integrity_check` before use. The station itself was only ever read:
three `GET /api/v1/history` calls for calibration, no deploy, no restart, no
write.

Because six days cannot answer a question about three months, a **grown copy**
was built by replaying the station's own rows backwards in time — every row is
a real row from this station, with its real JSON payload, string lengths and
group mix; only the primary keys and the timestamps change. Sixty-one epochs
of six days gives **6,575,861 detections across 367 days, 6.40 GB**, at
17,918 detections/day. Row extents were snapshotted before the loop so a later
epoch cannot re-select an earlier epoch's copies (the first attempt did, and
inflated the density by 23% — the corrected build is the one used throughout).

**Calibration to the real hardware.** Three windows measured end-to-end on the
Pi 5 against its own database, with the row count each covers:

| window | rows in window | Pi, whole endpoint |
|---|---:|---:|
| `last-night` | 24,297 | 0.325 s |
| `last-24h` | 40,430 | 0.606 s |
| 6 days (`since`/`until`) | 107,801 | 1.611 s |

That is a straight line through the origin at **15–17 µs per detection row**
(15.4 µs/row between the first and third points, 17.4 between the first and
second, intercept within noise of zero). **16 µs/row is used for every Pi
projection below, and every such projection is arithmetic, not a measurement.**
The same work on the benchmark desktop is 1.6 µs/row, so this Pi is about
**10×** slower on this workload — a little worse than the 5–6× ADR-037
measured, which is consistent with this being CPU-bound on text parsing rather
than I/O-bound.

Detections per day, measured, by plugin:

| day | activity-v1 | birdnet | ultrasonic | total |
|---|---:|---:|---:|---:|
| 2026-08-05 | 12,058 | 1,590 | 2,616 | 16,264 |
| 2026-08-06 | 11,746 | 2,761 | 502 | 15,009 |
| 2026-08-07 | 1,628 | 426 | 35 | 2,089 (the ADR-024 outage) |
| 2026-08-08 | 10,018 | 1,370 | 1,489 | 12,877 |
| 2026-08-09 | 16,770 | 4,335 | 2,912 | 24,017 |
| 2026-08-10 (13.6 h) | 25,602 | 3,227 | 2,207 | 31,036 so far |

**This is materially higher than ADR-037's 12,900–16,300/day**, measured eight
days earlier. 9 August is 24,017 and 10 August had already passed 31,000 before
lunch. The projections below use **20,000/day**; anyone rerunning them should
re-measure rather than trust that number, and ADR-037's growth table is now at
the top of its own range (150.6 MB over 5.70 days = **26.4 MB/day**, against
the 20.8 MB/day it measured).

---

### Finding 1: the cost of a window is the rows it contains, and nothing else

Measured on the grown copy, through the real `history.py` functions, with a
deliberately small 2 MB page cache:

| window | rows | auto bucket | buckets out | `timeline()` | `species_summary()` | `coverage()` |
|---|---:|---:|---:|---:|---:|---:|
| 1 day | 40,354 | 900 s | 96 | 46 ms | 15 ms | 8 ms |
| 7 days | 148,155 | 7,200 s | 63 | 169 ms | 57 ms | 10 ms |
| 30 days | 539,005 | 43,200 s | 50 | 629 ms | 209 ms | 15 ms |
| 90 days | 1,617,015 | 86,400 s | 91 | **1,898 ms** | 647 ms | 30 ms |
| 365 days | 6,557,466 | 86,400 s | 366 | **7,744 ms** | 2,758 ms | 106 ms |

Forcing a 30-day window to 15-minute buckets — 2,880 buckets instead of 50 —
changed the timeline's cost from 629 ms to **631 ms**. On the real database, a
6-day window at automatic buckets took the Pi 1.611 s and the same window at
900-second buckets took **1.555 s**.

**Bucket count is free. It is not the problem, and it was never going to be.**
The query is a range scan over every detection in the window followed by an
aggregate; the number of groups it collapses into changes nothing. The
operator's instinct that 2,880 buckets over a month is absurd is right about the
*chart* and wrong about the *database*. It is also worth recording that the
server **already** chooses the bucket: `History.tsx` never sends
`bucket_seconds`, and `choose_bucket_seconds` picks 600 s for a twelve-hour
night and 900 s for a 24-hour window. The client asking for a resolution is a
capability the API has (`bucket_seconds`, capped at 86,400) and the UI has
never used, and that is the right division — the server is the only party that
knows what the span costs.

Projected to the Pi at 16 µs/row and 20,000 detections/day:

| window | rows | Pi, projected |
|---|---:|---:|
| 7 days | 140,000 | **2.2 s** |
| 30 days | 600,000 | **9.6 s** |
| 90 days | 1,800,000 | **29 s** |
| 365 days | 7,300,000 | **117 s** |

Seven days is a spinner. Thirty is a page that looks broken. Ninety is a
request an operator will assume has failed, cancel, and retry — three times, on
a Pi that is also capturing audio at 384 kHz.

**ADR-037's own trip-wire has already fired.** It said the recommendation would
change "if a query that scans the *whole* detection history is added to the UI
or API". `GET /api/v1/history?since=…` is unbounded today: it takes an
arbitrary `since` with no cap, no `until` requirement and no authentication by
default. Nobody added a full-history endpoint deliberately; one has existed for
some time as a consequence of a convenience parameter.

---

### Finding 2: no index fixes this, and the reason is worth knowing

All measured on the grown copy, 90-day window, isolated SQL:

| variant | 90-day timeline | index cost |
|---|---:|---:|
| `count(*)` only — the pure index range scan | **46 ms** | — |
| as shipped (bucketed group-by + `audio_stream` join) | 1,463 ms | — |
| as shipped, without the join | 1,285 ms | — |
| `+ (event_start_utc, taxonomic_group, stream_id, score)` covering index | 1,228 ms | **576 MB/year** |
| integer-epoch column + covering index (ADR-037 option E) | 535 ms | 228 MB/year |
| group-by with **no** bucket expression at all | 392 ms | — |
| `substr()` on the ISO text instead of date parsing | 616 ms | — |

Reading 1.6 million index entries costs **46 ms**. Everything else — a factor
of thirty — is CPU spent above the storage layer: `strftime('%s', …)` parsing
an ISO-8601 *text* timestamp, twice per row (the modulo form in
`bucket_expression` evaluates it twice; SQLite does not eliminate the common
subexpression), and a temp b-tree for the group-by.

Three consequences.

- **A covering index buys 16% and costs 576 MB a year.** Rejected outright.
  ADR-037 dropped four indexes for 9.76 MB; adding one 59 times larger to buy
  nothing would be an unusually expensive way to ignore that ADR.
- **ADR-037's option E — integer epoch timestamps — is worth about 2× on this
  workload specifically.** That is new evidence for a decision that ADR-037
  banked for the PostgreSQL move, and it should be recorded there: this is the
  one query shape that cares. It is still not enough on its own (535 ms desktop
  is ~4 s on the Pi at 90 days) and it is still a breaking rewrite of every row.
  **Bank it; do not bring it forward for this.**
- The floor for *any* per-row approach is the 392 ms of scanning and grouping,
  which is ~4 s on the Pi at 90 days. **The row-per-detection shape cannot
  answer a seasonal question interactively, however it is indexed.**

---

### Finding 3: a roll-up is a write amplifier only if you write it on the hot path

Two candidate tables, both **derivable from `detection` by definition** — a
cache, not a record, and rebuildable from the detections at any time:

- `detection_bucket(bucket_start_utc, taxonomic_group) → detections, best_score`
  at a **900-second grain**;
- `species_day(day_start_utc, group, common_name, scientific_name, label) →
  detections, best_score, first_utc, last_utc`.

Built from the grown year and measured:

| | rows | size | per row |
|---|---:|---:|---:|
| `detection_bucket` | 50,569 | 1.57 MB | 31 B |
| `species_day` | 18,317 | 2.78 MB | 152 B |

**4.35 MB for a year**, against 6.40 GB of detail: **0.07%**. Serving the same
questions from it:

| window | timeline, detail table | timeline, roll-up | species, detail | species, roll-up |
|---|---:|---:|---:|---:|
| 7 days | 169 ms | **0.3 ms** | 57 ms | **0.3 ms** |
| 30 days | 629 ms | **0.9 ms** | 209 ms | **0.9 ms** |
| 90 days | 1,898 ms | **2.9 ms** | 647 ms | **2.6 ms** |
| 365 days | 7,744 ms | **12.0 ms** | 2,758 ms | **11.8 ms** |

A whole year's phenology — first and last day every species was heard, with its
total — is **7.4 ms** for 101 species. On the Pi, at the measured 10× factor,
those are 29 ms and 120 ms. Interactive, on the device, over a season.

**The write cost, which is the part the charter cares about.** The database
lives on an SD card and write amplification is a real cost, so the roll-up was
costed the way ADR-037 costed inserts: WAL bytes per detection insert, one
commit per detection, `wal_autocheckpoint=0` so every dirtied page is counted.

| maintenance strategy | µs/row | WAL B/row | extra writes/day at 20,000/day |
|---|---:|---:|---:|
| as shipped, no roll-up | 35.4 | 38,147 | — |
| roll-up table present, rebuilt **daily in batch** | 27.8 | 38,112 | **~20 kB** |
| roll-up maintained by an `AFTER INSERT` trigger | 28.6 | 42,212 | **~81 MB** |

An incremental trigger adds **4,065 WAL bytes to every single detection**, a
10.7% increase on the station's whole database write volume, forever, to
maintain a 4 MB table. Rebuilding yesterday in one batch costs **20 kB and
0.09 s of CPU, once a day** — 7.2 MB a year, **0.08%** of the ~9.6 GB/year the
detections themselves write.

That is the whole answer to "a roll-up table is a write amplifier, cost it".
**It is, if it is written per event; it is not, if it is written per day.** The
station already has a daily batch process with a CPU fence — the refinement
runner (ADR-045) — and this belongs beside it, not in
`Station._insert_detection`.

Two rules keep it honest rather than making it a second, divergent record:

- **The detection table always wins.** The roll-up is served only when the
  requested bucket is a whole multiple of the 900-second grain *and* the window
  is longer than the crossover; anything finer or shorter goes to the detail
  table, which is fast at those sizes anyway. A test should assert the two
  paths return identical numbers for a window both can serve.
- **Rebuildable, and marked with what built it.** A roll-up row records the day
  it covers and the version of the code that summarised it, so a changed
  definition (a new detector, a corrected filter, a withdrawn detection under
  ADR-044) is a rebuild of affected days rather than a permanent lie. This also
  makes refinement safe: refinement changes rows, so the days it touched are
  re-rolled.

Note what this roll-up is **not**. ADR-037's option D proposed *replacing*
`acoustic_event` rows with a per-minute summary — destroying per-event detail
permanently, which that ADR correctly left to the operator's judgement. This is
additive: nothing is deleted, every detection remains addressable, and the
roll-up can be dropped entirely with no loss. It does not ask the operator for
anything.

---

### Finding 4: UTC-aligned day buckets file 15% of this station's bats on the wrong day

`bucket_expression` truncates to multiples of the bucket size in **epoch
seconds**. For any bucket up to an hour that is invisible. At 86,400 seconds —
which `choose_bucket_seconds` selects for any window over about twelve days —
buckets begin at **UTC midnight**, and the station presents local time.

In British Summer Time that puts the local hour 00:00–01:00 into the *previous*
day's column. Measured on the live database:

| group | detections in 23:00–24:00 UTC (00:00–01:00 local) | share of that group |
|---|---:|---:|
| bat | 1,592 | **15.15%** |
| acoustic_event | 143 | 0.17% |
| bird | 1 | 0.01% |
| all | 1,736 | 1.61% |

The first hour after local midnight is prime bat time, so the group that most
needs a per-night reading is the one most damaged by a per-UTC-day one. A
column labelled "5 August" that contains a sixth of the night of the 6th
violates the charter's flat requirement that *a number shown to a human must
mean what its label says*, and it does it silently, in the exact view a
seasonal question would use.

It cannot be fixed by adding to the friendly ladder either. A week is 604,800
seconds and epoch zero was a Thursday, so epoch-multiple "weeks" start on
Thursdays. Months and years are not fixed-length at all.

**So: buckets of a day or more must be *calendar* buckets, computed in the
station's timezone.** In SQL that is `date(event_start_utc, '<offset> hours')`
or a `local_day` column on the roll-up; the roll-up makes this nearly free,
because a 900-second grain re-aggregates exactly into local days for every
timezone whose offset is a whole multiple of 15 minutes — which is all of them.
Storing UTC 900-second buckets and deriving the local calendar at query time
also means a change to the station's `timezone` setting re-labels history
correctly instead of corrupting it.

`choose_bucket_seconds` should also grow a stated ceiling. Its `friendly` ladder
ends at 86,400 and silently returns that for anything larger, so a ten-year
window quietly becomes 3,650 buckets. Above a day the ladder should be
calendar-named — day, week, month — not a number of seconds.

---

### Finding 5: the coverage bar already fails, and the window length is not why

Charter item 2 — distinguishing a quiet night from a dead microphone — does not
weaken as the window grows. The current presentation cannot carry it, and
measurement shows it stopped carrying it some time ago.

Running the station's own `coverage()` over the whole record (5.70 days,
2026-08-04 18:44Z to 2026-08-10 11:37Z): **fraction captured 0.6439**, 76
stream spans, 1,778 capture-gap rows, and the complement of the merged live
intervals is **71 distinct outages**:

| outage length | count | total |
|---|---:|---:|
| ≥ 1 s | 71 | 2,949 min |
| ≥ 10 s | 30 | 2,946 min |
| ≥ 60 s | 10 | 2,938 min |
| ≥ 300 s | 6 | 2,932 min |
| ≥ 1 h | 5 | 2,905 min |

The five longest are 1,777 min (the ADR-024 hang, 7–8 August), 432 min, 343
min, 180 min and 173 min. Now render those into a phone-width bar of ~360 px:

| window | one pixel is | outages narrower than a pixel |
|---|---:|---:|
| 6 days | 24 min | **65 of 71** |
| 42 days | 168 min | **66 of 71** |
| 365 days | 1,460 min | **70 of 71** |

**At the window the UI already offers, 65 of 71 real outages are invisible.**
Six weeks is not where this breaks; six days is. The operator's instinct that a
two-hour outage becomes invisible at fortnight scale is right, and understated —
a two-hour outage is *already* only three-quarters of a pixel at six days.

There is a payload problem stacked on top of it. `coverage()` returns one span
object per `audio_stream` row in the window. Measured on the grown year:

| window | spans returned | coverage JSON |
|---|---:|---:|
| 1 day | 72 | 24 kB |
| 30 days | 441 | 142 kB |
| 90 days | 1,201 | **386 kB** |
| 365 days | 4,672 | **1,501 kB** |

A one-and-a-half-megabyte JSON array of stream spans, sent over garden WiFi, so
that a browser can draw 4,672 rectangles into 360 pixels. That is squarely
against the charter's network-efficiency constraint and it buys nothing legible.

**Proposal: at long spans, render the complement.** Coverage is normally ~99%;
the *gaps* are the information, and there are few of them. Three changes:

1. **Bucket the coverage server-side into the same buckets as the timeline** —
   fraction captured per bucket — and draw it as a strip beneath the bars, so
   a day that was only 60% covered reads immediately and its bar is understood
   as an undercount rather than a quiet day.
2. **Return the outage list explicitly, complete above a threshold**, longest
   first, with start, end, duration and cause where one is known — plus a count
   and total of those below the threshold, so nothing is hidden silently. Then
   a two-hour outage in a six-week window is *guaranteed* to be stated in words
   whatever its pixel width. This is the mechanism that makes charter item 2
   survive scale: it stops depending on geometry.
3. **Stop returning per-stream spans for long windows.** They are a diagnostic,
   not a picture; keep them under the existing operator/diagnostic depth toggle
   (ADR-028) or behind an explicit parameter.

The suspect-stream machinery from ADR-024 must survive this unchanged: a
`suspect_stream_count` in a season view is exactly the kind of thing that must
not be averaged away, and it is one integer. The same is true of
`seconds_paused` and the pause list (ADR-055) — a paused fortnight must not
read as a broken one.

---

### Finding 6: the live-shaped view is right for a day and wrong for a season

`History.tsx` borrows the live view's frame: one stacked bar chart against a
time axis, one coverage bar, one species table. That is a good shape for "the
shape of last night" and a bad shape for "when did the swifts arrive", because
a year of stacked bars answers a question nobody asked and hides the one they
did.

**Proposal: three shapes, chosen by span, not three pages.**

| span | shape | what it answers |
|---|---|---|
| ≤ 48 h | **today's view, unchanged** | the shape of a night; when activity peaked |
| 2 days – ~6 weeks | **period view**: one column per local day, stacked by group; coverage as a per-day fraction strip; outage list; species table gains a sparkline per species | how this week compared with last; what changed after the microphone moved |
| > ~6 weeks | **season view**: species × week matrix (a phenology grid), first-heard / last-heard table, no per-detection timeline at all | when the swifts arrived; which weeks the bats were out |

Two notes on this.

`species_summary` **already returns `first_seen_utc` and `last_seen_utc`**. Over
a wide range those two columns *are* the arrival and departure dates. The
phenology question needs no new aggregate — only a range wide enough to ask it
over and a table that puts those columns first instead of last. That is a
genuinely cheap win once the roll-up makes the range affordable.

The season view must keep the honesty markers the day view has. A phenology grid
is exactly the artefact that gets screenshotted and believed, so
`excluded_withdrawn_count`, `excluded_synthetic_count` and the "counts of
detections, not of animals" note have to travel with it, and a first-heard date
resting on a single detection needs to look different from one resting on
forty. A cell should carry its detection count, not just a colour.

---

### The range control: what is actually good on a phone in a garden

The obvious answer is a pair of date pickers, and the obvious answer is wrong
here as the primary control. On a phone it is six-plus taps to express "last
week", two native pickers with different behaviour on iOS and Android, and it
invites spans the station cannot answer. It is a good *escape hatch* and a bad
default.

**Proposal: granularity plus a stepper.**

- A row of granularity chips: `day` · `week` · `month` · `season` · `year`,
  alongside the existing sub-day named windows.
- A `‹ ›` pair that steps the window back and forward by its own length, and a
  "now" affordance to return.
- A `custom…` link that reveals two native date inputs, for the rare precise
  case.

Three properties earn this over a picker:

1. **"How did this week compare with last?" is one tap** — the `‹` button. No
   comparison mode, no dual-range query shape, no second aggregate. The
   comparison the operator asked for falls out of navigation.
2. **It is thumb-sized and stateless.** The whole control is (granularity,
   anchor), which is one string on the wire — and ADR-054's `segmented-wrap`
   already handles a chip row that will not fit a phone width.
3. **It maps onto calendar ranges exactly**, which is what Finding 4 says the
   buckets have to be anyway.

Relative ranges (`last 7 days`) and calendar ranges (`July`) are different
questions and both are wanted: relative for "what has been happening lately",
calendar for "what did July look like", which is the one that compares across
years. Both are in the grammar. Open-ended ranges are already expressible as
`since=` with no `until`.

**"Everything" is deliberately not offered.** A full-history aggregate with no
time bound is the exact query ADR-037 identified as the one thing that degrades
with database size, and offering a button for it before the roll-up exists
would be building the trap that ADR's trip-wire describes. Once the roll-up is
there, "everything" costs 12 ms and becomes the easiest thing on the page.

---

### Alternatives rejected

| # | Option | Why not |
|---|---|---|
| A | **Do nothing; cap windows at 24 h** | This is charter item 6, the thing the system is *for*. The record already spans longer than the UI can ask about, and the gap widens daily. |
| B | **Date/datetime pickers as the primary control** | Six taps for "last week", divergent native behaviour, and it hands the user a way to request a 3-year aggregate that takes the Pi two minutes. Kept as the `custom…` escape hatch. |
| C | **Index the detection table harder** | Measured: a covering index buys **16%** (1,463 → 1,228 ms at 90 days) for **576 MB/year**. ADR-037 dropped four indexes to save 9.76 MB; this would undo that 59 times over to buy nothing. |
| D | **Integer-epoch timestamps now (ADR-037 option E)** | Measured at ~2× on this workload — real, and worth recording against that ADR — but still ~4 s on the Pi at 90 days, and it is a breaking rewrite of every row. Stays banked for the PostgreSQL move, as ADR-037 decided. |
| E | **Maintain the roll-up with an `AFTER INSERT` trigger** | Measured: **+4,065 WAL bytes per detection**, +10.7% on the station's whole write volume, forever, on an SD card, to maintain a 4 MB table. The daily batch costs 20 kB/day for the same result. |
| F | **ADR-037 option D — replace `acoustic_event` rows with a per-minute summary** | Destroys per-event detail permanently and needs the operator's judgement, which this proposal does not have and does not need. The roll-up here is additive and droppable. |
| G | **Cache the `/history` JSON responses** | Does not compose across ranges (a cached July does not help "June to August"), invalidation has to understand refinement and withdrawal, and the *first* request still costs 29 s. A roll-up is a cache with a schema, which is the version that composes. |
| H | **Sample or approximate the counts for wide windows** | Violates the honesty constraint outright: a number shown to a human must mean what its label says, and "about 4,000 detections" in a chart that elsewhere means exactly what it says is the kind of sincere error the charter's precedent table is made of. |
| I | **PostgreSQL + TimescaleDB continuous aggregates** | The right *idea* — this is a continuous aggregate — but it needs a database server the station does not have, on a machine whose first rule is that capture wins. The roll-up proposed here is the same concept in 4 MB and one daily batch, and it works on the SQLite the station actually runs. Revisit at the PostgreSQL move, where it may replace the batch job. |
| J | **Aggregate in the browser** | 1.6 million rows over garden WiFi. The module docstring in `history.py` already settled this. |
| K | **A second "reports" page, separate from HISTORY** | Two places to look for the same fact, and the shape should follow the question, not the URL. One view that changes shape with span keeps a single answer to "what happened". |

---

### What this change actually did

**Implemented, tested, not deployed:**

- `history.resolve_range(name, timezone, *, now)` — a strict range grammar
  returning `None` for anything it does not recognise. It understands the six
  original windows unchanged, plus rolling relative ranges (`last-7d`,
  `last-36h`, bounded by `MAX_RELATIVE_DAYS`/`MAX_RELATIVE_HOURS`), calendar
  periods (`this-week`, `last-week`, `this-month`, `last-month`, `this-year`,
  `last-year`, ISO weeks starting Monday) and calendar literals (`2026`,
  `2026-07`, `2026-W32`, `2026-08-05`, bounded to years 2000–2999).
- Everything resolves in the station's timezone and is stored in UTC. A
  calendar month starts at **local** midnight — 1 July 2026 begins at
  2026-06-30T23:00Z in British Summer Time — and a month spanning a clock
  change is 743 hours, not 744. Both are asserted by tests.
- **No window ever ends in the future.** An unfinished period is truncated at
  `now`, so "this month" on the tenth is ten days long. This is not cosmetic:
  `coverage()` divides captured seconds by the window's length, so an
  untruncated in-progress month would report ~30% captured and look exactly
  like the dead microphone charter item 2 exists to distinguish it from. A
  test walks all 24 hours of a day across all the new names asserting it,
  mirroring the existing test that caught `last-night` resolving into the
  future.
- `resolve_named_range` is now a thin lenient wrapper over `resolve_range`,
  keeping its documented fall-back to `last-hour` for unknown names and the
  test that asserts it. Behaviour for every existing caller is unchanged.
- Because `window` is an opaque string threaded through `/api/v1/history`,
  `/api/v1/detections` and `/api/v1/detections/export`, **no endpoint changed**
  and "every bat pass in July" is now a URL:
  `GET /api/v1/detections/export?window=2026-07&group=bat&format=csv`.
- `last-7d` ("7 days") added to the dashboard's window chips and to
  `/api/v1/history/windows`. **Deliberately the only new chip.** Seven days is
  ~140,000 rows and a projected 2.2 s on the Pi, which is a spinner; thirty
  days is 9.6 s and ninety is 29 s, which are not. The longer ranges resolve
  correctly server-side today and are reachable by URL and by export; they are
  not put on screen until there is something behind them that can answer
  quickly. The reason is written into the component so it is not re-litigated
  by guesswork.

**Proposed and not built:** the roll-up tables and their daily batch job, the
calendar-aligned buckets, the coverage/outage response change, the period and
season views, and the granularity-plus-stepper control.

**Not verified:**

- Nothing here ran on the Pi 5 beyond the three calibration `GET`s. Every Pi
  figure above 1.6 s is arithmetic from the measured 16 µs/row, and is labelled
  as such.
- Nothing ran against PostgreSQL. `date(…, '<offset> hours')` in Finding 4 is
  SQLite syntax and would need the same dialect branch `bucket_expression`
  already has.
- The grown copy replays six real days sixty-one times, so it has the right row
  shapes and the right density but only 101 distinct species and a repeating
  seasonal pattern. It is a fair model of *cost* and a poor model of *biology*;
  `species_day`'s 18,317 rows/year would be somewhat larger with a real year's
  species list, and the size conclusion (4.35 MB against 6.40 GB) is not
  sensitive to that.
- The daily-batch write cost was measured on the desktop, not on the SD card.
  The *ratio* against a per-insert trigger transfers; the absolute figure does
  not.
- The 0.6439 coverage figure in Finding 5 is over the whole record including
  the ADR-024 hang and is not a claim about the station's current health.

### Trigger — do the roll-up when any of these is true

| Metric | Threshold |
|---|---|
| A window longer than 7 days is put on screen | immediately |
| p95 of `GET /api/v1/history` on the Pi | > 3 s |
| Sustained detections/day, 7-day mean | > 30,000 (already close: 9 August was 24,017) |
| Anyone asks for a species-by-week or phenology view | immediately — it has no cheap form without one |


## ADR-060: A read that never returns is a dead stream, not a slow one

**Status:** active. Bounds two loops, adds one backstop, and stops `capture.state` capping the health severity.

**The incident.** On 2026-08-14 the station was deaf from 02:12 UTC and nobody
knew until the operator looked at the indoor display at 05:47 and read `no audio
block for 12815.893s`. Full account in `HANDOVER.md` §1e; the decision-relevant
part is why nothing recovered and nothing shouted.

`alsa_source.py` treated any ALSA error containing `Input/output error` as a
transient xrun: count, log, `continue`. That `continue` sits **inside the
`while collected < self._block_frames` loop that assembles a block**. A permanent
error therefore never finishes a block, so `_read_blocking` never returns and
never raises; `_capture_loop` never returns; and `_capture_supervisor` — whose
docstring is *"Reopen the device with bounded backoff after any loss"* — is never
reached. It logged 23,135 errors at one every 0.576 s and `stream_restarts` never
left 0.

**The recovery architecture was already correct.** A plain `systemctl restart`
brought capture straight back, which proves the PCM was reopenable the whole time
and the supervisor would have fixed this within seconds of being given control.
The defect was not a missing mechanism; it was one `continue` below the mechanism,
denying it the chance to run. This is the same shape as ADR-020's incident (§3a),
where the error string did *not* match the swallowing branch, so it propagated and
the station degraded correctly. Whether the station recovered came down to which
words the driver happened to put in an error message.

**Decision, in four parts.**

1. **Bound the swallow, in both branches.** `AlsaSource` takes
   `stall_timeout_s` (default 5 s). Any run of read attempts that makes no
   progress — errors, or `length == 0` — raises `AlsaCaptureError` once it has
   made none for that long. A read that yields audio resets it, so an ordinary
   xrun still costs nothing. The `length == 0` branch carried a comment claiming
   it already did this ("treat a run of them as a stalled device rather than
   spinning") and did not; a fix to the `-EIO` branch alone would have left the
   identical trap one screen further down.
2. **A backstop that does not need to know why.** `_capture_loop` wraps a live
   read in `asyncio.wait_for(…, capture_read_timeout_s)` (default 15 s, longer
   than the source's own bound so the source names the fault first). This covers
   the branches we have not thought of yet. **ALSA sources only** — a `step`-mode
   replay source blocks deliberately, and timing that out would break the
   fixture-driven tests it exists for.
3. **Silence is critical, whatever `state` says.** The severity expression read
   `"degraded" if capture["state"] == "capturing" else "critical"`, and `state`
   stayed `capturing` for three and a half hours, because it describes what the
   capture task was *asked* to do rather than whether audio is arriving. A
   station that has heard nothing for longer than `capture_silence_critical_s`
   (default 30 s, comfortably past a reopen plus backoff) is now `critical`, so
   `/api/v1/health` answers 503 and Home Assistant's healthy sensor flips.
4. **Left deliberately unchanged: `capture.state`.** It stays a description of
   the task's intent. Making it mean "audio is arriving" would overload one field
   with two facts; `block_age_s` already carries the second one honestly, and the
   fix is to stop *deriving* severity from the wrong one.

**Consequences.** A wedged device now costs seconds instead of hours, and costs a
`stream_restarts` increment, a `critical` health event and a 503 — all things
that were available during the incident and none of which fired. The cost is that
a genuine 5 s device stall now tears the stream down and rebuilds it, losing the
in-flight block. That is the right trade: the audio was already lost, and the
alternative is what happened on the 14th.

**What this does not fix.** The wedge itself is unexplained. The AudioMoth never
left the USB bus — no `dmesg` event since 8 August, autosuspend off — and
`/proc/asound/…/status` read `state: RUNNING` with `hw_ptr` frozen at 768. This
ADR makes the station survive it, not prevent it. Related and also open: gap rows
arrive in pairs exactly `retention_interval_s` apart, so the retention sweep is
costing about 7 s of audio an hour (§1e).

## ADR-061: An operator-set keep flag replaces the computed exemplar rule

**Status:** active. Removes `RetentionSweeper._exemplar_detection_ids()`;
supersedes ADR-026's first/best-of-species exemption; deployment and
on-station verification pending (Task 6 of the implementing plan is
documentation only, by the controller's instruction).

**The measurement.** `_exemplar_detection_ids()` ran first inside `sweep()`,
before any deadline check: an unbounded `DISTINCT` join across every live
`media_asset` — ~46,000 rows, seven columns including the `native_result` JSON
blob — materialised into Python so first-of-species/best-of-species ranking
could be done in a loop. Measured on the station: **2.978 s**, against a
`retention_batch_budget_s` of **1.5 s**.

**Three failures from one query.**

1. **Retention never deleted a single file, nine days past its threshold.**
   The budget was spent in the preamble, before the first tier's guard ran, so
   `_strip_native` was never entered. `complete=False` and a flat zero
   deletion count read identically whether a sweep merely ran out of time with
   real work left, or never started at all — which is why nine days of this
   produced no symptom worth noticing.
2. **~7 s of audio lost per hour**, which cost the 72-hour soak (2026-08-10 to
   2026-08-13) its continuity gate: **99.865%** against a **≥99.9%**
   criterion (`MILESTONE_STATUS.md`, Milestone 4.5). ~3 s of GIL-holding ORM
   work every 300 s starved the capture event loop long enough to overrun the
   0.5 s ALSA ring. `capture_gap` rows arrive in pairs ~300 s apart, matching
   `retention_interval_s` to the second — the same beat `HANDOVER.md` §1e
   names.
3. **The 2026-08-14 wedge.** Each overrun forces an `snd_pcm_prepare()`
   device restart — roughly 12 an hour — and one of those did not come back,
   leaving the microphone deaf for **3 h 35 min** (ADR-060, `HANDOVER.md`
   §1e). The coupling to this query is proven to the millisecond, not merely
   correlated: the sweep began at **02:18:19.893** with **`duration_s`
   `2.6608`**, and the first `-EIO` landed at **02:18:22.552** — 2.66 s later,
   the length of the sweep itself. ADR-060 made the wedge survivable (bounded
   reads, a watchdog, a severity that no longer trusts `capture.state`); this
   ADR removes the query that was forcing the restarts in the first place.
   The self-reinforcing shape is worth naming: the query's cost scales with
   the number of live media assets, which grows precisely because the query
   was the thing preventing deletion.

**Decision: a human sets `kept`; nothing computes it.** `detection.kept_at`
(indexed) / `detection.kept_by` replace the first-of-species/best-of-species
computation. `kept` means **keep forever, until a human removes the flag** —
set and cleared only through `PUT`/`DELETE /api/v1/detections/{id}/keep`,
`oo detections keep <id> [--unkeep]`, or the drawer's keep toggle. Every
tier's candidate query now excludes `kept_at IS NOT NULL`: `_strip_native`
(7 d), `_strip_unkept` (renamed from `_strip_non_exemplar`, 30 d),
`_strip_expired` (90 d), and — the one place a computed exemplar rule never
reached — `_watermark_reclaim`. A kept recording survives the disk running
out, not just the calendar.

**Why a human flag beats a computed one.** The computed rule answered "is
this recording special", cheaply for one row but expensively for the whole
table, and an operator had no way to add to or override its judgement. A flag
answers "did someone decide to keep this", is one indexed boolean check per
row, and puts the judgement where it belongs — with the person who heard the
recording, not a query. The cost of a wrong computed answer was silent (a
recording quietly not exempted, or exempted for the wrong reason); the cost
of a wrong flag is visible and correctable by the same operator who set it.

**Why first-of-species is backfilled and best is not.** The `0008_detection_kept`
migration (Alembic revision `0008_detection_kept`, parent
`0007_capture_pause`) stamps `kept_at`/`kept_by = 'exemplar-backfill'` on
exactly one detection per species key — the earliest by `event_start_utc`,
ranked with a `ROW_NUMBER()` window function rather than `GROUP BY … HAVING
event_start_utc = MIN(…)`, because two detectors can fire on the same window
and share an `event_start_utc` to the second, which would otherwise stamp
both rows for one species. A first-ever record of a species at this station
cannot be recreated once its clip is gone. A *better* recording, by contrast,
may come along at any time — "best" was always a moving target the computed
rule re-evaluated every sweep, and there is no equivalent loss in leaving it
unflagged: nothing is destroyed by not backfilling it, and a human can mark a
genuinely exceptional recording kept the moment they hear it. The backfill
set is therefore smaller than the ~177 recordings the old computation
protected, which was the union of first and best.

**Why kept survives the watermark, and what that costs.** ADR-026's
watermark tier is this project's one hard safety valve: disk space wins over
any retention preference, so that clip writes never fail outright. Making
`kept` exempt there too means a station whose operator keeps enough evidence
can, in principle, stay over its watermark with nothing left to reclaim.
`_watermark_reclaim` now reports this rather than overriding it:
`RetentionReport.watermark_blocked_by_kept` sums the bytes held by kept,
un-reclaimed assets, computed only when the watermark is actually exceeded so
a healthy station never pays for the query. It surfaces in
`housekeeping.retention_not_keeping_up`, `GET /api/v1/retention/status`, and
as a named `/api/v1/health` `problems` entry naming the byte figure and that
these are operator-kept recordings the sweep will not delete. The operator
gets a loud health problem instead of a silently deleted keep, which is the
trade this project's charter requires: a human's decision outranks the
sweep's convenience. `held` (ADR-043) is unchanged and deliberately narrower
— it exempts the three age-based tiers but not the watermark, since it is a
review-workflow marker ("needs my ear"), not a permanent keep; an operator
who needs the watermark exemption too must mark the detection `kept`.

**Breaking metric rename, accepted deliberately.**
`oo_retention_exemplar_detections` becomes `oo_retention_kept_detections`,
with **no backwards-compatible alias**. Any Home Assistant sensor or Grafana
panel bound to the old name breaks on this deploy. This was a deliberate
choice, not an oversight: the number now counts something genuinely
different — detections a human chose to keep, not detections a computation
decided were exemplary — and giving it the old, familiar name over changed
semantics would be exactly the kind of dishonest instrumentation this
project's counters exist to refuse (see `MEMORY.md`,
"Measure the instrument, not just the thing"). `RetentionReport.exemplar_detections`
is renamed to `kept_detections` for the same reason, and every consumer
(`api/app.py`, `api/metrics.py`, `cli.py`, `RetentionPanel.tsx`) was updated
in the same commit so nothing reads the old field name.

**A deviation from the approved design, ruled on here.**
`docs/superpowers/specs/2026-08-14-keep-flag-retention-design.md` specified
that a sweep exiting with budget unspent while candidates remain should log a
`WARN` enriching the existing `housekeeping.retention_not_keeping_up` event.
The implementation instead adds a **separate ERROR event**,
`housekeeping.retention_never_reached_a_tier`, gated on
`len(result.tiers_skipped) == 4`, and leaves `retention_not_keeping_up`
untouched. **Ruling: accepted as an improvement on the design doc, not a
regression from it.** The existing WARN fired every 300 s for nine days
during the incident this ADR fixes, and nobody noticed — the noise was *why*
the failure hid, not despite it. Enriching a WARN that already fires
routinely would have reproduced the same blind spot with more fields
attached. An event that fires only when a sweep did no work at all — every
tier skipped, not merely a backlog trailing off — is rare enough in a healthy
station's steady state to mean something when it does fire.
`len(tiers_skipped) == 4` is sound as the gate specifically because the
watermark tier's guard has no config-disable clause, unlike the other three
(each can be turned off by setting its `*_days` to 0): all four can only
land in `tiers_skipped` together through genuine deadline or budget
exhaustion, never through configuration. `RetentionReport.preamble_s` (the
monotonic time from sweep start to the first tier guard) rides alongside
both events and the new `oo_retention_preamble_seconds` gauge, so a future
preamble regression is visible as a number close to `batch_budget_s`, not
just as an absence of deletions.

**Names that outlived the concept — left deliberately, decision deferred to
the operator.** The tier key `tier="exemplar_only"` (a Prometheus label and
an `/api/v1/retention/status` field) and the setting
`retention_exemplar_only_days` (env var `OO_RETENTION_EXEMPLAR_ONLY_DAYS`,
present in the live station's `runtime.env`) both still name the mechanism
this ADR removes — the 90-day tier now exempts `kept`, not "exemplars".
Renaming either is a second breaking metric change (`exemplar_only` is a
label value scraped by anything graphing per-tier deletions) plus a hand-edit
to an operator-owned env file this repository does not control. That is a
cost worth naming, not paying silently: this ADR leaves both names as they
are and records the mismatch here so it is found by reading this document,
not rediscovered as a mystery by whoever next greps `retention.py` for
"exemplar" and finds nothing computing one.

**Known asymmetry, out of scope here.** `_watermark_reclaim` already ignored
`held_ids` before this change — a held-but-not-kept recording could be
reclaimed at the watermark. This ADR makes `kept` exempt there; it
deliberately leaves `held`'s narrower, unchanged behaviour alone, since
widening ADR-043's watermark behaviour is a separate decision. An operator
who needs a hold to survive the watermark must mark it `kept` as well.

**Consequences.** A station that has never once deleted a file (nine days
overdue) starts deleting again on the next sweep after this deploys. The
preamble drops from an unbounded ~3 s scan to one indexed `COUNT` and one
`held_detection_ids` query — small and bounded regardless of table size,
which also removes the self-reinforcing growth loop. `oo_retention_exemplar_detections`
disappears from `/metrics`; anything graphing it must be repointed at
`oo_retention_kept_detections` by hand.

**Rollback.** `.venv/bin/alembic downgrade 0007_capture_pause` drops
`kept_at`/`kept_by` entirely. **Every operator keep is lost and is not
recoverable from the code** — export the kept detection ids first:

```bash
sqlite3 -json data/openobservatory.sqlite \
  "SELECT id, kept_at, kept_by FROM detection WHERE kept_at IS NOT NULL" \
  > kept-detections-backup.json
.venv/bin/alembic downgrade 0007_capture_pause
sudo systemctl restart open-observatory
```

Reverting the application code without downgrading the migration is safe on
its own: `kept_at`/`kept_by` are additive columns and nothing reads them once
`retention.py`'s exemption clauses are reverted alongside it — but doing that
without the migration leaves the columns in place unused, which is a valid
intermediate state, not a hazard.

**Target smoke test**, to run after deploying and before trusting the
figures:

```bash
HOST=simon@192.168.1.195 ./deploy/deploy.sh --no-web   # runs alembic upgrade head

curl -s http://192.168.1.195:8080/metrics | grep -E "oo_retention_(files_deleted|kept_detections|preamble_seconds)"
curl -s http://192.168.1.195:8080/api/v1/retention/status | python3 -m json.tool
curl -s http://192.168.1.195:8080/api/v1/health | python3 -m json.tool | grep -A3 retention
```

Pass criteria, none of which may be assumed from the first two alone (Task 6
of the implementing plan flags this explicitly):

1. `oo_retention_files_deleted_total{tier="native"}` is **non-zero** —
   deletion has never once happened on this station, so this is the claim
   that matters most.
2. Sweep `duration_s` is **inside** `retention_batch_budget_s` (1.5 s), and
   `complete` is `true`. `preamble_s` should read as a small fraction of the
   budget, not close to it.
3. After ~30 minutes, `capture_gap` rows are **no longer arriving in pairs
   ~300 s apart** (read-only against the station database,
   `sqlite3.connect('file:...?mode=ro', uri=True)`). If the beat is still
   there, the sweep is still costing audio and the fix is incomplete — that
   must be reported plainly rather than reporting (1) and (2) as success on
   their own.

### ADR-061 addendum, 2026-08-14: the first deploy failed, and the index was why

**The pass criteria above were run, and criterion 1 failed.** Recorded here
rather than quietly fixed, because the failure is more instructive than the
decision it followed.

The 2.978 s exemplar preamble was genuinely gone: `preamble_s` was ~0 and the
sweep reached `_strip_native` for the first time in the station's life. It then
blocked **inside a single SQL statement for over five minutes.** `py-spy dump`
caught it exactly:

```
do_execute (sqlalchemy/engine/default.py:941)
  _strip_native (open_observatory/retention.py:397)
    sweep (open_observatory/retention.py:288)
```

Retention runs in the evidence executor and the housekeeping loop awaits it, so
everything behind it stopped: stream heartbeats, the ADR-057 media audit, the
ADR-059 disk-usage refresh. `clip_usage_age_s` climbed 1:1 with wall clock and
`housekeeping_blocking_s` was byte-identical between samples — a stopped clock,
not a slow one. Capture was untouched, because it owns its own thread (ADR-030).

**Cause: `ix_detection_kept_at`, added by this ADR's own revision 0008.**
`kept_at IS NULL` matches ~99.8% of rows (112 non-null of ~46,000), so the index
narrows nothing — but SQLite preferred it, and preferring it meant abandoning
`ix_detection_event_start_utc`, which had been serving the range predicate *and*
the `ORDER BY` together. Measured on the station's own database, best of three:

| | Plan | Time |
|---|---|---|
| Index present | `SEARCH d USING ix_detection_kept_at` + `USE TEMP B-TREE FOR ORDER BY` | 0.555 s |
| Index dropped | `SEARCH d USING ix_detection_event_start_utc (event_start_utc<?)`, no sort | 0.117 s |

Revision `0009_drop_kept_at_index` drops it. The predicate still applies; it is
just evaluated against rows the ordering index has already narrowed.

**Three things worth keeping from this.**

1. **923 passing tests could not have caught it, and did not.** No fixture in
   this repository is within two orders of magnitude of 119,476 media assets and
   ~46,000 detections, and below some size SQLite's planner simply makes the
   right choice. A green suite is not evidence about a query plan. Measure the
   plan on a database the size of the station's, or do not claim anything.
2. **`retention_batch_budget_s` cannot bound a single statement.** The budget is
   checked *between* rows of the result. A slow query therefore blows through it
   entirely instead of degrading to "fewer deletions", which is what the budget
   was designed to do. This was always true; the old code never reached a tier,
   so it never showed. **Still open** — the honest fix is a statement timeout
   (`sqlite3` `progress_handler`, or `Connection.interrupt` from a watchdog),
   not a larger budget.
3. **An index added to make a filter cheap can make the query dearer**, when the
   planner takes it in preference to one that was also providing the ordering. A
   low-selectivity index is not merely useless; it is a live hazard next to an
   `ORDER BY ... LIMIT`.

**Interim state on the station:** `retention_enabled=false` was written to the
station's `config/runtime.env` to unwedge the housekeeping loop, and must be set
back to `true` once revision 0009 is deployed and criterion 1 actually passes.

### ADR-061 second addendum, 2026-08-14: the same symptom, a third cause

Revision 0009 fixed `_strip_native` and broke the other half of the same sweep.
`RetentionReport.kept_detections` counts `kept_at IS NOT NULL`; with no index
that is a full `SCAN detection` over **290,956 rows**, measured ~6 s on the live
station under WAL contention. It sits in the sweep's *preamble*, before any tier
guard, so the 1.5 s budget was gone before the first tier and all four were
skipped. Zero deletions again — same symptom, third distinct cause.

**The observability from this ADR is what made that a ten-minute diagnosis
rather than a nine-day one.** The first sweep after deploy reported
`preamble_s: 6.0778`, `duration_s: 6.0781` and
`tiers_skipped: ["native","unkept","expired","watermark"]`. That is the entire
failure, stated by the station, on its first occurrence. Before this ADR the
same condition produced `complete=False` and a flat zero — indistinguishable
from "nothing to delete".

**Resolution: a partial index (revision 0010).** The two requirements only look
contradictory:

* the count wants an index on `kept_at`;
* the four candidate queries must have none available, or SQLite prefers it over
  `ix_detection_event_start_utc` and loses the ordering.

`CREATE INDEX ... ON detection(kept_at) WHERE kept_at IS NOT NULL` indexes 112
rows of 290,956. The planner may use it for `IS NOT NULL` and cannot use it for
`IS NULL`. Measured on a copy of the station's database, best of three:

| | before | after |
|---|---|---|
| `kept_detections` count | 0.151 s, `SCAN detection` | **0.000 s**, covering index |
| `_strip_native` candidates | 0.113 s, `ix_detection_event_start_utc` | 0.115 s, **unchanged**, no sort |

`tests/test_migrations.py` asserts the `WHERE` clause itself, not the index
name: a plain index would satisfy a name check and restore both failures at
once.

**The generalisable lesson, since this ADR has now been wrong twice about the
same column.** An index is not a property of a column, it is a property of the
*query plans it makes available* — including the plans you did not want. Both
mistakes were locally reasonable ("the filter should be indexed", then "so drop
the index") and both were made without measuring a plan on data the size of the
station's. The rule this ADR should have followed from the start, and now
states: **for any index on a hot path, produce `EXPLAIN QUERY PLAN` against a
station-sized database, for every query that touches the column, before and
after.** 924 passing tests said nothing useful about any of this.

### ADR-061 third addendum, 2026-08-14: the deferred rename, taken — and the 90-day tier retired as dead code

The "Names that outlived the concept" section above deliberately left
`tier="exemplar_only"` and `retention_exemplar_only_days` alone and deferred
the decision to the operator. The operator has now made it, and a second
finding came with it: the 90-day tier those names belonged to,
`_strip_expired`, is unreachable and is removed as dead code, not merely
renamed.

**Why `_strip_expired` never ran.** `_strip_unkept` (30 d) and
`_strip_expired` (90 d) issued byte-for-byte identical queries — same joins,
same `reclaimed_at IS NULL`, same `kept_at IS NULL`, same `notin_(held_ids)`,
same `order_by(event_start_utc.asc())`, same `limit(budget)` — differing only
in the cutoff constant and the label written to the decision. Because 90 > 30,
`_strip_expired`'s candidate set was always a subset of `_strip_unkept`'s, and
`_strip_unkept` ran first, oldest-first, against the same shared `budget`.
Three ways this was checked, not assumed:

1. If `_strip_unkept` exhausts its `budget` or the wall-clock `deadline`
   before finishing, the `budget > 0 and time.monotonic() < deadline` guard
   before `_strip_expired` fails and it is skipped outright.
2. If `_strip_unkept`'s query returns fewer rows than `budget` (proof it
   found every row matching a strictly looser condition), every row
   `_strip_expired` could ever have wanted was already offered to and
   processed by `_strip_unkept` first.
3. There is no asset-kind filter, ordering subtlety, or budget interaction
   that changes this: both queries scan the same table with the same joins
   and the same order.

**Correction (fourth correction to this ADR, final pre-merge review,
2026-08-14): the unreachability claim above is about row *logic*, given the
default tier configuration, and was stated without qualification. It is not
also true of every configuration.** Both `_strip_native` (`retention_native_days`)
and `_strip_unkept` (`retention_audible_only_days`) are individually
disabled by setting their `*_days` to `0` (`sweep()`'s guards:
`self.native_days > 0 and ...`, `self.audible_only_days > 0 and ...`). With
both set to `0`, both age tiers are disabled by configuration, not by the
row-subset argument above, and since `_strip_expired` no longer exists to
fall back on, **nothing in `retention.py` ever deletes a clip by age** —
only `_watermark_reclaim`, which `kept` and `held` both partially or wholly
exempt. `validate_merged` (`site_settings.py`) previously permitted this
combination outright, and the settings help text for both fields never
said `0` disables the tier. Fixed the same day this was found: a
`validate_merged` rule now rejects `retention_native_days == 0 and
retention_audible_only_days == 0` together, and both fields' help text
states plainly that `0` disables the tier and what disabling both leaves
running (the watermark only). An operator who wants every clip kept forever
should raise `retention_watermark_ratio` and mark evidence `kept`, not zero
both age tiers -- the watermark is this project's one tier with no
config-disable clause, by design (see the "Why kept survives the
watermark" section above), so it is the correct place to express "never
delete by age" rather than a configuration this ADR now blocks.

This was true before ADR-026's `kept` predicate existed too, but it did not
matter then: exemplars were exempt from the 30-day tier but not the 90-day
one, so the two tiers protected different rows and "between 30 and 90 days,
only exemplars survive" was a real, distinct policy. `kept` (this ADR)
exempts a detection from *every* tier identically, which is what collapsed
the two tiers into duplicates without anyone deciding that on purpose.

**What changed.** `_strip_expired`, its `sweep()` call site, the `"expired"`
tier key, the `exemplar_only_days` constructor parameter/attribute, and the
`retention_exemplar_only_days` setting (`OO_RETENTION_EXEMPLAR_ONLY_DAYS`) are
all removed. `_TIER_ORDER` and `RetentionReport.tiers_skipped` go from four
tiers to three (`native`, `unkept`, `watermark`), so
`housekeeping.retention_never_reached_a_tier`'s gate moves from
`len(tiers_skipped) == 4` to `== 3` — still every tier, still sound for the
same reason: the watermark guard has no config-disable clause, so all three
land in `tiers_skipped` together only through genuine deadline or budget
exhaustion, never configuration. `GET /api/v1/retention/status` loses its
"kept only" tier entry (it described the now-nonexistent 30–90 day band);
`eligible_for_deletion`'s cutoff moves from `exemplar_only_days` to
`audible_only_days`, since that is now the last age boundary the policy has.

**Test-first verification.** Before touching `retention.py`, a
characterisation test was added and confirmed to pass against the
*unmodified* code: a detection 200 days old is deleted, its recorded tier is
never `"expired"`, and `tier_counts.get("expired", 0) == 0` — i.e. the 90-day
tier already contributes nothing, on the code as it stood. The same test
passes unchanged after the removal (`"expired"` simply cannot appear once the
key no longer exists), which is the point of a characterisation test: the
observable behaviour is identical on both sides of the change.

**The tier key rename, taken this time.**
`oo_retention_files_deleted_total{tier="exemplar_only"}` becomes
`{tier="unkept"}`, and `{tier="expired"}` disappears with the tier it named.
Both are breaking Prometheus label changes, accepted deliberately for the
same reason the metric rename earlier in this ADR was: a label naming a
mechanism (`exemplar_only`) or a tier (`expired`) that no longer exists is a
worse instrument than a breaking one. No alias is added.

**The silent-drop hazard this leaves behind.** `Settings` is built with
`extra="ignore"`, so an operator's `runtime.env` that still sets
`OO_RETENTION_EXEMPLAR_ONLY_DAYS=90` — including the live station's, per the
"Names that outlived the concept" section above — will not raise on startup;
Pydantic drops it silently, and the value is simply never read again. That is
the dangerous kind of stale config: not a crash that demands attention, but a
setting that keeps *looking* live in an env file while doing nothing. This is
recorded here rather than fixed here, because fixing it (an unknown-key
warning, or refusing to start) is a `Settings`-wide decision, not one this
retention change should make unilaterally — but the next person who greps
`runtime.env` for why a number they changed had no effect should find the
answer here.

**Consequences.** No behaviour change to what gets deleted or when — the
characterisation test is the evidence for that claim, not merely an
assertion of it. `retention.py`'s module docstring's tier table drops the
30–90/90+ split in favour of a single "30+ days: only kept survives"
statement. The live station's `runtime.env` still carries
`OO_RETENTION_EXEMPLAR_ONLY_DAYS=90`; per the paragraph above, this deploy
makes that line inert rather than erroring, and it should be removed by the
operator at the same time as the deploy, not left to be rediscovered later.

## ADR-062: Retention walks the live assets, not the whole history

**Status:** accepted, 2026-08-19
**Supersedes in part:** ADR-026 (tier age is now measured on the asset), ADR-061
(revision 0001's `ix_media_asset_reclaimed_at` is dropped)

### The failure

Between 2026-08-17 and 2026-08-19 the station reclaimed nothing. 351 of roughly
526 sweeps ended `complete=False`, `total_deleted=0`, `tiers_skipped=['native',
'unkept', 'watermark']`, every five minutes, while the clip volume climbed from
72% to 78% at 3.8 GB/hour. `/api/v1/health` returned `status: "ok"` with an
empty `problems` list throughout.

The proximate error in the log was honest and specific:

    retention.statement_interrupted  after_s=1.5109  batch_budget_s=1.5  tier=native

`_bounded_statements` did exactly its job. The native tier's candidate query
could no longer finish inside the 1.5 s budget, so it was aborted, and because
the native tier is first, the budget was gone before the unkept and watermark
tiers were reached. **The watermark tier is the safety valve for disk pressure,
and it lives inside the sweep that was failing**, so there was no backstop: the
mechanism that should have caught the rising disk was one of the casualties.

### Root cause

Every tier ordered by `detection.event_start_utc` and filtered
`media_asset.reclaimed_at IS NULL`. Those two facts live in different tables,
so the walk was driven by `ix_detection_event_start_utc` and had to join out to
`media_asset` for each row to discover whether that row still had a file.

Rows already reclaimed stay in `ix_detection_event_start_utc` forever. So the
sweep had to step over everything it had already done to reach anything it had
not. Measured on the station's database on 2026-08-19: 38,268 reclaimed assets,
~210,000 old detections examined per sweep, to find **1,699** genuinely
outstanding native clips.

**The query got slower every single time it succeeded.** It ran in 0.986 s on
2026-08-17 and could not finish in 1.5 s two days later. Nothing "went wrong" —
this was the designed behaviour meeting enough history.

The measurement that identified it, and the one worth remembering:

| `LIMIT` | 1 | 10 | 50 | 250 | 1000 | 4000 |
|---|---|---|---|---|---|---|
| time | 2.29 s | 2.22 s | 2.25 s | 2.17 s | 2.20 s | 2.21 s |

Constant. A query whose `LIMIT 1` costs the same as its `LIMIT 4000` is not
selecting rows, it is scanning to reach the first one. That single table ruled
out three plausible-but-wrong hypotheses (a bad plan — the plans were identical
either way; a slow join; accumulation cost) in one step.

### Decision

**1. Partial indexes keyed on the asset, excluding reclaimed rows.**

    CREATE INDEX ix_media_asset_live_kind_created
        ON media_asset (kind, created_at) WHERE reclaimed_at IS NULL;
    CREATE INDEX ix_media_asset_live_created
        ON media_asset (created_at)       WHERE reclaimed_at IS NULL;

The `WHERE` clause is the mechanism, not an optimisation: **a reclaimed row
leaves the index**, so completed work is never walked again. `created_at` is
last so the range predicate and the `ORDER BY` are satisfied by one index and
the scan stops at `LIMIT` instead of sorting.

**2. Tier age is measured on `media_asset.created_at`, not
`detection.event_start_utc`.** This is a real semantic change and the reason it
is safe is empirical: across all 86,377 native assets on the station,
`created_at` is *always* later than its detection's `event_start_utc` (minimum
+0.45 s, mean +823 s, maximum +29 h, zero exceptions). So `created_at <= cutoff`
implies `event_start_utc <= cutoff`, and the substitution can only make a tier
**late**, never early — it cannot delete a clip before its policy age. The cost
is that a clip written unusually long after its event survives its tier by up to
that lag, which resolves itself as the cutoff advances.

**3. An index on the reverse join.** `detection_media`'s primary key indexes
`(detection_id, media_asset_id)`; retention travels the other way and had no
index for it (0.114 s per 250 candidates, scanning the link table).

**4. `ix_media_asset_reclaimed_at` is dropped, and that is load-bearing.**
`reclaimed_at` is NULL on 176,231 of 214,499 rows. SQLite treats a plain index
on it as a cheap way in and prefers it over the partial ones, losing the
`ORDER BY` and adding a `USE TEMP B-TREE`. With it present the native tier plans
at 0.1215 s; without it, 0.0004 s.

**5. A run of barren sweeps is a health problem.** Three consecutive sweeps that
neither complete nor delete anything now populate `problems`, so the endpoint
says what the storage block already knew.

### Measured result

Best of three, on a copy of the live database:

| tier | before | after |
|---|---|---|
| native | 2.2000 s | 0.0049 s |
| unkept | (same shape, would degrade identically) | 0.0000 s |
| watermark | 1.8048 s | 0.0032 s |
| asset → detection join, 250 candidates | 0.1144 s | 0.0046 s |

### Do not run `ANALYZE` to "help"

It was tried. It made things worse, and instructively so. `sqlite_stat1` records
an average of 6 rows per `reclaimed_at` value — arithmetically true and
completely useless, because the NULL bucket holds 176,231 of them. With those
statistics present the planner abandoned the correct plan and returned to the
temp B-tree: 0.0004 s → 0.1215 s. This is the third time on this project a
confidently-wrong measurement has pointed at the wrong fix; see ADR-061's
addenda for the first two.

### Consequences

- This is the **third** index incident here with one shape: a low-selectivity
  index the planner prefers over the one that serves the query
  (`ix_detection_kept_at`, revision 0009; `ix_media_asset_reclaimed_at`, now).
  `tests/test_retention.py::TestCandidateQueryPlans` asserts the plans
  directly, via `EXPLAIN QUERY PLAN`, because every one of these incidents was
  invisible to functional tests — the answers stayed correct, they just took
  400× longer. A wall-clock assertion would be flaky on a loaded Pi and silent
  on a fast dev box; the plan is what regressed, so the plan is what is pinned.
- `retention_batch_budget_s` still cannot bound a statement on PostgreSQL
  (`_bounded_statements` is a SQLite progress handler). Unchanged by this ADR,
  still open.
- Rollback: `alembic downgrade 0010_kept_at_partial_index` restores the previous
  indexes and plans; the tier code reads `created_at` regardless, which without
  the partial indexes is merely slow, not wrong.

## ADR-063: The stream clock re-anchors when the wall clock steps

**Status:** accepted, 2026-08-19
**Amends:** the `StreamClock` contract (technical spec §4.3)

### The failure

The operator noticed a banner in the live-listen view reading

    hearing 111.3 s ahead of the newest column ±0.2 s

on both the audible and ultrasonic panels. Hearing sound *ahead* of the picture
is not a thing that can happen, so one of the two numbers was wrong.

It was the picture — or rather, the timestamp on it. Measured against the
station's own wall clock (agreeing with an independent host to 0.044 s), live
spectrogram columns arrived stamped **113.8 s in the past**, steadily, on both
channels, while frames arrived at full real-time rate. The station's own
arithmetic agreed: over 178,749 s of wall time it had delivered 178,634 s of
audio, a 115 s deficit that frame accounting could not explain (actual vs
expected frames differed by only 9.26 s, the AudioMoth's −51 ppm crystal
offset).

Two different channels, derived from two different frame counters at two
different sample rates, wrong by the identical amount. That points at the one
thing they share: `Station.clock`.

### Root cause

`StreamClock` samples the monotonic and wall clocks **once**, when the first
block of a stream arrives, and thereafter answers "when did frame N happen?" by
counting frames from that anchor. That is a deliberate and good design — it is
what makes derived-audio timestamps exact rather than wandering by up to ~19 ms
with the resampler's ragged chunk sizes, and `contracts.py` says so.

What nobody had considered is that it makes the anchor's *correctness*
permanent too. The module docstring claims "an NTP step can never reorder
frames", which is true and was the only property anyone checked. An NTP step
cannot reorder frames. It can make every single one of them carry the wrong
UTC, for the life of the stream.

The journal from the boot in question:

    10:07:30  capture.opened
    10:07:32  station.clock_anchored  utc=2026-08-17T09:07:32.173784+00:00
    10:09:17  systemd-timesyncd: Initial clock synchronization to 10:09:17 BST
    10:09:17  systemd-resolved: Clock change detected. Flushing caches.

A Raspberry Pi has no battery-backed RTC. It boots with the timestamp systemd
saved at last shutdown and NTP steps it forward once the network is up. Capture
anchored **1 minute 45 seconds before** that step, and the step was ~106 s.

So this is not an exotic race. On this hardware it is the ordinary boot path,
and it will happen on **every** unattended reboot where the network comes up
after capture does — including the 2026-08-17 reboot that voided the 72-hour
soak.

### Blast radius

Every UTC value the station derived from frames for those 49 hours was ~106 s
early: `detection.event_start_utc` and `event_end_utc`, clip filenames, clip
`created_at`, spectrogram column times, MQTT payload timestamps.

Not affected: anything keyed to the monotonic clock — frame ordering, gap
detection, durations, `capture_gap` records, the continuity ratio. That part of
the original design held exactly as intended.

### Decision

1. **Detect and correct steps, once per housekeeping tick.**
   `StreamClock.stepped_by(sample)` compares the anchor's implied wall-minus-
   monotonic skew against a fresh `ClockCorrelation`; past a 1 s threshold,
   `Station._reanchor_clock_if_stepped` swaps in `clock.reanchored(sample)`.

2. **`monotonic_ns_at_frame_zero` is never touched.** The monotonic clock does
   not step; frame N still happened when it happened. Only the UTC *name* of
   that instant changes, so every ordering and duration measurement stays valid
   across a re-anchor.

3. **Steps only, never slew.** NTP slews at up to 500 ppm — about 5 ms across a
   10 s tick, three orders of magnitude under the threshold. Correcting slew
   continuously would reintroduce precisely the timestamp jitter `StreamClock`
   exists to remove.

4. **The correction is forward-only, and says so.** Rows already written keep
   their wrong value. `station.clock_reanchored` logs the step size and both
   anchors, and `oo_capture_clock_reanchors_total` /
   `oo_capture_clock_last_step_seconds` make it alertable — so "which timestamps
   are wrong, and by how much" is answerable after the fact rather than
   guessable. No backfill is attempted: the correct offset for an arbitrary
   historical row is not recoverable from anything the station kept.

5. **`After=time-sync.target` on the unit**, as a cheap reduction in how often
   this fires at boot. Deliberately *not* `systemd-time-wait-sync.service`,
   which would block capture indefinitely on a station with no network —
   recording with an imperfect clock beats not recording.

### Consequences

- A re-anchor introduces a one-off discontinuity in derived UTC. Audio
  timestamped either side of it is up to `clock_last_step_s` apart in UTC while
  being contiguous in the recording. This is the honest representation: the
  timeline really did have a naming error, and the alternative is keeping the
  error forever.
- **The only reason this was ever found is that a human looked at a live-listen
  banner in a browser.** Nothing in the health payload, the metrics, the logs or
  the soak criteria would have caught a 106 s timestamp error — the continuity
  ratio was 0.999949 throughout, because continuity is a monotonic-clock
  property and was genuinely fine. `oo_capture_clock_reanchors_total` exists so
  the next one is caught by the instrument rather than by eye.
- The playhead banner was right, and was the only thing that was. It is worth
  keeping honest for that reason: a UI that had clamped the impossible-looking
  negative number to zero would have hidden a real data-integrity bug.
