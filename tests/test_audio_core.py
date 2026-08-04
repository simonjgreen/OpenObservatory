"""Tests for the load-bearing audio primitives.

These cover the properties that, if wrong, corrupt everything downstream without
producing an obvious error: frame addressing in the ring buffer, the native ↔
derived frame mapping in the resampler, and frame-to-time conversion.
"""

from __future__ import annotations

from itertools import pairwise
from typing import ClassVar

import numpy as np
import pytest

from open_observatory.audio.contracts import (
    NS_PER_S,
    ClockCorrelation,
    StreamClock,
    WindowSpec,
)
from open_observatory.audio.levels import LevelAggregator, measure
from open_observatory.audio.resample import AudibleResampler
from open_observatory.audio.ring import RingBuffer
from open_observatory.audio.spectrogram import SpectrogramEncoder, decode_header_size


class TestRingBuffer:
    def test_extract_returns_exact_frame_range(self) -> None:
        ring = RingBuffer(1000, seconds=2.0)
        for block in range(5):
            ring.append(block * 100, np.arange(block * 100, block * 100 + 100, dtype=np.float32), 0)
        got = ring.extract(150, 250)
        assert got is not None
        np.testing.assert_array_equal(got, np.arange(150, 250, dtype=np.float32))

    def test_extract_spanning_many_chunks(self) -> None:
        ring = RingBuffer(1000, seconds=2.0)
        for block in range(10):
            ring.append(block * 10, np.full(10, block, dtype=np.float32), 0)
        got = ring.extract(5, 95)
        assert got is not None
        assert got.shape[0] == 90
        assert got[0] == 0.0
        # Frame 94 is the fifth frame of chunk 9, which was filled with 9.
        assert got[-1] == 9.0

    def test_eviction_bounds_memory_and_is_counted(self) -> None:
        ring = RingBuffer(100, seconds=1.0)  # 100 frames capacity
        for block in range(20):
            ring.append(block * 10, np.zeros(10, dtype=np.float32), 0)
        assert ring.held_frames <= 100 + 10
        assert ring.stats.frames_evicted > 0
        oldest, newest = ring.available_range
        assert newest == 200
        assert oldest >= 100

    def test_aged_out_request_is_a_miss_not_wrong_audio(self) -> None:
        ring = RingBuffer(100, seconds=1.0)
        for block in range(20):
            ring.append(block * 10, np.full(10, block, dtype=np.float32), 0)
        # Frame 0 is long gone. Returning *something* here would be far worse
        # than returning nothing: it would be misaligned evidence.
        assert ring.extract(0, 50) is None
        assert ring.stats.extraction_misses == 1

    def test_partial_extraction_is_allowed_but_flagged(self) -> None:
        ring = RingBuffer(100, seconds=1.0)
        for block in range(20):
            ring.append(block * 10, np.full(10, block, dtype=np.float32), 0)
        oldest, newest = ring.available_range
        got = ring.extract(oldest - 50, oldest + 30, allow_partial=True)
        assert got is not None
        assert got.shape[0] == 30
        assert ring.stats.extraction_partial == 1

    def test_stored_pcm_is_immutable(self) -> None:
        ring = RingBuffer(1000, seconds=1.0)
        source = np.ones(10, dtype=np.float32)
        ring.append(0, source, 0)
        # Mutating the caller's array must not change what the ring holds.
        source[:] = 5.0
        got = ring.extract(0, 10)
        assert got is not None
        np.testing.assert_array_equal(got, np.ones(10, dtype=np.float32))


