# Handover: state, decisions, and what to do next

Written at the end of the first implementation session, 2026-08-04, against the
live station at `station.example`. Read `MILESTONE_STATUS.md` for progress against
the plan; this file is the operational and engineering context a successor needs.

---

## 1. Where things stand in one paragraph

The station captures live 384 kHz mono audio from an AudioMoth on a Pi 5, derives a
48 kHz audible stream with verified zero group delay, cuts immutable time-addressed
windows to three detectors' own specifications, normalises and persists detections,
writes checksummed evidence clips (including audible renderings of ultrasound), and
serves a real-time debug UI over two WebSocket channels — in scrolling or waterfall
orientation, with unidentified events hidden by default — plus a history mode for
browsing what was persisted. 161 Python tests and 38 frontend tests pass on the
target; ruff is clean. It runs as a systemd unit and survives reboots. It is **not
complete**: no 72-hour soak, no authentication, no product dashboard, and one
Milestone 3 exit gate only partially met.

## 2. How to operate it

```bash
# From a workstation with the repo checked out:
HOST=station.example ./deploy/deploy.sh          # build UI, sync, install unit, restart
./deploy/deploy.sh --no-web --no-deps          # fast code-only redeploy

# On the Pi:
sudo systemctl status open-observatory
sudo journalctl -u open-observatory -f
cd ~/open-observatory && .venv/bin/oo audio probe
.venv/bin/python -m pytest -q
```

UI: `http://station.example:8080`. API: `/api/v1/…`. Metrics: `/metrics`.

**`config/runtime.env` on the Pi is not in version control** and holds the station
name, coordinates and any device override. `deploy.sh` excludes it — it must stay
excluded, because `rsync --delete` deleted it once already.

## 3. The ten bugs found by measuring, and why they matter

Every one of these looked fine until a number was checked. They are listed because
the *method* is the transferable part: measure the property, do not assume it.

| Bug | How it presented | How it was found |
|---|---|---|
| Gap estimator anchored on `open()` | A phantom 0.21 s gap reported on **every** block | The reported "missing frames" was constant, not growing — a fixed bias, not lost audio |
| Clock offset conflated with lost frames | One overrun reported forever as −1439 ppm | 8 s CLI test showed 0.2 ppm; the number had to be wrong |
| Derived-stream timestamps from a running output count | Up to 19 ms of wander | libsoxr emits 4884/4070-frame chunks, not 4800; measured the deficit band over 10 min |
| Activity detector threshold below the noise floor | Fired on 100% of windows | Measured the statistic on pure noise: median 8.5 dB, max 11.9 dB, against a 7 dB threshold |
| Clipping every event | 890 MB in 2 minutes ≈ 640 GB/day | `du` on the clip directory |
| Window leases leaked | Lease count grew without bound | `lease.expired` warnings; releases only happened on the detection path |
| **Concurrent WebSocket writers** | **Spectrogram froze after ~1 frame while JSON kept flowing** | Ran the measurement *in the browser over Wi-Fi*; loopback had looked perfect |
| Ultrasonic hop wider than its FFT | 55% of the audio never inspected; 4% too many columns | Column timeline showed overlapping timestamps and more audio than wall time |
| `/` in SQLAlchemy 2 is *true* division | History buckets never truncated: 1899 ten-minute buckets in a twelve hour window | The bucket count was arithmetically impossible; raw SQL was fine, the ORM expression was not |
| Streams left unclosed by killed processes | Capture coverage of a night reported as 1302% | A fraction above 1 cannot be true |

Two more, cosmetic but misleading: `/metrics` and `/api/v1/detectors` both returned
500 (caught by the new integration tests), and derivative clip durations were
computed from native frame bounds divided by the derivative's own rate, reporting a
4.6 s clip's playback copy as 36.5 s.

### The one that matters most for future work

**Loopback is not a test of a network path.** The concurrent-writer bug produced a
perfect result from a probe on the Pi (100 ms cadence, zero gaps, zero malformed)
and near-total failure in the actual browser, because on loopback sends complete too
fast to ever overlap. Any future work on the live channels must be measured from a
real client over the real link.

## 4. Architecture decisions a successor must not accidentally undo

- **One process owns the microphone.** Everything else consumes windows. Do not add
  a second ALSA opener.
- **Frames, not timestamps, address audio.** `StreamClock` maps frame → time,
  anchored on the first block actually read. Never timestamp derived audio with the
  native block's clock.
- **Never trust an ALSA card index.** Devices resolve by `stable_device_key`
  (USB vendor:product:serial). The AudioMoth moved from card 2 to card 0 across a
  reboot during commissioning.
- **Refuse rate substitution.** `arecord -r 48000` appears to work on this device
  but silently resamples. Capture rejects any rate the device substitutes.
- **One writer per WebSocket.** See §3.
- **Capture always wins.** Every queue between capture and a consumer is bounded
  with an explicit drop policy, and drops are counted and surfaced.
