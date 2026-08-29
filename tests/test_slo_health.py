"""The health payload must report loss and drift as different things."""

from __future__ import annotations

import asyncio
import time
import uuid

import numpy as np
import pytest

# Reuses the real end-to-end fixture (synthetic capture through the actual
# FastAPI app) rather than redefining it -- this is the same client
# `test_api.py` uses to assert on `continuity_ratio` itself.
from tests.test_api import client as client

from open_observatory import slo
from open_observatory.audio.contracts import (
    NS_PER_S,
    AudioFormat,
    CaptureBlock,
    ClockCorrelation,
    DiscontinuityReason,
    SourceKind,
    StreamClock,
    StreamInfo,
)


def test_the_health_keys_decompose_the_deficit_exactly() -> None:
    """Whatever the payload says, lost + drift must equal the deficit.

    This is the invariant that stops the two ever drifting apart in the
    reporting layer -- the place ADR-073 says the category error lived.
    """
    s = slo.split_deficit(
        expected_frames=384_000 * 3600,
        frames=384_000 * 3600 - 100_000,
        missing_frames=20_000,
        sample_rate=384_000,
    )
    assert s.lost_frames + s.drift_frames == s.deficit_frames


def test_integrity_ratio_and_continuity_ratio_disagree_and_that_is_correct() -> None:
    """The old number and the new one are not interchangeable.

    continuity_ratio charges drift to the station. integrity_ratio does not.
    A test asserting they match would be asserting the bug.
    """
    expected = 384_000 * 3600
    frames = expected - 100_000  # pure drift, nothing lost
    s = slo.split_deficit(expected_frames=expected, frames=frames, missing_frames=0, sample_rate=384_000)
    continuity = frames / expected
    assert continuity < 1.0
    assert s.integrity_ratio == 1.0


def test_the_health_payload_carries_loss_drift_and_integrity_alongside_continuity(
    client,
) -> None:
    """The wiring, not just the arithmetic.

    `split_deficit` is already proven correct by the tests above (and by
    `tests/test_slo.py`). What is NOT proven anywhere else is that
    `station.py` actually calls it and puts the result in the payload
    `/api/v1/station` serves -- delete the wiring in station.py and every
    other test still passes. This is the one that would not.
    """
    capture = client.get("/api/v1/station").json()["capture"]

    # continuity_ratio must never be removed -- ADR-073 and two other ADRs
    # reference it, and it is the number these three are explaining, not
    # replacing.
    assert "continuity_ratio" in capture

    for key in ("audio_lost_seconds", "drift_seconds", "capture_integrity_ratio"):
        assert key in capture

    # Unconditional, deliberately. The guarded form this replaced --
    # `if integrity is not None and audio_lost_seconds == 0.0` -- disarmed
    # itself under the exact regression it was written to catch: feed
    # `split_deficit` a process-lifetime loss counter instead of this stream's
    # and `audio_lost_seconds` goes non-zero, the guard turns False, and the
    # assertion never runs. A conditional assertion cannot fail.
    #
    # This fixture runs a real synthetic stream that loses nothing, so:
    assert capture["audio_lost_seconds"] == 0.0
    assert capture["capture_integrity_ratio"] == 1.0

    # ...and the crystal-free synthetic source still lags the nominal rate
    # slightly, so continuity charges the station for audio that was never
    # lost. That the two numbers *disagree* is the whole of ADR-073; pin it
    # here at the payload, not only in slo.py's arithmetic.
    assert capture["continuity_ratio"] < 1.0


# -- the stream-scoped loss counter (ADR-073 / the CaptureCounters docstring) --


def _station(settings):
    from open_observatory.station import Station

    station = Station(settings)
    # These three write rows, want a live engine, and none of them is what
    # these tests are about: the subject is which counter reaches
    # `split_deficit`, and under what condition it is computed at all.
    station._insert_gap_row = lambda block: None  # type: ignore[method-assign]
    station._upsert_device_and_stream = lambda info: None  # type: ignore[method-assign]
    station._upsert_detector_rows = lambda: None  # type: ignore[method-assign]
    return station


def _stream(rate: int) -> StreamInfo:
    return StreamInfo(
        stream_id=uuid.uuid4(),
        source_kind=SourceKind.SYNTHETIC,
        device_key="test",
        device_label="test device",
        fmt=AudioFormat(sample_rate=rate, channels=1, sample_format="FLOAT_LE"),
        started_monotonic_ns=time.monotonic_ns(),
        clock=ClockCorrelation.sample(),
    )


