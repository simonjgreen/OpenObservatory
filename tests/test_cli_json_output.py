"""`--json` must survive a pipe.

Every `--json` flag in the CLI used to go through `console.print_json`, which
colourises its output: rich emitted ANSI escape sequences onto stdout even when
stdout was a pipe rather than a terminal. The result parsed perfectly by eye and
failed for every machine — `jq` and `json.load` both reject it at column 2,
because the first byte is `\x1b` and not `{`.

That is the project's recurring failure mode in miniature: an output that looks
right to a human and is wrong to everything else. These tests run the real
installed entry point in a subprocess with stdout redirected to a pipe, because
that is the only arrangement in which the bug reproduces — an in-process runner
that captures output would have reported success throughout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC
from pathlib import Path

import pytest


def _run(*args: str) -> str:
    """Invoke the CLI as an operator's shell would, with stdout on a pipe."""
    result = subprocess.run(
        [sys.executable, "-m", "open_observatory.cli", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"command failed: {result.stderr[-2000:]}"
    return result.stdout


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["system-report", "--json"], id="system-report"),
        pytest.param(
            ["audio", "window-dump", "--seconds", "4", "--scene", "tone", "--json"],
            id="window-dump",
        ),
    ],
)
def test_json_output_is_machine_readable(args: list[str]) -> None:
    stdout = _run(*args)

    # The specific regression: an escape sequence before the opening brace.
    assert "\x1b" not in stdout, "ANSI escape sequences leaked into --json output"
    assert stdout.lstrip().startswith("{"), stdout[:80]

    # And the contract that actually matters to a caller.
    payload = json.loads(stdout)
    assert isinstance(payload, dict) and payload


def _seed_suspect_stream(dsn: str) -> None:
    """One closed stream claiming far more wall time than its frames support.

    2 hours claimed, 20 minutes of frames -- the ADR-024 shape, well under the
    0.9 ratio and over the 60 s minimum.
    """
    import uuid
    from datetime import datetime, timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from open_observatory.db import models as orm

    # No create_all: the schema is bootstrapped by the CLI's own
    # ensure_schema_at_head (ADR-042), which refuses an unstamped database --
    # so this seeds into a database that has already been migrated to head.
    engine = create_engine(dsn, future=True)
    start = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    rate = 48_000
    with Session(engine) as session:
        session.add(orm.AudioStream(
            id=uuid.uuid4(),
            source_kind="alsa",
            start_utc=start,
            end_utc=start + timedelta(hours=2),
            start_monotonic_ns=0,
            end_monotonic_ns=2 * 3600 * 1_000_000_000,
            sample_rate=rate,
            sample_format="S16_LE",
            channels=1,
            frame_count=20 * 60 * rate,
            last_frame_at_utc=start + timedelta(minutes=20),
            end_reason="test-seed",
        ))
        session.commit()
    engine.dispose()


def test_advisory_text_after_the_document_goes_to_stderr(tmp_path) -> None:
    """The bug the first version of this test did not catch.

    `detections reconcile-plausibility --json` emitted a well-formed report and
    then printed "Dry run only -- nothing was changed" to stdout underneath it,
    so `json.load` failed with "Extra data" at the line where the advice began.
    Found on the live station against a 1,485-line report, not in CI, because
    the two commands originally covered above happen to print nothing after
    their document.

    Exercised through `history reconcile-streams`, which shares the dry-run
    notice and needs no model assets, so this runs everywhere rather than only
    where BirdNET is installed. Any command that emits JSON *and* has something
    to say to a human belongs in this test.
    """
    env = {
        **os.environ,
        "OO_DATA_DIR": str(tmp_path),
        "OO_DATABASE_DSN": f"sqlite+pysqlite:///{tmp_path / 't.db'}",
        "OO_RUNTIME_ENV_PATH": str(tmp_path / "runtime.env"),
    }
    # Let the CLI bootstrap the schema through Alembic before seeding into it.
    subprocess.run(
        [sys.executable, "-m", "open_observatory.cli", "history", "reconcile-streams", "--json"],
        capture_output=True, text=True, timeout=300, env=env, check=True,
    )
    # A suspect stream must actually exist, or the command returns before it
    # reaches the notice and this test passes against the very bug it exists to
    # catch. It did, on the first draft: an empty database has nothing to report.
    _seed_suspect_stream(env["OO_DATABASE_DSN"])

    result = subprocess.run(
        [sys.executable, "-m", "open_observatory.cli",
         "history", "reconcile-streams", "--json"],
        capture_output=True, text=True, timeout=300, env=env,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    findings = json.loads(result.stdout)   # the whole document, nothing trailing it
    assert findings, "seed did not produce a suspect; the notice path was not exercised"
    assert "Dry run only" not in result.stdout
    assert "Dry run only" in result.stderr
    assert "\x1b" not in result.stdout


def test_failure_messages_do_not_corrupt_the_document(tmp_path) -> None:
    """An error path must leave stdout parseable too.

    `reconcile-plausibility` refuses to run without station coordinates, which
    is correct since ADR-047 made location unset by default. It printed that
    refusal to stdout, so a caller piping `--json` got neither a document nor a
    clean error -- the worst of both.
    """
    env = {
        **os.environ,
        "OO_DATA_DIR": str(tmp_path),
        "OO_DATABASE_DSN": f"sqlite+pysqlite:///{tmp_path / 't.db'}",
        "OO_RUNTIME_ENV_PATH": str(tmp_path / "runtime.env"),
    }
    env.pop("OO_LATITUDE", None)
    env.pop("OO_LONGITUDE", None)
    result = subprocess.run(
        [sys.executable, "-m", "open_observatory.cli",
         "detections", "reconcile-plausibility", "--json"],
        capture_output=True, text=True, timeout=300, env=env,
    )
    assert result.returncode != 0
    assert result.stdout.strip() == "", f"error text leaked to stdout: {result.stdout[:200]!r}"
    assert "coordinates" in result.stderr


def test_logs_go_to_stderr_not_stdout() -> None:
    """Structured logs must not contaminate the JSON document either.

    `audio window-dump` emits a `replay.opened` log line while it works. If that
    landed on stdout it would sit above the JSON and break parsing just as
    thoroughly as the escape codes did, so this pins the split.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "open_observatory.cli",
            "audio",
            "window-dump",
            "--seconds",
            "4",
            "--scene",
            "tone",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0
    json.loads(result.stdout)
    assert "replay.opened" in result.stderr
    assert "replay.opened" not in result.stdout


def test_no_command_uses_console_print_json() -> None:
    """The structural version of everything above.

    The tests in this file name specific commands, which means they only cover
    the ones somebody remembered. That failed twice. `oo refine status --json`
    shipped with `console.print_json` because ADR-045 was written on a branch cut
    before `emit_json` existed, and nothing noticed until rich 15 changed the
    output enough to break parsing months later. `oo detections
    reconcile-taxonomy --json` had the same hole.

    So assert the property rather than the instances: no command may call
    `console.print_json`. `emit_json` is the only way JSON reaches stdout.
    """
    source = (
        Path(__file__).resolve().parents[1]
        / "src" / "open_observatory" / "cli.py"
    ).read_text()

    offenders = [
        line.strip()
        for line in source.splitlines()
        # The call, not the word: this module's own docstring names it.
        if "console.print_json(" in line
    ]
    assert not offenders, (
        "console.print_json colourises and appends, so its output is not "
        "machine-readable. Use emit_json instead. Offending lines:\n  "
        + "\n  ".join(offenders)
    )
