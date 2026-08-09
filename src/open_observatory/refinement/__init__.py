"""The refinement runner: charter priority 5, in its own process (ADR-045).

The charter's item 5 is "refine the record later, when better information
exists — and never silently", with three rules that make refinement honest
rather than a slow way of rewriting history:

* only from **new information** — never from re-reading the same score more
  optimistically;
* the **original claim is preserved** and stays attributable;
* a **refined record is distinguishable from an original one**, carrying what
  changed it and when.

All three are enforced here in code (:mod:`.contracts` and :mod:`.store`), not
left to the discipline of whoever writes the next refiner — every honesty
failure on this project so far has been sincere, so "we will remember" is not a
control.

Why a separate process, and not the station's own housekeeping loop: ADR-033.
A 10 s retention sweep — 0.30 s of work in a dedicated thread — starved the
station's event loop for 55-150 ms and cost ~1.9 false ``capture.gap`` records a
minute, because an executor partitions queueing and nothing partitions the GIL.
A BatDetect2 pass is 2.1 s of inference; it is the same defect two orders of
magnitude larger. Capture always wins (charter item 1), so the refiner runs in a
process capture does not share a GIL, a thread pool or (via ``AllowedCPUs=2-3``)
a core with. See ``deploy/open-observatory-refine.service``.
"""

from .contracts import (
    EXAMINED_OUTCOMES,
    EvidenceIdentity,
    RefinementBasis,
    RefinementCandidate,
    RefinementOutcome,
    RefinementProposal,
    RefinementViolation,
    Refiner,
    RefinerUnavailable,
)
from .runner import RefinementReport, RefinementRunner, in_quiet_window
from .store import find_candidates, record_refinement

__all__ = [
    "EXAMINED_OUTCOMES",
    "EvidenceIdentity",
    "RefinementBasis",
    "RefinementCandidate",
    "RefinementOutcome",
    "RefinementProposal",
    "RefinementReport",
    "RefinementRunner",
    "RefinementViolation",
    "Refiner",
    "RefinerUnavailable",
    "find_candidates",
    "in_quiet_window",
    "record_refinement",
]
