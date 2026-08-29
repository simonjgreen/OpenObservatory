"""The arithmetic ADR-073 exists to enforce: drift is not loss."""

from __future__ import annotations

import pytest

from open_observatory import slo

RATE = 384_000


def test_a_perfect_stream_has_no_loss_and_no_drift() -> None:
    s = slo.split_deficit(expected_frames=RATE * 100, frames=RATE * 100, missing_frames=0, sample_rate=RATE)
    assert s.lost_frames == 0
    assert s.drift_frames == 0
    assert s.integrity_ratio == 1.0


def test_the_2026_08_25_soak_lost_no_audio_at_all() -> None:
    """The regression this module exists for.

    78.69 h, continuity 0.999943, and *zero* confirmed loss. The old ratio
    called that a 16-second shortfall. It was the crystal, and every second of
    audio was present and correct.
    """
    expected = 108_785_512_945
    frames = 108_779_366_400
    s = slo.split_deficit(expected_frames=expected, frames=frames, missing_frames=0, sample_rate=RATE)

    assert s.deficit_frames == expected - frames
    assert s.lost_frames == 0
    assert s.lost_seconds == 0.0
    assert s.integrity_ratio == 1.0  # <- the point
    assert s.drift_seconds == pytest.approx(16.0, abs=0.1)


def test_real_loss_is_charged_to_integrity_and_not_to_drift() -> None:
    """The 2026-08-22 soak: 0.597 s genuinely lost, the rest drift."""
    missing = 229_245
    expected = RATE * 259_585
    frames = expected - missing - 4_970_000  # loss plus ~12.9 s of drift
    s = slo.split_deficit(expected_frames=expected, frames=frames, missing_frames=missing, sample_rate=RATE)

    assert s.lost_seconds == pytest.approx(0.597, abs=0.001)
    assert s.drift_seconds == pytest.approx(12.9, abs=0.2)
    assert s.integrity_ratio < 1.0
    assert s.integrity_ratio > 0.999997


def test_drift_can_never_reduce_the_integrity_ratio() -> None:
    """A station with a terrible crystal and perfect capture scores 100%.

    At ~1150 ppm drift alone consumes the whole 0.1% budget the old criterion
    allowed, so a flawless station would have failed it. Integrity must be
    blind to that.
    """
    expected = RATE * 86_400
    for ppm in (50, 500, 5_000):
        frames = int(expected * (1 - ppm * 1e-6))
        s = slo.split_deficit(expected_frames=expected, frames=frames, missing_frames=0, sample_rate=RATE)
        assert s.integrity_ratio == 1.0, f"{ppm} ppm of drift reduced integrity"
        assert s.lost_frames == 0


def test_a_negative_deficit_does_not_invent_negative_drift() -> None:
    """The device can run marginally fast, or a sample can land early.

    Clamped, because a negative "drift" reads as the clock running backwards
    and a negative loss is meaningless.
    """
    s = slo.split_deficit(
        expected_frames=RATE * 10, frames=RATE * 10 + 500, missing_frames=0, sample_rate=RATE
    )
    assert s.drift_frames == 0
    assert s.lost_frames == 0
    assert s.integrity_ratio == 1.0


def test_missing_frames_exceeding_the_deficit_is_reported_not_hidden() -> None:
    """Defensive: the two counters come from different code paths.

    If confirmed loss somehow exceeds the total deficit, drift clamps to zero
    rather than going negative and silently offsetting the loss.
    """
    s = slo.split_deficit(
        expected_frames=RATE * 10, frames=RATE * 10 - 100, missing_frames=1000, sample_rate=RATE
    )
    assert s.lost_frames == 1000
    assert s.drift_frames == 0


def test_a_zero_length_stream_does_not_divide_by_zero() -> None:
    s = slo.split_deficit(expected_frames=0, frames=0, missing_frames=0, sample_rate=RATE)
    assert s.integrity_ratio == 1.0
    assert s.drift_seconds == 0.0
