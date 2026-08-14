# Audio Pipeline Specification

This document is the pre-implementation design spec, written before the pipeline existed.
It records intent and is kept as-is below. See **"As implemented"** at the end for what was
actually built, where it matches this spec, and where it went further. Measured figures
(group delay, capture continuity, CPU) live in `docs/operations/TARGET_DIAGNOSTICS.md` and
are not restated here from memory.

## Objective

Capture once, preserve timing, and serve multiple detector-specific representations without reopening the hardware device.

## Pipeline stages

1. **Probe** — enumerate the AudioMoth and supported modes.
2. **Capture** — read fixed-duration blocks from ALSA with monotonic timestamps.
3. **Validate** — detect short reads, overruns, clipping, impossible timestamps and device reset.
4. **Buffer** — append native PCM to an in-memory ring.
5. **Derive** — resample native PCM to audible 48 kHz.
6. **Segment** — cut streams into model-requested windows.
7. **Lease** — publish a window reference with expiration and consumer count.
8. **Analyse** — detector processes the window.
9. **Retain** — clip manager extracts only evidence-required source ranges.
10. **Expire** — transient windows and leases are deleted.

## Recommended block size

Begin with 100 ms native blocks. This balances syscall overhead, gap granularity and evidence alignment. Make it configurable and benchmark 50/100/250 ms.

*As implemented: settled, not open.* `capture_block_ms` defaults to `100` in
`src/open_observatory/config.py` and is configurable but has not been revisited — see
"As implemented" below.

## Ring buffer implementation

Implement a single-writer, multiple-reader ring over chunk objects with:

- immutable PCM bytes or NumPy-compatible buffers;
- source frame offsets;
- monotonic timestamp range;
- reference counting for outstanding extraction;
- wrap/eviction metrics;
- no reader locks on the capture hot path where practical.

A first implementation may use a bounded deque plus indexed metadata. Do not prematurely write a custom lock-free native extension.

## High-rate arithmetic

At 384 kHz, 16-bit mono:

- 768,000 bytes/second;
- approximately 46 MB/minute;
- approximately 92 MB for a 120-second memory ring;
- approximately 66 GB/day if continuously persisted.

Therefore continuous native-rate disk archival is explicitly off by default.

## Resampling correctness

Tests must verify:

- output frame count within one frame of expected ratio;
- deterministic mapping from output timestamps to native source frames;
- no aliasing above audible Nyquist in a synthetic sweep test;
- no cumulative timestamp drift over one hour of generated PCM;
- continuity across capture block boundaries.

## Audio-level telemetry

For each second compute:

- RMS;
- peak;
- crest factor;
- clipped sample count;
- optional band energies for audible and ultrasonic bands;
- zero/constant signal warning.

These are operational measurements, not calibrated SPL unless a calibration procedure is later added.

## Replay mode

All downstream functionality must run against WAV fixtures through a replay source implementing the same capture-block contract. Replay modes:

- real-time;
- accelerated;
- deterministic step mode for tests.

This is mandatory for repeatable development without a live microphone.

## As implemented

The stages above were built substantially as specified, plus contracts this spec did not
anticipate. This section describes what exists in `src/open_observatory/audio/` and
`src/open_observatory/segmenter.py`; it does not replace the stage list above.

### Frame-based addressing, not timestamp addressing

`StreamClock` (`src/open_observatory/audio/contracts.py`) maps a stream frame index to
wall-clock and monotonic time, anchored once on the first block actually read
(`utc_ns_at_frame_zero`, `monotonic_ns_at_frame_zero`). Every later frame's time is
computed from that anchor and the sample rate, never from a running count of frames the
resampler has emitted.

This exists because the resampler's output arrives in ragged chunk sizes rather than a
constant frame count per input block, so a running-output-count clock would wander (bounded
but non-zero drift; see the resampler timing table in `TARGET_DIAGNOSTICS.md`). Anchoring
on stream start instead makes frame-to-time exact at every rate, including across a
dropped block, because a gap simply advances the frame index by the missing amount.

Consequence for this spec: derived audio (the resampled audible stream, spectrogram
columns) is timestamped from `StreamClock`, never from the native block's own measured
clock. The two are close but not identical, and the module docstring is explicit that
conflating them is the mistake this contract exists to prevent.

### Rate-substitution refusal

`src/open_observatory/audio/alsa_source.py` opens the device with `hw:` addressing only,
never through ALSA's `plug` layer. After opening, it reads back the negotiated rate and
compares it against what was requested; if they differ it closes the device and refuses
that rate rather than capturing:

> ALSA silently substituted a rate: refuse it rather than record audio whose true
> bandwidth we cannot state.

