# Keep-flag retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unbounded computed exemplar rule with an operator-set `kept` flag, so the retention sweep stops costing 3 s of GIL-held event-loop time every 300 s and starts actually deleting.

**Architecture:** Two mutable columns on `detection` (`kept_at`, `kept_by`) replace a 2.978 s Python-side set computation. Every retention tier gains one indexed SQL clause. A migration backfills first-of-species before the computation is deleted, so nothing currently protected becomes deletable. Operator surfaces (API, CLI, UI) follow in a second commit.

**Tech Stack:** Python 3.12, SQLAlchemy 2, Alembic, FastAPI, Pydantic v2, pytest, React + TypeScript + Vite, vitest.

**Spec:** [[2026-08-14-keep-flag-retention-design]]

## Global Constraints

- `kept` means keep forever, until a human removes the flag. Age, the 90-day expiry and disk pressure must never clear it.
- All four tiers (`_strip_native`, `_strip_unkept`, `_strip_expired`, `_watermark_reclaim`) must exempt `kept_at IS NOT NULL`.
- `held` ([[ADR-043 - Taxon correction|ADR-043]]) keeps its own distinct meaning and existing behaviour. Do not merge the two concepts. A detection may be both.
- No CLI command may call `console.print_json` — `tests/test_cli_json_output.py` asserts this by scanning `cli.py`. Use `emit_json`.
- Everything test-first. Watch each test fail before implementing.
- Run `.venv/bin/python -m pytest` — the system `python3` is 3.14 and is not the project interpreter.
- Deselect `tests/test_api.py::TestLiveChannels` when running the full suite; three of its cases hang.
- Baseline before this work: 900 passed, 9 skipped, 12 deselected. `ruff check .` clean. `mypy src` 21 errors in 11 files (pre-existing; do not add to it).

---

### Task 1: The columns, the migration, and the backfill

**Files:**
- Modify: `src/open_observatory/db/models.py` (class `Detection`, around line 148)
- Create: `alembic/versions/20260814_0007_000000000008_detection_kept.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `orm.Detection.kept_at: Mapped[datetime | None]`, `orm.Detection.kept_by: Mapped[str | None]`, index `ix_detection_kept_at`, alembic revision `0008_detection_kept` with `down_revision = "0007_capture_pause"`.

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_migrations.py`:

```python
def test_kept_columns_exist_at_head(migrated_session) -> None:
    """ADR-061: retention filters on these in SQL, so they must be real columns."""
    from sqlalchemy import inspect

    columns = {c["name"] for c in inspect(migrated_session.bind).get_columns("detection")}
    assert "kept_at" in columns
    assert "kept_by" in columns


def test_kept_at_is_indexed(migrated_session) -> None:
    """Every tier's candidate query filters on kept_at; an unindexed filter
    would reintroduce the scan this change exists to remove."""
    from sqlalchemy import inspect

    indexes = {i["name"] for i in inspect(migrated_session.bind).get_indexes("detection")}
    assert "ix_detection_kept_at" in indexes
```

Read the top of `tests/test_migrations.py` first and reuse whatever fixture it already
has for a migrated database; if the fixture is named differently from
`migrated_session`, use the existing name rather than adding one.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_migrations.py -q -k kept`
Expected: FAIL — `assert 'kept_at' in columns`.

- [ ] **Step 3: Add the columns to the model**

In `src/open_observatory/db/models.py`, class `Detection`, after the taxonomy columns:

```python
    #: ADR-061. Set by a human who wants this recording kept; cleared only by a
    #: human. Every retention tier exempts a row with this set, including the
    #: 90-day expiry and the disk watermark -- "keep" that a sweep can overrule
    #: is not a keep. Indexed because all four candidate queries filter on it.
    kept_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    #: Who kept it. "exemplar-backfill" for the rows migrated from the computed
    #: first-of-species rule this replaced.
    kept_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
