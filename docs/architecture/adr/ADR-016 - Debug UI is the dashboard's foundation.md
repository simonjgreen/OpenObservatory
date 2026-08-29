---
aliases:
  - ADR-016
tags:
  - adr
---
# ADR-016: The debug UI is the foundation of the product dashboard, not a throwaway
**Status:** active; supersedes part of [[ADR-011 - Debug UI is not the dashboard|ADR-011]]. Its measurements of the *starting*
state (`styles.css` line count, no component test library, `App.tsx` size) are historical —
all three were addressed in Milestone 4, and re-measured on 2026-08-29 in the status
blockquote below.

**Decision:** Milestone 4 promotes the existing UI rather than starting a second one.
Operator and diagnostic views become progressive disclosure within one application —
one component library, one design-token stylesheet, one transport client, one type set —
not two applications sharing a repository.

**Reason:** [[ADR-011 - Debug UI is not the dashboard|ADR-011]] was written before the UI existed, when the risk was that debug
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
depths, which is what [[ADR-011 - Debug UI is not the dashboard|ADR-011]] forbade. The plan's gate wins.

**Constraint — what [[ADR-011 - Debug UI is not the dashboard|ADR-011]] got right and is retained:** a diagnostic number must never
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
> survives a refresh ([[ADR-028 - One depth toggle|ADR-028]]); and `styles.css` gained spacing and type scales
> ([[ADR-027 - Spacing and type scale|ADR-027]] — which also records that ~700 lines of older component CSS were
> deliberately *not* migrated, so that part of the assessment still stands). The
> line counts and hook counts quoted above are kept as the measurement that
> justified the decision, not as a description of the code today.
>
> **Re-measured 2026-08-29, against the working tree.** The 2026-08-09 figures
> above are themselves now historical. `App.tsx` is 417 lines, but holds only
> **two** direct `useState` calls (`selected`, `showSettings`) and composes nine
> extracted hooks: it grew back towards its original length because product
> surfaces were added — first-run, login, pause, retention, settings, firmware,
> near-miss — not because state crept back into it, so the Prerequisite's
> concern is not re-emerging. `vitest run` reports **300 tests passing across 25
> files**. `styles.css` is 1394 lines with 39 custom properties, among them
> `--space-1`..`--space-8`, `--text-2xs`..`--text-2xl` and three line-height
> tokens.
>
> **Two corrections to the 2026-08-08 note.** Its figures do not pair: 323 lines
> and the 15-file test count both belong to commit `de385b1`, dated 2026-08-08,
> where `App.tsx` held **one** direct `useState` call — the "3" is what
> `grep -c useState` returns there, counting the import line and a comment. By
> `15c9193`, the 22-file commit late on 2026-08-09, the file was already 403
> lines with three calls, so the regrowth measured today is 403 → 417, not
> 323 → 417. Second, the URL carries the depth and nothing else: `?view=` is the
> only parameter synced (`web/src/hooks/useViewMode.ts:29`), so a refresh still
> loses LIVE/HISTORY mode, the chosen history window and the open detection.
> Every other claim in that note holds.

---
Part of the [[ADRS|Architecture Decision Record index]].
