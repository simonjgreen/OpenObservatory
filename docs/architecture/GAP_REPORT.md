# Gap, contradiction and unresolved-assumption report

Produced before implementation, as required by `CLAUDE.md`. Status date: 2026-08-04.
Target device: `pi2` at `station.example`.

## 1. Hardware findings that contradict the seed assumptions

| Seed assumption | Actual target state | Consequence |
|---|---|---|
| Raspberry Pi 5 running 64-bit **Raspberry Pi OS** | **Ubuntu 24.04.3 LTS** aarch64, kernel `6.8.0-1060-raspi` | No `raspi-config`, different package names, no Pi-OS-specific ALSA config. Python 3.12.3 is the system interpreter, which matches the required stack. |
| AudioMoth USB Microphone attached | **No USB device present.** `lsusb` lists root hubs only; `/proc/asound/cards` lists only `vc4hdmi0`/`vc4hdmi1`; `dmesg` shows no hot-plug event since boot | Live capture cannot be validated. Everything downstream is developed and proven against the mandated replay source until the device appears. `oo audio probe` is written to work with zero, one or many capture devices. |
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

5. **`detection-event.schema.json` requires `native_result`, but `additionalProperties: false` omits fields the data model needs** — notably `rank`, `taxonomic_group`, and any window/run identifiers. The schema is treated as the *published wire* contract; internal records carry more. No change made to the schema in this phase; flagged for Milestone 3.

6. **`config/example.env` names `OO_AUDIO_DEVICE=/dev/snd/by-id/REPLACE_ME`.** `/dev/snd/by-id` does not exist on this target and only appears once a USB audio device is attached. Device selection is therefore by *stable device key* (a normalised USB vendor:product:serial, falling back to the ALSA card id string), resolved at open time — never by a card number. See §4.1 of the technical spec, which already forbids trusting `hw:1,0`.

## 3. Gaps the specification leaves open, and the decisions taken

| Gap | Decision for this phase |
|---|---|
| No spectrogram/live-audio transport is specified; the API doc only offers SSE for detections | WebSocket carrying binary spectrogram columns + JSON telemetry frames, plus a chunked-WAV HTTP endpoint for live listening. Documented in `docs/api/DEBUG_UI_TRANSPORT.md`. |
| Capture block size for the *audible* derived stream is unspecified | 100 ms native blocks (spec §Recommended block size) produce 100 ms audible blocks; spectrogram hop is decoupled from block size. |
| Resampler library not chosen | `soxr` if a wheel is available on aarch64/cp312, else `scipy.signal.resample_poly`. Both are polyphase; the adapter records which was used so measurements stay attributable. |
| Clock correlation method unspecified | Single `time.clock_gettime(CLOCK_MONOTONIC)` / `time.time_ns()` pair sampled per capture block, plus a correlation record per stream. Monotonic is authoritative for ordering and duration; UTC is derived. |
| No definition of "event" for the debug UI | Four event families on one bus: `capture.*`, `window.*`, `detection.*`, `health.*`, all sharing the API doc's envelope. |
| Timezone/location for the station | `Europe/London`, coordinates unset until the operator supplies them. |

## 4. Unresolved and deliberately unclaimed

- AudioMoth negotiated rate, format, channel count and gain behaviour. **Unknown.** Will be recorded by `oo audio probe` into `docs/operations/TARGET_DIAGNOSTICS.md` once the device is attached.
- Whether 384 kHz capture is sustainable on this Pi/kernel/SD-card combination. Unmeasured.
- BirdNET on aarch64/cp312: TFLite runtime availability is unverified at the time of writing and is treated as a risk, not a promise.
- BatDetect2: out of scope for this phase.
