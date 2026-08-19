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

from open_observatory.audio.alsa_source import AlsaCaptureError, AlsaSource
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


class RingedDevice:
    """A fake capture device with a real kernel-style ring, driven by `_Clock`.

    This is the instrument the estimator has to be judged against, because the
    property under test — "was any audio actually lost?" — is a property of the
    *device*, not of the reader. It produces frames at its own crystal rate,
    holds them in a ring of `ring_frames`, drops what will not fit, and raises
    the same `Input/output error` ALSA does when it discovers the overflow. So
    `device.dropped` is ground truth, injected by the test, and every assertion
    below compares the estimate against it rather than against another estimate.

    `read()` advances the fake clock to the moment the next period is ready,
    which is what a blocking ALSA read does in real time. A test simulates a
    stalled consumer simply by advancing the clock itself before reading.
    """

    def __init__(
        self,
        clock: _Clock,
        *,
        ring_frames: int,
        rate: int = RATE,
        period_frames: int = PERIOD_FRAMES,
        ppm: float = 0.0,
    ) -> None:
        self._clock = clock
        self._t0 = clock.now
        self._rate = rate * (1.0 + ppm / 1e6)
        self._period = period_frames
        self.ring_frames = ring_frames
        self.delivered = 0
        self.dropped = 0
        self.overruns = 0
        self._inject = 0
        self._inject_silent = 0
        self._payload = np.zeros(period_frames, dtype="<i2").tobytes()

    def drop(self, frames: int, *, report: bool = True) -> None:
        """Remove `frames` of audio outright, as an overflowing ring would.

        With ``report=False`` the frames vanish and the device says nothing —
        the case where the only evidence is the frame deficit itself.
        """
        if report:
            self._inject += frames
        else:
            self._inject_silent += frames

    def _produced(self) -> int:
        return int((self._clock.now - self._t0) * self._rate / 1e9)

    def read(self) -> tuple[int, bytes]:
        if self._inject_silent:
            self.delivered += self._inject_silent
            self.dropped += self._inject_silent
            self._inject_silent = 0
        due = self._t0 + int((self.delivered + self._period) * 1e9 / self._rate)
        if self._clock.now < due:
            self._clock.now = due
        backlog = self._produced() - self.delivered
        lost = self._inject + max(0, backlog - self.ring_frames)
        if lost:
            self._inject = 0
            self.delivered += lost
            self.dropped += lost
            self.overruns += 1
            raise _ensure_alsaaudio()("Input/output error")
        self.delivered += self._period
        return self._period, self._payload


def _drain(source: AlsaSource, blocks: int) -> list[object]:
    return [source._read_blocking() for _ in range(blocks)]


def _prepared_source(*, ring_frames: int = 0) -> AlsaSource:
    source = AlsaSource(block_ms=100)
    source._period_frames = PERIOD_FRAMES
    source._block_frames = BLOCK_FRAMES
    source.buffer_frames = ring_frames
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


RING_FRAMES = 192_000  # 500 ms at 384 kHz, the ring ADR-030 shipped


def _ringed(
    monkeypatch: pytest.MonkeyPatch, *, ppm: float = 0.0, ring_frames: int = RING_FRAMES
) -> tuple[AlsaSource, RingedDevice, _Clock]:
    clock = _Clock()
    monkeypatch.setattr("open_observatory.audio.alsa_source.time.monotonic_ns", clock)
    source = _prepared_source(ring_frames=ring_frames)
    device = RingedDevice(clock, ring_frames=ring_frames, ppm=ppm)
    source._pcm = device
    return source, device, clock


