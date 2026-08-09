"""Purging stored evidence of human speech (ADR-049).

Real WAV-sized files on disk, real `media_asset` rows, and an assertion that
what survives a purge is the *record* and what does not is the *audio*.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from open_observatory import privacy
from open_observatory.db import models as orm
from open_observatory.db.session import create_all, init_engine, session_scope

BASE = datetime(2026, 8, 8, 18, 55, tzinfo=UTC)

#: (common name, label, asset kinds)
SEED = [
    ("Human vocal", "Human vocal_Human vocal", ("evidence_native", "playback")),
    ("Dog", "Dog_Dog", ("playback",)),
    ("Tawny Owl", "Strix aluco_Tawny Owl", ("playback",)),
]


def _seed(settings) -> dict[str, list[uuid.UUID]]:
    init_engine(settings)
    create_all()
    clip_dir = Path(settings.clip_dir)
    clip_dir.mkdir(parents=True, exist_ok=True)
    station_id = uuid.uuid4()
    stream_id = uuid.uuid4()
    detector_id = uuid.uuid4()
    assets: dict[str, list[uuid.UUID]] = {}

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
        for name, label, kinds in SEED:
            detection_id = uuid.uuid4()
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
                    score=0.98,
                    taxonomic_group="bird",
                    native_result={},
                )
            )
            assets[name] = []
            for kind in kinds:
                asset_id = uuid.uuid4()
                path = clip_dir / f"{name.replace(' ', '-')}-{kind}.wav"
                path.write_bytes(b"RIFF" + b"\0" * 1020)
                session.add(
                    orm.MediaAsset(
                        id=asset_id,
                        kind=kind,
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
                assets[name].append(asset_id)
    return assets


class TestFindHumanAudioAssets:
    def test_selects_only_human_sound(self, settings) -> None:
        _seed(settings)
        with session_scope() as session:
            items = privacy.find_human_audio_assets(session)
        assert {item.common_name for item in items} == {"Human vocal"}
        assert len(items) == 2  # native + playback derivative
        assert all(item.existed_on_disk for item in items)


class TestPurge:
    def test_dry_run_deletes_nothing(self, settings) -> None:
        _seed(settings)
        report = privacy.purge_human_audio(session_scope, dry_run=True)
        assert report.dry_run is True
        assert len(report.items) == 2
        assert report.deleted == 0
        for item in report.items:
            assert Path(item.path).exists()
        with session_scope() as session:
            asset = session.get(orm.MediaAsset, report.items[0].asset_id)
            assert asset.reclaimed_at is None

    def test_apply_removes_the_audio_and_marks_the_row(self, settings) -> None:
        assets = _seed(settings)
        report = privacy.purge_human_audio(session_scope, dry_run=False)
        assert report.deleted == 2
        assert report.bytes_reclaimed > 0
        assert report.failed == 0
        for item in report.items:
            assert not Path(item.path).exists()
        with session_scope() as session:
            for asset_id in assets["Human vocal"]:
                asset = session.get(orm.MediaAsset, asset_id)
                # The row survives, so /api/v1/media/{id} keeps answering 410
                # rather than 500 -- ADR-026's shape, a new reason for it.
                assert asset is not None
                assert asset.reclaimed_at is not None
                assert asset.reclaim_reason == privacy.RECLAIM_REASON

    def test_the_detection_row_survives_the_purge(self, settings) -> None:
        """The charter withdraws records; it deletes bytes. Not the reverse."""
        _seed(settings)
        privacy.purge_human_audio(session_scope, dry_run=False)
        with session_scope() as session:
            rows = session.query(orm.Detection).all()
            names = {row.common_name for row in rows}
        assert names == {"Human vocal", "Dog", "Tawny Owl"}

    def test_other_species_keep_their_evidence(self, settings) -> None:
        assets = _seed(settings)
        privacy.purge_human_audio(session_scope, dry_run=False)
        with session_scope() as session:
            for name in ("Dog", "Tawny Owl"):
                asset = session.get(orm.MediaAsset, assets[name][0])
                assert asset.reclaimed_at is None
                assert Path(asset.storage_uri).exists()

    def test_a_second_purge_finds_nothing(self, settings) -> None:
        _seed(settings)
        privacy.purge_human_audio(session_scope, dry_run=False)
        again = privacy.purge_human_audio(session_scope, dry_run=False)
        assert again.items == []
        assert again.deleted == 0