```

- [ ] **Step 4: Write the migration**

Create `alembic/versions/20260814_0007_000000000008_detection_kept.py`:

```python
"""Add `detection.kept_at` / `detection.kept_by`, and backfill first-of-species.

ADR-061. Replaces `RetentionSweeper._exemplar_detection_ids`, an unbounded
2.978 s query run before any deadline check, with two columns and an indexed
SQL clause.

The backfill matters: without it, every recording the computed rule currently
protects becomes deletable on the next sweep, and the native tier has a full
batch of candidates waiting. First-of-species only -- a first-ever record
cannot be recreated, a better recording may come along.

Revision ID: 0008_detection_kept
Revises: 0007_capture_pause
Create Date: 2026-08-14 09:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_detection_kept"
down_revision: str | None = "0007_capture_pause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("detection")}
    # Same precedent as revisions 0003, 0005 and 0007: a station that ran the
    # new code before the migration already has these from `create_all()`.
    if "kept_at" not in existing:
        op.add_column("detection", sa.Column("kept_at", sa.DateTime(timezone=True), nullable=True))
    if "kept_by" not in existing:
        op.add_column("detection", sa.Column("kept_by", sa.String(length=120), nullable=True))
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("detection")}
    if "ix_detection_kept_at" not in indexes:
        op.create_index("ix_detection_kept_at", "detection", ["kept_at"])

    # Backfill: the earliest surviving detection per species key, matching the
    # key rule of the computed exemplar it replaces -- canonical_taxon_id, else
    # common_name, else taxonomic_group. Only detections that still have
    # un-reclaimed evidence: there is nothing to protect for one that does not.
    op.execute(
        sa.text(
            """
            UPDATE detection
               SET kept_at = :now, kept_by = 'exemplar-backfill'
             WHERE id IN (
                   SELECT d.id FROM detection d
                     JOIN detection_media dm ON dm.detection_id = d.id
                     JOIN media_asset ma ON ma.id = dm.media_asset_id
                    WHERE ma.reclaimed_at IS NULL
                    GROUP BY COALESCE(d.canonical_taxon_id, d.common_name,
                                      d.taxonomic_group, 'unknown')
                   HAVING d.event_start_utc = MIN(d.event_start_utc)
             )
            """
        ).bindparams(now=sa.func.now())
    )


def downgrade() -> None:
    op.drop_index("ix_detection_kept_at", table_name="detection")
    op.drop_column("detection", "kept_by")
    op.drop_column("detection", "kept_at")
```

If `bindparams(now=sa.func.now())` does not bind cleanly on SQLite, replace it
with a Python-side timestamp: compute `datetime.now(UTC).isoformat()` and pass
that string instead. Do not silently skip the timestamp — `kept_at` is what every
tier filters on, so a NULL there means the backfill protected nothing.

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_migrations.py -q`
Expected: PASS, including any existing round-trip/downgrade test.

- [ ] **Step 6: Write the backfill test**

Add to `tests/test_retention.py`:

```python
def test_backfill_keeps_the_first_of_each_species_and_nothing_else(migrated_session) -> None:
    """ADR-061: first-ever is irreplaceable; 'best' is not, and is not backfilled."""
    # Build: two robins (one earlier), one wren, each with live evidence.
    # Assert exactly the earlier robin and the wren carry kept_by ==
    # "exemplar-backfill", and the later robin does not -- even if it has the
    # higher score, which is what the old 'best' rule would have kept.
```

Write this against the fixtures `tests/test_retention.py` already uses to build
detections with media; read the file first and follow its existing builders
rather than inventing new ones.

- [ ] **Step 7: Run it, watch it fail, implement until it passes**

Run: `.venv/bin/python -m pytest tests/test_retention.py -q -k backfill`

- [ ] **Step 8: Commit**

```bash
git add src/open_observatory/db/models.py alembic/versions/ tests/
git commit -m "Add detection.kept_at/kept_by and backfill first-of-species (ADR-061)"
```

---

### Task 2: Delete the exemplar computation; every tier honours `kept`

**Files:**
- Modify: `src/open_observatory/retention.py` — delete `_exemplar_detection_ids` (lines ~470-536) and its call (~line 225); rename `_strip_non_exemplar` to `_strip_unkept`; add the clause to all four tiers
- Test: `tests/test_retention.py`

**Interfaces:**
- Consumes: `orm.Detection.kept_at` from Task 1.
- Produces: `RetentionSweeper._strip_unkept(...)` — same signature as the old `_strip_non_exemplar` minus the `exemplar_ids` parameter. `RetentionReport.exemplar_detections` is replaced by `kept_detections: int`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_kept_detection_survives_every_tier(sweeper, session) -> None:
    """Age, the 90-day expiry and the watermark must all leave it alone."""
    kept = make_detection_with_media(session, age_days=400, kept=True)
    sweeper.sweep()
    assert media_for(session, kept) != [], "a kept recording was deleted by age"


def test_unkeeping_makes_it_deletable_again(sweeper, session) -> None:
    """Only a human clears the flag -- but when they do, normal rules resume."""
    det = make_detection_with_media(session, age_days=400, kept=True)
    det.kept_at = None
    session.commit()
    sweeper.sweep()
    assert media_for(session, det) == []


def test_the_native_tier_runs_on_the_first_sweep(sweeper, session) -> None:
    """The regression this whole change exists to fix: the 3 s exemplar
    preamble spent the budget before any tier was entered, so nothing was
    ever deleted."""
    make_detection_with_media(session, age_days=30, kept=False)
    report = sweeper.sweep()
    assert report.tier_counts.get("native", 0) > 0


def test_a_held_detection_is_still_exempt_and_held_is_not_kept(sweeper, session) -> None:
    """ADR-043's mechanism is untouched, and the two remain distinct."""
    held = make_detection_with_media(session, age_days=400, held=True)
    sweeper.sweep()
    assert media_for(session, held) != []
    assert held.kept_at is None
