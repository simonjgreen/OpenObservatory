---
aliases:
  - ADR-027
tags:
  - adr
---
# ADR-027: A spacing/type scale for the web UI, applied to new surfaces rather than migrated wholesale
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

---
Part of the [[ADRS|Architecture Decision Record index]].
