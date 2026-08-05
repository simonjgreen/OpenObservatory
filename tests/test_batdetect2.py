"""BatDetect2 fixture test — the Milestone 5 exit gate (ADR-017).

BatDetect2's code, weights and example recordings are all CC-BY-NC-4.0 and
are never committed to this repository (ADR-006, ADR-017). This test must
therefore **skip, not fail**, whenever the library or its example audio are
absent — exactly the discipline the BirdNET tests in test_detectors.py
already follow. When both are present, this is what lets the project say
BatDetect2 is "evaluated" rather than merely "expected to work": a known,
labelled recording must yield a detection of the correct species.

This does not import a project adapter class because none exists yet: per
ADR-017, BatDetect2 is adopted behind the deferred-queue path only once
scripts/benchmark_batdetect2.py shows real-time inference is viable on the
target device. Until then this test exercises the library directly, through
the same non-default resampling path (project soxr, not BatDetect2's
internal librosa) the benchmark script uses.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_SAMPLE_RATE_HZ = 256_000

EXPECTED_SPECIES_BY_CODE = {
    "MYOMYS": "Myotis myotis",
    "EPTSER": "Eptesicus serotinus",
    "RHIFER": "Rhinolophus ferrumequinum",
}


def _example_audio_dir() -> Path | None:
    """Mirrors scripts/benchmark_batdetect2.py's discovery order."""
    env_dir = os.environ.get("OO_BATDETECT2_EXAMPLE_AUDIO")
    if env_dir and Path(env_dir).is_dir():
        return Path(env_dir)

    local = REPO_ROOT / "models" / "batdetect2" / "example_data" / "audio"
    if local.is_dir() and (any(local.glob("*.wav")) or any(local.glob("*.WAV"))):
        return local

    try:
        import batdetect2

        packaged = Path(batdetect2.__file__).resolve().parent.parent / "example_data" / "audio"
        if packaged.is_dir() and (any(packaged.glob("*.wav")) or any(packaged.glob("*.WAV"))):
            return packaged
    except Exception:
        pass

    return None


def _expected_species_for(path: Path) -> str | None:
    name = path.name.upper()
    for code, species in EXPECTED_SPECIES_BY_CODE.items():
        if code in name:
            return species
    return None


class TestBatDetect2Available:
    """Checks requiring only the library and its bundled model weights.

    BatDetect2 1.3.1 ships its checkpoint inside the pip package, so these
    run whenever ``pip install batdetect2==1.3.1`` (plus torch) succeeded —
    no separately-fetched assets needed.
    """

    def test_model_loads_with_expected_class_count(self) -> None:
        api = pytest.importorskip("batdetect2.api")
        model, params = api.load_model()
        assert model is not None
        assert params["model_name"] == "Net2DFast"
        # 17 UK species classes as of the 1.3.1 checkpoint. A different count
        # would mean a different model shipped than the one this project
        # evaluated, and downstream label handling would need re-checking.
        assert len(params["class_names"]) == 17

    def test_default_config_targets_256khz(self) -> None:
        api = pytest.importorskip("batdetect2.api")
        config = api.get_config()
        assert config["target_samp_rate"] == TARGET_SAMPLE_RATE_HZ


class TestBatDetect2Fixture:
    """The exit-gate test: a known recording must yield the correct species.

    ADR-017's constraint is explicit: no claim without a passing fixture
    test on the target architecture. This is that test.
    """

    def test_known_recording_yields_expected_species(self) -> None:
        api = pytest.importorskip("batdetect2.api")
        import soundfile as sf

        audio_dir = _example_audio_dir()
        if audio_dir is None:
            pytest.skip(
                "BatDetect2 example recordings not present locally. ADR-017: they are "
                "CC-BY-NC-4.0, same terms as the model weights, and are never bundled "
                "with this repository. Fetch example_data/audio from "
                "https://github.com/macaodha/batdetect2 (tag v1.3.1) into "
                "models/batdetect2/example_data/audio, or set "
                "OO_BATDETECT2_EXAMPLE_AUDIO, then re-run."
            )

        files = sorted(list(audio_dir.glob("*.wav")) + list(audio_dir.glob("*.WAV")))
        labelled = [(f, _expected_species_for(f)) for f in files]
        labelled = [pair for pair in labelled if pair[1] is not None]
        if not labelled:
            pytest.skip(f"no recognised MYOMYS/EPTSER/RHIFER example files under {audio_dir}")

        path, expected_species = labelled[0]

        # Read raw audio and resample through the project's own soxr-backed
        # path (ADR-017: BatDetect2's internal librosa resampling is not the
        # path this project uses for its native 384 kHz -> 256 kHz stream).
        from open_observatory.audio.resample import AudibleResampler

        pcm, native_rate = sf.read(str(path), dtype="float32", always_2d=False)
        if pcm.ndim > 1:
            pcm = pcm.mean(axis=1).astype("float32")

        if native_rate == TARGET_SAMPLE_RATE_HZ:
            audio = pcm
        else:
            resampler = AudibleResampler(native_rate, TARGET_SAMPLE_RATE_HZ)
            audio = resampler.process(pcm).pcm

        model, _params = api.load_model()
        config = api.get_config()
        import torch

        predictions, _features, _spec = api.process_audio(
            audio,
            samp_rate=TARGET_SAMPLE_RATE_HZ,
            model=model,
            config=config,
            device=torch.device("cpu"),
        )

        assert predictions, f"BatDetect2 found no calls at all in {path.name}"
        best = max(predictions, key=lambda p: p.get("det_prob", 0.0))
        assert 0.0 <= best["det_prob"] <= 1.0
        assert best["class"] == expected_species, (
            f"expected top detection {expected_species!r} for {path.name}, "
            f"got {best['class']!r} (det_prob={best['det_prob']:.3f}). "
            "A fast wrong answer is not a pass."
        )
