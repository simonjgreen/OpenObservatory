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
