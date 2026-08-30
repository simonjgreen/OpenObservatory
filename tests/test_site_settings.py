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
from open_observatory.config import Settings, get_settings, set_settings
from open_observatory.site_settings import (
    CATEGORY_IDS,
    EDITABLE_BY_NAME,
    EDITABLE_SETTINGS,
    NON_EDITABLE,
    RuntimeEnvStore,
    SettingValueError,
    coerce_updates,
    describe_settings,
    describe_setup,
    validate_merged,
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

    def test_every_field_carries_its_shipped_default_as_a_reference_point(
        self, settings: Settings
    ) -> None:
        """ADR-041's measured floor/ceiling are only a reference point if an
        operator who has wandered away from them can get back."""
        payload = describe_settings(settings, applied_site=None)
        floor = next(
            f for f in payload["fields"] if f["name"] == "ultrasonic_spectrogram_floor_db"
        )
        assert floor["default"] == -85.0
        assert floor["unit"] == "dB"
        assert floor["tier"] == "live"

    def test_tuple_valued_fields_round_trip_as_the_comma_form_an_operator_types(
        self, settings: Settings
    ) -> None:
        payload = describe_settings(settings, applied_site=None)
        band = next(f for f in payload["fields"] if f["name"] == "ultrasonic_band_hz")
        assert band["value"] == "15000.0,125000.0"
        assert band["kind"] == "csv"

    def test_live_fields_report_pending_when_the_running_object_did_not_take_them(
        self, settings: Settings
    ) -> None:
        """The honesty constraint applied to the settings surface itself: a
        live-tier value the station saved but could not push (no ultrasonic
        encoder at this rate, detector switched off) is reported as pending,
        not as applied."""
        configured = settings.model_copy(update={"ultrasonic_min_snr_db": 30.0})
        payload = describe_settings(
            configured, applied_site={"ultrasonic_min_snr_db": 12.0}
        )
        assert payload["pending_restart"] == ["ultrasonic_min_snr_db"]

    def test_categories_and_non_editable_reasons_travel_with_the_payload(
        self, settings: Settings
    ) -> None:
        payload = describe_settings(settings, applied_site=None)
        assert [c["id"] for c in payload["categories"]] == list(CATEGORY_IDS)
        reasons = {entry["name"]: entry["reason"] for entry in payload["non_editable"]}
        assert set(reasons) == set(NON_EDITABLE)
        assert "lockout" in reasons["bind_host"]


class TestTheAuditIsComplete:
    """ADR-048's core claim: every field of Settings has a recorded decision.

    Not prose -- the two registries between them have to name every field
    exactly once, so adding a setting without deciding its tier fails here
    rather than quietly landing as "not editable, no reason given".
    """

    def test_every_settings_field_is_classified_exactly_once(self) -> None:
        classified = set(EDITABLE_BY_NAME) | set(NON_EDITABLE)
        assert set(Settings.model_fields) - classified == set()
        assert classified - set(Settings.model_fields) == set()
        assert set(EDITABLE_BY_NAME) & set(NON_EDITABLE) == set()

    def test_every_exclusion_names_a_hazard(self) -> None:
        for name, reason in NON_EDITABLE.items():
            assert len(reason) > 40, f"{name}'s exclusion is asserted, not reasoned"

    def test_the_settings_surface_cannot_weaken_the_gate_that_protects_it(self) -> None:
        for name in Settings.model_fields:
            if name.startswith("auth_"):
                assert name in NON_EDITABLE

    def test_every_editable_field_lands_in_a_declared_category(self) -> None:
        for spec in EDITABLE_SETTINGS:
            assert spec.category in CATEGORY_IDS, spec.name

    def test_defaulting_to_editable_actually_happened(self) -> None:
        """The instruction was to default to making a setting editable. If
        this ratio ever inverts, the bar for "never" has slipped."""
        assert len(EDITABLE_BY_NAME) > 6 * len(NON_EDITABLE)

    def test_implausible_species_is_editable_and_live(self) -> None:
        """ADR-076: the plausibility gate is an operator list, not a migration.

        ADR-074's first amendment proposed persisting the band as an indexed
        column -- a migration, a write-path change and an ADR of its own. Its
        second amendment found that unnecessary: which birds are *impossible
        here* is the same kind of judgement as which are *boring*, and that is
        a list a person edits.
        """
        entry = next(
            e for e in EDITABLE_SETTINGS if e.name == "evidence_implausible_species"
        )
        assert entry.category == "retention"
        assert entry.tier == "live"
        # Ships empty -- which birds are impossible is a property of where the
        # station is, not something the software can know in advance -- but
        # the field is still a live, editable operator list.
        assert get_settings().evidence_implausible_species == ()


class TestValidation:
    def test_bounds_are_enforced_with_a_message_naming_the_limit(self) -> None:
        with pytest.raises(SettingValueError) as exc:
            coerce_updates({"ultrasonic_min_snr_db": 500.0})
        assert "at most 90 dB" in exc.value.errors["ultrasonic_min_snr_db"]

    def test_a_non_editable_field_is_refused_with_its_recorded_reason(self) -> None:
        with pytest.raises(SettingValueError) as exc:
            coerce_updates({"bind_port": 9000})
        assert "lockout" in exc.value.errors["bind_port"]

    def test_enum_choices_are_enforced_including_the_ones_we_narrowed(self) -> None:
        """`replay` is a valid Settings value and deliberately not offered
        here, because it needs a file path the browser may not choose."""
        with pytest.raises(SettingValueError) as exc:
            coerce_updates({"source": "replay"})
        assert "auto, alsa, synthetic" in exc.value.errors["source"]

    def test_band_edges_accept_the_comma_form_and_reject_an_inverted_band(self) -> None:
        assert coerce_updates({"ultrasonic_band_hz": "20000, 90000"}) == {
            "ultrasonic_band_hz": (20000.0, 90000.0)
        }
        with pytest.raises(SettingValueError) as exc:
            coerce_updates({"ultrasonic_band_hz": "90000,20000"})
        assert "below the high edge" in exc.value.errors["ultrasonic_band_hz"]

    def test_blank_restores_the_shipped_default_for_a_required_field(self) -> None:
        assert coerce_updates({"ultrasonic_min_snr_db": ""}) == {
            "ultrasonic_min_snr_db": 12.0
        }
        assert coerce_updates({"spectrogram_floor_db": None}) == {
            "spectrogram_floor_db": -95.0
        }

    def test_an_empty_sequence_is_refused_because_capture_could_not_start(self) -> None:
        with pytest.raises(SettingValueError) as exc:
            coerce_updates({"preferred_sample_rates": ","})
        assert "at least one" in exc.value.errors["preferred_sample_rates"]


class TestCrossFieldValidation:
    def test_a_floor_above_its_ceiling_is_refused(self, settings: Settings) -> None:
        with pytest.raises(SettingValueError) as exc:
            validate_merged(settings, {"ultrasonic_spectrogram_floor_db": -10.0})
        assert "below the ceiling" in exc.value.errors["ultrasonic_spectrogram_floor_db"]

    def test_a_ring_shorter_than_two_capture_blocks_is_refused(
        self, settings: Settings
    ) -> None:
        with pytest.raises(SettingValueError) as exc:
            validate_merged(settings, {"capture_buffer_ms": 120.0})
        assert "two capture blocks" in exc.value.errors["capture_buffer_ms"]

    def test_an_out_of_order_retention_ladder_is_refused(self, settings: Settings) -> None:
        with pytest.raises(SettingValueError) as exc:
            validate_merged(settings, {"retention_native_days": 60})
        assert "no later than the audible tier" in exc.value.errors["retention_native_days"]

    def test_clip_rolls_that_cannot_fit_the_maximum_are_refused(
        self, settings: Settings
    ) -> None:
        with pytest.raises(SettingValueError) as exc:
            validate_merged(settings, {"clip_pre_roll_s": 20.0})
        assert "must fit inside" in exc.value.errors["clip_pre_roll_s"]

    def test_a_lone_coordinate_is_refused_against_the_merged_configuration(
        self, settings: Settings
    ) -> None:
        with pytest.raises(SettingValueError) as exc:
            validate_merged(settings, {"latitude": 51.4769})
        assert "both coordinates" in exc.value.errors["latitude"]
        # ...and accepted once its partner arrives in the same request.
        validate_merged(settings, {"latitude": 51.4769, "longitude": -0.0005})

    def test_a_pre_existing_inconsistency_does_not_block_an_unrelated_edit(
        self, settings: Settings
    ) -> None:
        """A rule only fires when one of its own fields is being changed, so
        an operator is never trapped by something they are not touching."""
        broken = settings.model_copy(update={"retention_native_days": 999})
        validate_merged(broken, {"station_name": "Somewhere"})

    def test_the_plausibility_ladder_must_stay_in_order(self, settings: Settings) -> None:
        with pytest.raises(SettingValueError):
            validate_merged(settings, {"birdnet_plausibility_floor": 0.5})


@pytest.fixture
def api_client(settings: Settings):
    set_settings(settings)
    app = create_app(settings)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def station(api_client: TestClient):
    """The running Station behind `api_client`, for asserting that a live
    setting reached the object that holds it rather than only the file."""
    return api_client.app.state.station  # type: ignore[attr-defined]


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


class TestTuningOverHttp:
    """The operator's immediate need: change a detector threshold or a
    spectrogram contrast from the browser and have it take effect now."""

    def test_a_spectrogram_contrast_edit_reaches_the_running_encoder(
        self, api_client: TestClient, settings: Settings
    ) -> None:
        response = api_client.put(
            "/api/v1/settings", json={"spectrogram_floor_db": "-70", "spectrogram_ceiling_db": "-20"}
        )
        assert response.status_code == 200
        body = response.json()
        assert set(body["saved"]) == {"spectrogram_floor_db", "spectrogram_ceiling_db"}
        # Applied, not merely saved: nothing is pending and the encoder agrees.
        assert body["pending_restart"] == []
        spec = next(
            s
            for s in api_client.get("/api/v1/station").json()["spectrograms"]
            if s["name"] == "audible"
        )
        assert spec["floor_db"] == -70.0
        assert spec["ceiling_db"] == -20.0

    def test_a_detector_threshold_edit_reaches_the_running_detector(
        self, api_client: TestClient, station
    ) -> None:
        response = api_client.put(
            "/api/v1/settings",
            json={"activity_min_snr_db": "26", "activity_band_hz": "800,9000"},
        )
        assert response.status_code == 200
        assert response.json()["pending_restart"] == []
        plugin = next(
            worker.plugin for worker in station.workers if worker.plugin_id == "activity-v1"
        )
        assert plugin._min_snr_db == 26.0
        assert plugin._band == (800.0, 9000.0)

    def test_a_live_setting_whose_target_does_not_exist_is_reported_pending(
        self, api_client: TestClient
    ) -> None:
        """This station is capturing at 48 kHz, so there is no ultrasonic
        spectrogram encoder to retune. The setting is still saved -- it will
        be in force the moment a high-rate device is attached and the station
        restarts -- but reporting it as applied would be a claim about a
        component that does not exist."""
        response = api_client.put(
            "/api/v1/settings", json={"ultrasonic_spectrogram_floor_db": "-70"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["pending_restart"] == ["ultrasonic_spectrogram_floor_db"]
        field = next(
            f for f in body["fields"] if f["name"] == "ultrasonic_spectrogram_floor_db"
        )
        assert field["pending_restart"] is True
        assert field["value"] == -70.0
        # And the same claim reaches health, where the operator will see it.
        health = api_client.get("/api/v1/health").json()
        assert any("ultrasonic_spectrogram_floor_db" in note for note in health["notes"])

    def test_a_clip_budget_edit_reaches_the_clip_manager_in_bytes(
        self, api_client: TestClient, station
    ) -> None:
        assert api_client.put(
            "/api/v1/settings", json={"clip_max_total_gb": "5"}
        ).status_code == 200
        assert station.clips.max_total_bytes == 5 * 1024**3

    def test_capture_settings_are_saved_and_reported_pending_not_applied(
        self, api_client: TestClient, settings: Settings
    ) -> None:
        """Charter item 1: a form submission never re-negotiates capture."""
        response = api_client.put("/api/v1/settings", json={"native_ring_seconds": "180"})
        assert response.status_code == 200
        assert response.json()["pending_restart"] == ["native_ring_seconds"]
        assert "OO_NATIVE_RING_SECONDS=180" in settings.runtime_env_path.read_text(
            encoding="utf-8"
        )
        health = api_client.get("/api/v1/health").json()
        assert any("native_ring_seconds" in note for note in health["notes"])

    def test_a_rejected_write_changes_neither_the_file_nor_the_process(
        self, api_client: TestClient, settings: Settings
    ) -> None:
        before_floor = settings.spectrogram_floor_db
        before_file = (
            settings.runtime_env_path.read_text(encoding="utf-8")
            if settings.runtime_env_path.exists()
            else None
        )
        response = api_client.put(
            "/api/v1/settings",
            # First is fine on its own; the pair is not.
            json={"spectrogram_floor_db": "-10", "station_name": "Somewhere"},
        )
        assert response.status_code == 422
        assert settings.spectrogram_floor_db == before_floor
        assert settings.station_name != "Somewhere"
        after_file = (
            settings.runtime_env_path.read_text(encoding="utf-8")
            if settings.runtime_env_path.exists()
            else None
        )
        assert after_file == before_file

    def test_resetting_a_tuned_value_returns_the_measured_default(
        self, api_client: TestClient, settings: Settings
    ) -> None:
        api_client.put("/api/v1/settings", json={"ultrasonic_min_snr_db": "30"})
        response = api_client.put("/api/v1/settings", json={"ultrasonic_min_snr_db": ""})
        assert response.status_code == 200
        assert settings.ultrasonic_min_snr_db == 12.0


class TestFirstRun:
    def test_setup_names_what_is_outstanding_on_a_fresh_station(
        self, api_client: TestClient
    ) -> None:
        payload = api_client.get("/api/v1/setup").json()
        assert payload["completed"] is False
        assert "location" in payload["required_outstanding"]
        assert [step["id"] for step in payload["steps"]] == [
            "location",
            "timezone",
            "microphone",
            "mqtt",
        ]

    def test_the_microphone_step_reads_live_capture_rather_than_a_stored_flag(
        self, api_client: TestClient
    ) -> None:
        """A first-run flow that ticked "microphone" while the station ran on
        a synthetic fallback would be the most expensive lie on this surface."""
        step = next(
            s for s in api_client.get("/api/v1/setup").json()["steps"] if s["id"] == "microphone"
        )
        capture = api_client.get("/api/v1/station").json()["capture"]
        assert step["done"] is capture["is_live_hardware"]
        if not capture["is_live_hardware"]:
            assert "synthetic" in step["detail"]

    def test_answering_a_step_marks_it_done(self, api_client: TestClient) -> None:
        api_client.put(
            "/api/v1/settings", json={"latitude": "51.4769", "longitude": "-0.0005"}
        )
        payload = api_client.get("/api/v1/setup").json()
        assert "location" not in payload["required_outstanding"]

    def test_dismissal_is_recorded_on_the_station_not_the_browser(
        self, api_client: TestClient, settings: Settings
    ) -> None:
        assert api_client.put(
            "/api/v1/settings", json={"setup_completed": True}
        ).status_code == 200
        assert api_client.get("/api/v1/setup").json()["completed"] is True
        assert "OO_SETUP_COMPLETED=true" in settings.runtime_env_path.read_text(
            encoding="utf-8"
        )

    def test_health_says_the_timezone_is_still_the_shipped_default(
        self, api_client: TestClient
    ) -> None:
        notes = api_client.get("/api/v1/health").json()["notes"]
        assert any("timezone is still UTC" in note for note in notes)

    def test_that_note_goes_away_once_the_operator_is_done_being_guided(
        self, api_client: TestClient
    ) -> None:
        api_client.put("/api/v1/settings", json={"setup_completed": True})
        notes = api_client.get("/api/v1/health").json()["notes"]
        assert not any("timezone is still UTC" in note for note in notes)


class TestSetupDescription:
    def test_a_low_rate_device_is_told_ultrasonic_detection_is_unavailable(
        self, settings: Settings
    ) -> None:
        payload = describe_setup(
            settings,
            capture={
                "is_live_hardware": True,
                "state": "capturing",
                "detail": "",
                "device": {"name": "USB mic", "sample_rate": 48000},
            },
        )
        step = next(s for s in payload["steps"] if s["id"] == "microphone")
        assert step["done"] is True
        assert "ultrasonic detection is unavailable" in step["detail"]


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
