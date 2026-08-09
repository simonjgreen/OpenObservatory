"""Historical taxonomy repair for BirdNET's sound categories (ADR-049).

Seeds a database in the shape the live station's actually is — ``Engine``,
``Human vocal`` and ``Dog`` stored with ``rank='species'``,
``taxonomic_group='bird'``, ``scientific_name`` repeating the common name and
``canonical_taxon_id='sci:engine'`` — alongside a real bird, and checks that
the repair corrects the first three and leaves the fourth entirely alone.

No model assets are needed: unlike the plausibility repair, this one never
consults the range model. The label list it needs is the small curated
catalogue in ``detectors/birdnet_classes.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from open_observatory import taxonomy_repair as repair
from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope

BASE = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)

#: (common name, label, sound kind or None for a real species)
SEED = [
    ("Engine", "Engine_Engine", "anthropogenic"),
    ("Human vocal", "Human vocal_Human vocal", "human"),
    ("Dog", "Dog_Dog", "domestic_animal"),
    ("Tawny Owl", "Strix aluco_Tawny Owl", None),
]


@pytest.fixture
def seeded(settings) -> dict[str, uuid.UUID]:
    init_engine(settings)
    create_all()
    station_id = uuid.uuid4()
    stream_id = uuid.uuid4()
    detector_id = uuid.uuid4()
    ids: dict[str, uuid.UUID] = {}

    with session_scope() as session:
        session.add(orm.Station(id=station_id, name="test", timezone="Europe/London"))
        session.add(
            orm.Detector(
                id=detector_id,
                plugin_id="birdnet-v2.4",
                plugin_version="1",
                model_id="m",
                model_version="2.4",
            )
        )
        session.add(
            orm.AudioStream(
                id=stream_id,
                source_kind="alsa",
                start_utc=BASE,
                end_utc=BASE + timedelta(hours=1),
                start_monotonic_ns=0,
                sample_rate=48000,
                sample_format="FLOAT_LE",
                frame_count=48000 * 3600,
            )
        )
        for name, label, _kind in SEED:
            scientific = label.split("_", 1)[0]
            det_id = uuid.uuid4()
            session.add(
                orm.Detection(
                    id=det_id,
                    station_id=station_id,
                    detector_id=detector_id,
                    stream_id=stream_id,
                    window_id=uuid.uuid4(),
                    event_start_utc=BASE,
                    event_end_utc=BASE + timedelta(seconds=3),
                    source_start_frame=0,
                    source_end_frame=1,
                    detector_label=label,
                    common_name=name,
                    scientific_name=scientific,
                    # Exactly what the pre-ADR-049 pipeline wrote, including
                    # the fabricated taxon key.
                    canonical_taxon_id=f"sci:{scientific.lower().replace(' ', '_')}",
                    rank="species",
                    taxonomic_group="bird",
                    score=0.98,
                    native_result={"detector": "birdnet-v2.4", "week": 30},
                )
            )
            ids[name] = det_id
    return ids


class TestFindMislabelledTaxonomy:
    def test_finds_the_sound_categories_and_only_those(self, settings, seeded) -> None:
        with session_scope() as session:
            findings = repair.find_mislabelled_taxonomy(session)
        assert {item.common_name for item in findings} == {"Engine", "Human vocal", "Dog"}
        kinds = {item.common_name: item.sound_kind for item in findings}
        assert kinds["Human vocal"] == "human"
        assert kinds["Dog"] == "domestic_animal"

    def test_is_read_only(self, settings, seeded) -> None:
        with session_scope() as session:
            repair.find_mislabelled_taxonomy(session)
        with session_scope() as session:
            row = session.get(orm.Detection, seeded["Engine"])
            assert row.rank == "species"
            assert repair.REVIEW_KEY not in (row.native_result or {})

    def test_a_correctly_stored_row_is_not_a_finding(self, settings, seeded) -> None:
        """Post-ADR-049 rows must not be re-found forever."""
        with session_scope() as session:
            row = session.get(orm.Detection, seeded["Dog"])
            row.rank = None
            row.taxonomic_group = "acoustic_event"
            row.scientific_name = None
            row.canonical_taxon_id = None
        with session_scope() as session:
            findings = repair.find_mislabelled_taxonomy(session)
        assert {item.common_name for item in findings} == {"Engine", "Human vocal"}


class TestApplyTaxonomyCorrection:
    def _apply_all(self) -> int:
        with session_scope() as session:
            findings = repair.find_mislabelled_taxonomy(session)
            for item in findings:
                repair.apply_taxonomy_correction(session, item)
        return len(findings)

    def test_corrects_the_fields_and_keeps_everything_else(self, settings, seeded) -> None:
        assert self._apply_all() == 3
        with session_scope() as session:
            row = session.get(orm.Detection, seeded["Engine"])
            assert row is not None  # never deleted
            assert row.rank is None
            assert row.taxonomic_group == "acoustic_event"
            assert row.scientific_name is None
            assert row.canonical_taxon_id is None
            # The one thing that must survive: what was actually heard.
            assert row.common_name == "Engine"
            assert row.score == pytest.approx(0.98)

    def test_the_original_values_stay_on_the_record(self, settings, seeded) -> None:
        """ADR-044's rule: a refined record is distinguishable from an
        original one, with what changed it and when."""
        self._apply_all()
        with session_scope() as session:
            row = session.get(orm.Detection, seeded["Human vocal"])
            block = row.native_result[repair.REVIEW_KEY]
            assert block["original_rank"] == "species"
            assert block["original_taxonomic_group"] == "bird"
            assert block["original_scientific_name"] == "Human vocal"
            assert block["original_canonical_taxon_id"] == "sci:human_vocal"
            assert block["corrected_taxonomic_group"] == "acoustic_event"
            assert block["sound_kind"] == "human"
            assert block["reviewed_utc"]
            # The detector's own output is preserved beside it, not replaced.
            assert row.native_result["week"] == 30

    def test_a_real_bird_is_untouched(self, settings, seeded) -> None:
        self._apply_all()
        with session_scope() as session:
            row = session.get(orm.Detection, seeded["Tawny Owl"])
            assert row.rank == "species"
            assert row.taxonomic_group == "bird"
            assert row.scientific_name == "Strix aluco"
            assert row.canonical_taxon_id == "sci:strix_aluco"
            assert repair.REVIEW_KEY not in row.native_result

    def test_running_twice_changes_nothing_the_second_time(self, settings, seeded) -> None:
        assert self._apply_all() == 3
        assert self._apply_all() == 0
        with session_scope() as session:
            row = session.get(orm.Detection, seeded["Engine"])
            block = row.native_result[repair.REVIEW_KEY]
            # Still the *original* values, not the corrected ones re-recorded.
            assert block["original_rank"] == "species"
