"""Operator-editable site settings, managed through the web UI.

The repository describes a *system*; a deployment describes a *site*
(see docs/architecture/ADRS.md, "site parameters are runtime state").
Everything in this module exists so that parameters true of exactly one
installation -- where the station is, what it is called, which broker it
publishes to -- live in untracked runtime configuration and are editable
from the on-device UI, never committed to version control.

Persistence is ``config/runtime.env``, the same gitignored, operator-owned
file the environment-variable path has always read. The web UI is a second
writer of that file, not a second configuration system: a value set through
the UI and a value set by editing the file are indistinguishable at the next
startup, and ``oo config`` prints the merged result either way.

Three tiers, decided here and enforced by the whitelist:

* **Live** -- applied to the running process the moment they are saved
  (station identity fields; MQTT, via a publisher restart the API layer
  performs). These also persist, so a restart changes nothing.
* **Restart-pinned** -- persisted immediately and *reported* immediately,
  but deliberately not injected into running components: latitude/longitude
  are bound into the BirdNET range filter and the ultrasonic night schedule
  when detectors start, and swapping coordinates under a running range
  model would change what "plausible" means mid-stream without any detector
  row recording the switch. The API says loudly that a restart is pending
  rather than pretending the new value is in force.
* **Never browser-editable** -- everything not in the whitelist. Deliberate
  exclusions, with reasons, in the module-level comment on
  :data:`EDITABLE_SETTINGS` below.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError

from .config import Settings


@dataclass(frozen=True)
class EditableSetting:
    """One field of :class:`Settings` the web UI may change."""

    name: str
    category: str
    #: Never echoed back to a client; GET reports only whether it is set.
    secret: bool = False
    #: Persisted and reported immediately, applied at next process start.
    restart_required: bool = False
    note: str = ""


#: The whitelist. Everything else in Settings is deliberately not editable
#: from a browser:
#:
#: * ``auth_*`` -- the authentication configuration must not be editable
#:   through the surface it protects (a session could grant itself weaker
#:   auth, or lock every operator out with a typo).
#: * ``bind_host``/``bind_port`` -- changing where the API listens from the
#:   API is a remote-hands lockout with no recovery path but SSH.
#: * ``data_dir``/``database_dsn`` -- repointing storage under a running
#:   station orphans the database mid-write; this is a shutdown-and-migrate
#:   operation, not a form field.
#: * capture device/rates and detector thresholds -- operational tuning with
#:   measured defaults, changed via runtime.env with the documentation open;
#:   a misclick here silently costs recordings.
EDITABLE_SETTINGS: tuple[EditableSetting, ...] = (
    EditableSetting("station_name", "station"),
    EditableSetting("timezone", "station"),
    EditableSetting(
        "latitude",
        "station",
        restart_required=True,
        note=(
            "Bound into the BirdNET range filter and the night schedule when "
            "detectors start; saved now, in force after the next restart."
        ),
    ),
    EditableSetting(
        "longitude",
        "station",
        restart_required=True,
        note=(
            "Bound into the BirdNET range filter and the night schedule when "
            "detectors start; saved now, in force after the next restart."
        ),
    ),
    EditableSetting("mqtt_enabled", "mqtt"),
    EditableSetting("mqtt_host", "mqtt"),
    EditableSetting("mqtt_port", "mqtt"),
    EditableSetting("mqtt_tls", "mqtt"),
    EditableSetting("mqtt_tls_insecure", "mqtt"),
    EditableSetting("mqtt_username", "mqtt"),
    EditableSetting("mqtt_password", "mqtt", secret=True),
    EditableSetting("mqtt_client_id", "mqtt"),
    EditableSetting("mqtt_topic_prefix", "mqtt"),
    EditableSetting("mqtt_discovery_enabled", "mqtt"),
    EditableSetting("mqtt_discovery_prefix", "mqtt"),
)

EDITABLE_BY_NAME: dict[str, EditableSetting] = {e.name: e for e in EDITABLE_SETTINGS}


class SettingValueError(ValueError):
    """A proposed value failed validation. ``errors`` maps field -> message."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def coerce_updates(updates: dict[str, Any]) -> dict[str, Any]:
    """Validate a client's proposed updates against the Settings field types.

    Returns the coerced values. Raises :class:`SettingValueError` naming every
    failing field at once, so a form round-trips one correction pass, not one
    per mistake. Unknown fields are an error, not ignored -- silently dropping
    a key is how a UI bug looks like a saved setting.

    Cross-field rules that depend on the *merged* result (a location must be
    both coordinates or neither -- schedule.py treats a lone coordinate as
    unset) belong to the caller, which knows the current values.
    """
    errors: dict[str, str] = {}
    coerced: dict[str, Any] = {}
    for name, raw in updates.items():
        spec = EDITABLE_BY_NAME.get(name)
        if spec is None:
            errors[name] = "not an operator-editable setting"
            continue
        # "" from a cleared form field means "unset" for optional fields,
        # matching how config.py reads an empty env value.
        if isinstance(raw, str) and raw.strip() == "":
            raw = None
        try:
            adapter: TypeAdapter[Any] = TypeAdapter(Settings.model_fields[name].annotation)
            value = adapter.validate_python(raw)
        except ValidationError as exc:
            errors[name] = exc.errors()[0]["msg"]
            continue
        detail = _check_semantics(name, value)
        if detail is not None:
            errors[name] = detail
            continue
        coerced[name] = value
    if errors:
        raise SettingValueError(errors)
    return coerced


