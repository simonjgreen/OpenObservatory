---
aliases:
  - ADR-016
tags:
  - adr
---
# ADR-016: The debug UI is the foundation of the product dashboard, not a throwaway
**Decision:** Milestone 4 promotes the existing UI rather than starting a second one.
Operator and diagnostic views become progressive disclosure within one application —
one component library, one design-token stylesheet, one transport client, one type set —
not two applications sharing a repository.

**Reason:** [[ADR-011]] was written before the UI existed, when the risk was that debug
affordances would set the product's direction. The opposite happened: the surface that
got built is largely product-shaped. HISTORY mode already answers "what visited last
night" over persisted data, with named windows resolved in the station's timezone and
DST-aware, a stacked timeline over real aggregation SQL, species summaries with counts
and first/last-seen, focus-by-click, and capture coverage computed with correct interval
merging so an empty stretch is distinguishable from a quiet one. `Suggestions` is already
a species-grouped list a person can read. Discarding that to rebuild it would be waste,
and the second implementation would not be better — it would be untested.

**What is honestly *not* foundation, measured rather than assumed:**

- `styles.css` is 692 lines with 18 custom properties covering colour, radius and font
  stacks. There is no spacing or typographic scale, and the bulk is component-specific
  selectors with hardcoded pixel values and off-token hex colours. It is a colour-token
  header over ad-hoc CSS. A product surface inherits the dark, dense, monospace debug
  aesthetic unless it is restyled, and restyling is real work.
- `geometry.ts` is pure and well tested, but it is spectrogram-canvas mathematics. A
  timeline, review queue or retention chart shares its discipline, not its code.
- `types.ts` has a split personality: `Detection`, `MediaRef` and `Envelope` are near
  product shape; `StationStatus` is almost entirely pipeline internals.
- `audio.ts` and `live.ts` are genuinely surface-agnostic and reusable as they stand,
  though `AudioTelemetry` bakes debug counters into its public interface.
- The frontend has **no component testing library installed**. `vitest` is present but
  only pure functions can be tested, which is why `geometry.test.ts` is the only test.
  Everything with behaviour — mode switching, WebSocket wiring, history focus logic,
  jitter-buffer resync — is untested.
- There is no router and no URL-driven state, so a refresh loses the view.

Milestone 4's own exit gate in [[IMPLEMENTATION_PLAN]] asks that a user can "operate
**and diagnose** the station entirely through the local UI". That is one surface with two
depths, which is what [[ADR-011]] forbade. The plan's gate wins.

**Constraint — what [[ADR-011]] got right and is retained:** a diagnostic number must never
be mistaken for a product claim. Queue depths, drop counters, frame offsets, resampler
deficits and detector lag stay behind an explicit diagnostics disclosure. Product
surfaces must not present uncalibrated levels as measurements, model scores as
probabilities, or a frequency band as a species identification. Polish on the operator
surface is never evidence that the pipeline is correct; only the measurements are.

**Constraint — the debug affordances are not deleted.** They are how the pipeline was
proven and how the next regression will be found. Promotion means reorganising them
behind disclosure, not removing them.

**Prerequisite:** `web/src/App.tsx` currently holds around twenty-five `useState` hooks
covering live transport, history fetching, spectrogram controls, audio monitoring and
mode switching in one 425-line component. A third surface cannot be added to that
cleanly. State extraction is the first task of Milestone 4, not an optional tidy-up.

> **Status 2026-08-08: the "not foundation" list above is historical, and all of
> it was addressed.** Measured on 2026-08-09: `App.tsx` is 323 lines with 3
> `useState` hooks, decomposed into `web/src/hooks/*` and `web/src/state/*`;
> `@testing-library/react` is installed and there are **235 frontend tests across
> 22 files** — re-measured on merged `main` late on 2026-08-09; it read 140 across
> 15 files earlier the same day — not one; `?view=operate|diagnose` gives URL-driven state that
> survives a refresh ([[ADR-028]]); and `styles.css` gained spacing and type scales
> ([[ADR-027]] — which also records that ~700 lines of older component CSS were
> deliberately *not* migrated, so that part of the assessment still stands). The
> line counts and hook counts quoted above are kept as the measurement that
> justified the decision, not as a description of the code today.

---
Part of the [[ADRS|Architecture Decision Record index]].
