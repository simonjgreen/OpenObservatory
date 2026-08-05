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
| USB SSD strongly preferred for data | Single 235 GB SD card (`/dev/mmcblk0p2`), 222 GB free | Acceptable for the debug slice. Continuous native-rate archival stays off by default, as the spec already requires. |
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

5. **`detection-event.schema.json` requires `native_result`, but `additionalProperties: false` omits fields the data model needs** — notably `rank`, `taxonomic_group`, and any window/run identifiers. The schema is treated as the *published wire* contract; internal records carry more. **Still open after Milestone 3.** It was flagged for Milestone 3 and was not done: `schemas/detection-event.schema.json` still omits `rank` and `taxonomic_group` and still sets `additionalProperties: false`. It must be closed before anything external consumes the envelope — the MQTT publisher of Milestone 6 is the forcing function.

6. **`config/example.env` names `OO_AUDIO_DEVICE=/dev/snd/by-id/REPLACE_ME`.** `/dev/snd/by-id` does not exist on this target and only appears once a USB audio device is attached. Device selection is therefore by *stable device key* (a normalised USB vendor:product:serial, falling back to the ALSA card id string), resolved at open time — never by a card number. See §4.1 of the technical spec, which already forbids trusting `hw:1,0`.

## 3. Gaps the specification leaves open, and the decisions taken

| Gap | Decision for this phase |
|---|---|
| No spectrogram/live-audio transport is specified; the API doc only offers SSE for detections | WebSocket carrying binary spectrogram columns + JSON telemetry frames. **As built:** live listening became a *second* WebSocket (`/api/v1/live/audio`) rather than a chunked-WAV endpoint, so a stalled listener cannot block the visual channel. See ADR-012 and `docs/api/DEBUG_UI_TRANSPORT.md`. |
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

- **No fixture test proves a known species from a known recording.** Milestone 3's exit gate asks for one; identification is currently demonstrated only live. Needs a reference recording whose individual licence permits redistribution.
- **No Alembic migration environment exists.** `create_all()` builds the schema. See ADR-007.
- **The `review` table has no writer.** It is defined and unused; nothing reads or writes it until Milestone 4.
- **Concurrent WebSocket writers are a live-transport hazard invisible on loopback.** See ADR-012 — this was found in production behaviour, not by review, and it is the strongest argument in this repository for measuring over the real link.
- **`ultrasonic-pass-v1` runs 24 hours a day** with no night scheduler and a known false-positive rate on broadband transients. See ADR-013.
- **No authentication.** ADR-015 records the deviation from `TECHNICAL_SPEC.md` §9.
