"""Partial indexes on the *live* media assets, and the reverse join retention walks.

ADR-062. The third instance of the same mistake, and the first one that took
the station down slowly enough that nothing noticed for two days.

**The failure.** Every retention tier's candidate query ordered by
`detection.event_start_utc` and filtered `media_asset.reclaimed_at IS NULL`.
That is fine while nothing has been reclaimed. Once the sweep has done some
work, the rows it already reclaimed stay in the index it walks, and it has to
step over all of them again on the next pass to reach the ones it has not.
Measured on the station's own database, 2026-08-19: 38,268 reclaimed assets,
~210,000 old detections walked per sweep, to find the 1,699 native clips
actually outstanding. **The query got slower every time it succeeded** until it
could no longer finish inside `retention_batch_budget_s` (1.5 s) at all -- at
which point `_bounded_statements` interrupted it, the native tier deleted
nothing, and the unkept and watermark tiers never ran. 351 of ~526 sweeps
reclaimed zero bytes while the disk climbed 3.8 GB/hour toward a watermark
whose own tier was among the ones being skipped.

The tell that this was not simply "a big query": cost was **constant at every
`LIMIT`** from 1 to 4000 (~2.2 s), because all of it was spent reaching the
first surviving row. A query whose `LIMIT 1` costs the same as its `LIMIT 4000`
is not selecting, it is scanning.

**The fix.** Partial indexes whose `WHERE` clause is `reclaimed_at IS NULL`, so
a reclaimed row *leaves the index*. The work already done is no longer walked.
`created_at` is last in each index so the range predicate and the `ORDER BY`
are served together and the scan stops at `LIMIT` instead of sorting.

Measured on a copy of the live database, best of three:

    native tier     ix_detection_event_start_utc, 210k-row walk   2.2000 s
                    -> ix_media_asset_live_kind_created           0.0049 s
    unkept tier     (same shape, would degrade identically)       0.0000 s
    watermark tier  SCAN detection_media + TEMP B-TREE            1.8048 s
                    -> ix_media_asset_live_created                0.0032 s
    asset->detection join, 250 candidates                         0.1144 s
                    -> ix_detection_media_asset                   0.0046 s

**Dropping `ix_media_asset_reclaimed_at` is load-bearing, not tidying.** It is
the index revision 0001 added, and it is the reason the planner would not use
the new partial ones: `reclaimed_at` is NULL on 176,231 of 214,499 rows, so a
plain index on it looks like a cheap way in and SQLite takes it, losing the
`ORDER BY` and adding a `USE TEMP B-TREE`. With it present the native tier
plans at 0.1215 s instead of 0.0004 s. Every consumer in the codebase filters
`reclaimed_at IS NULL`, which the partial indexes serve; nothing filters
`IS NOT NULL` in a hot path.

**Do not run `ANALYZE` on this database to "help".** It was tried here and made
things worse: `sqlite_stat1` records an average of 6 rows per `reclaimed_at`
value, which is true and useless, since the NULL bucket holds 176,231 of them.
With stats present the planner abandoned the correct plan and went back to the
temp B-tree (0.0004 s -> 0.1215 s). This is the same class of wrong-but-
confident measurement ADR-061's addenda record.

Revision ID: 0011_retention_live_asset_indexes
Revises: 0010_kept_at_partial_index
Create Date: 2026-08-19 10:45:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_retention_live_asset_indexes"
down_revision: str | None = "0010_kept_at_partial_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIVE_KIND_CREATED = "ix_media_asset_live_kind_created"
LIVE_CREATED = "ix_media_asset_live_created"
DETECTION_MEDIA_ASSET = "ix_detection_media_asset"
OBSOLETE_RECLAIMED_AT = "ix_media_asset_reclaimed_at"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    # Same check-then-act precedent as 0003, 0005, 0007, 0008, 0009 and 0010.
    asset_indexes = {i["name"] for i in inspector.get_indexes("media_asset")}
    if LIVE_KIND_CREATED not in asset_indexes:
        op.create_index(
            LIVE_KIND_CREATED,
            "media_asset",
            ["kind", "created_at"],
            sqlite_where=sa.text("reclaimed_at IS NULL"),
        )
    if LIVE_CREATED not in asset_indexes:
        op.create_index(
            LIVE_CREATED,
            "media_asset",
            ["created_at"],
            sqlite_where=sa.text("reclaimed_at IS NULL"),
        )

    link_indexes = {i["name"] for i in inspector.get_indexes("detection_media")}
    if DETECTION_MEDIA_ASSET not in link_indexes:
        op.create_index(DETECTION_MEDIA_ASSET, "detection_media", ["media_asset_id"])

    # Dropped last, so a failure part-way through this migration never leaves a
    # database with neither the old index nor the new ones.
    if OBSOLETE_RECLAIMED_AT in asset_indexes:
        op.drop_index(OBSOLETE_RECLAIMED_AT, table_name="media_asset")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    asset_indexes = {i["name"] for i in inspector.get_indexes("media_asset")}
    if OBSOLETE_RECLAIMED_AT not in asset_indexes:
        op.create_index(OBSOLETE_RECLAIMED_AT, "media_asset", ["reclaimed_at"])
    for name in (LIVE_KIND_CREATED, LIVE_CREATED):
        if name in asset_indexes:
            op.drop_index(name, table_name="media_asset")

    link_indexes = {i["name"] for i in inspector.get_indexes("detection_media")}
    if DETECTION_MEDIA_ASSET in link_indexes:
        op.drop_index(DETECTION_MEDIA_ASSET, table_name="detection_media")
