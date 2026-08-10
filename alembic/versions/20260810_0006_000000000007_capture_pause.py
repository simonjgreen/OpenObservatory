"""Add the `capture_pause` table.

ADR-055: the operator pause. The deadline of a running pause is persisted so a
station that reboots mid-pause comes back paused rather than recording, and so
that history and coverage can draw the window as deliberate rather than as an
unexplained silence (charter item 2).

Purely additive: a new table, no column added to and no data read from any
existing one. A station upgraded to this revision and then rolled back loses
only the pause records; nothing else in the schema refers to them.

Revision ID: 0007_capture_pause
Revises: 0006_refinement
Create Date: 2026-08-10 09:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_capture_pause"
down_revision: str | None = "0006_refinement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "capture_pause" in sa.inspect(op.get_bind()).get_table_names():
        # Same precedent as revisions 0003 and 0005: a station that started the
        # new code before the migration ran already has this table from
        # `create_all()`, and a bare CREATE would abort the whole upgrade.
        return
    op.create_table(
        "capture_pause",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("station_id", sa.Uuid(), nullable=True),
        sa.Column("started_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(length=24), nullable=True),
        sa.Column("preset", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["station_id"], ["station.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_capture_pause_started_utc"), "capture_pause", ["started_utc"], unique=False
    )
    op.create_index(
        op.f("ix_capture_pause_ends_utc"), "capture_pause", ["ends_utc"], unique=False
    )


def downgrade() -> None:
    """Drop the table, discarding the record of every pause ever taken.

    Not reversible in any useful sense: the pauses themselves are gone from the
    durable record, so a coverage view rendered afterwards will show those
    windows as ordinary capture with nothing detected in them.
    """
    op.drop_index(op.f("ix_capture_pause_ends_utc"), table_name="capture_pause")
    op.drop_index(op.f("ix_capture_pause_started_utc"), table_name="capture_pause")
    op.drop_table("capture_pause")
