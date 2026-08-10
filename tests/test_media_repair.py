"""Tests for reconciling rows that claim evidence the disk does not have (ADR-057).

The defect these are written against: 8,067 of 48,941 live ``media_asset``
rows on the station (16.5%, 20.59 GB) had ``reclaimed_at IS NULL`` -- the
database's assertion that the clip exists -- while the file had been unlinked
by ``ClipManager.enforce_retention``, which never touches the database. The
storage panel counted the bytes, retention's budget counted them as
reclaimable, the API offered a play button for them, and the refinement runner
drew every candidate from them.

Two properties matter more than the plumbing and are asserted hardest:

* **Nothing is destroyed.** No file (there is none), no ``media_asset`` row, no
  ``detection`` row, no association, and no previously-recorded reclaim reason.
* **"Missing" is not a retention tier.** A clip aged out by policy and a clip
  that vanished are different facts, and the reconciliation must not flatten
  them into each other -- that is the whole reason it is safe to run at all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope
from open_observatory.media_repair import (
    DETAIL_KEY,
    MISSING_REASON,
    apply_missing_reconciliation,
    find_missing_assets,
)
from open_observatory.retention import RetentionSweeper

FIXED_NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(settings):
    init_engine(settings)
    create_all()
    return settings


@pytest.fixture
def seeded(db):
    """One detection, four assets: two on disk, two whose files are removed.

    Mirrors the live shape -- a native clip, its playback rendering and two
    ultrasonic derivatives share a detection, and the filesystem sweep that
    caused this took them by mtime, not by row.
    """
    station_id, detector_id, detection_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    present: dict[str, uuid.UUID] = {}
    absent: dict[str, uuid.UUID] = {}
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
        session.add(
            orm.Detection(
                id=detection_id,
                station_id=station_id,
                detector_id=detector_id,
                stream_id=uuid.uuid4(),
                window_id=uuid.uuid4(),
                event_start_utc=FIXED_NOW - timedelta(days=6),
                event_end_utc=FIXED_NOW - timedelta(days=6) + timedelta(seconds=3),
                source_start_frame=0,
                source_end_frame=1,
                detector_label="Spotted Crake",
                common_name="Spotted Crake",
                scientific_name="Porzana porzana",
                taxonomic_group="bird",
                score=0.7,
                native_result={},
            )
        )
        day = db.clip_dir / "2026-08-04"
        day.mkdir(parents=True, exist_ok=True)
        for kind, on_disk in (
            ("evidence_native", True),
            ("playback", True),
            ("audible_ultrasonic", False),
            ("evidence_native", False),
        ):
            asset_id = uuid.uuid4()
            path = day / f"{asset_id}_{kind}.wav"
            if on_disk:
                path.write_bytes(b"\x00" * 2048)
            session.add(
                orm.MediaAsset(
                    id=asset_id,
                    kind=kind,
                    storage_uri=str(path),
                    mime_type="audio/wav",
                    byte_length=2048,
                    sha256="0" * 64,
                    created_at=FIXED_NOW - timedelta(days=6),
                )
            )
            session.add(
                orm.DetectionMedia(
                    detection_id=detection_id, media_asset_id=asset_id, role="evidence"
                )
            )
            (present if on_disk else absent)[f"{kind}:{on_disk}"] = asset_id
    return db, detection_id, list(present.values()), list(absent.values())


class TestFinding:
    def test_finds_only_rows_whose_file_is_gone(self, seeded) -> None:
        _db, _detection_id, present, absent = seeded
        with session_scope() as session:
            report = find_missing_assets(session)
        assert report.scanned == 4
        assert report.missing == 2
        assert report.missing_bytes == 4096
        found = {uuid.UUID(str(item.asset_id)) for item in report.findings}
        assert found == set(absent)
        assert found.isdisjoint(set(present))

    def test_finding_carries_the_detection_it_belonged_to(self, seeded) -> None:
        """Without this the operator gets a list of UUIDs and no idea what was lost."""
        _db, detection_id, _present, _absent = seeded
        with session_scope() as session:
            report = find_missing_assets(session)
        for item in report.findings:
            assert item.detection_id == detection_id
            assert item.common_name == "Spotted Crake"
            assert item.to_dict()["reclaim_reason"] == MISSING_REASON

    def test_finding_writes_nothing(self, seeded) -> None:
        _db, _detection_id, _present, absent = seeded
        with session_scope() as session:
            find_missing_assets(session)
        with session_scope() as session:
            for asset_id in absent:
                assert session.get(orm.MediaAsset, asset_id).reclaimed_at is None

    def test_already_reclaimed_rows_are_not_findings(self, seeded) -> None:
        """A clip the retention sweeper aged out is not a fault to report."""
        db, _detection_id, _present, absent = seeded
        with session_scope() as session:
            row = session.get(orm.MediaAsset, absent[0])
            row.reclaimed_at = FIXED_NOW
            row.reclaim_reason = "native"
        with session_scope() as session:
            report = find_missing_assets(session)
        assert report.missing == 1
        assert report.scanned == 3

    def test_held_for_review_is_reported(self, db, seeded) -> None:
        """The live station is holding 61 Spotted Crake detections whose clips are
        all gone, so the hold cannot be satisfied. An operator must be told that
        the thing they asked to listen to is what they are about to write off."""
        _db, detection_id, _present, _absent = seeded
        with session_scope() as session:
            session.add(
                orm.Review(
                    id=uuid.uuid4(),
                    detection_id=detection_id,
                    status="held",
                    actor="operator",
                    created_at=FIXED_NOW,
                )
            )
        with session_scope() as session:
            report = find_missing_assets(session)
        assert all(item.held_for_review for item in report.findings)
        assert report.to_dict()["held_for_review"] == 2


class TestApply:
    def test_marks_reclaimed_with_a_reason_that_is_not_a_tier(self, seeded) -> None:
        _db, _detection_id, _present, absent = seeded
        with session_scope() as session:
            report = find_missing_assets(session)
            for item in report.findings:
                apply_missing_reconciliation(session, item, now=FIXED_NOW)
        with session_scope() as session:
            for asset_id in absent:
                row = session.get(orm.MediaAsset, asset_id)
                assert row.reclaimed_at is not None
                assert row.reclaim_reason == MISSING_REASON
                # Not "native"/"exemplar_only"/"expired"/"watermark": no policy
                # decided to give this clip up, and recording that it did would
                # be the system lying about its own history.
                assert row.reclaim_reason not in {
                    "native",
                    "exemplar_only",
                    "expired",
                    "watermark",
                }

    def test_preserves_what_the_row_used_to_claim(self, seeded) -> None:
        _db, _detection_id, _present, absent = seeded
        with session_scope() as session:
            report = find_missing_assets(session)
            uris = {item.asset_id: item.storage_uri for item in report.findings}
            for item in report.findings:
                apply_missing_reconciliation(session, item, now=FIXED_NOW)
        with session_scope() as session:
            for asset_id in absent:
                block = session.get(orm.MediaAsset, asset_id).detail[DETAIL_KEY]
                assert block["reconciled"] is True
                assert block["claimed_byte_length"] == 2048
                assert block["claimed_storage_uri"] == uris[asset_id]
                assert block["file_present"] is False

    def test_never_deletes_a_row_a_file_or_a_detection(self, seeded) -> None:
        db, detection_id, present, absent = seeded
        present_paths = []
        with session_scope() as session:
            present_paths = [
                Path(session.get(orm.MediaAsset, a).storage_uri) for a in present
            ]
            report = find_missing_assets(session)
            for item in report.findings:
                apply_missing_reconciliation(session, item, now=FIXED_NOW)
        with session_scope() as session:
            assert session.get(orm.Detection, detection_id) is not None
            assert session.get(orm.Detection, detection_id).common_name == "Spotted Crake"
            assert session.query(orm.MediaAsset).count() == 4
            assert session.query(orm.DetectionMedia).count() == 4
            for asset_id in absent:
                assert session.get(orm.MediaAsset, asset_id) is not None
        for path in present_paths:
            assert path.exists()

    def test_is_idempotent(self, seeded) -> None:
        """A second run must find nothing and must not overwrite the first
        run's record of what the row claimed."""
        _db, _detection_id, _present, _absent = seeded
        with session_scope() as session:
            first = find_missing_assets(session)
            for item in first.findings:
                apply_missing_reconciliation(session, item, now=FIXED_NOW)
        with session_scope() as session:
            second = find_missing_assets(session)
        assert first.missing == 2
        assert second.missing == 0

    def test_leaves_a_policy_reclaim_reason_alone(self, seeded) -> None:
        db, _detection_id, _present, absent = seeded
        with session_scope() as session:
            row = session.get(orm.MediaAsset, absent[0])
            row.reclaimed_at = FIXED_NOW
            row.reclaim_reason = "expired"
        with session_scope() as session:
            report = find_missing_assets(session)
            for item in report.findings:
                apply_missing_reconciliation(session, item, now=FIXED_NOW)
        with session_scope() as session:
            assert session.get(orm.MediaAsset, absent[0]).reclaim_reason == "expired"


