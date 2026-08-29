---
aliases:
  - ADR-027
tags:
  - adr
---
# ADR-027: A spacing/type scale for the web UI, applied to new surfaces rather than migrated wholesale
**Status:** active on the token layer itself; its "no new one-off pixel value"
constraint has not held for the CSS written after it (see the review note).

**Decision:** `web/src/styles.css` gains a token layer — an 8-step spacing scale
(`--space-1`..`--space-8`, 4px base) and an 8-step type scale (`--text-2xs`..`--text-2xl`,
anchored on the existing 13px body size) — alongside the pre-existing 18 colour/radius/font
custom properties. Every surface built for Milestone 4 (`OperatorSummary`, the diagnostics
toggle, `ExportLinks`, `RetentionPanel`, the review controls, the mobile breakpoint) is
built exclusively from these tokens. The ~700 lines of component CSS that predate this
work — spectrogram, suggestions list, event log, history chart, drawer — are **not**
migrated to the scale in this change.

**Reason:** ADRS.md's own honest assessment of Milestone 4's starting point was "a
colour-token header over ad-hoc component CSS, no spacing or type scale," and fixing that
is explicitly named as a foundation task, not a tidy-up. But a wholesale migration of 700
lines of working, visually-tuned CSS — much of it density-tuned for a spectrogram and
timeline that read correctly at specific pixel values — is a large, high-risk, low-reward
diff to run alongside behavioural changes (state extraction, disclosure, export). The
scale exists and is proven correct on real new surfaces; retrofitting it onto the old
surfaces is real, separately-reviewable follow-up work, not a rename.

**Constraint:** No new CSS added after this ADR may introduce a one-off pixel value for
spacing or font size where a token fits. `--radius`, `--radius-sm`, `--radius-lg` and the
existing colour tokens are unchanged and continue to be the source of truth for colour.

**Deliberately out of scope: a light theme.** The UI stays dark-by-default — it is an
ambient instrument left open next to a spectrogram at dusk (the module comment on
`styles.css` predates this ADR and is still accurate), and a second palette was not asked
for. `color-scheme: dark` is set on `:root` so the browser's own chrome (scrollbars, native
form controls) matches rather than mismatching light-on-dark, which is the concrete gap a
light theme would otherwise be closing.

**Reviewed 2026-08-29:** the token layer holds and the migration is still scoped out.
`web/src/styles.css:39-63` defines `--space-1`..`--space-8` on a 4px base and
`--text-2xs`..`--text-2xl` anchored on the 13px body size, `color-scheme: dark` is still
set on `:root` (`web/src/styles.css:66`) and no second palette exists anywhere under
`web/`, and the Milestone 4 block (`web/src/styles.css:867-984`) is still written from the
tokens. The station's deployed bundle carries the same token names, so what is running
matches this. The ~700 lines of older component CSS were never migrated, which is what
this ADR said would happen; the follow-up work it scoped out has not been done.

The constraint, however, did not hold. The stylesheet has grown from 895 lines to 1394,
and everything from `web/src/styles.css:1093` to the end of the file — the login and
password-change gate ([[ADR-034 - Authentication foundation|ADR-034]]), the site settings panel ([[ADR-048 - Web-configurable settings|ADR-048]]), the first-run
banners and the firmware upload — was written after this ADR out of one-off values: 28 raw
pixel font sizes against 5 uses of `--text-*`, and 54 raw spacing values against 5 uses of
`--space-*`. Four colours were hard-coded rather than taken from the colour tokens this
ADR names as the source of truth for colour: `#d4a017` five times and `#f5d78a` once
beside `--warn`, `#d0454c` beside `--danger`, and `#2a2a2a` twice as a literal fallback in
`var(--line, #2a2a2a)`. Nothing enforces the constraint: `web/` has no stylelint
configuration and no test asserts it.

---
Part of the [[ADRS|Architecture Decision Record index]].
