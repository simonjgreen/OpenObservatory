---
aliases:
  - ADR-028
tags:
  - adr
---
# ADR-028: Operator/diagnostic disclosure implemented as one depth toggle, not a second route
**Decision:** [[ADR-016]] promoted the debug UI to be the product dashboard's foundation
rather than a surface to be replaced, and named the gap: no progressive disclosure existed
between "everything" and "nothing." This is implemented as a single `useViewMode` hook
holding one value, `'operate' | 'diagnose'`, synced to `?view=` via
`history.replaceState`. `operate` (the default) shows `OperatorSummary`'s four
plain-language cards, the spectrogram with its channel/window/overlay controls, the
species list, and `RetentionPanel`. `diagnose` additionally reveals `Header`'s raw stat
row, the spectrogram's tuning controls (palette/floor/ceiling/orientation) and level
meters, and the `CapturePanel`/`DetectorPanel`/`StoragePanel`/`EventLog` columns that used
to be the whole page.

**Reason:** A second route (e.g. `/diagnostics`) would duplicate the live WebSocket
connection, the spectrogram canvases and their registered sinks, and the detection list —
exactly the "two applications sharing a repository" [[ADR-016]] rejected. A single boolean held
above one component tree, gating what renders, keeps one connection, one set of canvases,
one detection list; only the JSX branches on depth.

**What "diagnostic" means here, concretely:** the header's continuity/gaps/block-age/
hot-path/link stats, spectrogram palette and black/white point tuning, the native/audible
level meters, and the four pipeline-internals panels. Everything else — the synthetic-audio
warning, the spectrogram itself, the species list, storage headroom, review — is
operator-facing in both depths, per [[ADR-016]]'s "promotion, not replacement."

**Constraint carried forward from [[ADR-011]]:** a diagnostic number is never the sole backing
for a product claim. `OperatorSummary`'s cards are computed independently in
`state/operatorHealth.ts` from the same `StationStatus` fields the diagnostics panels
read — not derived *from* the diagnostics panels' rendered output — so hiding diagnostics
never hides the reasoning behind a card's tone.

**Verified:** manually, in a real Chrome tab (`claude-in-chrome`) against a local station
running `oo serve --source synthetic`, at both a desktop and a 390×844 mobile viewport;
`?view=diagnose` survives a reload. Not verified: a second human operator's read of the
copy, or the same test over a real Wi-Fi link to the Pi (see the report's "not verified"
section).

---
Part of the [[ADRS|Architecture Decision Record index]].
