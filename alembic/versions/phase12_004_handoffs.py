"""Handoffs: a visitor offered or taking a way to reach a person (conversation layer C5).

One row per handoff: the channel (WhatsApp, call, callback, e-mail, contact page, booking, or
`offered` for a card shown on an API surface), the reason, an optional Fernet-encrypted
summary, the office state at the time, and a `new`/`handled` status. It cascades from the
knowledge base and the tenant; a deleted conversation or lead leaves the handoff with NULL.

Downgrade drops the table.

Revision ID: phase12_004
Revises: phase12_007
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase12_004"
down_revision: str | None = "phase12_007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handoffs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "corpus_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("corpora.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("summary_encrypted", sa.Text(), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("page_url", sa.String(2048), nullable=True),
        sa.Column("office_open", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default=sa.text("'new'")),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("status IN ('new','handled')", name="ck_handoffs_status"),
    )
    op.create_index("ix_handoffs_corpus_id", "handoffs", ["corpus_id"])
    op.create_index("ix_handoffs_tenant_id", "handoffs", ["tenant_id"])
    op.create_index("ix_handoffs_corpus_created", "handoffs", ["corpus_id", "created_at"])
    op.create_index("ix_handoffs_tenant_created", "handoffs", ["tenant_id", "created_at"])
    op.create_index(
        "ix_handoffs_new",
        "handoffs",
        ["corpus_id"],
        postgresql_where=sa.text("status = 'new'"),
    )


def downgrade() -> None:
    op.drop_table("handoffs")
