"""The streaming resampler check must measure exactly what the accumulating one did.

`oo audio resample-check` is a documented target-device smoke test, and the
audio pipeline spec asks for it at **one hour** of generated PCM. Its first
implementation concatenated every output block and diffed the result, which
peaks at about 2.8 GB at that duration — on a 7.8 GiB device that is also
running capture at 384 kHz. `audio/resample_check.py` computes the same four
properties in one pass with bounded state instead.

That is only a safe change if the numbers are the same numbers, so the reference
implementation below is the original algorithm, kept verbatim in shape
(accumulate everything, then measure), and the tests assert agreement on the
same input. Everything except the median is exact; the median comes from a
histogram and is asserted to a stated tolerance, five orders below the threshold
it feeds.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import numpy as np
import pytest
from typer.testing import CliRunner

from open_observatory.audio.resample import AudibleResampler
from open_observatory.audio.resample_check import measure_resampler
from open_observatory.cli import app

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# Long enough to cross many block seams and to exceed the 65,536-sample FFT
# head (1.4 s of output), short enough for the reference to hold in memory.
_SECONDS = 8.0


def _reference(
    source_rate: int = 384000,
    target_rate: int = 48000,
    seconds: float = _SECONDS,
    block_ms: int = 100,
    tone_hz: float = 1000.0,
) -> dict[str, float]:
    """The original accumulating implementation, reproduced for comparison."""
    block_frames = int(source_rate * block_ms / 1000)
    blocks = int(seconds * source_rate / block_frames)

    delay_converter = AudibleResampler(source_rate, target_rate)
    impulse_at = block_frames * 5 + block_frames // 3
    impulse_out: list[np.ndarray] = []
    for index in range(blocks if blocks < 20 else 20):
        chunk = np.zeros(block_frames, dtype=np.float32)
        local = impulse_at - index * block_frames
        if 0 <= local < block_frames:
            chunk[local] = 1.0
        impulse_out.append(delay_converter.process(chunk).pcm)
    impulse_signal = np.concatenate(impulse_out)
    ideal_position = impulse_at * target_rate / source_rate
    actual_position = float(np.argmax(np.abs(impulse_signal)))
    group_delay = actual_position - ideal_position

    converter = AudibleResampler(source_rate, target_rate)
    outputs: list[np.ndarray] = []
    deficits: list[int] = []
    phase = 0
    for _ in range(blocks):
        t = (phase + np.arange(block_frames)) / source_rate
        outputs.append(converter.process(0.5 * np.sin(2 * np.pi * tone_hz * t)).pcm)
        phase += block_frames
        deficits.append(
            converter.expected_output_frames(converter.input_frames) - converter.output_frames
        )

    derived = np.concatenate(outputs)
    tenth = max(1, len(deficits) // 10)
    trend = float(np.mean(deficits[-tenth:]) - np.mean(deficits[:tenth]))
    diffs = np.abs(np.diff(derived))
    size = min(1 << 16, derived.shape[0])
    spectrum = np.abs(np.fft.rfft(derived[:size] * np.hanning(size)))
    peak_hz = float(np.fft.rfftfreq(size, 1.0 / target_rate)[int(np.argmax(spectrum))])

    return {
        "group_delay": group_delay,
        "output_frames": float(derived.shape[0]),
        "expected_output_frames": float(
            converter.expected_output_frames(converter.input_frames)
        ),
        "deficit_min": float(min(deficits)),
        "deficit_max": float(max(deficits)),
        "trend": trend,
        "median_step": float(np.median(diffs)),
        "worst_step": float(diffs.max()),
        "peak_hz": peak_hz,
    }


@pytest.fixture(scope="module")
def pair() -> tuple[dict[str, float], object]:
    return _reference(), measure_resampler(seconds=_SECONDS)


def test_exact_statistics_are_identical(pair) -> None:
    """Everything except the median is computed exactly, so it must match bit for bit."""
    reference, streamed = pair
    assert streamed.group_delay_frames == reference["group_delay"]
    assert streamed.output_frames == int(reference["output_frames"])
    assert streamed.expected_output_frames == int(reference["expected_output_frames"])
    assert streamed.deficit_min == int(reference["deficit_min"])
    assert streamed.deficit_max == int(reference["deficit_max"])
    assert streamed.deficit_trend == reference["trend"]
    assert streamed.peak_hz == reference["peak_hz"]


def test_worst_step_is_exact_and_spans_block_seams(pair) -> None:
    """The maximum is the statistic a click would show up in, so it is exact.

    It also has to be taken *across* block boundaries: the streaming version
    carries the previous block's last sample forward, and if it did not, the one
    difference that a seam click actually lands on would be the one never
    measured.
    """
    reference, streamed = pair
    assert streamed.worst_step == reference["worst_step"]


def test_median_step_matches_within_the_histogram_resolution(pair) -> None:
    """The median is a 2^20-bin histogram estimate over [0, 2].

    Half a bin is 9.5e-7 absolute. Asserted at 1e-4 relative, which is still
    four orders inside the 25x seam threshold this figure feeds -- so no
    plausible histogram error can change a verdict.
    """
    reference, streamed = pair
    assert streamed.median_step == pytest.approx(reference["median_step"], rel=1e-4)


def test_verdicts_agree_with_the_reference_thresholds(pair) -> None:
    """The four pass/fail rules, re-applied to the reference's own numbers."""
    reference, streamed = pair
    trend_limit = (reference["deficit_max"] - reference["deficit_min"]) + 8
    assert abs(reference["group_delay"]) <= 1.0
    assert abs(reference["trend"]) < trend_limit
    assert reference["worst_step"] < reference["median_step"] * 25
    assert streamed.passed
    assert streamed.failures == []
    assert streamed.deficit_trend_limit == trend_limit


