"""BirdNET's non-taxonomic output classes (ADR-049).

Two kinds of test here, and they are doing different jobs.

The first kind pins the *behaviour* the rest of the system depends on, and
runs everywhere. The second re-derives the catalogue from the real, shipped
``birdnet_labels.txt`` and runs only where that file exists — it is never
committed (ADR-006: the labels are CC BY-NC-SA model data), so on a checkout
without ``oo models fetch`` it skips, exactly as the BirdNET fixture tests do.
That skip is the designed behaviour and not a hole: the derivation is what
proves the eleven names are the *right* eleven, so it must be run on a machine
with the assets — the live station or a Pi — whenever the model version moves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from open_observatory.detectors import birdnet_classes
from open_observatory.models import DEFAULT_MODEL_DIR


class TestTheCatalogue:
    def test_the_eleven_sound_categories_are_named(self) -> None:
        assert set(birdnet_classes.NON_TAXONOMIC_LABELS) == {
            "Dog",
            "Engine",
            "Environmental",
            "Fireworks",
            "Gun",
            "Human non-vocal",
            "Human vocal",
            "Human whistle",
            "Noise",
            "Power tools",
            "Siren",
        }

    def test_only_the_three_human_classes_are_human(self) -> None:
        assert set(birdnet_classes.HUMAN_LABELS) == {
            "Human vocal",
            "Human non-vocal",
            "Human whistle",
        }

    def test_a_real_species_is_not_a_sound_category(self) -> None:
        assert birdnet_classes.kind_of("Strix aluco") is None
        assert birdnet_classes.is_non_taxonomic("Turdus merula") is False

    def test_the_two_crickets_stay_taxonomic(self) -> None:
        """`Gryllus assimilis` has `scientific == common` and is still a species.

        This is the reason the catalogue is a curated list rather than a rule
        computed from the label file at import time. A "scientific equals
        common" rule would demote two real insects; a "looks like a binomial"
        rule would promote "Power tools".
        """
        for name in birdnet_classes.BINOMIALS_WITHOUT_A_COMMON_NAME:
            assert birdnet_classes.kind_of(name) is None

    def test_a_stored_label_is_split_before_it_is_matched(self) -> None:
        assert birdnet_classes.kind_of_detector_label("Engine_Engine") == "anthropogenic"
        assert birdnet_classes.kind_of_detector_label("Strix aluco_Tawny Owl") is None

    def test_human_audio_predicate_matches_only_the_human_classes(self) -> None:
        assert birdnet_classes.is_human_audio("Human vocal_Human vocal") is True
        assert birdnet_classes.is_human_audio("Human whistle_Human whistle") is True
        assert birdnet_classes.is_human_audio("Dog_Dog") is False
        assert birdnet_classes.is_human_audio("Strix aluco_Tawny Owl") is False

    def test_absent_and_malformed_labels_do_not_raise(self) -> None:
        """Rows written by the other two detectors reach these predicates too."""
        assert birdnet_classes.kind_of_detector_label(None) is None
        assert birdnet_classes.kind_of_detector_label("") is None
        assert birdnet_classes.kind_of_detector_label("acoustic event") is None
        assert birdnet_classes.is_human_audio(None) is False

    def test_the_group_reuses_the_existing_sentinel(self) -> None:
        """Not a twelfth taxonomic group: the one that already means 'not an organism'."""
        from open_observatory.normaliser import NON_TAXONOMIC_GROUPS

        assert birdnet_classes.NON_TAXONOMIC_GROUP in NON_TAXONOMIC_GROUPS


def _labels_path() -> Path | None:
    candidate = DEFAULT_MODEL_DIR / "birdnet_labels.txt"
    return candidate if candidate.exists() else None


@pytest.mark.skipif(
    _labels_path() is None,
    reason="birdnet_labels.txt is not installed (ADR-006; run 'oo models fetch')",
)
class TestAgainstTheShippedLabels:
    """Re-derive the catalogue from the real label file.

    Verified against the live station's own copy on 2026-08-09: 6,522 lines,
    thirteen with `scientific == common`, of which two are real binomials.
    """

    def _entries(self) -> list[tuple[str, str]]:
        path = _labels_path()
        assert path is not None
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [tuple(line.split("_", 1)) for line in lines]  # type: ignore[misc]

    def test_the_catalogue_is_exactly_the_non_binomial_self_named_labels(self) -> None:
        self_named = {
            scientific for scientific, common in self._entries() if scientific == common
        }
        derived = self_named - birdnet_classes.BINOMIALS_WITHOUT_A_COMMON_NAME
        assert derived == set(birdnet_classes.NON_TAXONOMIC_LABELS)

    def test_every_catalogued_label_is_actually_in_the_model(self) -> None:
        """A catalogue naming a class the model cannot emit would be dead code."""
        scientific_names = {scientific for scientific, _ in self._entries()}
        assert set(birdnet_classes.NON_TAXONOMIC_LABELS) <= scientific_names
        assert set(birdnet_classes.BINOMIALS_WITHOUT_A_COMMON_NAME) <= scientific_names
