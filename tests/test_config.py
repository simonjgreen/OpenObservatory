"""Tests for tuple-typed `Settings` fields set from the environment.

pydantic-settings tries to JSON-decode a tuple-typed field's raw *environment*
value -- via `EnvSettingsSource` -- before any of this project's own
comma-splitting validators run. A plain comma-separated value such as
`384000,192000` -- exactly what `config/example.env` shows an operator -- is
not valid JSON, so setting one of these fields the documented way raised
`SettingsError` at startup and brought the whole station down. (Passing the
same value as a constructor keyword does not reproduce the bug: only the env/
dotenv sources apply the JSON-decode pass, so every test below goes through
`monkeypatch.setenv`, matching how an operator actually sets these.)

`auth_public_read_paths` (ADR-034) was fixed by annotating the field
`NoDecode`, which opts it out of pydantic-settings' own JSON-decode pass so
the field's `mode="before"` validator sees the raw string first. This file
applies the same annotation to `preferred_sample_rates`, `preferred_formats`
and `clip_plugins` -- the three fields `config/example.env` warned an
operator away from setting -- plus `activity_band_hz` and `ultrasonic_band_hz`,
which have the identical latent bug (found while auditing for it).

Every field is tested for: comma form (what an operator types), JSON form
(what may already be sitting in a live `config/runtime.env`, which this
repository never sees and must not break), a single value, an empty value,
and comma/JSON forms with incidental whitespace.
"""

from __future__ import annotations

import pytest

from open_observatory.config import Settings


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


class TestPreferredSampleRates:
    """`tuple[int, ...]`."""

    def test_comma_separated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_PREFERRED_SAMPLE_RATES", "384000,192000")
        assert _settings().preferred_sample_rates == (384000, 192000)

    def test_json_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_PREFERRED_SAMPLE_RATES", "[384000, 192000]")
        assert _settings().preferred_sample_rates == (384000, 192000)

    def test_single_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_PREFERRED_SAMPLE_RATES", "384000")
        assert _settings().preferred_sample_rates == (384000,)

    def test_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_PREFERRED_SAMPLE_RATES", "")
        assert _settings().preferred_sample_rates == ()

    def test_whitespace_around_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_PREFERRED_SAMPLE_RATES", " 384000 , 192000 ")
        assert _settings().preferred_sample_rates == (384000, 192000)


class TestPreferredFormats:
    """`tuple[str, ...]`."""

    def test_comma_separated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_PREFERRED_FORMATS", "S16_LE,S32_LE")
        assert _settings().preferred_formats == ("S16_LE", "S32_LE")

    def test_json_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_PREFERRED_FORMATS", '["S16_LE", "S32_LE"]')
        assert _settings().preferred_formats == ("S16_LE", "S32_LE")

    def test_single_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_PREFERRED_FORMATS", "S16_LE")
        assert _settings().preferred_formats == ("S16_LE",)

    def test_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_PREFERRED_FORMATS", "")
        assert _settings().preferred_formats == ()

    def test_whitespace_around_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_PREFERRED_FORMATS", " S16_LE , S32_LE ")
        assert _settings().preferred_formats == ("S16_LE", "S32_LE")


class TestClipPlugins:
    """`tuple[str, ...]`."""

    def test_comma_separated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_CLIP_PLUGINS", "birdnet-v2.4,ultrasonic-pass-v1")
        assert _settings().clip_plugins == ("birdnet-v2.4", "ultrasonic-pass-v1")

    def test_json_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_CLIP_PLUGINS", '["birdnet-v2.4", "ultrasonic-pass-v1"]')
        assert _settings().clip_plugins == ("birdnet-v2.4", "ultrasonic-pass-v1")

    def test_single_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_CLIP_PLUGINS", "birdnet-v2.4")
        assert _settings().clip_plugins == ("birdnet-v2.4",)

    def test_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_CLIP_PLUGINS", "")
        assert _settings().clip_plugins == ()

    def test_whitespace_around_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_CLIP_PLUGINS", " birdnet-v2.4 , ultrasonic-pass-v1 ")
        assert _settings().clip_plugins == ("birdnet-v2.4", "ultrasonic-pass-v1")


class TestActivityBandHz:
    """`tuple[float, float]` -- same latent bug, found while auditing for it."""

    def test_comma_separated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_ACTIVITY_BAND_HZ", "1200,11000")
        assert _settings().activity_band_hz == (1200.0, 11000.0)

    def test_json_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_ACTIVITY_BAND_HZ", "[1200.0, 11000.0]")
        assert _settings().activity_band_hz == (1200.0, 11000.0)

    def test_whitespace_around_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_ACTIVITY_BAND_HZ", " 1200 , 11000 ")
        assert _settings().activity_band_hz == (1200.0, 11000.0)


class TestUltrasonicBandHz:
    """`tuple[float, float]` -- same latent bug, found while auditing for it."""

    def test_comma_separated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_ULTRASONIC_BAND_HZ", "15000,125000")
        assert _settings().ultrasonic_band_hz == (15000.0, 125000.0)

    def test_json_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_ULTRASONIC_BAND_HZ", "[15000.0, 125000.0]")
        assert _settings().ultrasonic_band_hz == (15000.0, 125000.0)

    def test_whitespace_around_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OO_ULTRASONIC_BAND_HZ", " 15000 , 125000 ")
        assert _settings().ultrasonic_band_hz == (15000.0, 125000.0)
