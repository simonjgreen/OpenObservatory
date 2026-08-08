"""add missing media asset reclaimed at index

Real gap found while building this migration environment (see ADR-035),
not a synthetic example. The defensive ``ALTER TABLE ... ADD COLUMN`` patcher
in ``db/session.py`` added ``media_asset.reclaimed_at`` and
``media_asset.reclaim_reason`` to the live station's SQLite database earlier
today, but ``ADD COLUMN`` does not add indexes -- so the index the model
declares on ``reclaimed_at`` was silently never created on that database
(confirmed against a local copy of the live station's
``openobservatory.sqlite``: the column is present, the index is not).
``create_all()`` on a database that never had the table would have created
the index along with the table, so this only affects databases that lived
through the patched-column path, i.e. the live station and any developer
database old enough to have needed the patch.

Written as ``CREATE INDEX IF NOT EXISTS`` / plain ``DROP INDEX`` rather than
the batch-mode form Alembic's autogenerate first produced here: creating or
dropping a plain index needs no table rebuild on SQLite (unlike adding a
column), so batch mode buys nothing, and the unconditional
``batch_alter_table`` form autogenerate emitted fails with "index already
exists" when applied to a database that reached ``0001_initial`` by a normal
``upgrade`` (0001 already creates this index as part of the honest
baseline). ``IF NOT EXISTS`` makes this migration a safe no-op on that path
and a real fix on the live station's path -- both are legitimate ways to
arrive at ``0001_initial``. Both clauses are standard SQL, supported the same
way by SQLite and PostgreSQL 16, so this migration behaves identically on
both (see ``docs/data/DATA_MODEL.md``).

Revision ID: 0002_media_asset_reclaimed_at_index
Revises: 0001_initial
Create Date: 2026-08-08 18:30:42.903596+00:00

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0002_media_asset_reclaimed_at_index'
down_revision: str | None = '0001_initial'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_media_asset_reclaimed_at ON media_asset (reclaimed_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_media_asset_reclaimed_at")
