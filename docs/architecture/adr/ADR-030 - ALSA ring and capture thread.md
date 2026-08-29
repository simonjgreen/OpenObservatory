---
aliases:
  - ADR-030
tags:
  - adr
---
# ADR-030: The ALSA ring is sized for scheduling jitter, and the capture read owns its own thread
**Status:** active; [[ADR-039 - Confirmed loss, not deficit|ADR-039]] and [[ADR-046 - Deficit is mostly drift|ADR-046]] amend the gap estimator and the
display behind the 2026-08-08 figures cited below, and [[ADR-033 - Retention is paced|ADR-033]] qualifies the
thread argument — an executor partitions queueing, not the GIL.
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
2026-08-05 for exactly this reason ([[ADR-021 - Clips on their own device|ADR-021]]); this extends the same rule to the reader
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

**Reviewed 2026-08-29:** the decision holds unchanged. `capture_buffer_ms` still
defaults to 500.0 (`src/open_observatory/config.py:96`), the read still runs on
`AlsaSource`'s own single-thread `oo-capture` executor
(`src/open_observatory/audio/alsa_source.py:126`), and the negotiated ring on the
station is the one recorded in [[TARGET_DIAGNOSTICS]]. The ring-deeper-than-two-blocks
rule became an enforced site-settings validation when capture settings became
web-editable ([[ADR-048 - Web-configurable settings|ADR-048]]), so an operator can no longer set the shallow ring back.
The "take it then, with a measurement" clause above has since had its measurements, and
they point in opposite directions: one four-hour evening window where the ring
overflowed repeatedly and late-read headroom reached its worst recorded figure
([[OPEN_INVESTIGATION_CAPTURE_GAPS]]), against the 72-hour soak, where a single
overflow cost well under a second across the whole window and headroom peaked at about
half the ring ([[SOAK_2026-08-22]]). The free-running reader thread has still not been
built.

---
Part of the [[ADRS|Architecture Decision Record index]].
