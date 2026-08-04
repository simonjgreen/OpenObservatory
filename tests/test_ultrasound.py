"""Tests for rendering ultrasound into the audible band.

These verify the DSP actually moved the energy where it claims to, by measuring the
output spectrum — not merely that a file was produced. A silently-broken renderer
would still write a plausible-looking WAV.
"""

from __future__ import annotations

import numpy as np
import pytest

from open_observatory.audio import ultrasound

NATIVE_RATE = 384000


def bat_pulse_train(
    rate: int = NATIVE_RATE,
    duration_s: float = 1.0,
    f0: float = 55_000.0,
    f1: float = 42_000.0,
    pulses: int = 8,
    interval_s: float = 0.09,
    pulse_ms: float = 4.0,
    rumble: bool = False,
) -> np.ndarray:
    """A synthetic bat pass: downward-sweeping ultrasonic pulses."""
    signal = np.random.default_rng(1).normal(0, 0.0005, int(rate * duration_s)).astype(np.float32)
    if rumble:
        # Wind/traffic energy at 90 Hz, 40 dB above the call. This is the thing the
        # high-pass exists to remove.
        t = np.arange(signal.shape[0]) / rate
        signal += (0.5 * np.sin(2 * np.pi * 90 * t)).astype(np.float32)
    length = int(rate * pulse_ms / 1000)
    for index in range(pulses):
        begin = int(0.05 * rate) + index * int(interval_s * rate)
        if begin + length > signal.shape[0]:
            break
        t = np.arange(length) / rate
        freq = f0 + (f1 - f0) * (t / (pulse_ms / 1000))
        envelope = np.sin(np.pi * t / (pulse_ms / 1000)) ** 2
        signal[begin : begin + length] += (0.3 * envelope * np.sin(2 * np.pi * freq * t)).astype(
            np.float32
        )
    return signal


def dominant_hz(pcm: np.ndarray, rate: int) -> float:
    """Frequency holding the most energy, ignoring DC."""
    size = 1 << int(np.floor(np.log2(max(pcm.shape[0], 2))))
    spectrum = np.abs(np.fft.rfft(pcm[:size] * np.hanning(size)))
    freqs = np.fft.rfftfreq(size, 1.0 / rate)
    spectrum[freqs < 20] = 0.0
    return float(freqs[int(np.argmax(spectrum))])


def band_energy(pcm: np.ndarray, rate: int, low: float, high: float) -> float:
    size = 1 << int(np.floor(np.log2(max(pcm.shape[0], 2))))
    spectrum = np.abs(np.fft.rfft(pcm[:size] * np.hanning(size))) ** 2
    freqs = np.fft.rfftfreq(size, 1.0 / rate)
    mask = (freqs >= low) & (freqs < high)
    return float(spectrum[mask].sum())


class TestExpansionFactor:
    def test_adaptive_factor_targets_the_audible_band(self) -> None:
        # A noctule at 25 kHz and a lesser horseshoe at 110 kHz must both end up
        # near the target, which a single fixed factor could not achieve.
        for peak in (25_000, 45_000, 55_000, 110_000):
            factor = ultrasound.choose_expansion_factor(peak, 4000.0)
            assert 2000 < peak / factor < 8000, f"{peak} Hz landed badly with x{factor}"

    def test_fixed_factor_is_honoured(self) -> None:
        assert ultrasound.choose_expansion_factor(45_000, 4000.0, fixed=10) == 10.0

    def test_already_audible_needs_no_expansion(self) -> None:
        assert ultrasound.choose_expansion_factor(3000, 4000.0) == 1.0
        assert ultrasound.choose_expansion_factor(None, 4000.0) == 1.0


