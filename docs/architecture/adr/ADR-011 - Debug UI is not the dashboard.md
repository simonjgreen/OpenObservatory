---
aliases:
  - ADR-011
tags:
  - adr
---
# ADR-011: The debug UI is an observability surface, not the product dashboard
**Status:** partly superseded by [[ADR-016 - Debug UI is the dashboard's foundation|ADR-016]].

**Decision:** The real-time debug UI reads only from the API and the live WebSocket, shows
raw pipeline state alongside detections, and is allowed to expose internals (frame counts,
monotonic offsets, queue depth, drop counters, detector lag) that the Milestone 4 product
dashboard would hide.

**Reason:** It exists to prove and diagnose Milestones 1–3 on real hardware. Keeping it
separate stops diagnostic affordances leaking into the product surface and stops product
polish being mistaken for pipeline correctness.

**See also:** [[ADR-012 - One writer per WebSocket|ADR-012]] for the transport it reads from. **Partly superseded by [[ADR-016 - Debug UI is the dashboard's foundation|ADR-016]]**,
which makes this UI the foundation of the Milestone 4 product dashboard rather than a
surface to be replaced by it. The separation of *concerns* below still holds; the
separation of *codebases* does not.

**Reviewed 2026-08-29:** the separation of *concerns* holds, and is now enforced in code
rather than asserted — [[ADR-028 - One depth toggle|ADR-028]]'s `useViewMode` depth toggle gates the internals
this ADR named: `CapturePanel`, `DetectorPanel`, `StoragePanel` and `EventLog` render only
when `view.depth === 'diagnose'` (`web/src/App.tsx:80,378`). The **"reads only" half has not
aged as well and is the sentence to distrust**: the same surface now writes as well as reads
— review verdicts and keep flags, settings, pause, firmware rollout, login, and the live
ultrasonic retune ([[ADR-022 - HTTP retune control|ADR-022]]) all POST/PUT/DELETE from it. "The live WebSocket" is two
things now: [[ADR-012 - One writer per WebSocket|ADR-012]] split the visual channel from audio, and [[ADR-019 - Chunked-WAV live playback|ADR-019]] moved this
UI's listen path onto a chunked-WAV `<audio>` stream — the `/api/v1/live/audio` socket
itself is unchanged and still serves other clients. The part that matters most is unchanged
— the UI still talks only to its own station: every request it makes is a relative path or
is built from `window.location` (`web/src/audio.ts:90,103`), so this surface never needs
the cloud.

---
Part of the [[ADRS|Architecture Decision Record index]].
