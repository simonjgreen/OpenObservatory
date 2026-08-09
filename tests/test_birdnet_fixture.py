"""BirdNET fixture test — the Milestone 3 exit gate.

`docs/delivery/MILESTONE_STATUS.md` records the gate as only "partially met":
BirdNET produced real identifications on live audio (including *Columba
palumbus*), but that was a live demonstration, not a repeatable test, because
no licensed reference recording was committed to the repository. This module
is that repeatable test.

Unlike the BirdNET *model* assets (ADR-006 — CC BY-NC-SA, fetched on demand via
`oo models fetch`, never committed), the reference recording here is a short
third-party audio clip whose own licence explicitly permits redistribution
(CC BY-SA 4.0), so it is committed directly. See
`tests/fixtures/audio/ATTRIBUTION.md` for full provenance — source, recordist,
date, location, licence text, and a checksum this test verifies before use.
Xeno-canto licences vary per recording, so that file, not the site in general,
is what was checked.

This test still must **skip, not fail** when the (unbundled) BirdNET model
assets or a TFLite runtime are absent — exactly the discipline
`tests/test_batdetect2.py` already follows, and the reason the project reports
"3 skipped" on a machine with no models installed. It also still exercises the
plausibility filtering landed in ADR-032: the station coordinates and analysis
date below are chosen deliberately, not inherited from ambient settings, and
one test asserts the range model's own occurrence figure for the chosen
species/week/location combination so a future change to the model or the
floor that would silently break this fixture is caught here rather than by a
confusing failure downstream.

## Why this species, date and location

European Robin is a common, visually and acoustically well known UK
resident, present and vocal essentially year-round -- unlike a scarce winter
visitor, there is no plausible week where it *should* fail the range model.
The reference coordinates are the Royal Observatory, Greenwich (51.4769,
-0.0005): a neutral, published reference location that belongs to no
particular deployment of this software, and comfortably inside the Robin's
year-round range. The analysis date is fixed at 2026-08-08T06:00
Europe/London (BirdNET week 30); measured with the real, shipped range model
at these coordinates on 2026-08-09, that week gives European Robin an
occurrence probability of 0.8099, comfortably inside "in_range" (threshold
0.55) and nowhere near the ADR-032 plausibility floor (0.0005). This is
recorded explicitly, and asserted in
`test_the_fixed_date_and_location_keep_the_species_plausible`, so a change to
the range model or the floor that silently makes this fixture inapplicable in
future is caught by name rather than by a mysteriously empty candidate list.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from open_observatory.audio.contracts import NS_PER_S, AudioWindow
from open_observatory.audio.ring import RingBuffer
from open_observatory.clips import ClipManager
from open_observatory.detectors.base import DetectorContext, DetectorUnavailable
from open_observatory.detectors.birdnet import BirdNetDetector
from open_observatory.models import DEFAULT_MODEL_DIR, sha256_file

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "audio"
FIXTURE_AUDIO = FIXTURE_DIR / "erithacus_rubecula_XC441752.mp3"
FIXTURE_SHA256 = "21d116a92365cf6f753ef90f166eaeeebedf46300dd212a050cfdc28ce2d68ca"

#: Royal Observatory, Greenwich -- see the module docstring.
REFERENCE_LATITUDE = 51.4769
REFERENCE_LONGITUDE = -0.0005
STATION_TIMEZONE = "Europe/London"
#: Deliberately fixed, not "now" — see the module docstring. Any date would do
#: for a year-round resident, but a fixed date keeps the test's own week
#: calculation reproducible and inspectable rather than silently depending on
#: whatever week the test happens to run in.
ANALYSIS_LOCAL_TIME = datetime(2026, 8, 8, 6, 0, tzinfo=ZoneInfo(STATION_TIMEZONE))
EXPECTED_COMMON_NAME = "European Robin"
EXPECTED_SCIENTIFIC_NAME = "Erithacus rubecula"

AUDIBLE_RATE = 48000
#: Silence either side of the call, long enough that "the detection's window
#: overlaps the call" and "the detection's window is just padding" are not
#: close calls.
PAD_S = 3.0
#: Windows are stepped densely enough across the whole padded recording that
#: the (short, 6-7 s) call cannot fall entirely between two sampled windows --
#: production uses `birdnet_window_stride_s=1.5`, which is too coarse for a
#: clip this short and would make the test flaky depending on exactly where
#: the call lands.
TEST_STRIDE_S = 0.25


async def _skip_if_unavailable(detector: BirdNetDetector, context: DetectorContext) -> None:
    try:
        await detector.initialise(context)
    except DetectorUnavailable as exc:
        pytest.skip(
            f"BirdNET model assets or TFLite runtime not available: {exc}. "
            "Run 'oo models fetch' and install the 'birdnet' extra "
            "(ai-edge-litert) to exercise this fixture test."
        )


@pytest.fixture()
def detector() -> BirdNetDetector:
    return BirdNetDetector(model_dir=DEFAULT_MODEL_DIR)


@pytest.fixture()
def context() -> DetectorContext:
    return DetectorContext(
        station_name="fixture-test",
        timezone=STATION_TIMEZONE,
        latitude=REFERENCE_LATITUDE,
        longitude=REFERENCE_LONGITUDE,
    )


def _load_padded_audible_pcm() -> tuple[np.ndarray, int, int]:
    """Resample the fixture to the audible stream rate and pad it with silence.

    Returns ``(pcm, call_start_frame, call_end_frame)`` where the bounds mark
    exactly where the fixture's own audio sits inside the padded buffer, in
    frames at :data:`AUDIBLE_RATE` -- the ground truth the alignment
    assertions are checked against.
    """
    import soundfile as sf

    if not FIXTURE_AUDIO.exists():
        pytest.fail(
            f"fixture audio missing at {FIXTURE_AUDIO} -- this file is committed to "
            "the repository (see tests/fixtures/audio/ATTRIBUTION.md); a missing "
            "file here is a checkout problem, not a licence-driven skip."
        )
    actual_sha256 = sha256_file(FIXTURE_AUDIO)
    assert actual_sha256 == FIXTURE_SHA256, (
        f"fixture audio checksum mismatch: expected {FIXTURE_SHA256}, got "
        f"{actual_sha256}. The committed recording no longer matches what "
        "tests/fixtures/audio/ATTRIBUTION.md and manifest.tsv describe -- do not "
        "trust species assertions against it until this is resolved."
    )

    pcm, native_rate = sf.read(str(FIXTURE_AUDIO), dtype="float32", always_2d=False)
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1).astype("float32")

    if native_rate == AUDIBLE_RATE:
        call = pcm
    else:
        from open_observatory.audio.resample import AudibleResampler

        call = AudibleResampler(native_rate, AUDIBLE_RATE).process(pcm).pcm

    pad = np.zeros(int(PAD_S * AUDIBLE_RATE), dtype=np.float32)
    full = np.concatenate([pad, call, pad])
    call_start_frame = pad.shape[0]
    call_end_frame = pad.shape[0] + call.shape[0]
    return full, call_start_frame, call_end_frame


async def _run_detector_over(
    detector: BirdNetDetector, pcm: np.ndarray, *, utc0_ns: int
) -> list[tuple[int, int, object]]:
    """Slide the detector's own window size across ``pcm``.

    Returns ``(window_start_frame, window_end_frame, NativeDetection)`` for
    every detection produced, so callers can recover exactly which native
    frame range each candidate came from -- the thing the alignment assertion
    needs and BirdNET's own `offset_start_s`/`offset_end_s` (always the full
    window, per `birdnet.py`) cannot supply on their own.
    """
    win_samples = detector._expected_samples
    stride = int(TEST_STRIDE_S * AUDIBLE_RATE)
    results: list[tuple[int, int, object]] = []
    offset = 0
    while offset + win_samples <= pcm.shape[0]:
        window = AudioWindow(
            window_id=uuid.uuid4(),
            stream_id=uuid.uuid4(),
            stream_kind="audible48",
            sample_rate=AUDIBLE_RATE,
            start_frame=offset,
            end_frame=offset + win_samples,
            native_start_frame=offset,
            native_end_frame=offset + win_samples,
            utc_start_ns=utc0_ns + int(offset / AUDIBLE_RATE * NS_PER_S),
            utc_end_ns=utc0_ns + int((offset + win_samples) / AUDIBLE_RATE * NS_PER_S),
            monotonic_start_ns=0,
            pcm=pcm[offset : offset + win_samples],
            spec=detector.window_spec,
            created_monotonic_ns=0,
        )
        detections = await detector.analyse(window)
        for detection in detections:
            results.append((offset, offset + win_samples, detection))
        offset += stride
    return results


class TestBirdNetKnownRecording:
    """The exit-gate test: a known recording must yield the expected species
    as a candidate, with an aligned, playable evidence clip."""

    async def test_the_fixed_date_and_location_keep_the_species_plausible(
        self, detector: BirdNetDetector, context: DetectorContext
    ) -> None:
        """Guard against the trap named in the module docstring: assert,
        against the real shipped range model, that European Robin clears the
        plausibility bar at the reference location for the exact week this test's fixed
        date computes to. If this fails, the fixture is not "wrong" -- the
        chosen date has stopped being a safe one and needs revisiting, which
        is a far more legible failure than an empty candidate list below.
        """
        await _skip_if_unavailable(detector, context)
        from open_observatory.detectors.birdnet import birdnet_week

        week = birdnet_week(ANALYSIS_LOCAL_TIME.astimezone(ZoneInfo("UTC")))
        assert week == 30, (
            "the analysis date's BirdNET week changed -- ANALYSIS_LOCAL_TIME or "
            "this assertion needs updating together"
        )
        assert detector._range is not None, "range model must be loaded for this check"
        robin_index = next(
            i for i, (_sci, common) in enumerate(detector._parsed) if common == EXPECTED_COMMON_NAME
        )
        occurrence = float(detector._range.probabilities(week)[robin_index])
        assert occurrence > 0.5, (
            f"European Robin occurrence at the reference location in week {week} is "
            f"{occurrence:.4f}, no longer comfortably 'in_range' -- the fixture's "
            "date/location choice needs revisiting, not the test's threshold"
        )

    async def test_known_recording_produces_the_expected_candidate_label(
        self, detector: BirdNetDetector, context: DetectorContext
    ) -> None:
        """Property 1 of the gate: the expected species label appears among
        the candidates. Deliberately does not assert an exact score -- scores
        are not calibrated probabilities and will drift with model or
        preprocessing changes -- only that the label is present and that its
        score is a plausible number in (0, 1].
        """
        await _skip_if_unavailable(detector, context)
        pcm, call_start, call_end = _load_padded_audible_pcm()
        utc0_ns = int(ANALYSIS_LOCAL_TIME.timestamp() * NS_PER_S)

        found = await _run_detector_over(detector, pcm, utc0_ns=utc0_ns)
        robin_hits = [
            (start, end, det)
            for start, end, det in found
            if det.common_name == EXPECTED_COMMON_NAME
        ]
        all_names = {det.common_name for _s, _e, det in found}
        assert robin_hits, (
            f"expected {EXPECTED_COMMON_NAME!r} somewhere in the candidates for "
            f"the fixture recording; got {sorted(n for n in all_names if n)}. A "
            "fast wrong answer is not a pass."
        )

        best_start, best_end, best = max(robin_hits, key=lambda item: item[2].score)
        assert best.scientific_name == EXPECTED_SCIENTIFIC_NAME
        assert 0.0 < best.score <= 1.0
        assert best.rank == "species"
        assert best.taxonomic_group == "bird"
        assert best.calibrated_probability is None, "BirdNET scores are not calibrated"
        assert best.native_result["plausibility_band"] in ("in_range", "unfiltered"), (
            "the winning detection must have cleared plausibility on its own merits, "
            f"not landed in a suppressed/strict band: {best.native_result['plausibility_band']}"
        )

        # The winning window must actually overlap where the call really is in
        # the source recording -- not some other, silent, part of the padding.
        # (The clip test below re-derives its own winning window independently,
        # from the same deterministic inputs, rather than sharing state here.)
        overlap = min(best_end, call_end) - max(best_start, call_start)
        assert overlap > 0, (
            f"the winning detection's window [{best_start}, {best_end}) does not "
            f"overlap the known call region [{call_start}, {call_end}) at all -- "
            "this would be a real-species label attached to the wrong audio"
        )

    async def test_evidence_clip_is_playable_and_aligned_to_the_call(
        self, detector: BirdNetDetector, context: DetectorContext, tmp_path: Path
    ) -> None:
        """Property 2 of the gate, and the half most likely to be skipped: the
        evidence clip must exist, be readable as audio, have the expected
        sample rate and a sane duration, and its frame bounds must genuinely
        correspond to where the call is in the source recording -- not just
        "some audio got written somewhere".
        """
        await _skip_if_unavailable(detector, context)
        pcm, call_start, call_end = _load_padded_audible_pcm()
        utc0_ns = int(ANALYSIS_LOCAL_TIME.timestamp() * NS_PER_S)

        found = await _run_detector_over(detector, pcm, utc0_ns=utc0_ns)
        robin_hits = [
            (start, end, det)
            for start, end, det in found
            if det.common_name == EXPECTED_COMMON_NAME
        ]
        assert robin_hits, "no European Robin candidate to build a clip from"
        best_start, best_end, best = max(robin_hits, key=lambda item: item[2].score)

        ring = RingBuffer(sample_rate=AUDIBLE_RATE, seconds=(pcm.shape[0] / AUDIBLE_RATE) + 5.0)
        ring.append(0, pcm, 0)

        manager = ClipManager(
            clip_dir=tmp_path / "clips",
            clip_plugins=("birdnet-v2.4",),
            min_score=0.0,
            pre_roll_s=1.0,
            post_roll_s=1.0,
            max_duration_s=30.0,
            write_playback_derivative=True,
        )
        event_start_utc = ANALYSIS_LOCAL_TIME.astimezone(ZoneInfo("UTC"))
        assets = manager.extract(
            ring=ring,
            detection_id=uuid.uuid4(),
            stream_id=uuid.uuid4(),
            event_start_frame=best_start,
            event_end_frame=best_end,
            score=best.score,
            label=best.label,
            event_start_utc=event_start_utc,
            plugin_id="birdnet-v2.4",
        )
        assert assets, "ClipManager produced no evidence clip for an admitted detection"
        asset = assets[0]

        assert asset.path.exists(), f"clip file missing at {asset.path}"
        assert asset.mime_type == "audio/wav"

        import soundfile as sf

        clip_pcm, clip_rate = sf.read(str(asset.path), dtype="float32", always_2d=False)
        assert clip_rate == AUDIBLE_RATE == asset.sample_rate, "clip must play at the native audible rate"

        # Sane duration: at least the event itself, at most event + roll + slack.
        min_expected_s = (best_end - best_start) / AUDIBLE_RATE
        max_expected_s = min_expected_s + manager.pre_roll_s + manager.post_roll_s + 0.5
        assert min_expected_s <= asset.duration_s <= max_expected_s, (
            f"clip duration {asset.duration_s:.2f}s is not between the expected "
            f"[{min_expected_s:.2f}, {max_expected_s:.2f}]s bounds"
        )
        assert clip_pcm.shape[0] == pytest.approx(asset.duration_s * AUDIBLE_RATE, abs=2)

        # Alignment: the clip's own recorded frame bounds must overlap where the
        # call actually is in the source recording, with real margin -- not a
        # one-sample coincidence.
        overlap = min(asset.end_frame, call_end) - max(asset.start_frame, call_start)
        assert overlap > AUDIBLE_RATE * 0.5, (
            f"clip frame bounds [{asset.start_frame}, {asset.end_frame}) overlap the "
            f"known call region [{call_start}, {call_end}) by only {overlap} frames"
        )

        # Stronger than overlap: the clip's actual samples must equal the
        # corresponding slice of the source recording (within 16-bit PCM
        # quantisation, since the clip is written as PCM_16), proving this is
        # not merely "audio from roughly the right place" but the right audio,
        # frame for frame -- "frames, not timestamps, address audio".
        reference = pcm[asset.start_frame : asset.end_frame]
        assert reference.shape[0] == clip_pcm.shape[0], "clip length must match its own recorded frame bounds"
        np.testing.assert_allclose(
            clip_pcm,
            reference,
            atol=2.0 / 32768.0,
            err_msg="written clip audio does not match the source recording at its own recorded frame bounds",
        )
