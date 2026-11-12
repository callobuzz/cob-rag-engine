"""Leads, their consent records, and knowledge-base webhooks (conversation layer C4).

- `leads`: contact data only inside `fields_encrypted` (Fernet); keyed hashes for exact
  search; `retention_until` drives the daily purge; `notified_at` makes the notification
  task idempotent.
- `lead_consents`: what the visitor agreed to; survives erasure (`lead_id` SET NULL).
- `webhook_config.corpus_id`: a knowledge base's own webhook. Lead and handoff events go
  only to these.

Downgrade drops both tables and deletes knowledge-base webhooks before dropping the column.

Revision ID: phase12_003
Revises: phase12_006
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase12_003"
down_revision: str | None = "phase12_006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fk(target: str, ondelete: str) -> sa.ForeignKey:
    return sa.ForeignKey(target, ondelete=ondelete)


def upgrade() -> None:
    uuid_type = postgresql.UUID(as_uuid=True)
    op.create_table(
        "leads",
        sa.Column("id", uuid_type, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("corpus_id", uuid_type, _fk("corpora.id", "CASCADE"), nullable=False),
        sa.Column("tenant_id", uuid_type, _fk("tenants.id", "CASCADE"), nullable=False),
        sa.Column("conversation_id", uuid_type, _fk("conversations.id", "SET NULL"), nullable=True),
        sa.Column("widget_key_id", uuid_type, _fk("widget_keys.id", "SET NULL"), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("fields_encrypted", sa.Text(), nullable=False),
        sa.Column("phone_hash", sa.String(64), nullable=True),
        sa.Column("email_hash", sa.String(64), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("page_url", sa.String(2048), nullable=True),
        sa.Column("utm", postgresql.JSONB(), nullable=True),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("source_label", sa.String(32), nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(40)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("callback_window", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default=sa.text("'new'")),
        sa.Column("qualification", sa.String(12), nullable=True),
        sa.Column("assignee_user_id", uuid_type, _fk("users.id", "SET NULL"), nullable=True),
        sa.Column("notes_encrypted", sa.Text(), nullable=True),
        sa.Column("summary_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "sources_consulted",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('new','contacted','won','lost','spam')", name="ck_leads_status"
        ),
        sa.CheckConstraint(
            "qualification IS NULL OR qualification IN ('qualified','partial','unqualified')",
            name="ck_leads_qualification",
        ),
    )
    for column in (
        "corpus_id",
        "tenant_id",
        "conversation_id",
        "phone_hash",
        "email_hash",
        "retention_until",
    ):
        op.create_index(f"ix_leads_{column}", "leads", [column])
    op.create_index("ix_leads_corpus_created", "leads", ["corpus_id", "created_at"])

    op.create_table(
        "lead_consents",
        sa.Column("id", uuid_type, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("lead_id", uuid_type, _fk("leads.id", "SET NULL"), nullable=True),
        sa.Column("corpus_id", uuid_type, _fk("corpora.id", "CASCADE"), nullable=False),
        sa.Column("notice_version", sa.Integer(), nullable=False),
        sa.Column("wording_hash", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(300), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column(
            "consented_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_lead_consents_lead_id", "lead_consents", ["lead_id"])
    op.create_index("ix_lead_consents_corpus_id", "lead_consents", ["corpus_id"])

    op.add_column("webhook_config", sa.Column("corpus_id", uuid_type, nullable=True))
    op.create_foreign_key(
        "fk_webhook_config_corpus_id",
        "webhook_config",
        "corpora",
        ["corpus_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_webhook_config_corpus_id", "webhook_config", ["corpus_id"])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM webhook_config WHERE corpus_id IS NOT NULL"))
    op.drop_index("ix_webhook_config_corpus_id", table_name="webhook_config")
    op.drop_constraint("fk_webhook_config_corpus_id", "webhook_config", type_="foreignkey")
    op.drop_column("webhook_config", "corpus_id")
    op.drop_table("lead_consents")
    op.drop_table("leads")
