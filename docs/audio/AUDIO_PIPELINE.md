# Audio Pipeline Specification

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
