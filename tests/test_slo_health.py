"""The health payload must report loss and drift as different things."""

from __future__ import annotations

# Reuses the real end-to-end fixture (synthetic capture through the actual
# FastAPI app) rather than redefining it -- this is the same client
# `test_api.py` uses to assert on `continuity_ratio` itself.
from tests.test_api import client as client

from open_observatory import slo


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

    # This fixture runs a real synthetic stream, so a stream and clock exist
    # and `capture_integrity_ratio` is a number, not the no-stream `None`.
    # A station losing no audio scores 100% integrity no matter what the
    # crystal is doing -- that disagreement with `continuity_ratio` is the
    # entire point of ADR-073, so pin it here rather than in slo.py alone.
    if capture["capture_integrity_ratio"] is not None and capture["audio_lost_seconds"] == 0.0:
        assert capture["capture_integrity_ratio"] == 1.0
