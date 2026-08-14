"""A *partial* index on `detection.kept_at`, covering only the kept rows.

ADR-061. Revision 0008 added a plain index on `kept_at` and it wedged the
station (see 0009). Revision 0009 dropped it and fixed `_strip_native` — and
broke the other half of the same sweep: `RetentionReport.kept_detections`
counts `kept_at IS NOT NULL`, which with no index is a full `SCAN detection`
over 290,956 rows. Measured ~6 s on the live station under WAL contention,
spent in the sweep's *preamble*, before any tier guard. The 1.5 s budget was
gone before the first tier and all four were skipped — the same
silent-zero-deletions symptom as the original defect, reached by a third route.

The two requirements looked contradictory, and are not:

* `kept_at IS NOT NULL` (the count) wants an index.
* `kept_at IS NULL` (every tier's candidate query) must NOT have one available,
  because SQLite will prefer it over `ix_detection_event_start_utc` and lose
  the index that serves both the range predicate and the `ORDER BY`.

A partial index gives exactly that asymmetry. It indexes only rows matching its
`WHERE` clause — 112 of 290,956 on the station — so the planner may use it for
`IS NOT NULL` and *cannot* use it for `IS NULL`.

Measured on a copy of the live station's database, best of three:

    kept count      SCAN detection                        0.151 s
                    -> COVERING INDEX ix_..._partial      0.000 s
    _strip_native   ix_detection_event_start_utc          0.113 s
                    -> ix_detection_event_start_utc       0.115 s   (unchanged,
                                                                     no sort)

**If you ever replace this with a plain index, both failures return.** The
`WHERE` clause is the mechanism, not a detail — `tests/test_migrations.py`
asserts the clause itself for that reason, since a name check alone would pass
against a plain index.

Revision ID: 0010_kept_at_partial_index
Revises: 0009_drop_kept_at_index
Create Date: 2026-08-14 14:05:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_kept_at_partial_index"
down_revision: str | None = "0009_drop_kept_at_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_detection_kept_at_partial"


def upgrade() -> None:
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("detection")}
    # Same check-then-act precedent as 0003, 0005, 0007, 0008 and 0009.
    if INDEX_NAME not in indexes:
        op.create_index(
            INDEX_NAME,
            "detection",
            ["kept_at"],
            sqlite_where=sa.text("kept_at IS NOT NULL"),
        )


def downgrade() -> None:
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("detection")}
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="detection")
