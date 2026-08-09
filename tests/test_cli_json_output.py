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
import subprocess
import sys

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
