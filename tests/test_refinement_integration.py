"""End-to-end: ``oo refine run`` against a real database, real clips, real migrations.

``tests/test_refinement.py`` covers the rules unit by unit. This file runs the
actual operator command the systemd timer runs, over a database built by
``alembic upgrade head`` (not ``create_all``, so the migration is on the path a
station will really take), with real WAV files on disk.

BatDetect2 itself is stubbed at the library boundary — its code and weights are
CC-BY-NC-4.0 and are never installed here (ADR-006, ADR-017) — but everything
between the CLI and that boundary is the shipped code: settings resolution,
engine setup, candidate selection, trimming, resampling through the project's own
soxr path, the store's rule enforcement, the health event, and ``oo refine
status``.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from open_observatory.cli import app
from open_observatory.config import Settings, set_settings
from open_observatory.db import models as orm

REPO_ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


@pytest.fixture()
def station(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A migrated database with three bat passes and their native clips."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "openobservatory.sqlite"
    dsn = f"sqlite+pysqlite:///{db_path}"

    monkeypatch.setenv("OO_DATABASE_DSN", dsn)
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")

    engine = sa.create_engine(dsn, future=True)
    clip_dir = data_dir / "clips"
    clip_dir.mkdir()

    ids: list[uuid.UUID] = []
    with Session(engine) as session:
        st = orm.Station(name="Test")
        detector = orm.Detector(
            plugin_id="ultrasonic-pass-v1",
            plugin_version="1",
            model_id="ultrasonic-pass",
            model_version="1",
            claim="detects echolocation passes, not species",
        )
        session.add_all([st, detector])
        session.flush()
        for index in range(3):
            clip = clip_dir / f"pass-{index}.wav"
            pcm = np.zeros(384_000 * 2, dtype="float32")
            pcm[384_000] = 0.8
            sf.write(str(clip), pcm, 384_000, subtype="FLOAT")
            asset = orm.MediaAsset(
                kind="evidence_native",
                storage_uri=str(clip),
                mime_type="audio/wav",
                sample_rate=384_000,
                byte_length=clip.stat().st_size,
                sha256=f"{index:064d}",
            )
            detection = orm.Detection(
                station_id=st.id,
                detector_id=detector.id,
                stream_id=uuid.uuid4(),
                window_id=uuid.uuid4(),
                event_start_utc=datetime(2026, 8, 5, 22, index, tzinfo=UTC),
                event_end_utc=datetime(2026, 8, 5, 22, index, 1, tzinfo=UTC),
                source_start_frame=0,
                source_end_frame=768_000,
                common_name="Bat pass",
                taxonomic_group="bat",
                score=0.55 + index / 100,
                peak_frequency_hz=34_000.0,
                native_result={"peak_snr_db": 22.0, "pulse_count": 6},
            )
            session.add_all([asset, detection])
            session.flush()
            session.add(
                orm.DetectionMedia(detection_id=detection.id, media_asset_id=asset.id)
            )
            ids.append(detection.id)
        session.commit()

    settings = Settings(data_dir=data_dir, database_dsn=dsn, log_json=True)
    set_settings(settings)

    # Reset the module-global engine between tests, so each gets its own file.
    import open_observatory.db.session as db_session

    db_session._engine = None
    db_session._session_factory = None

    yield {"engine": engine, "detection_ids": ids, "data_dir": data_dir, "dsn": dsn}

    engine.dispose()
    db_session._engine = None
    db_session._session_factory = None


@pytest.fixture()
def stub_batdetect2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the CC-BY-NC-4.0 library at its own boundary."""
    from open_observatory.refinement.batdetect2 import BatDetect2Refiner

    class _Api:
        @staticmethod
        def process_audio(pcm, *, samp_rate, model, config, device):
            return (
                [
                    {"class": "Myotis nattereri", "det_prob": 0.26},
                    {"class": "Myotis daubentonii", "det_prob": 0.19},
                ],
                None,
                None,
            )

    def _prepare(self: BatDetect2Refiner) -> None:
        self._api = _Api()
        self._model = object()
        self._config = object()
        self._device = object()
        self._model_version = "1.3.1"

    monkeypatch.setattr(BatDetect2Refiner, "prepare", _prepare)


#: rich's ``print_json`` writes ANSI colour codes even under CliRunner.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _json(stdout: str) -> dict[str, Any]:
    return json.loads(_ANSI.sub("", stdout))


def _rows(engine: sa.Engine, model: Any) -> list[Any]:
    with Session(engine) as session:
        return list(session.execute(sa.select(model)).scalars().all())