```

Use the builders already in `tests/test_retention.py`; add `kept=` / `held=`
parameters to them if they do not exist.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_retention.py -q -k "kept or native_tier or held"`
Expected: FAIL — `kept` is not yet honoured anywhere.

- [ ] **Step 3: Delete the exemplar computation**

Delete `_exemplar_detection_ids` entirely, and the `exemplar_ids = ...` line at
`retention.py:225`. Remove `exemplar_ids` from `_strip_non_exemplar`'s signature
and its Python-side `if detection_id in exemplar_ids` filter, and rename it
`_strip_unkept`. Remove the now-unneeded `limit(budget * 4)` over-fetch, replacing
it with `limit(budget)`, and update the comment above it that claims exemplar
filtering "isn't expressible as a join condition".

- [ ] **Step 4: Add the clause to all four tiers**

In each of `_strip_native`, `_strip_unkept`, `_strip_expired` and
`_watermark_reclaim`, add to the candidate query:

```python
            .where(orm.Detection.kept_at.is_(None))
```

Update `_strip_unkept`'s `reason` string from "not first-of-species or
best-of-species" to `f"age >= {self.audible_only_days}d and not kept"`. Update
`_watermark_reclaim`'s reason, which currently says "tier and exemplar status
ignored", to say that kept recordings are never reclaimed.

- [ ] **Step 5: Rename the report field**

In `RetentionReport`, replace `exemplar_detections: int = 0` with
`kept_detections: int = 0`, and update `to_dict()`. Then update every consumer:

```bash
grep -rn "exemplar_detections" src/ web/ tests/ docs/
```

`api/app.py` and `api/metrics.py` both read it (`oo_retention_exemplar_detections`).
Rename the metric to `oo_retention_kept_detections` and update
`web/src/components/RetentionPanel.tsx` and its test if they surface it.

- [ ] **Step 6: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_retention.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/open_observatory/retention.py src/open_observatory/api/ web/src/ tests/
git commit -m "Every retention tier honours kept; the exemplar computation is gone (ADR-061)"
```

---

### Task 3: The watermark refuses, loudly

**Files:**
- Modify: `src/open_observatory/retention.py` (`_watermark_reclaim`)
- Modify: `src/open_observatory/api/app.py` (`_health_payload`, near the existing watermark `problems` entry around line 972)
- Test: `tests/test_retention.py`, `tests/test_api.py`

**Interfaces:**
- Produces: `RetentionReport.watermark_blocked_by_kept: int` — bytes held by kept recordings that the watermark tier declined to reclaim. 0 when the watermark is not exceeded.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_watermark_reports_rather_than_deleting_what_was_kept(sweeper, session) -> None:
    """Silently deleting a recording a human asked to keep would be worse than
    a full disk they can see coming."""
    kept = make_detection_with_media(session, age_days=400, kept=True)
    report = sweeper.sweep()  # fixture forces disk_used_ratio above the watermark
    assert media_for(session, kept) != []
    assert report.watermark_blocked_by_kept > 0
```

```python
def test_health_names_kept_evidence_when_the_watermark_cannot_be_met(self, client) -> None:
    """A full disk must be visible before it is a surprise."""
    # Drive status_snapshot to report disk over the watermark with
    # watermark_blocked_by_kept > 0, as tests/test_api.py does elsewhere with
    # monkeypatch, then assert a problems entry mentioning "kept".
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_retention.py tests/test_api.py -q -k watermark --deselect tests/test_api.py::TestLiveChannels`

- [ ] **Step 3: Implement**

`_watermark_reclaim` already skips kept rows after Task 2. Add a second query
that sums the bytes of kept, un-reclaimed assets and record it:

```python
        report.watermark_blocked_by_kept = int(
            session.execute(
                select(func.coalesce(func.sum(orm.MediaAsset.byte_length), 0))
                .join(orm.DetectionMedia, orm.DetectionMedia.media_asset_id == orm.MediaAsset.id)
                .join(orm.Detection, orm.Detection.id == orm.DetectionMedia.detection_id)
                .where(orm.MediaAsset.reclaimed_at.is_(None))
                .where(orm.Detection.kept_at.is_not(None))
            ).scalar_one()
        )
```

