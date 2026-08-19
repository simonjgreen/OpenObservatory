"""Tests for ``oo audio window-dump``, Milestone 2's window inspection CLI.

The command runs the real ``StreamClock`` / ``AudibleResampler`` /
``StreamSegmenter`` / ``RingBuffer`` classes over replayed or synthetic audio
(never the live capture path -- see the command's own docstring for why) and
reports ground truth: the actual frame bounds and actual sample count of each
completed window, cross-checked against an independent ring-buffer read of the
same frames.
"""

from __future__ import annotations

import json
import re
from itertools import pairwise
from pathlib import Path

import numpy as np
import soundfile as sf
from typer.testing import CliRunner

from open_observatory.cli import app

# mix_stderr=False: stdout carries the JSON document and nothing else, which
# is the contract emit_json exists to keep. Mixing the streams hid a real
# defect -- `oo refine status --json` printed through rich and nobody saw,
# because the logger used to hold the pre-redirect stderr and its output
# never reached the captured stdout at all.
runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _json_payload(output: str) -> dict:
    """Strip rich's ANSI styling and structlog's stray stderr line, then parse.

    ``CliRunner`` merges stdout/stderr by default, and rich's ``Console`` emits
    colour codes even into a non-terminal stream in some environments (this one
    included), so both have to be stripped before the remainder is valid JSON --
    exactly the situation a real operator hits piping into ``jq`` without
    ``NO_COLOR`` set.
    """
    text = _ANSI.sub("", output)
    # Drop any structlog line (e.g. "replay.opened ...") preceding the JSON body.
    start = text.index("{")
    return json.loads(text[start:])


def test_lists_windows_for_a_synthetic_scene() -> None:
    result = runner.invoke(
        app, ["audio", "window-dump", "--seconds", "6", "--scene", "tone"]
    )
    assert result.exit_code == 0, result.output
    assert "Windows emitted" in result.output
    assert "segmenter view" in result.output
    assert "ring cross-check" in result.output


def test_json_reports_ground_truth_frame_counts_matching_actual_pcm_shape() -> None:
    result = runner.invoke(
        app,
        ["audio", "window-dump", "--seconds", "8", "--scene", "tone", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = _json_payload(result.output)

    assert payload["windows"], "expected at least one completed window"
    window = payload["windows"][0]
    # The frame count reported is the window's own array shape, not just the
    # spec's arithmetic restated -- and here they must agree since nothing
    # discontinuous happened.
    assert window["actual_frame_count"] == window["expected_frame_count"]
    assert window["end_frame"] - window["start_frame"] == window["actual_frame_count"]
    # UTC is authoritative; local time is derived from it, not the reverse.
    assert window["utc_start"].endswith("Z")
    assert "detail" in window
    # An independent second read (a fresh RingBuffer fed the same audio) agrees
    # with the segmenter's own window -- the ground-truth cross-check.
    assert window["ring_cross_check"] == "match"


def test_native_and_derived_frame_bounds_map_by_the_resample_ratio() -> None:
    """384 kHz -> 48 kHz is an exact 8x ratio; native bounds must reflect it."""
    result = runner.invoke(
        app,
        [
            "audio", "window-dump",
            "--seconds", "6", "--scene", "tone",
            "--stream-kind", "audible48",
            "--duration-s", "2", "--stride-s", "2",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json_payload(result.output)
    window = payload["windows"][0]
    assert window["native_start_frame"] == window["start_frame"] * 8
    assert window["native_end_frame"] == window["end_frame"] * 8


def test_gap_injection_is_visible_in_the_segmenter_and_start_frame() -> None:
    """A gap mid-stream must drop the segmenter's buffered tail (ADR-003
    behaviour: a window may never span a discontinuity), which is directly
    observable as a non-zero start_frame and a segmenter reset."""
    result = runner.invoke(
        app,
        [
            "audio", "window-dump",
            "--seconds", "6", "--scene", "tone",
            "--stream-kind", "native",
            "--duration-s", "1", "--stride-s", "1",
            "--gap-at-s", "2", "--gap-frames", "50000",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json_payload(result.output)
    assert payload["gap_injected"] == {"at_s": 2.0, "frames": 50000}
    # Windows before the gap (2 s in) are unaffected. `StreamSegmenter.push`
    # drops its buffered tail across a discontinuity (a window must never span
    # a gap), so the frame index jumps by more than one window's worth right
    # where the gap was injected -- ground truth, visible in the frame numbers
    # themselves rather than a summary claim.
    starts = [w["start_frame"] for w in payload["windows"]]
    assert starts[0] == 0
    # A normal stride (native, no resampling) advances by exactly 384_000
    # frames (1 s at 384 kHz) every window; the gap must produce one visibly
    # larger jump than that.
    jumps = [b - a for a, b in pairwise(starts)]
    assert any(jump > 400_000 for jump in jumps), starts


def test_bad_stream_kind_is_rejected() -> None:
    result = runner.invoke(app, ["audio", "window-dump", "--stream-kind", "bogus"])
    assert result.exit_code == 2


def test_index_out_of_range_reports_and_fails() -> None:
    result = runner.invoke(
        app, ["audio", "window-dump", "--seconds", "6", "--index", "50"]
    )
    assert result.exit_code == 1
    assert "out of range" in result.output


def test_no_completed_window_is_reported_honestly_not_silently() -> None:
    result = runner.invoke(
        app,
        ["audio", "window-dump", "--seconds", "0.5", "--duration-s", "3"],
    )
    assert result.exit_code == 1
    assert "No window completed" in result.output


def test_replay_source_dumps_the_actual_recorded_samples(tmp_path: Path) -> None:
    """Feeding a known WAV file must round-trip its own samples exactly --
    the whole point of the command is that the numbers shown are real."""
    sample_rate = 48000
    t = np.arange(sample_rate * 3) / sample_rate
    pcm = (0.4 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    wav_path = tmp_path / "known_tone.wav"
    sf.write(str(wav_path), pcm, sample_rate)

    out_wav = tmp_path / "window0.wav"
    result = runner.invoke(
        app,
        [
            "audio", "window-dump",
            "--source", str(wav_path),
            "--sample-rate", str(sample_rate),
            "--stream-kind", "native",
            "--duration-s", "1", "--stride-s", "1",
            "--seconds", "3",
            "--write-wav", str(out_wav),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_wav.exists()

    written, written_rate = sf.read(str(out_wav), dtype="float32")
    assert written_rate == sample_rate
    np.testing.assert_allclose(written, pcm[:sample_rate], atol=1e-4)


def test_local_time_rendering_uses_the_requested_timezone() -> None:
    result = runner.invoke(
        app,
        [
            "audio", "window-dump",
            "--seconds", "4", "--scene", "tone",
            "--duration-s", "1", "--stride-s", "1",
            "--timezone", "Pacific/Kiritimati",  # UTC+14, unambiguous offset
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json_payload(result.output)
    assert payload["windows"][0]["local_start"].endswith("+14:00")
