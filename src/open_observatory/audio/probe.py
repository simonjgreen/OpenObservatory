"""Capture-device discovery.

Card numbering is unstable across reboots and hot-plugs, so nothing in this
project is allowed to address a device as ``hw:1,0`` (technical spec §4.1).
A device is identified by a *stable device key* derived from its USB
vendor/product/serial, and opened through the ``/dev/snd/by-id`` symlink or the
ALSA card *id* string — never the index.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

PROC_ASOUND = Path("/proc/asound")
SND_BY_ID = Path("/dev/snd/by-id")

_CARD_LINE = re.compile(r"^\s*(\d+)\s*\[(\S+)\s*\]:\s*(\S+)\s*-\s*(.*)$")


@dataclass(slots=True)
class PcmProfile:
    """One altsetting the kernel reports for a USB audio capture interface."""

    sample_format: str
    channels: int
    sample_rates: tuple[int, ...]
    bits: int | None = None
    channel_map: str | None = None
    endpoint: str | None = None


@dataclass(slots=True)
class CaptureDevice:
    card_index: int
    card_id: str
    driver: str
    card_name: str
    long_name: str = ""
    #: Path we actually open, e.g. ``hw:CARD=Microphone,DEV=0``.
    alsa_address: str = ""
    by_id_symlink: str | None = None
    usb_vendor_id: str | None = None
    usb_product_id: str | None = None
    usb_serial: str | None = None
    profiles: list[PcmProfile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def stable_device_key(self) -> str:
        """Identity that survives reboots, hot-plugs and card renumbering."""
        if self.usb_vendor_id and self.usb_product_id:
            serial = self.usb_serial or "noserial"
            return f"usb-{self.usb_vendor_id}:{self.usb_product_id}:{serial}"
        return f"alsa-{self.card_id}"

    @property
    def advertised_rates(self) -> tuple[int, ...]:
        rates: set[int] = set()
        for profile in self.profiles:
            rates.update(profile.sample_rates)
        return tuple(sorted(rates, reverse=True))

    @property
    def is_usb(self) -> bool:
        return self.driver.upper().startswith("USB")

    def to_dict(self) -> dict[str, object]:
        return {
            "stable_device_key": self.stable_device_key,
            "card_index": self.card_index,
            "card_id": self.card_id,
            "driver": self.driver,
            "card_name": self.card_name,
            "long_name": self.long_name,
            "alsa_address": self.alsa_address,
            "by_id_symlink": self.by_id_symlink,
            "usb": {
                "vendor_id": self.usb_vendor_id,
                "product_id": self.usb_product_id,
                "serial": self.usb_serial,
            },
            "advertised_rates": list(self.advertised_rates),
            "profiles": [
                {
                    "sample_format": p.sample_format,
                    "channels": p.channels,
                    "sample_rates": list(p.sample_rates),
                    "bits": p.bits,
                    "channel_map": p.channel_map,
                    "endpoint": p.endpoint,
                }
                for p in self.profiles
            ],
            "notes": list(self.notes),
        }


def _read_cards() -> list[tuple[int, str, str, str]]:
    cards_file = PROC_ASOUND / "cards"
    if not cards_file.exists():
        return []
    entries: list[tuple[int, str, str, str]] = []
    for line in cards_file.read_text().splitlines():
        match = _CARD_LINE.match(line)
        if match:
            index, card_id, driver, name = match.groups()
            entries.append((int(index), card_id, driver, name.strip()))
    return entries


def _has_capture(card_index: int) -> bool:
    card_dir = PROC_ASOUND / f"card{card_index}"
    if not card_dir.exists():
        return False
    return any(child.name.endswith("c") and child.name.startswith("pcm") for child in card_dir.iterdir())


def _parse_stream_file(text: str) -> tuple[list[PcmProfile], str]:
    """Parse the ``Capture:`` section of ``/proc/asound/cardN/streamM``."""
    profiles: list[PcmProfile] = []
    section: str | None = None
    current: dict[str, object] | None = None
    long_name = text.splitlines()[0].strip() if text else ""

    def flush() -> None:
        nonlocal current
        if current and current.get("sample_format") and current.get("sample_rates"):
            profiles.append(
                PcmProfile(
                    sample_format=str(current["sample_format"]),
                    channels=int(current.get("channels", 1) or 1),
                    sample_rates=tuple(current["sample_rates"]),  # type: ignore[arg-type]
                    bits=current.get("bits"),  # type: ignore[arg-type]
                    channel_map=current.get("channel_map"),  # type: ignore[arg-type]
                    endpoint=current.get("endpoint"),  # type: ignore[arg-type]
                )
            )
        current = None

    for raw in text.splitlines():
        line = raw.strip()
        if line in ("Capture:", "Playback:"):
            flush()
            section = line.rstrip(":")
            continue
        if section != "Capture":
            continue
        if line.startswith("Altset"):
            flush()
            current = {}
            continue
        if current is None:
            continue
        if line.startswith("Format:"):
            current["sample_format"] = line.split(":", 1)[1].strip()
        elif line.startswith("Channels:"):
            with_default = line.split(":", 1)[1].strip()
            current["channels"] = int(with_default) if with_default.isdigit() else 1
        elif line.startswith("Rates:"):
            rates = [int(r) for r in re.findall(r"\d+", line.split(":", 1)[1])]
            current["sample_rates"] = tuple(sorted(set(rates), reverse=True))
        elif line.startswith("Bits:"):
            digits = re.findall(r"\d+", line)
            current["bits"] = int(digits[0]) if digits else None
        elif line.startswith("Channel map:"):
            current["channel_map"] = line.split(":", 1)[1].strip()
        elif line.startswith("Endpoint:"):
            current["endpoint"] = line.split(":", 1)[1].strip()
    flush()
    return profiles, long_name


def _usb_ids_for_card(card_index: int) -> tuple[str | None, str | None, str | None]:
    """Resolve USB vendor/product/serial by walking sysfs up from the card."""
    node = Path(f"/sys/class/sound/card{card_index}")
    try:
        node = node.resolve(strict=True)
    except OSError:
        return None, None, None
    for _ in range(8):
        vendor = node / "idVendor"
        product = node / "idProduct"
        if vendor.exists() and product.exists():
            serial_file = node / "serial"
            serial = serial_file.read_text().strip() if serial_file.exists() else None
            return (
                vendor.read_text().strip().lower(),
                product.read_text().strip().lower(),
                serial,
            )
        if node.parent == node:
            break
        node = node.parent
    return None, None, None


def _by_id_symlink_for_card(card_index: int) -> str | None:
    if not SND_BY_ID.is_dir():
        return None
    for link in sorted(SND_BY_ID.iterdir()):
        try:
            target = link.resolve()
        except OSError:
            continue
        if target.name in (f"controlC{card_index}", f"pcmC{card_index}D0c"):
            return str(link)
    return None


def enumerate_capture_devices() -> list[CaptureDevice]:
    """List every ALSA card that offers a capture PCM."""
    devices: list[CaptureDevice] = []
    for index, card_id, driver, name in _read_cards():
        if not _has_capture(index):
            continue
        device = CaptureDevice(
            card_index=index,
            card_id=card_id,
            driver=driver,
            card_name=name,
            alsa_address=f"hw:CARD={card_id},DEV=0",
        )
        card_dir = PROC_ASOUND / f"card{index}"
        for stream_file in sorted(card_dir.glob("stream*")):
            try:
                profiles, long_name = _parse_stream_file(stream_file.read_text())
            except OSError:
                continue
            device.profiles.extend(profiles)
            if long_name and not device.long_name:
                device.long_name = long_name
        vendor, product, serial = _usb_ids_for_card(index)
        device.usb_vendor_id, device.usb_product_id, device.usb_serial = vendor, product, serial
        device.by_id_symlink = _by_id_symlink_for_card(index)
        if device.is_usb and not device.profiles:
            device.notes.append(
                "kernel reported no capture altsettings; rates will be discovered by probing"
            )
        devices.append(device)
    return devices


def find_device(key: str | None) -> CaptureDevice | None:
    """Resolve a device by stable key, card id, or ``None`` for best guess.

    With no key, prefer a USB capture device (an AudioMoth by name when present)
    over on-board codecs, which on a Pi are HDMI outputs with no useful input.
    """
    devices = enumerate_capture_devices()
    if not devices:
        return None
    if key:
        for device in devices:
            if key in (device.stable_device_key, device.card_id, device.alsa_address):
                return device
        return None
    audiomoth = [d for d in devices if "audiomoth" in f"{d.card_name} {d.long_name}".lower()]
    if audiomoth:
        return audiomoth[0]
    usb = [d for d in devices if d.is_usb]
    return usb[0] if usb else devices[0]


def probe_supported_rates(
    device: CaptureDevice, candidates: tuple[int, ...], sample_format: str = "S16_LE"
) -> dict[int, str]:
    """Ask ALSA directly whether each candidate rate is accepted *natively*.

    A USB device happily accepts any rate through ALSA's ``plug`` layer by
    resampling behind our back, which would silently destroy the ultrasonic band
    we captured the device for. This opens the ``hw:`` device so only genuinely
    supported rates succeed.

    Returns one of ``"supported"``, ``"unsupported"``, ``"resampled"`` (the device
    substituted a different rate) or ``"busy"`` per rate. ``"busy"`` is reported
    separately from ``"unsupported"`` on purpose: while the station is capturing it
    owns the device exclusively, and reporting every rate as unsupported in that
    situation would be a false negative that looks like broken hardware.
    """
    try:
        import alsaaudio
    except ImportError:
        return {}

    fmt = getattr(alsaaudio, f"PCM_FORMAT_{sample_format}", alsaaudio.PCM_FORMAT_S16_LE)
    results: dict[int, str] = {}
    for rate in candidates:
        pcm = None
        try:
            pcm = alsaaudio.PCM(
                alsaaudio.PCM_CAPTURE,
                alsaaudio.PCM_NONBLOCK,
                device=device.alsa_address,
                channels=1,
                rate=rate,
                format=fmt,
                periodsize=1024,
            )
            # pyalsaaudio reports what was actually negotiated, which may differ
            # from what we asked for.
            actual = int(pcm.info().get("rate", rate))
            results[rate] = "supported" if actual == rate else "resampled"
        except Exception as exc:
            message = str(exc).lower()
            if "busy" in message or "resource temporarily unavailable" in message:
                results[rate] = "busy"
            else:
                results[rate] = "unsupported"
        finally:
            if pcm is not None:
                with contextlib.suppress(Exception):
                    pcm.close()
    return results


def system_report() -> dict[str, object]:
    """Host facts worth recording alongside a capture diagnostic."""

    def read(path: str) -> str | None:
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None

    def run(*cmd: str) -> str | None:
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=5, check=False
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    temp_raw = read("/sys/class/thermal/thermal_zone0/temp")
    return {
        "kernel": run("uname", "-srm"),
        "os_release": read("/etc/os-release"),
        "model": read("/proc/device-tree/model"),
        "cpu_temperature_c": (float(temp_raw) / 1000.0 if temp_raw and temp_raw.isdigit() else None),
        "throttled": run("vcgencmd", "get_throttled"),
        "asound_cards": read("/proc/asound/cards"),
        "usb_devices": run("lsusb"),
    }