class TestRefineRunCommand:
    def test_it_refuses_outside_the_quiet_window(self, station, stub_batdetect2) -> None:
        result = runner.invoke(app, ["refine", "run"])
        assert result.exit_code == 0
        # Either it is genuinely outside 01:00-03:00 UTC when the suite runs, in
        # which case it says so and writes nothing, or it is inside, in which
        # case it runs. Both are correct; what must never happen is a silent
        # nothing. Assert on the observable consequence instead of the clock.
        refinements = _rows(station["engine"], orm.Refinement)
        from open_observatory.refinement.runner import in_quiet_window

        if in_quiet_window(datetime.now(UTC), 1, 3):
            assert len(refinements) == 3
        else:
            assert refinements == []
            assert "quiet window" in result.stdout

    def test_a_forced_dry_run_classifies_and_writes_nothing(
        self, station, stub_batdetect2
    ) -> None:
        result = runner.invoke(app, ["refine", "run", "--force", "--dry-run"])
        assert result.exit_code == 0, result.stdout
        assert "DRY RUN" in result.stdout
        assert "Myotis nattereri" in result.stdout
        assert _rows(station["engine"], orm.Refinement) == []
        with Session(station["engine"]) as session:
            assert (
                session.execute(
                    sa.select(orm.Detection).where(orm.Detection.refined_at.is_not(None))
                )
                .scalars()
                .all()
                == []
            )

    def test_a_forced_run_records_proposals_and_stamps_the_events(
        self, station, stub_batdetect2
    ) -> None:
        result = runner.invoke(app, ["refine", "run", "--force"])
        assert result.exit_code == 0, result.stdout

        refinements = _rows(station["engine"], orm.Refinement)
        assert len(refinements) == 3
        for row in refinements:
            assert row.outcome == "proposed"
            assert row.applied is False
            assert row.basis == "new_model"
            assert row.proposed_scientific_name == "Myotis nattereri"
            assert row.proposed_score == pytest.approx(0.26)
            # Rule 2: the prior verdict, verbatim.
            assert row.original_common_name == "Bat pass"
            assert row.original_taxonomic_group == "bat"
            # The evidence a human needs to check the proposal against physics.
            assert row.evidence["our_peak_frequency_hz"] == 34_000.0
            assert row.evidence["needs_human_ear"] is True
            assert row.evidence["identity"]["model_id"] == "batdetect2"
            assert row.evidence["classified_audio_s"] == pytest.approx(1.5, abs=1e-3)

        with Session(station["engine"]) as session:
            detections = session.execute(sa.select(orm.Detection)).scalars().all()
            for detection in detections:
                # Rule 3: the event says refinement ran, at what version, with
                # what outcome -- what the charter's retention decision needs.
                assert detection.refined_at is not None
                assert detection.refinement_outcome == "proposed"
                assert "batdetect2@1.3.1" in detection.refinement_version
                # Rule 2 again, from the other side: nothing about the claim moved.
                assert detection.common_name == "Bat pass"
                assert detection.scientific_name is None
                assert detection.taxonomic_group == "bat"

    def test_a_second_run_adds_nothing(self, station, stub_batdetect2) -> None:
        runner.invoke(app, ["refine", "run", "--force"])
        result = runner.invoke(app, ["refine", "run", "--force"])
        assert result.exit_code == 0, result.stdout
        assert len(_rows(station["engine"], orm.Refinement)) == 3

    def test_a_reclaimed_clip_is_recorded_as_unavailable_not_no_change(
        self, station, stub_batdetect2
    ) -> None:
        """The charter's whole worry: data the refiner never actually saw."""
        with Session(station["engine"]) as session:
            asset = session.execute(sa.select(orm.MediaAsset)).scalars().first()
            Path(asset.storage_uri).unlink()
            session.commit()

        result = runner.invoke(app, ["refine", "run", "--force"])
        assert result.exit_code == 0, result.stdout
        outcomes = {row.outcome for row in _rows(station["engine"], orm.Refinement)}
        assert outcomes == {"proposed", "unavailable"}

    def test_the_run_is_recorded_as_a_health_event(self, station, stub_batdetect2) -> None:
        runner.invoke(app, ["refine", "run", "--force"])
        events = [
            row for row in _rows(station["engine"], orm.HealthEvent) if row.service == "refinement"
        ]
        assert len(events) == 1
        assert events[0].detail["outcomes"] == {"proposed": 3}
        assert events[0].detail["examined"] == 3

    def test_disabled_by_configuration_does_nothing(self, station, stub_batdetect2) -> None:
        settings = Settings(
            data_dir=station["data_dir"],
            database_dsn=station["dsn"],
            refinement_enabled=False,
        )
        set_settings(settings)
        result = runner.invoke(app, ["refine", "run", "--force"])
        assert result.exit_code == 0
        assert _rows(station["engine"], orm.Refinement) == []


class TestRefineStatusCommand:
    def test_it_reports_what_has_never_been_examined(self, station, stub_batdetect2) -> None:
        result = runner.invoke(app, ["refine", "status", "--json"])
        assert result.exit_code == 0, result.stdout
        payload = _json(result.stdout)
        assert payload["bat_detections_never_examined"] == 3
        assert payload["last_run_at"] is None
        assert payload["unresolved_proposals"] == []

    def test_after_a_run_it_lists_the_unresolved_proposals(
        self, station, stub_batdetect2
    ) -> None:
        runner.invoke(app, ["refine", "run", "--force"])
        result = runner.invoke(app, ["refine", "status"])
        assert result.exit_code == 0, result.stdout
        assert "Myotis nattereri" in result.stdout
        assert "awaiting a human ear" in result.stdout


class TestRetentionInteraction:
    def test_retention_would_still_delete_an_unexamined_clip(self, station) -> None:
        """Pins the gap ADR-045 records rather than closes. See its "What this does not do"."""
        from open_observatory.db.session import init_engine, session_scope
        from open_observatory.retention import RetentionSweeper

        with Session(station["engine"]) as session:
            for detection in session.execute(sa.select(orm.Detection)).scalars().all():
                detection.event_start_utc = datetime.now(UTC) - timedelta(days=200)
            session.commit()

        init_engine(Settings(data_dir=station["data_dir"], database_dsn=station["dsn"]))
        sweeper = RetentionSweeper(
            clip_dir=station["data_dir"] / "clips",
            session_factory=session_scope,
            watermark_ratio=1.1,
        )
        report = sweeper.sweep(dry_run=True)
        assert report.total_deleted > 0
        with Session(station["engine"]) as session:
            unexamined = (
                session.execute(
                    sa.select(sa.func.count()).select_from(orm.Detection).where(
                        orm.Detection.refined_at.is_(None)
                    )
                )
            ).scalar_one()
        assert unexamined == 3, (
            "retention deletes on age alone; these three clips would go without any "
            "refiner having examined them once"
        )
