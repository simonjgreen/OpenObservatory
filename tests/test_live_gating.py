"""The live view's cost is charged to capture, so it is only paid when watched.

ADR-040. The steady state of this station is *no browser connected* -- the wall
display is the first-class surface -- so every FFT computed for an absent viewer
is waste charged against the event loop whose stalls demonstrably cause capture
gaps (ADR-033). The heterodyne already gates on `listener_count`; these tests
hold the same line for the two spectrogram encoders, which measured 0.0554 of a
core on the target against a whole-hot-path 0.1067.

The awkward half of the property is the one these tests spend most of their
length on: history. `LiveHub` snapshots history on connect precisely so a viewer
does not "stare at an empty canvas for a minute, which looks exactly like a
broken pipeline". If encoding stops while idle, the retained history is stale --
minutes or hours old -- and sending it as though it were the last thirty seconds
would be worse than sending nothing. So resuming must *discard* it, and the
station must say plainly that it is filling.
"""

from __future__ import annotations

import uuid

import numpy as np
import pytest

from open_observatory.audio.contracts import CaptureBlock, ClockCorrelation
from open_observatory.audio.resample import AudibleResampler
from open_observatory.audio.ring import RingBuffer
from open_observatory.config import Settings
from open_observatory.segmenter import WindowRouter
from open_observatory.station import SPECTROGRAM_AUDIBLE, SPECTROGRAM_ULTRASONIC, Station

NATIVE_RATE = 384_000
BLOCK_S = 0.1


def build_station(**overrides: object) -> Station:
    """A Station wired far enough to run `_handle_block`, and no further.

    Deliberately assembled by hand rather than by `_on_stream_open`, which wants
    a database, a device row and a detector build -- none of which this property
    touches.
    """
    settings = Settings(**overrides)  # type: ignore[arg-type]
    station = Station(settings)
    station.native_ring = RingBuffer(NATIVE_RATE, 2.0)
    station.audible_ring = RingBuffer(settings.audible_sample_rate, 2.0)
    station.resampler = AudibleResampler(NATIVE_RATE, settings.audible_sample_rate)
    station.router = WindowRouter(native_rate=NATIVE_RATE, stream_id=uuid.uuid4())
    station._build_spectrograms(NATIVE_RATE)
    return station


def feed(station: Station, seconds: float, *, first_frame: int = 0) -> int:
    """Push `seconds` of audio through the hot path; returns the next frame index."""
    frames_per_block = int(NATIVE_RATE * BLOCK_S)
    rng = np.random.default_rng(11)
    frame = first_frame
    for index in range(int(seconds / BLOCK_S)):
        pcm = (rng.standard_normal(frames_per_block) * 0.05).astype(np.float32)
        station._handle_block(
            CaptureBlock(
                stream_id=uuid.uuid4(),
                sequence=index,
                first_frame=frame,
                sample_rate=NATIVE_RATE,
                pcm=pcm,
                monotonic_start_ns=int(frame * 1e9 / NATIVE_RATE),
                clock=ClockCorrelation.sample(),
            )
        )
        frame += frames_per_block
    return frame


def columns(station: Station) -> dict[int, int]:
    return {key: encoder.columns_emitted for key, encoder in station.spectrograms.items()}


class TestEncodingIsGatedOnThereBeingAViewer:
    def test_no_viewer_means_no_spectrogram_work(self) -> None:
        station = build_station()
        feed(station, 1.0)

        assert columns(station) == {SPECTROGRAM_AUDIBLE: 0, SPECTROGRAM_ULTRASONIC: 0}
        # And nothing was retained to send, which is the honest consequence.
        assert all(len(e.history) == 0 for e in station.spectrograms.values())

    def test_a_viewer_resumes_both_channels(self) -> None:
        station = build_station()
        feed(station, 1.0)
        station.set_spectrogram_consumer_count(lambda: 1)
        feed(station, 1.0, first_frame=int(NATIVE_RATE * 1.0))

        emitted = columns(station)
        assert emitted[SPECTROGRAM_AUDIBLE] > 0
        assert emitted[SPECTROGRAM_ULTRASONIC] > 0

    def test_losing_the_last_viewer_stops_the_work_again(self) -> None:
        station = build_station()
        viewers = 1
        station.set_spectrogram_consumer_count(lambda: viewers)
        feed(station, 1.0)
        watched = columns(station)

        viewers = 0
        feed(station, 1.0, first_frame=int(NATIVE_RATE * 1.0))

        assert columns(station) == watched

    def test_the_gate_is_configurable_and_zero_means_always_encode(self) -> None:
        """A station that wants the old always-on behaviour can have it."""
        station = build_station(spectrogram_encode_min_viewers=0)
        feed(station, 1.0)

        assert all(count > 0 for count in columns(station).values())

    def test_the_audible_channel_can_be_kept_warm_on_its_own(self) -> None:
        """The asymmetry knob: pay the cheaper encoder to keep instant history.

        Measured on the target the two encoders cost almost the same (0.0261 vs
        0.0293 of a core), so this is *not* the default -- it buys back half the
        history for 47% of the cost. It exists because the trade is a judgement
        about the operator's taste, not a fact about the machine.
        """
        station = build_station(spectrogram_keep_audible_warm=True)
        feed(station, 1.0)

        emitted = columns(station)
        assert emitted[SPECTROGRAM_AUDIBLE] > 0
        assert emitted[SPECTROGRAM_ULTRASONIC] == 0


