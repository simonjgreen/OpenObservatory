"""Repository hygiene: two documented rules, enforced here as ratchets.

`pytest` is the only gate this repository actually runs — there is no
`.pre-commit-config.yaml` and no `.github/workflows/` — so a rule that is
written down in an ADR and checked nowhere is a rule that does not exist.
These two are checked here.

Both are **ratchets**, not absolutes, because neither rule can be made
retroactively true today:

* ADR-047, "the repository ships no site": site state that predates the ADR is
  still committed in two agent working documents under `docs/`. They are named
  explicitly in `SITE_LEAK_ALLOWLIST` rather than papered over.
* ADR-027, "the existing colour tokens … continue to be the source of truth for
  colour": ~700 lines of pre-ADR component CSS were deliberately never migrated
  (the ADR says so), and four surfaces written *after* the ADR hard-coded
  colours anyway (the ADR's own 2026-08-29 review note says so).

**Every allowlist in this file may only ever shrink.** Adding an entry is not a
fix — it is a decision to publish the thing ADR-047 says must not be published,
or to grow the debt ADR-027 says must not grow. Entries come out as the
underlying files are cleaned up; they never go in.

Neither test needs the network, the station, or a database. Both read only
tracked files on disk.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# This file is exempt from its own scan: it has to spell out the strings it
# forbids in order to look for them. Nothing else is exempt by path.
SELF = "tests/test_repo_hygiene.py"


# --------------------------------------------------------------------------
# ADR-047: the repository ships no site
# --------------------------------------------------------------------------

#: (rule name, pattern). ADR-047 names the categories: "coordinates, place
#: names, LAN addresses, hostnames, account names, filesystem homes". These
#: three are the ones with a mechanical shape; coordinates and place names are
#: not reliably greppable and are left to review.
SITE_LEAK_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # The development station's LAN address. Deliberately the exact address and
    # not "any RFC1918 literal": 192.168.4.1 is the ESP32's own SoftAP portal
    # gateway, which is universal firmware behaviour, not site state.
    ("station LAN address", re.compile(r"192\.168\.1\.195")),
    # The operator's account name.
    ("operator username", re.compile(r"\bsimon\b", re.IGNORECASE)),
    # Any absolute home-directory path, whoever the user is. A committed
    # `/home/<user>/…` path is a deploy root, which is site state by definition.
    ("home-directory path", re.compile(r"/home/[A-Za-z0-9._-]+/")),
)

#: Known, pre-existing site leaks in tracked files, as (path, matched text).
#:
#: MAY ONLY SHRINK. Each entry is a thing ADR-047 says must not be in the
#: repository, still in the repository. Remove the leak, then remove the entry.
#: Never add one — a new leak is what this test exists to stop.
#: Empty, and it reached empty the day it was written: the two agent working notes
#: it briefly held (a `curl` against the development station, and the operator's
#: account name as an example `detection.kept_by`) were fixed on 2026-08-29 rather
#: than exempted. Keep it that way -- an entry here publishes what ADR-047 says
#: must not be published.
SITE_LEAK_ALLOWLIST: frozenset[tuple[str, str]] = frozenset()


@lru_cache(maxsize=1)
def _tracked_text_files() -> tuple[tuple[str, str], ...]:
    """Every tracked file that decodes as UTF-8 text, as (repo-relative path, content).

    Binaries are skipped: they cannot carry a `path:line` report, and a NUL byte
    or a decode failure is a good enough test for "not source or prose".
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    out: list[tuple[str, str]] = []
    for name in listing.split(b"\0"):
        if not name:
            continue
        path = name.decode("utf-8", "surrogateescape")
        blob = REPO_ROOT / path
        if not blob.is_file():  # a deleted-but-still-staged path
            continue
        data = blob.read_bytes()
        if b"\0" in data:
            continue
        try:
            out.append((path, data.decode("utf-8")))
        except UnicodeDecodeError:
            continue
    return tuple(out)


