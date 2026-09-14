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
REFINE_UNIT = (
    Path(__file__).resolve().parents[1] / "deploy" / "open-observatory-refine.service"
)
ANALYTICS_UNIT = (
    Path(__file__).resolve().parents[1] / "deploy" / "open-observatory-analytics.service"
)


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


def test_refine_unit_gives_numba_a_writable_cache() -> None:
    """The same sandbox trap, one unit over.

    librosa pulls in numba, which JIT-compiles on first use and caches the
    result beside the library's own source. Under `ProtectHome=read-only` that
    raises `RuntimeError: cannot cache function ... no locator available` and
    kills the pass before it classifies anything.

    It really happened: the timer fired at 02:01 BST on 2026-08-10, ran eleven
    seconds and exited 1. `oo refine status` still said `last_run: None`, so
    nothing surfaced the failure except the journal.
    """
    text = REFINE_UNIT.read_text()
    assert "ProtectHome=read-only" in text, "the sandbox is the reason this matters"
    assert "NUMBA_CACHE_DIR" in text, (
        "numba has nowhere writable to cache, so the refinement pass dies on "
        "first compile under ProtectHome=read-only"
    )
    cache_line = next(
        line for line in text.splitlines() if line.startswith("Environment=NUMBA_CACHE_DIR=")
    )
    target = cache_line.split("=", 2)[2]
    writable = {
        line.split("=", 1)[1]
        for line in text.splitlines()
        if line.startswith("ReadWritePaths=")
    }
    assert any(target.startswith(path) for path in writable), (
        f"NUMBA_CACHE_DIR is {target!r}, which is not under any ReadWritePaths "
        f"entry ({sorted(writable)}) -- it would fail exactly as before"
    )


@pytest.mark.parametrize(
    ("unit", "name"),
    [
        (UNIT, "open-observatory.service"),
        (REFINE_UNIT, "open-observatory-refine.service"),
        (ANALYTICS_UNIT, "open-observatory-analytics.service"),
    ],
)
def test_a_database_on_the_evidence_volume_is_writable_by_every_unit(unit: Path, name: str) -> None:
    """ADR-078 puts the database under the evidence mount, in every unit's paths.

    Three processes open the database: the station, the nightly refinement
    runner and the hourly analytics roll-up (ADR-079). The relocation target is
    documented as `data/clips/database/`, which inherits `data`'s entry today
    -- this pins that nobody narrows a unit's `ReadWritePaths` to `data/clips`
    alone, or to `data/transient`, and quietly turns a timer into an EROFS.
    """
    target = "@DEPLOY_ROOT@/data/clips/database"
    writable = {
        entry
        for line in unit.read_text().splitlines()
        if line.startswith("ReadWritePaths=")
        for entry in line.split("=", 1)[1].split()
    }
    assert any(target.startswith(path) for path in writable), (
        f"{name}: the relocated database at {target!r} is not under any "
        f"ReadWritePaths entry ({sorted(writable)})"
    )


def test_analytics_unit_is_fenced_like_the_refinement_runner() -> None:
    """ADR-079's roll-up reads the whole detection table for a day; it must
    lose every contest with capture for CPU and disk, as ADR-045's runner does."""
    text = ANALYTICS_UNIT.read_text()
    for directive in ("AllowedCPUs=2-3", "Nice=19", "IOSchedulingClass=idle", "Type=oneshot"):
        assert directive in text, f"{directive} missing from the analytics unit"
    assert "ProtectHome=read-only" in text
