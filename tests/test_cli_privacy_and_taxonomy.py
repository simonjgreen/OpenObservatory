"""``oo detections reconcile-taxonomy`` and ``oo clips purge-human-audio`` (ADR-049).

Same shape as ``test_cli_detections.py``: dry-run by default, ``--apply``
alone still asks, ``--apply --yes`` writes, and the row survives whatever
happens.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from typer.testing import CliRunner

from open_observatory.cli import app
from open_observatory.config import set_settings
from open_observatory.db import models as orm
from open_observatory.db.session import ensure_schema_at_head, init_engine, session_scope

# mix_stderr=False: stdout carries the JSON document and nothing else, which
# is the contract emit_json exists to keep. Mixing the streams hid a real
# defect -- `oo refine status --json` printed through rich and nobody saw,
# because the logger used to hold the pre-redirect stderr and its output
# never reached the captured stdout at all.
runner = CliRunner(mix_stderr=False)

BASE = datetime(2026, 8, 8, 18, 55, tzinfo=UTC)


def _seed(settings, *, with_clips: bool = False) -> dict[str, uuid.UUID]:
    init_engine(settings)
    ensure_schema_at_head()
    clip_dir = Path(settings.clip_dir)
    clip_dir.mkdir(parents=True, exist_ok=True)
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
        for name, label in (
            ("Engine", "Engine_Engine"),
            ("Human vocal", "Human vocal_Human vocal"),
            ("Tawny Owl", "Strix aluco_Tawny Owl"),
        ):
            scientific = label.split("_", 1)[0]
            detection_id = uuid.uuid4()
            ids[name] = detection_id
            session.add(
                orm.Detection(
                    id=detection_id,
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
                    canonical_taxon_id=f"sci:{scientific.lower().replace(' ', '_')}",
                    rank="species",
                    taxonomic_group="bird",
                    score=0.98,
                    native_result={"detector": "birdnet-v2.4", "week": 30},
                )
            )
            if with_clips:
                asset_id = uuid.uuid4()
                path = clip_dir / f"{name.replace(' ', '-')}.wav"
                path.write_bytes(b"RIFF" + b"\0" * 1020)
                session.add(
                    orm.MediaAsset(
                        id=asset_id,
                        kind="playback",
                        storage_uri=str(path),
                        mime_type="audio/wav",
                        stream_id=stream_id,
                        source_start_frame=0,
                        source_end_frame=1,
                        sample_rate=48000,
                        byte_length=path.stat().st_size,
                        sha256="0" * 64,
                        created_at=BASE,
                        detail={},
                    )
                )
                session.add(
                    orm.DetectionMedia(
                        detection_id=detection_id, media_asset_id=asset_id, role="evidence"
                    )
                )
    return ids


class TestReconcileTaxonomy:
    def test_dry_run_reports_but_changes_nothing(self, settings) -> None:
        set_settings(settings)
        ids = _seed(settings)

        result = runner.invoke(app, ["detections", "reconcile-taxonomy"])

        assert result.exit_code == 0, result.output
        assert "Engine" in result.output
        assert "Dry run only" in result.output
        with session_scope() as session:
            assert session.get(orm.Detection, ids["Engine"]).rank == "species"

    def test_json_output_is_parseable(self, settings) -> None:
        set_settings(settings)
        _seed(settings)

        result = runner.invoke(app, ["detections", "reconcile-taxonomy", "--json"])

        assert result.exit_code == 0, result.output
        # `CliRunner` folds stderr into stdout, and the dry-run notice goes to
        # stderr on purpose (see `cli.notice` and `test_cli_json_output.py`),
        # so decode the leading document rather than the whole capture. The
        # property under test is that the JSON comes first and is complete.
        payload, _end = json.JSONDecoder().raw_decode(result.stdout.lstrip())
        assert {item["common_name"] for item in payload} == {"Engine", "Human vocal"}
        assert all(item["corrected_taxonomic_group"] == "acoustic_event" for item in payload)

    def test_apply_without_yes_still_asks(self, settings) -> None:
        set_settings(settings)
        ids = _seed(settings)

        result = runner.invoke(app, ["detections", "reconcile-taxonomy", "--apply"], input="n\n")

        assert result.exit_code != 0
        with session_scope() as session:
            assert session.get(orm.Detection, ids["Engine"]).rank == "species"

    def test_apply_corrects_and_keeps_the_row(self, settings) -> None:
        set_settings(settings)
        ids = _seed(settings)

        result = runner.invoke(app, ["detections", "reconcile-taxonomy", "--apply", "--yes"])

        assert result.exit_code == 0, result.output
        with session_scope() as session:
            engine = session.get(orm.Detection, ids["Engine"])
            assert engine is not None
            assert engine.rank is None
            assert engine.taxonomic_group == "acoustic_event"
            assert engine.common_name == "Engine"
            owl = session.get(orm.Detection, ids["Tawny Owl"])
            assert owl.rank == "species"
            assert owl.taxonomic_group == "bird"

    def test_a_clean_database_says_so(self, settings) -> None:
        set_settings(settings)
        init_engine(settings)
        ensure_schema_at_head()

        result = runner.invoke(app, ["detections", "reconcile-taxonomy"])

        assert result.exit_code == 0, result.output
        assert "No detections are recording a sound as a species" in result.output


class TestPurgeHumanAudio:
    def test_dry_run_lists_without_deleting(self, settings) -> None:
        set_settings(settings)
        _seed(settings, with_clips=True)

        result = runner.invoke(app, ["clips", "purge-human-audio"])

        assert result.exit_code == 0, result.output
        assert "Human vocal" in result.output
        assert "Dry run only" in result.output
        assert (Path(settings.clip_dir) / "Human-vocal.wav").exists()

    def test_apply_without_yes_still_asks(self, settings) -> None:
        set_settings(settings)
        _seed(settings, with_clips=True)

        result = runner.invoke(app, ["clips", "purge-human-audio", "--apply"], input="n\n")

        assert result.exit_code != 0
        assert (Path(settings.clip_dir) / "Human-vocal.wav").exists()

    def test_apply_deletes_the_audio_and_keeps_the_detection(self, settings) -> None:
        set_settings(settings)
        ids = _seed(settings, with_clips=True)

        result = runner.invoke(app, ["clips", "purge-human-audio", "--apply", "--yes"])

        assert result.exit_code == 0, result.output
        assert not (Path(settings.clip_dir) / "Human-vocal.wav").exists()
        assert (Path(settings.clip_dir) / "Tawny-Owl.wav").exists()
        with session_scope() as session:
            assert session.get(orm.Detection, ids["Human vocal"]) is not None

    def test_nothing_stored_says_so(self, settings) -> None:
        set_settings(settings)
        init_engine(settings)
        ensure_schema_at_head()

        result = runner.invoke(app, ["clips", "purge-human-audio"])

        assert result.exit_code == 0, result.output
        assert "No human-audio evidence clips are stored" in result.output