This was not a hypothetical: `TARGET_DIAGNOSTICS.md` records that `arecord -r 48000`
*appears* to succeed on the AudioMoth used for commissioning, because ALSA's `plug` layer
silently resamples and only prints a warning. The station's own capture path takes the
device's one native hardware profile (384 kHz mono S16_LE on the unit measured) or nothing.

### Window contracts and segmentation

`WindowSpec` and `AudioWindow` (`src/open_observatory/segmenter.py`) are the immutable,
time-addressed window contract this spec's "Segment" and "Lease" stages describe.
`StreamSegmenter` cuts one stream into windows for a single `WindowSpec`, keeping only a
rolling tail sized to the spec's overlap so memory is bounded by window length rather than
by how far behind a detector has fallen. `WindowRouter` owns one segmenter per distinct
`WindowSpec` on a stream kind, so two detectors requesting the same window shape share one
segmenter instead of duplicating the slicing work.

### Ultrasonic sub-windowing

`src/open_observatory/audio/spectrogram.py` computes the ultrasonic channel with an FFT
size of 4096 and a hop of 24 ms, which at the native 384 kHz rate is 9216 frames — more
than twice the FFT size. A single FFT window per output column therefore looked at 4096 of
each 9216-frame hop and ignored the remaining 5120: 55% of the audio was never inspected,
and a 4 ms bat pulse could land entirely in the unexamined gap. Columns are now built from
several sub-windows spread across the hop and max-combined, so no part of the hop is
skipped. `TARGET_DIAGNOSTICS.md` records the measured cost of this: per-block hot-path CPU
went from 9.5% to 10.9% of one core when full-hop coverage replaced single-window sampling.

### Ultrasound-to-audible rendering

`src/open_observatory/audio/ultrasound.py` renders ultrasonic detections into an audible
derivative for human review, by time-expansion (replay slowed by a factor, preserving call
shape) or heterodyne (mixed against a tuned local oscillator, real-time duration, narrowband).
This is not part of the stage list above; it exists because an ultrasonic clip is not
checkable by ear as recorded. See ADR-014 in `docs/architecture/ADRS.md`. The rendering is
peak-normalised and high-pass filtered and is explicitly not amplitude-comparable to the
native recording — levels throughout this system are uncalibrated dBFS, never SPL.

### Block size

100 ms (`capture_block_ms = 100` in `src/open_observatory/config.py`) is a measured,
settled default rather than the open benchmarking question this spec posed. Live-channel
delivery measured from a real browser over Wi-Fi shows inter-arrival at p50 100 ms (one
capture block), consistent with that setting; see `TARGET_DIAGNOSTICS.md`.

### What the measurements say about the stages above

Resampling correctness (native-rate to 48 kHz): group delay is 0 output frames, measured —
output frame *n* maps exactly to native frame *8n* — with no cumulative drift over 5
minutes of audio. Capture continuity, measured on the running systemd service, is
0.9990–0.9997. Both figures, and the device's measured −43 ppm clock offset, are recorded
in `docs/operations/TARGET_DIAGNOSTICS.md` and are not restated in full here.

An earlier version of this paragraph added "with zero gaps and overruns". That is no
longer true and should not be restated: the live station reported 369 `capture.gap`
records and 3 ALSA overruns over 12.4 hours on 2026-08-09, at continuity 0.999907. Almost
none of that was lost audio at the time — the real frame deficit over the same period was
4.15 s (0.0095%) against an `estimated_missing_seconds` of 54.5 — because the pre-fix
deficit-step estimator credited a *late* read as lost audio. See
`docs/delivery/OPEN_INVESTIGATION_CAPTURE_GAPS.md` and ADR-033.

That estimator was corrected on 2026-08-09 (**ADR-039**): a deficit step is now
credited only once it fails to come back, `reason=overrun` is reserved for an event
ALSA actually reported, and a late read that cost nothing increments `late_reads`
instead of minting a gap. The fix was deployed and confirmed on target 2026-08-09.
Since then, **judge real loss by `estimated_missing_seconds`, never by the raw
`expected_frames - frames` deficit** — the raw deficit also carries crystal drift
and a ±50 ms block-sampling phase artefact, and conflating the two is exactly the
mistake ADR-039 fixed. If an earlier reading of this document told you to prefer
the raw deficit, it predates ADR-046, which is the record of this reversal.

Note also that the one-hour no-drift test this document's "Resampling correctness" list
asks for has been run at **five minutes**, not one hour, and the 72-hour soak ran
2026-08-10 to 2026-08-13 and **failed** its continuity criterion (99.865% against
≥ 99.9%; see `docs/delivery/MILESTONE_STATUS.md` §Milestone 4.5). Neither gap is closed.