def test_a_stall_the_ring_absorbs_reports_no_loss_and_is_not_an_overrun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect this fix exists for: a late read is not lost audio.

    A 300 ms stall behind a 500 ms ring loses nothing — the frames wait in the
    kernel and arrive on the next read. The old estimator credited the timing
    step as loss the moment it saw it and labelled it `reason=overrun`, which is
    how the live station reported 252,495 missing frames while ALSA's own
    overrun counter sat at zero.
    """
    source, device, clock = _ringed(monkeypatch)
    _drain(source, 5)

    clock.advance_ms(300)  # the event loop is blocked; nobody drains the ring
    blocks = _drain(source, 20)

    assert device.dropped == 0, "the ring should have absorbed a 300 ms stall"
    assert device.overruns == 0
    assert source.overrun_count == 0
    assert source.missing_frames_total == 0
    assert source.gaps_with_loss == 0
    assert source.gaps_without_loss == 0
    assert [b for b in blocks if b.discontinuity is not None] == []
    # It is still reported — as what it is.
    assert source.late_reads == 1
    # The backlog still in the ring when the late read completed: 300 ms of
    # audio accumulated, 100 ms of it consumed by the read itself.
    assert source.late_read_max_frames == pytest.approx(200 * 384, rel=0.05)


def test_a_genuine_overrun_still_has_its_cost_estimated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-030's regression, which must not come back.

    The estimate used to be gated on `discontinuity is None`, so a block on
    which ALSA had already raised EPIPE skipped it and published
    `missing_frames=0` — the event most likely to have lost audio was the one
    whose cost was never measured. Here the stall exceeds the ring, the device
    really does drop frames, and the estimate must match what it dropped.
    """
    source, device, clock = _ringed(monkeypatch)
    _drain(source, 5)

    clock.advance_ms(900)  # 345,600 frames behind a 192,000-frame ring
    blocks = _drain(source, 20)

    assert device.dropped > 0
    assert source.overrun_count == 1
    lost = [b for b in blocks if b.missing_frames > 0]
    assert len(lost) == 1
    assert lost[0].discontinuity == DiscontinuityReason.OVERRUN
    assert lost[0].missing_frames == pytest.approx(device.dropped, abs=BLOCK_FRAMES)
    assert source.missing_frames_total == pytest.approx(device.dropped, abs=BLOCK_FRAMES)
    assert source.gaps_with_loss == 1
    assert source.gaps_without_loss == 0


def test_an_injected_gap_of_known_size_is_reported_at_its_true_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly 40,000 frames removed; exactly 40,000 frames reported."""
    source, device, _clock = _ringed(monkeypatch)
    _drain(source, 5)

    device.drop(40_000)
    blocks = _drain(source, 20)

    assert device.dropped == 40_000
    lost = [b for b in blocks if b.missing_frames > 0]
    assert len(lost) == 1
    assert lost[0].missing_frames == pytest.approx(40_000, abs=PERIOD_FRAMES)
    assert source.missing_frames_total == pytest.approx(40_000, abs=PERIOD_FRAMES)


def test_an_unreported_deficit_is_not_labelled_an_overrun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audio can go missing without EPIPE. It must not borrow ALSA's word for it."""
    source, device, _clock = _ringed(monkeypatch)
    _drain(source, 5)

    # Frames vanish and the device says nothing at all.
    device.drop(40_000, report=False)
    blocks = _drain(source, 20)

    lost = [b for b in blocks if b.missing_frames > 0]
    assert len(lost) == 1
    assert lost[0].discontinuity == DiscontinuityReason.FRAME_DEFICIT
    assert source.overrun_count == 0
    assert source.gaps_with_loss == 1


def test_the_gap_is_reported_at_the_frame_it_happened_not_where_it_settled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, device, _clock = _ringed(monkeypatch)
    _drain(source, 5)
    device.drop(40_000)
    blocks = _drain(source, 20)

    gap = next(b for b in blocks if b.missing_frames > 0)
    assert gap.discontinuity_at_frame is not None
    assert gap.gap_at_frame == gap.discontinuity_at_frame
    assert gap.gap_at_frame < gap.first_frame  # the verdict arrived later
    assert gap.gap_at_frame >= 5 * BLOCK_FRAMES


