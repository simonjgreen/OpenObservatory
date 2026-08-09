"""The one firmware image this station will offer the counter-top display.

ADR-050. The station holds the image; the display never needs a cable again.

Deliberately dependency-free -- no SQLAlchemy, no FastAPI, no Pydantic -- for
the same reason ``plausibility.py`` is: ``display_channel.py`` is documented as
free of the database and of the web framework, and the display socket has to be
able to ask "is there an update for this build?" without importing either.

**One image, not a catalogue.** A station serving several firmware versions
would need a rollout policy, a per-device pin and a way to reason about which
display is on which build, none of which a house with one display needs. The
whole feature is "the operator has a new build and wants it on the glass". A
second version is expressed by replacing the first.

**The station never decides what is newer than what.** It records the version
the operator supplied and offers it; the *display* compares it against its own
running version and refuses anything not strictly newer. That check exists on
both sides because they protect against different mistakes -- a station that
mislabels a build, and a display that has somehow ended up ahead of it -- but
the display's copy is the one that guards the flash write, so it is the one
that matters.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import struct
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Bytes an app slot can hold: 0x1F0000, from
#: ``firmware/inside-observer/partitions/inside-observer.csv``. An image larger
#: than this cannot be installed, so it is refused at upload rather than
#: discovered by a display halfway through writing it.
APP_SLOT_BYTES = 0x1F0000

#: Below this, whatever was uploaded is not a firmware image for this board.
#: The smallest thing that has ever come out of this project's build is about
#: 900 kB; 64 kB is a floor that only catches obvious mistakes (an empty file, a
#: stray JSON blob) without needing to be revised every time the build shrinks.
MIN_IMAGE_BYTES = 64 * 1024

#: First byte of any ESP32 application image.
ESP_IMAGE_MAGIC = 0xE9
#: ``esp_app_desc_t.magic_word``, at a fixed offset in every IDF/Arduino app
#: image: 24-byte image header + 8-byte segment header = 0x20.
APP_DESC_OFFSET = 0x20
APP_DESC_MAGIC = 0xABCD5432
#: ``esp_image_header_t.chip_id``. 0 is ESP32; this board is an ESP32-D0WD-V3.
#: An ESP32-S3 image flashed to an ESP32 does not boot, and the display has no
#: way to say so from a shelf.
CHIP_ID_OFFSET = 12
CHIP_ID_ESP32 = 0

MANIFEST_NAME = "manifest.json"
IMAGE_NAME = "inside-observer.bin"


class FirmwareError(ValueError):
    """An image or a version this station will not offer to a display."""


def is_plausible_version(value: str) -> bool:
    """1-4 dot-separated runs of digits, nothing else.

    Exactly the rule ``isPlausibleVersion`` enforces in
    ``firmware/inside-observer/src/model/ota_policy.cpp``, and it has to stay
    exactly that rule: a version the station accepts and the display refuses to
    parse is a rollout that silently never lands. A suffix scheme ("0.2.0-rc1")
    is rejected on both sides rather than ordered by guesswork.
    """
    if not value or len(value) > 15:
        return False
    parts = value.split(".")
    if len(parts) > 4:
        return False
    return all(part.isdigit() and 1 <= len(part) <= 5 and part.isascii() for part in parts)


def compare_versions(left: str, right: str) -> int:
    """-1, 0 or 1. Missing components read as zero, so "0.2" == "0.2.0".

    Returns 0 for anything either side cannot parse, which callers must read as
    "not newer" -- never as "equal, so probably fine".
    """
    if not is_plausible_version(left) or not is_plausible_version(right):
        return 0
    a = [int(part) for part in left.split(".")]
    b = [int(part) for part in right.split(".")]
    width = max(len(a), len(b))
    a += [0] * (width - len(a))
    b += [0] * (width - len(b))
    for one, two in zip(a, b, strict=True):
        if one != two:
            return -1 if one < two else 1
    return 0


def validate_image(payload: bytes) -> None:
    """Refuse anything that is not an ESP32 application image for this board.

    Not security -- a checksum the uploader also supplies proves nothing about
    intent (see ADR-050, "What this does not defend against"). This is about the
    likely mistake: uploading ``firmware.elf``, or the merged 4 MB flash dump, or
    a build for a different chip. Each of those would download cleanly, verify
    cleanly against its own digest, and then not boot -- and a display that does
    not boot is the car journey this whole feature exists to avoid.
    """
    if len(payload) < MIN_IMAGE_BYTES:
        raise FirmwareError(
            f"{len(payload)} bytes is too small to be a firmware image for this board "
            f"(expected at least {MIN_IMAGE_BYTES})."
        )
    if len(payload) > APP_SLOT_BYTES:
        raise FirmwareError(
            f"{len(payload)} bytes will not fit an app slot ({APP_SLOT_BYTES} bytes). "
            "The display would refuse it, so it is refused here."
        )
    if payload[0] != ESP_IMAGE_MAGIC:
        raise FirmwareError(
            "This is not an ESP32 application image: it does not start with 0xE9. "
            "Upload .pio/build/cyd/firmware.bin, not firmware.elf and not a "
            "whole-flash backup."
        )
    if payload[CHIP_ID_OFFSET] != CHIP_ID_ESP32:
        raise FirmwareError(
            f"This image declares chip id {payload[CHIP_ID_OFFSET]}, not ESP32 (0). "
            "It would not boot on this display."
        )
    (magic,) = struct.unpack_from("<I", payload, APP_DESC_OFFSET)
    if magic != APP_DESC_MAGIC:
        raise FirmwareError(
            "This image has no ESP-IDF application descriptor where one is "
            "required, so it is not a bootable application for this board."
        )


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class FirmwareRelease:
    """What the station is currently offering, if anything."""

    version: str
    sha256: str
    size_bytes: int
    published_utc: str
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class FirmwareStore:
    """One directory holding at most one image and its manifest.

    Writes are atomic: the image goes to a temporary file in the same directory
    and is renamed into place, and the manifest is written the same way *after*
    it. A crash between the two leaves an image nobody is offering, which is
    inert; the reverse order would leave a manifest advertising a digest for
    bytes that are not there, and the first display to connect would be offered
    an image it cannot fetch.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    @property
    def image_path(self) -> Path:
        return self.directory / IMAGE_NAME

    @property
    def manifest_path(self) -> Path:
        return self.directory / MANIFEST_NAME

    def current(self) -> FirmwareRelease | None:
        """The published release, or ``None``.

        Returns ``None`` rather than raising for a manifest that is missing,
        unreadable, or describes an image that is not on disk. A station whose
        firmware directory has been half-deleted must keep capturing and keep
        pushing detections; a firmware offer is the least important thing it
        does.
        """
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            release = FirmwareRelease(
                version=str(raw["version"]),
                sha256=str(raw["sha256"]),
                size_bytes=int(raw["size_bytes"]),
                published_utc=str(raw["published_utc"]),
                notes=str(raw.get("notes", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None
        try:
            if self.image_path.stat().st_size != release.size_bytes:
                return None
        except OSError:
            return None
        return release

    def publish(self, payload: bytes, *, version: str, notes: str = "") -> FirmwareRelease:
        """Validate, store and announce one image. Replaces whatever was there."""
        if not is_plausible_version(version):
            raise FirmwareError(
                f"{version!r} is not a version this display can order against. "
                "Use dot-separated numbers only, e.g. 0.2.1 -- the firmware "
                "refuses to guess where a suffix like '-rc1' sorts."
            )
        validate_image(payload)

        self.directory.mkdir(parents=True, exist_ok=True)
        release = FirmwareRelease(
            version=version,
            sha256=digest(payload),
            size_bytes=len(payload),
            published_utc=datetime.now(UTC).isoformat(timespec="seconds"),
            notes=notes,
        )
        self._atomic_write(self.image_path, payload)
        self._atomic_write(
            self.manifest_path,
            (json.dumps(release.as_dict(), indent=2) + "\n").encode("utf-8"),
        )
        return release

    def withdraw(self) -> bool:
        """Stop offering. Returns whether anything was being offered.

        The manifest goes first, so there is never a moment where the image is
        gone but something still claims it is there.
        """
        had = self.current() is not None
        for path in (self.manifest_path, self.image_path):
            with contextlib.suppress(OSError):
                path.unlink()
        return had

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".fw-")
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise


def should_offer(release: FirmwareRelease | None, running_version: str | None) -> bool:
    """Whether a display running ``running_version`` should be told about ``release``.

    ``None``/unknown running version means the display did not say -- an older
    build than ADR-050, which has no update path and would ignore the frame
    anyway. Offering to it costs bytes and achieves nothing, so we do not.
    """
    if release is None or not running_version:
        return False
    return compare_versions(release.version, running_version) > 0
