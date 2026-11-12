"""Phase 5: add retrieval_config JSONB to corpora.

Revision ID: phase5_001
Revises: phase4_001
Create Date: 2026-03-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase5_001"
down_revision: str | None = "phase4_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "corpora",
        sa.Column(
            "retrieval_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Per-corpus retrieval overrides: top_k, mmr, reranking, validation, cache, etc.",
        ),
    )


def downgrade() -> None:
    op.drop_column("corpora", "retrieval_config")
