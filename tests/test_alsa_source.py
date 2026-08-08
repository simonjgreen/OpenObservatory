"""Capture-side accounting and ring sizing.

`alsaaudio` is a compiled extension that is not installed off-target, so these
tests drive `AlsaSource._read_blocking` against a stub module and a fake PCM
handle. That is deliberate: the properties under test — how many periods we ask
for, and whether an overrun's cost is measured — are arithmetic and control flow,
not anything the real driver contributes.
"""

from __future__ import annotations

import sys
import types
import uuid

import numpy as np
import pytest

from open_observatory.audio.alsa_source import AlsaSource
from open_observatory.audio.contracts import (
    AudioFormat,
    ClockCorrelation,
    DiscontinuityReason,
    SourceKind,
    StreamInfo,
)

RATE = 384_000
PERIOD_FRAMES = 3_840
BLOCK_FRAMES = 38_400


class _StubALSAAudioError(Exception):
    pass


def _ensure_alsaaudio() -> type[BaseException]:
    """Return whatever error type `_read_blocking` will actually catch.

    On the target the real extension is installed and its own exception class is
    the one that matters; off-target there is nothing to import. Resolving this
    at call time rather than stubbing unconditionally keeps the test honest on
    the Pi and runnable on a laptop.
    """
    try:
        import alsaaudio
    except ImportError:
        module = types.ModuleType("alsaaudio")
        module.ALSAAudioError = _StubALSAAudioError  # type: ignore[attr-defined]
        sys.modules["alsaaudio"] = module
        return _StubALSAAudioError
    return alsaaudio.ALSAAudioError  # type: ignore[no-any-return]


@pytest.fixture(autouse=True)
def alsaaudio_importable() -> None:
    """`_read_blocking` imports alsaaudio before anything else can run."""
    _ensure_alsaaudio()


class FakePcm:
    """Hands back whole periods, optionally raising an overrun on the way."""

    def __init__(self, *, raise_overrun_at: int | None = None) -> None:
        self.reads = 0
        self._raise_at = raise_overrun_at
        self._payload = np.zeros(PERIOD_FRAMES, dtype="<i2").tobytes()

    def read(self) -> tuple[int, bytes]:
        if self._raise_at is not None and self.reads == self._raise_at:
            self._raise_at = None
            raise _ensure_alsaaudio()("Input/output error")
        self.reads += 1
        return PERIOD_FRAMES, self._payload


def _prepared_source() -> AlsaSource:
    source = AlsaSource(block_ms=100)
    source._period_frames = PERIOD_FRAMES
    source._block_frames = BLOCK_FRAMES
    source._dtype = "<i2"
    source._scale = 1.0 / 32768.0
    source._pending_discontinuity = None
    source.info = StreamInfo(
        stream_id=uuid.uuid4(),
        source_kind=SourceKind.ALSA,
        device_key="test",
        device_label="test",
        fmt=AudioFormat(sample_rate=RATE, channels=1, sample_format="S16_LE"),
        started_monotonic_ns=0,
        clock=ClockCorrelation.sample(),
    )
    return source


class _Clock:
    """A monotonic clock the test advances by hand, in nanoseconds."""

    def __init__(self) -> None:
        self.now = 10**12

    def advance_ms(self, ms: float) -> None:
        self.now += int(ms * 1_000_000)

    def __call__(self) -> int:
        return self.now


# -- ring sizing ---------------------------------------------------------


def test_ring_is_deeper_than_one_capture_block() -> None:
    """The shipped `periods=8` gave an 80 ms ring behind a 100 ms block."""
    source = AlsaSource(block_ms=100, period_ms=10.0, buffer_ms=500.0)
    periods = source._periods_for_buffer()
    assert periods == 50
    assert periods * 10.0 > 100.0


def test_ring_never_falls_below_two_blocks_even_if_buffer_ms_is_small() -> None:
    source = AlsaSource(block_ms=250, period_ms=10.0, buffer_ms=50.0)
    assert source._periods_for_buffer() * 10.0 >= 500.0


# -- gap accounting ------------------------------------------------------


def test_clean_block_reports_no_discontinuity(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _prepared_source()
    source._pcm = FakePcm()
    clock = _Clock()
    monkeypatch.setattr("open_observatory.audio.alsa_source.time.monotonic_ns", clock)

    block = source._read_blocking()
    assert block is not None
    assert block.frame_count == BLOCK_FRAMES

    clock.advance_ms(100)
    block = source._read_blocking()
    assert block is not None
    assert block.discontinuity is None
    assert block.missing_frames == 0
    assert source.gaps_with_loss == 0
    assert source.gaps_without_loss == 0


def test_overrun_that_lost_audio_reports_the_frames_it_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the estimate used to be skipped when ALSA had already raised.

    An EPIPE overrun set `discontinuity`, and the frame-deficit estimator was
    gated on `discontinuity is None`, so exactly the events most likely to have
    lost audio were published as `missing_frames=0` and never counted.
    """
    source = _prepared_source()
    clock = _Clock()
    monkeypatch.setattr("open_observatory.audio.alsa_source.time.monotonic_ns", clock)

    source._pcm = FakePcm()
    source._read_blocking()  # anchors the stream

    # 300 ms of wall time passes but only one block of audio arrives, and ALSA
    # tells us why: 200 ms — 76,800 frames — never reached us.
    source._pcm = FakePcm(raise_overrun_at=2)
    clock.advance_ms(300)
    block = source._read_blocking()

    assert block is not None
    assert block.discontinuity == DiscontinuityReason.OVERRUN
    assert source.overrun_count == 1
    assert block.missing_frames == pytest.approx(76_800, abs=BLOCK_FRAMES)
    assert source.missing_frames_total == block.missing_frames
    assert source.gaps_with_loss == 1
    assert source.gaps_without_loss == 0


def test_overrun_with_no_lost_audio_is_counted_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _prepared_source()
    clock = _Clock()
    monkeypatch.setattr("open_observatory.audio.alsa_source.time.monotonic_ns", clock)

    source._pcm = FakePcm()
    source._read_blocking()

    # ALSA reports an overrun, but the frames still add up: nothing was lost.
    source._pcm = FakePcm(raise_overrun_at=2)
    clock.advance_ms(100)
    block = source._read_blocking()

    assert block is not None
    assert block.discontinuity == DiscontinuityReason.OVERRUN
    assert block.missing_frames == 0
    assert source.gaps_without_loss == 1
    assert source.gaps_with_loss == 0
    assert source.missing_frames_total == 0


def test_crystal_drift_is_absorbed_rather_than_reported_as_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A -43 ppm device runs slow forever; that is not lost audio."""
    source = _prepared_source()
    clock = _Clock()
    monkeypatch.setattr("open_observatory.audio.alsa_source.time.monotonic_ns", clock)

    source._pcm = FakePcm()
    for _ in range(200):
        block = source._read_blocking()
        assert block is not None
        assert block.discontinuity is None or block.discontinuity is DiscontinuityReason.STREAM_START
        # Each block of audio takes very slightly longer than nominal to arrive.
        clock.advance_ms(100.05)

    assert source.gaps_with_loss == 0
    assert source.missing_frames_total == 0
