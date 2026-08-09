"""The refinement runner, and the three charter rules it has to enforce (ADR-042).

The tests that matter here are the refusals. A refinement pipeline that works is
easy; the failure this project keeps having is the *sincere* one — the system was
confident and wrong, and every test stayed green. So the bulk of this file is
about what must not be possible: re-reading the same score, quietly moving the
original claim, and a classifier with known accuracy problems editing the record
on its own.

The BatDetect2 refiner itself is exercised through a stub that stands in for the
library's ``process_audio``, because BatDetect2's code and weights are
CC-BY-NC-4.0 and are never installed in CI (ADR-006, ADR-017). The one test that
needs the real library skips cleanly when it is absent, the way
``tests/test_batdetect2.py`` already does.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from open_observatory.db import models as orm
from open_observatory.refinement.batdetect2 import BatDetect2Refiner
from open_observatory.refinement.contracts import (
    EXAMINED_OUTCOMES,
    EvidenceIdentity,
    RefinementBasis,
    RefinementCandidate,
    RefinementOutcome,
    RefinementProposal,
    RefinementViolation,
    RefinerUnavailable,
)
from open_observatory.refinement.runner import (
    RefinementRunner,
    in_quiet_window,
    write_health_event,
)
from open_observatory.refinement.store import find_candidates, record_refinement

PASS_DETECTOR_MODEL = ("ultrasonic-pass", "1")


# ----------------------------------------------------------------------
# fixtures


@pytest.fixture()
def session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'test.sqlite'}", future=True)
    orm.Base.metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False, future=True)

    class _Scope:
        def __enter__(self) -> Session:
            self.session = maker()
            return self.session

        def __exit__(self, exc_type, exc, tb) -> None:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
            self.session.close()

    return _Scope


def _write_clip(path: Path, *, seconds: float = 0.5, rate: int = 384_000) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    pcm = np.zeros(frames, dtype="float32")
    pcm[frames // 2] = 0.9  # a single loud sample, so trimming has something to centre on
    sf.write(str(path), pcm, rate, subtype="FLOAT")
    return frames


def _seed(
    session: Session,
    tmp_path: Path,
    *,
    count: int = 1,
    with_clip: bool = True,
    group: str = "bat",
    peak_hz: float | None = 34_000.0,
) -> list[orm.Detection]:
    # Get-or-create: `detector` carries a unique index on
    # (plugin_id, plugin_version, model_version), so a test that seeds twice
    # must reuse the row rather than insert a second one.
    station = session.execute(select(orm.Station)).scalars().first()
    if station is None:
        station = orm.Station(name="test")
        session.add(station)
    detector = session.execute(select(orm.Detector)).scalars().first()
    if detector is None:
        detector = orm.Detector(
            plugin_id="ultrasonic-pass-v1",
            plugin_version="1",
            model_id=PASS_DETECTOR_MODEL[0],
            model_version=PASS_DETECTOR_MODEL[1],
        )
        session.add(detector)
    session.flush()

    made: list[orm.Detection] = []
    existing = len(session.execute(select(orm.Detection)).scalars().all())
    for offset in range(count):
        index = existing + offset
        detection = orm.Detection(
            station_id=station.id,
            detector_id=detector.id,
            stream_id=uuid.uuid4(),
            window_id=uuid.uuid4(),
            event_start_utc=datetime(2026, 8, 1, tzinfo=UTC) + timedelta(minutes=index),
            event_end_utc=datetime(2026, 8, 1, tzinfo=UTC) + timedelta(minutes=index, seconds=1),
            source_start_frame=0,
            source_end_frame=384_000,
            common_name="Bat pass",
            taxonomic_group=group,
            score=0.62,
            peak_frequency_hz=peak_hz,
            native_result={"peak_snr_db": 21.4, "pulse_count": 7},
        )
        session.add(detection)
        session.flush()
        if with_clip:
            clip = tmp_path / "clips" / f"clip-{index}.wav"
            _write_clip(clip)
            asset = orm.MediaAsset(
                kind="evidence_native",
                storage_uri=str(clip),
                mime_type="audio/wav",
                sample_rate=384_000,
                sha256="0" * 64,
            )
            session.add(asset)
            session.flush()
            session.add(
                orm.DetectionMedia(detection_id=detection.id, media_asset_id=asset.id)
            )
        made.append(detection)
    session.flush()
    return made


NEW_MODEL_IDENTITY = EvidenceIdentity(
    refiner_id="test-refiner",
    refiner_version="1",
    model_id="some-other-model",
    model_version="9",
    config={"threshold": 0.1},
)


def _candidate_from(detection: orm.Detection, clip: Path | None) -> RefinementCandidate:
    return RefinementCandidate(
        detection_id=detection.id,
        event_start_utc=detection.event_start_utc,
        taxonomic_group=detection.taxonomic_group,
        common_name=detection.common_name,
        scientific_name=detection.scientific_name,
        score=detection.score,
        peak_frequency_hz=detection.peak_frequency_hz,
        detector_plugin_id="ultrasonic-pass-v1",
        detector_model_id=PASS_DETECTOR_MODEL[0],
        detector_model_version=PASS_DETECTOR_MODEL[1],
        native_result=dict(detection.native_result),
        clip_path=clip,
        clip_sample_rate=384_000,
        media_asset_id=None,
    )


PROPOSAL = RefinementProposal(
    outcome=RefinementOutcome.PROPOSED,
    basis=RefinementBasis.NEW_MODEL,
    reason="a different model suggests something else",
    proposed_scientific_name="Myotis nattereri",
    proposed_score=0.28,
    evidence={"classified_audio_s": 1.5},
)


# ----------------------------------------------------------------------
# rule 1: only from new information


class TestOnlyFromNewInformation:
    def test_the_fingerprint_covers_configuration_not_just_the_model(self) -> None:
        """Otherwise the only way to get a second answer is to bump a version string."""
        base = NEW_MODEL_IDENTITY
        same = EvidenceIdentity(
            refiner_id=base.refiner_id,
            refiner_version=base.refiner_version,
            model_id=base.model_id,
            model_version=base.model_version,
            config={"threshold": 0.1},
        )
        different = EvidenceIdentity(
            refiner_id=base.refiner_id,
            refiner_version=base.refiner_version,
            model_id=base.model_id,
            model_version=base.model_version,
            config={"threshold": 0.2},
        )
        assert base.fingerprint == same.fingerprint
        assert base.fingerprint != different.fingerprint

    def test_fingerprint_is_order_independent(self) -> None:
        a = EvidenceIdentity("r", "1", "m", "1", config={"a": 1, "b": 2})
        b = EvidenceIdentity("r", "1", "m", "1", config={"b": 2, "a": 1})
        assert a.fingerprint == b.fingerprint

    def test_the_original_instrument_may_not_refine_its_own_claim(
        self, session_factory, tmp_path: Path
    ) -> None:
        with session_factory() as session:
            detection = _seed(session, tmp_path)[0]
            candidate = _candidate_from(detection, tmp_path / "clips" / "clip-0.wav")
            same_model = EvidenceIdentity(
                refiner_id="rereader",
                refiner_version="1",
                model_id=PASS_DETECTOR_MODEL[0],
                model_version=PASS_DETECTOR_MODEL[1],
            )
            with pytest.raises(RefinementViolation, match="not new information"):
                record_refinement(
                    session,
                    candidate=candidate,
                    identity=same_model,
                    proposal=PROPOSAL,
                    authority="propose",
                )

    def test_a_newer_version_of_the_same_model_is_new_information(
        self, session_factory, tmp_path: Path
    ) -> None:
        """The rule is about re-reading, not about the model's family."""
        with session_factory() as session:
            detection = _seed(session, tmp_path)[0]
            candidate = _candidate_from(detection, None)
            newer = EvidenceIdentity(
                refiner_id="rereader",
                refiner_version="1",
                model_id=PASS_DETECTOR_MODEL[0],
                model_version="2",
            )
            row = record_refinement(
                session,
                candidate=candidate,
                identity=newer,
                proposal=PROPOSAL,
                authority="propose",
            )
            assert row is not None

    def test_the_same_evidence_cannot_be_banked_twice(
        self, session_factory, tmp_path: Path
    ) -> None:
        with session_factory() as session:
            detection = _seed(session, tmp_path)[0]
            candidate = _candidate_from(detection, None)
            first = record_refinement(
                session,
                candidate=candidate,
                identity=NEW_MODEL_IDENTITY,
                proposal=PROPOSAL,
                authority="propose",
            )
            assert first is not None
            session.flush()

            # The same refiner runs again and this time "reads" a better score.
            optimistic = RefinementProposal(
                outcome=RefinementOutcome.PROPOSED,
                basis=RefinementBasis.NEW_MODEL,
                reason="looks better this time",
                proposed_scientific_name="Myotis nattereri",
                proposed_score=0.95,
            )
            again = record_refinement(
                session,
                candidate=candidate,
                identity=NEW_MODEL_IDENTITY,
                proposal=optimistic,
                authority="propose",
            )
            assert again is None, "re-running unchanged must be idempotent, not a new claim"
            rows = session.execute(select(orm.Refinement)).scalars().all()
            assert len(rows) == 1
            assert rows[0].proposed_score == pytest.approx(0.28)

    def test_find_candidates_skips_what_this_refiner_already_examined(
        self, session_factory, tmp_path: Path
    ) -> None:
        with session_factory() as session:
            detections = _seed(session, tmp_path, count=3)
            candidate = _candidate_from(detections[0], tmp_path / "clips" / "clip-0.wav")
            record_refinement(
                session,
                candidate=candidate,
                identity=NEW_MODEL_IDENTITY,
                proposal=PROPOSAL,
                authority="propose",
            )
            session.flush()
            remaining = find_candidates(
                session, identity=NEW_MODEL_IDENTITY, groups=["bat"], limit=10
            )
            assert {item.detection_id for item in remaining} == {
                detections[1].id,
                detections[2].id,
            }