Compute it only when the watermark is exceeded, so a healthy station never pays
for it. In `_health_payload`, add a `problems` entry when
`watermark_blocked_by_kept > 0` and disk is over the watermark, naming the byte
figure and that these are operator-kept recordings.

- [ ] **Step 4: Run to verify they pass, then commit**

```bash
git add src/open_observatory/retention.py src/open_observatory/api/app.py tests/
git commit -m "The watermark reports kept evidence rather than reclaiming it (ADR-061)"
```

---

### Task 4: `preamble_s` and `tiers_skipped`, so this cannot hide again

**Files:**
- Modify: `src/open_observatory/retention.py` (`RetentionReport`, `sweep`)
- Modify: `src/open_observatory/api/metrics.py`
- Test: `tests/test_retention.py`

**Interfaces:**
- Produces: `RetentionReport.preamble_s: float`, `RetentionReport.tiers_skipped: list[str]`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_sweep_that_never_reaches_a_tier_says_so(sweeper, session, monkeypatch) -> None:
    """`complete=False` read identically whether a sweep ran out of time with
    work outstanding or never started, and the only other symptom was a flat
    zero counter. That is why this hid for nine days."""
    monkeypatch.setattr(sweeper, "batch_budget_s", 0.0)
    report = sweeper.sweep()
    assert report.complete is False
    assert report.tiers_skipped == ["native", "unkept", "expired", "watermark"]
    assert report.preamble_s >= 0.0
```

- [ ] **Step 2: Run it, watch it fail**

- [ ] **Step 3: Implement**

Record `preamble_s` as the monotonic time from sweep start to just before the
first tier guard. Append a tier's name to `tiers_skipped` at each guard that
evaluates False. Add both to `to_dict()`. Emit
`oo_retention_preamble_seconds` from `api/metrics.py` alongside the existing
retention gauges. In `station.py`'s `housekeeping.retention_not_keeping_up`
warning, the extra fields ride along for free via `result.to_dict()`.

- [ ] **Step 4: Run, then commit**

```bash
git add src/open_observatory/retention.py src/open_observatory/api/metrics.py tests/
git commit -m "A sweep that never reached a tier now says which and why (ADR-061)"
```

---

### Task 5: API, CLI, and the drawer toggle

**Files:**
- Modify: `src/open_observatory/api/app.py` (detection routes)
- Modify: `src/open_observatory/cli.py`
- Modify: `web/src/components/DetectionDrawer.tsx`
- Test: `tests/test_api.py`, `tests/test_cli_detections.py`, `web/src/components/DetectionDrawer.test.tsx`

**Interfaces:**
- Consumes: `orm.Detection.kept_at`, `kept_by` from Task 1.
- Produces: `PUT /api/v1/detections/{id}/keep`, `DELETE /api/v1/detections/{id}/keep`; both return the serialised detection with `kept_at` and `kept_by` present. The detection serialiser gains `kept_at: str | None` and `kept_by: str | None`.

- [ ] **Step 1: Write the failing API tests**

```python
def test_keeping_a_detection_sets_who_and_when(self, client) -> None:
    detection_id = client.get("/api/v1/detections?limit=1").json()["detections"][0]["id"]
    body = client.put(f"/api/v1/detections/{detection_id}/keep").json()
    assert body["kept_at"] is not None
    assert body["kept_by"]


def test_unkeeping_clears_both(self, client) -> None:
    detection_id = client.get("/api/v1/detections?limit=1").json()["detections"][0]["id"]
    client.put(f"/api/v1/detections/{detection_id}/keep")
    body = client.request("DELETE", f"/api/v1/detections/{detection_id}/keep").json()
    assert body["kept_at"] is None
    assert body["kept_by"] is None


def test_keeping_an_unknown_detection_is_404(self, client) -> None:
    assert client.put(f"/api/v1/detections/{uuid.uuid4()}/keep").status_code == 404
