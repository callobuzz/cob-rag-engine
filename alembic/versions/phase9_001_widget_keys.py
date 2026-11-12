"""Phase 9: widget keys — publishable pk_ keys for the embeddable website chat.

Revision ID: phase9_001
Revises: phase8_003
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase9_001"
down_revision: str | None = "phase8_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "widget_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "corpus_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("corpora.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("public_key", sa.String(64), nullable=False),
        sa.Column(
            "allowed_origins",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "rate_limit_per_minute", sa.Integer(), nullable=False, server_default=sa.text("10")
        ),
        sa.Column(
            "daily_message_limit", sa.Integer(), nullable=False, server_default=sa.text("1000")
        ),
        sa.Column(
            "appearance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_widget_keys_corpus_id", "widget_keys", ["corpus_id"])
    op.create_index("ix_widget_keys_public_key", "widget_keys", ["public_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_widget_keys_public_key", table_name="widget_keys")
    op.drop_index("ix_widget_keys_corpus_id", table_name="widget_keys")
    op.drop_table("widget_keys")