class TestHighpass:
    def test_low_frequency_rumble_is_removed(self) -> None:
        rate = 48000
        t = np.arange(rate) / rate
        signal = (np.sin(2 * np.pi * 90 * t) + 0.1 * np.sin(2 * np.pi * 20_000 * t)).astype(
            np.float32
        )
        filtered = ultrasound.highpass(signal, rate, 12_000.0)
        assert band_energy(filtered, rate, 0, 1000) < band_energy(signal, rate, 0, 1000) * 1e-6
        # The wanted content survives.
        assert band_energy(filtered, rate, 19_000, 21_000) > 0

    def test_no_edge_click_is_introduced(self) -> None:
        rate = 48000
        signal = np.ones(rate, dtype=np.float32)
        filtered = ultrasound.highpass(signal, rate, 1000.0)
        # Brick-wall filtering plus the fade must leave the edges quiet, not ringing.
        assert abs(float(filtered[0])) < 0.05
        assert abs(float(filtered[-1])) < 0.05


class TestTimeExpansion:
    def test_call_lands_in_the_audible_band(self) -> None:
        signal = bat_pulse_train()
        rendered = ultrasound.time_expand(signal, NATIVE_RATE, peak_hz=48_000.0)
        assert rendered is not None
        assert rendered.method == "time-expansion"
        peak = dominant_hz(rendered.pcm, rendered.sample_rate)
        assert 1500 < peak < 9000, f"expanded call sits at {peak:.0f} Hz, not audible"

    def test_frequencies_divide_by_exactly_the_factor(self) -> None:
        """The defining property: a pure tone must divide by the stated factor."""
        rate = NATIVE_RATE
        t = np.arange(rate) / rate
        tone = (0.5 * np.sin(2 * np.pi * 40_000 * t)).astype(np.float32)
        rendered = ultrasound.time_expand(tone, rate, peak_hz=40_000.0, fixed_factor=10)
        assert rendered is not None
        assert rendered.detail["factor"] == 10.0
        assert rendered.sample_rate == rate // 10
        assert dominant_hz(rendered.pcm, rendered.sample_rate) == pytest.approx(4000, abs=60)

    def test_samples_are_untouched_so_nothing_is_lost(self) -> None:
        """Expansion is a header change, not a resample: no filter artefacts."""
        rate = NATIVE_RATE
        signal = bat_pulse_train(duration_s=0.5)
        rendered = ultrasound.time_expand(
            signal, rate, peak_hz=48_000.0, fixed_factor=8, highpass_hz=0.0
        )
        assert rendered is not None
        # Same frame count (only scaled in amplitude by normalisation).
        assert rendered.pcm.shape[0] == signal.shape[0]

    def test_duration_is_multiplied_and_reported(self) -> None:
        signal = bat_pulse_train(duration_s=1.0)
        rendered = ultrasound.time_expand(signal, NATIVE_RATE, peak_hz=48_000.0, fixed_factor=10)
        assert rendered is not None
        assert rendered.duration_s == pytest.approx(10.0, rel=0.02)
        assert rendered.detail["plays_slower_by"] == 10.0

    def test_rumble_does_not_dominate_the_result(self) -> None:
        """Without the high-pass, 90 Hz wind becomes 9 Hz and swamps the call."""
        signal = bat_pulse_train(rumble=True)
        rendered = ultrasound.time_expand(signal, NATIVE_RATE, peak_hz=48_000.0, fixed_factor=10)
        assert rendered is not None
        peak = dominant_hz(rendered.pcm, rendered.sample_rate)
        assert 1500 < peak < 9000, f"rumble won: dominant output is {peak:.0f} Hz"

    def test_output_rate_stays_browser_playable(self) -> None:
        # A very high factor must not produce a rate a browser will reject.
        rendered = ultrasound.time_expand(
            bat_pulse_train(), NATIVE_RATE, peak_hz=48_000.0, fixed_factor=64
        )
        assert rendered is not None
        assert rendered.sample_rate >= 8000

    def test_duration_is_capped(self) -> None:
        signal = bat_pulse_train(duration_s=4.0)
        rendered = ultrasound.time_expand(
            signal, NATIVE_RATE, peak_hz=48_000.0, fixed_factor=10, max_seconds=5.0
        )
        assert rendered is not None
        assert rendered.duration_s <= 5.5

    def test_normalisation_is_recorded_not_hidden(self) -> None:
        rendered = ultrasound.time_expand(
            bat_pulse_train(), NATIVE_RATE, peak_hz=48_000.0, fixed_factor=10
        )
        assert rendered is not None
        assert "normalisation_gain_db" in rendered.detail
        assert rendered.detail["amplitudes_comparable_to_native"] is False
        assert float(np.abs(rendered.pcm).max()) == pytest.approx(0.708, abs=0.05)


