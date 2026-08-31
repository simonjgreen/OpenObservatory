"""Model acquisition and licence disclosure (ADR-006).

Model assets are not in the repository and are not downloaded implicitly. The
operator runs ``oo models fetch``, sees the licence of each asset, and the
digests are verified before anything is installed. A mismatch is a hard failure:
a silently-wrong classifier would produce confident nonsense that looks exactly
like a working system.

Not every model arrives that way. BatDetect2 is ``pip install batdetect2``:
code and weights together, under one licence, with no single file to name and
no digest to verify. ADR-006's rule is about *disclosure before acquisition*,
not about file digests, so both routes are described here — separately, each
tagged with its own ``kind``. Writing a manifest row for a pip install would
claim a checksum this station never checks, which is the same shape of
dishonesty as a confident score on a species that was never there.
"""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from importlib.util import find_spec
from pathlib import Path

import structlog

from .config import REPO_ROOT

log = structlog.get_logger(__name__)

DEFAULT_MANIFEST = REPO_ROOT / "models" / "manifest.tsv"
DEFAULT_MODEL_DIR = REPO_ROOT / "models"


@dataclass(frozen=True, slots=True)
class ModelAsset:
    filename: str
    sha256: str
    licence: str
    url: str


@dataclass(frozen=True, slots=True)
class PackageModel:
    """A model acquired as a Python package, code and weights together.

    There is nothing here for :func:`fetch` to download or verify — the
    operator's ``pip install`` is the acquisition step, and the version pin is
    the only integrity claim that can honestly be made about it.
    """

    #: Import name, which is also the distribution name for every entry here.
    #: A package whose two names differ would need a second field rather than
    #: a guess, since :attr:`installed` asks the import system.
    name: str
    #: The pinned version, which is what :attr:`used_for` and the licence were
    #: checked against — not necessarily what is on this machine. See
    #: :attr:`installed_version`.
    version: str
    licence: str
    install_command: str
    url: str
    #: One line on what the station does with it, so a licence in the UI is
    #: attached to a purpose rather than floating on its own.
    used_for: str

    @property
    def installed(self) -> bool:
        """Ask the import system, without importing.

        These packages are heavy and optional — BatDetect2 pulls in PyTorch —
        and this is read by a listing endpoint that must stay cheap on a Pi.
        A half-removed install reads as absent rather than raising: absent is a
        normal state of a working station (ADR-017), and a licence listing is
        the wrong place to fail over a broken dependency.
        """
        try:
            return find_spec(self.name) is not None
        except (ImportError, ValueError):  # pragma: no cover - broken install
            return False

    @property
    def installed_version(self) -> str | None:
        """What is actually on this machine, or ``None`` if nothing is.

        Reported next to :attr:`version` rather than instead of it, because
        "1.3.1" and "installed" side by side would assert that 1.3.1 is what
        is installed — which the finder cannot tell us, and which is wrong on
        any station that pinned differently. Read from distribution metadata,
        so this still does not import the package.
        """
        if not self.installed:
            return None
        try:
            return distribution_version(self.name)
        except PackageNotFoundError:  # pragma: no cover - importable, no metadata
            return None


