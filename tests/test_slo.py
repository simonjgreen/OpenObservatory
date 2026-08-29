"""The arithmetic ADR-073 exists to enforce: drift is not loss."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


def _dt(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=UTC)


def test_full_coverage_when_one_stream_spans_the_window() -> None:
    c = slo.coverage([(_dt(1), _dt(4))], start=_dt(2), end=_dt(3))
    assert c.ratio == 1.0
    assert c.outages == []


def test_a_stream_that_straddles_the_window_edge_is_clipped_not_excluded() -> None:
    """The bug I made by hand on 2026-08-29.

    Filtering streams by `start >= cutoff` drops a stream that *spans* the
    cutoff and reports 72.857% uptime for a station that never stopped.
    Intervals must be clipped to the window, never filtered by their start.
    """
    c = slo.coverage([(_dt(1), _dt(10))], start=_dt(5), end=_dt(6))
    assert c.ratio == 1.0
    assert c.covered_seconds == 86_400.0


def test_the_gap_between_two_streams_is_an_outage() -> None:
    c = slo.coverage(
        [(_dt(1), _dt(2, 0)), (_dt(2, 1), _dt(3))],
        start=_dt(1),
        end=_dt(3),
    )
    assert len(c.outages) == 1
    when, seconds = c.outages[0]
    assert when == _dt(2, 0)
    assert seconds == 3600.0
    assert c.ratio == pytest.approx(1 - 3600 / 172_800)


def test_overlapping_streams_are_not_double_counted() -> None:
    """Coverage is of the window, not the sum of stream lengths.

    A reopen can write overlapping rows; summing them would report >100%.
    """
    c = slo.coverage(
        [(_dt(1), _dt(3)), (_dt(2), _dt(4))],
        start=_dt(1),
        end=_dt(4),
    )
    assert c.ratio == 1.0
    assert c.covered_seconds == 3 * 86_400.0


def test_an_outage_at_the_end_of_the_window_still_counts() -> None:
    c = slo.coverage([(_dt(1), _dt(2))], start=_dt(1), end=_dt(3))
    assert c.ratio == pytest.approx(0.5)
    assert len(c.outages) == 1


def test_no_streams_at_all_is_zero_coverage_not_a_crash() -> None:
    c = slo.coverage([], start=_dt(1), end=_dt(2))
    assert c.ratio == 0.0
    assert c.covered_seconds == 0.0


def test_the_measured_steady_state_meets_the_slo() -> None:
    """The real figure from 2026-08-19 to 29: three outages, 1.9 minutes."""
    start = _dt(19)
    end = start + timedelta(days=9.8)
    intervals = [
        (start, start + timedelta(days=2.66)),
        (start + timedelta(days=2.66, seconds=105), start + timedelta(days=5.74)),
        (start + timedelta(days=5.74, seconds=4), start + timedelta(days=9.07)),
        (start + timedelta(days=9.07, seconds=6), end),
    ]
    c = slo.coverage(intervals, start=start, end=end)
    assert c.ratio > 0.995  # SLO A
    assert len(c.outages) == 3


SURREY = {"latitude": 51.24, "longitude": -0.59}


def test_prime_intervals_bracket_dawn_and_dusk() -> None:
    ivs = slo.prime_intervals(_dt(15), _dt(16), **SURREY)
    assert ivs, "August in Surrey has both a dawn and a dusk"
    for s, e in ivs:
        assert e > s
        assert (e - s).total_seconds() <= 4 * 3600 + 1  # 2 h either side


def test_prime_intervals_do_not_overlap_and_are_ordered() -> None:
    ivs = slo.prime_intervals(_dt(15), _dt(20), **SURREY)
    for a, b in zip(ivs, ivs[1:], strict=False):  # noqa: RUF007
        assert a[1] <= b[0], "intervals must be merged and ordered"


def test_prime_coverage_is_perfect_when_capture_never_stops() -> None:
    ivs = slo.prime_intervals(_dt(15), _dt(18), **SURREY)
    c = slo.coverage([(_dt(14), _dt(19))], start=_dt(15), end=_dt(18))
    assert c.ratio == 1.0
    total_prime = sum((e - s).total_seconds() for s, e in ivs)
    assert total_prime > 0


def test_an_outage_at_noon_does_not_touch_prime_coverage() -> None:
    """The whole point of A2. Down for four hours in the middle of the day."""
    ivs = slo.prime_intervals(_dt(15), _dt(16), **SURREY)
    running = [(_dt(15), _dt(15, 11)), (_dt(15, 15), _dt(16))]
    prime_covered = 0.0
    prime_total = 0.0
    for ps, pe in ivs:
        c = slo.coverage(running, start=ps, end=pe)
        prime_covered += c.covered_seconds
        prime_total += c.span_seconds
    assert prime_total > 0
    assert prime_covered / prime_total == 1.0, "a midday outage must not count against A2"


def test_polar_latitudes_yield_no_prime_window_rather_than_crashing() -> None:
    """June above the Arctic circle has no civil twilight at all."""
    ivs = slo.prime_intervals(
        datetime(2026, 6, 20, tzinfo=UTC),
        datetime(2026, 6, 22, tzinfo=UTC),
        latitude=78.9,
        longitude=11.9,
    )
    assert ivs == []
