"""Phase 7: Memory model extensions for client-application integration.

Adds user_id, source, confidence, invalidated_at columns to memories table.
Supports per-user memory isolation and soft-delete.

Revision ID: phase7_004
Revises: phase7_003
Create Date: 2026-05-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase7_004"
down_revision: str | None = "phase7_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("user_id", sa.String(255), nullable=True))
    op.add_column("memories", sa.Column("source", sa.String(20), nullable=True))
    op.add_column("memories", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column(
        "memories",
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_memories_user_id", "memories", ["user_id"])
    op.create_index("ix_memories_invalidated_at", "memories", ["invalidated_at"])


def downgrade() -> None:
    op.drop_index("ix_memories_invalidated_at", table_name="memories")
    op.drop_index("ix_memories_user_id", table_name="memories")
    op.drop_column("memories", "invalidated_at")
    op.drop_column("memories", "confidence")
    op.drop_column("memories", "source")
    op.drop_column("memories", "user_id")
