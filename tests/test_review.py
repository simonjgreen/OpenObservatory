"""Unit tests for `review.py` (ADR-043): the shared "latest review" and
taxon-lookup helpers used by the detection API, the retention sweeper and
`plausibility_repair.py`.

HTTP-level coverage of the review endpoints themselves (POST/GET
`/detections/{id}/review`, `GET /taxa/search`, and how a correction shows up
in the list/detail/export payloads) lives in `test_api.py::TestReviewWorkflow`
-- these tests are for the query logic in isolation, with a hand-seeded
database rather than the full pipeline.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from open_observatory import review as review_queries
from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope

BASE = datetime(2026, 8, 8, 21, 0, tzinfo=UTC)


@pytest.fixture
def db(settings):
    init_engine(settings)
    create_all()
    return settings


@pytest.fixture
def station_and_detector(db):
    station_id = uuid.uuid4()
    detector_id = uuid.uuid4()
    with session_scope() as session:
        session.add(orm.Station(id=station_id, name="test", timezone="Europe/London"))
        session.add(
            orm.Detector(
                id=detector_id,
                plugin_id="birdnet-v2.4",
                plugin_version="1",
                model_id="m",
                model_version="1",
            )
        )
    return station_id, detector_id


def _detection(
    session,
    *,
    station_id: uuid.UUID,
    detector_id: uuid.UUID,
    common_name: str | None,
    scientific_name: str | None,
    canonical_taxon_id: str | None,
    taxonomic_group: str = "bird",
    rank: str | None = "species",
    score: float = 0.7,
) -> uuid.UUID:
    detection_id = uuid.uuid4()
    session.add(
        orm.Detection(
            id=detection_id,
            station_id=station_id,
            detector_id=detector_id,
            stream_id=uuid.uuid4(),
            window_id=uuid.uuid4(),
            event_start_utc=BASE,
            event_end_utc=BASE + timedelta(seconds=3),
            source_start_frame=0,
            source_end_frame=1,
            detector_label=common_name or "event",
            common_name=common_name,
            scientific_name=scientific_name,
            canonical_taxon_id=canonical_taxon_id,
            rank=rank,
            taxonomic_group=taxonomic_group,
            score=score,
        )
    )
    return detection_id


class TestLatestReviews:
    def test_no_review_is_absent_from_the_map(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            det_id = _detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name="Robin",
                scientific_name="Erithacus rubecula",
                canonical_taxon_id="sci:erithacus_rubecula",
            )
        with session_scope() as session:
            assert review_queries.latest_review(session, det_id) is None
            assert review_queries.latest_reviews_by_detection(session, [det_id]) == {}

    def test_latest_by_created_at_wins(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            det_id = _detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name="Robin",
                scientific_name="Erithacus rubecula",
                canonical_taxon_id="sci:erithacus_rubecula",
            )
            session.add(
                orm.Review(
                    detection_id=det_id, actor="a", status="rejected", created_at=BASE
                )
            )
            session.add(
                orm.Review(
                    detection_id=det_id,
                    actor="b",
                    status="confirmed",
                    created_at=BASE + timedelta(seconds=1),
                )
            )
        with session_scope() as session:
            latest = review_queries.latest_review(session, det_id)
            assert latest is not None
            assert latest.status == "confirmed"
            assert latest.actor == "b"

            batch = review_queries.latest_reviews_by_detection(session, [det_id])
            assert batch[det_id].status == "confirmed"

    def test_batch_handles_an_empty_id_list(self, db) -> None:
        with session_scope() as session:
            assert review_queries.latest_reviews_by_detection(session, []) == {}


class TestHeldDetectionIds:
    def test_only_currently_held_detections_are_returned(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            held = _detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name="Robin",
                scientific_name="Erithacus rubecula",
                canonical_taxon_id="sci:erithacus_rubecula",
            )
            released = _detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name="Wren",
                scientific_name="Troglodytes troglodytes",
                canonical_taxon_id="sci:troglodytes_troglodytes",
            )
            untouched = _detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name="Blackbird",
                scientific_name="Turdus merula",
                canonical_taxon_id="sci:turdus_merula",
            )
            session.add(orm.Review(detection_id=held, actor="op", status="held", created_at=BASE))
            session.add(
                orm.Review(detection_id=released, actor="op", status="held", created_at=BASE)
            )
            session.add(
                orm.Review(
                    detection_id=released,
                    actor="op",
                    status="confirmed",
                    created_at=BASE + timedelta(seconds=1),
                )
            )
        with session_scope() as session:
            ids = review_queries.held_detection_ids(session)
        assert ids == {held}
        assert released not in ids
        assert untouched not in ids


class TestReviewedDetectionIds:
    def test_any_status_counts_as_reviewed(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            confirmed = _detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name="Robin",
                scientific_name="Erithacus rubecula",
                canonical_taxon_id="sci:erithacus_rubecula",
            )
            untouched = _detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name="Wren",
                scientific_name="Troglodytes troglodytes",
                canonical_taxon_id="sci:troglodytes_troglodytes",
            )
            session.add(orm.Review(detection_id=confirmed, actor="op", status="confirmed"))
        with session_scope() as session:
            reviewed = review_queries.reviewed_detection_ids(session, [confirmed, untouched])
        assert reviewed == {confirmed}

    def test_empty_input_is_empty_output(self, db) -> None:
        with session_scope() as session:
            assert review_queries.reviewed_detection_ids(session, []) == set()


class TestResolveAndSearchTaxa:
    def test_resolve_taxon_matches_by_canonical_id(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name="European Robin",
                scientific_name="Erithacus rubecula",
                canonical_taxon_id="sci:erithacus_rubecula",
            )
        with session_scope() as session:
            found = review_queries.resolve_taxon(session, "sci:erithacus_rubecula")
            assert found is not None
            assert found.common_name == "European Robin"
            assert review_queries.resolve_taxon(session, "sci:nonexistent") is None

    def test_search_matches_common_or_scientific_name_case_insensitively(
        self, db, station_and_detector
    ) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name="European Robin",
                scientific_name="Erithacus rubecula",
                canonical_taxon_id="sci:erithacus_rubecula",
            )
            _detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name="Eurasian Wren",
                scientific_name="Troglodytes troglodytes",
                canonical_taxon_id="sci:troglodytes_troglodytes",
            )
        with session_scope() as session:
            by_common = review_queries.search_taxa(session, "robin")
            assert [t["taxon_id"] for t in by_common] == ["sci:erithacus_rubecula"]

            by_scientific = review_queries.search_taxa(session, "TROGLODYTES")
            assert [t["taxon_id"] for t in by_scientific] == ["sci:troglodytes_troglodytes"]

            assert review_queries.search_taxa(session, "nonexistent-species") == []
            assert review_queries.search_taxa(session, "   ") == []

    def test_search_excludes_taxa_with_no_canonical_id(self, db, station_and_detector) -> None:
        """A bat pass or an acoustic event never carries a species claim
        (ADR-010/ADR-013) -- neither may ever surface as a correction target."""
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            _detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name=None,
                scientific_name=None,
                canonical_taxon_id=None,
                taxonomic_group="acoustic_event",
                rank=None,
            )
        with session_scope() as session:
            assert review_queries.search_taxa(session, "event") == []

    def test_search_ranks_by_detection_count_and_respects_limit(
        self, db, station_and_detector
    ) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            for _ in range(3):
                _detection(
                    session,
                    station_id=station_id,
                    detector_id=detector_id,
                    common_name="European Robin",
                    scientific_name="Erithacus rubecula",
                    canonical_taxon_id="sci:erithacus_rubecula",
                )
            _detection(
                session,
                station_id=station_id,
                detector_id=detector_id,
                common_name="Eurasian Robin Chat",
                scientific_name="Cossypha semirufa",
                canonical_taxon_id="sci:cossypha_semirufa",
            )
        with session_scope() as session:
            results = review_queries.search_taxa(session, "robin", limit=1)
            assert len(results) == 1
            assert results[0]["taxon_id"] == "sci:erithacus_rubecula"
            assert results[0]["detections"] == 3