class TestAccountingIsCorrectedAfterwards:
    def test_reclaiming_removes_the_bytes_from_the_live_total(self, seeded) -> None:
        """The point of the exercise: the sums every consumer computes over
        `reclaimed_at IS NULL` stop counting bytes that are not there."""
        _db, _detection_id, _present, _absent = seeded

        def live_bytes() -> int:
            with session_scope() as session:
                rows = (
                    session.query(orm.MediaAsset)
                    .filter(orm.MediaAsset.reclaimed_at.is_(None))
                    .all()
                )
                return sum(r.byte_length for r in rows)

        assert live_bytes() == 8192  # 4 rows x 2048, half of it fictional
        with session_scope() as session:
            report = find_missing_assets(session)
            for item in report.findings:
                apply_missing_reconciliation(session, item, now=FIXED_NOW)
        assert live_bytes() == 4096  # what is actually on the disk


class TestRollingAudit:
    """`RetentionSweeper.audit_missing_files` -- the recurrence check (ADR-057).

    It must find the same rows the manual command does, without ever walking
    the clip tree and without statting the whole table on one call: capture
    always wins, and ADR-033 measured what sustained work on this thread costs.
    """

    def _sweeper(self, db, **kw) -> RetentionSweeper:
        return RetentionSweeper(
            clip_dir=db.clip_dir,
            session_factory=session_scope,
            clock=lambda: FIXED_NOW,
            **kw,
        )

    def test_a_completed_pass_counts_every_missing_row_exactly(self, seeded) -> None:
        db, _detection_id, _present, _absent = seeded
        sweeper = self._sweeper(db, batch_size=100)
        sweeper.audit_missing_files()
        assert sweeper.audit_passes == 1
        assert sweeper.last_pass_scanned == 4
        assert sweeper.known_missing == 2
        assert sweeper.known_missing_bytes == 4096

    def test_partial_passes_are_labelled_as_partial(self, seeded) -> None:
        """A sample of 3% finding zero is not the claim "nothing is missing",
        and `exact`/`passes_completed` is what keeps those apart."""
        db, _detection_id, _present, _absent = seeded
        sweeper = self._sweeper(db, batch_size=1)
        sweeper.audit_missing_files()
        assert sweeper.audit_passes == 0
        assert sweeper.audit_snapshot()["passes_completed"] == 0
        # ... and it converges once enough slices have run.
        for _ in range(6):
            sweeper.audit_missing_files()
        assert sweeper.audit_passes >= 1
        assert sweeper.known_missing == 2

    def test_the_cursor_does_not_skip_rows_sharing_a_timestamp(self, seeded) -> None:
        """Every asset here has an identical `created_at`, which is what four
        clips written for one detection look like. A cursor on `created_at`
        alone would read one row and then skip the rest forever."""
        db, _detection_id, _present, _absent = seeded
        sweeper = self._sweeper(db, batch_size=2)
        sweeper.audit_missing_files()
        sweeper.audit_missing_files()
        sweeper.audit_missing_files()
        assert sweeper.last_pass_scanned == 4
        assert sweeper.known_missing == 2

    def test_audit_marks_nothing(self, seeded) -> None:
        db, _detection_id, _present, absent = seeded
        self._sweeper(db, batch_size=100).audit_missing_files()
        with session_scope() as session:
            for asset_id in absent:
                assert session.get(orm.MediaAsset, asset_id).reclaimed_at is None

    def test_audit_stops_reporting_once_reconciled(self, seeded) -> None:
        db, _detection_id, _present, _absent = seeded
        with session_scope() as session:
            report = find_missing_assets(session)
            for item in report.findings:
                apply_missing_reconciliation(session, item, now=FIXED_NOW)
        sweeper = self._sweeper(db, batch_size=100)
        sweeper.audit_missing_files()
        assert sweeper.known_missing == 0
        assert sweeper.last_pass_scanned == 2

    def test_snapshot_exposes_the_audit_to_health_and_metrics(self, seeded) -> None:
        db, _detection_id, _present, _absent = seeded
        sweeper = self._sweeper(db, batch_size=100)
        sweeper.audit_missing_files()
        snapshot = sweeper.snapshot()
        assert snapshot["known_missing"] == 2
        assert snapshot["known_missing_bytes"] == 4096
        assert snapshot["missing_audit"]["passes_completed"] == 1