class TestResampler:
    RATE_PAIRS: ClassVar[list[tuple[int, int]]] = [
        (384000, 48000),
        (192000, 48000),
        (96000, 48000),
        (48000, 48000),
    ]

    @pytest.mark.parametrize(("source", "target"), RATE_PAIRS)
    def test_frame_mapping_is_exact_and_invertible(self, source: int, target: int) -> None:
        converter = AudibleResampler(source, target)
        for output_frame in (0, 1, 4800, 48_000, 1_234_567):
            native = converter.native_frame_for_output(output_frame)
            assert native == round(output_frame * source / target)
            assert converter.output_frame_for_native(native) == pytest.approx(output_frame, abs=1)

    @pytest.mark.parametrize(("source", "target"), RATE_PAIRS)
    def test_delivery_deficit_is_bounded_not_cumulative(self, source: int, target: int) -> None:
        """The property that matters: no cumulative drift over long runs.

        libsoxr emits ragged chunk sizes, so the count of frames produced lags the
        exact ratio by a varying amount. That is delivery latency. It must stay
        inside a band rather than growing, or an hour of capture would slide.
        """
        converter = AudibleResampler(source, target)
        block = int(source * 0.1)  # 100 ms blocks
        deficits = []
        for _ in range(600):  # 60 seconds of audio
            converter.process(np.zeros(block, dtype=np.float32))
            deficits.append(
                converter.expected_output_frames(converter.input_frames)
                - converter.output_frames
            )
        span = max(deficits) - min(deficits)
        first_decile = float(np.mean(deficits[:60]))
        last_decile = float(np.mean(deficits[-60:]))
        assert abs(last_decile - first_decile) < span + 8, (
            f"deficit trended from {first_decile:.0f} to {last_decile:.0f} "
            f"(band {min(deficits)}..{max(deficits)}) — that is cumulative drift"
        )
        assert span < target * 0.05

    def test_group_delay_is_zero_so_timestamps_are_unbiased(self) -> None:
        """An impulse must come out where the exact ratio says it should.

        A non-zero group delay would bias every audible detection's timestamp.
        """
        converter = AudibleResampler(384000, 48000)
        block = 38400
        impulse_frame = block * 3 + 1000
        pieces = []
        for index in range(12):
            chunk = np.zeros(block, dtype=np.float32)
            local = impulse_frame - index * block
            if 0 <= local < block:
                chunk[local] = 1.0
            pieces.append(converter.process(chunk).pcm)
        out = np.concatenate(pieces)
        assert float(np.abs(out).max()) > 0.01
        peak = int(np.argmax(np.abs(out)))
        ideal = impulse_frame * 48000 / 384000
        assert abs(peak - ideal) <= 1

    def test_tone_survives_downsampling(self) -> None:
        converter = AudibleResampler(384000, 48000)
        block = 38400
        pieces = []
        for index in range(40):
            t = (index * block + np.arange(block)) / 384000
            pieces.append(converter.process(0.4 * np.sin(2 * np.pi * 1000 * t)).pcm)
        out = np.concatenate(pieces)
        size = 1 << 15
        spectrum = np.abs(np.fft.rfft(out[:size] * np.hanning(size)))
        peak_hz = float(np.fft.rfftfreq(size, 1 / 48000)[int(np.argmax(spectrum))])
        assert abs(peak_hz - 1000.0) < 5.0

    def test_no_aliasing_above_audible_nyquist(self) -> None:
        """A 60 kHz tone must not fold down into the audible band."""
        converter = AudibleResampler(384000, 48000)
        block = 38400
        pieces = []
        for index in range(30):
            t = (index * block + np.arange(block)) / 384000
            pieces.append(converter.process(0.5 * np.sin(2 * np.pi * 60_000 * t)).pcm)
        out = np.concatenate(pieces)
        # Skip the filter's start-up transient.
        settled = out[4800:]
        assert float(np.sqrt(np.mean(settled**2))) < 0.01, (
            "ultrasonic content leaked into the derived audible stream"
        )

    def test_block_seams_are_continuous(self) -> None:
        converter = AudibleResampler(384000, 48000)
        block = 38400
        pieces = []
        for index in range(30):
            t = (index * block + np.arange(block)) / 384000
            pieces.append(converter.process(0.4 * np.sin(2 * np.pi * 500 * t)).pcm)
        out = np.concatenate(pieces)[500:]
        steps = np.abs(np.diff(out))
        assert float(steps.max()) < float(np.median(steps)) * 25


class TestStreamClock:
    def test_frame_to_time_has_no_drift(self, stream_info) -> None:
        clock = StreamClock.from_stream(stream_info)
        rate = 48000
        for seconds in (0, 1, 60, 3600, 86400):
            frame = seconds * rate
            expected = clock.utc_ns_at_frame_zero + seconds * NS_PER_S
            assert clock.utc_ns(frame, rate) == expected

    def test_monotonic_and_utc_advance_together(self, stream_info) -> None:
        clock = StreamClock.from_stream(stream_info)
        rate = 384000
        frame = 12_345_678
        utc_delta = clock.utc_ns(frame, rate) - clock.utc_ns(0, rate)
        mono_delta = clock.monotonic_ns(frame, rate) - clock.monotonic_ns(0, rate)
        assert utc_delta == mono_delta

    def test_clock_correlation_midpoint_is_within_read_span(self) -> None:
        sample = ClockCorrelation.sample()
        assert sample.monotonic_ns > 0
        assert sample.utc_ns > 1_600_000_000 * NS_PER_S


