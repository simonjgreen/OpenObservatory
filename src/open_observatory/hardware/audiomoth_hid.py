"""Minimal AudioMoth USB-HID client speaking directly to ``/dev/hidraw*``.

Open Acoustic Devices ship a helper binary (``usbhidtool-linux``) with
``AudioMoth-HID``, but it is an x86-64 ELF and therefore unusable on the aarch64
target. The wire protocol is trivial — a 64-byte output report whose first byte
is the (unnumbered) report id and whose second byte is the message type — so we
implement it here rather than depend on an unavailable binary.

Message types are taken from ``OpenAcousticDevices/AudioMoth-HID``
(``audiomoth-hid.js``), MIT licensed.
"""

from __future__ import annotations

import contextlib
import glob
import os
import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

VENDOR_ID = 0x10C4
# Application firmware exposes this product id over HID. The serial bootloader
# appears separately as a CDC ACM device with product id 0x0003.
PRODUCT_ID_APPLICATION = 0x0002
PRODUCT_ID_BOOTLOADER = 0x0003

REPORT_SIZE = 64
FIRMWARE_DESCRIPTION_LENGTH = 32


class Message(IntEnum):
    GET_TIME = 0x01
    SET_TIME = 0x02
    GET_UID = 0x03
    GET_BATTERY = 0x04
    GET_APP_PACKET = 0x05
    SET_APP_PACKET = 0x06
    GET_FIRMWARE_VERSION = 0x07
    GET_FIRMWARE_DESCRIPTION = 0x08
    QUERY_SERIAL_BOOTLOADER = 0x09
    ENTER_SERIAL_BOOTLOADER = 0x0A
    QUERY_USBHID_BOOTLOADER = 0x0B
    ENTER_USBHID_BOOTLOADER = 0x0C


class AudioMothHidError(RuntimeError):
    """Raised when the device is absent, inaccessible or answers incoherently."""


@dataclass(frozen=True, slots=True)
class AudioMothIdentity:
    hidraw_path: str
    firmware_version: tuple[int, int, int]
    firmware_description: str
    device_uid: str
    battery_state: int
    supports_serial_bootloader: bool


def _sysfs_usb_ids(hidraw_name: str) -> tuple[int, int] | None:
    """Walk up from ``/sys/class/hidraw/hidrawN`` to the owning USB device."""
    device = Path("/sys/class/hidraw") / hidraw_name / "device"
    try:
        node = device.resolve(strict=True)
    except OSError:
        return None
    for _ in range(6):
        vendor = node / "idVendor"
        product = node / "idProduct"
        if vendor.exists() and product.exists():
            try:
                return int(vendor.read_text().strip(), 16), int(product.read_text().strip(), 16)
            except ValueError:
                return None
        if node.parent == node:
            break
        node = node.parent
    return None


def find_hidraw_devices() -> list[str]:
    """Return ``/dev/hidrawN`` paths that belong to an AudioMoth application interface."""
    matches: list[str] = []
    for path in sorted(glob.glob("/dev/hidraw*")):
        ids = _sysfs_usb_ids(os.path.basename(path))
        if ids == (VENDOR_ID, PRODUCT_ID_APPLICATION):
            matches.append(path)
    return matches


def find_bootloader_ports() -> list[str]:
    """Return serial ports exposed by the AudioMoth USB (CDC) bootloader."""
    ports: list[str] = []
    for path in sorted(glob.glob("/dev/ttyACM*")):
        name = os.path.basename(path)
        link = Path("/sys/class/tty") / name / "device"
        try:
            node = link.resolve(strict=True)
        except OSError:
            continue
        for _ in range(6):
            vendor = node / "idVendor"
            product = node / "idProduct"
            if vendor.exists() and product.exists():
                try:
                    vid = int(vendor.read_text().strip(), 16)
                    pid = int(product.read_text().strip(), 16)
                except ValueError:
                    break
                if vid == VENDOR_ID and pid in (PRODUCT_ID_BOOTLOADER, PRODUCT_ID_APPLICATION):
                    ports.append(path)
                break
            if node.parent == node:
                break
            node = node.parent
    return ports


