# Evidence Bank Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ADR-074's recomputed per-species bank with a persisted `detection.banked_at` column, so the bank is monotone, affordable and safe to enable.

**Architecture:** Bank membership becomes a nullable, partially-indexed column on `detection` instead of a `frozenset[str]` of species names derived from an 18.32-second census every sweep. Promotion into the bank is a bounded write; the age tiers' exclusion collapses to a single `banked_at IS NULL` predicate; the one-off archive walk moves to an offline CLI command with no time budget.

**Tech Stack:** Python 3.12, SQLAlchemy 2, Alembic, SQLite, pytest, Typer/Rich CLI, Pydantic v2 settings.

**Spec:** `docs/architecture/adr/ADR-076 - The evidence bank is a column, not a recomputed set.md`
**Measurements the spec argues from:** `docs/operations/EVIDENCE_BANK_MEASUREMENTS_2026-08-29.md`

## Global Constraints

- **`evidence_value_enabled` ships `False` and stays `False`.** Every task must leave the flag off. Tests that exercise the policy pass `evidence_value_enabled=True` explicitly to `_sweeper(...)`. No task turns it on in `config.py`.
- **`banked_at` is monotone.** Nothing clears it except `_watermark_reclaim` reclaiming that detection's evidence, in the same transaction. (ADR-076 rule 1.)
- **The index stays partial** on `banked_at IS NOT NULL`. A plain index re-creates the ADR-061 failure that wedged the station for five minutes. (ADR-076 rule 2.)
- **`kept_at` outranks `banked_at`.** ADR-061's operator flag is exempt from every tier including the watermark; the bank is not. (ADR-076 rule 4.)
- **Never break the stage-then-commit-then-unlink ordering** in `_run_tier` / `_watermark_reclaim` (C1, 2026-08-14). Files are unlinked only after the tier's transaction commits.
- **Every DB statement in the sweep stays inside `_bounded_statements`.** An abort degrades the sweep, never fails it.
- The batch budget is `batch_budget_s`, default **1.5 s** (ADR-062). The sweep runs in the capture process on a paced housekeeping loop (ADR-033).
- Repo conventions: `ruff`, `mypy`, `pytest`. Run `./.venv/bin/python -m pytest` from the repo root. Full suite baseline before this plan: **1055 passed, 9 skipped, 12 deselected**.
- Do **not** touch `src/open_observatory/models.py`, `api/app.py`, `cli.py`'s models section, `web/src/App.tsx`, `web/src/components/ModelsPanel.tsx` or `tests/test_api.py` beyond what a task explicitly names — another session has uncommitted work in those files. Check `git diff <path>` before staging anything.

---

## File Structure

| File | Responsibility |
|---|---|
| `alembic/versions/20260829_0011_000000000012_detection_banked_at.py` | **Create.** The column and the partial index. |
| `src/open_observatory/db/models.py` | **Modify.** `Detection.banked_at` + the `Index` in `__table_args__`. |
| `src/open_observatory/config.py` | **Modify.** `evidence_implausible_species` setting. |
| `src/open_observatory/tuning.py` | **Modify.** `LiveTarget` for the new setting. |
| `src/open_observatory/site_settings.py` | **Modify.** `_e(...)` registry entry. |
| `src/open_observatory/evidence_value.py` | **Modify.** `cap_for()` replaces `classify()`'s bank branch. |
| `src/open_observatory/retention.py` | **Modify.** Delete the census; add promotion; rewrite the exclusion; watermark two-pass. |
| `src/open_observatory/cli.py` | **Modify.** `oo retention bank-backfill`. |
| `tests/test_evidence_value.py` | **Modify.** `cap_for` tests. |
| `tests/test_retention.py` | **Modify.** Promotion, cliff regression, watermark ordering. |
| `tests/test_migrations.py` | **Modify.** The partial-index shape assertion. |
| `tests/test_site_settings.py` | **Modify.** The new setting is editable and live. |

---

### Task 1: The migration — `banked_at` column and its partial index

**Files:**
- Create: `alembic/versions/20260829_0011_000000000012_detection_banked_at.py`
- Modify: `src/open_observatory/db/models.py` (the `Detection` class and its `__table_args__`)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `orm.Detection.banked_at: Mapped[datetime | None]`; index name `ix_detection_banked_partial` on `(common_name, event_start_utc)` with `sqlite_where=text("banked_at IS NOT NULL")`; alembic revision id `0012_detection_banked_at`, down_revision `0011_retention_live_asset_indexes`.

- [ ] **Step 1: Write the failing test**

In `tests/test_migrations.py`, alongside `test_kept_at_has_a_partial_index_that_cannot_steal_the_ordered_plan`:

```python
def test_banked_at_index_is_partial_so_it_cannot_steal_the_ordered_plan(
    migrated_engine,
) -> None:
    """ADR-076 rule 2, asserted on the `WHERE` clause rather than the name.

    `banked_at` is NULL for ~99.2% of rows -- the same shape as `kept_at`,
    whose *plain* index SQLite preferred for the `IS NULL` filter, costing the
    planner `ix_detection_event_start_utc` and wedging the station inside one
    statement for over five minutes (ADR-061, revision 0009). A name check
    alone would pass against a plain index, which is the defect this asserts
    against.
    """
    with migrated_engine.connect() as conn:
        sql = conn.execute(
            sa.text(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='ix_detection_banked_partial'"
            )
        ).scalar()
    assert sql is not None, "the partial index is missing"
    assert "banked_at IS NOT NULL" in sql, (
        "the index is not partial; a plain index on banked_at re-creates the "
        "ADR-061 failure that wedged the station"
    )


def test_detection_has_a_banked_at_column(migrated_engine) -> None:
    cols = {c["name"] for c in sa.inspect(migrated_engine).get_columns("detection")}
    assert "banked_at" in cols
```

