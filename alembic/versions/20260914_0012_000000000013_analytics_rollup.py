"""The analytics roll-up tables and the saved-report table (ADR-079).

Six tables, all rebuildable from the detection table, none of them a record
of anything. ADR-056 measured the shape: a year of day-and-hour summaries is a
few megabytes against gigabytes of detail, and a phenology grid over it is
milliseconds where the detail table is minutes. They are built by
`oo analytics rebuild` on a timer, never by a trigger -- ADR-056 costed a
per-insert trigger at +4,065 WAL bytes per detection and rejected it.

`analytics_report` is the one table here that is not derived: a question an
operator chose to keep.

Revision ID: 0013_analytics_rollup
Revises: 0012_detection_banked_at
Create Date: 2026-09-14 12:00:00+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_analytics_rollup"
down_revision: str | None = "0012_detection_banked_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "analytics_day",
    "analytics_coverage_hour",
    "analytics_hour",
    "analytics_group_day",
    "analytics_taxon_hour",
    "analytics_report",
)


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "analytics_day" not in existing:
        op.create_table(
            "analytics_day",
            sa.Column("local_date", sa.String(length=10), primary_key=True),
            sa.Column("timezone", sa.String(length=64), nullable=False),
            sa.Column("day_start_utc", sa.DateTime(timezone=True), nullable=False),
            sa.Column("day_end_utc", sa.DateTime(timezone=True), nullable=False),
            sa.Column("seconds_in_day", sa.Integer(), nullable=False),
            sa.Column("seconds_captured", sa.Float(), nullable=False),
            sa.Column("seconds_from_microphone", sa.Float(), nullable=False),
            sa.Column("seconds_paused", sa.Float(), nullable=False),
            sa.Column("detections", sa.Integer(), nullable=False),
            sa.Column("excluded_synthetic", sa.Integer(), nullable=False),
            sa.Column("excluded_withdrawn", sa.Integer(), nullable=False),
            sa.Column("excluded_rejected", sa.Integer(), nullable=False),
            sa.Column("sunrise_utc", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sunset_utc", sa.DateTime(timezone=True), nullable=True),
            sa.Column("civil_dawn_utc", sa.DateTime(timezone=True), nullable=True),
            sa.Column("civil_dusk_utc", sa.DateTime(timezone=True), nullable=True),
            sa.Column("daylight_captured_seconds", sa.Float(), nullable=False),
            sa.Column("dusk_to_midnight_captured_seconds", sa.Float(), nullable=False),
            sa.Column("midnight_to_dawn_captured_seconds", sa.Float(), nullable=False),
            sa.Column("complete", sa.Boolean(), nullable=False),
            sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("builder_version", sa.String(length=16), nullable=False),
            sa.Column("build_seconds", sa.Float(), nullable=False),
        )
    if "analytics_coverage_hour" not in existing:
        op.create_table(
            "analytics_coverage_hour",
            sa.Column("local_date", sa.String(length=10), primary_key=True),
            sa.Column("hour", sa.Integer(), primary_key=True),
            sa.Column("seconds_captured", sa.Float(), nullable=False),
            sa.Column("seconds_from_microphone", sa.Float(), nullable=False),
            sa.Column("seconds_paused", sa.Float(), nullable=False),
        )
    if "analytics_hour" not in existing:
        op.create_table(
            "analytics_hour",
            sa.Column("local_date", sa.String(length=10), primary_key=True),
            sa.Column("hour", sa.Integer(), primary_key=True),
            sa.Column("taxonomic_group", sa.String(length=48), primary_key=True),
            sa.Column("detections", sa.Integer(), nullable=False),
            sa.Column("best_score", sa.Float(), nullable=False),
        )
        op.create_index(
            "ix_analytics_hour_group_date", "analytics_hour", ["taxonomic_group", "local_date"]
        )
    if "analytics_group_day" not in existing:
        op.create_table(
            "analytics_group_day",
            sa.Column("local_date", sa.String(length=10), primary_key=True),
            sa.Column("taxonomic_group", sa.String(length=48), primary_key=True),
            sa.Column("detections", sa.Integer(), nullable=False),
            sa.Column("best_score", sa.Float(), nullable=False),
            sa.Column("first_utc", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_utc", sa.DateTime(timezone=True), nullable=True),
            sa.Column("p05_utc", sa.DateTime(timezone=True), nullable=True),
            sa.Column("p95_utc", sa.DateTime(timezone=True), nullable=True),
            sa.Column("daylight_detections", sa.Integer(), nullable=False),
            sa.Column("dusk_to_midnight_detections", sa.Integer(), nullable=False),
            sa.Column("midnight_to_dawn_detections", sa.Integer(), nullable=False),
        )
    if "analytics_taxon_hour" not in existing:
        op.create_table(
            "analytics_taxon_hour",
            sa.Column("local_date", sa.String(length=10), primary_key=True),
            sa.Column("hour", sa.Integer(), primary_key=True),
            sa.Column("taxonomic_group", sa.String(length=48), primary_key=True),
            sa.Column("label", sa.String(length=240), primary_key=True),
            sa.Column("scientific_name", sa.String(length=240), nullable=True),
            sa.Column("detections", sa.Integer(), nullable=False),
            sa.Column("best_score", sa.Float(), nullable=False),
        )
        op.create_index(
            "ix_analytics_taxon_hour_label_date", "analytics_taxon_hour", ["label", "local_date"]
        )
    if "analytics_report" not in existing:
        op.create_table(
            "analytics_report",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("view", sa.String(length=24), nullable=False),
            sa.Column("params", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.String(length=80), nullable=False),
        )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in reversed(TABLES):
        if table in existing:
            op.drop_table(table)
