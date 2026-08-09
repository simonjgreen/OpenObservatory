"""Site settings: the web-UI-managed slice of runtime configuration.

Covers the three claims site_settings.py makes:

1. runtime.env round-trips: UI writes are indistinguishable from hand edits,
   comments and non-whitelisted keys survive byte-for-byte, and a cleared
   optional falls back to the shipped default by *removing* its key.
2. Validation refuses bad values by name (all of them at once) and refuses
   non-whitelisted fields outright.
3. The API applies live what is safe, persists everything, and reports
   coordinates as pending-restart rather than pretending they are in force.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from open_observatory.api.app import create_app
from open_observatory.config import Settings, set_settings
from open_observatory.site_settings import (
    EDITABLE_BY_NAME,
    RuntimeEnvStore,
    SettingValueError,
    coerce_updates,
    describe_settings,
)


class TestRuntimeEnvStore:
    def test_updates_existing_key_in_place_and_preserves_everything_else(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "runtime.env"
        path.write_text(
            "# hand-written comment\n"
            "OO_STATION_NAME=Old Name\n"
            "OO_NATIVE_RING_SECONDS=120\n",
            encoding="utf-8",
        )
        RuntimeEnvStore(path).apply({"station_name": "New Name"})
        content = path.read_text(encoding="utf-8")
        assert "OO_STATION_NAME=New Name" in content
        assert "# hand-written comment" in content
        assert "OO_NATIVE_RING_SECONDS=120" in content
        assert "Old Name" not in content

    def test_new_keys_are_appended_under_a_marked_section(self, tmp_path: Path) -> None:
        path = tmp_path / "runtime.env"
        path.write_text("OO_STATION_NAME=Somewhere\n", encoding="utf-8")
        RuntimeEnvStore(path).apply({"latitude": 51.4769, "longitude": -0.0005})
        content = path.read_text(encoding="utf-8")
        assert RuntimeEnvStore.MANAGED_MARK in content
        assert "OO_LATITUDE=51.4769" in content
        assert "OO_LONGITUDE=-0.0005" in content

    def test_none_removes_the_key_instead_of_writing_an_empty_value(
        self, tmp_path: Path
    ) -> None:
        """An absent key is the honest spelling of "unset": `OO_LATITUDE=`
        (empty) is ambiguous, and historically crashed startup outright."""
        path = tmp_path / "runtime.env"
        path.write_text("OO_LATITUDE=51.4769\nOO_LONGITUDE=-0.0005\n", encoding="utf-8")
        RuntimeEnvStore(path).apply({"latitude": None, "longitude": None})
        content = path.read_text(encoding="utf-8")
        assert "OO_LATITUDE" not in content
        assert "OO_LONGITUDE" not in content

    def test_creates_the_file_when_missing_with_owner_only_permissions(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "config" / "runtime.env"
        RuntimeEnvStore(path).apply({"mqtt_password": "hunter22"})
        assert path.exists()
        assert (path.stat().st_mode & 0o777) == 0o600
        assert "OO_MQTT_PASSWORD=hunter22" in path.read_text(encoding="utf-8")

    def test_booleans_serialise_to_lowercase_env_form(self, tmp_path: Path) -> None:
        path = tmp_path / "runtime.env"
        RuntimeEnvStore(path).apply({"mqtt_enabled": True, "mqtt_tls": False})
        content = path.read_text(encoding="utf-8")
        assert "OO_MQTT_ENABLED=true" in content
        assert "OO_MQTT_TLS=false" in content

    def test_written_file_is_readable_by_settings(self, tmp_path: Path) -> None:
        """The whole point of one file: what the UI writes, Settings reads."""
        path = tmp_path / "runtime.env"
        RuntimeEnvStore(path).apply(
            {"station_name": "Reference Station", "latitude": 51.4769, "longitude": -0.0005}
        )
        loaded = Settings(_env_file=path)
        assert loaded.station_name == "Reference Station"
        assert loaded.latitude == pytest.approx(51.4769)
        assert loaded.longitude == pytest.approx(-0.0005)


class TestCoerceUpdates:
    def test_rejects_unknown_fields_by_name(self) -> None:
        with pytest.raises(SettingValueError) as exc:
            coerce_updates({"auth_enabled": True})
        assert "auth_enabled" in exc.value.errors

    def test_rejects_bad_values_and_names_every_failure_at_once(self) -> None:
        with pytest.raises(SettingValueError) as exc:
            coerce_updates(
                {"latitude": 123.0, "timezone": "Narnia/Wardrobe", "mqtt_port": 0}
            )
        assert set(exc.value.errors) == {"latitude", "timezone", "mqtt_port"}

    def test_empty_string_means_unset_for_optionals(self) -> None:
        assert coerce_updates({"latitude": ""}) == {"latitude": None}

    def test_coerces_string_forms_of_numbers_and_booleans(self) -> None:
        coerced = coerce_updates({"latitude": "51.4769", "mqtt_enabled": "true"})
        assert coerced == {"latitude": 51.4769, "mqtt_enabled": True}

    def test_valid_timezone_and_coordinates_pass(self) -> None:
        coerced = coerce_updates(
            {"timezone": "Europe/London", "latitude": 51.4769, "longitude": -0.0005}
        )
        assert coerced["timezone"] == "Europe/London"


class TestDescribeSettings:
    def test_secret_fields_never_echo_their_value(self, settings: Settings) -> None:
        configured = settings.model_copy(update={"mqtt_password": "hunter22"})
        payload = describe_settings(configured, applied_site=None)
        password = next(f for f in payload["fields"] if f["name"] == "mqtt_password")
        assert password["value"] is None
        assert password["is_set"] is True
        assert "hunter22" not in str(payload)

    def test_reports_location_unconfigured_when_unset(self, settings: Settings) -> None:
        payload = describe_settings(settings, applied_site=None)
        assert payload["location_configured"] is False

    def test_pending_restart_names_saved_but_unapplied_coordinates(
        self, settings: Settings
    ) -> None:
        configured = settings.model_copy(update={"latitude": 51.4769, "longitude": -0.0005})
        payload = describe_settings(
            configured, applied_site={"latitude": None, "longitude": None}
        )
        assert set(payload["pending_restart"]) == {"latitude", "longitude"}

    def test_every_whitelisted_field_is_a_real_settings_field(self) -> None:
        for name in EDITABLE_BY_NAME:
            assert name in Settings.model_fields


@pytest.fixture
def api_client(settings: Settings):
    set_settings(settings)
    app = create_app(settings)
    with TestClient(app) as client:
        yield client


class TestSettingsEndpoints:
    def test_get_lists_fields_without_secrets(self, api_client: TestClient) -> None:
        payload = api_client.get("/api/v1/settings").json()
        names = {f["name"] for f in payload["fields"]}
        assert {"station_name", "latitude", "longitude", "mqtt_host"} <= names
        assert payload["location_configured"] is False

    def test_put_persists_applies_identity_live_and_flags_coordinates(
        self, api_client: TestClient, settings: Settings
    ) -> None:
        response = api_client.put(
            "/api/v1/settings",
            json={
                "station_name": "Reference Station",
                "latitude": "51.4769",
                "longitude": "-0.0005",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body["saved"]) == {"station_name", "latitude", "longitude"}
        # Persisted:
        content = settings.runtime_env_path.read_text(encoding="utf-8")
        assert "OO_STATION_NAME=Reference Station" in content
        assert "OO_LATITUDE=51.4769" in content
        # Identity applied live:
        station = api_client.get("/api/v1/station").json()["station"]
        assert station["name"] == "Reference Station"
        assert station["latitude"] == pytest.approx(51.4769)
        # Coordinates honestly reported as not yet driving the detectors:
        assert set(body["pending_restart"]) == {"latitude", "longitude"}
        assert set(station["site_pending_restart"]) == {"latitude", "longitude"}
        health = api_client.get("/api/v1/health").json()
        assert any("restart required" in note for note in health["notes"])

    def test_put_rejects_a_half_set_location(self, api_client: TestClient) -> None:
        response = api_client.put("/api/v1/settings", json={"latitude": "51.4769"})
        assert response.status_code == 422
        assert "latitude" in response.json()["detail"]["errors"]

    def test_put_rejects_non_whitelisted_fields(self, api_client: TestClient) -> None:
        response = api_client.put("/api/v1/settings", json={"bind_port": 1})
        assert response.status_code == 422

    def test_health_notes_say_no_location_configured(self, api_client: TestClient) -> None:
        health = api_client.get("/api/v1/health").json()
        assert any("no station location configured" in note for note in health["notes"])

    def test_mqtt_password_is_write_only_through_the_api(
        self, api_client: TestClient, settings: Settings
    ) -> None:
        response = api_client.put(
            "/api/v1/settings", json={"mqtt_password": "broker-secret"}
        )
        assert response.status_code == 200
        assert "broker-secret" not in response.text
        assert "OO_MQTT_PASSWORD=broker-secret" in settings.runtime_env_path.read_text(
            encoding="utf-8"
        )


class TestEmptyEnvValuesAreUnset:
    def test_copying_example_env_verbatim_does_not_crash(self, tmp_path: Path) -> None:
        """`OO_LATITUDE=` with no value -- exactly what config/example.env
        ships for every optional key -- used to raise a float-parsing
        ValidationError at startup."""
        env = tmp_path / "runtime.env"
        env.write_text(
            "OO_LATITUDE=\nOO_LONGITUDE=\nOO_AUDIO_DEVICE=\n"
            "OO_MQTT_USERNAME=\nOO_MQTT_PASSWORD=\n",
            encoding="utf-8",
        )
        loaded = Settings(_env_file=env)
        assert loaded.latitude is None
        assert loaded.longitude is None
        assert loaded.audio_device is None
        assert loaded.mqtt_username is None
        assert loaded.mqtt_password is None