def test_memory_does_not_grow_with_duration() -> None:
    """The property the rewrite exists for, asserted rather than asserted-about.

    A 4x longer run must not cost meaningfully more resident memory. Measured in
    a subprocess because peak RSS is a process-level fact; the threshold is
    deliberately loose (1.5x) so this fails only on a real regression to
    accumulating the run.
    """
    script = (
        "import resource;"
        "from open_observatory.audio.resample_check import measure_resampler;"
        "measure_resampler(seconds=SECONDS);"
        "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)"
    )
    peaks = []
    for seconds in (5, 20):
        out = subprocess.run(
            [sys.executable, "-c", script.replace("SECONDS", str(seconds))],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert out.returncode == 0, out.stderr[-2000:]
        peaks.append(int(out.stdout.strip().splitlines()[-1]))
    short, long = peaks
    assert long < short * 1.5, f"peak RSS grew with duration: {short} -> {long} KiB"


def test_json_output_carries_every_threshold_and_is_pipe_clean() -> None:
    """The hour's result has to be diffable against the five-minute baseline.

    So the JSON carries not just the measurements but the limit each one was
    judged against -- a recorded result that cannot be re-checked against its own
    criterion is how a threshold gets moved after the fact.
    """
    result = runner.invoke(app, ["audio", "resample-check", "--seconds", "3", "--json"])
    assert result.exit_code == 0, result.output
    text = _ANSI.sub("", result.output)
    payload = json.loads(text[text.index("{") : text.rindex("}") + 1])

    assert payload["passed"] is True
    assert payload["failures"] == []
    for key in (
        "backend_detail",
        "audio_seconds",
        "group_delay_frames",
        "deficit_min",
        "deficit_max",
        "deficit_trend",
        "deficit_trend_limit",
        "median_step",
        "worst_step",
        "seam_limit_ratio",
        "peak_hz",
        "tone_tolerance_hz",
    ):
        assert key in payload, key
    assert payload["ratio"] == "1/8"
    assert payload["audio_seconds"] == pytest.approx(3.0)