#: Pinned to the version the evidence is about: ADR-017's cascade timings and
#: the accuracy findings in :mod:`.refinement.batdetect2` were measured on 1.3.1.
PACKAGE_MODELS: tuple[PackageModel, ...] = (
    PackageModel(
        name="batdetect2",
        version="1.3.1",
        licence="CC-BY-NC-4.0",
        install_command="pip install batdetect2==1.3.1",
        url="https://github.com/macaodha/batdetect2",
        used_for=(
            "Bat-call proposals over stored ultrasonic evidence clips, in the "
            "ADR-045 refinement runner only. Never in the live pipeline."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class AssetStatus:
    asset: ModelAsset
    present: bool
    digest_matches: bool
    size_bytes: int | None
    actual_sha256: str | None

    @property
    def ok(self) -> bool:
        return self.present and self.digest_matches


def load_manifest(path: Path = DEFAULT_MANIFEST) -> list[ModelAsset]:
    assets: list[ModelAsset] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t")
        if len(parts) != 4:
            raise ValueError(f"malformed manifest line (expected 4 tab-separated fields): {line!r}")
        assets.append(ModelAsset(*(part.strip() for part in parts)))
    return assets


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def status(model_dir: Path = DEFAULT_MODEL_DIR, manifest: Path = DEFAULT_MANIFEST) -> list[AssetStatus]:
    results: list[AssetStatus] = []
    for asset in load_manifest(manifest):
        target = model_dir / asset.filename
        if not target.exists():
            results.append(AssetStatus(asset, False, False, None, None))
            continue
        actual = sha256_file(target)
        results.append(
            AssetStatus(
                asset,
                True,
                actual == asset.sha256,
                target.stat().st_size,
                actual,
            )
        )
    return results


def _maybe_extract(path: Path) -> None:
    """Some hosts serve a tar.gz wrapping a single model file."""
    with path.open("rb") as handle:
        if handle.read(2) != b"\x1f\x8b":
            return
    with tempfile.TemporaryDirectory() as scratch:
        with tarfile.open(path, "r:gz") as archive:
            archive.extractall(scratch, filter="data")
        candidates = [
            child
            for child in Path(scratch).rglob("*")
            if child.is_file() and child.suffix in (".tflite", ".txt", ".csv")
        ]
        if candidates:
            shutil.move(str(candidates[0]), str(path))


def fetch(
    model_dir: Path = DEFAULT_MODEL_DIR,
    manifest: Path = DEFAULT_MANIFEST,
    *,
    force: bool = False,
    timeout_s: float = 60.0,
) -> list[AssetStatus]:
    """Download and verify every manifest entry. Raises on any failure."""
    model_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    for asset in load_manifest(manifest):
        target = model_dir / asset.filename
        if target.exists() and not force:
            actual = sha256_file(target)
            if actual == asset.sha256:
                log.info("models.cached", filename=asset.filename)
                continue
            log.warning(
                "models.digest_mismatch_redownloading",
                filename=asset.filename,
                expected=asset.sha256[:12],
                actual=actual[:12],
            )

        partial = target.with_suffix(target.suffix + ".part")
        log.info("models.fetching", filename=asset.filename, licence=asset.licence)
        try:
            request = urllib.request.Request(
                asset.url, headers={"User-Agent": "open-observatory/models"}
            )
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                partial.write_bytes(response.read())
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            partial.unlink(missing_ok=True)
            failures.append(f"{asset.filename}: download failed ({exc})")
            continue

        _maybe_extract(partial)
        actual = sha256_file(partial)
        if actual != asset.sha256:
            partial.unlink(missing_ok=True)
            failures.append(
                f"{asset.filename}: checksum mismatch "
                f"(expected {asset.sha256}, got {actual})"
            )
            continue
        partial.replace(target)
        log.info("models.installed", filename=asset.filename, bytes=target.stat().st_size)

    if failures:
        raise RuntimeError("model acquisition failed:\n  " + "\n  ".join(failures))
    return status(model_dir, manifest)


def licence_summary(
    model_dir: Path = DEFAULT_MODEL_DIR, manifest: Path = DEFAULT_MANIFEST
) -> list[dict[str, object]]:
    """Licence and provenance for every model, for the UI to display.

    Two shapes, tagged by ``kind``, because the two acquisition routes support
    different claims. A ``file`` was downloaded and checked against a digest, so
    it can report ``verified``; a ``package`` was pip installed, so it can
    report only that its module is importable. Flattening them into one shape
    would mean either inventing a digest or dropping the one there is.
    """
    rows: list[dict[str, object]] = [
        {
            "kind": "file",
            "filename": entry.asset.filename,
            "licence": entry.asset.licence,
            "source_url": entry.asset.url,
            "expected_sha256": entry.asset.sha256,
            "installed": entry.present,
            "verified": entry.ok,
            "size_bytes": entry.size_bytes,
        }
        for entry in status(model_dir, manifest)
    ]
    rows.extend(
        {
            "kind": "package",
            "name": package.name,
            "version": package.version,
            "licence": package.licence,
            "source_url": package.url,
            "install_command": package.install_command,
            "used_for": package.used_for,
            "installed": package.installed,
            "installed_version": package.installed_version,
        }
        for package in PACKAGE_MODELS
    )
    return rows
