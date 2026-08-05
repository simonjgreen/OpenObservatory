"""Tests for feeding-buzz flagging and candidate-frequency naming.

The pulse-train mechanics (pass detection, click rejection, taxonomy honesty)
are already covered in ``tests/test_detectors.py::TestUltrasonicDetector``.
This file is scoped to the additions in
``docs/superpowers/specs/2026-08-05-bat-feeding-buzz-and-frequency-titles-design.md``:
buzz detection over the kept pulse-interval series, the extended
``frequency_candidate`` naming, and the night-schedule gate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from open_observatory.audio.contracts import NS_PER_S, AudioWindow, WindowSpec
from open_observatory.detectors.base import DetectorContext
from open_observatory.detectors.ultrasonic import UltrasonicDetector, frequency_candidate

RATE = 384000
UTC0_NS = 1_700_000_000 * NS_PER_S


def make_window(
    pcm: np.ndarray, rate: int, spec: WindowSpec, *, start_frame: int = 0, utc_start_ns: int = UTC0_NS
) -> AudioWindow:
    duration_ns = int(pcm.shape[0] * NS_PER_S / rate)
    return AudioWindow(
        window_id=uuid.uuid4(),
        stream_id=uuid.uuid4(),
        stream_kind=spec.stream_kind,
        sample_rate=rate,
        start_frame=start_frame,
        end_frame=start_frame + int(pcm.shape[0]),
        native_start_frame=start_frame,
        native_end_frame=start_frame + int(pcm.shape[0]),
        utc_start_ns=utc_start_ns,
        utc_end_ns=utc_start_ns + duration_ns,
        monotonic_start_ns=0,
        pcm=np.ascontiguousarray(pcm, dtype=np.float32),
        spec=spec,
        created_monotonic_ns=0,
    )


def chirp(rate: int, duration_s: float, f0: float, f1: float, amplitude: float = 0.3) -> np.ndarray:
    n = int(rate * duration_s)
    t = np.arange(n) / rate
    freq = f0 + (f1 - f0) * (t / max(duration_s, 1e-9))
    envelope = np.sin(np.pi * np.clip(t / duration_s, 0, 1)) ** 1.5
    return (amplitude * envelope * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def pulse_train_signal(
    pulse_times_s: list[float],
    *,
    rate: int = RATE,
    duration_s: float = 2.0,
    f0: float = 55_000.0,
    f1: float = 40_000.0,
    pulse_ms: float = 4.0,
    amplitude: float = 0.5,
    seed: int = 2,
) -> np.ndarray:
    """A pulse train with pulses placed at exactly the given offsets."""
    rng = np.random.default_rng(seed)
    signal = rng.normal(0, 0.0005, int(rate * duration_s)).astype(np.float32)
    pulse = chirp(rate, pulse_ms / 1000.0, f0, f1, amplitude=amplitude)
    for t in pulse_times_s:
        begin = int(t * rate)
        end = begin + pulse.shape[0]
        assert end <= signal.shape[0], "test pulse falls outside the synthetic window"
        signal[begin:end] += pulse
    return signal


async def _detector(**kwargs) -> UltrasonicDetector:
    detector = UltrasonicDetector(native_sample_rate=RATE, **kwargs)
    await detector.initialise(
        DetectorContext(station_name="t", timezone="UTC", latitude=None, longitude=None)
    )
    return detector


class _StubSchedule:
    """A minimal is_active(now) stub, deliberately not the real NightSchedule."""

    def __init__(self, active: bool) -> None:
        self._active = active
        self.calls: list[datetime] = []

    def is_active(self, now: datetime) -> bool:
        self.calls.append(now)
        return self._active


class TestFeedingBuzz:
    async def test_terminal_collapse_is_flagged(self) -> None:
        # 8 pulses at a steady 90 ms cadence, then a terminal collapse into 7
        # pulses at 5 ms — a real feeding buzz.
        slow_times = [0.2 + i * 0.09 for i in range(8)]
        last_slow = slow_times[-1]
        fast_times = [last_slow + (i + 1) * 0.005 for i in range(6)]
        times = slow_times + fast_times

        detector = await _detector()
        signal = pulse_train_signal(times, duration_s=1.5)
        found = await detector.analyse(make_window(signal, RATE, detector.window_spec))
        assert found, "the train must still be detected as a pass"
        result = found[0].native_result

        assert result["has_feeding_buzz"] is True
        assert result["buzz_pulse_count"] is not None
        assert result["buzz_pulse_count"] >= 5
        assert result["buzz_offset_s"] == pytest.approx(last_slow - times[0], abs=0.01)
        assert result["buzz_min_interval_ms"] < 12.0
        assert result["min_interval_ms"] < 12.0

    async def test_uniform_long_intervals_are_not_flagged(self) -> None:
        times = [0.2 + i * 0.09 for i in range(10)]
        detector = await _detector()
        signal = pulse_train_signal(times, duration_s=1.5)
        found = await detector.analyse(make_window(signal, RATE, detector.window_spec))
        assert found
        result = found[0].native_result
        assert result["has_feeding_buzz"] is False
        assert result["buzz_offset_s"] is None
        assert result["buzz_pulse_count"] is None
        # min_interval_ms is still reported even with no buzz.
        assert result["min_interval_ms"] == pytest.approx(90.0, abs=1.0)

    async def test_fast_throughout_with_no_terminal_collapse_is_not_flagged(self) -> None:
        """The ratio condition, not just the run-length one, must gate the flag.

        Every interval here is below buzz_max_interval_ms, so a run-length-only
        rule would flag this. The train calls fast for its entire duration with
        no true terminal collapse, so the ratio test (run median vs whole-train
        median) must suppress it.
        """
        times = [0.2 + i * 0.010 for i in range(12)]
        detector = await _detector()
        signal = pulse_train_signal(times, duration_s=1.0, pulse_ms=3.0)
        found = await detector.analyse(make_window(signal, RATE, detector.window_spec))
        assert found
        result = found[0].native_result
        assert result["has_feeding_buzz"] is False, (
            "a uniformly fast train has no terminal collapse and must not be "
            "flagged as a feeding buzz"
        )
        assert result["min_interval_ms"] < 12.0, "sanity: the intervals really are short"

    async def test_run_shorter_than_minimum_is_not_flagged(self) -> None:
        # Only 3 consecutive fast intervals; buzz_min_pulses default is 5.
        slow_times = [0.2 + i * 0.09 for i in range(8)]
        last_slow = slow_times[-1]
        fast_times = [last_slow + (i + 1) * 0.005 for i in range(3)]
        times = slow_times + fast_times

        detector = await _detector()
        signal = pulse_train_signal(times, duration_s=1.5)
        found = await detector.analyse(make_window(signal, RATE, detector.window_spec))
        assert found
        result = found[0].native_result
        assert result["has_feeding_buzz"] is False
        assert result["buzz_offset_s"] is None

    async def test_buzz_thresholds_are_configurable(self) -> None:
        # A run of 4 fast pulses, which the default min_pulses=5 would reject,
        # must be accepted once buzz_min_pulses is lowered to 4.
        slow_times = [0.2 + i * 0.09 for i in range(6)]
        last_slow = slow_times[-1]
        fast_times = [last_slow + (i + 1) * 0.005 for i in range(4)]
        times = slow_times + fast_times

        detector = await _detector(buzz_min_pulses=4)
        signal = pulse_train_signal(times, duration_s=1.5)
        found = await detector.analyse(make_window(signal, RATE, detector.window_spec))
        assert found
        assert found[0].native_result["has_feeding_buzz"] is True


class TestFrequencyCandidate:
    def test_low_band_carries_the_bush_cricket_ambiguity(self) -> None:
        name, ambiguity = frequency_candidate(18_000)
        assert name == "noctule / serotine"
        assert ambiguity == "may be a bush-cricket"

    def test_upper_half_of_the_split_band_has_no_ambiguity(self) -> None:
        name, ambiguity = frequency_candidate(23_000)
        assert name == "noctule / serotine"
        assert ambiguity is None

    def test_exact_boundary_belongs_to_the_upper_band(self) -> None:
        # 21 kHz exactly is the split point: [17k, 21k) is ambiguous, [21k, 26k)
        # is not, and the boundary value itself must land in the latter.
        name, ambiguity = frequency_candidate(21_000)
        assert name == "noctule / serotine"
        assert ambiguity is None

    def test_common_pipistrelle_band(self) -> None:
        assert frequency_candidate(45_000) == ("common pipistrelle", None)

    def test_soprano_pipistrelle_band(self) -> None:
        assert frequency_candidate(55_000) == ("soprano pipistrelle", None)

    def test_horseshoe_bands(self) -> None:
        assert frequency_candidate(70_000) == ("greater horseshoe", None)
        assert frequency_candidate(100_000) == ("lesser horseshoe", None)

    def test_above_the_top_band_returns_nothing(self) -> None:
        assert frequency_candidate(130_000) == (None, None)
        assert frequency_candidate(200_000) == (None, None)

    def test_below_the_bottom_band_returns_nothing(self) -> None:
        assert frequency_candidate(10_000) == (None, None)

    async def test_candidate_fields_reach_native_result(self) -> None:
        times = [0.2 + i * 0.09 for i in range(8)]
        detector = await _detector()
        # A constant 45 kHz tone, not a sweep, so the measured peak sits
        # unambiguously inside the common-pipistrelle band.
        signal = pulse_train_signal(times, duration_s=1.5, f0=45_000.0, f1=45_000.0)
        found = await detector.analyse(make_window(signal, RATE, detector.window_spec))
        assert found
        result = found[0].native_result
        assert result["candidate_name"] == "common pipistrelle"
        assert result["candidate_ambiguity"] is None


class TestScheduleGate:
    def _signal(self) -> np.ndarray:
        times = [0.2 + i * 0.09 for i in range(8)]
        return pulse_train_signal(times, duration_s=1.5)

    async def test_inactive_schedule_returns_no_detections(self) -> None:
        schedule = _StubSchedule(active=False)
        detector = await _detector(schedule=schedule)

        # Prove the gate precedes the expensive path: a signal that would
        # normally produce a detection must not even reach _find_pulses.
        def _boom(*args, **kwargs):
            raise AssertionError("_find_pulses ran despite an inactive schedule")

        detector._find_pulses = _boom  # type: ignore[method-assign]

        window = make_window(self._signal(), RATE, detector.window_spec)
        assert await detector.analyse(window) == []
        assert schedule.calls, "the schedule must actually be consulted"

    async def test_active_schedule_behaves_as_before(self) -> None:
        schedule = _StubSchedule(active=True)
        detector = await _detector(schedule=schedule)
        window = make_window(self._signal(), RATE, detector.window_spec)
        found = await detector.analyse(window)
        assert found, "an active schedule must not suppress a genuine pass"
        assert schedule.calls

    async def test_schedule_is_checked_against_window_time_not_wall_clock(self) -> None:
        schedule = _StubSchedule(active=True)
        detector = await _detector(schedule=schedule)
        window_utc_ns = UTC0_NS + 3600 * NS_PER_S  # arbitrary, far from "now"
        window = make_window(
            self._signal(), RATE, detector.window_spec, utc_start_ns=window_utc_ns
        )
        await detector.analyse(window)
        assert schedule.calls[-1] == datetime.fromtimestamp(window_utc_ns / NS_PER_S, tz=UTC)
        # And nowhere near actual wall-clock now.
        assert abs((schedule.calls[-1] - datetime.now(UTC)).total_seconds()) > timedelta(
            days=1
        ).total_seconds()

    async def test_no_schedule_means_always_on(self) -> None:
        detector = await _detector()
        window = make_window(self._signal(), RATE, detector.window_spec)
        assert await detector.analyse(window)

    async def test_gated_state_is_visible_in_health(self) -> None:
        schedule = _StubSchedule(active=False)
        detector = await _detector(schedule=schedule)
        window = make_window(self._signal(), RATE, detector.window_spec)
        await detector.analyse(window)
        health = await detector.health()
        assert "schedule" in health.detail.lower()
        assert "1" in health.detail


class TestPeakFrequencyResolution:
    """The pulse FFT has 3 kHz bins at 384 kHz, and the candidate-species band
    edges fall between bin centres. Without sub-bin interpolation every reported
    peak is a multiple of 3 kHz and the band assignment is decided by
    quantisation rather than by the call."""

    async def test_off_bin_tone_is_not_quantised_to_the_bin_centre(self) -> None:
        # 37_500 Hz sits exactly between the 36 kHz and 39 kHz bins, and exactly
        # on the Myotis / common-pipistrelle boundary at 38 kHz.
        detector = await _detector(min_pulses_per_pass=3)
        times = [0.20, 0.35, 0.50, 0.65]
        signal = pulse_train_signal(times, f0=37_500.0, f1=37_500.0, pulse_ms=6.0)
        window = make_window(signal, RATE, detector.window_spec)

        detections = await detector.analyse(window)

        assert detections, "expected the tone train to be detected"
        peak = detections[0].peak_frequency_hz
        assert peak is not None
        # Within half a bin of the truth, and demonstrably not snapped to a
        # multiple of the 3 kHz bin width.
        assert abs(peak - 37_500.0) < 1_500.0, f"peak {peak} is off by more than half a bin"
        assert peak % 3000.0 != 0.0, f"peak {peak} was quantised to a bin centre"

    async def test_interpolation_stays_within_neighbouring_half_bins(self) -> None:
        from open_observatory.detectors.ultrasonic import _interpolated_peak_hz

        freqs = np.array([30_000.0, 33_000.0, 36_000.0], dtype=float)
        # A pathological column: the parabola through these points has its vertex
        # far outside the peak bin, and must be clamped rather than trusted.
        column = np.array([1.0, 1.0000001, 1e-30], dtype=float)
        hz = _interpolated_peak_hz(column, 1, freqs)
        assert 31_500.0 <= hz <= 34_500.0

    async def test_edge_bin_falls_back_to_the_bin_centre(self) -> None:
        from open_observatory.detectors.ultrasonic import _interpolated_peak_hz

        freqs = np.array([30_000.0, 33_000.0, 36_000.0], dtype=float)
        column = np.array([9.0, 1.0, 1.0], dtype=float)
        assert _interpolated_peak_hz(column, 0, freqs) == 30_000.0
        assert _interpolated_peak_hz(column, 2, freqs) == 36_000.0


class TestFragmentMerging:
    """A bat call is one frequency sweep whose envelope dips mid-call, so it
    arrives as several threshold crossings a millisecond or two apart. Counting
    those as separate pulses lets the fragments of a single call satisfy
    min_pulses_per_pass and manufacture a bat pass, and it destroys the
    call-to-call interval series that buzz detection depends on."""

    async def test_fragments_of_one_call_become_one_pulse(self) -> None:
        detector = await _detector(min_pulses_per_pass=3)
        # Three crossings 1 ms apart: one call, not a pass.
        fragments = [0.300, 0.3015, 0.3030]
        signal = pulse_train_signal(fragments, pulse_ms=1.6, f0=45_000.0, f1=45_000.0)
        window = make_window(signal, RATE, detector.window_spec)

        detections = await detector.analyse(window)

        assert not detections, (
            "fragments of a single call must not satisfy min_pulses_per_pass; "
            f"got {detections}"
        )

    async def test_buzz_spaced_calls_are_not_merged(self) -> None:
        """The decisive counter-test: a feeding buzz's calls are ~6 ms apart and
        must survive de-fragmentation, or the fix would hide the thing we added
        buzz detection to find."""
        detector = await _detector(min_pulses_per_pass=3)
        calls = [0.300 + i * 0.006 for i in range(8)]
        signal = pulse_train_signal(calls, pulse_ms=2.0, f0=45_000.0, f1=45_000.0)
        window = make_window(signal, RATE, detector.window_spec)

        detections = await detector.analyse(window)

        assert detections, "buzz-spaced calls should still form a pass"
        assert detections[0].native_result["pulse_count"] >= 6, (
            "6 ms spacing is a buzz, not intra-call fragmentation, and must not be "
            f"merged away: {detections[0].native_result}"
        )

    def test_merge_is_a_pure_function_over_pulses(self) -> None:
        """Unit-level, because through `analyse` the two paths are
        indistinguishable: crossings close enough to be fragments are usually
        contiguous above threshold anyway, and sub-1.5 ms pieces are filtered by
        min_pulse_ms before a pass is formed."""
        from open_observatory.detectors.ultrasonic import Pulse

        detector = UltrasonicDetector(native_sample_rate=RATE, merge_gap_ms=2.0)
        fragments = [
            Pulse(offset_s=0.300, duration_s=0.0008, peak_hz=45_000.0, snr_db=20.0),
            Pulse(offset_s=0.3013, duration_s=0.0008, peak_hz=44_000.0, snr_db=26.0),
            Pulse(offset_s=0.3026, duration_s=0.0008, peak_hz=45_500.0, snr_db=18.0),
        ]

        merged = detector._merge_fragments(fragments)

        assert len(merged) == 1
        assert merged[0].offset_s == pytest.approx(0.300)
        assert merged[0].duration_s == pytest.approx(0.0034, abs=1e-4)
        # The loudest fragment decides the frequency: a sweep's quiet tail is a
        # worse estimate of the call than its strongest part.
        assert merged[0].peak_hz == pytest.approx(44_000.0)
        assert merged[0].snr_db == pytest.approx(26.0)

    def test_merge_disabled_returns_pulses_untouched(self) -> None:
        from open_observatory.detectors.ultrasonic import Pulse

        detector = UltrasonicDetector(native_sample_rate=RATE, merge_gap_ms=0.0)
        fragments = [
            Pulse(offset_s=0.300, duration_s=0.0008, peak_hz=45_000.0, snr_db=20.0),
            Pulse(offset_s=0.3013, duration_s=0.0008, peak_hz=44_000.0, snr_db=26.0),
        ]

        assert detector._merge_fragments(fragments) == fragments

    def test_merge_never_exceeds_a_plausible_single_call(self) -> None:
        """Onset spacing alone is not enough: a long run of near-simultaneous
        crossings must not accumulate into one implausibly long "call"."""
        from open_observatory.detectors.ultrasonic import Pulse

        detector = UltrasonicDetector(
            native_sample_rate=RATE, merge_gap_ms=2.0, max_pulse_ms=10.0
        )
        fragments = [
            Pulse(offset_s=0.300 + i * 0.0015, duration_s=0.0008, peak_hz=45_000.0, snr_db=20.0)
            for i in range(20)
        ]

        merged = detector._merge_fragments(fragments)

        assert len(merged) > 1, "merging must stop at max_pulse_ms, not run away"
        assert all(p.duration_s <= 0.010 + 1e-6 for p in merged)
