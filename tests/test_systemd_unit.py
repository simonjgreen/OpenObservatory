"""The systemd unit must be able to write everything the code writes.

`ProtectHome=read-only` is deliberate and worth keeping, but it silently
converts "the operator changed a setting" into a 500 the moment anything
persists outside `ReadWritePaths`. That is exactly what happened on 2026-08-09:
ADR-048 made every setting web-editable, its tests all passed locally -- where
no sandbox applies -- and the feature was dead on the live station from the
moment it deployed, because `config/runtime.env` was unwritable.

These tests read the real unit file and the real code, so a future write path
that nothing has whitelisted fails here rather than in production.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

UNIT = Path(__file__).resolve().parents[1] / "deploy" / "open-observatory.service"


def _read_write_paths() -> set[str]:
    text = UNIT.read_text()
    paths: set[str] = set()
    for line in text.splitlines():
        if line.startswith("ReadWritePaths="):
            paths.update(line.split("=", 1)[1].split())
    return paths


def test_unit_file_exists_and_hardening_is_still_on() -> None:
    text = UNIT.read_text()
    # If these are ever removed the tests below stop meaning anything, so pin
    # them: the point is that the sandbox is real AND the write paths are right.
    assert "ProtectHome=read-only" in text
    assert "ProtectSystem=full" in text


@pytest.mark.parametrize(
    ("subdirectory", "why"),
    [
        ("data", "the database, evidence clips and transient assets"),
        ("config", "runtime.env, which every web-editable setting persists to (ADR-048)"),
    ],
)
def test_writable_directories_are_whitelisted(subdirectory: str, why: str) -> None:
    paths = _read_write_paths()
    assert f"@DEPLOY_ROOT@/{subdirectory}" in paths, (
        f"{subdirectory}/ is written at runtime ({why}) but is not in "
        f"ReadWritePaths, so writes fail with EROFS under ProtectHome=read-only. "
        f"Currently whitelisted: {sorted(paths)}"
    )


def test_settings_still_persist_to_the_config_directory() -> None:
    """Pin the assumption the test above rests on.

    If persistence ever moves out of `config/`, this fails and whoever moved it
    has to revisit the unit file rather than discovering EROFS on a Sunday.
    """
    settings_src = (
        Path(__file__).resolve().parents[1]
        / "src" / "open_observatory" / "config.py"
    ).read_text()
    match = re.search(r"runtime_env_path[^=]*=\s*[^\n]*", settings_src)
    assert match, "runtime_env_path is no longer declared in config.py"
    assert "config" in match.group(0), (
        f"runtime.env no longer lives under config/: {match.group(0)!r}. "
        "Update deploy/open-observatory.service's ReadWritePaths to match."
    )
