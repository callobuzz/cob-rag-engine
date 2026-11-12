"""Phase 9: daily usage rollup per corpus and traffic source.

Revision ID: phase9_002
Revises: phase9_001
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase9_002"
down_revision: str | None = "phase9_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_daily",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column(
            "corpus_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("corpora.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "completion_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("day", "corpus_id", "source", name="uq_usage_daily_day_corpus_source"),
    )
    op.create_index("ix_usage_daily_day", "usage_daily", ["day"])
    op.create_index("ix_usage_daily_corpus_id", "usage_daily", ["corpus_id"])
    op.create_index("ix_usage_daily_tenant_id", "usage_daily", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_usage_daily_tenant_id", table_name="usage_daily")
    op.drop_index("ix_usage_daily_corpus_id", table_name="usage_daily")
    op.drop_index("ix_usage_daily_day", table_name="usage_daily")
    op.drop_table("usage_daily")