def _scan_for_site_leaks() -> list[tuple[str, int, str, str]]:
    """Return (path, line number, rule name, matched text) for every hit."""
    hits: list[tuple[str, int, str, str]] = []
    for path, content in _tracked_text_files():
        if path == SELF:
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            for rule, pattern in SITE_LEAK_RULES:
                for match in pattern.finditer(line):
                    hits.append((path, lineno, rule, match.group(0)))
    return hits


def test_site_leak_rules_actually_match() -> None:
    """Guard against a vacuous pass: prove each pattern still fires."""
    samples = {
        "station LAN address": "curl -s http://192.168.1.195:8080/api/v1/health",
        "operator username": 'kept_by = "simon"',
        "home-directory path": '"path": "/home/anyuser/open-observatory/models/x.wav"',
    }
    for rule, pattern in SITE_LEAK_RULES:
        assert pattern.search(samples[rule]), f"{rule} pattern no longer matches its own example"
    # And that they do not fire on the ADR-approved placeholders.
    clean = "curl -s http://<station-host>:8080/api/v1/health  # <deploy-root>/models/x.wav"
    for rule, pattern in SITE_LEAK_RULES:
        assert not pattern.search(clean), f"{rule} pattern fires on an ADR-047 placeholder"


def test_no_site_state_in_tracked_files() -> None:
    """ADR-047: nothing true of exactly one installation is repository content.

    Site state committed here becomes a permanent public disclosure the moment
    the repository is published, and — worse — a clone that inherits it runs
    against somebody else's site.
    """
    scanned = _tracked_text_files()
    assert len(scanned) > 100, f"only {len(scanned)} tracked text files scanned; `git ls-files` failed?"

    unexpected = [
        hit for hit in _scan_for_site_leaks() if (hit[0], hit[3].lower()) not in SITE_LEAK_ALLOWLIST
    ]
    report = "\n".join(
        f"  {path}:{lineno}  [{rule}]  {matched}" for path, lineno, rule, matched in unexpected
    )
    assert not unexpected, (
        f"{len(unexpected)} site-state leak(s) in tracked files (ADR-047):\n{report}\n"
        "Use <station-host>, <deploy-root>, RFC 5737/RFC 2606 examples, or a test input. "
        "Do not add these to SITE_LEAK_ALLOWLIST — that list may only shrink."
    )


# --------------------------------------------------------------------------
# ADR-027: the colour tokens are the source of truth for colour
# --------------------------------------------------------------------------

STYLESHEET = REPO_ROOT / "web" / "src" / "styles.css"

#: A CSS hex colour: 3, 4, 6 or 8 digits, not run together with an identifier.
#: An id selector made only of hex letters (`#faded`) would be a false positive;
#: there is none today (`#root` is the only id selector in the file).
HEX_COLOUR = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9A-Za-z_-])")

#: Every hex colour literal that appeared outside a `:root` block in
#: `web/src/styles.css` when this ratchet was set, on 2026-08-29. 36 in total:
#: 68 occurrences of 36 distinct colours.
#:
#: MAY ONLY SHRINK. ADR-027 names "the existing colour tokens" as the source of
#: truth for colour; each literal below is a place that is not using them. The
#: test asserts a *subset*, so deleting a literal from the stylesheet passes
#: without touching this list — but tidy the entry away when you do, or the
#: ratchet stops measuring anything.
#:
#: Nothing may be added. A new surface that needs a colour takes a token, or
#: adds one to `:root`.
COLOUR_LITERAL_ALLOWLIST: frozenset[str] = frozenset(
    {
        # ---- the ~700 lines of component CSS that predate ADR-027 ----
        # ADR-027 explicitly scoped the migration of these out: spectrogram,
        # suggestions list, event log, history chart, drawer. 32 literals.
        "#05060a",
        "#06070b",
        "#0a0c11",
        "#0b0d13",
        "#0e1118",
        "#0f1219",
        "#10131b",
        "#12141b",
        "#12151d",
        "#14171f",
        "#161a23",
        "#171b24",
        "#191d26",
        "#1b2130",
        "#1d2a3d",
        "#262c39",
        "#2f7d4f",
        "#333b4b",
        "#39414f",
        "#3f6ea8",
        "#414a5e",
        "#5ce08a",
        "#7f1d1d",
        "#b7c2d8",
        "#cfe4ff",
        "#ef4444",
        "#f87171",
        "#f8b4b4",
        "#fbbf24",
        "#fca5a5",
        "#fee2e2",
        "#fff",
        # ---- written after ADR-027, in breach of it ----
        # The four the ADR's own 2026-08-29 review note names: the login gate,
        # the settings panel, the first-run banners and the firmware upload.
        # These are the entries most obviously due to be removed: each already
        # has a token sitting next to it.
        "#d4a017",  # 5 uses, beside --warn
        "#f5d78a",  # 1 use, beside --warn
        "#d0454c",  # 1 use, beside --danger
        "#2a2a2a",  # 2 uses, as a literal fallback in var(--line, #2a2a2a)
    }
)