def _anchor(station, info: StreamInfo, *, seconds_ago: float, frames: int) -> None:
    """Put the station on `info` as though capture had been running that long."""
    station.stream = info
    station.clock = StreamClock(
        utc_ns_at_frame_zero=1_700_000_000 * NS_PER_S,
        monotonic_ns_at_frame_zero=time.monotonic_ns() - int(seconds_ago * NS_PER_S),
    )
    station._stream_frames = frames
    station.capture_state = "capturing"


def _gap(missing_frames: int, rate: int) -> CaptureBlock:
    return CaptureBlock(
        stream_id=uuid.uuid4(),
        sequence=0,
        first_frame=0,
        sample_rate=rate,
        pcm=np.zeros(8, dtype="float32"),
        monotonic_start_ns=0,
        clock=ClockCorrelation.sample(),
        discontinuity=DiscontinuityReason.OVERRUN,
        missing_frames=missing_frames,
    )


async def test_a_reopened_stream_does_not_inherit_the_previous_streams_loss(
    settings,
) -> None:
    """C1. The device re-enumerates; the process does not restart.

    `_stream_frames` resets on reopen and `counters.estimated_missing_frames`
    -- process lifetime by design -- does not. Feeding the second into a
    calculation scoped to the first is precisely what `CaptureCounters`'
    docstring forbids: one second into a spotless new stream the integrity
    ratio read 0.466 for a stream that had lost nothing, and stayed under the
    0.9999 SLO for about 89 minutes. Worse, `drift = max(0, deficit - lost)`
    clamps to zero throughout that window, so drift reads *zero* at exactly
    the moment ADR-073 says drift dominates.
    """
    rate = 48_000
    station = _station(settings)

    first_info = _stream(rate)
    _anchor(station, first_info, seconds_ago=2.0, frames=2 * rate)
    station._record_gap(_gap(rate // 2, rate))  # half a second genuinely lost
    await asyncio.sleep(0.05)  # let the gap-row task finish

    first = station.status_snapshot()["capture"]
    assert first["audio_lost_seconds"] == pytest.approx(0.5, abs=0.01)
    assert first["capture_integrity_ratio"] < 1.0

    # The supervisor reopens the device. Same process, new stream, one second
    # in, 10 ms behind the nominal rate -- i.e. pure crystal, nothing lost.
    second_info = _stream(rate)
    await station._on_stream_open(second_info)
    _anchor(station, second_info, seconds_ago=1.0, frames=rate - 480)

    second = station.status_snapshot()["capture"]
    assert second["audio_lost_seconds"] == 0.0
    assert second["capture_integrity_ratio"] == 1.0
    # And the residual is reported as drift rather than clamped to zero by the
    # previous stream's phantom loss -- drift reading zero at the moment it
    # dominates is the second half of this bug.
    assert second["drift_seconds"] == pytest.approx(0.01, abs=0.005)

    # ...while the process-lifetime counter and its payload key are untouched.
    # Other code depends on them answering "since this process started".
    assert station.counters.estimated_missing_frames == rate // 2
    assert second["estimated_missing_frames"] == rate // 2
    assert second["estimated_missing_seconds"] == pytest.approx(0.5, abs=0.01)


async def test_a_dead_stream_reports_no_integrity_rather_than_a_rising_one(
    settings,
) -> None:
    """I1. Between a capture failure and the next open, nothing is captured.

    `_on_stream_close` clears only `self.source`; `self.stream` and
    `self.clock` survive until a *successful* reopen, so during a backoff of
    up to 30 s the dead stream's anchor keeps `expected_frames` growing while
    `_stream_frames` is frozen. Guarded on `stream is not None` alone, the
    whole outage lands in `drift_seconds` and `capture_integrity_ratio` climbs
    back towards 1.0 the longer capture stays dead -- ADR-073's failure mode
    exactly inverted, a real outage reported as the crystal.
    """
    rate = 48_000
    station = _station(settings)
    info = _stream(rate)
    _anchor(station, info, seconds_ago=10.0, frames=5 * rate)

    live = station.status_snapshot()["capture"]
    assert live["capture_integrity_ratio"] is not None

    # The supervisor's failure path: state goes to "error", stream and clock
    # are deliberately left in place.
    station._set_capture_state("error", "OSError: device gone")

    dead = station.status_snapshot()["capture"]
    assert dead["capture_integrity_ratio"] is None
    assert dead["audio_lost_seconds"] == 0.0
    assert dead["drift_seconds"] == 0.0
    # continuity_ratio is ADR-073's *subject*, not its target. Unchanged.
    assert dead["continuity_ratio"] is not None
    assert dead["continuity_ratio"] == pytest.approx(live["continuity_ratio"], abs=0.01)