def test_estimated_missing_frames_agrees_with_the_frame_deficit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two numbers the station publishes must not contradict each other.

    `expected_frames - frames` is the ground truth `continuity_ratio` is built
    on. `estimated_missing_frames` is the per-event estimate. The live station
    had them disagreeing by 12x. Over a run with a mix of absorbed stalls and
    real losses they must agree to within the estimator's own granularity.
    """
    source, device, clock = _ringed(monkeypatch)
    _drain(source, 5)

    for stall_ms in (200, 300, 900, 150, 1200, 250):
        clock.advance_ms(stall_ms)
        _drain(source, 20)

    rate = source.info.fmt.sample_rate
    elapsed_ns = clock.now - source._anchor_monotonic_ns
    expected = elapsed_ns * rate // 10**9
    deficit = expected - source._frames_read

    assert device.dropped > 0, "the 900 ms and 1200 ms stalls must overflow the ring"
    assert source.missing_frames_total == pytest.approx(device.dropped, abs=2 * BLOCK_FRAMES)
    # One block of slack: a deficit is only ever observed at block granularity.
    assert source.missing_frames_total == pytest.approx(deficit, abs=BLOCK_FRAMES)


def test_rate_offset_converges_on_the_crystal_despite_absorbed_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second number phantom frames contaminated.

    Phantom missing frames are added back into `presented`, so the station read
    +2,680 ppm against a true device offset near -43 ppm. With the estimator
    crediting only confirmed loss, stalls must leave the figure alone.
    """
    source, device, clock = _ringed(monkeypatch, ppm=-43.0)
    _drain(source, 100)

    for _ in range(10):
        clock.advance_ms(300)  # absorbed by the ring, every time
        _drain(source, 100)

    assert device.dropped == 0
    assert source.missing_frames_total == 0
    assert source.rate_offset_ppm is not None
    assert source.rate_offset_ppm == pytest.approx(-43.0, abs=5.0)


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


# -- giving up, so the supervisor can take over --------------------------
#
# The 2026-08-14 wedge (HANDOVER §1e): every read raised `Input/output error`
# for 3 h 35 min. That branch counted, logged and `continue`d inside the
# block-assembly loop, so `_read_blocking` never returned and never raised —
# and `_capture_supervisor`, which exists to reopen the device with backoff and
# would have fixed it in seconds, was never reached. `stream_restarts` stayed 0
# through 23,135 errors.
#
# Both fakes below have a hard attempt cap that raises `_Wedged`. Without it a
# regression would hang the suite forever instead of failing it.


class _Wedged(Exception):
    """The fake gave up before the source did — i.e. the source never gave up."""


class PermanentlyFailingPcm:
    """A device whose every read raises the error the wedge actually produced."""

    def __init__(self, *, cap: int = 10_000) -> None:
        self.reads = 0
        self._cap = cap

    def read(self) -> tuple[int, bytes]:
        self.reads += 1
        if self.reads > self._cap:
            raise _Wedged(f"source still looping after {self.reads} failed reads")
        raise _ensure_alsaaudio()("Input/output error [hw:CARD=Microphone,DEV=0]")


class SilentPcm:
    """A device that never errors and never returns data (`length == 0`)."""

    def __init__(self, *, cap: int = 10_000) -> None:
        self.reads = 0
        self._cap = cap

    def read(self) -> tuple[int, bytes]:
        self.reads += 1
        if self.reads > self._cap:
            raise _Wedged(f"source still looping after {self.reads} empty reads")
        return 0, b""


def _stall_clock(monkeypatch: pytest.MonkeyPatch, *, per_read_s: float) -> None:
    """Advance the wall clock the source measures stalls with, per read attempt."""
    state = {"now": 1000.0}

    def monotonic() -> float:
        state["now"] += per_read_s
        return state["now"]

    monkeypatch.setattr("open_observatory.audio.alsa_source.time.monotonic", monotonic)


def test_a_permanently_failing_device_gives_up_instead_of_looping_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _prepared_source()
    source.stall_timeout_s = 5.0
    source._pcm = PermanentlyFailingPcm()
    _stall_clock(monkeypatch, per_read_s=0.576)  # the interval the wedge logged at

    with pytest.raises(AlsaCaptureError, match="no audio"):
        source._read_blocking()


def test_a_device_returning_no_data_gives_up_instead_of_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `length == 0` branch claims to do this already, and does not."""
    source = _prepared_source()
    source.stall_timeout_s = 5.0
    source._pcm = SilentPcm()
    _stall_clock(monkeypatch, per_read_s=0.01)

    with pytest.raises(AlsaCaptureError, match="no audio"):
        source._read_blocking()


def test_a_recoverable_overrun_is_still_absorbed_without_giving_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound must not turn an ordinary xrun into a stream teardown."""
    source = _prepared_source()
    source.stall_timeout_s = 5.0
    source._pcm = FakePcm(raise_overrun_at=3)
    clock = _Clock()
    monkeypatch.setattr("open_observatory.audio.alsa_source.time.monotonic_ns", clock)
    _stall_clock(monkeypatch, per_read_s=0.001)

    block = source._read_blocking()
    assert block is not None
    assert block.frame_count == BLOCK_FRAMES
    assert source.overrun_count == 1
