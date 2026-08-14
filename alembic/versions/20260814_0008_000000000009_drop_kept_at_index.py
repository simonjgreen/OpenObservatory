"""Drop `ix_detection_kept_at`: it was a pessimisation, not an optimisation.

ADR-061. Revision 0008 added this index on the reasoning that all four
retention tiers filter on `kept_at`, so the filter should be indexed. The
reasoning was wrong, and it was wrong in the direction that takes a station
down rather than merely leaving performance on the table.

`kept_at IS NULL` matches almost every row -- 112 non-null out of ~46,000 on
the live station -- so the index cannot usefully narrow anything. SQLite
preferred it regardless, and choosing it meant *not* choosing
`ix_detection_event_start_utc`, which was serving both the range predicate and
the `ORDER BY` in `_strip_native`'s candidate query. The plan degraded to
materialising the join into a temp B-tree and sorting it.

Measured on the live station's own database, best of three:

    with the index:     SEARCH d USING ix_detection_kept_at
                        + USE TEMP B-TREE FOR ORDER BY          0.555 s
    without the index:  SEARCH d USING ix_detection_event_start_utc
                        (no sort)                               0.117 s

Those figures are from a quiet read-only copy. On the live station, under WAL
contention and selecting whole `MediaAsset` rows rather than one column, the
indexed plan blocked a single `sweep()` call for over five minutes. Retention
runs in the evidence executor and the housekeeping loop awaits it, so
everything behind it stopped: stream heartbeats, the ADR-057 media audit and
the ADR-059 disk-usage refresh. Capture was unaffected -- it owns its own
thread (ADR-030) -- but the loop that is supposed to watch the station had
stopped watching.

The `kept_at IS NULL` predicate is still applied; it is simply evaluated
against rows the ordering index has already narrowed, which is the cheap way
round.

**Do not re-add this index without measuring the plan on a database the size
of the station's.** No test fixture in this repository is large enough for
SQLite's planner to make the bad choice, so a green suite proves nothing about
it -- 923 tests passed against the version that wedged the station.

Revision ID: 0009_drop_kept_at_index
Revises: 0008_detection_kept
Create Date: 2026-08-14 13:50:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_drop_kept_at_index"
down_revision: str | None = "0008_detection_kept"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("detection")}
    # Same check-then-act precedent as 0003, 0005, 0007 and 0008: a station that
    # ran this revision's code before the migration has no such index to drop,
    # and an unconditional DROP would abort the whole upgrade.
    if "ix_detection_kept_at" in indexes:
        op.drop_index("ix_detection_kept_at", table_name="detection")


def downgrade() -> None:
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("detection")}
    if "ix_detection_kept_at" not in indexes:
        op.create_index("ix_detection_kept_at", "detection", ["kept_at"])
