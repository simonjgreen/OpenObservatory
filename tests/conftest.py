from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from open_observatory.audio.contracts import (
    AudioFormat,
    ClockCorrelation,
    SourceKind,
    StreamInfo,
)
from open_observatory.config import Settings, set_settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Isolated settings: SQLite and clips under tmp_path, synthetic capture."""
    configured = Settings(
        data_dir=tmp_path / "data",
        database_dsn=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite'}",
        source="synthetic",
        synthetic_scene="dawn-chorus",
        synthetic_sample_rate=48000,
        birdnet_enabled=False,
        metrics_enabled=True,
        web_dist=tmp_path / "nonexistent-dist",
    )
    configured.ensure_directories()
    set_settings(configured)
    return configured


@pytest.fixture
def stream_info() -> StreamInfo:
    return StreamInfo(
        stream_id=uuid.uuid4(),
        source_kind=SourceKind.SYNTHETIC,
        device_key="test",
        device_label="test device",
        fmt=AudioFormat(sample_rate=48000, channels=1, sample_format="FLOAT_LE"),
        started_monotonic_ns=1_000_000_000,
        clock=ClockCorrelation(monotonic_ns=1_000_000_000, utc_ns=1_700_000_000_000_000_000),
    )