- **Honesty rules are enforced in code**, not just documented: a non-taxonomic
  detector cannot emit a species name (the normaliser raises), an uncalibrated
  detector cannot report a probability, and a synthetic source is announced loudly
  everywhere including `/api/v1/health`.

Deviations from the seed spec are ADR-007 to ADR-015 in
`docs/architecture/ADRS.md`: SQLite in developer mode, native systemd instead of
Compose, in-process event bus instead of Redis Streams, an owned activity detector
as the first plugin, the debug UI as an observability surface rather than the
product dashboard, the two-channel live transport and its single-writer rule, the
ultrasonic pass detector as a second owned plugin, the audible rendering of
ultrasonic evidence, and — the one with a real consequence — running with anonymous
read access and no authentication until Milestone 4.

## 5. Known-good measured figures (regressions should be judged against these)

| Property | Value |
|---|---|
| Capture continuity | 0.9990–0.9997 |
| Gaps / overruns in normal running | 0 |
| Device clock offset | −43 ppm (a real crystal property, not an error) |
| Per-block hot-path CPU | 10.9% of one core (was 9.5% before ultrasonic sub-windowing) |
| Whole-process CPU | ~29% of 4 cores with all three detectors |
| Resampler group delay | 0 frames |
| Resampler delivery deficit | bounded 112–924 frames, no trend over 5 min |
| Live spectrogram delivery | 1246 columns / 29.9 s audio per 30 s wall, 0 gaps, 0 overlaps |
| Live audio latency | ~180 ms end to end (139 ms buffer + 42 ms device) |
| BirdNET | p95 77–109 ms, ~40× realtime, 6522 labels |
| Ultrasonic detector | p95 54–104 ms, ~36–40× realtime |
| Activity detector | p95 13–16 ms, ~95× realtime, fires on ~9% of windows |
| BirdNET plausibility | week 29, 139 species plausible at the development station |

## 6. Immediate next steps, in the order I would do them

### 6.1 Close the Milestone 1–3 gates properly

1. **Run the 72-hour soak.** This is the single biggest outstanding item and the
   acceptance criteria require it before the word "complete" may be used. Watch
   `oo_capture_continuity_ratio`, `oo_ring_extraction_misses_total`,
   `oo_detector_windows_dropped_total`, RSS, and the clip budget.
