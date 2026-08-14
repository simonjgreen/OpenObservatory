# Test plan

What "tested" has to mean for this system, why each layer exists, and which
layers are currently thin. Ordered by `docs/CHARTER.md`: a layer that protects
a higher charter priority matters more than one that protects a lower.

This is a *plan*, not a status report. `MILESTONE_STATUS.md` records what is
done; this records what completeness would look like.

---

## The governing lesson

Nearly every serious bug in this project passed its tests first. The pattern is
consistent enough to design against:

| Bug | Why the tests missed it |
|---|---|
| Concurrent WebSocket writers | Loopback sends completed too fast to overlap. Perfect on the Pi, near-total failure in a browser over Wi-Fi. |
| Home Assistant acoustic-event filter | The test invented `taxonomic_group=None`; the detector actually emits the sentinel `"acoustic_event"`. Test asserted an assumption, not the system. |
| Deficit estimator over-reporting 12.9x | Nothing compared the estimate against ground truth, because there was no ground truth to compare to. |
| Retention starving capture | Every unit test passed; the cost only appeared as event-loop lag on real hardware. |
| Coverage reading 1302% | No test asserted the arithmetic was *possible*. |
| Spectrogram gating (2026-08-09) | Gate logic was unit-tested; the wiring between hub and encoder was not. |

Three rules follow, and they are the point of this document:

1. **Test against ground truth, not against a second estimate.** Inject a known
   loss and assert the reported loss equals it.
2. **Fixtures must carry values the real system emits.** Read them off the live
   station or the database, never invent them.
3. **A test on a component is not a test of the wiring.** Most failures here
   were in the seams.

---

## Layers

### L1 — Pure logic (host, fast, no I/O)

Everything under `src/open_observatory/` that does not touch hardware, plus
`web/src/state/` and `web/src/components/geometry.ts`, plus
`firmware/inside-observer/src/model/`.

Deliberately hardware-free so the rules that matter — what appears, in what
order, at what time — are testable on a laptop. Hypothesis where the input
space is wide (frame arithmetic, time bucketing, retention tiers).

**Runs:** `pytest`, `npm test`, `pio test -e native`. Seconds.

### L2 — Contract and schema

The shapes something *outside* this repository depends on:
`schemas/detection-event.schema.json`, the MQTT/Home Assistant discovery
payloads, the `/api/v1/display` wire format, and the REST responses the ESP32
parses.

Validate **real emitted events** against the schema, not hand-written examples —
that is the only version that catches drift. A wire format with an external
consumer needs a versioning test: an old consumer must not break silently.

**Currently thin:** the display wire format has host tests; nothing asserts the
firmware and server agree if only one changes.

### L3 — Integration, in-process

The real app, real pipeline, real database, synthetic or replay audio source.
`tests/test_api.py` is the model. Every endpoint exercised through the app, not
by calling its handler.

**Rule:** a new endpoint without an HTTP-level test is not done. The true-division
bucket bug (1899 ten-minute buckets in a twelve-hour window) was invisible to
function-level tests and obvious through the app.

### L4 — Failure injection

The charter's items 1 and 2 are mostly *failure* properties, so they need
failure tests. Each of these has actually happened:

| Failure | Must assert |
|---|---|
| Microphone disappears mid-stream | Degrades, announces itself, **and recovers unattended** |
| Capture wedges without erroring | Detected; coverage tells the truth about the silent period |
| Disk fills / SSD unmounted | Named degradation, capture continues |
| MQTT broker down or rejecting | Capture and detection unaffected; bounded queue drops counted |
| Wi-Fi lost at the display | Visibly stale, reconnects unattended |
| Database locked / slow | Bookkeeping fails without taking capture down |
| Detector throws or hangs | Circuit breaker; windows dropped and counted |

**Currently the thinnest layer, and the one guarding the highest priorities.**

### L5 — Target hardware (aarch64 Pi 5)

Some things are only true on the target: ALSA negotiation, `ai-edge-litert`
inference, real timing, real SD-card latency.

`CLAUDE.md` is explicit — a detector is "supported" only once its fixture test
passes **on target architecture**. `tests/test_birdnet_fixture.py` now does
(3 passed, 6.83 s). Anything asserting a measured figure belongs here.

### L6 — Real client over the real network

**Loopback is not a test of a network path.** This is the project's
hardest-won lesson and it has its own layer because of it.

Anything touching a live channel — WebSocket, chunked-WAV audio, the display
push channel — must be measured from a real browser or the real ESP32 over
Wi-Fi before it is believed. Chrome automation covers the browser; the device's
own counters (`display_channel.per_client`) cover the display.

### L7 — Soak and endurance

**The 72-hour soak ran 2026-08-10 to 2026-08-13 and failed**, at 99.865%
continuity against a ≥ 99.9% criterion (349.3 s lost out of 259,200 s; see
`docs/delivery/MILESTONE_STATUS.md` §Milestone 4.5). `CLAUDE.md` forbids the
word "complete" until it passes, and it has not passed yet. A re-run is needed
once ADR-060 and ADR-061 are deployed and verified; a deploy voids a soak in
progress, so it needs a deliberate quiet period.

Watch: `oo_capture_continuity_ratio`, real deficit as judged by
`estimated_missing_seconds` (**not** the raw `expected_frames - frames`,
which also carries crystal drift and sampling-phase artefact — see ADR-046),
`loop_lag_max_s`, `late_reads`, RSS, disk growth, clip budget, MQTT drops,
display reconnects.

Also outstanding: the **one-hour drift run** (verified at 5 minutes only).

Longer-horizon endurance the soak will not cover: SD-card write amplification,
and detection-table growth (ADR-037's triggers are the tripwires).

### L8 — Data integrity and irreversible operations

Anything that deletes or rewrites the operator's record:

- Migrations: upgrade **and** downgrade, on a copy of the real database, with
  row counts unchanged either side. `alembic check` must report no drift.
- Retention: every tier boundary, the watermark reclaim, and that **dry-run
  output matches what a real sweep does**.
- Repair paths (`reconcile-streams`, `reconcile-plausibility`): dry-run first,
  original claim preserved, never a silent rewrite.

### L9 — Regression corpus of measured figures

The measured tables in `TARGET_DIAGNOSTICS.md` and `HANDOVER.md` are a test
oracle, not decoration. A change that moves continuity, hot-path CPU, inference
p95 or bytes-on-the-wire should have to justify it.

Not yet automated. The honest first step is asserting the ones with hard
thresholds — hot-path CPU under budget, continuity above 0.999, display frames
under one MTU.

---

## What completeness would require

Roughly in charter order:

1. **L4 failure injection built out.** Highest value, thinnest coverage, guards
   charter items 1–2.
2. **The 72-hour soak, then the one-hour drift run.** Blocks "complete".
3. **L6 made routine** rather than a thing each agent improvises.
4. **L2 firmware/server contract test** so the display and station cannot drift
   apart silently.
5. **L9 thresholds** on the figures that already have budgets.
6. Refinement (charter item 5), once it exists, needs its own L8 tests: a
   refined record must be distinguishable, reversible, and never fabricated.

## What "done" means for a change

- Unit tests, and an HTTP-level test if it adds an endpoint.
- A failure test if it can fail (L4).
- Measured on target if it claims a figure (L5).
- Measured from a real client if it touches a live channel (L6).
- Dry-run proven if it deletes or rewrites (L8).
- Docs updated, rollback noted.
- **Nothing claimed that was not observed.** If a layer was skipped, say so.