def _check_semantics(name: str, value: Any) -> str | None:
    if value is None:
        return None
    if name == "timezone":
        try:
            ZoneInfo(str(value))
        except Exception:
            return f"{value!r} is not an IANA timezone name (e.g. 'Europe/London')"
    if name == "latitude" and not -90.0 <= float(value) <= 90.0:
        return "latitude must be between -90 and 90 degrees"
    if name == "longitude" and not -180.0 <= float(value) <= 180.0:
        return "longitude must be between -180 and 180 degrees"
    if name == "mqtt_port" and not 1 <= int(value) <= 65535:
        return "port must be between 1 and 65535"
    return None


def to_env_value(value: Any) -> str | None:
    """Render a coerced value as the string ``runtime.env`` stores.

    ``None`` means the key is removed from the file entirely, falling back to
    the shipped default -- an absent key is honest about being unset, where an
    empty ``OO_LATITUDE=`` used to crash startup (see config.py's
    ``_empty_env_is_unset``).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class RuntimeEnvStore:
    """Reads and rewrites ``config/runtime.env`` without owning it.

    The file is operator state: hand-written comments and settings outside
    the UI whitelist must survive a UI save byte-for-byte. Only lines whose
    key is being updated are touched; new keys are appended under a marked
    section. Writes are atomic (tempfile + rename in the same directory) so
    a crash mid-save leaves the old file, not half a file.
    """

    MANAGED_MARK = "# --- written by the web UI settings page ---"

    def __init__(self, path: Path) -> None:
        self.path = path

    def apply(self, updates: dict[str, Any]) -> None:
        env_updates = {f"OO_{name.upper()}": to_env_value(value) for name, value in updates.items()}
        lines = (
            self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        )
        remaining = dict(env_updates)
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            key = stripped.split("=", 1)[0].strip() if "=" in stripped else None
            if key in remaining and not stripped.startswith("#"):
                value = remaining.pop(key)
                if value is not None:
                    out.append(f"{key}={value}")
                # None: drop the line -- absent key means shipped default.
                continue
            out.append(line)
        additions = [f"{key}={value}" for key, value in remaining.items() if value is not None]
        if additions:
            if self.MANAGED_MARK not in out:
                out.extend(["", self.MANAGED_MARK])
            out.extend(additions)
        self._write_atomic("\n".join(out) + "\n")

    def _write_atomic(self, content: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".runtime.env.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            # runtime.env can hold credentials; never world-readable.
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise


def describe_settings(
    settings: Settings, *, applied_site: dict[str, Any] | None
) -> dict[str, Any]:
    """The GET /api/v1/settings payload.

    ``applied_site`` is what the running detector pipeline was actually built
    with (Station.applied_site_snapshot), so a saved-but-not-yet-live value is
    reported as exactly that instead of quietly looking live.
    """
    fields = []
    pending: list[str] = []
    for spec in EDITABLE_SETTINGS:
        value = getattr(settings, spec.name)
        entry: dict[str, Any] = {
            "name": spec.name,
            "category": spec.category,
            "secret": spec.secret,
            "restart_required": spec.restart_required,
            "note": spec.note or None,
        }
        if spec.secret:
            entry["value"] = None
            entry["is_set"] = bool(value)
        else:
            entry["value"] = str(value) if isinstance(value, Path) else value
        if (
            spec.restart_required
            and applied_site is not None
            and spec.name in applied_site
            and applied_site[spec.name] != value
        ):
            entry["pending_restart"] = True
            pending.append(spec.name)
        fields.append(entry)
    return {
        "fields": fields,
        "pending_restart": pending,
        "location_configured": settings.latitude is not None and settings.longitude is not None,
    }