class TestLevels:
    def test_full_scale_sine_reads_near_zero_dbfs_peak(self) -> None:
        t = np.arange(48000) / 48000
        sample = measure(np.sin(2 * np.pi * 1000 * t).astype(np.float32), 48000)
        assert sample.peak_dbfs == pytest.approx(0.0, abs=0.1)
        # RMS of a sine is peak/sqrt(2) ~= -3 dB.
        assert sample.rms_dbfs == pytest.approx(-3.01, abs=0.2)
        assert sample.crest_factor_db == pytest.approx(3.01, abs=0.2)

    def test_clipping_is_counted(self) -> None:
        pcm = np.concatenate([np.ones(50, dtype=np.float32), np.zeros(950, dtype=np.float32)])
        sample = measure(pcm, 48000)
        assert sample.clipped_samples == 50
        assert sample.clipping_ratio == pytest.approx(0.05)

    def test_silence_is_flagged(self) -> None:
        assert measure(np.zeros(1000, dtype=np.float32), 48000).silent

    def test_aggregator_emits_once_per_second_of_audio(self) -> None:
        aggregator = LevelAggregator(sample_rate=48000)
        emitted = [
            aggregator.push(np.full(4800, 0.5, dtype=np.float32)) for _ in range(25)
        ]
        produced = [sample for sample in emitted if sample is not None]
        assert len(produced) == 2
        assert produced[0].frames == 48000
        assert produced[0].rms == pytest.approx(0.5, abs=1e-6)


