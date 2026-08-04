"""Checksummed model acquisition (ADR-006).

Model assets are not in the repository and are not downloaded implicitly. The
operator runs ``oo models fetch``, sees the licence of each asset, and the
digests are verified before anything is installed. A mismatch is a hard failure:
a silently-wrong classifier would produce confident nonsense that looks exactly
like a working system.
"""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
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
    """Licence and provenance for every installed asset, for the UI to display."""
    return [
        {
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
