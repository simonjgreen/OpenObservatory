---
aliases:
  - ADR-058
tags:
  - adr
---
# ADR-058: The spectrogram's detection labels are placed, not drawn where they fall
**Status:** active. Frontend only: one new pure module, one draw-loop change.

**The bug, as the operator saw it.** On his phone, the live spectrogram's overlay
read:

> **Eurasia Eurasian Jackdaw 95%**

Two Eurasian Jackdaw detections a couple of seconds apart, each label drawn at
its own box's left edge, the second painted over the first. The visible string is
not a species, not a score and not a claim the station holds — it is two claims
sheared together. That is the honesty constraint, not a cosmetic defect: *a
number or name shown to a human must mean what its label says.* It is also not an
edge case. At 390 px the plot is a few hundred pixels wide, a label is ~140 px of
it, and one woodpigeon calling twice is enough.

**Why the tests did not catch it.** jsdom has no canvas: `Spectrogram.test.tsx`
renders the overlay against a `getContext` proxy that records nothing and returns
`{ width: 10 }` for every measurement. Every assertion about this overlay that
could ever have been written in that environment would have passed. The fix is
therefore split so that the part that can be wrong is testable without a canvas.

**Decision.** Labels are laid out over the whole set before any of them is drawn,
by a pure function (`web/src/components/overlayLabels.ts`), under three rules in
priority order:

1. **Never two labels overlapping.** A placement that would intersect one already
   made is not made.
2. **Never a truncated species name.** No ellipsis and no clipping. A name cut
   short reads as a different, real bird — "Great Spotted Woodpecker" clipped to
   "Great" is a word the reader finishes themselves, wrongly. The *score* and the
   *count* may be shed to make a name fit, because each is a separately labelled
   fact whose absence claims nothing; the name may not be shortened.
3. **Dropping a label is honest; overlapping two is not.** When neither of the
   above can be satisfied the label is not drawn. The box stays, so the detection
   is still visible and still countable — it has simply not been named in a place
   where naming it would lie.

Before any of that, the common case is collapsed rather than fought: a run of the
same title close together in time becomes one label with a count —
`Eurasian Jackdaw ×3 · best 95%` — which is both what actually happened (one bird
calling repeatedly) and the form the operator already reads in the suggestions
list.

Four details are load-bearing:

- **The merge key is the rendered title, not a taxon id.** Two detections merge
  only if their words would have been identical anyway, so a withdrawn claim
  ([[ADR-044]] renders it `… · withdrawn`) can never be counted into a standing one,
  and a 45 kHz bat pass never into a 55 kHz one. If the key matches, the merged
  label is literally correct for every member.
- **A run's score is labelled `best`.** `Eurasian Jackdaw ×3 95%` would be a
  number that does not mean what its label says: the run holds a 95%, a 91% and a
  60%. Naming which one it is costs six characters.
- **A run collapses when its labels would collide, and not before.** The
  threshold is the label's own extent along the time axis, not a fixed pixel gap.
  Two jackdaws two seconds apart are 30 px apart on the phone, where the count is
  the only honest option, and 120 px apart on a 1440 px desktop — where, if the
  name is short enough, both fit and both are shown. Same audio, two pictures,
  both true; a count on a desktop with room for the detail would be hiding it for
  nothing.
- **Labels move only along the frequency axis** — y in `scroll`, x in
  `waterfall` — never along time. Displacement across frequency costs nothing
  because a label was never a claim about frequency (its box is). Moving one
  along time would put a name at a moment nothing happened, which is the same
  class of error this ADR exists to prevent.

**Rejected: eliding text to fit.** It is the obvious answer and it is the wrong
one here. The failure mode being fixed *is* a partial species name; `Northern
Rough-winged Swal…` is a smaller version of the same lie, and on a narrow plot it
would be the normal rendering rather than the exception.

**Rejected: dropping every label below a width threshold and relying on tap.**
The station's normal state is nobody at a browser and, when there is somebody,
often a phone in a garden. A picture that names nothing at the width it is most
often read at fails charter item 8 without buying any honesty that rule 3 does
not already buy in the cases that actually need it.

**Charter item 8 pays nothing.** No polling, no `ResizeObserver`, no `matchMedia`,
no new React state, and nothing added to the transport: the layout runs inside the
overlay's existing `requestAnimationFrame` loop, from the detections it already
had through a ref. Its working set — the runs, the ordering, the placements and
the inputs — is pooled in module-level and ref-held arrays and reused frame to
frame rather than reallocated sixty times a second, and text widths are cached by
string, which *removes* a `measureText` per detection per frame that the old code
paid. The placement search is O(n²) over at most a dozen labels with a bounded
lane count. Ordering is an insertion sort over indices rather than
`Array.prototype.sort`, whose comparator would be a fresh closure each frame.

**Verified by rendering, because unit tests structurally cannot see this.**

- `web/src/components/overlayLabels.test.ts`: 36 assertions, each run for both
  orientations — the reported two-jackdaw case, a name wider than its box, a name
  wider than the whole plot, both plot edges, eight species crowded into a phone,
  the desktop case where everything fits, and the buffer-reuse contract. Removing
  the collision test from the implementation fails four of them, which was checked
  rather than assumed.
- Real Chromium, real canvas, real text metrics, at 390 px and 1440 px in both
  orientations, with the pre-change component rendered directly above the new one
  from the same fabricated detections. The 390 px "before" panel reproduces the
  operator's string exactly — `Eur Eurasian Jackdaw 91%` — and, in the crowded
  scene, `Northern Rough-winged Swallo` cut off by `European Robin 70%`. The
  "after" panel shows `Eurasian Jackdaw ×2 · best 95%`, both boxes intact, and no
  overlap anywhere in any scene at either width.
- Desktop is unchanged where there is room: at 1440 px the four well-separated
  labels in the dawn-chorus scene are at identical positions before and after.
  Only the ones that were overlapping moved.

**Not verified:** a real iOS or Android browser, and the live station (nothing was
deployed). The harness feeds fabricated detections, deliberately: the reported
collision is a two-second coincidence in a garden and waiting for one is not a
test.

**Known and not addressed here.** Above the 640 px breakpoint [[ADR-054]] floats the
spectrogram's badge strip back over the plot's top-right corner, and a detection
label placed there is partly hidden behind it. That is a DOM element over the
canvas rather than two canvas labels colliding, it predates this change, and
reserving the corner would mean measuring the strip's box in the draw loop —
a forced layout every frame, which is exactly the cost item 8 may not impose.

### Rollback and smoke test (ADR-058)

Rollback is `git revert` of the frontend commit and `npm run build`. The change
touches no Python, no schema, no persisted state and no transport; the only
runtime effect is which pixels the overlay canvas paints.

```bash
cd web && npm ci && npm test -- --run && npx tsc --noEmit && npm run build
OO_WEB_DIST=$PWD/dist ../.venv/bin/oo serve --source synthetic

# Watch the overlay while two detections of one species land within a few
# seconds of each other. `--mute-audio` matters: this page can start playing
# the live stream.
chromium --headless --disable-gpu --no-sandbox --mute-audio --hide-scrollbars \
  --window-size=390,844 --virtual-time-budget=18000 \
  --screenshot=$HOME/shots/labels390.png http://127.0.0.1:8080/

# What must be true in the picture: no two labels overlapping, no species name
# cut short, nothing crossing the canvas edge, and a repeated species reading
# "<name> ×N · best NN%" rather than N labels in one place.
```

---
Part of the [[ADRS|Architecture Decision Record index]].
