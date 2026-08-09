# Gap, contradiction and unresolved-assumption report

Produced before implementation, as required by `CLAUDE.md`. Status date: 2026-08-04.
Target device: `pi2` at `station.example`.

**Revised 2026-08-05.** The pre-implementation findings are kept as written, because the
record of what was assumed is worth more than a tidy document. Where something has since
been settled on real hardware it is marked **RESOLVED** with a pointer to the measurement.
`docs/delivery/MILESTONE_STATUS.md` is the authority on progress; this file is the
authority on what was uncertain and how it was decided.

## 1. Hardware findings that contradict the seed assumptions

| Seed assumption | Actual target state | Consequence |
|---|---|---|
| Raspberry Pi 5 running 64-bit **Raspberry Pi OS** | **Ubuntu 24.04.3 LTS** aarch64, kernel `6.8.0-1060-raspi` | No `raspi-config`, different package names, no Pi-OS-specific ALSA config. Python 3.12.3 is the system interpreter, which matches the required stack. |
| AudioMoth USB Microphone attached | At the time of writing, **no USB device present** — `lsusb` listed root hubs only. **RESOLVED 2026-08-04:** the device is attached and characterised. Single profile, 384 kHz mono S16_LE, stable key `usb-16d0:06f3:0384_2453800264933F8F`. See `docs/operations/TARGET_DIAGNOSTICS.md`. | Everything downstream was developed against the mandated replay source first, which is why the replay path is real rather than a stub. Live capture has since been validated on hardware. `oo audio probe` works with zero, one or many capture devices. |
| USB SSD strongly preferred for data | Single 235 GB SD card (`/dev/mmcblk0p2`), 222 GB free. **RESOLVED 2026-08-08:** a 465.8 GB SanDisk Extreme USB SSD is now mounted at `data/clips` for evidence (ADR-021); the database deliberately stays on the SD card. | Acceptable for the debug slice. Continuous native-rate archival stays off by default, as the spec already requires. The SD card turned out to be a real write-throughput constraint on a busy bat night, not a theoretical one. |
| Docker Compose deployment | Docker not installed | See ADR-008. |
| 8 GB Pi assumed by memory budget | 7.8 GB usable, 4 cores | 120 s native ring at 384 kHz (~92 MB) is comfortable. |

## 2. Contradictions inside the specification

1. **"Do not begin with the dashboard" vs. the current objective.**
   `README.md` says prove capture, buffering and replay determinism first, and the dashboard is Milestone 4. The operator's objective for this phase is explicitly a *debug* real-time UI. These are reconciled by treating the UI as an **observability surface over Milestones 1–3**, not as the product dashboard: it renders capture-block telemetry, ring-buffer state, window/job flow, detector lag and events. It is built on top of the real pipeline contracts, never on mock data. The product dashboard of Milestone 4 remains separate work.

2. **PostgreSQL 16 required vs. "keep the repository runnable at the end of each milestone".**
   Postgres is not installable as a zero-friction dev dependency on the current target (no Docker). See ADR-007.

3. **Redis Streams required vs. "A simpler in-process event bus may be used in the first capture prototype".**
   The second clause governs this phase. The bus is defined by a transport-neutral `EventBus` protocol so a Redis Streams implementation is a drop-in.

4. **Model licensing vs. "prove the bird detector".**
   ADR-006 forbids bundling BirdNET model assets. The BirdNET adapter is therefore an *optional* plugin that reports `unavailable` until the operator runs a documented acquisition step. To keep the UI's event surface honest and testable from the first run, a fully-owned **acoustic activity detector** (band-limited onset/energy segmentation) ships as the always-available first plugin. It makes no taxonomic claim.

5. **`detection-event.schema.json` requires `native_result`, but `additionalProperties: false` omits fields the data model needs** — notably `rank`, `taxonomic_group`, and any window/run identifiers. The schema is treated as the *published wire* contract; internal records carry more. **RESOLVED 2026-08-08 (ADR-025).** The MQTT publisher was indeed the forcing function. The schema now describes the full envelope, adds `rank`, `taxonomic_group` and `media`, and is bumped to `schema_version` 1.1 — a version bump rather than a silent widening, because a strict consumer validating against 1.0 would have rejected the new fields. `tests/test_mqtt_schema.py` validates real emitted events against it so it cannot drift again.

6. **`config/example.env` names `OO_AUDIO_DEVICE=/dev/snd/by-id/REPLACE_ME`.** `/dev/snd/by-id` does not exist on this target and only appears once a USB audio device is attached. Device selection is therefore by *stable device key* (a normalised USB vendor:product:serial, falling back to the ALSA card id string), resolved at open time — never by a card number. See §4.1 of the technical spec, which already forbids trusting `hw:1,0`. **RESOLVED:** `config/example.env` now ships `OO_AUDIO_DEVICE=` empty, with a comment saying to run `oo audio probe` for the real key.

## 3. Gaps the specification leaves open, and the decisions taken