class TestHeterodyne:
    def test_tuned_call_appears_near_baseband(self) -> None:
        signal = bat_pulse_train(f0=45_000, f1=45_000)
        rendered = ultrasound.heterodyne(signal, NATIVE_RATE, tune_hz=45_000.0)
        assert rendered is not None
        assert rendered.sample_rate == 48000
        peak = dominant_hz(rendered.pcm, rendered.sample_rate)
        # Tuned exactly on the call, so the difference frequency is near zero;
        # allow the sweep and filter width.
        assert peak < 6000, f"heterodyne output peaked at {peak:.0f} Hz"

    def test_detuning_shifts_the_pitch_predictably(self) -> None:
        signal = bat_pulse_train(f0=45_000, f1=45_000)
        rendered = ultrasound.heterodyne(signal, NATIVE_RATE, tune_hz=42_000.0)
        assert rendered is not None
        peak = dominant_hz(rendered.pcm, rendered.sample_rate)
        assert peak == pytest.approx(3000, abs=700), f"expected ~3 kHz, got {peak:.0f} Hz"

    def test_real_time_duration_is_preserved(self) -> None:
        signal = bat_pulse_train(duration_s=1.0)
        rendered = ultrasound.heterodyne(signal, NATIVE_RATE, tune_hz=48_000.0)
        assert rendered is not None
        assert rendered.duration_s == pytest.approx(1.0, rel=0.05)
        assert rendered.detail["real_time"] is True

    def test_out_of_band_content_is_discarded(self) -> None:
        rate = NATIVE_RATE
        t = np.arange(rate) / rate
        # Two tones: one in the tuned band, one far outside it.
        signal = (
            0.3 * np.sin(2 * np.pi * 45_000 * t) + 0.3 * np.sin(2 * np.pi * 100_000 * t)
        ).astype(np.float32)
        rendered = ultrasound.heterodyne(signal, rate, tune_hz=45_000.0, bandwidth_hz=5000.0)
        assert rendered is not None
        # 100 kHz would land at 55 kHz difference, far outside the 48 kHz output; the
        # band-pass must have removed it before mixing rather than letting it alias in.
        assert dominant_hz(rendered.pcm, rendered.sample_rate) < 6000

    def test_refuses_impossible_tuning(self) -> None:
        signal = bat_pulse_train()
        assert ultrasound.heterodyne(signal, NATIVE_RATE, tune_hz=None) is None
        # Above Nyquist there is nothing to tune to.
        assert ultrasound.heterodyne(signal, NATIVE_RATE, tune_hz=250_000.0) is None


class TestRenderDispatch:
    def test_both_produces_one_of_each(self) -> None:
        renders = ultrasound.render(
            bat_pulse_train(), NATIVE_RATE, method="both", peak_hz=48_000.0
        )
        assert {r.method for r in renders} == {"time-expansion", "heterodyne"}

    def test_none_produces_nothing(self) -> None:
        assert (
            ultrasound.render(bat_pulse_train(), NATIVE_RATE, method="none", peak_hz=48_000.0)
            == []
        )

    def test_single_method_selection(self) -> None:
        renders = ultrasound.render(
            bat_pulse_train(), NATIVE_RATE, method="heterodyne", peak_hz=48_000.0
        )
        assert [r.method for r in renders] == ["heterodyne"]

    def test_audible_source_gets_no_expansion(self) -> None:
        """A bird at 4 kHz needs no rendering; expansion would be nonsense."""
        renders = ultrasound.render(
            bat_pulse_train(), NATIVE_RATE, method="time-expansion", peak_hz=4000.0
        )
        assert renders == []


