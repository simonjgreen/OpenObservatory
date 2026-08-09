"""What a flagged historical detection means to everything downstream (ADR-044).

``oo detections reconcile-plausibility --apply`` (``plausibility_repair.py``,
ADR-032) writes a ``plausibility_review`` block into a stored detection's
``native_result`` when that detection would no longer be admitted by today's
BirdNET plausibility logic -- principally the North American owls measured at
the development station in August 2026. The original ``native_result`` is preserved
verbatim alongside it; nothing is deleted and nothing is overwritten.

ADR-032 stopped there: it flagged rows and no consumer read the flag, so a
*Western Screech-Owl* already in the database still reached the API, the MQTT
publisher and the ESP32 wall display as a plain factual claim. This module is
the other half. It is the single definition of "this row has been withdrawn",
so the five surfaces that must agree about it cannot drift apart:

* ``api/app.py`` -- keeps the row and marks it withdrawn (charter item 5);
* ``history.py`` / ``api/app.py`` species tallies -- exclude it and say so;
* ``display_channel.py`` -- never puts it on the glass;
* ``mqtt/publisher.py`` -- never announces it;
* ``firmware/inside-observer`` -- refuses it on the HTTP fallback path too.

Deliberately dependency-free: no SQLAlchemy, no numpy, no FastAPI. The wall
display's channel module (``display_channel.py``) is documented as free of the
database and of FastAPI, and it has to be able to import this. The one thing
that genuinely needs SQLAlchemy -- a predicate that keeps withdrawn rows out of
a SQL aggregate -- lives next to ``is_live``/``is_not_live`` in ``history.py``
instead, which is where this codebase already keeps its "exclude from a
wildlife-facing view" predicates.

**Withdrawn, not deleted, and not silent.** The charter's item 5 is explicit:
"Preserve the original claim. The prior verdict stays visible and
attributable." A withdrawn row therefore keeps its score, its name and its
evidence; what changes is that every surface which presents it as an
*observation* either marks it or declines to show it. See ADR-044 for why the
line falls where it does between those two treatments.
"""

from __future__ import annotations

from typing import Any

#: Where ``plausibility_repair.apply_plausibility_flag`` writes its finding,
#: inside ``detection.native_result``. Named here rather than spelled as a
#: string literal in six modules.
REVIEW_KEY = "plausibility_review"

#: The boolean inside that block which means "this claim no longer stands".
WITHDRAWN_KEY = "implausible"


def review_block(native_result: Any) -> dict[str, Any] | None:
    """The stored review block, or ``None`` if this row has never been reviewed.

    Tolerant of every shape a ``native_result`` actually takes in this codebase:
    a dict, ``None`` (the column is nullable in practice on old rows), or the
    loosely-typed mapping that arrives on the event bus.
    """
    if not isinstance(native_result, dict):
        return None
    block = native_result.get(REVIEW_KEY)
    if not isinstance(block, dict):
        return None
    return block


def is_withdrawn(native_result: Any) -> bool:
    """True when a reviewer (the repair CLI) has withdrawn this row's claim.

    A row carrying a review block that concluded the detection is *still*
    plausible is not withdrawn -- the block records that a review happened, and
    the boolean inside it records the verdict. Reading the block's presence
    alone would withdraw rows that were examined and cleared.
    """
    block = review_block(native_result)
    return bool(block and block.get(WITHDRAWN_KEY))


def withdrawal(native_result: Any) -> dict[str, Any] | None:
    """The public, attributable form of the withdrawal, or ``None``.

    Returned verbatim rather than summarised: the stored block already carries
    the recomputed band, the recomputed occurrence probability, the threshold
    and a human-readable reason, and the charter's requirement is that a
    refined record be "distinguishable from an original one, with what changed
    it and when". Rewriting it here for brevity would lose exactly that.
    """
    block = review_block(native_result)
    if not block or not block.get(WITHDRAWN_KEY):
        return None
    return dict(block)