2. **Commit a fixture test that proves a known species from a known recording.**
   Milestone 3's exit gate asks for this and it is currently only demonstrated
   live. Needs a freely-licensable reference recording (Xeno-canto CC0/BY are
   candidates — check each recording's own licence, they vary).
3. **Run the one-hour drift test at full duration.** Currently verified at 5 min.

### 6.2 History browsing, and what it still lacks

`HISTORY` mode reads persisted detections, aggregates them in SQL, and shows capture
coverage beside them. What it deliberately does **not** offer is a historical
*spectrogram*: the ring buffer is memory-only by design, and the audio pipeline spec
rules out continuous native-rate archival (66 GB/day at 384 kHz). If a day-view
spectrogram is wanted, the honest way is to persist the uint8 spectrogram columns —
about 40 MB/day for both channels — rather than the audio. That would be a genuinely
useful addition and is not currently planned.

### 6.3 Fix the things I know are wrong or unfinished

4. **Reduce the AudioMoth gain.** The input clips on loud nearby events. This needs
   the AudioMoth USB Microphone app with the switch in `USB/OFF`; the HID
   app-packet format for writing configuration is not implemented here. Either
   implement it (see `AudioMoth-USB-Microphone-App` for the packet layout) or do it
   by hand and record the new setting in `TARGET_DIAGNOSTICS.md`.
5. **Add a night scheduler.** The ultrasonic detector currently runs 24 hours a
   day. The technical spec wants civil dusk to civil dawn plus a margin, and the
   `solar.py` approach from the earlier OutdoorAcousticEvents prototype is a
   reasonable starting point.
6. **Review the ultrasonic detector's false-positive rate.** On a windy or
   handling-noisy evening it reports "bat pass" for broadband transients. Detections
   at 18–21 kHz are ambiguous between noctule and bush-cricket. The audible
   renderings now make these checkable by ear; use them to tune
   `min_snr_db`, `min_pulses_per_pass` and the band.
7. **`oo audio window-dump`.** Milestone 2 asked for a window inspection CLI and
   only the resampler check exists.
8. **Cover the history endpoints at the HTTP level.** `tests/test_history.py` tests
   the aggregation functions; nothing exercises `/api/v1/history` or
   `/api/v1/history/windows` through the app, which is where the true-division
   bucket bug would have shown itself.
9. **Close the event-envelope schema gap.** `schemas/detection-event.schema.json`
   sets `additionalProperties: false` and omits `rank` and `taxonomic_group`, which
   internal records carry. It was flagged for Milestone 3 and not done. The MQTT
   publisher of Milestone 6 is the forcing function, because that is the point at
   which something outside this repository starts depending on the shape.
10. **Write the Alembic migration environment before, not during, the PostgreSQL
    move.** `alembic` is a declared dependency with no `alembic/` directory;
    `create_all()` is what actually builds the schema. ADR-007 now records this.

### 6.4 Then the plan's own next milestones

11. **Milestone 4**: product dashboard, review workflow, retention UI, and the
   authentication foundation. ADR-015 records the deferred authentication as a
   deviation with a real security consequence; this milestone is what closes it. Note the `review` table exists and nothing writes to
   it. Keep the debug UI separate (ADR-011).
9. **Milestone 5 proper**: BatDetect2 evaluation and benchmark on this Pi. The
   window contract and native stream are already in place, so this is an adapter
   plus a decision about whether real-time inference is sustainable.
10. **Milestone 6**: MQTT publisher and Home Assistant discovery. Cheap, because
    the event envelope is already the published one — a publisher subscribing to
    the existing bus needs no contract changes.

### 6.5 Longer-term, and worth deciding early

- **PostgreSQL migration.** ADR-007 keeps SQLite for the debug slice. Anything
  needing concurrent writers, `LISTEN/NOTIFY` or JSON indexing must wait for this.
  The DSN is the only change; Alembic migrations need writing (none exist yet —
  `create_all()` is used, which is fine for SQLite but not a migration path).
- **Redis Streams.** ADR-009's `EventBus` protocol is the seam. The bounded queues
  and drop counters already model the back-pressure a real transport imposes.
- **USB SSD.** Currently running on the SD card with a 20 GB clip budget. A
  continuous archive is explicitly out of scope, but a soak test plus retained
  evidence will grow.

## 7. Traps worth knowing about

- **`pkill -f "oo serve"` from an ssh command matches its own command line** and
  kills the ssh session, not the service. Use `systemctl`.
- **`rsync --delete` will remove anything gitignored from the target.** Already
  bitten once (`config/runtime.env`).
- **The AudioMoth's switch position changes its USB identity.** `USB/OFF` gives HID
  and no ALSA card at all; `DEFAULT` gives USB Audio. A device that seems missing is
  usually in the wrong position, not broken.
- **A weak PSU limits USB current.** An intermittently-enumerating microphone is a
  plausible symptom; one was replaced during commissioning.
- **`ai-edge-litert`, not `tflite-runtime`.** The latter has no cp312 aarch64 wheel
  and needs NumPy 1.x.
- **Test helpers can mask a fix.** The activity detector's recalibration appeared not
  to work because the test helper passed the old threshold explicitly.
- **`/` on an Integer column in SQLAlchemy 2 is true division**, not integer
  division: it casts to NUMERIC. Use `x - (x % n)` to truncate, which also needs no
  `FLOOR` and behaves the same on SQLite and PostgreSQL.
- **A stream row is only closed on graceful shutdown.** `Station.start` now closes
  any left open by a previous process; without that, history treats them as still
  running and coverage exceeds 100%.
- **The default thread pool is shared with the ALSA read.** `alsa_source.read`
  uses `asyncio.to_thread`, so anything else using `to_thread` — clip writing,
  retention sweeps, database inserts — competes for the same 8 worker threads on a
  4-core Pi. Heavy clip writing delayed the capture read enough to overrun the ALSA
  ring: 11 gaps and 8 overruns in five minutes. Evidence extraction and retention
  now have their own single-thread executor. Anything else that does sustained disk
  I/O needs the same treatment.
- **A bottleneck can be load-bearing.** The ultrasonic detector was stalling on
  inline clip writes and dropping 70% of its windows, which was *also* throttling
  clip production. Fixing the stall tripled evidence volume and exposed an SD-card
  I/O limit that had never been reached. Expect the next constraint to appear when
  you remove one.
- **Clip volume on a busy bat night is the binding storage constraint.** Roughly
  15 MB per pass across four clips, 15 GB in one night, against a 20 GB budget. The
  station now renders heterodyne only rather than heterodyne plus time expansion
  (`OO_ULTRASONIC_AUDIBLE_METHOD=heterodyne`) and caps `OO_CLIP_MAX_PER_MINUTE=6`.
  Both are in `runtime.env` and reversible. The durable fix is the USB SSD.
- **`pytest` escalates `DeprecationWarning` to an error** by configuration. That is
  deliberate — it is what forced the FastAPI lifespan migration — but it means a
  dependency deprecation will fail the suite.

## 8. What must not be claimed

Per `CLAUDE.md`, this system is not complete and must not be described as such
until the acceptance criteria pass a 72-hour soak. Specifically avoid claiming:

- that any detector "identifies" anything the detector itself does not claim —
  `ultrasonic-pass-v1` detects passes, not species;
- that scores are probabilities, unless the detector declares calibration;
- that levels are sound pressure levels — no calibration procedure exists;
- that bat support is complete — there is no classifier and no night scheduler.