class TestClipIntegration:
    """The manager must write the derivative and label it in the asset detail."""

    def test_ultrasonic_detection_gets_audible_derivatives(self, tmp_path) -> None:
        import uuid
        from datetime import UTC, datetime

        from open_observatory.audio.ring import RingBuffer
        from open_observatory.clips import ClipManager

        ring = RingBuffer(NATIVE_RATE, seconds=6.0)
        signal = bat_pulse_train(duration_s=3.0)
        ring.append(0, signal, 0)

        manager = ClipManager(
            clip_dir=tmp_path / "clips",
            pre_roll_s=0.2,
            post_roll_s=0.2,
            max_duration_s=3.0,
            min_score=0.0,
            clip_plugins=("ultrasonic-pass-v1",),
            ultrasonic_audible_max_s=20.0,
        )
        assets = manager.extract(
            ring=ring,
            detection_id=uuid.uuid4(),
            stream_id=uuid.uuid4(),
            event_start_frame=int(0.05 * NATIVE_RATE),
            event_end_frame=int(0.8 * NATIVE_RATE),
            score=0.9,
            label="bat pass",
            event_start_utc=datetime.now(UTC),
            plugin_id="ultrasonic-pass-v1",
            peak_frequency_hz=48_000.0,
        )

        kinds = [asset.kind for asset in assets]
        assert "evidence_native" in kinds, "the authoritative recording must still be written"
        audible = [a for a in assets if a.kind == "audible_ultrasonic"]
        assert len(audible) == 2, f"expected both renderings, got {kinds}"

        for asset in audible:
            assert asset.path.exists()
            assert asset.sample_rate <= 96000, "must be a rate a browser can decode"
            assert asset.detail["authoritative"] is False
            assert asset.detail["description"]
            assert asset.detail["amplitudes_comparable_to_native"] is False

        # And the file really does contain audible-band energy.
        import soundfile as sf

        expanded = next(a for a in audible if a.detail["method"] == "time-expansion")
        pcm, rate = sf.read(str(expanded.path), dtype="float32")
        assert 1500 < dominant_hz(pcm, rate) < 9000

    def test_audible_detection_gets_no_ultrasonic_derivative(self, tmp_path) -> None:
        import uuid
        from datetime import UTC, datetime

        from open_observatory.audio.ring import RingBuffer
        from open_observatory.clips import ClipManager

        ring = RingBuffer(48000, seconds=6.0)
        t = np.arange(48000 * 3) / 48000
        ring.append(0, (0.3 * np.sin(2 * np.pi * 4000 * t)).astype(np.float32), 0)

        manager = ClipManager(
            clip_dir=tmp_path / "clips", min_score=0.0, clip_plugins=("birdnet-v2.4",)
        )
        assets = manager.extract(
            ring=ring,
            detection_id=uuid.uuid4(),
            stream_id=uuid.uuid4(),
            event_start_frame=0,
            event_end_frame=48000,
            score=0.9,
            label="robin",
            event_start_utc=datetime.now(UTC),
            plugin_id="birdnet-v2.4",
            peak_frequency_hz=4000.0,
        )
        assert [a.kind for a in assets] == ["playback"]

    def test_barely_audible_18khz_event_is_still_rendered(self, tmp_path) -> None:
        """18 kHz survives the 48 kHz derivative but most adults cannot hear it."""
        import uuid
        from datetime import UTC, datetime

        from open_observatory.audio.ring import RingBuffer
        from open_observatory.clips import ClipManager

        rate = NATIVE_RATE
        ring = RingBuffer(rate, seconds=4.0)
        t = np.arange(rate * 2) / rate
        ring.append(0, (0.3 * np.sin(2 * np.pi * 18_000 * t)).astype(np.float32), 0)

        manager = ClipManager(
            clip_dir=tmp_path / "clips",
            min_score=0.0,
            clip_plugins=("ultrasonic-pass-v1",),
            ultrasonic_highpass_hz=8000.0,
        )
        assets = manager.extract(
            ring=ring,
            detection_id=uuid.uuid4(),
            stream_id=uuid.uuid4(),
            event_start_frame=0,
            event_end_frame=rate,
            score=0.9,
            label="bat pass",
            event_start_utc=datetime.now(UTC),
            plugin_id="ultrasonic-pass-v1",
            peak_frequency_hz=18_000.0,
        )
        assert any(a.kind == "audible_ultrasonic" for a in assets)
