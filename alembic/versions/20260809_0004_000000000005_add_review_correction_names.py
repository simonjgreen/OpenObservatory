"""Add `review.corrected_common_name` / `review.corrected_scientific_name`.

ADR-043: taxon correction. A reviewer replacing an identification supplies a
`corrected_taxon_id` (already a column, unused until now -- every prior
review write forced it to `None`). Displaying that correction anywhere --
the review drawer, `GET /api/v1/detections`, the CSV/JSON export -- needs a
human-readable name, and this station has no taxonomy service to resolve a
`canonical_taxon_id` back to a name at read time (see `review.py`'s module
docstring). So the name is captured once, at write time, from whichever of
the station's own past detections `corrected_taxon_id` matched
(`review.resolve_taxon`), and stored alongside the id on the `Review` row
itself -- consistent with `Review` already being an append-only, fully
self-contained record of one human judgement.

Written directly rather than through `db/session.py`'s SQLite
`_patch_sqlite_columns` safety net: that net exists only for columns that
already shipped without a migration (see its docstring), not as a sanctioned
path for a new one.

Revision ID: 0005_review_correction_names
Revises: 0004_drop_dead_detection_indexes
Create Date: 2026-08-09 10:35:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_review_correction_names"
down_revision: str | None = "0004_drop_dead_detection_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    """Add the two columns, skipping either that already exists.

    The skip follows revision 0003's precedent: an operator who deploys and
    restarts before running this migration already has these columns via
    `db/session.py`'s SQLite patcher (it applies to any new nullable model
    column, this pair included), and a bare `ADD COLUMN` would then abort
    with "duplicate column name" on a live station holding real review rows.
    """
    existing = _existing_columns("review")
    with op.batch_alter_table("review", schema=None) as batch_op:
        if "corrected_common_name" not in existing:
            batch_op.add_column(sa.Column("corrected_common_name", sa.String(length=240), nullable=True))
        if "corrected_scientific_name" not in existing:
            batch_op.add_column(
                sa.Column("corrected_scientific_name", sa.String(length=240), nullable=True)
            )


def downgrade() -> None:
    """Drop both columns, discarding any recorded correction display names.

    `corrected_taxon_id` itself is untouched (it predates this revision) --
    only the denormalised display names go. No backup is taken here.
    """
    existing = _existing_columns("review")
    with op.batch_alter_table("review", schema=None) as batch_op:
        if "corrected_scientific_name" in existing:
            batch_op.drop_column("corrected_scientific_name")
        if "corrected_common_name" in existing:
            batch_op.drop_column("corrected_common_name")
