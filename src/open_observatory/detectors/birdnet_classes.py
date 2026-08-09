"""BirdNET's non-taxonomic output classes, and what they are not (ADR-049).

BirdNET GLOBAL 6K V2.4 does not only emit species. Eleven of its 6,522 output
classes are **sound categories**: an engine, a siren, fireworks, a dog, a human
voice. They are useful — "that was traffic, not a bird" is an honest and
valuable thing for the station to be able to say — but they are not species,
and this module exists so that nothing in the pipeline treats them as if they
were.

**How they are distinguished, measured rather than guessed.** Every line of
``birdnet_labels.txt`` is ``Scientific name_Common Name``. Reading the shipped
en_uk label file (6,522 lines, sha256 ``487937b6…`` per ``models/manifest.tsv``)
on the live station on 2026-08-09:

* Thirteen lines have ``scientific == common``.
* Two of those thirteen are genuine Linnaean binomials that happen to have no
  vernacular name in this list — ``Gryllus assimilis`` and
  ``Miogryllus saussurei``, both real crickets.
* The remaining eleven are the sound categories enumerated below. Seven of
  them (``Dog``, ``Engine``, ``Environmental``, ``Fireworks``, ``Gun``,
  ``Noise``, ``Siren``) are single words and so are not binomials by shape;
  four (``Human non-vocal``, ``Human vocal``, ``Human whistle``,
  ``Power tools``) are two words and *look* exactly like a binomial
  — ``Power tools`` has the same capitalisation shape as ``Turdus merula``.

That last point is why this catalogue is an explicit, curated list rather than
a shape heuristic computed at import time. There is no string rule that keeps
``Gryllus assimilis`` and rejects ``Power tools``. The list is small, it is
fixed for a given model version, and
``tests/test_birdnet_classes.py::TestAgainstTheShippedLabels`` re-derives it
from the real label file when that file is present (it is never committed —
ADR-006) so a model bump that changed the set cannot pass unnoticed.

**Why this matters twice over.**

*Honesty.* Before ADR-049 the adapter stamped ``rank="species"`` and
``taxonomic_group="bird"`` onto every one of its outputs, so the live station's
database asserts that a car engine is a bird, at species rank, 203 times over —
and ``normaliser._canonical_taxon_id`` minted ``sci:engine`` as a stable taxon
key for it, which then became selectable as a correction target in
``GET /api/v1/taxa/search``. The charter's honesty constraint says a detector
that cannot identify a species must not name one. BirdNET *can* name species;
for these eleven classes it is not trying to, and the pipeline was upgrading a
sound category into a taxonomic claim on its behalf.

*Plausibility.* The BirdNET range (MData) model returns an occurrence
probability per *species* for a location and week. Asked about ``Engine`` it
returns 4e-06 at this station — not because engines are rare in this garden,
but because a car is not a taxon with a distribution and the question is
meaningless. ADR-032's plausibility floor reads that 4e-06 as "essentially
impossible here" and would withdraw a perfectly correct detection of a passing
car. A prior that cannot be meaningful must not be used as evidence, so these
classes are exempted from the floor and judged on score alone.

*Privacy.* Three of the eleven are human sound. The charter's privacy
constraint is about people who never consented to a microphone in a garden, and
:data:`HUMAN_KIND` is what ``clips.py`` uses to decide whether their audio is
written to disk at all. See ADR-049 and ``Settings.clip_human_audio``.

Deliberately dependency-free (no numpy, no SQLAlchemy, no model assets): it is
imported by ``clips.py`` on the evidence path, by ``normaliser.py``, by the two
repair modules and by the CLI, none of which may depend on the classifier being
installed.
"""

from __future__ import annotations

#: The kind whose members are human sound, and therefore the only kind the
#: privacy constraint is about. Named rather than spelled as a string literal
#: in ``clips.py``, ``taxonomy_repair.py`` and ``cli.py``.
HUMAN_KIND = "human"

#: ``taxonomic_group`` for every class here. Reuses the existing sentinel that
#: already means "this row records an event, not an organism" —
#: ``normaliser.NON_TAXONOMIC_GROUPS`` contains it, which is what stops
#: ``_canonical_taxon_id`` from minting ``sci:engine``, and
#: ``detectors/activity.py`` has emitted it since Milestone 1. Inventing a
#: twelfth group would have meant teaching every consumer about it.
NON_TAXONOMIC_GROUP = "acoustic_event"

#: The plausibility band these classes are sorted into (ADR-049), replacing
#: whatever the range model would otherwise have said about them.
NON_TAXONOMIC_BAND = "non_biological"

#: BirdNET label (the *scientific* field, which for these classes equals the
#: common field) -> the kind of sound it is. The kinds are for the operator's
#: benefit and for the privacy gate; only ``human`` changes behaviour.
NON_TAXONOMIC_LABELS: dict[str, str] = {
    "Dog": "domestic_animal",
    "Engine": "anthropogenic",
    "Environmental": "environmental",
    "Fireworks": "anthropogenic",
    "Gun": "anthropogenic",
    "Human non-vocal": HUMAN_KIND,
    "Human vocal": HUMAN_KIND,
    "Human whistle": HUMAN_KIND,
    "Noise": "environmental",
    "Power tools": "anthropogenic",
    "Siren": "anthropogenic",
}

#: The two ``scientific == common`` entries that are *not* sound categories.
#: Kept here because they are the whole reason this catalogue is curated, and
#: because the test that re-derives the list from the real label file needs
#: them to make the derivation exact.
BINOMIALS_WITHOUT_A_COMMON_NAME: frozenset[str] = frozenset(
    {"Gryllus assimilis", "Miogryllus saussurei"}
)

#: Only these labels carry human sound. A frozenset, because ``clips.py``
#: tests membership on the evidence path.
HUMAN_LABELS: frozenset[str] = frozenset(
    name for name, kind in NON_TAXONOMIC_LABELS.items() if kind == HUMAN_KIND
)


def kind_of(scientific: str | None) -> str | None:
    """The sound kind for a BirdNET *scientific-field* value, or ``None``.

    ``None`` means "this is an ordinary taxonomic label" — the common case, and
    the answer for all 6,511 real species.
    """
    if scientific is None:
        return None
    return NON_TAXONOMIC_LABELS.get(scientific)


def is_non_taxonomic(scientific: str | None) -> bool:
    """True when this label is a sound category rather than a species."""
    return kind_of(scientific) is not None


def kind_of_detector_label(label: str | None) -> str | None:
    """The sound kind for a whole stored label, e.g. ``"Human vocal_Human vocal"``.

    Takes the raw ``detection.detector_label`` as persisted, so callers that
    only have the database row — the clip writer, the purge command, the
    taxonomy repair — do not each have to re-implement the underscore split.
    Tolerates a label with no underscore, and a ``None`` label, both of which
    occur on rows written by other detectors.
    """
    if not label:
        return None
    scientific = label.split("_", 1)[0].strip() if "_" in label else label.strip()
    return kind_of(scientific)


def is_human_audio(label: str | None) -> bool:
    """True when a stored detector label names one of the three human classes.

    This is the privacy predicate. It is deliberately about the *label*, not
    about a score or a band: whether a recording contains a neighbour's voice
    is not a question the confidence threshold gets a say in.
    """
    return kind_of_detector_label(label) == HUMAN_KIND