| Gap | Decision for this phase |
|---|---|
| No spectrogram/live-audio transport is specified; the API doc only offers SSE for detections | WebSocket carrying binary spectrogram columns + JSON telemetry frames. **As built:** live listening became a *second* WebSocket (`/api/v1/live/audio`) rather than a chunked-WAV endpoint, so a stalled listener cannot block the visual channel. See ADR-012 and `docs/api/DEBUG_UI_TRANSPORT.md`. **Revised 2026-08-08 (ADR-019):** a chunked-WAV endpoint, `GET /api/v1/live/audio.wav`, was added *as well* and is now the debug UI's default listen path, because Web Audio produced no audible output at all on the operator's laptop. The WebSocket channel is unchanged and still used by a phone. Retuning the WAV path is `POST /api/v1/live/tune` (ADR-022). No SSE endpoint exists or is planned. |
| Capture block size for the *audible* derived stream is unspecified | 100 ms native blocks (spec §Recommended block size) produce 100 ms audible blocks; spectrogram hop is decoupled from block size. |
| Resampler library not chosen | `soxr` if a wheel is available on aarch64/cp312, else `scipy.signal.resample_poly`. Both are polyphase; the adapter records which was used so measurements stay attributable. |
| Clock correlation method unspecified | Single `time.clock_gettime(CLOCK_MONOTONIC)` / `time.time_ns()` pair sampled per capture block, plus a correlation record per stream. Monotonic is authoritative for ordering and duration; UTC is derived. |
| No definition of "event" for the debug UI | Four event families on one bus: `capture.*`, `window.*`, `detection.*`, `health.*`, all sharing the API doc's envelope. |
| Timezone/location for the station | `Europe/London`, coordinates unset until the operator supplies them. |

## 4. Unresolved and deliberately unclaimed

- ~~AudioMoth negotiated rate, format, channel count and gain behaviour.~~ **RESOLVED.** One profile only: 384 kHz mono S16_LE. Gain behaviour measured, and the input is found to clip on loud nearby events — reducing it needs the AudioMoth USB Microphone app and is still outstanding. `docs/operations/TARGET_DIAGNOSTICS.md`.
- ~~Whether 384 kHz capture is sustainable on this Pi/kernel/SD-card combination.~~ **RESOLVED over minutes, not yet over days.** Continuity 0.9990–0.9997 with zero gaps or overruns, ~29% of 4 cores with all three detectors. The 72-hour soak that would make this a durable claim has not been run.
- ~~BirdNET on aarch64/cp312: TFLite runtime availability is unverified.~~ **RESOLVED.** Runs via `ai-edge-litert` — not `tflite-runtime`, which has no cp312 aarch64 wheel and needs NumPy 1.x. Measured p95 77–109 ms, ~40× realtime.
- BatDetect2: still out of scope. Milestone 5. The native window contract it would need now exists, built for `ultrasonic-pass-v1` (ADR-013).

### Opened since implementation began

Each entry is kept as written and annotated with what happened to it, checked
against the code on 2026-08-09. A resolved gap is more useful with its history
than deleted.

- ~~**No fixture test proves a known species from a known recording.**~~ **RESOLVED 2026-08-08.** `tests/test_birdnet_fixture.py` runs real inference on a committed, individually licence-checked European Robin recording (Xeno-canto XC441752, CC BY-SA 4.0) and asserts both halves of the gate; it passed on the target Pi 5. See `MILESTONE_STATUS.md`'s Milestone 3 exit-gate note.
- ~~**No Alembic migration environment exists.**~~ **RESOLVED 2026-08-08 (ADR-035).** `alembic/` and `alembic.ini` exist with three revisions, wired to `Settings` and `Base.metadata`. The live station is at `0003_auth_tables (head)`. **Still open within it:** nothing calls `alembic upgrade head` at startup or in `deploy.sh`, so `create_all()` and the `ALTER TABLE` patcher in `db/session.py` are deliberately retained.
- ~~**The `review` table has no writer.**~~ **RESOLVED 2026-08-08 (ADR-029).** `POST`/`GET /api/v1/detections/{id}/review` exist and insert append-only rows, wired to confirm/reject in the detection drawer. **Still open:** `corrected_taxon_id` is always written `None`; correcting a misidentified taxon is deliberately deferred.
- **Concurrent WebSocket writers are a live-transport hazard invisible on loopback.** *Still true, and permanently so — this is a property of the medium, not a bug that was fixed.* See ADR-012; it is the strongest argument in this repository for measuring over the real link.
- ~~**`ultrasonic-pass-v1` runs 24 hours a day** with no night scheduler~~ **RESOLVED 2026-08-05.** `src/open_observatory/schedule.py` gates it to civil dusk–dawn plus configurable margins (`ultrasonic_schedule`, repository default `always`; the live station sets `night`). **The known false-positive rate on broadband transients is NOT resolved** — scheduling changes when the detector runs, not whether an individual pass is right, and resolving it needs a human listening to the audible renderings.
- ~~**No authentication.**~~ **RESOLVED 2026-08-08 (ADR-034), with a caveat that matters.** Argon2id passwords, session cookies and revocable API tokens exist, closing ADR-015 — but `auth_enabled` defaults to **false**, so a station that has not opted in is still exactly in the position `TECHNICAL_SPEC.md` §9 objects to. Even with it on, `GET /api/v1/detections`, `GET /api/v1/health` and `/metrics` stay credential-free by design, for the ESP32 display and `deploy.sh`.

### Opened later still, and still open

- **The deficit-step estimator over-credits lost audio**, by roughly 13× as measured on the live station on 2026-08-09. `capture.gap lost_audio=True` currently means "the read was late", not "recording was lost". See `docs/delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md` finding 2 and ADR-033.
- **~5833 BirdNET detections were persisted under the pre-ADR-032 plausibility logic** and no consumer hides a flagged one. `oo detections reconcile-plausibility` finds them but has never been run against the live station's own database. See `HANDOVER.md` §6.3 item 0.
- **No 72-hour soak has run**, so nothing here may be described as complete.
