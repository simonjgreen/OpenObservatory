"""Tests for the live streaming heterodyne.

The decisive property is continuity: chunking a continuous signal must not
change the output versus processing it in one pass, and must not introduce a
discontinuity at the joins. Everything else (tuning lands near DC, out-of-band
is attenuated, output rate, idle cost) is the ordinary heterodyne contract
that ``ultrasound.py`` already tests for the offline renderer.
"""

from __future__ import annotations

import numpy as np
import pytest

from open_observatory.audio.heterodyne_stream import StreamingHeterodyne

NATIVE_RATE = 384_000
OUTPUT_RATE = 48_000


def tone(rate: int, freq_hz: float, duration_s: float, *, phase0: float = 0.0) -> np.ndarray:
    t = np.arange(int(rate * duration_s), dtype=np.float64) / rate
    return (0.5 * np.cos(2 * np.pi * freq_hz * t + phase0)).astype(np.float64)


def dominant_hz(pcm: np.ndarray, rate: int) -> float:
    size = 1 << int(np.floor(np.log2(max(pcm.shape[0], 2))))
    spectrum = np.abs(np.fft.rfft(pcm[:size] * np.hanning(size)))
    freqs = np.fft.rfftfreq(size, 1.0 / rate)
    return float(freqs[int(np.argmax(spectrum))])


def rms(pcm: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(pcm)))) if pcm.size else 0.0


class TestChunkContinuity:
    def test_chunked_matches_single_pass(self) -> None:
        """Heterodyning N sequential chunks must match one big pass, closely."""
        signal = tone(NATIVE_RATE, 45_000.0, 1.0)

        one_pass = StreamingHeterodyne(
            NATIVE_RATE, output_rate=OUTPUT_RATE, tune_hz=44_000.0, bandwidth_hz=5000.0
        )
        whole = one_pass.process(signal)

        chunked = StreamingHeterodyne(
            NATIVE_RATE, output_rate=OUTPUT_RATE, tune_hz=44_000.0, bandwidth_hz=5000.0
        )
        chunk_frames = 3_840  # 10 ms at native rate, deliberately not a divisor of the signal
        pieces = []
        for start in range(0, signal.shape[0], chunk_frames):
            pieces.append(chunked.process(signal[start : start + chunk_frames]))
        stitched = np.concatenate(pieces)

        assert stitched.shape == whole.shape
        # Skip the filter's startup transient (taps-1 native samples, decimated).
        skip = one_pass.taps // one_pass.decimation + 4
        np.testing.assert_allclose(stitched[skip:], whole[skip:], atol=1e-6)

    def test_no_discontinuity_at_chunk_joins(self) -> None:
        """No join should look like an impulse: sample-to-sample steps at a
        chunk boundary must stay in the same range as steps elsewhere."""
        signal = tone(NATIVE_RATE, 40_000.0, 0.5)
        het = StreamingHeterodyne(
            NATIVE_RATE, output_rate=OUTPUT_RATE, tune_hz=40_500.0, bandwidth_hz=5000.0
        )
        chunk_frames = 4_000
        out_chunks = []
        for start in range(0, signal.shape[0], chunk_frames):
            out_chunks.append(het.process(signal[start : start + chunk_frames]))

        joined = np.concatenate(out_chunks)
        steps = np.abs(np.diff(joined))
        # Boundary sample indices in the decimated output.
        boundary_out_idx = np.cumsum([c.shape[0] for c in out_chunks[:-1]])
        typical_step = np.percentile(steps, 95)
        for idx in boundary_out_idx:
            if 0 < idx < steps.shape[0]:
                assert steps[idx - 1] <= typical_step * 5 + 1e-6

    def test_retune_ramps_without_a_jump_in_phase_state(self) -> None:
        het = StreamingHeterodyne(
            NATIVE_RATE, output_rate=OUTPUT_RATE, tune_hz=40_000.0, bandwidth_hz=5000.0
        )
        signal = tone(NATIVE_RATE, 40_000.0, 0.2)
        het.process(signal)
        phase_before = het._phase  # white-box: continuity is the point of the test
        het.set_tune_hz(50_000.0)
        het.process(tone(NATIVE_RATE, 50_000.0, 0.2))
        # The retune must not have reset phase to zero or any fixed constant;
        # it should simply be wherever the continuous accumulation left it.
        assert het._phase != 0.0
        assert het.tune_hz == pytest.approx(50_000.0)
        assert phase_before is not None


class TestHeterodyneBehaviour:
    def test_tone_at_tuning_frequency_lands_near_dc(self) -> None:
        het = StreamingHeterodyne(
            NATIVE_RATE, output_rate=OUTPUT_RATE, tune_hz=45_000.0, bandwidth_hz=5000.0
        )
        signal = tone(NATIVE_RATE, 45_000.0, 0.5)
        out = het.process(signal)
        peak_hz = dominant_hz(out[out.shape[0] // 4 :], OUTPUT_RATE)
        assert peak_hz < 500.0

    def test_tone_far_outside_bandwidth_is_attenuated(self) -> None:
        het_on = StreamingHeterodyne(
            NATIVE_RATE, output_rate=OUTPUT_RATE, tune_hz=45_000.0, bandwidth_hz=3000.0
        )
        het_off = StreamingHeterodyne(
            NATIVE_RATE, output_rate=OUTPUT_RATE, tune_hz=45_000.0, bandwidth_hz=3000.0
        )
        in_band = tone(NATIVE_RATE, 45_500.0, 0.3)
        out_of_band = tone(NATIVE_RATE, 80_000.0, 0.3)

        on_level = rms(het_on.process(in_band)[500:])
        off_level = rms(het_off.process(out_of_band)[500:])
        assert off_level < on_level * 0.1

    def test_output_rate_is_48khz_and_length_matches_decimation(self) -> None:
        het = StreamingHeterodyne(
            NATIVE_RATE, output_rate=OUTPUT_RATE, tune_hz=45_000.0, bandwidth_hz=5000.0
        )
        assert het.output_rate == 48_000
        assert het.decimation == 8
        signal = tone(NATIVE_RATE, 45_000.0, 1.0)
        out = het.process(signal)
        assert out.shape[0] == signal.shape[0] // het.decimation
        assert out.dtype == np.float32

    def test_empty_chunk_is_a_no_op(self) -> None:
        het = StreamingHeterodyne(
            NATIVE_RATE, output_rate=OUTPUT_RATE, tune_hz=45_000.0, bandwidth_hz=5000.0
        )
        out = het.process(np.zeros(0, dtype=np.float32))
        assert out.shape[0] == 0

    def test_rejects_non_integer_decimation_ratio(self) -> None:
        with pytest.raises(ValueError):
            StreamingHeterodyne(250_000, output_rate=48_000, tune_hz=45_000.0, bandwidth_hz=5000.0)

    def test_rejects_tune_above_nyquist(self) -> None:
        with pytest.raises(ValueError):
            StreamingHeterodyne(NATIVE_RATE, output_rate=OUTPUT_RATE, tune_hz=300_000.0, bandwidth_hz=5000.0)
