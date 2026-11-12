"""WhatsApp channel: connections, chats, webhook events and the plan flag (TV6, phase17_003).

Revision ID: phase17_003
Revises: phase17_002
Create Date: 2026-10-05

- `plans.channels` (JSONB, default `{}`): what channels a plan includes. Missing keys mean the
  defaults in `channels/policy.py` (WhatsApp off, Meta fees passed through).
- `channel_connections`: one number per knowledge base (and kind), `phone_number_id` unique across
  the engine so an inbound message is routed unambiguously. Token and PIN are Fernet tokens.
- `channel_threads`: one chat per (connection, contact); contact stored encrypted with a keyed
  hash for lookup.
- `channel_events`: every webhook event, unique per (connection, external id) so Meta's retry is
  stored once. Purged after seven days by a beat task.

Every table with corpus affinity has a `corpus_id` FK with ON DELETE CASCADE.
Downgrade drops the three tables (events first) and the plan column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase17_003"
down_revision: str | None = "phase17_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_GEN = sa.text("gen_random_uuid()")
_EMPTY = sa.text("'{}'::jsonb")


def _ts(name: str, *, nullable: bool = True, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=_NOW if default else None,
        nullable=nullable,
    )


def upgrade() -> None:
    op.add_column(
        "plans", sa.Column("channels", postgresql.JSONB(), server_default=_EMPTY, nullable=False)
    )

    op.create_table(
        "channel_connections",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("corpus_id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("kind", sa.String(16), server_default=sa.text("'whatsapp'"), nullable=False),
        sa.Column("status", sa.String(12), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("phone_number_id", sa.String(20), nullable=False),
        sa.Column("waba_id", sa.String(20), nullable=False),
        sa.Column("display_phone", sa.String(32), nullable=False),
        sa.Column("verified_name", sa.String(200), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("token_hint", sa.String(8), nullable=False),
        sa.Column("pin_encrypted", sa.Text(), nullable=True),
        sa.Column("setup", sa.String(10), nullable=False),
        sa.Column("coexistence", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("meta_billing", sa.String(10), nullable=False),
        sa.Column("settings", postgresql.JSONB(), server_default=_EMPTY, nullable=False),
        sa.Column("last_error_code", sa.String(40), nullable=True),
        _ts("last_inbound_at"),
        sa.Column("created_by", sa.String(320), nullable=False),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["corpus_id"],
            ["corpora.id"],
            name="fk_channel_connections_corpus_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_channel_connections_tenant_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("corpus_id", "kind", name="uq_channel_connections_corpus_kind"),
        sa.UniqueConstraint("phone_number_id", name="uq_channel_connections_phone_number_id"),
        sa.CheckConstraint("kind in ('whatsapp')", name="ck_channel_connections_kind"),
        sa.CheckConstraint(
            "status in ('pending','active','error','disabled')",
            name="ck_channel_connections_status",
        ),
        sa.CheckConstraint("setup in ('embedded','manual')", name="ck_channel_connections_setup"),
        sa.CheckConstraint(
            "meta_billing in ('business','deployer')", name="ck_channel_connections_meta_billing"
        ),
    )
    op.create_index("ix_channel_connections_corpus_id", "channel_connections", ["corpus_id"])
    op.create_index("ix_channel_connections_tenant_id", "channel_connections", ["tenant_id"])

    op.create_table(
        "channel_threads",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("connection_id", _UUID, nullable=False),
        sa.Column("corpus_id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("contact_hash", sa.String(64), nullable=False),
        sa.Column("contact_encrypted", sa.Text(), nullable=False),
        sa.Column("profile_name_encrypted", sa.Text(), nullable=True),
        sa.Column("conversation_id", _UUID, nullable=True),
        _ts("last_inbound_at"),
        _ts("last_outbound_at"),
        _ts("bot_paused_until"),
        sa.Column("paused_by", sa.String(10), nullable=True),
        _ts("opted_in_at"),
        _ts("opted_out_at"),
        _ts("opt_in_asked_at"),
        _ts("follow_up_sent_at"),
        sa.Column("unread_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["channel_connections.id"],
            name="fk_channel_threads_conn",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["corpus_id"], ["corpora.id"], name="fk_channel_threads_corpus_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_channel_threads_tenant_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_channel_threads_conv",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("connection_id", "contact_hash", name="uq_channel_threads_contact"),
        sa.CheckConstraint(
            "paused_by is null or paused_by in ('echo','dashboard')",
            name="ck_channel_threads_paused_by",
        ),
    )
    op.create_index(
        "ix_channel_threads_corpus_inbound",
        "channel_threads",
        ["corpus_id", sa.text("last_inbound_at DESC")],
    )

    op.create_table(
        "channel_events",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("connection_id", _UUID, nullable=False),
        sa.Column("corpus_id", _UUID, nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("thread_id", _UUID, nullable=True),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), server_default=sa.text("'received'"), nullable=False),
        _ts("received_at", default=True),
        _ts("processed_at"),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["channel_connections.id"],
            name="fk_channel_events_conn",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["corpus_id"], ["corpora.id"], name="fk_channel_events_corpus_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["channel_threads.id"],
            name="fk_channel_events_thread",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("connection_id", "external_id", name="uq_channel_events_external"),
        sa.CheckConstraint(
            "kind in ('message','echo','status','other')", name="ck_channel_events_kind"
        ),
        sa.CheckConstraint(
            "status in ('received','processing','done','ignored','failed')",
            name="ck_channel_events_status",
        ),
    )
    op.create_index("ix_channel_events_thread_status", "channel_events", ["thread_id", "status"])
    op.create_index("ix_channel_events_received_at", "channel_events", ["received_at"])


def downgrade() -> None:
    op.drop_table("channel_events")
    op.drop_table("channel_threads")
    op.drop_table("channel_connections")
    op.drop_column("plans", "channels")
