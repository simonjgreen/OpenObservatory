"""Favicon and app icons are actually served, not just present as files.

HANDOVER.md 6.3a: the web UI previously shipped no favicon at all, so every
page load requested `/favicon.ico` and got the SPA fallback or a 404 -- a
404 that "looks done" is worse than an honestly missing icon, because nobody
notices it. These tests exercise the real FastAPI app (the same
`StaticFiles` mount `create_app` uses in production) with a real HTTP
request through `TestClient`, rather than asserting the files merely exist
on disk.

`web/dist/` is a build artifact (gitignored) and is not assumed to exist in
a fresh checkout, so the fixture below reproduces exactly what `vite build`
does with `web/public/`: copy its contents verbatim into the served root.
That keeps this test hermetic (no `npm ci`/`npm run build` dependency) while
still exercising the real files this repository commits and the real
static-file-serving code path.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from open_observatory.api.app import create_app
from open_observatory.config import REPO_ROOT, Settings, set_settings

WEB_PUBLIC = REPO_ROOT / "web" / "public"


@pytest.fixture
def icon_client(tmp_path: Path) -> Iterator[TestClient]:
    dist = tmp_path / "dist"
    dist.mkdir()
    # This is exactly what Vite's public-dir copy does at build time: every
    # file under web/public/ lands verbatim at the root of web/dist/.
    for item in WEB_PUBLIC.iterdir():
        if item.is_file():
            shutil.copy2(item, dist / item.name)
    (dist / "index.html").write_text("<!doctype html><title>t</title>")

    configured = Settings(
        data_dir=tmp_path / "data",
        database_dsn=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite'}",
        source="synthetic",
        synthetic_scene="dawn-chorus",
        synthetic_sample_rate=48000,
        birdnet_enabled=False,
        metrics_enabled=True,
        web_dist=dist,
    )
    configured.ensure_directories()
    set_settings(configured)
    app = create_app(configured)
    with TestClient(app) as client:
        yield client


class TestIconFilesCommitted:
    """The files HANDOVER 6.3a asks for exist in web/public/ at all."""

    @pytest.mark.parametrize(
        "name",
        [
            "favicon.svg",
            "favicon.ico",
            "apple-touch-icon.png",
            "icon-192.png",
            "icon-512.png",
            "site.webmanifest",
            "ATTRIBUTION.md",
        ],
    )
    def test_file_exists(self, name: str) -> None:
        path = WEB_PUBLIC / name
        assert path.is_file(), f"missing {path}"
        assert path.stat().st_size > 0

    def test_svg_uses_the_real_upstream_path_data(self) -> None:
        """Guards against the exact failure mode CLAUDE.md warns about: an
        invented approximation standing in for a real glyph. This is the
        literal `d` attribute of `@mdi/svg` 7.4.47's `svg/bird.svg`, copied
        verbatim -- see web/public/ATTRIBUTION.md for the package version
        and SHA-256 checksums this was fetched and verified against."""
        svg = (WEB_PUBLIC / "favicon.svg").read_text()
        assert (
            "M23 11.5L19.95 10.37C19.69 9.22 19.04 8.56 19.04 8.56C17.4 6.92 "
            "14.75 6.92 13.11 8.56L11.63 10.04L5 3C4 7 5 11 7.45 14.22L2 19.5"
        ) in svg
        assert 'viewBox="0 0 24 24"' in svg

    def test_attribution_records_version_and_checksums(self) -> None:
        text = (WEB_PUBLIC / "ATTRIBUTION.md").read_text()
        assert "@mdi/svg" in text
        assert "7.4.47" in text
        assert "de92e5dc9ce46c392ab5c53aa7190b19f82b40cb48872a083f788c7e13e91fef" in text
        assert "70e0790bd69196c357bf47fe353941eb5e3614a46058a8622f3f4661048deec1" in text
        assert "Apache" in text


class TestIconsAreServed:
    """A real request through the real app, not just a file on disk."""

    @pytest.mark.parametrize(
        ("path", "content_type_prefix"),
        [
            ("/favicon.svg", "image/svg+xml"),
            ("/favicon.ico", "image/"),
            ("/apple-touch-icon.png", "image/png"),
            ("/icon-192.png", "image/png"),
            ("/icon-512.png", "image/png"),
            ("/site.webmanifest", None),
        ],
    )
    def test_icon_served_at_root(
        self, icon_client: TestClient, path: str, content_type_prefix: str | None
    ) -> None:
        response = icon_client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"
        assert len(response.content) > 0
        if content_type_prefix is not None:
            assert response.headers["content-type"].startswith(content_type_prefix)

    def test_index_html_references_the_icons(self, icon_client: TestClient) -> None:
        """The SPA document itself has to link them, or a browser never asks
        for these paths in the first place. Checked against the real
        web/index.html source (not the stub dist/index.html this fixture
        writes), since that is what `vite build` actually packages."""
        source = (REPO_ROOT / "web" / "index.html").read_text()
        assert 'rel="icon"' in source
        assert "favicon.svg" in source
        assert "favicon.ico" in source
        assert 'rel="apple-touch-icon"' in source
        assert 'rel="manifest"' in source

    def test_favicon_ico_request_is_not_a_404_or_spa_fallback(
        self, icon_client: TestClient
    ) -> None:
        """The exact bug this task closes: before this change, every page
        load's implicit `/favicon.ico` request hit the SPA catch-all or a
        bare 404 instead of a real icon."""
        response = icon_client.get("/favicon.ico")
        assert response.status_code == 200
        # A 404/SPA-fallback response here would be the app's HTML shell,
        # not an .ico payload -- distinguish by content-type and magic bytes.
        assert response.headers["content-type"] in ("image/vnd.microsoft.icon", "image/x-icon")
        assert response.content[:4] == b"\x00\x00\x01\x00"  # ICO file signature