def _blank_comments(css: str) -> str:
    """Replace `/* … */` with spaces, keeping every newline so line numbers hold."""
    return re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), css, flags=re.S)


def _root_block_spans(css: str) -> list[tuple[int, int]]:
    """Character spans of every rule whose selector mentions `:root`, brace-matched."""
    spans: list[tuple[int, int]] = []
    for opener in re.finditer(r"[^{}]*:root[^{}]*\{", css):
        depth, index = 0, opener.end() - 1
        while index < len(css):
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
                if depth == 0:
                    spans.append((opener.start(), index + 1))
                    break
            index += 1
    return spans


def _colour_literals_outside_root(source: str) -> list[tuple[int, str]]:
    """(line number, lowercased hex) for each colour literal outside the token layer."""
    css = _blank_comments(source)
    spans = _root_block_spans(css)
    assert spans, "no `:root` block found in styles.css — the parser, not the stylesheet, is wrong"
    inside = [css[start:end] for start, end in spans]
    assert any("--bg:" in block for block in inside), "`:root` located but holds no colour tokens"

    found: list[tuple[int, str]] = []
    for match in HEX_COLOUR.finditer(css):
        if any(start <= match.start() < end for start, end in spans):
            continue
        found.append((css.count("\n", 0, match.start()) + 1, match.group(0).lower()))
    return found


def test_colour_extractor_actually_matches() -> None:
    """Guard against a vacuous pass: the extractor must see outside and not inside."""
    sample = "\n".join(
        [
            ":root {",
            "  --bg: #08090d;",  # inside the token layer: not a violation
            "  --warn: #fbbf24;",
            "}",
            "html, body, #root { height: 100%; }",  # an id selector, not a colour
            "/* a comment mentioning #abcdef is not a declaration */",
            ".panel { background: #123456; color: #fff; }",
        ]
    )
    assert _colour_literals_outside_root(sample) == [(7, "#123456"), (7, "#fff")]


def test_no_new_hardcoded_colours_in_stylesheet() -> None:
    """ADR-027: colour comes from the tokens in `:root`, not from new literals.

    The full constraint is not enforceable — the pre-ADR component CSS was never
    migrated and four later surfaces broke it wholesale. What *is* enforceable,
    and what this asserts, is that the set of hard-coded colours may not grow.
    """
    found = _colour_literals_outside_root(STYLESHEET.read_text(encoding="utf-8"))
    unexpected = [
        (lineno, hexcolour) for lineno, hexcolour in found if hexcolour not in COLOUR_LITERAL_ALLOWLIST
    ]
    report = "\n".join(f"  web/src/styles.css:{lineno}  {hexcolour}" for lineno, hexcolour in unexpected)
    assert not unexpected, (
        f"{len(unexpected)} hard-coded colour(s) outside `:root` that ADR-027 does not already "
        f"account for:\n{report}\n"
        "Use an existing colour token, or add one to `:root`. COLOUR_LITERAL_ALLOWLIST may only shrink."
    )