class TestAConnectingClientIsNeverShownAStaleCanvas:
    def test_resuming_discards_history_from_before_the_idle_period(self) -> None:
        """Stale columns must not be re-served as though they were recent.

        Without this, a viewer connecting after an hour idle would be sent an
        hour-old picture timestamped as the last thirty seconds -- which is not
        merely unhelpful, it is the pipeline lying about what it heard and when.
        """
        station = build_station()
        station.set_spectrogram_consumer_count(lambda: 1)
        frame = feed(station, 2.0)
        audible = station.spectrograms[SPECTROGRAM_AUDIBLE]
        assert len(audible.history) > 0
        stale = list(audible.history)[-1].copy()
        stale_first_utc = audible.history_first_utc_s

        # Nobody watching for a while, then someone connects.
        station.set_spectrogram_consumer_count(lambda: 0)
        frame = feed(station, 2.0, first_frame=frame)
        station.set_spectrogram_consumer_count(lambda: 1)

        # The instant the gate reopens, the old history is gone rather than
        # waiting to be aged out one column at a time.
        history = station.spectrograms[SPECTROGRAM_AUDIBLE].history_frame()
        assert history is None

        feed(station, 1.0, first_frame=frame)
        history = station.spectrograms[SPECTROGRAM_AUDIBLE].history_frame()
        assert history is not None
        assert history.columns < 2400
        assert not np.array_equal(history.data[0], stale)
        assert audible.history_first_utc_s != stale_first_utc

    def test_column_times_after_a_resume_are_the_new_audio_not_the_old(self) -> None:
        """The encoder's part-filled buffer is stale state too, not just history."""
        station = build_station()
        station.set_spectrogram_consumer_count(lambda: 1)
        frame = feed(station, 1.0)
        station.set_spectrogram_consumer_count(lambda: 0)
        frame = feed(station, 5.0, first_frame=frame)
        station.set_spectrogram_consumer_count(lambda: 1)
        feed(station, 1.0, first_frame=frame)

        history = station.spectrograms[SPECTROGRAM_AUDIBLE].history_frame()
        assert history is not None
        assert station.clock is not None
        # Six seconds of audio elapsed before the gate reopened, so the retained
        # columns must be dated from there, not from the 1 s mark where encoding
        # stopped -- which is what a buffer carried across the idle period would
        # have produced.
        stream_began_s = station.clock.utc_ns_at_frame_zero / 1e9
        assert history.first_utc_s - stream_began_s == pytest.approx(6.0, abs=0.5)

    def test_the_station_says_which_channels_are_filling(self) -> None:
        """A blank canvas that is deliberately blank must be legible as such.

        `LiveHub`'s docstring is right that an empty canvas looks like a broken
        pipeline. Gating makes empty canvases normal, so the server states the
        reason rather than leaving the UI to guess from a column count.
        """
        station = build_station()
        described = {spec["name"]: spec for spec in station.describe_spectrograms()}
        assert described["audible"]["viewer_gated"] is True
        assert described["audible"]["history_seconds"] == 0.0

        station.set_spectrogram_consumer_count(lambda: 1)
        feed(station, 1.0)
        described = {spec["name"]: spec for spec in station.describe_spectrograms()}
        assert described["audible"]["history_seconds"] > 0.0

    def test_an_ungated_station_does_not_claim_to_be_gated(self) -> None:
        station = build_station(spectrogram_encode_min_viewers=0)
        described = station.describe_spectrograms()
        assert all(spec["viewer_gated"] is False for spec in described)


class TestUltrasonicSpectrogramHasItsOwnRange:
    """The ultrasonic channel's noise floor sits far higher than the audible
    channel's, because the AudioMoth's gain is documented as too hot
    (HANDOVER.md sec6.3 item 4) and the two channels were sharing one
    floor/ceiling pair meant for 48 kHz audio. Measured on the live station,
    2026-08-09 (30 s, viewer connected, `scripts/measure_ultrasonic_contrast.py`):

    15-45 kHz (bat band): p1 -72.1 dB, p50 -66.7 dB, p95 -60.8 dB, p99 -58.3 dB
    >=50 kHz (quiet band): p1 -82.1 dB, p50 -76.1 dB, p95 -69.9 dB, p99 -59.2 dB

    That noise sat at roughly 48-58% up the shared -105..-25 dB ramp -- squarely
    in the ramp's orange band -- which is the saturation the operator reported.
    """

    def test_ultrasonic_channel_gets_its_own_default_range(self) -> None:
        station = build_station()
        ultrasonic = station.spectrograms[SPECTROGRAM_ULTRASONIC]
        assert ultrasonic.floor_db == pytest.approx(-85.0)
        assert ultrasonic.ceiling_db == pytest.approx(-30.0)

    def test_audible_channel_keeps_its_own_unrelated_default_range(self) -> None:
        station = build_station()
        audible = station.spectrograms[SPECTROGRAM_AUDIBLE]
        assert audible.floor_db == pytest.approx(-95.0)
        assert audible.ceiling_db == pytest.approx(-15.0)

    def test_ultrasonic_range_is_independently_configurable(self) -> None:
        station = build_station(
            ultrasonic_spectrogram_floor_db=-90.0,
            ultrasonic_spectrogram_ceiling_db=-40.0,
        )
        ultrasonic = station.spectrograms[SPECTROGRAM_ULTRASONIC]
        assert ultrasonic.floor_db == pytest.approx(-90.0)
        assert ultrasonic.ceiling_db == pytest.approx(-40.0)
        # And the audible channel is unaffected by that override.
        audible = station.spectrograms[SPECTROGRAM_AUDIBLE]
        assert audible.floor_db == pytest.approx(-95.0)
        assert audible.ceiling_db == pytest.approx(-15.0)

    def test_the_new_range_is_published_for_the_ui_badges(self) -> None:
        station = build_station()
        described = {spec["name"]: spec for spec in station.describe_spectrograms()}
        assert described["ultrasonic"]["floor_db"] == pytest.approx(-85.0)
        assert described["ultrasonic"]["ceiling_db"] == pytest.approx(-30.0)
