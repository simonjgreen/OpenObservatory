"""ADR-078: the database on its own volume, and the tools that move it.

Local tests cannot see the station's sandbox or its SSD, so what they pin is
the mechanism: the volume resolver, the refusal, the directory creation, and
the copy/check commands that the relocation runbook is written around.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from open_observatory.cli import app
from open_observatory.config import Settings, set_settings
from open_observatory.db import session as db_session
from open_observatory.db.admin import check_database, copy_database, sqlite_facts
from open_observatory.db.session import create_all, database_volume, init_engine, session_scope


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base = Settings(
        data_dir=tmp_path / "data",
        database_dsn=f"sqlite+pysqlite:///{tmp_path / 'volume' / 'db' / 'oo.sqlite'}",
        source="synthetic",
        birdnet_enabled=False,
        runtime_env_path=tmp_path / "runtime.env",
        web_dist=tmp_path / "nonexistent-dist",
    )
    configured = base.model_copy(update=overrides)
    set_settings(configured)
    return configured


def test_init_engine_creates_the_dsn_directory_not_just_data_dir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    target = Path(settings.resolved_database_dsn.split(":///", 1)[1])
    assert not target.parent.exists()
    init_engine(settings)
    create_all()
    assert target.exists()


def test_database_volume_reports_where_the_file_is(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    before = database_volume(settings)
    assert before is not None
    assert before["exists"] is False
    assert before["bytes"] is None
    assert before["require_mount"] is False
    init_engine(settings)
    create_all()
    after = database_volume(settings)
    assert after is not None
    assert after["exists"] is True
    assert after["bytes"] and after["bytes"] > 0
    # The resolver agrees with the operating system about the mount point.
    assert after["on_system_disk"] == (after["mount_point"] == "/")
    assert Path(str(after["mount_point"])).is_mount()


def test_database_volume_is_none_for_a_non_sqlite_dsn(tmp_path: Path) -> None:
    settings = _settings(tmp_path, database_dsn="postgresql+psycopg://x:y@localhost/oo")
    assert database_volume(settings) is None


def test_refuses_the_system_disk_when_a_mount_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, database_require_mount=True)
    target = Path(settings.resolved_database_dsn.split(":///", 1)[1])
    monkeypatch.setattr(db_session, "_mount_point_of", lambda _path: Path("/"))
    with pytest.raises(RuntimeError, match="system disk"):
        init_engine(settings)
    # Nothing was created: the whole point is that no second database appears.
    assert not target.parent.exists()


def test_a_mounted_volume_satisfies_the_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, database_require_mount=True)
    monkeypatch.setattr(db_session, "_mount_point_of", lambda _path: tmp_path / "volume")
    init_engine(settings)
    create_all()
    volume = database_volume(settings)
    assert volume is not None and volume["on_system_disk"] is False


@pytest.fixture
def populated(tmp_path: Path) -> tuple[Settings, Path]:
    settings = _settings(tmp_path)
    init_engine(settings)
    create_all()
    from open_observatory.db import models as orm

    with session_scope() as session:
        session.add(orm.Station(name="t", timezone="UTC", software_version="test"))
    return settings, Path(settings.resolved_database_dsn.split(":///", 1)[1])


def test_copy_is_a_consistent_snapshot_with_the_same_rows(populated: tuple[Settings, Path]) -> None:
    _, source = populated
    destination = source.parent / "copy.sqlite"
    result = copy_database(source, destination)
    assert destination.exists()
    assert result["bytes"] == destination.stat().st_size
    assert sqlite_facts(destination)["row_counts"] == sqlite_facts(source)["row_counts"]
    assert sqlite_facts(destination)["row_counts"]["detection"] == 0
    with pytest.raises(FileExistsError):
        copy_database(source, destination)
    with pytest.raises(ValueError):
        copy_database(source, source)
    copy_database(source, destination, force=True)


def test_check_reports_ok_and_the_facts(populated: tuple[Settings, Path]) -> None:
    _, source = populated
    quick = check_database(source)
    assert quick["ok"] is True
    assert quick["check"] == "quick_check"
    assert quick["problems"] == []
    full = check_database(source, full=True)
    assert full["ok"] is True and full["check"] == "integrity_check"
    assert full["journal_mode"] == "wal"
    assert "detection" in full["row_counts"]


def test_cli_status_copy_and_check(populated: tuple[Settings, Path]) -> None:
    _, source = populated
    runner = CliRunner()
    status = runner.invoke(app, ["db", "status", "--json"])
    assert status.exit_code == 0, status.output
    assert '"on_system_disk"' in status.output and '"row_counts"' in status.output

    destination = source.parent / "cli-copy.sqlite"
    copied = runner.invoke(app, ["db", "copy", str(destination), "--json"])
    assert copied.exit_code == 0, copied.output
    assert destination.exists()
    again = runner.invoke(app, ["db", "copy", str(destination)])
    assert again.exit_code == 2

    checked = runner.invoke(app, ["db", "check", str(destination)])
    assert checked.exit_code == 0, checked.output
    assert "ok" in checked.output
    missing = runner.invoke(app, ["db", "check", str(source.parent / "nope.sqlite")])
    assert missing.exit_code == 2
