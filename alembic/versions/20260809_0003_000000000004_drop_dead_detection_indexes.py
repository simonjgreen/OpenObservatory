"""Drop the four detection indexes nothing reads (ADR-037 option B).

``ix_detection_station_start`` (4.71 MB on the live database),
``ix_detection_station_id`` (2.85 MB), ``ix_detection_taxonomic_group``
(1.44 MB) and ``ix_detection_canonical_taxon_id`` (0.76 MB) -- 9.76 MB
combined, measured with ``dbstat`` against a read-only copy of the live
station's ``openobservatory.sqlite`` on 2026-08-09.

ADR-037's original research (2026-08-09, proposed) identified five dead
indexes, including ``ix_detection_detector_id``, and estimated 12.49 MB.
Re-verification against current ``main`` before this revision was written
found that ``plausibility_repair.reconcile_plausibility`` (ADR-032, added
after the ADR-037 research) joins *from* ``detector`` (filtered by
``plugin_id``) *into* ``detection``, and ``EXPLAIN QUERY PLAN`` against the
live database shows SQLite satisfies that join with
``SEARCH detection USING INDEX ix_detection_detector_id``, not a table scan.
That index is therefore kept -- ADR-037 explicitly anticipated this
possibility ("if a query now uses one of these indexes, keep that index and
say so") -- and this revision drops the remaining four, all reconfirmed dead
by the same two-part check (``EXPLAIN QUERY PLAN`` over every query in
``history.py``, ``api/app.py``, ``plausibility_repair.py``, ``retention.py``,
``station.py``, plus a grep for any filter/order-by on the affected columns):

* ``ix_detection_station_start`` (``station_id, event_start_utc``) -- no
  query filters or orders by ``station_id``, and it holds one distinct value
  across the whole live database (61,453+ rows).
* ``ix_detection_station_id`` (``station_id``) -- same column, plain index,
  same finding, and it duplicates the composite above.
* ``ix_detection_taxonomic_group`` (``taxonomic_group``) -- a strict prefix
  of ``ix_detection_group_start`` (``taxonomic_group, event_start_utc``), so
  the query planner never has a reason to choose it; confirmed with
  ``EXPLAIN QUERY PLAN`` on a bare ``taxonomic_group = ?`` filter, which still
  uses ``ix_detection_group_start``.
* ``ix_detection_canonical_taxon_id`` (``canonical_taxon_id``) -- only ever
  appears in a ``SELECT`` column list (``retention.py``'s exemplar scan,
  which reads it into Python after the query runs), never in a ``WHERE`` or
  ``ORDER BY``.

Written as plain ``DROP INDEX`` / ``CREATE INDEX``, following revision
0002's precedent: dropping or creating a plain index needs no table rebuild
on SQLite (unlike a column change), so batch mode buys nothing here, and
both statements are standard SQL supported identically by SQLite and
PostgreSQL 16 (ADR-007).

The downgrade recreates all four indexes, in one command
(``alembic downgrade -1``), so this is fully reversible if a future query
needs one of them back -- ADR-037 flags that as the specific risk worth
watching for ``station_id`` if the station ever becomes multi-station.

Revision ID: 0004_drop_dead_detection_indexes
Revises: 0003_auth_tables
Create Date: 2026-08-09 08:19:56.469376+00:00

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_drop_dead_detection_indexes"
down_revision: str | None = "0003_auth_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_detection_station_start")
    op.execute("DROP INDEX IF EXISTS ix_detection_station_id")
    op.execute("DROP INDEX IF EXISTS ix_detection_taxonomic_group")
    op.execute("DROP INDEX IF EXISTS ix_detection_canonical_taxon_id")


def downgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_detection_station_start "
        "ON detection (station_id, event_start_utc)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_detection_station_id ON detection (station_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_detection_taxonomic_group ON detection (taxonomic_group)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_detection_canonical_taxon_id "
        "ON detection (canonical_taxon_id)"
    )
