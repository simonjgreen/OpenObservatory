"""Refinement records, and the three bookkeeping columns retention will need (ADR-042).

Charter item 5 -- "refine the record later, when better information exists --
and never silently" -- becomes two schema facts here.

**The ``refinement`` table.** Append-only, like ``review``. It carries the
identity of the new information that produced it (``refiner_*``, ``model_*``,
``evidence_fingerprint``), the *original* claim snapshotted verbatim
(``original_*``), and what is being proposed instead (``proposed_*``), plus
``basis``, ``outcome``, ``reason`` and ``created_at`` -- "what changed it and
when".

``ix_refinement_evidence`` is **unique on (detection_id, evidence_fingerprint)**
and is the charter's first rule expressed as a constraint rather than a
convention: the fingerprint covers refiner, model, weights *and* configuration,
so the same instrument at the same version under the same settings physically
cannot bank a second, more optimistic answer about the same event. Change the
model, the weights or the settings and the fingerprint changes and a new
refinement is admissible.

**Three columns on ``detection``.** ``refined_at`` (indexed),
``refinement_version`` and ``refinement_outcome`` -- the charter's retention
decision in schema form: "each event should carry the fact that refinement ran,
at what version, with what outcome, and deletion should require it". They are
denormalised from the newest refinement row on purpose: the retention sweeper
runs inside the capture process on a paced 1.5 s budget (ADR-026, ADR-033), so
the question "has this been refined?" has to be answerable from an index, not
from a correlated subquery against a second table.

Note what this revision deliberately does **not** do: it does not change
``retention.py``. Turning the age-based tiers into "age *and* refinement has
run" is a live-data deletion policy change and is the operator's call, not a
side effect of adding a column -- see ADR-042's "What this does not do".

``refinement.resolved_review_id`` references ``review.id``, so a human accepting
or rejecting a proposal is linkable both ways; nothing writes it yet, exactly as
nothing writes ``review`` yet (ADR-029).

Both statements are plain ``CREATE TABLE`` / ``ADD COLUMN`` of nullable columns,
which SQLite performs without a table rebuild, so batch mode buys nothing here
(revision 0002's precedent) and the SQL is identical on PostgreSQL 16 (ADR-007).

Revision ID: 0005_refinement
Revises: 0004_drop_dead_detection_indexes
Create Date: 2026-08-09 11:05:00.000000+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_refinement"
down_revision: str | None = "0004_drop_dead_detection_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _existing_columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    """Create the table and columns, skipping anything that already exists.

    The guard follows revision 0003's precedent, for the same reason and with
    the same force here. ``create_all()`` runs on every app and CLI startup and
    ``db/session.py``'s ALTER TABLE patcher adds any missing nullable column
    (ADR-035 kept both, because nothing calls ``alembic upgrade head`` at
    startup yet). So an operator who deploys and restarts *before* stamping and
    migrating already has ``refinement`` and the three ``detection`` columns,
    and a bare CREATE TABLE would abort the upgrade with "table refinement
    already exists" -- on a live station holding 50,000+ detections.

    Note the patcher's known limitation, which this revision has to cover: it
    cannot create an index, so a station that reached these columns that way has
    ``detection.refined_at`` **without** ``ix_detection_refined_at``. That is
    exactly the defect revision 0002 was written to repair for
    ``media_asset.reclaimed_at``, and the index is therefore created here
    independently of whether the column had to be added.
    """
    tables = _existing_tables()
    if "refinement" not in tables:
        _create_refinement_table()

    columns = _existing_columns("detection")
    if "refined_at" not in columns:
        op.add_column(
            "detection", sa.Column("refined_at", sa.DateTime(timezone=True), nullable=True)
        )
    if "refinement_version" not in columns:
        op.add_column(
            "detection", sa.Column("refinement_version", sa.String(length=200), nullable=True)
        )
    if "refinement_outcome" not in columns:
        op.add_column(
            "detection", sa.Column("refinement_outcome", sa.String(length=24), nullable=True)
        )
    # Unconditional, and deliberately outside the column guard above: ADD COLUMN
    # cannot create an index, so the column existing is no evidence the index
    # does (revision 0002's finding, on a real station).
    op.execute("CREATE INDEX IF NOT EXISTS ix_detection_refined_at ON detection (refined_at)")


def _create_refinement_table() -> None:
    op.create_table(
        "refinement",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("detection_id", sa.Uuid(), nullable=False),
        sa.Column("refiner_id", sa.String(length=80), nullable=False),
        sa.Column("refiner_version", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=120), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("model_sha256", sa.String(length=64), nullable=True),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("basis", sa.String(length=24), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("original_common_name", sa.String(length=240), nullable=True),
        sa.Column("original_scientific_name", sa.String(length=240), nullable=True),
        sa.Column("original_taxonomic_group", sa.String(length=48), nullable=True),
        sa.Column("original_score", sa.Float(), nullable=True),
        sa.Column("proposed_common_name", sa.String(length=240), nullable=True),
        sa.Column("proposed_scientific_name", sa.String(length=240), nullable=True),
        sa.Column("proposed_rank", sa.String(length=32), nullable=True),
        sa.Column("proposed_taxonomic_group", sa.String(length=48), nullable=True),
        sa.Column("proposed_score", sa.Float(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_review_id", sa.Uuid(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["detection_id"], ["detection.id"]),
        sa.ForeignKeyConstraint(["resolved_review_id"], ["review.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refinement_detection_id", "refinement", ["detection_id"])
    op.create_index("ix_refinement_refiner_id", "refinement", ["refiner_id"])
    op.create_index("ix_refinement_outcome", "refinement", ["outcome"])
    op.create_index("ix_refinement_created_at", "refinement", ["created_at"])
    op.create_index(
        "ix_refinement_evidence",
        "refinement",
        ["detection_id", "evidence_fingerprint"],
        unique=True,
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_detection_refined_at")
    op.drop_column("detection", "refinement_outcome")
    op.drop_column("detection", "refinement_version")
    op.drop_column("detection", "refined_at")
    op.drop_table("refinement")