class AudioMothHid:
    """Blocking request/response client for one AudioMoth HID interface."""

    def __init__(self, path: str | None = None, *, timeout_s: float = 2.0) -> None:
        if path is None:
            candidates = find_hidraw_devices()
            if not candidates:
                raise AudioMothHidError(
                    "no AudioMoth HID interface found; check the USB cable and that the "
                    "side switch is in the USB/OFF position"
                )
            path = candidates[0]
        self.path = path
        self._timeout_s = timeout_s
        try:
            self._fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as exc:
            raise AudioMothHidError(
                f"{path} is not writable. Install the udev rule from "
                "docs/operations/AUDIOMOTH_FIRMWARE.md, or run this command with sudo."
            ) from exc
        except OSError as exc:
            raise AudioMothHidError(f"cannot open {path}: {exc}") from exc

    def close(self) -> None:
        with contextlib.suppress(OSError):
            os.close(self._fd)

    def __enter__(self) -> AudioMothHid:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _request(self, message: Message, payload: bytes = b"") -> bytes:
        # Byte 0 is the report id. AudioMoth uses unnumbered reports, so it is
        # always zero and the kernel strips it before the device sees it.
        report = bytearray(REPORT_SIZE)
        report[1] = int(message)
        report[2 : 2 + len(payload)] = payload
        self._drain()
        try:
            os.write(self._fd, bytes(report))
        except OSError as exc:
            raise AudioMothHidError(f"write to {self.path} failed: {exc}") from exc

        deadline = time.monotonic() + self._timeout_s
        while time.monotonic() < deadline:
            try:
                data = os.read(self._fd, REPORT_SIZE)
            except BlockingIOError:
                time.sleep(0.005)
                continue
            except OSError as exc:
                raise AudioMothHidError(f"read from {self.path} failed: {exc}") from exc
            if not data:
                time.sleep(0.005)
                continue
            if data[0] != int(message):
                # Stale or unrelated report; keep waiting for our own echo.
                continue
            return bytes(data[1:])
        raise AudioMothHidError(
            f"no response to {message.name} within {self._timeout_s:.1f}s"
        )

    def _drain(self) -> None:
        while True:
            try:
                if not os.read(self._fd, REPORT_SIZE):
                    return
            except (BlockingIOError, OSError):
                return

    def firmware_version(self) -> tuple[int, int, int]:
        body = self._request(Message.GET_FIRMWARE_VERSION)
        return body[0], body[1], body[2]

    def firmware_description(self) -> str:
        body = self._request(Message.GET_FIRMWARE_DESCRIPTION)
        raw = body[:FIRMWARE_DESCRIPTION_LENGTH]
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()

    def device_uid(self) -> str:
        body = self._request(Message.GET_UID)
        # Eight bytes, transmitted least-significant first.
        return "".join(f"{byte:02X}" for byte in reversed(body[:8]))

    def battery_state(self) -> int:
        return self._request(Message.GET_BATTERY)[0]

    def supports_serial_bootloader(self) -> bool:
        return self._request(Message.QUERY_SERIAL_BOOTLOADER)[0] == 0x01

    def supports_usbhid_bootloader(self) -> bool:
        return self._request(Message.QUERY_USBHID_BOOTLOADER)[0] == 0x01

    def set_time(self, unix_seconds: int | None = None) -> None:
        stamp = int(time.time()) if unix_seconds is None else unix_seconds
        self._request(Message.SET_TIME, struct.pack("<I", stamp))

    def enter_serial_bootloader(self) -> None:
        """Reboot into the Silicon Labs USB serial bootloader.

        The device detaches immediately, so a missing response is expected and
        must not be treated as a failure.
        """
        report = bytearray(REPORT_SIZE)
        report[1] = int(Message.ENTER_SERIAL_BOOTLOADER)
        try:
            os.write(self._fd, bytes(report))
        except OSError as exc:
            raise AudioMothHidError(f"could not request bootloader: {exc}") from exc

    def identify(self) -> AudioMothIdentity:
        return AudioMothIdentity(
            hidraw_path=self.path,
            firmware_version=self.firmware_version(),
            firmware_description=self.firmware_description(),
            device_uid=self.device_uid(),
            battery_state=self.battery_state(),
            supports_serial_bootloader=self.supports_serial_bootloader(),
        )


def wait_for_bootloader_port(timeout_s: float = 20.0) -> str:
    """Block until the serial bootloader appears, returning its device path."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ports = find_bootloader_ports()
        if ports:
            # Give the CDC interface a moment to settle before the first write.
            time.sleep(0.5)
            return ports[0]
        time.sleep(0.25)
    raise AudioMothHidError(
        f"serial bootloader did not appear within {timeout_s:.0f}s; "
        "check dmesg for a 10c4:0003 CDC device"
    )
