# ADR-012: Two live channels, and exactly one writer per WebSocket
**Decision:** The live surface is two separate WebSockets — `/api/v1/live` carrying JSON
frames and binary spectrogram columns for the visual pipeline, and `/api/v1/live/audio`
carrying raw PCM for the listen channel. On each socket, exactly one task performs every
send; producers `offer()` into that task's bounded queue and never touch the socket.
`DEBUG_UI_TRANSPORT.md` holds the frame formats.

**Reason:** This is a correctness requirement, not tidiness. Concurrent writers to one
WebSocket silently destroyed spectrogram delivery — the channel froze after roughly one
frame while JSON kept flowing. Splitting audio from the visual channel keeps a slow or
absent listener from stalling the spectrogram, and the single-writer rule makes interleaved
sends structurally impossible rather than merely unlikely.

**Constraint:** The bug is invisible on loopback, where sends complete too fast to overlap.
Any change to these channels must be measured from a real browser over the real network
link before it is believed. Both queues are bounded and drop rather than block: capture
always wins.