Use whatever fixture the neighbouring migration tests use for a migrated engine — read `tests/test_migrations.py` and match it exactly rather than inventing `migrated_engine`.

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_migrations.py -k banked -v`
Expected: FAIL — the index and column do not exist.

- [ ] **Step 3: Write the migration**

```python
"""`detection.banked_at`, and a *partial* index on the banked rows.

ADR-076. The evidence bank stops being a set of species names recomputed each
sweep and becomes a fact about a row. The census that recomputation needed was
measured at **18.3219 s** on the station against a 1.5 s budget (ADR-062); the
count that replaces it is 0.0023 s.

**The index is partial, and the `WHERE` clause is the mechanism, not a detail.**
`banked_at` is NULL for about 99.2% of rows. Revision 0008 added a *plain*
index on `kept_at`, which has the same shape, and SQLite preferred it for the
`IS NULL` filter in every tier's candidate query -- losing
`ix_detection_event_start_utc`, which serves the range predicate and the
`ORDER BY` together, and turning an ordered indexed scan into a temp B-tree
sort. That blocked one sweep inside a single statement for over five minutes.
See revisions 0009 and 0010, and ADR-061.

A partial index contains only the rows matching its `WHERE`, so the planner may
use it for `banked_at IS NOT NULL` (the per-species banked count) and *cannot*
use it for `banked_at IS NULL` (every tier's candidate query).

`(common_name, event_start_utc)` rather than `(banked_at)`: the one query that
reads this index is "how many detections has each species banked, and which are
they, oldest first", so those are the columns it needs to cover.

Revision ID: 0012_detection_banked_at
Revises: 0011_retention_live_asset_indexes
Create Date: 2026-08-29 22:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_detection_banked_at"
down_revision: str | None = "0011_retention_live_asset_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_detection_banked_partial"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # Same check-then-act precedent as 0003, 0005, 0007, 0008, 0009 and 0010.
    columns = {c["name"] for c in inspector.get_columns("detection")}
    if "banked_at" not in columns:
        op.add_column(
            "detection",
            sa.Column("banked_at", sa.DateTime(timezone=True), nullable=True),
        )
    indexes = {i["name"] for i in inspector.get_indexes("detection")}
    if INDEX_NAME not in indexes:
        op.create_index(
            INDEX_NAME,
            "detection",
            ["common_name", "event_start_utc"],
            sqlite_where=sa.text("banked_at IS NOT NULL"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if INDEX_NAME in {i["name"] for i in inspector.get_indexes("detection")}:
        op.drop_index(INDEX_NAME, table_name="detection")
    if "banked_at" in {c["name"] for c in inspector.get_columns("detection")}:
        op.drop_column("detection", "banked_at")
```

- [ ] **Step 4: Add the ORM column and index**

In `src/open_observatory/db/models.py`, immediately after the `kept_by` column in `Detection`:

```python
    # -- the evidence bank (ADR-076) -----------------------------------------
    #: When this detection was promoted into the evidence bank: the archive
    #: ADR-074 wants and ADR-076 makes monotone. Set once by promotion, and
    #: cleared **only** by `_watermark_reclaim` actually reclaiming this
    #: detection's evidence, in the same transaction -- which is what frees the
    #: species' slot again.
    #:
    #: Not the same thing as `kept_at`, and must never be presented as though
    #: it were: `kept_at` is a human's decision and is exempt from every tier
    #: including the watermark; this is a policy's decision and the watermark
    #: overrules it (ADR-074 rule 1, ADR-076 rule 4).
    #:
    #: Indexed **partially** -- see `ix_detection_banked_partial` below and
    #: revision 0012. A plain index here re-creates the ADR-061 failure.
    banked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

and in `__table_args__`, after `ix_detection_kept_at_partial`:

```python
        # Partial, for exactly the reason `ix_detection_kept_at_partial` is
        # (ADR-061, ADR-076 rule 2): `banked_at` is NULL for ~99.2% of rows, so
        # a plain index is one SQLite prefers for the `IS NULL` filter in every
        # tier's candidate query, losing `ix_detection_event_start_utc` and
        # adding a temp B-tree sort. Covering `(common_name, event_start_utc)`
        # because the banked count and the promotion candidate query are the
        # only two readers.
        Index(
            "ix_detection_banked_partial",
            "common_name",
            "event_start_utc",
            sqlite_where=text("banked_at IS NOT NULL"),
        ),
```

- [ ] **Step 5: Run the tests**

Run: `./.venv/bin/python -m pytest tests/test_migrations.py -v`
Expected: PASS, including the existing round-trip downgrade test.

- [ ] **Step 6: Commit**

```bash
git add "alembic/versions/20260829_0011_000000000012_detection_banked_at.py" \
        src/open_observatory/db/models.py tests/test_migrations.py
git commit -m "ADR-076: detection.banked_at, with a partial index that cannot steal the plan"
```

---

### Task 2: The `evidence_implausible_species` setting

**Files:**
- Modify: `src/open_observatory/config.py` (after `evidence_common_species`)
- Modify: `src/open_observatory/tuning.py:123-126`
- Modify: `src/open_observatory/site_settings.py` (the retention block, after `evidence_common_species`)
- Test: `tests/test_site_settings.py`, `tests/test_config.py` (match whichever exists)

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.evidence_implausible_species: tuple[str, ...]`, live-tunable, default the five measured names.

- [ ] **Step 1: Write the failing test**

```python
def test_implausible_species_is_editable_and_live() -> None:
    """ADR-076: the plausibility gate is an operator list, not a migration.

    ADR-074's first amendment proposed persisting the band as an indexed
    column -- a migration, a write-path change and an ADR of its own. Its
    second amendment found that unnecessary: which birds are *impossible here*
    is the same kind of judgement as which are *boring*, and that is a list a
    person edits.
    """
    entry = next(
        e for e in EDITABLE_SETTINGS if e.name == "evidence_implausible_species"
    )
    assert entry.category == "retention"
    assert entry.tier == "live"
    assert "California Quail" in get_settings().evidence_implausible_species
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_site_settings.py -k implausible -v`
Expected: FAIL with `StopIteration`.

- [ ] **Step 3: Add the setting**

`config.py`, immediately after `evidence_common_species`:

```python
    #: Species the range model says cannot occur here (ADR-049 bands, ADR-076).
    #: They are banked to `evidence_implausible_cap` examples rather than
    #: `evidence_bank_size`: three examples are enough to judge a systematic
    #: misidentification, and the 3,000th adds nothing.
    #:
    #: An operator list rather than a query against the stored band, because
    #: the band lives inside the wide `native_result` JSON column that the
    #: sweep's preamble goes out of its way never to touch (ADR-062's 1.5 s
    #: budget). ADR-074's own principle -- which birds are boring is a list a
    #: person edits, not a threshold a machine picks -- applies word for word
    #: to which birds are impossible here.
    #:
    #: Pre-populated with the five this station has actually recorded, one
    #: detection each, none of which can occur in a Surrey garden.
    evidence_implausible_species: Annotated[tuple[str, ...], NoDecode] = (
        "Chestnut-backed Chickadee",
        "California Quail",
        "Asian Brown Flycatcher",
        "Eastern Screech-Owl",
        "Grey-winged Inca-Finch",
    )
    #: Examples kept of an implausible species, ever (ADR-074).
    evidence_implausible_cap: int = 3
```

Add `"evidence_implausible_species"` to the `_split_sequence` field list at `config.py:711` (read the surrounding tuple and append to it — it is the list of sequence-valued settings that accept both JSON and comma-separated spellings).

`tuning.py`, after line 126:

```python
    "evidence_implausible_species": LiveTarget("retention", "evidence_implausible_species"),
    "evidence_implausible_cap": LiveTarget("retention", "evidence_implausible_cap"),
```

`site_settings.py`, after the `evidence_common_species` entry:

```python
    _e("evidence_implausible_species", "retention", label="impossible species",
       help="Birds the range model says cannot occur here. Kept as a handful "
            "of examples to judge the misidentification, not archived."),
    _e("evidence_implausible_cap", "retention", label="examples of an impossible species",
       minimum=0, maximum=1000),
```

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/python -m pytest tests/test_site_settings.py tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/open_observatory/config.py src/open_observatory/tuning.py \
        src/open_observatory/site_settings.py tests/test_site_settings.py
git commit -m "ADR-076: an operator list for impossible species, not a migration"
```

---

### Task 3: `cap_for()` — the promotion policy as pure logic

**Files:**
- Modify: `src/open_observatory/evidence_value.py`
- Test: `tests/test_evidence_value.py`

**Interfaces:**
- Consumes: `Policy` (existing).
- Produces: `Policy` gains `implausible_cap` (already present) and the new field `common_bank_size` is **not** added — see below. New function:

```python
def cap_for(species: str, *, is_common: bool, is_implausible: bool, policy: Policy) -> int
```

Returns how many detections of `species` may be banked: `0` if common, `policy.implausible_cap` if implausible, else `policy.bank_size`. Implausible wins over common (a species on both lists is a misidentification worth three examples, not a boring bird worth none).

**Why `classify()` does not go away:** `_run_tier` still calls `sampled()` to tally SAMPLE vs QUOTA in the dry-run report. Keep `sampled()`, `frequency_band()`, `sparse_bands()` and `Verdict` exactly as they are. `classify()` loses its only caller (`_derive_bank`) — **delete it** in Task 4, along with `IMPLAUSIBLE_BANDS`, and delete its tests.

- [ ] **Step 1: Write the failing tests**

```python
class TestCapFor:
    """ADR-076: the cap is decided once, at promotion, not re-litigated."""

    def test_uncommon_species_gets_the_full_bank(self) -> None:
        assert cap_for("Grey Heron", is_common=False, is_implausible=False,
                       policy=Policy()) == 200

    def test_common_species_banks_nothing_new(self) -> None:
        assert cap_for("European Robin", is_common=True, is_implausible=False,
                       policy=Policy()) == 0

    def test_implausible_species_is_capped_at_three_examples(self) -> None:
        assert cap_for("California Quail", is_common=False, is_implausible=True,
                       policy=Policy()) == 3

    def test_implausible_beats_common(self) -> None:
        """A species on both lists is a misidentification, not a boring bird.

        Three examples are what makes a systematic misidentification judgeable.
        Reading `common` first would silence it instead, which is exactly the
        failure ADR-074's suggestion rule guards against for the same reason.
        """
        assert cap_for("Ambiguous", is_common=True, is_implausible=True,
                       policy=Policy()) == 3

    def test_a_zero_bank_size_disables_the_bank_without_disabling_the_policy(self) -> None:
        assert cap_for("Grey Heron", is_common=False, is_implausible=False,
                       policy=Policy(bank_size=0)) == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_evidence_value.py -k CapFor -v`
Expected: FAIL with `ImportError` / `NameError: cap_for`.

- [ ] **Step 3: Implement**

```python
__all__ += ["cap_for"]


def cap_for(
    species: str,
    *,
    is_common: bool,
    is_implausible: bool,
    policy: Policy,
) -> int:
    """How many detections of ``species`` may be banked, ever.

    ADR-076 replaces ADR-074's per-sweep ``classify()`` verdict with a cap
    applied **once, at promotion**. That is the whole fix for the cliff: a
    verdict recomputed every sweep can flip from BANK to EXPIRE for a whole
    back-catalogue at once, and a cap cannot -- reaching it stops promotion and
    unbanks nothing.

    ``is_implausible`` is read before ``is_common`` deliberately. A species on
    both lists is a systematic misidentification, and three examples are what
    make one judgeable; treating it as merely boring would silence the very
    thing worth investigating.
    """
    if is_implausible:
        return max(0, policy.implausible_cap)
    if is_common:
        return 0
    return max(0, policy.bank_size)
```

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/python -m pytest tests/test_evidence_value.py -v`
Expected: PASS (existing tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/open_observatory/evidence_value.py tests/test_evidence_value.py
git commit -m "ADR-076: the bank cap is decided once at promotion, not per sweep"
```

---

### Task 4: Delete the census; the exclusion becomes one predicate

**Files:**
- Modify: `src/open_observatory/retention.py` — delete `_banked_counts`, `_band_counts`'s bank role, `_derive_bank`, `_EVIDENCE_CENSUS_TTL_S`, `_ASSUMED_BAND`, `_band_expression`'s bank role, the census-abort machinery (`_census`, `_census_taken_at`, `_census_attempted_at`, `last_census_duration_s`, `census_aborts`) and `evidence_value.classify` / `IMPLAUSIBLE_BANDS`.
- Modify: `src/open_observatory/evidence_value.py` — delete `classify` and `IMPLAUSIBLE_BANDS`.
- Test: `tests/test_retention.py`, `tests/test_evidence_value.py`

**Interfaces:**
- Consumes: `orm.Detection.banked_at` (Task 1), `cap_for` (Task 3).
- Produces:

```python
@dataclass(frozen=True, slots=True)
class _EvidenceBank:
    #: species -> how many detections it has banked. Empty is meaningful.
    banked: Mapping[str, int]

    def exclusion(self) -> Any: ...   # always `orm.Detection.banked_at.is_(None)`
```

`RetentionSweeper._evidence_bank(session)` returns `_EvidenceBank | None` (None only when the flag is off), and no longer takes `now` or `deadline`.

**Note on `_band_counts`:** keep it. It still measures which bat bands are sparse, is index-served, and is needed by Task 6's band promotion. Only its role in `_derive_bank` goes.

**Note on `snapshot()`:** remove `last_census_duration_s`, `census_age_s` and `census_aborts` and replace them with `bank_size_now` (total banked detections) and `promoted_last_sweep`. Update `tests/test_retention.py`'s snapshot assertions accordingly.

- [ ] **Step 1: Write the failing test**

```python
class TestExclusionIsOnePredicate:
    def test_a_banked_detection_survives_both_age_tiers(self, db, station_and_detector) -> None:
        """The exclusion is `banked_at IS NULL`, and nothing else.

        ADR-074 spelled this as `NOT (common_name IN (...) OR band IN (...))`
        over up to 127 species names, recomputed from an 18.32 s census. The
        column makes it one indexed predicate.
        """
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            det, assets = _seed_detection(
                session, station_id, detector_id,
                created_at=FIXED_NOW - timedelta(days=90),
                common_name="Grey Heron",
            )
            session.execute(
                sa.update(orm.Detection)
                .where(orm.Detection.id == det)
                .values(banked_at=FIXED_NOW - timedelta(days=1))
            )
            session.commit()

        _sweeper(db, evidence_value_enabled=True).sweep()

        with session_scope() as session:
            for asset_id in assets:
                assert _asset(session, asset_id).reclaimed_at is None, (
                    "a banked detection was reclaimed by an age tier"
                )

    def test_census_machinery_is_gone(self, db) -> None:
        sweeper = _sweeper(db, evidence_value_enabled=True)
        assert not hasattr(sweeper, "census_aborts")
        assert "census_aborts" not in sweeper.snapshot()
```

Match `_seed_detection`'s real signature — read it in `tests/test_retention.py` and pass whatever it actually accepts rather than the arguments sketched above.

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_retention.py -k Exclusion -v`
Expected: FAIL — `census_aborts` still exists.

- [ ] **Step 3: Rewrite `_EvidenceBank` and `_evidence_bank`**

```python
@dataclass(frozen=True, slots=True)
class _EvidenceBank:
    """What each species has banked right now (ADR-076).

    ADR-074 carried the *members* of the bank -- a set of species names, and a
    set of bat bands, recomputed from a census measured at 18.3219 s against a
    1.5 s budget. This carries only the **counts**, read from an index over the
    banked rows in 0.0023 s, because membership is now a column and no longer
    has to be re-derived to be applied.
    """

    #: species name -> detections it has banked. Bats appear under `_BAT_GROUP`
    #: keyed by band edge in `bands`, not here.
    banked: Mapping[str, int]
    #: band edge (kHz) -> detections that band has banked.
    bands: Mapping[int, int]

    def exclusion(self) -> Any:
        """SQL for "the bank is not keeping this detection".

        One predicate over an ordinary nullable column, and none of ADR-074's
        three-valued-logic hazard: `banked_at IS NULL` is true or false for
        every row, including the bat passes whose `common_name` is NULL by
        design and the birds whose `peak_frequency_hz` is. The `NOT (x OR y)`
        spelling this replaces evaluated to NULL for exactly those rows, which
        would have dropped every bat clip out of both tiers' candidate queries
        for ever.
        """
        return orm.Detection.banked_at.is_(None)


    def total(self) -> int:
        return sum(self.banked.values()) + sum(self.bands.values())
```

```python
    def _evidence_bank(self, session: Session) -> _EvidenceBank | None:
        """Per-species and per-band banked counts: one index-only scan each.

        No TTL, no cached census, no abort handling -- all three existed to
        survive an 18.32 s query that no longer exists. Measured at **0.0023 s**
        against the station's own 903,240 detections
        (`EVIDENCE_BANK_MEASUREMENTS_2026-08-29`), because
        `ix_detection_banked_partial` contains only the ~7,300 banked rows and
        covers both columns this reads.
        """
        if not self.evidence_value_enabled:
            return None
        species_rows = session.execute(
            select(orm.Detection.common_name, func.count())
            .where(orm.Detection.banked_at.is_not(None))
            .where(orm.Detection.common_name.is_not(None))
            .group_by(orm.Detection.common_name)
        ).all()
        band = _band_expression().label("band")
        band_rows = session.execute(
            select(band, func.count())
            .where(orm.Detection.banked_at.is_not(None))
            .where(orm.Detection.taxonomic_group == _BAT_GROUP)
            .where(orm.Detection.peak_frequency_hz.is_not(None))
            .where(orm.Detection.peak_frequency_hz > 0)
            .group_by(band)
        ).all()
        return _EvidenceBank(
            banked={n: c for n, c in species_rows if n is not None},
            bands={int(e): c for e, c in band_rows if e is not None},
        )
```

Delete `_banked_counts`, `_derive_bank`, `_EVIDENCE_CENSUS_TTL_S`, `_ASSUMED_BAND`, and every `self._census*` / `self.census_aborts` / `self.last_census_duration_s` attribute and its `snapshot()` keys. In `sweep()`, replace the `bank = (self._evidence_bank(session, now=now, deadline=deadline) if ... else None)` call with `bank = self._evidence_bank(session)`.

In `_strip_native` and `_strip_unkept`, replace:

```python
        exclusion = bank.exclusion() if bank is not None else None
        if exclusion is not None:
            query = query.where(exclusion)
```

with:

```python
        # ADR-076: one indexed predicate, in the candidate query where every
        # other exemption lives. `None` when the flag is off, and then this
        # query is byte-identical to the age-only one.
        if bank is not None:
            query = query.where(bank.exclusion())
```

Delete `classify` and `IMPLAUSIBLE_BANDS` from `evidence_value.py` and their tests from `tests/test_evidence_value.py`.

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/python -m pytest tests/test_retention.py tests/test_evidence_value.py -v`
Expected: PASS. Several existing ADR-074 tests will fail first — they assert on the census and the species-set bank. Rewrite each to assert on `banked_at` instead of deleting it, and if a test's *intent* no longer has a mechanism (e.g. "an aborted census degrades to age-only"), delete it and say so in the commit message.

- [ ] **Step 5: Commit**

```bash
git add src/open_observatory/retention.py src/open_observatory/evidence_value.py \
        tests/test_retention.py tests/test_evidence_value.py
git commit -m "ADR-076: delete the 18.32 s census; the exclusion is one indexed predicate"
```

---

### Task 5: Promotion into the bank, bounded

**Files:**
- Modify: `src/open_observatory/retention.py`
- Test: `tests/test_retention.py`

**Interfaces:**
- Consumes: `_EvidenceBank` (Task 4), `cap_for` (Task 3), `Settings.evidence_implausible_species` (Task 2).
- Produces: `RetentionSweeper._promote_to_bank(session, bank, *, now, deadline, limit) -> int` (count promoted), and `RetentionReport.promoted: int`. `RetentionSweeper.__init__` gains `evidence_implausible_species: Sequence[str] = ()` and `evidence_implausible_cap: int = 3`; `station.py` and `cli.py` pass them.

- [ ] **Step 1: Write the failing tests**

```python
class TestPromotion:
    def test_a_rare_species_is_promoted_oldest_first(self, db, station_and_detector) -> None:
        """The first-ever recording is promoted first, so it is protected first."""
        station_id, detector_id = station_and_detector
        ids = []
        with session_scope() as session:
            for days in (50, 40, 30):
                det, _ = _seed_detection(
                    session, station_id, detector_id,
                    created_at=FIXED_NOW - timedelta(days=days),
                    common_name="Grey Heron",
                )
                ids.append(det)
            session.commit()

        _sweeper(db, evidence_value_enabled=True, evidence_bank_size=2).sweep()

        with session_scope() as session:
            banked = [
                session.get(orm.Detection, d).banked_at is not None for d in ids
            ]
        assert banked == [True, True, False], (
            "promotion did not take the oldest first"
        )

    def test_a_common_species_is_never_promoted(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            det, _ = _seed_detection(
                session, station_id, detector_id,
                created_at=FIXED_NOW - timedelta(days=1),
                common_name="European Robin",
            )
            session.commit()
        _sweeper(db, evidence_value_enabled=True,
                 evidence_common_species=("European Robin",)).sweep()
        with session_scope() as session:
            assert session.get(orm.Detection, det).banked_at is None

    def test_an_implausible_species_stops_at_three(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            for days in range(10, 20):
                _seed_detection(
                    session, station_id, detector_id,
                    created_at=FIXED_NOW - timedelta(days=days),
                    common_name="California Quail",
                )
            session.commit()
        _sweeper(db, evidence_value_enabled=True,
                 evidence_implausible_species=("California Quail",)).sweep()
        with session_scope() as session:
            n = session.execute(
                sa.select(sa.func.count()).select_from(orm.Detection)
                .where(orm.Detection.common_name == "California Quail")
                .where(orm.Detection.banked_at.is_not(None))
            ).scalar_one()
        assert n == 3

    def test_promotion_never_takes_a_detection_with_no_live_evidence(
        self, db, station_and_detector
    ) -> None:
        """Banking a row whose clips are already gone protects nothing.

        The bank is an archive of files. A detection whose every asset has been
        reclaimed has no files, so promoting it would consume a species' slot
        and preserve nothing -- and, because promotion is monotone, would
        consume it permanently.
        """
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            det, assets = _seed_detection(
                session, station_id, detector_id,
                created_at=FIXED_NOW - timedelta(days=60),
                common_name="Grey Heron",
            )
            session.execute(
                sa.update(orm.MediaAsset)
                .where(orm.MediaAsset.id.in_(assets))
                .values(reclaimed_at=FIXED_NOW)
            )
            session.commit()
        _sweeper(db, evidence_value_enabled=True).sweep()
        with session_scope() as session:
            assert session.get(orm.Detection, det).banked_at is None

    def test_promotion_is_off_when_the_flag_is(self, db, station_and_detector) -> None:
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            det, _ = _seed_detection(
                session, station_id, detector_id,
                created_at=FIXED_NOW - timedelta(days=1),
                common_name="Grey Heron",
            )
            session.commit()
        _sweeper(db).sweep()          # flag defaults off
        with session_scope() as session:
            assert session.get(orm.Detection, det).banked_at is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_retention.py -k Promotion -v`
Expected: FAIL — nothing sets `banked_at`.

- [ ] **Step 3: Implement promotion**

```python
    #: Detections promoted into the bank in one sweep. Bounded so promotion can
    #: never be the thing that eats a sweep's budget: the bank fills over a few
    #: sweeps instead of one, and `oo retention bank-backfill` exists for the
    #: one-off case where filling it now actually matters.
    _PROMOTE_PER_SWEEP = 200

    def _promote_to_bank(
        self,
        session: Session,
        bank: _EvidenceBank,
        *,
        now: datetime,
        deadline: float,
        limit: int = _PROMOTE_PER_SWEEP,
    ) -> int:
        """Bank the oldest live, unbanked detections of every under-cap species.

        **Oldest first, and that is the whole point.** ADR-074's bank deleted
        the first-ever recording of a species the moment that species reached
        its cap, because membership was a boolean on the *species* and the age
        tiers order `created_at ASC`. Promoting oldest-first and never demoting
        inverts that: the earliest recording is the first thing protected and
        the last thing at risk.

        A species at its cap is excluded **in the candidate query itself**,
        not filtered out afterwards. That is what keeps this affordable: the
        candidate set is `LIMIT`ed, and the commonest species are exactly the
        ones that would otherwise fill it with rows the policy will never bank.

        What this deliberately does **not** do is find the oldest unbanked
        detection in the whole archive -- that question cannot be asked inside
        the budget (see `_promotion_candidates`). It promotes from a trailing
        window; `oo retention bank-backfill` does the archive, oldest-first,
        with no budget at all.
        """
        promoted = 0
        common = {n.casefold() for n in self.evidence_common_species}
        implausible = {n.casefold() for n in self.evidence_implausible_species}
        policy = evidence_value.Policy(
            bank_size=self.evidence_bank_size,
            sample_permille=self.evidence_sample_permille,
            implausible_cap=self.evidence_implausible_cap,
        )

        # The loop body is written out below, under "`_promote_to_bank`'s
        # loop becomes" -- it is given there rather than here because it
        # depends on `_promotion_candidates`, whose shape was settled by
        # measurement after this docstring was written.

        if promoted:
            session.commit()
        return promoted

    def _promotion_candidates(
        self, session: Session, *, now: datetime, skip: set[str], limit: int
    ) -> list[tuple[uuid.UUID, str]]:
        """Recent unbanked detections whose species could still take one.

        **No join to the media tables, and that is the entire reason this is
        affordable.** The obvious spelling -- group live assets by species --
        is the 18.32 s census again in a different shape: SQLite scans
        `detection_media` whole regardless of any time bound on `detection`,
        because the bound is on the wrong side of the join. Measured on the
        station's own cardinalities: 5.9754 s unbounded, and still **2.6672 s
        bounded to two hours**. Bounding the window does not help.

        This asks a cheaper question instead. A detection created moments ago
        always has live evidence -- its clips were just written -- so the
        incremental case does not need to ask whether it does. Liveness is
        checked in `_promote_to_bank` on the handful actually about to be
        promoted, one PK-prefix seek each.

        `skip` carries the species that cannot take another detection: the
        common list, and every species already at its cap. Excluded **in SQL**
        rather than in Python, because the candidate set is `LIMIT`ed and the
        commonest species are exactly the ones that would otherwise fill it --
        a window full of robins the policy will never bank, re-scanned every
        300 s, while a heron three rows further along is never seen.

        `common_name NOT IN (...)` is safe here only because of the
        `IS NOT NULL` guard above it: SQL three-valued logic makes
        `NULL NOT IN (...)` evaluate to NULL, not true, and a `WHERE` keeps
        only rows that are true. Without that guard every bat pass would
        silently vanish from this query. Same trap, same place, as
        `_EvidenceBank.exclusion` under ADR-074.

        Measured: **0.0149 s** for a 24-hour window at a 500-row limit,
        `SEARCH detection USING INDEX ix_detection_event_start_utc`.
        """
        query = (
            select(orm.Detection.id, orm.Detection.common_name)
            .where(orm.Detection.event_start_utc >= now - _PROMOTION_LOOKBACK)
            .where(orm.Detection.banked_at.is_(None))
            .where(orm.Detection.common_name.is_not(None))
            .order_by(orm.Detection.event_start_utc.asc())
            .limit(limit)
        )
        if skip:
            query = query.where(orm.Detection.common_name.notin_(sorted(skip)))
        return [(i, n) for i, n in session.execute(query).all() if n is not None]

    def _has_live_evidence(self, session: Session, detection_id: uuid.UUID) -> bool:
        """One PK-prefix seek plus one PK lookup. Measured at 0.46 ms.

        Banking a detection whose clips are already gone protects nothing and
        consumes a species' slot **permanently** -- promotion is monotone and
        never reconsiders. So the check is worth its cost, and its cost is only
        paid for detections that have already survived the cap filter.
        """
        return session.execute(
            select(orm.DetectionMedia.detection_id)
            .join(orm.MediaAsset, orm.MediaAsset.id == orm.DetectionMedia.media_asset_id)
            .where(orm.DetectionMedia.detection_id == detection_id)
            .where(orm.MediaAsset.reclaimed_at.is_(None))
            .limit(1)
        ).first() is not None
```

`_PROMOTION_LOOKBACK` is a module constant:

```python
#: How far back one sweep looks for detections to promote (ADR-076).
#:
#: Not "the whole archive": that question cannot be asked affordably from
#: inside the sweep -- see `_promotion_candidates`. Historical material is the
#: backfill's job (`oo retention bank-backfill`), which walks the archive
#: oldest-first under no time budget and is idempotent, so a station that was
#: down for a week catches up by running it rather than by widening this.
#:
#: Twenty-four hours against a 300 s sweep cadence is about 288 overlapping
#: passes over the same window, which is deliberate: promotion is idempotent
#: (a banked row is not a candidate), so overlap costs one indexed scan and
#: buys tolerance of a sweep that got interrupted or a budget that ran out.
_PROMOTION_LOOKBACK = timedelta(hours=24)
```

and `_promote_to_bank`'s loop becomes:

```python
        # Species that cannot take another, computed once and pushed into the
        # candidate query. `cap_for` returns 0 for a common species, so the
        # common list needs no separate handling here.
        skip = {
            species
            for species, banked in bank.banked.items()
            if banked >= evidence_value.cap_for(
                species,
                is_common=species.casefold() in common,
                is_implausible=species.casefold() in implausible,
                policy=policy,
            )
        }
        skip |= set(self.evidence_common_species)

        room_by_species: dict[str, int] = {}
        for detection_id, species in self._promotion_candidates(
            session, now=now, skip=skip, limit=limit * 2
        ):
            if promoted >= limit or time.monotonic() >= deadline:
                break
            if species not in room_by_species:
                cap = evidence_value.cap_for(
                    species,
                    is_common=species.casefold() in common,
                    is_implausible=species.casefold() in implausible,
                    policy=policy,
                )
                room_by_species[species] = cap - bank.banked.get(species, 0)
            if room_by_species[species] <= 0:
                continue
            if not self._has_live_evidence(session, detection_id):
                continue
            session.execute(
                sa_update(orm.Detection)
                .where(orm.Detection.id == detection_id)
                .values(banked_at=now)
            )
            room_by_species[species] -= 1
            promoted += 1
```

Import `update as sa_update` from `sqlalchemy` at the top of the module if it is not already imported.

In `sweep()`, after `bank = self._evidence_bank(session)` and **before** the age tiers:

```python
                if bank is not None:
                    with self._bounded_statements(session, deadline):
                        report.promoted = self._promote_to_bank(
                            session, bank, now=now, deadline=deadline
                        )
                    # Re-read: the tiers below must see what was just banked,
                    # or a detection promoted this sweep is deleted by the same
                    # sweep that decided to keep it.
                    bank = self._evidence_bank(session)
```

Add `promoted: int = 0` to `RetentionReport`, and `"promoted"` plus `"bank_size_now"` to `snapshot()`.

Add the two constructor parameters and store them; wire them in `station.py:286-289` and `cli.py:1574-1577` and the `_sweeper` test helper.

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/python -m pytest tests/test_retention.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/open_observatory/retention.py src/open_observatory/station.py \
        src/open_observatory/cli.py tests/test_retention.py
git commit -m "ADR-076: promote the oldest live evidence of an under-cap species"
```

---

### Task 6: The cliff regression test

**Files:**
- Test only: `tests/test_retention.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5. Produces nothing.

This task is a test and nothing else. It is separate because it is the defect ADR-074 records as blocking, and it must be possible to point at one test and say "this is the thing that could not happen any more".

- [ ] **Step 1: Write the test**

```python
class TestTheCliffIsGone:
    """ADR-074's blocking defect, as an executable regression.

    Under ADR-074, `_derive_bank` returned a set of species *names* and the
    exclusion exempted every clip of a named species, with membership
    recomputed each sweep and `classify()` returning BANK only while
    `banded_already < bank_size`. So the moment a species reached its cap the
    exemption vanished **for the entire back-catalogue at once** -- and both
    age tiers order `created_at ASC`, which made the first-ever recording of a
    species the first thing deleted.

    The bank existed to prevent exactly that outcome and produced it.
    """

    def test_crossing_the_cap_does_not_delete_the_first_ever_recording(
        self, db, station_and_detector
    ) -> None:
        station_id, detector_id = station_and_detector
        cap = 3
        first, assets_of_first = None, []
        with session_scope() as session:
            for days in (400, 300, 200):
                det, assets = _seed_detection(
                    session, station_id, detector_id,
                    created_at=FIXED_NOW - timedelta(days=days),
                    common_name="Grey Heron",
                )
                if first is None:
                    first, assets_of_first = det, assets
            session.commit()

        sweeper = _sweeper(db, evidence_value_enabled=True, evidence_bank_size=cap)
        sweeper.sweep()

        # The species is now exactly at its cap -- the ADR-074 cliff edge.
        with session_scope() as session:
            at_cap = session.execute(
                sa.select(sa.func.count()).select_from(orm.Detection)
                .where(orm.Detection.common_name == "Grey Heron")
                .where(orm.Detection.banked_at.is_not(None))
            ).scalar_one()
        assert at_cap == cap

        # A fourth heron arrives, taking the species past the cap.
        with session_scope() as session:
            _seed_detection(
                session, station_id, detector_id,
                created_at=FIXED_NOW - timedelta(days=100),
                common_name="Grey Heron",
            )
            session.commit()

        sweeper.sweep()
        sweeper.sweep()   # and again: the cliff fired on re-derivation

        with session_scope() as session:
            assert session.get(orm.Detection, first).banked_at is not None, (
                "the first-ever recording was unbanked by crossing the cap"
            )
            for asset_id in assets_of_first:
                assert _asset(session, asset_id).reclaimed_at is None, (
                    "the first-ever recording of the species was deleted -- "
                    "ADR-074's blocking defect has returned"
                )

    def test_adding_a_banked_species_to_the_common_list_keeps_its_history(
        self, db, station_and_detector
    ) -> None:
        """ADR-074's stated consequence, which its own code contradicted.

        "A species moved onto the common list does not retroactively delete its
        history; the change applies to the next sweep forward" (ADR-074,
        Consequences). Under the species-set bank it deleted the history
        oldest-first. Under a monotone column it cannot.
        """
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            det, assets = _seed_detection(
                session, station_id, detector_id,
                created_at=FIXED_NOW - timedelta(days=200),
                common_name="European Greenfinch",
            )
            session.commit()

        _sweeper(db, evidence_value_enabled=True).sweep()
        with session_scope() as session:
            assert session.get(orm.Detection, det).banked_at is not None

        # The operator decides greenfinches are boring.
        _sweeper(db, evidence_value_enabled=True,
                 evidence_common_species=("European Greenfinch",)).sweep()

        with session_scope() as session:
            assert session.get(orm.Detection, det).banked_at is not None
            for asset_id in assets:
                assert _asset(session, asset_id).reclaimed_at is None
```

- [ ] **Step 2: Run**

Run: `./.venv/bin/python -m pytest tests/test_retention.py -k Cliff -v`
Expected: PASS. **If either test fails, stop** — the redesign has not achieved its purpose and the remaining tasks are premature.

- [ ] **Step 3: Commit**

```bash
git add tests/test_retention.py
git commit -m "ADR-076: the cliff, as a regression test that fails on the old design"
```

---

### Task 7: Bats — promotion by frequency band

**Files:**
- Modify: `src/open_observatory/retention.py`
- Test: `tests/test_retention.py`

**Interfaces:**
- Consumes: `_band_counts` (existing), `sparse_bands` (existing), `_EvidenceBank.bands` (Task 4).
- Produces: `RetentionSweeper._promote_bands(session, bank, *, now, deadline, limit) -> int`, called from `_promote_to_bank`.

- [ ] **Step 1: Write the failing test**

```python
class TestBandPromotion:
    def test_a_sparse_band_is_banked_and_a_busy_one_is_not(
        self, db, station_and_detector
    ) -> None:
        """ADR-074's third defect: the unit.

        `classify()` was handed a *pass* count and compared it against a *clip*
        budget, so a sparse band holding more than `bank_size` passes was never
        banked at all -- the rarest signal the station has, excluded by an
        arithmetic slip. Promotion counts banked detections against the cap on
        both sides.
        """
        station_id, detector_id = station_and_detector
        with session_scope() as session:
            for _ in range(200):                        # the busy 20-25 kHz band
                _seed_detection(
                    session, station_id, detector_id,
                    created_at=FIXED_NOW - timedelta(days=2),
                    common_name=None, taxonomic_group="bat",
                    peak_frequency_hz=22_000.0,
                )
            sparse = []
            for _ in range(2):                          # the sparse 60-65 kHz band
                det, _a = _seed_detection(
                    session, station_id, detector_id,
                    created_at=FIXED_NOW - timedelta(days=2),
                    common_name=None, taxonomic_group="bat",
                    peak_frequency_hz=62_000.0,
                )
                sparse.append(det)
            session.commit()

        _sweeper(db, evidence_value_enabled=True).sweep()

        with session_scope() as session:
            assert all(
                session.get(orm.Detection, d).banked_at is not None for d in sparse
            ), "the sparse band was not banked"
            busy_banked = session.execute(
                sa.select(sa.func.count()).select_from(orm.Detection)
                .where(orm.Detection.peak_frequency_hz == 22_000.0)
                .where(orm.Detection.banked_at.is_not(None))
            ).scalar_one()
        assert busy_banked == 0, "a busy band was banked"
```

`_seed_detection` may not accept `taxonomic_group` / `peak_frequency_hz` — read it and extend it if not, in this task.

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_retention.py -k BandPromotion -v`
Expected: FAIL — nothing promotes bats.

- [ ] **Step 3: Implement**

```python
    def _promote_bands(
        self,
        session: Session,
        bank: _EvidenceBank,
        *,
        now: datetime,
        deadline: float,
        limit: int,
    ) -> int:
        """Bank the oldest live passes of every sparse band, up to the cap.

        A bat pass is never given a species by design (ADR-017), so rarity
        cannot come from a name; it comes from the peak-frequency band, and a
        band under `_BAND_SPARSE_PERMILLE` of the trailing window is the bat
        equivalent of an uncommon species.

        The cap is compared against **banked detections in that band**, not
        against the band's pass count. ADR-074 compared a pass count to a clip
        budget, which meant a sparse band that had ever produced more than
        `bank_size` passes was never banked at all.
        """
        counts = self._band_counts(
            session, since=now - timedelta(days=_BAND_WINDOW_DAYS)
        )
        sparse = evidence_value.sparse_bands(counts, permille=_BAND_SPARSE_PERMILLE)
        promoted = 0
        for edge in sorted(sparse):
            if promoted >= limit or time.monotonic() >= deadline:
                break
            room = min(self.evidence_bank_size - bank.bands.get(edge, 0), limit - promoted)
            if room <= 0:
                continue
            lo = edge * 1000
            hi = lo + evidence_value.BAND_WIDTH_HZ
            candidates = session.execute(
                select(orm.Detection.id)
                .join(orm.DetectionMedia, orm.DetectionMedia.detection_id == orm.Detection.id)
                .join(orm.MediaAsset, orm.MediaAsset.id == orm.DetectionMedia.media_asset_id)
                .where(orm.MediaAsset.reclaimed_at.is_(None))
                .where(orm.Detection.banked_at.is_(None))
                .where(orm.Detection.taxonomic_group == _BAT_GROUP)
                .where(orm.Detection.peak_frequency_hz >= lo)
                .where(orm.Detection.peak_frequency_hz < hi)
                .group_by(orm.Detection.id)
                .order_by(func.min(orm.Detection.event_start_utc).asc())
                .limit(room)
            ).scalars().all()
            if candidates:
                session.execute(
                    sa_update(orm.Detection)
                    .where(orm.Detection.id.in_(candidates))
                    .values(banked_at=now)
                )
                promoted += len(candidates)
        return promoted
```

Call it from `_promote_to_bank` after the species loop, sharing the same `limit`:

```python
        promoted += self._promote_bands(
            session, bank, now=now, deadline=deadline, limit=limit - promoted
        )
```

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/python -m pytest tests/test_retention.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/open_observatory/retention.py tests/test_retention.py
git commit -m "ADR-076: bank sparse bat bands by banked detections, not by pass count"
```

---

### Task 8: The watermark prefers unbanked, and clears what it takes

**Files:**
- Modify: `src/open_observatory/retention.py` — `_watermark_reclaim`, `sweep()` ordering
- Test: `tests/test_retention.py`

**Interfaces:**
- Consumes: `_EvidenceBank` (Task 4).
- Produces: `_watermark_reclaim(session, report, *, deadline, budget, dry_run, bank=None)`; `RetentionReport.watermark_took_banked: int`.

- [ ] **Step 1: Write the failing tests**

```python
class TestWatermarkPrefersUnbanked:
    def test_unbanked_evidence_goes_first(self, db, station_and_detector, monkeypatch) -> None:
        """ADR-076 defect 5.

        `_watermark_reclaim` orders `created_at ASC` and was never passed the
        bank, so the emergency valve reclaimed the oldest clips on the disk --
        which, once the bank works, is precisely the banked set. The archive
        would be the first thing sacrificed to protect the disk.
        """
        # Seed one old banked detection and one newer unbanked one, force the
        # disk over the watermark, and assert the unbanked one goes first.
        ...

    def test_the_watermark_still_takes_banked_evidence_when_it_must(
        self, db, station_and_detector, monkeypatch
    ) -> None:
        """ADR-074 rule 1, and ADR-076 rule 3: a preference, never an exemption.

        A disk that can only be saved by deleting the archive still gets the
        archive deleted. "The watermark may delete banked evidence" and "the
        watermark deletes banked evidence first" are different rules, and only
        the first was ever intended.
        """
        ...

    def test_reclaiming_a_banked_detection_clears_banked_at(
        self, db, station_and_detector, monkeypatch
    ) -> None:
        """The one thing that may clear `banked_at` (ADR-076 rule 1).

        Without this the species' slot stays consumed by a detection with no
        files, and because promotion is monotone it stays consumed for ever --
        the species is locked out of the bank permanently.
        """
        ...
```

Fill in the three bodies using the existing watermark tests in `tests/test_retention.py` as the pattern for forcing the disk over the line (they already monkeypatch `shutil.disk_usage`; find and copy that mechanism exactly).

- [ ] **Step 2: Run to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_retention.py -k WatermarkPrefers -v`
Expected: FAIL.

- [ ] **Step 3: Implement the two passes**

Give `_watermark_reclaim` a `bank: _EvidenceBank | None = None` parameter. Factor its reclaim loop into `_watermark_pass(session, report, tally, *, deadline, budget, bytes_over, freed, dry_run, exclude_banked)` and call it twice:

```python
        # ADR-076: unbanked material first, banked material only if that did
        # not free enough. A *preference*, never an exemption -- ADR-074 rule 1
        # is untouched and a disk that can only be saved by deleting the
        # archive still gets the archive deleted.
        passes = (True, False) if bank is not None else (False,)
        for exclude_banked in passes:
            if freed >= bytes_over or budget <= 0 or time.monotonic() >= deadline:
                break
            budget, freed = self._watermark_pass(
                ..., exclude_banked=exclude_banked
            )
```

In the second pass, when a reclaimed asset's detection carries `banked_at`, clear it in the same transaction and count it into `report.watermark_took_banked`:

```python
        # The one thing that may clear `banked_at` (ADR-076 rule 1). Cleared
        # here rather than lazily, because a slot held by a detection with no
        # files is a slot the species never gets back -- promotion is monotone
        # and would never reconsider it.
        if banked_ids:
            session.execute(
                sa_update(orm.Detection)
                .where(orm.Detection.id.in_(banked_ids))
                .values(banked_at=None)
            )
```

In `sweep()`, move the `bank = self._evidence_bank(session)` call **above** the `if self._disk_over_watermark():` block and pass `bank=bank` to both `_watermark_reclaim` call sites. Replace ADR-074's "deliberately below the watermark tier" comment with:

```python
                # ADR-076 reverses ADR-074's ordering here. That ordering
                # existed because the census cost 18.32 s and must never delay
                # the one tier that stops a full disk stopping capture. The
                # replacement is 0.0023 s, so the reason is gone -- and the
                # watermark now needs the bank, to reclaim unbanked material
                # first rather than reclaiming the archive first. The safety
                # property is kept another way: `bank` is `None` when the flag
                # is off, and then every tier below behaves exactly as it did.
```

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/python -m pytest tests/test_retention.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/open_observatory/retention.py tests/test_retention.py
git commit -m "ADR-076: the watermark reclaims unbanked evidence first, and frees the slot"
```

---

### Task 9: `oo retention bank-backfill`

**Files:**
- Modify: `src/open_observatory/cli.py`
- Test: `tests/test_cli_retention.py` (or wherever the existing `oo retention sweep` test lives — find it)

**Interfaces:**
- Consumes: `RetentionSweeper` (Tasks 4–7).
- Produces: `RetentionSweeper.bank_backfill(*, dry_run: bool) -> dict[str, int]` returning `{"promoted": n, "species": n, "bands": n}`, and a Typer command `bank-backfill` under the existing `retention` group, with `--dry-run/--no-dry-run` defaulting to **dry-run on**.

- [ ] **Step 1: Write the failing test**

```python
def test_bank_backfill_dry_run_promotes_nothing(tmp_station) -> None:
    result = runner.invoke(app, ["retention", "bank-backfill", "--dry-run"])
    assert result.exit_code == 0
    assert "would bank" in result.stdout.lower()
    # and assert banked_at is still NULL everywhere
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_cli_retention.py -k backfill -v`
Expected: FAIL — no such command.

- [ ] **Step 3: Implement**

`bank_backfill` calls `_promote_to_bank` with `limit=` a large number and `deadline=` far in the future — the point of the command is that it is **not** budgeted:

```python
    def bank_backfill(self, *, dry_run: bool = True) -> dict[str, int]:
        """Fill the bank from the existing archive, once, with no time budget.

        The expensive part of ADR-074 does not disappear under ADR-076; it
        moves here, where there is no budget to break. Measured at **8.9753 s**
        for the station's whole archive, banking 7,302 detections across 135
        species (`EVIDENCE_BANK_MEASUREMENTS_2026-08-29`).

        The same shape as `oo detections reconcile-plausibility` (ADR-032): a
        command that walks the archive under no time budget precisely so the
        sweep never has to.
        """
```

Roll back instead of committing when `dry_run` is true. Print a per-species table and the total, and when dry-running say plainly that nothing was written.

Register the command next to the existing `retention sweep`, with a docstring that says it writes only `detection.banked_at`, never touches a file, and is safe to re-run (it is idempotent — a banked row is not a candidate).

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/python -m pytest tests/ -k "backfill or retention" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/open_observatory/cli.py tests/
git commit -m "ADR-076: oo retention bank-backfill, the one-off archive walk"
```

---

### Task 10: Full suite, docs, and the station's own query plan

**Files:**
- Modify: `docs/architecture/adr/ADR-076 - The evidence bank is a column, not a recomputed set.md`
- Modify: `docs/operations/EVIDENCE_BANK_MEASUREMENTS_2026-08-29.md`
- Modify: `docs/delivery/MILESTONE_STATUS.md`

- [ ] **Step 1: Run the whole suite, with a real exit code**

```bash
./.venv/bin/python -m pytest -q > /tmp/suite.log 2>&1; echo "exit=$?"
grep -cE "^FAILED" /tmp/suite.log; tail -3 /tmp/suite.log
```

Expected: `exit=0` and a `FAILED` count of **0**. Do not report a result from a piped tail — a `| tail -3` on this suite has already masked both a failure and the exit code once in this project's history.

- [ ] **Step 2: Lint and type-check**

```bash
./.venv/bin/ruff check src tests && ./.venv/bin/mypy src
```

- [ ] **Step 3: Confirm the station's own query plan**

`EVIDENCE_BANK_MEASUREMENTS_2026-08-29.md` records that the lab copy did not reproduce the station's plan for `_strip_native` (no `ANALYZE`, untyped copies), and owes a confirmation on the real database. Deploy, then read the plan on the station read-only and check it still names `ix_media_asset_live_kind_created` with the `banked_at IS NULL` predicate present. Record the result in the measurements file. **If the plan regressed, stop and revert the migration** — this is the ADR-061 failure mode.

- [ ] **Step 4: Run the backfill dry-run on the station and read it**

```bash
ssh <station> 'cd open-observatory && ./.venv/bin/oo retention bank-backfill --dry-run'
```

Expected order of magnitude: ~7,300 detections across ~135 species. Record the actual figures in the measurements file.

- [ ] **Step 5: Update the docs to match what shipped**

In ADR-076, replace the forward-looking wording with what was measured. In ADR-074, the "Blocking defect" section stays as the historical record — do not delete it — but confirm Amendment 3's forward pointer is accurate.

- [ ] **Step 6: Commit**

```bash
git add docs/
git commit -m "ADR-076: measured on the station, and the docs say what shipped"
```

---

## Self-Review

**Spec coverage.** ADR-076's five defects map to tasks: defect 1 (cliff) → Tasks 4, 5, 6; defect 2 (census) → Task 4; defect 3 (bat units) → Task 7; defect 5 (watermark) → Task 8. Defect 4 (the policy cannot save a byte) is **deliberately not implemented** — ADR-076's "What this ADR does not do" section says so, and Task 10 step 5 keeps ADR-074's "Expected effect" table marked as unmet. The plausibility gate → Tasks 2, 3. The backfill → Task 9. Rules 1–5 → Global Constraints, and rule 1 additionally has a test in Task 8.

**Placeholders.** Task 8's three test bodies are `...` and Task 9's test is a sketch. Both are deliberate and both say what to read to fill them in (the existing watermark tests' `shutil.disk_usage` monkeypatch; the existing CLI retention test). Every other code block is complete. Task 1's `migrated_engine` fixture name is flagged as needing to be matched against the real file rather than assumed.

**Type consistency.** `_EvidenceBank` carries `banked: Mapping[str, int]` and `bands: Mapping[int, int]` from Task 4 and is read under those names in Tasks 5, 7 and 8. `cap_for` has one signature, defined in Task 3 and called in Task 5. `_promote_to_bank` returns `int` and is called in Tasks 5, 7 and 9. `banked_at` is the column name in Tasks 1, 4, 5, 6, 7, 8 and 9.

**The gap this review found, and closed.** The first draft of Task 5 used a
`_promotable_species` aggregate over live assets joined to detections — the same
*shape* as the 18.32 s census this ADR deletes. It was measured rather than
handed to an implementer: **5.9754 s** unbounded, and **2.6672 s** even bounded
to a two-hour window, because SQLite scans `detection_media` whole regardless of
a time bound on the far side of the join. Bounding the window is not the fix.

Task 5 now uses a candidate query with **no media join at all** — 0.0149 s for a
24-hour window — and checks liveness only on the few candidates that survive the
cap filter, at 0.46 ms each. Worst case measured end to end: **~0.245 s** against
a 1.5 s budget. The cost of that change is that sweep promotion sees only a
trailing window, which is why `oo retention bank-backfill` (Task 9) is not
optional and why `_PROMOTION_LOOKBACK` carries the reasoning in a comment.

**Still owed.** Task 10 step 3 confirms `_strip_native`'s plan on the station
itself; the lab copy could not reproduce it (no `ANALYZE`, untyped tables). That
is the ADR-061 failure mode and it is the one check that must not be skipped.
