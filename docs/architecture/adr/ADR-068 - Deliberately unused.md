---
aliases:
  - ADR-068
tags:
  - adr
---
# ADR-068: Deliberately unused
**Status:** not a decision — the number is unused; do not reuse.

Not a decision. This number was claimed and released on 2026-08-23 while two
pieces of work were being written concurrently against the same checkout: both
reached for the next free number at the same moment, discovered the collision,
and renumbered to 069 and 070. Nothing was written under 068 and nothing was
deleted.

Recorded rather than closed up, because silently renumbering 069 and 070 would
rewrite twenty-two references in code and tests to remove a gap that costs
nothing, and a reader who finds a hole in this file deserves an answer better
than the absence of one.

---
Part of the [[ADRS|Architecture Decision Record index]].