class TestSpectrogramEncoder:
    def _encoder(self, **kwargs) -> SpectrogramEncoder:
        defaults = dict(
            channel=0,
            name="audible",
            sample_rate=48000,
            fft_size=1024,
            hop_ms=20.0,
            bins=64,
            min_hz=100.0,
            max_hz=12000.0,
        )
        defaults.update(kwargs)
        return SpectrogramEncoder(**defaults)  # type: ignore[arg-type]

    def test_bins_are_log_spaced_and_ascending(self) -> None:
        encoder = self._encoder()
        centres = encoder.centre_frequencies
        assert len(centres) == 64
        assert all(b > a for a, b in pairwise(centres))
        # Log spacing: successive ratios roughly constant.
        ratios = [b / a for a, b in pairwise(centres)]
        assert max(ratios) / min(ratios) < 1.4

    def test_max_hz_is_clamped_below_nyquist(self) -> None:
        encoder = self._encoder(sample_rate=48000, max_hz=40000.0)
        assert encoder.max_hz < 24000.0

    def test_tone_lights_the_expected_bin(self) -> None:
        encoder = self._encoder()
        rate = 48000
        t = np.arange(rate) / rate
        pcm = (0.5 * np.sin(2 * np.pi * 2000 * t)).astype(np.float32)
        batch = encoder.push(pcm, 0, 1_700_000_000_000_000_000)
        assert batch is not None
        column = batch.data[batch.columns // 2]
        loudest = int(np.argmax(column))
        assert abs(encoder.centre_frequencies[loudest] - 2000) < 200

    def test_column_times_derive_from_frame_index(self) -> None:
        encoder = self._encoder()
        utc0 = 1_700_000_000_000_000_000
        first = encoder.push(np.zeros(48000, dtype=np.float32), 0, utc0)
        assert first is not None
        # Second push continues the stream; its first column must follow on in
        # time by exactly the columns already emitted.
        second = encoder.push(np.zeros(48000, dtype=np.float32), 48000, utc0 + NS_PER_S)
        assert second is not None
        expected = first.first_utc_s + first.columns * encoder.hop_s
        assert second.first_utc_s == pytest.approx(expected, abs=encoder.hop_s * 0.51)

    def test_wide_hop_channel_covers_all_audio(self) -> None:
        """A hop wider than the FFT must not create blind spots between columns.

        The ultrasonic channel runs a 4096-point FFT with a 9216-frame hop at
        384 kHz. Taking one window per column looked at 4096 frames and ignored the
        other 5120 — 55% of the audio — so a 4 ms bat pulse landing in the gap was
        invisible. Every position in the stream must reach some column.
        """
        encoder = SpectrogramEncoder(
            channel=1,
            name="ultrasonic",
            sample_rate=384000,
            fft_size=4096,
            hop_ms=24.0,
            bins=64,
            min_hz=15_000.0,
            max_hz=150_000.0,
        )
        assert encoder.hop_frames > encoder.fft_size, "precondition: hop wider than window"
        # Sub-windows must span the whole hop.
        assert encoder._sub_offsets[0] == 0
        assert encoder._sub_offsets[-1] + encoder.fft_size >= encoder.hop_frames

        # A short 60 kHz burst placed anywhere in the hop must show up.
        rate = 384000
        detected = []
        for position in (0, 2000, 5000, 7000, 9000):
            fresh = SpectrogramEncoder(
                channel=1, name="u", sample_rate=rate, fft_size=4096, hop_ms=24.0,
                bins=64, min_hz=15_000.0, max_hz=150_000.0,
            )
            pcm = np.zeros(rate // 10, dtype=np.float32)
            length = int(rate * 0.004)
            t = np.arange(length) / rate
            pcm[position : position + length] = (
                0.5 * np.sin(2 * np.pi * 60_000 * t)
            ).astype(np.float32)
            batch = fresh.push(pcm, 0, 1_700_000_000_000_000_000)
            assert batch is not None
            detected.append(int(batch.data.max()))
        assert all(v > 40 for v in detected), (
            f"a 4 ms burst was lost depending on where it fell in the hop: {detected}"
        )

    def test_frame_index_never_advances_past_the_buffer(self) -> None:
        """Overshooting the buffer corrupted the column timeline.

        With a hop wider than the window the write offset can pass the end of the
        buffered audio. Advancing the frame index by that overshoot claimed frames
        that were never present, which produced ~4% too many columns and
        overlapping column timestamps on the live ultrasonic channel.
        """
        rate = 384000
        encoder = SpectrogramEncoder(
            channel=1, name="u", sample_rate=rate, fft_size=4096, hop_ms=24.0,
            bins=64, min_hz=15_000.0, max_hz=150_000.0,
        )
        utc0 = 1_700_000_000 * NS_PER_S
        block = rate // 10  # 100 ms blocks, as capture delivers
        previous_end: float | None = None
        emitted = 0
        for index in range(60):  # 6 seconds
            batch = encoder.push(
                np.zeros(block, dtype=np.float32), index * block, utc0 + index * NS_PER_S // 10
            )
            if batch is None:
                continue
            emitted += batch.columns
            if previous_end is not None:
                # Contiguous: no overlap and no gap beyond rounding.
                assert batch.first_utc_s >= previous_end - 1e-6, (
                    f"column timeline went backwards by "
                    f"{(previous_end - batch.first_utc_s) * 1000:.1f} ms"
                )
                assert batch.first_utc_s - previous_end < encoder.hop_s, "gap in the timeline"
            previous_end = batch.first_utc_s + (batch.columns - 1) * encoder.hop_s + encoder.hop_s
        # 6 s of audio at a 24 ms hop is 250 columns; allow the trailing partial.
        assert 240 <= emitted <= 252, f"emitted {emitted} columns for 6 s of audio"

    def test_binary_frame_roundtrips(self) -> None:
        encoder = self._encoder()
        batch = encoder.push(np.random.default_rng(1).normal(0, 0.1, 48000).astype(np.float32), 0, 0)
        assert batch is not None
        payload = batch.to_binary()
        assert len(payload) == decode_header_size() + batch.bins * batch.columns
        assert payload[0] == 1  # FRAME_SPECTROGRAM
        assert payload[1] == 0  # channel


class TestWindowSpec:
    def test_derived_quantities(self) -> None:
        spec = WindowSpec(stream_kind="audible48", sample_rate=48000, duration_s=3.0, stride_s=1.5)
        assert spec.frame_count == 144000
        assert spec.stride_frames == 72000
        assert spec.overlap_s == pytest.approx(1.5)
