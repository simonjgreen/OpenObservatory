---
aliases:
  - ADR-012
tags:
  - adr
---
# ADR-012: Two live channels, and exactly one writer per WebSocket
**Status:** active; the single-writer rule is unchanged and now governs every
WebSocket on the station. "Two channels" has since grown: [[ADR-019 - Chunked-WAV live playback|ADR-019]] added a
chunked-WAV HTTP listen path, now the debug UI's default, and [[ADR-022 - HTTP retune control|ADR-022]] its HTTP
retune control; `/api/v1/live/audio` is unchanged and still serves other clients.
[[ADR-038 - Display push channel|ADR-038]] added a third WebSocket, `/api/v1/display`, which obeys this rule
rather than reusing `/api/v1/live`.

**Decision:** The live surface is two separate WebSockets — `/api/v1/live` carrying JSON
frames and binary spectrogram columns for the visual pipeline, and `/api/v1/live/audio`
carrying raw PCM for the listen channel. On each socket, exactly one task performs every
send; producers `offer()` into that task's bounded queue and never touch the socket.
[[DEBUG_UI_TRANSPORT]] holds the frame formats.

**Reason:** This is a correctness requirement, not tidiness. Concurrent writers to one
WebSocket silently destroyed spectrogram delivery — the channel froze after roughly one
frame while JSON kept flowing. Splitting audio from the visual channel keeps a slow or
absent listener from stalling the spectrogram, and the single-writer rule makes interleaved
sends structurally impossible rather than merely unlikely.

**Constraint:** The bug is invisible on loopback, where sends complete too fast to overlap.
Any change to these channels must be measured from a real browser over the real network
link before it is believed. Both queues are bounded and drop rather than block: capture
always wins.

---
Part of the [[ADRS|Architecture Decision Record index]].