# ----------------------------------------------------------------------
# rule 2: preserve the original claim


class TestPreserveTheOriginal:
    def test_recording_a_refinement_does_not_move_the_detection_claim(
        self, session_factory, tmp_path: Path
    ) -> None:
        with session_factory() as session:
            detection = _seed(session, tmp_path)[0]
            before = (
                detection.common_name,
                detection.scientific_name,
                detection.score,
                detection.taxonomic_group,
                dict(detection.native_result),
            )
            record_refinement(
                session,
                candidate=_candidate_from(detection, None),
                identity=NEW_MODEL_IDENTITY,
                proposal=PROPOSAL,
                authority="propose",
            )
            session.flush()
            session.refresh(detection)
            assert (
                detection.common_name,
                detection.scientific_name,
                detection.score,
                detection.taxonomic_group,
                dict(detection.native_result),
            ) == before

    def test_the_refinement_row_snapshots_the_prior_verdict(
        self, session_factory, tmp_path: Path
    ) -> None:
        with session_factory() as session:
            detection = _seed(session, tmp_path)[0]
            row = record_refinement(
                session,
                candidate=_candidate_from(detection, None),
                identity=NEW_MODEL_IDENTITY,
                proposal=PROPOSAL,
                authority="propose",
            )
            assert row is not None
            assert row.original_common_name == "Bat pass"
            assert row.original_score == pytest.approx(0.62)
            assert row.original_taxonomic_group == "bat"
            assert row.proposed_scientific_name == "Myotis nattereri"

    def test_a_refiner_that_edits_the_claim_is_caught(
        self, session_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard exists for the refiner nobody has written yet.

        Simulated by making the snapshot comparison see a mutation: a future
        writer that scribbled on the detection inside ``record_refinement``
        must fail loudly rather than quietly rewrite history.
        """
        import open_observatory.refinement.store as store

        real_snapshot = store._claim_snapshot
        calls = {"n": 0}

        def _snapshot(detection: orm.Detection) -> tuple[object, ...]:
            calls["n"] += 1
            if calls["n"] == 2:  # the "after" read
                detection.common_name = "Myotis nattereri"
            return real_snapshot(detection)

        monkeypatch.setattr(store, "_claim_snapshot", _snapshot)
        with session_factory() as session:
            detection = _seed(session, tmp_path)[0]
            with pytest.raises(RefinementViolation, match="moved the original claim"):
                store.record_refinement(
                    session,
                    candidate=_candidate_from(detection, None),
                    identity=NEW_MODEL_IDENTITY,
                    proposal=PROPOSAL,
                    authority="propose",
                )

    def test_the_guard_sees_an_in_place_mutation_of_native_result(
        self, session_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A shallow snapshot would compare the same dict with itself and pass."""
        import open_observatory.refinement.store as store

        real_snapshot = store._claim_snapshot
        calls = {"n": 0}

        def _snapshot(detection: orm.Detection) -> tuple[object, ...]:
            calls["n"] += 1
            if calls["n"] == 2:
                detection.native_result["species"] = "Myotis nattereri"
            return real_snapshot(detection)

        monkeypatch.setattr(store, "_claim_snapshot", _snapshot)
        with session_factory() as session:
            detection = _seed(session, tmp_path)[0]
            with pytest.raises(RefinementViolation, match="native_result"):
                store.record_refinement(
                    session,
                    candidate=_candidate_from(detection, None),
                    identity=NEW_MODEL_IDENTITY,
                    proposal=PROPOSAL,
                    authority="propose",
                )


# ----------------------------------------------------------------------
# rule 3: distinguishable, with what changed it and when


class TestDistinguishable:
    def test_the_event_records_that_refinement_ran_at_what_version_with_what_outcome(
        self, session_factory, tmp_path: Path
    ) -> None:
        """This is exactly what the charter's retention decision needs."""
        with session_factory() as session:
            detection = _seed(session, tmp_path)[0]
            assert detection.refined_at is None
            assert detection.refinement_outcome is None

            record_refinement(
                session,
                candidate=_candidate_from(detection, None),
                identity=NEW_MODEL_IDENTITY,
                proposal=PROPOSAL,
                authority="propose",
            )
            session.flush()
            session.refresh(detection)
            assert detection.refined_at is not None
            assert detection.refinement_outcome == "proposed"
            assert detection.refinement_version == (
                "test-refiner@1/some-other-model@9"
            )

    def test_unavailable_is_not_the_same_as_no_change(
        self, session_factory, tmp_path: Path
    ) -> None:
        """A clip the refiner never read has not been examined.

        The charter's safeguard is aimed at exactly this: "the risk is not old
        data, it is data the refiner never actually saw".
        """
        assert RefinementOutcome.NO_CHANGE in EXAMINED_OUTCOMES
        assert RefinementOutcome.UNAVAILABLE not in EXAMINED_OUTCOMES
        assert RefinementOutcome.FAILED not in EXAMINED_OUTCOMES

        with session_factory() as session:
            detection = _seed(session, tmp_path, with_clip=False)[0]
            record_refinement(
                session,
                candidate=_candidate_from(detection, None),
                identity=NEW_MODEL_IDENTITY,
                proposal=RefinementProposal(
                    outcome=RefinementOutcome.UNAVAILABLE,
                    basis=RefinementBasis.NEW_MODEL,
                    reason="clip already reclaimed",
                ),
                authority="propose",
            )
            session.flush()
            session.refresh(detection)
            assert detection.refinement_outcome == "unavailable"


# ----------------------------------------------------------------------
# the propose-only ceiling


class TestProposeOnly:
    def test_a_propose_authority_refiner_cannot_apply(
        self, session_factory, tmp_path: Path
    ) -> None:
        with session_factory() as session:
            detection = _seed(session, tmp_path)[0]
            with pytest.raises(RefinementViolation, match="may not apply"):
                record_refinement(
                    session,
                    candidate=_candidate_from(detection, None),
                    identity=NEW_MODEL_IDENTITY,
                    proposal=RefinementProposal(
                        outcome=RefinementOutcome.APPLIED,
                        basis=RefinementBasis.NEW_MODEL,
                        reason="I am very sure",
                        proposed_scientific_name="Pipistrellus pygmaeus",
                        proposed_score=0.77,
                    ),
                    authority="propose",
                )

    def test_the_shipped_batdetect2_refiner_has_propose_authority(self) -> None:
        """If this ever flips, ADR-042 and the measured evidence must change first."""
        assert BatDetect2Refiner().authority == "propose"

    def test_an_unknown_authority_is_refused(self, session_factory, tmp_path: Path) -> None:
        with session_factory() as session:
            detection = _seed(session, tmp_path)[0]
            with pytest.raises(RefinementViolation, match="unknown refiner authority"):
                record_refinement(
                    session,
                    candidate=_candidate_from(detection, None),
                    identity=NEW_MODEL_IDENTITY,
                    proposal=PROPOSAL,
                    authority="whatever",
                )


# ----------------------------------------------------------------------
# candidate selection


class TestCandidateSelection:
    def test_only_native_clips_that_still_exist_are_offered(
        self, session_factory, tmp_path: Path
    ) -> None:
        with session_factory() as session:
            _seed(session, tmp_path, count=2)
            # A third detection whose only asset is an audible rendering.
            extra = _seed(session, tmp_path, count=1, with_clip=False)[0]
            asset = orm.MediaAsset(
                kind="audible_ultrasonic",
                storage_uri=str(tmp_path / "clips" / "audible.wav"),
                mime_type="audio/wav",
                sha256="1" * 64,
            )
            session.add(asset)
            session.flush()
            session.add(orm.DetectionMedia(detection_id=extra.id, media_asset_id=asset.id))
            session.flush()

            found = find_candidates(
                session, identity=NEW_MODEL_IDENTITY, groups=["bat"], limit=10
            )
            assert extra.id not in {item.detection_id for item in found}
            assert len(found) == 2

    def test_a_reclaimed_asset_is_not_offered(self, session_factory, tmp_path: Path) -> None:
        with session_factory() as session:
            _seed(session, tmp_path, count=1)
            asset = session.execute(select(orm.MediaAsset)).scalar_one()
            asset.reclaimed_at = datetime.now(UTC)
            asset.reclaim_reason = "native"
            session.flush()
            assert (
                find_candidates(session, identity=NEW_MODEL_IDENTITY, groups=["bat"], limit=10)
                == []
            )

    def test_candidates_come_oldest_first(self, session_factory, tmp_path: Path) -> None:
        """A backlog must drain from the end closest to losing its evidence."""
        with session_factory() as session:
            detections = _seed(session, tmp_path, count=4)
            found = find_candidates(
                session, identity=NEW_MODEL_IDENTITY, groups=["bat"], limit=3
            )
            assert [item.detection_id for item in found] == [d.id for d in detections[:3]]

    def test_other_taxonomic_groups_are_not_offered(
        self, session_factory, tmp_path: Path
    ) -> None:
        with session_factory() as session:
            _seed(session, tmp_path, count=1, group="bird")
            assert (
                find_candidates(session, identity=NEW_MODEL_IDENTITY, groups=["bat"], limit=10)
                == []
            )


# ----------------------------------------------------------------------
# the quiet window


class TestQuietWindow:
    @pytest.mark.parametrize(
        ("hour", "expected"),
        [(0, False), (1, True), (2, True), (3, False), (12, False), (23, False)],
    )
    def test_the_measured_window_is_half_open(self, hour: int, expected: bool) -> None:
        now = datetime(2026, 8, 9, hour, 30, tzinfo=UTC)
        assert in_quiet_window(now, 1, 3) is expected

    def test_a_window_may_wrap_midnight(self) -> None:
        assert in_quiet_window(datetime(2026, 8, 9, 23, tzinfo=UTC), 22, 2)
        assert in_quiet_window(datetime(2026, 8, 9, 1, tzinfo=UTC), 22, 2)
        assert not in_quiet_window(datetime(2026, 8, 9, 12, tzinfo=UTC), 22, 2)

    def test_a_local_time_is_converted_to_utc_before_comparing(self) -> None:
        """SETUP.md trap 9: mixing UTC and local time has flipped a conclusion here."""
        bst = datetime(2026, 8, 9, 2, 30, tzinfo=UTC).astimezone(
            __import__("zoneinfo").ZoneInfo("Europe/London")
        )
        assert bst.hour == 3  # 03:30 BST is 02:30 UTC
        assert in_quiet_window(bst, 1, 3)


# ----------------------------------------------------------------------
# the runner


class _StubRefiner:
    """Stands in for a real refiner. Deliberately has no model at all."""

    authority = "propose"
    handles_groups = frozenset({"bat"})

    def __init__(self, *, proposal: RefinementProposal | None = None, raises: bool = False) -> None:
        self.identity = NEW_MODEL_IDENTITY
        self.prepared = 0
        self.closed = 0
        self.seen: list[uuid.UUID] = []
        self._proposal = proposal or PROPOSAL
        self._raises = raises

    def prepare(self) -> None:
        self.prepared += 1

    def refine(self, candidate: RefinementCandidate) -> RefinementProposal:
        self.seen.append(candidate.detection_id)
        if self._raises:
            raise RuntimeError("model exploded")
        return self._proposal

    def close(self) -> None:
        self.closed += 1


class TestRunner:
    def test_it_refuses_to_run_outside_the_quiet_window(
        self, session_factory, tmp_path: Path
    ) -> None:
        with session_factory() as session:
            _seed(session, tmp_path, count=2)
        refiner = _StubRefiner()
        runner = RefinementRunner(
            refiner,
            session_factory=session_factory,
            clock=lambda: datetime(2026, 8, 9, 20, 0, tzinfo=UTC),
        )
        report = runner.run()
        assert report.skipped_reason is not None
        assert "quiet window" in report.skipped_reason
        assert refiner.prepared == 0, "it must not even load the model"
        assert refiner.seen == []

    def test_force_overrides_the_window(self, session_factory, tmp_path: Path) -> None:
        with session_factory() as session:
            _seed(session, tmp_path, count=2)
        refiner = _StubRefiner()
        runner = RefinementRunner(
            refiner,
            session_factory=session_factory,
            clock=lambda: datetime(2026, 8, 9, 20, 0, tzinfo=UTC),
        )
        report = runner.run(force=True)
        assert report.skipped_reason is None
        assert report.examined == 2

    def test_a_full_pass_records_one_refinement_per_event(
        self, session_factory, tmp_path: Path
    ) -> None:
        with session_factory() as session:
            _seed(session, tmp_path, count=3)
        runner = RefinementRunner(
            _StubRefiner(),
            session_factory=session_factory,
            clock=lambda: datetime(2026, 8, 9, 1, 30, tzinfo=UTC),
        )
        report = runner.run()
        assert report.outcomes == {"proposed": 3}
        assert report.complete is True
        with session_factory() as session:
            assert len(session.execute(select(orm.Refinement)).scalars().all()) == 3
            assert (
                session.execute(
                    select(orm.Detection).where(orm.Detection.refined_at.is_(None))
                )
                .scalars()
                .all()
                == []
            )

    def test_running_twice_is_idempotent(self, session_factory, tmp_path: Path) -> None:
        with session_factory() as session:
            _seed(session, tmp_path, count=2)
        clock = lambda: datetime(2026, 8, 9, 1, 30, tzinfo=UTC)  # noqa: E731
        RefinementRunner(_StubRefiner(), session_factory=session_factory, clock=clock).run()
        second = RefinementRunner(
            _StubRefiner(), session_factory=session_factory, clock=clock
        ).run()
        assert second.candidates_considered == 0
        with session_factory() as session:
            assert len(session.execute(select(orm.Refinement)).scalars().all()) == 2

    def test_dry_run_writes_nothing(self, session_factory, tmp_path: Path) -> None:
        with session_factory() as session:
            _seed(session, tmp_path, count=2)
        report = RefinementRunner(
            _StubRefiner(),
            session_factory=session_factory,
            clock=lambda: datetime(2026, 8, 9, 1, 30, tzinfo=UTC),
        ).run(dry_run=True)
        assert report.outcomes == {"proposed": 2}
        assert len(report.proposals) == 2
        with session_factory() as session:
            assert session.execute(select(orm.Refinement)).scalars().all() == []
            assert (
                session.execute(
                    select(orm.Detection).where(orm.Detection.refined_at.is_not(None))
                )
                .scalars()
                .all()
                == []
            )

    def test_one_bad_clip_does_not_end_the_pass(self, session_factory, tmp_path: Path) -> None:
        with session_factory() as session:
            _seed(session, tmp_path, count=2)
        report = RefinementRunner(
            _StubRefiner(raises=True),
            session_factory=session_factory,
            clock=lambda: datetime(2026, 8, 9, 1, 30, tzinfo=UTC),
        ).run()
        assert report.outcomes == {"failed": 2}
        assert report.examined == 0, "a failure is not an examination"

    def test_an_unavailable_refiner_is_skipped_not_silently_successful(
        self, session_factory, tmp_path: Path
    ) -> None:
        class _Missing(_StubRefiner):
            def prepare(self) -> None:
                raise RefinerUnavailable("batdetect2 is not installed")

        report = RefinementRunner(
            _Missing(),
            session_factory=session_factory,
            clock=lambda: datetime(2026, 8, 9, 1, 30, tzinfo=UTC),
        ).run()
        assert report.skipped_reason is not None
        assert "not installed" in report.skipped_reason
        assert report.examined == 0

    def test_the_item_budget_is_honoured_and_reported_incomplete(
        self, session_factory, tmp_path: Path
    ) -> None:
        with session_factory() as session:
            _seed(session, tmp_path, count=5)
        report = RefinementRunner(
            _StubRefiner(),
            session_factory=session_factory,
            max_items=2,
            clock=lambda: datetime(2026, 8, 9, 1, 30, tzinfo=UTC),
        ).run()
        assert report.examined == 2
        assert report.complete is False

    def test_the_wall_clock_budget_stops_the_pass(
        self, session_factory, tmp_path: Path
    ) -> None:
        with session_factory() as session:
            _seed(session, tmp_path, count=3)
        report = RefinementRunner(
            _StubRefiner(),
            session_factory=session_factory,
            max_seconds=-1.0,  # already spent
            clock=lambda: datetime(2026, 8, 9, 1, 30, tzinfo=UTC),
        ).run()
        assert report.examined == 0
        assert report.complete is False

    def test_a_health_event_records_that_the_refiner_ran(
        self, session_factory, tmp_path: Path
    ) -> None:
        """'The refiner has not run for three nights' must be an answerable question."""
        with session_factory() as session:
            _seed(session, tmp_path, count=1)
        report = RefinementRunner(
            _StubRefiner(),
            session_factory=session_factory,
            clock=lambda: datetime(2026, 8, 9, 1, 30, tzinfo=UTC),
        ).run()
        with session_factory() as session:
            write_health_event(session, report)
        with session_factory() as session:
            row = session.execute(
                select(orm.HealthEvent).where(orm.HealthEvent.service == "refinement")
            ).scalar_one()
            assert row.detail["outcomes"] == {"proposed": 1}
            assert row.severity == "info"


# ----------------------------------------------------------------------
# the BatDetect2 refiner itself


class _FakeApi:
    """Stands in for ``batdetect2.api``. Returns whatever predictions it is given."""

    def __init__(self, predictions: list[dict[str, Any]]) -> None:
        self._predictions = predictions
        self.rates_seen: list[int] = []

    def process_audio(self, pcm, *, samp_rate, model, config, device):
        self.rates_seen.append(samp_rate)
        return self._predictions, None, None


def _prepared_refiner(predictions: list[dict[str, Any]], **kwargs: Any) -> BatDetect2Refiner:
    refiner = BatDetect2Refiner(**kwargs)
    refiner._api = _FakeApi(predictions)
    refiner._model = object()
    refiner._config = object()
    refiner._device = object()
    refiner._model_version = "1.3.1"
    return refiner


class TestBatDetect2Refiner:
    def test_a_missing_clip_is_unavailable_not_no_change(self, tmp_path: Path) -> None:
        refiner = _prepared_refiner([])
        candidate = RefinementCandidate(
            detection_id=uuid.uuid4(),
            event_start_utc=datetime.now(UTC),
            taxonomic_group="bat",
            common_name="Bat pass",
            scientific_name=None,
            score=0.5,
            peak_frequency_hz=34_000.0,
            detector_plugin_id="ultrasonic-pass-v1",
            detector_model_id=PASS_DETECTOR_MODEL[0],
            detector_model_version=PASS_DETECTOR_MODEL[1],
            native_result={},
            clip_path=tmp_path / "gone.wav",
            clip_sample_rate=384_000,
            media_asset_id=None,
        )
        proposal = refiner.refine(candidate)
        assert proposal.outcome is RefinementOutcome.UNAVAILABLE
        assert proposal.evidence["clip_present"] is False

    def test_no_calls_found_is_no_change(self, tmp_path: Path) -> None:
        clip = tmp_path / "clip.wav"
        _write_clip(clip)
        refiner = _prepared_refiner([])
        proposal = refiner.refine(self._candidate(clip))
        assert proposal.outcome is RefinementOutcome.NO_CHANGE
        assert not proposal.carries_a_claim

    def test_a_species_is_proposed_never_applied(self, tmp_path: Path) -> None:
        clip = tmp_path / "clip.wav"
        _write_clip(clip)
        refiner = _prepared_refiner(
            [
                {"class": "Myotis nattereri", "det_prob": 0.21},
                {"class": "Myotis nattereri", "det_prob": 0.28},
                {"class": "Pipistrellus pipistrellus", "det_prob": 0.11},
            ]
        )
        proposal = refiner.refine(self._candidate(clip))
        assert proposal.outcome is RefinementOutcome.PROPOSED
        assert proposal.proposed_scientific_name == "Myotis nattereri"
        # Best call per species, not the first one seen.
        assert proposal.proposed_score == pytest.approx(0.28)
        assert proposal.evidence["needs_human_ear"] is True

    def test_the_proposal_carries_the_stations_own_peak_measurement(
        self, tmp_path: Path
    ) -> None:
        """This pairing is what exposed 0.77 P. pygmaeus on a 34 kHz call."""
        clip = tmp_path / "clip.wav"
        _write_clip(clip)
        refiner = _prepared_refiner([{"class": "Pipistrellus pygmaeus", "det_prob": 0.77}])
        proposal = refiner.refine(self._candidate(clip))
        assert proposal.evidence["our_peak_frequency_hz"] == 34_000.0
        assert "34.0 kHz" in proposal.reason
        assert "contradicted" in proposal.reason

    def test_a_confident_answer_still_carries_the_gain_caution(self, tmp_path: Path) -> None:
        clip = tmp_path / "clip.wav"
        _write_clip(clip)
        refiner = _prepared_refiner([{"class": "Pipistrellus pygmaeus", "det_prob": 0.99}])
        proposal = refiner.refine(self._candidate(clip))
        assert "gain is hot" in proposal.reason

    def test_a_low_confidence_lean_is_labelled_as_one(self, tmp_path: Path) -> None:
        clip = tmp_path / "clip.wav"
        _write_clip(clip)
        refiner = _prepared_refiner([{"class": "Myotis nattereri", "det_prob": 0.24}])
        proposal = refiner.refine(self._candidate(clip))
        assert "a lean, not an identification" in proposal.reason
        assert proposal.evidence["caution"]

    def test_species_below_the_noise_floor_are_dropped(self, tmp_path: Path) -> None:
        clip = tmp_path / "clip.wav"
        _write_clip(clip)
        refiner = _prepared_refiner(
            [
                {"class": "Myotis nattereri", "det_prob": 0.21},
                {"class": "Nyctalus noctula", "det_prob": 0.001},
            ]
        )
        proposal = refiner.refine(self._candidate(clip))
        assert proposal.evidence["distinct_species_named"] == 1

    def test_the_stations_measured_leans_survive_the_noise_floor(self, tmp_path: Path) -> None:
        """0.20-0.30 is exactly what a human ear should arbitrate, not what we hide."""
        clip = tmp_path / "clip.wav"
        _write_clip(clip)
        for prob in (0.20, 0.25, 0.30):
            refiner = _prepared_refiner([{"class": "Myotis nattereri", "det_prob": prob}])
            assert refiner.refine(self._candidate(clip)).carries_a_claim

    def test_audio_is_trimmed_and_resampled_to_batdetect2s_rate(self, tmp_path: Path) -> None:
        clip = tmp_path / "clip.wav"
        _write_clip(clip, seconds=6.0, rate=384_000)
        refiner = _prepared_refiner([], trim_s=1.5)
        proposal = refiner.refine(self._candidate(clip))
        assert proposal.evidence["classified_audio_s"] == pytest.approx(1.5, abs=1e-3)
        assert refiner._api.rates_seen == [256_000]

    def test_the_identity_changes_when_the_configuration_changes(self) -> None:
        a = BatDetect2Refiner(trim_s=1.5).identity.fingerprint
        b = BatDetect2Refiner(trim_s=3.0).identity.fingerprint
        assert a != b

    def test_prepare_raises_refiner_unavailable_when_the_library_is_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def _no_batdetect2(name: str, *args: Any, **kwargs: Any) -> Any:
            if name in ("torch", "batdetect2"):
                raise ImportError(f"no module named {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_batdetect2)
        with pytest.raises(RefinerUnavailable):
            BatDetect2Refiner().prepare()

    def _candidate(self, clip: Path) -> RefinementCandidate:
        return RefinementCandidate(
            detection_id=uuid.uuid4(),
            event_start_utc=datetime.now(UTC),
            taxonomic_group="bat",
            common_name="Bat pass",
            scientific_name=None,
            score=0.62,
            peak_frequency_hz=34_000.0,
            detector_plugin_id="ultrasonic-pass-v1",
            detector_model_id=PASS_DETECTOR_MODEL[0],
            detector_model_version=PASS_DETECTOR_MODEL[1],
            native_result={"peak_snr_db": 21.4, "pulse_count": 7},
            clip_path=clip,
            clip_sample_rate=384_000,
            media_asset_id=None,
        )


# ----------------------------------------------------------------------
# the gap this deliberately does not close


class TestRetentionGap:
    def test_retention_still_deletes_on_age_alone(self, session_factory, tmp_path: Path) -> None:
        """Documented, not fixed. See ADR-042 "What this does not do".

        The charter asks that deletion require "refinement has run". The schema
        now supports it; ``retention.py`` does not yet consult it, and changing a
        live station's deletion policy is the operator's call, not a side effect
        of adding a column. This test pins the current behaviour so the day
        somebody does change it, they change this too.
        """
        from open_observatory.retention import RetentionSweeper

        with session_factory() as session:
            detection = _seed(session, tmp_path, count=1)[0]
            detection.event_start_utc = datetime.now(UTC) - timedelta(days=200)
            assert detection.refined_at is None

        sweeper = RetentionSweeper(
            clip_dir=tmp_path / "clips",
            session_factory=session_factory,
            watermark_ratio=1.1,  # never trip the disk safety valve in a test
        )
        report = sweeper.sweep(dry_run=True)
        assert report.total_deleted > 0, (
            "retention currently deletes on age alone, including evidence no refiner "
            "has ever examined -- the gap ADR-042 records"
        )
