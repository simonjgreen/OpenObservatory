---
aliases:
  - ADR-054
tags:
  - adr
---
# ADR-054: Responsive layout is intrinsic first, breakpoints last — and overflow is fixed, never hidden
**Decision:** Every row of controls in the web UI wraps and carries `min-width: 0` at
*every* width, rather than being rearranged by a media query at one. Media queries are
reserved for genuine changes of arrangement — a column count, an overlay becoming
ordinary flow — and the whole ladder is four widths, written down in `styles.css`:

| | |
|---|---|
| 640px | phone: one column; the spectrogram badge strip leaves the plot; touch targets grow |
| 950px | the three panel columns collapse to one |
| 1100px | waterfall spectrograms sit side by side |
| 1500px | three panel columns instead of two |

And: **no `overflow-x: hidden` on `html`/`body`, ever.**

**Reason:** The operator reported that on his phone the `GO LIVE` button could not be
reached. Reproduced in headless Chromium against a station at 360, 390, 414 and 430 px:
at 430 px the channel toggle and the button were cut off mid-word, and at 390 px they
and the clock were gone entirely, the button's right edge sitting at x=487 in a 390 px
viewport.

Two lines caused it, and the second is the more instructive:

1. `.topbar` had `flex-wrap: wrap`, so the *outer* header wrapped — but
   `.topbar-right`, which holds the mode switch, settings, diagnostics, the entire
   listen control and the clock, had none. The overflow happened *inside* that box,
   where the outer wrapping could not help. The mobile block even set
   `.topbar-right { width: 100% }`, which reads as though wrapping had been intended
   and simply never enabled.
2. The mobile block set `html, body { overflow-x: hidden }`. That did not fix the
   overflow; it **hid** it. The page reported a clean `document.scrollWidth` — 390 of
   390 — while a control the operator wanted could not even be scrolled to. A layout
   bug was converted into a measurement that lied about it, which is the same failure
   mode the charter's honesty constraint names elsewhere: sincere, believable, wrong.

This is why the rule is "intrinsic first". A breakpoint fixes one width; there were
only four media queries in the entire 1145-line stylesheet, so every width between them
was unconsidered. `flex-wrap` plus `min-width: 0` degrades continuously and needs no
one to have thought about 414 px specifically.

**Constraint — nothing is hidden to make it fit ([[ADR-016]], [[ADR-028]]).** A stat that
disappears at 400 px is a stat an operator cannot check from the garden, which is
exactly where they are standing when they want it. So:

- The six spectrogram badges (`audible`, `80 Hz–15 kHz`, `192 bins`, `24 ms/col`,
  `FFT 2048`, `scroll`) were overlapping the plot and wrapping mid-word at phone
  widths, with detection labels drawn on top of them. The strip is now a **sibling** of
  the plot rather than a child: CSS floats it back over the plot's top-right corner on
  a wide screen — pixel-identical to before — and below 640 px it becomes an ordinary
  wrapping row underneath, where it can neither be clipped nor be drawn over. All six
  badges survive.
- The history window picker has six named windows and cannot fit them on one
  phone-width row. It gets an opt-in `segmented-wrap` variant that breaks into a chip
  group instead of dropping options: "what came through at dawn?" stays answerable
  from a phone.
- The `diagnose` depth on a 390 px screen shows all six header stats, both level
  meters and every spectrogram tuning control. Nothing was moved behind a second
  toggle.

**Constraint — charter item 8 pays nothing.** This is layout only: no polling, no
`ResizeObserver`, no `matchMedia` listener, no additional React state and no re-render
on resize. The only JS-visible change is one extra wrapper `<div>` per spectrogram.
The hover geometry still reads `.spectrogram`'s own `getBoundingClientRect`, so the
readout and the plot cannot disagree.

**Verified, by measurement rather than by reading CSS.** A headless Chromium driven
over CDP loaded the production bundle (`npm run build`, served by the station's own
FastAPI process) at 360, 390, 414, 430, 1280 and 1920 px, in five states each — live,
diagnose, history, settings, and a detection drawer open — and in each asserted that
**no element's bounding rect extends past `document.documentElement.clientWidth`**.
Result: 30 of 30 states clean, from 30 of 30 failing before. At 390 px and 360 px
`GO LIVE` additionally passes a hit test — `document.elementFromPoint` at its centre
returns the button itself, its target is 35 px tall, and clicking it flips the label to
`STOP` and opens the stream. Desktop is unchanged: `GO LIVE`'s bounding box at 1280 px
and 1920 px is pixel-identical before and after. The first-run flow was measured
separately, with `setup_completed` false.

**Not verified:** a real iOS or Android browser (Chromium's device emulation is not
Safari), and the same test over the real Wi-Fi link to the Pi. Nothing was deployed to
the station; the measurements are against a local station on a synthetic source.

**Regression cover:** `web/src/responsive.test.tsx`. jsdom has no layout engine, so it
cannot re-measure a bounding box — but it asserts the two things that actually
regressed silently: the stylesheet's own text (`.topbar-right` and `.listen` carry
`flex-wrap: wrap` and `min-width: 0`; no rule on `html` or `body` clips horizontal
overflow) and the DOM structure the stylesheet depends on (the badge strip is a sibling
of `.spectrogram`, and still contains all six badges). Seven of its eight assertions
fail against the pre-fix code, which was checked rather than assumed.

### Rollback and smoke test (ADR-054)

Rollback is `git revert` of the frontend commit and `npm run build`; the change is
CSS, one JSX wrapper and one class name, and touches no Python, no schema and no
persisted state.

```bash
# 1. Build and serve the bundle locally against a synthetic source.
cd web && npm ci && npm test -- --run && npx tsc --noEmit && npm run build
OO_WEB_DIST=$PWD/dist ./.venv/bin/oo serve --source synthetic

# 2. Nothing may extend past the viewport, at a phone width.
#    (`--mute-audio` matters: this page can start playing the live stream.)
chromium --headless --disable-gpu --no-sandbox --mute-audio --hide-scrollbars \
  --window-size=390,844 --virtual-time-budget=18000 \
  --screenshot=$HOME/shots/after390.png http://127.0.0.1:8080/

# 3. The assertion that matters, run in the page rather than judged by eye:
#    every element's right edge inside document.documentElement.clientWidth,
#    and .go-live topmost at its own centre.
```

---
Part of the [[ADRS|Architecture Decision Record index]].
