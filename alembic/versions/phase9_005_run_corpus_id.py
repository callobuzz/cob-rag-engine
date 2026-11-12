"""Phase 9: eval and optimization runs remember the corpus they were scoped to.

Admin eval and optimize triggers take a corpus_id, but the rows had nowhere to keep
it, so every list and detail answered `corpus_id: null`. Nullable because a run
over every corpus is legitimate; CASCADE like every other corpus-affine table.

Revision ID: phase9_005
Revises: phase9_004
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase9_005"
down_revision: str | None = "phase9_004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("eval_run", "optimization_runs")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "corpus_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("corpora.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        op.create_index(f"ix_{table}_corpus_id", table, ["corpus_id"])


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_corpus_id", table_name=table)
        op.drop_column(table, "corpus_id")
