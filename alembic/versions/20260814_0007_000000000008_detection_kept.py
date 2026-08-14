"""Add `detection.kept_at` / `detection.kept_by`, and backfill first-of-species.

ADR-061. Replaces `RetentionSweeper._exemplar_detection_ids`, an unbounded
2.978 s query run before any deadline check, with two columns and an indexed
SQL clause.

The backfill matters: without it, every recording the computed rule currently
protects becomes deletable on the next sweep, and the native tier has a full
batch of candidates waiting. First-of-species only -- a first-ever record
cannot be recreated, a better recording may come along, so `best` is a human
decision from here on and is deliberately not backfilled.

The backfill query below deliberately does **not** use `GROUP BY ... HAVING
event_start_utc = MIN(event_start_utc)`: on a real table, two detections of
the same species can share the same `event_start_utc` down to the second (two
detectors firing on the same window), and that pattern would then set
`kept_at` on *both* -- "first per species" would silently become "first
per species per tie". A window function ranks by `(event_start_utc, id)`
instead, so ties break deterministically on a total order and exactly one row
per species key is ever selected. SQLite has supported window functions
since 3.25 (2018), well within this project's target.

Revision ID: 0008_detection_kept
Revises: 0007_capture_pause
Create Date: 2026-08-14 09:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_detection_kept"
down_revision: str | None = "0007_capture_pause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("detection")}
    # Same precedent as revisions 0003, 0005 and 0007: a station that ran the
    # new code before the migration ran already has these from `create_all()`.
    if "kept_at" not in existing:
        op.add_column(
            "detection", sa.Column("kept_at", sa.DateTime(timezone=True), nullable=True)
        )
    if "kept_by" not in existing:
        op.add_column("detection", sa.Column("kept_by", sa.String(length=120), nullable=True))
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("detection")}
    if "ix_detection_kept_at" not in indexes:
        op.create_index("ix_detection_kept_at", "detection", ["kept_at"])

    # Backfill: the earliest surviving detection per species key, matching the
    # key rule of the computed exemplar it replaces -- canonical_taxon_id, else
    # common_name, else taxonomic_group, else 'unknown'. Only detections that
    # still have at least one un-reclaimed media asset: there is nothing to
    # protect for one that does not.
    #
    # `sa.func.now()` is not used here: it renders as the SQLite function
    # `now()` (bound as a scalar, not `CURRENT_TIMESTAMP`), which does not
    # exist and would fail at execute time. The timestamp is instead computed
    # once in Python and passed as a bound parameter, so `kept_at` is never
    # left NULL for a row this backfill selects.
    now = datetime.now(UTC)
    op.execute(
        sa.text(
            """
            UPDATE detection
               SET kept_at = :now, kept_by = 'exemplar-backfill'
             WHERE id IN (
                   SELECT id FROM (
                       SELECT
                           d.id AS id,
                           ROW_NUMBER() OVER (
                               PARTITION BY COALESCE(
                                   d.canonical_taxon_id, d.common_name,
                                   d.taxonomic_group, 'unknown'
                               )
                               ORDER BY d.event_start_utc ASC, d.id ASC
                           ) AS species_rank
                       FROM detection d
                       WHERE EXISTS (
                           SELECT 1 FROM detection_media dm
                           JOIN media_asset ma ON ma.id = dm.media_asset_id
                           WHERE dm.detection_id = d.id
                             AND ma.reclaimed_at IS NULL
                       )
                   ) ranked
                   WHERE ranked.species_rank = 1
             )
            """
        ).bindparams(now=now)
    )


def downgrade() -> None:
    op.drop_index("ix_detection_kept_at", table_name="detection")
    op.drop_column("detection", "kept_by")
    op.drop_column("detection", "kept_at")