```

- [ ] **Step 2: Run, watch fail (405/404), implement the routes**

Set `kept_by` from the authenticated actor when auth is enabled, else the string
`"operator"`. Keep both routes behind the auth gate — they are operator actions,
not public reads, so do **not** add them to `auth_public_read_paths`.

- [ ] **Step 3: CLI, test-first**

```python
def test_keep_marks_the_detection_and_emits_json(...) -> None:
    result = runner.invoke(app, ["detections", "keep", str(detection_id), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["kept_at"]
```

Implement `oo detections keep <id>` and `oo detections keep <id> --unkeep`, output
via `emit_json`. Then run `.venv/bin/python -m pytest tests/test_cli_json_output.py -q`
— it scans `cli.py` for `console.print_json` and has caught this twice before.

- [ ] **Step 4: The drawer toggle, test-first**

```tsx
it("keeps a recording and shows it as kept", async () => {
  // render DetectionDrawer with a detection whose kept_at is null,
  // click the keep control, assert the PUT fired and the control
  // reflects the kept state.
});
```

Read `DetectionDrawer.tsx` and follow the pattern its existing review controls use
for optimistic update and error handling. Run `cd web && npm test`.

- [ ] **Step 5: Commit**

```bash
git add src/open_observatory/api/app.py src/open_observatory/cli.py web/src tests/
git commit -m "Keep a recording from the drawer, the API or the CLI (ADR-061)"
```

---

### Task 6: ADR-061, docs, and verification on the station

**Files:**
- Modify: [[ADRS]] — **body and index row**; the index stopping short is the failure mode its own header names, and [[ADR-060 - A stalled read is a dead stream|ADR-060]] has already hit it
- Modify: `config/example.env`
- Modify: [[MILESTONE_STATUS]], [[HANDOVER]]

- [ ] **Step 1: Write [[ADR-061 - Operator keep flag|ADR-061]]**

Cover: the 2.978 s measurement against the 1.5 s budget; the three symptoms it
caused; why a human flag beats a computed one; why first-of-species is backfilled
and best is not; why kept survives the watermark. Include the **rollback note**
and **target smoke test** `CLAUDE.md` requires — [[ADR-060 - A stalled read is a dead stream|ADR-060]] shipped without either
and that is recorded as a defect.

Rollback: `alembic downgrade 0007_capture_pause`, and note that every operator
keep is lost and unrecoverable, so export the kept ids first.

- [ ] **Step 2: Add the index row, and fix [[ADR-060 - A stalled read is a dead stream|ADR-060]]'s missing one while you are here**

- [ ] **Step 3: Add the settings [[ADR-060 - A stalled read is a dead stream|ADR-060]] introduced to `config/example.env`**

`capture_silence_critical_s`, `capture_read_timeout_s`. (`stall_timeout_s` is a
constructor default, not a setting — do not add it.)

- [ ] **Step 4: Deploy and verify on the station**

```bash
HOST=<user>@<station-host> ./deploy/deploy.sh --no-web   # runs alembic upgrade head
```

Then verify, and record actual figures:

```bash
curl -s http://<station-host>:8080/metrics | grep -E "oo_retention_(files_deleted|bytes_reclaimed|preamble)"
curl -s http://<station-host>:8080/api/v1/health | python3 -m json.tool | grep -A3 retention
```

Three things must be true, and none may be assumed:
1. `oo_retention_files_deleted_total{tier="native"}` is **non-zero** — deletion has never once happened, so this is the claim that matters.
2. Sweep `duration_s` is **inside** `retention_batch_budget_s` (1.5 s), and `complete` is true.
3. After ~30 minutes, `capture_gap` rows are **no longer arriving in pairs 300 s apart**. Query the station database read-only via `sqlite3.connect('file:...?mode=ro', uri=True)`.

If (3) still shows the beat, the sweep is still costing audio and the fix is
incomplete — say so plainly rather than reporting the first two as success.

- [ ] **Step 5: Commit**

```bash
git add docs/ config/example.env
git commit -m "ADR-061: an operator-set keep flag replaces the computed exemplar rule"
```

---

## Self-review

**Spec coverage:** data model → Task 1; backfill → Task 1; retention change → Task 2; keep-forever across all four tiers → Task 2; watermark refusal → Task 3; observability → Task 4; API/CLI/UI → Task 5; ADR, rollback, smoke test → Task 6. No spec section is unimplemented.

**Known gaps, deliberate:** the doc-audit backlog (11 documents saying the soak never ran, 11 ADRs hidden in unterminated code fences, [[ADR-059 - Clip archive measured off-loop|ADR-059]]'s false status, the pre-[[ADR-046 - Deficit is mostly drift|ADR-046]] guidance in two files) is **not** in this plan. It is mechanical, unrelated to this change, and belongs in its own pass.

**Asymmetry worth knowing:** `_watermark_reclaim` currently ignores `held_ids` — at the watermark it will delete a held recording today. This plan makes `kept` exempt there but deliberately leaves `held` as it is, since changing [[ADR-043 - Taxon correction|ADR-043]]'s behaviour is out of scope. Flag it for a later decision rather than fixing it silently.
