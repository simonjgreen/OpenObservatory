"""The model registry, and the two different ways a model gets here.

`models/manifest.tsv` describes files: a filename, a digest, a licence, a URL,
and `oo models fetch` verifies every one of them. BatDetect2 is not that. It
arrives as `pip install batdetect2==1.3.1` — code and weights together, under
one CC-BY-NC-4.0 licence, with no file this station ever checksums.

Until these tests existed it therefore appeared nowhere, and its licence
reached no API or UI surface, which is the disclosure ADR-006 and ADR-017 both
promise. The fix is not a fabricated manifest row: a pip install has no digest,
and writing one would be the same class of lie as a confident score on a
species that was never there. It is a second table, tagged with its own
`kind`, saying honestly how the thing is acquired.
"""

from __future__ import annotations

from pathlib import Path

from open_observatory import models


class TestThePackageTable:
    def test_batdetect2_is_listed_with_its_licence_and_install_command(self) -> None:
        entry = next(item for item in models.PACKAGE_MODELS if item.name == "batdetect2")
        assert entry.version == "1.3.1"
        assert entry.licence == "CC-BY-NC-4.0"
        assert entry.install_command == "pip install batdetect2==1.3.1"
        assert entry.url == "https://github.com/macaodha/batdetect2"
        assert "ADR-045" in entry.used_for

    def test_installed_is_false_when_the_module_is_absent(self, monkeypatch) -> None:
        """The normal state of a working station (ADR-017): absent, not broken."""
        monkeypatch.setattr(models, "find_spec", lambda name: None)
        entry = models.PACKAGE_MODELS[0]
        assert entry.installed is False

    def test_installed_is_true_when_the_module_is_present(self, monkeypatch) -> None:
        monkeypatch.setattr(models, "find_spec", lambda name: object())
        entry = models.PACKAGE_MODELS[0]
        assert entry.installed is True

    def test_the_version_actually_present_is_reported_separately(self, monkeypatch) -> None:
        """`version` is the pin; `installed_version` is the fact.

        A station running 1.2.0 must not be shown "1.3.1, installed" — that is
        a claim the finder cannot support, and it is the same overstatement
        this table exists to remove. The pin is what the ADR-017 timings and
        the licence were checked against; what is on disk is a separate matter.
        """
        monkeypatch.setattr(models, "find_spec", lambda name: object())
        monkeypatch.setattr(models, "distribution_version", lambda name: "1.2.0")
        assert models.PACKAGE_MODELS[0].installed_version == "1.2.0"

    def test_installed_version_is_none_when_nothing_is_installed(self, monkeypatch) -> None:
        monkeypatch.setattr(models, "find_spec", lambda name: None)
        assert models.PACKAGE_MODELS[0].installed_version is None

    def test_the_check_never_imports_the_package(self, monkeypatch) -> None:
        """torch and BatDetect2 are heavy and optional.

        `/api/v1/models` is a cheap listing endpoint. Importing a package that
        pulls in PyTorch to answer "is it installed?" would cost seconds and
        hundreds of megabytes on a Pi, so the answer comes from the finder.
        """
        asked: list[str] = []

        def spy(name: str):
            asked.append(name)
            return None

        monkeypatch.setattr(models, "find_spec", spy)
        answers = [entry.installed for entry in models.PACKAGE_MODELS]
        assert answers == [False] * len(models.PACKAGE_MODELS)
        assert asked == [entry.name for entry in models.PACKAGE_MODELS]


class TestLicenceSummary:
    def test_it_returns_both_kinds(self, tmp_path: Path) -> None:
        rows = models.licence_summary(model_dir=tmp_path)
        kinds = {row["kind"] for row in rows}
        assert kinds == {"file", "package"}

    def test_file_entries_keep_every_key_they_had(self, tmp_path: Path) -> None:
        """Existing callers read these by name; only `kind` is new."""
        rows = [row for row in models.licence_summary(model_dir=tmp_path) if row["kind"] == "file"]
        assert rows
        for row in rows:
            assert set(row) == {
                "kind",
                "filename",
                "licence",
                "source_url",
                "expected_sha256",
                "installed",
                "verified",
                "size_bytes",
            }
            # tmp_path holds no assets, so nothing is installed or verified.
            assert row["installed"] is False
            assert row["verified"] is False

    def test_a_package_entry_says_how_it_is_acquired_and_never_claims_a_digest(
        self, tmp_path: Path
    ) -> None:
        rows = [
            row for row in models.licence_summary(model_dir=tmp_path) if row["kind"] == "package"
        ]
        assert rows
        batdetect2 = next(row for row in rows if row["name"] == "batdetect2")
        assert batdetect2["licence"] == "CC-BY-NC-4.0"
        assert batdetect2["source_url"].startswith("http")
        assert batdetect2["install_command"] == "pip install batdetect2==1.3.1"
        assert batdetect2["used_for"]
        assert batdetect2["version"] == "1.3.1"
        assert isinstance(batdetect2["installed"], bool)
        # Absent on this machine or not, the row never passes the pin off as
        # the installed version.
        assert "installed_version" in batdetect2
        assert "expected_sha256" not in batdetect2
        assert "filename" not in batdetect2
