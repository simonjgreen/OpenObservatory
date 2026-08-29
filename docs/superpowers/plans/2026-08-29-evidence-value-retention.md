# Evidence Value Retention (ADR-074, part 1 of 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decide which evidence clips are worth keeping by *value* rather than
only by age, so the station stops spending 31% of its SSD on robins and
woodpigeons while losing herons to a 30-day timer.

**Architecture:** A new dependency-free module `evidence_value.py` holds every
selection decision as a pure function — the `slo.py` / `plausibility.py` /
`firmware_store.py` precedent. `retention.py` consumes it by adding exclusions
to each tier's *candidate query*, which is where `kept_at` exemption already
lives (`_strip_native`, `retention.py:708`). **Nothing in this plan goes near
the stage-then-commit-then-unlink ordering** — that sequencing took a measured
failure to get right (see `_stage_delete`'s docstring) and is not this plan's
business.

**Tech Stack:** Python 3.12, SQLAlchemy 2, pytest, SQLite.

**Spec:** `docs/architecture/adr/ADR-074 - Evidence kept by value.md`

## Scope: this is part 1 of 2

**Part 1 (this plan)** — the selection policy, its configuration, and the
retention integration. Delivers the disk saving and is independently testable.

**Part 2 (not yet written)** — the operator-facing UI: editing
`evidence_common_species` from the settings page, and the "you may wish to add
this to the uninteresting list" suggestion with its dismissal list. It depends
on part 1's settings existing and is a React + settings-catalogue task, not a
retention task.

## Global Constraints

- **Deletion is irreversible and this policy has never run.** The first
  execution against real data must be `--dry-run`, reporting per-category counts
  for a human to read. No task may make deletion the default path.
- **`kept_at` outranks everything** (ADR-061). A human-kept or human-reviewed
  detection is never deleted by this policy, whatever its species, band or bank
  state. Enforced by test.
- **Age tiers remain as the backstop** (ADR-062). Value decides what is *worth*
  keeping; the watermark still decides what the disk can *hold*. Value must
  never override the watermark.
- **The 1% sample must stay blind.** Selection is
  `sha256(detection_id)` only — never score, band, or species. If it ever
  consults those it stops being able to estimate anything.
- `evidence_value.py` is standard-library only: no SQLAlchemy, no FastAPI, no
  Pydantic, no numpy.
- Run tests with `.venv/bin/python -m pytest`, always with
  `--deselect tests/test_api.py::TestLiveChannels`.
- Ruff and mypy clean on every touched file.
- UTC internally.

## Git hazard, live at time of writing

A second process is editing `docs/` and has ~70 staged renames in the index.
**Never `git add -A`, `git add .`, `git add -u`, or `git commit -a`.** Add only
named paths, and before committing a file that process may also have touched,
run `git diff <path>` and confirm the working-tree diff contains only your own
lines — checking the index alone is not enough (that mistake was made once in
this session already, in commit 70a3445).

---

### Task 1: The selection decision, as pure logic

**Files:**
- Create: `src/open_observatory/evidence_value.py`
- Test: `tests/test_evidence_value.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Verdict` (str enum: `"bank"`, `"quota"`, `"sample"`, `"expire"`),
  `Policy` dataclass (`bank_size: int = 200`, `sample_permille: int = 10`,
  `implausible_cap: int = 3`), and
  `classify(*, banded_already: int, band: str, is_common: bool, detection_id: str, policy: Policy) -> Verdict`.
  Deliberately does NOT take `species` or `lifetime_clips`: `banded_already`
  and `is_common` already carry everything the decision uses, and a parameter
  a function ignores is a parameter a caller will one day expect it to honour.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_value.py
"""Which clips are worth keeping (ADR-074).

The disk is not full of interesting things. It is full of robins: European
Robin and Common Woodpigeon alone hold 120.2 GB, 31% of the SSD, while the 91
species with fewer than 50 clips hold 1.62 GB between them.
"""
from __future__ import annotations

from open_observatory import evidence_value as ev

POLICY = ev.Policy()
ID = "0123456789abcdef0123456789abcdef"


def test_an_uncommon_plausible_species_banks_until_the_bank_is_full() -> None:
    """The heron. 139 clips today, and every one is worth keeping."""
    v = ev.classify(
        banded_already=139,
        band="in_range", is_common=False, detection_id=ID, policy=POLICY,
    )
    assert v == ev.Verdict.BANK


def test_the_bank_is_self_limiting() -> None:
    """Past K the same species falls back to the quota.

    This is what bounds the whole policy: 143 species x 200 x 1.53 MB is a
    44 GB ceiling even if every species maxes out, instead of the 313 GB/year
    that keeping the tail forever would cost.
    """
    v = ev.classify(
        banded_already=200,
        band="in_range", is_common=False, detection_id=ID, policy=POLICY,
    )
    assert v == ev.Verdict.QUOTA


def test_a_common_species_never_banks_at_all() -> None:
    """Robin would fill its 200 in a day, so it goes straight to quota."""
    v = ev.classify(
        banded_already=0,
        band="in_range", is_common=True, detection_id=ID, policy=POLICY,
    )
    assert v == ev.Verdict.QUOTA


def test_an_implausible_rarity_is_capped_not_banked() -> None:
    """California Quail cannot occur in a Surrey garden.

    Rarity alone is the wrong axis: the rarest entries in this station's record
    are mostly BirdNET's mistakes. Three examples are enough to judge a
    systematic error; the 3,000th adds nothing.
    """
    under = ev.classify(
        banded_already=1,
        band="implausible", is_common=False, detection_id=ID, policy=POLICY,
    )
    assert under == ev.Verdict.BANK

    over = ev.classify(
        banded_already=3,
        band="implausible", is_common=False, detection_id=ID, policy=POLICY,
    )
    assert over == ev.Verdict.EXPIRE


def test_the_blind_sample_rescues_roughly_one_percent_of_the_expired() -> None:
    """Without it the archive cannot estimate a false-positive rate.

    A best-of collection is selected on the very variable you would want to
    measure, so it can never answer "how often is the detector wrong?".
    """
    kept = sum(
        1
        for i in range(10_000)
        if ev.classify(
            banded_already=0,
            band="in_range", is_common=True,
            detection_id=f"{i:032x}", policy=ev.Policy(sample_permille=10),
        )
        is ev.Verdict.SAMPLE
    )
    assert 70 <= kept <= 130, f"expected ~1% sampled, got {kept}/10000"


def test_the_sample_is_deterministic_and_blind() -> None:
    """Same id, same verdict, whatever the score or species says.

    Reproducible so an auditor can re-derive the sample; blind so it stays an
    unbiased estimator.
    """
    a = ev.classify(
        banded_already=0,
        band="in_range", is_common=True, detection_id=ID, policy=POLICY,
    )
    b = ev.classify(
        banded_already=0,
        band="uncommon", is_common=True, detection_id=ID, policy=POLICY,
    )
    assert a == b, "the sample must not depend on species or band"


def test_a_zero_permille_sample_disables_sampling_entirely() -> None:
    for i in range(500):
        v = ev.classify(
            banded_already=0,
            band="in_range", is_common=True,
            detection_id=f"{i:032x}", policy=ev.Policy(sample_permille=0),
        )
        assert v is not ev.Verdict.SAMPLE
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_evidence_value.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'open_observatory.evidence_value'`

- [ ] **Step 3: Write the implementation**

```python
# src/open_observatory/evidence_value.py
"""Which evidence clips are worth keeping, as pure logic (ADR-074).

PURE. No SQLAlchemy, no FastAPI, no Pydantic -- the rule ``slo.py``,
``plausibility.py`` and ``firmware_store.py`` all follow, so that the retention
sweep, the dry-run report and any future UI all get the identical verdict from
the identical function.

Retention today asks only how old a clip is. That is why 234,761 live clips
occupy 388.8 GB, of which European Robin and Common Woodpigeon alone are
120.2 GB -- 31% of the SSD -- while the 91 species with fewer than 50 clips hold
1.62 GB between them. The disk is not full of interesting things.

**Rarity alone is the wrong axis.** The rarest entries in this station's record
are mostly wrong: California Quail, Asian Brown Flycatcher, Chestnut-backed
Chickadee, none of which can occur in a Surrey garden. A naive rarity bias would
preferentially archive BirdNET's mistakes. So rarity is crossed with the
plausibility band ADR-049 already computes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

__all__ = ["Verdict", "Policy", "classify", "sampled"]


class Verdict(str, Enum):
    #: Keep indefinitely, exempt from age expiry. Bounded by ``bank_size``.
    BANK = "bank"
    #: Keep under the daily best/median/worst quota, subject to the age tiers.
    QUOTA = "quota"
    #: Kept only because the blind sample drew it. Subject to the age tiers.
    SAMPLE = "sample"
    #: Nothing keeps this; the age tiers may reclaim it.
    EXPIRE = "expire"


@dataclass(frozen=True, slots=True)
class Policy:
    #: Clips a species may bank before falling back to the quota. The ceiling
    #: this puts on the whole archive is what makes the policy bounded by
    #: construction rather than by a high-water mark: 143 species x 200 x
    #: 1.53 MB is about 44 GB even if every species maxes out.
    bank_size: int = 200
    #: Blind sample rate in parts per thousand. 10 = 1%.
    sample_permille: int = 10
    #: Examples kept of a species the range model says cannot be here. Three is
    #: enough to judge a systematic misidentification.
    implausible_cap: int = 3


#: Bands ADR-049 computes that mean "the range model says not here".
IMPLAUSIBLE_BANDS = frozenset({"implausible", "out_of_range"})


def sampled(detection_id: str, permille: int) -> bool:
    """Deterministic, blind 1-in-N draw.

    Keyed on the detection id and NOTHING else. Not on score, not on band, not
    on species -- the moment it consults any of those it stops being an
    unbiased estimator and becomes another best-of pile, and the archive loses
    its only means of answering "how often is the detector wrong?".

    Deterministic so the sample is reproducible: an auditor can re-derive
    exactly which rows were drawn, months later, without stored state.
    """
    if permille <= 0:
        return False
    digest = hashlib.sha256(detection_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % 1000 < permille


def classify(
    *,
    banded_already: int,
    band: str,
    is_common: bool,
    detection_id: str,
    policy: Policy,
) -> Verdict:
    """One clip's verdict.

    ``is_common`` comes from the operator's own list, not from a threshold.
    Which birds are boring is a matter of taste and place: a purely volumetric
    rule would have swept up Spotted Flycatcher (397 detections, a declining
    species) alongside the woodpigeons.
    """
    if band in IMPLAUSIBLE_BANDS:
        # Kept for review, not for the record. Capped hard.
        return Verdict.BANK if banded_already < policy.implausible_cap else Verdict.EXPIRE

    if not is_common and banded_already < policy.bank_size:
        return Verdict.BANK

    if sampled(detection_id, policy.sample_permille):
        return Verdict.SAMPLE

    return Verdict.QUOTA
```

- [ ] **Step 4: Run it and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_evidence_value.py -v`
Expected: 7 passed.

- [ ] **Step 5: Lint, type-check, commit**

```bash
.venv/bin/python -m ruff check src/open_observatory/evidence_value.py tests/test_evidence_value.py
.venv/bin/python -m ruff format src/open_observatory/evidence_value.py tests/test_evidence_value.py
.venv/bin/python -m mypy src/open_observatory/evidence_value.py
git add src/open_observatory/evidence_value.py tests/test_evidence_value.py
git commit -m "ADR-074: the evidence-value decision, as pure logic"
```

---

### Task 2: Bat rarity is a frequency band, not a species

**Files:**
- Modify: `src/open_observatory/evidence_value.py`
- Test: `tests/test_evidence_value.py`

**Interfaces:**
- Consumes: `Policy` from Task 1.
- Produces: `BAND_WIDTH_HZ = 5000`,
  `frequency_band(peak_hz: float | None) -> int | None`, and
  `sparse_bands(counts: Mapping[int, int], *, permille: int = 10) -> frozenset[int]`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_evidence_value.py
def test_frequency_bands_are_five_kilohertz_wide() -> None:
    assert ev.frequency_band(22_000) == 20
    assert ev.frequency_band(63_400) == 60
    assert ev.frequency_band(None) is None


def test_the_stations_own_bat_distribution_marks_the_right_bands_sparse() -> None:
    """Measured 2026-08-29 over 66,485 passes.

    20-25 kHz holds 36,180; 60-65 kHz holds four. That band is the bat
    equivalent of the heron and a flat 1% sample would leave it an expected
    0.04 passes -- discarding the rarest signal the station has.
    """
    counts = {
        15: 7040, 20: 36180, 25: 768, 30: 8655, 35: 7603,
        40: 1336, 45: 3328, 50: 1464, 55: 107, 60: 4,
    }
    sparse = ev.sparse_bands(counts, permille=10)
    assert 60 in sparse
    assert 55 in sparse
    assert 20 not in sparse
    assert 30 not in sparse


def test_a_band_that_stops_being_sparse_stops_being_kept_whole() -> None:
    """Self-limiting, like the species bank."""
    counts = {20: 1000, 55: 900}
    assert 55 not in ev.sparse_bands(counts, permille=10)


def test_no_passes_at_all_yields_no_sparse_bands_rather_than_all_of_them() -> None:
    assert ev.sparse_bands({}, permille=10) == frozenset()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_evidence_value.py -k band -v`
Expected: FAIL — no attribute `frequency_band`

- [ ] **Step 3: Write the implementation**

```python
# append to src/open_observatory/evidence_value.py
from collections.abc import Mapping

__all__ += ["BAND_WIDTH_HZ", "frequency_band", "sparse_bands"]

#: Bat passes are never given a species, by design, so rarity cannot come from a
#: name. It comes from peak frequency, and the distribution is as skewed as the
#: birds': measured over 66,485 passes on 2026-08-29, 20-25 kHz held 36,180 and
#: 60-65 kHz held four.
BAND_WIDTH_HZ = 5000


def frequency_band(peak_hz: float | None) -> int | None:
    """The 5 kHz band a pass falls in, named by its lower edge in kHz."""
    if peak_hz is None or peak_hz <= 0:
        return None
    return int(peak_hz // BAND_WIDTH_HZ) * (BAND_WIDTH_HZ // 1000)


def sparse_bands(counts: Mapping[int, int], *, permille: int = 10) -> frozenset[int]:
    """Bands holding less than ``permille``/1000 of the passes: keep every one.

    Self-limiting in the same way the species bank is -- a band that stops being
    sparse stops being kept whole -- so this cannot grow without bound.
    """
    total = sum(counts.values())
    if total <= 0:
        return frozenset()
    return frozenset(b for b, n in counts.items() if n * 1000 < total * permille)
```

- [ ] **Step 4: Run and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_evidence_value.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
.venv/bin/python -m ruff check src/open_observatory/evidence_value.py tests/test_evidence_value.py
.venv/bin/python -m ruff format src/open_observatory/evidence_value.py tests/test_evidence_value.py
.venv/bin/python -m mypy src/open_observatory/evidence_value.py
git add src/open_observatory/evidence_value.py tests/test_evidence_value.py
git commit -m "ADR-074: bat rarity is a frequency band, and sparse bands are kept whole"
```

---

### Task 3: Configuration

**Files:**
- Modify: `src/open_observatory/config.py`
- Modify: `src/open_observatory/site_settings.py`
- Test: `tests/test_site_settings.py` (existing; it FAILS if a new `Settings`
  field is added without being classified — that is the audit, and it is why
  this task exists as its own step)

**Interfaces:**
- Produces settings: `evidence_value_enabled: bool = False`,
  `evidence_common_species: tuple[str, ...]`, `evidence_bank_size: int = 200`,
  `evidence_sample_permille: int = 10`.

- [ ] **Step 1: Add the fields to `Settings`**

Follow the existing `pause_presets` pattern at `config.py:204`
(`Annotated[tuple[str, ...], NoDecode]`):

```python
    #: ADR-074. Value-based evidence retention. **Off by default**: this policy
    #: deletes clips, it has never run against real data, and its first run must
    #: be a dry-run a human reads.
    evidence_value_enabled: bool = False
    #: Species whose clips go straight to the daily quota, skipping the bank.
    #: An operator list, not a computed threshold -- which birds are boring is a
    #: matter of taste and place. Pre-populated with the six species that are
    #: 86% of this station's bird evidence; a purely volumetric rule would also
    #: have swept up Spotted Flycatcher, which is declining.
    evidence_common_species: Annotated[tuple[str, ...], NoDecode] = (
        "Common Woodpigeon",
        "European Robin",
        "Eurasian Jackdaw",
        "Eurasian Blue Tit",
        "Rook",
        "Collared Dove",
    )
    #: Clips a species may bank before falling back to the quota (ADR-074).
    evidence_bank_size: int = 200
    #: Blind sample rate, parts per thousand. 10 = 1%.
    evidence_sample_permille: int = 10
```

- [ ] **Step 2: Run the audit test and watch it fail**

Run: `.venv/bin/python -m pytest tests/test_site_settings.py -v`
Expected: FAIL — the four new fields are named in neither `EDITABLE_SETTINGS`
nor `NON_EDITABLE`.

- [ ] **Step 3: Classify them in `site_settings.py`**

All four are `tier="live"` — retention reads them fresh on each sweep, so no
restart is needed. Add entries beside the other retention settings, following
the `_e(...)` form used at `site_settings.py:806` for `pause_presets`:

```python
    _e("evidence_value_enabled", "retention", label="value-based retention",
       help="Keep clips by how interesting they are, not only by age (ADR-074). "
            "Off until a dry-run has been read."),
    _e("evidence_common_species", "retention", label="common species",
       help="Species whose clips go straight to the daily quota. The birds you "
            "do not need a thousand recordings of."),
    _e("evidence_bank_size", "retention", label="clips banked per species",
       minimum=0, maximum=10000),
    _e("evidence_sample_permille", "retention", label="blind sample (per 1000)",
       minimum=0, maximum=1000,
       help="Kept at random regardless of score, so the archive can still "
            "estimate how often the detector is wrong."),
```

- [ ] **Step 4: Run the audit test and watch it pass**

Run: `.venv/bin/python -m pytest tests/test_site_settings.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
.venv/bin/python -m ruff check src/open_observatory/config.py src/open_observatory/site_settings.py
.venv/bin/python -m ruff format src/open_observatory/config.py src/open_observatory/site_settings.py
.venv/bin/python -m mypy src/open_observatory/config.py src/open_observatory/site_settings.py
git add src/open_observatory/config.py src/open_observatory/site_settings.py
git commit -m "ADR-074: settings for value-based retention, off by default"
```

---

### Task 4: Count what each species and band has banked

Selection needs two facts the pure module cannot know: how many clips a species
has already banked, and how many passes each frequency band holds.

**Files:**
- Modify: `src/open_observatory/retention.py`
- Test: `tests/test_evidence_value_queries.py`

**Interfaces:**
- Consumes: `orm.Detection`, `orm.MediaAsset`, `orm.DetectionMedia`.
- Produces, on `RetentionSweeper`:
  `_banked_counts(session) -> dict[str, int]` keyed by species label, and
  `_band_counts(session, *, since: datetime) -> dict[int, int]`.

- [ ] **Step 1: Write the failing test**

Put these in `tests/test_retention.py` itself rather than a new file: it
already has the fixtures (`db`, `station_and_detector`), the seeding helper
`_seed_detection(...)` and the `_sweeper(...)` builder these need, and a second
file would duplicate all three.

```python
# append to tests/test_retention.py

def test_banked_counts_are_per_species_and_ignore_reclaimed_assets(
    db, station_and_detector, tmp_path
) -> None:
    """A reclaimed clip no longer occupies a bank slot.

    Otherwise a species that banked its 200, had them reclaimed by the
    watermark tier, and then reappeared could never bank again -- the bank
    would remember clips that no longer exist.
    """
    station_id, detector_id = station_and_detector
    with session_scope() as session:
        for _ in range(3):
            _seed_detection(
                session, station_id=station_id, detector_id=detector_id,
                clip_dir=db.clip_dir, age_days=1.0, common_name="Grey Heron",
            )
        for _ in range(2):
            _seed_detection(
                session, station_id=station_id, detector_id=detector_id,
                clip_dir=db.clip_dir, age_days=1.0, common_name="European Robin",
            )
        session.commit()

    sweeper = _sweeper(db)
    with session_scope() as session:
        counts = sweeper._banked_counts(session)

    assert counts["Grey Heron"] == 3
    assert counts["European Robin"] == 2

    # Reclaim one heron clip; the bank must shrink to match reality.
    with session_scope() as session:
        asset = session.execute(
            select(orm.MediaAsset)
            .join(orm.DetectionMedia, orm.DetectionMedia.media_asset_id == orm.MediaAsset.id)
            .join(orm.Detection, orm.Detection.id == orm.DetectionMedia.detection_id)
            .where(orm.Detection.common_name == "Grey Heron")
            .limit(1)
        ).scalar_one()
        asset.reclaimed_at = FIXED_NOW
        session.commit()

    with session_scope() as session:
        assert sweeper._banked_counts(session)["Grey Heron"] == 2


def test_band_counts_bucket_bat_passes_by_five_kilohertz(
    db, station_and_detector, tmp_path
) -> None:
    """Bats have no species, so the bank is keyed on peak frequency instead."""
    station_id, detector_id = station_and_detector
    with session_scope() as session:
        for hz in (21_000, 22_500, 24_900, 61_000):
            _seed_detection(
                session, station_id=station_id, detector_id=detector_id,
                clip_dir=db.clip_dir, age_days=1.0,
                taxonomic_group="bat", common_name=None,
                peak_frequency_hz=hz,
            )
        session.commit()

    sweeper = _sweeper(db)
    with session_scope() as session:
        counts = sweeper._band_counts(session, since=FIXED_NOW - timedelta(days=90))

    assert counts[20] == 3, "21.0, 22.5 and 24.9 kHz all fall in the 20-25 band"
    assert counts[60] == 1
```

**If `_seed_detection` does not accept `peak_frequency_hz`,** add that parameter
to the helper (defaulting to `None`) rather than writing a second seeding
function — the helper is the repo's established way to build a detection and a
parallel one would drift from it.

- [ ] **Step 2: Implement the two count queries**

Both must be single indexed queries, not per-row loops: this runs inside the
sweep's 1.5 s budget (`retention_batch_budget_s`), and ADR-062 exists because an
unbounded query there cost 2.978 s and forced ALSA device restarts.

- [ ] **Step 3: Verify the budget**

Run the retention tests and confirm no sweep exceeds its preamble budget:
`.venv/bin/python -m pytest tests/test_retention.py -v`

- [ ] **Step 4: Commit**

```bash
git add src/open_observatory/retention.py tests/test_evidence_value_queries.py
git commit -m "ADR-074: count what each species and frequency band has banked"
```

---

### Task 5: Exempt banked clips from the age tiers

**Files:**
- Modify: `src/open_observatory/retention.py` — `_strip_native` (~line 682) and
  `_strip_unkept`
- Test: `tests/test_retention.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 4.
- Produces: an additional exclusion on each age tier's candidate query.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_retention.py
def test_a_banked_heron_survives_the_native_tier(...):
    """The clip is 30 days old and would age out. It must not.

    This is the whole point of ADR-074: the heron currently dies on a timer
    while the robins fill the disk.
    """


def test_a_common_species_clip_still_ages_out_normally(...):
    """Robin is on the operator's common list and banks nothing."""


def test_a_kept_detection_survives_regardless_of_species_or_band(...):
    """ADR-061 outranks ADR-074. A human said keep; that is the end of it."""


def test_the_watermark_tier_still_reclaims_banked_clips(...):
    """Value decides what is worth keeping; the watermark decides what the disk
    can hold. Value must never override the watermark, or a full disk stops
    capture -- and capture outranks evidence."""
```

- [ ] **Step 2: Add the exclusion to the candidate queries**

The exemption goes where `kept_at`'s already does, in the same `.where(...)`
chain — `_strip_native` at `retention.py:708` has
`.where(orm.Detection.kept_at.is_(None))`. Add the banked-set exclusion beside
it. **Do not touch `_run_tier`, `_stage_delete`, or the commit ordering.**

- [ ] **Step 3: Run and verify**

Run: `.venv/bin/python -m pytest tests/test_retention.py -v`
Expected: all pass, including the four new tests.

- [ ] **Step 4: Commit**

```bash
git add src/open_observatory/retention.py tests/test_retention.py
git commit -m "ADR-074: banked clips are exempt from the age tiers, never from the watermark"
```

---

### Task 6: A dry-run a human can read before anything is deleted

**Files:**
- Modify: `src/open_observatory/retention.py` — `RetentionReport`
- Modify: `src/open_observatory/cli.py` — the retention command
- Test: `tests/test_retention.py`

**Interfaces:**
- Produces: `RetentionReport.value_counts: dict[str, int]` and
  `value_bytes: dict[str, int]`, keyed by `Verdict` value.

- [ ] **Step 1: Write the failing test**

```python
def test_a_dry_run_reports_what_each_verdict_would_cost(...):
    """Deletion is irreversible and this policy has never run.

    The first execution against real data must say, per category, how many
    clips and how many bytes it would remove -- for a human to read BEFORE
    anything is unlinked.
    """
```

- [ ] **Step 2: Populate the per-verdict tallies during staging**

- [ ] **Step 3: Print them in the CLI's dry-run output**

The summary must name, for each verdict, the count and the bytes, and must state
plainly that nothing was deleted.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q --deselect tests/test_api.py::TestLiveChannels`

- [ ] **Step 5: Commit**

```bash
git add src/open_observatory/retention.py src/open_observatory/cli.py tests/test_retention.py
git commit -m "ADR-074: a dry-run that reports what each verdict would cost"
```

---

## Rollout, which is not a task

**Do not enable this on the station as part of implementing it.**
`evidence_value_enabled` ships `False`. The sequence afterwards is:

1. Deploy with the flag off. Nothing changes.
2. Run the dry-run against the live database and **read it**, per category.
3. Only if the numbers look right — roughly 389 GB falling toward the mid-tens
   of GB, with the heron and the 60 kHz bat passes among the banked — turn the
   flag on.

The expected steady state is ~28 GB now and ≤70 GB at the ceiling, bounded by
construction rather than by a high-water mark. If the dry-run says otherwise,
the policy is wrong and the flag stays off.
