---
aliases:
  - ADR-005
tags:
  - adr
---
# ADR-005: No scientifically authoritative composite biodiversity score in v1
**Status:** active.

**Decision:** Present transparent activity/diversity measurements individually.

**Reason:** A proprietary composite score would imply validation the project does not have.

**Reviewed 2026-08-29:** the decision holds, and nothing in the tree computes a
composite biodiversity score. `GET /api/v1/history` returns `timeline`,
`species` and `coverage` as three sibling keys
(`src/open_observatory/api/app.py:2046-2071`), so
"individually" is enforced by the response shape rather than left to convention
-- and coverage sits beside the counts precisely so an empty night cannot be
misread as a quiet one. Every `score` in the codebase is one detector's
confidence in one detection, never an aggregate across species.

The principle has since been applied more strictly than this ADR states.
[[ADR-026 - Tiered clip retention|ADR-026]] declined to rank two bat passes by `ultrasonic-pass-v1`'s own
internal composite -- unearned precision even within a single detector -- and
ranked by the measured `peak_snr_db` instead. That ranking is no longer running
code: [[ADR-061 - Operator keep flag|ADR-061]] removed the computed exemplar rule entirely in favour of an
operator-set `kept` flag, so nothing now ranks one recording against another
at all. [[ADR-023 - The ESP32 inside observer|ADR-023]] and
[[ADR-038 - Display push channel|ADR-038]] bar the counter-top display from
showing any score, percentage or confidence figure at all, with a firmware
regression test (`test_no_feed_item_ever_carries_a_score`) holding that line.

The "in v1" qualifier is still live rather than spent: a "scientific
biodiversity index" remains on [[IMPLEMENTATION_PLAN]]'s explicitly-deferred
list, and the synthetic authoritative score is a stated non-goal in [[PRD]] §6.

---
Part of the [[ADRS|Architecture Decision Record index]].
