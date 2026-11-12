"""Assistant turns and the conversation columns the director needs (conversation C1).

- `assistant_turns`: one row per assistant turn (label, route, gates, offers, timings) for
  analytics and the review inbox. `corpus_id` cascades (CLAUDE.md rule 2); the
  conversation and message links are SET NULL so deleting a transcript keeps the counts.
- `conversations`: `channel` (api | stream | widget | conversation | playground, default
  'api' for existing rows), the widget key and the sha256 of the visitor's opaque session
  token (unique), page URL, UTM, language, the director's per-conversation state and the
  assistant version that last answered.

Downgrade drops both.

Revision ID: phase12_002
Revises: phase12_001
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase12_002"
down_revision: str | None = "phase12_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("channel", sa.String(20), nullable=False, server_default=sa.text("'api'")),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "widget_key_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "widget_keys.id", ondelete="SET NULL", name="fk_conversations_widget_key"
            ),
            nullable=True,
        ),
    )
    op.create_index("ix_conversations_widget_key_id", "conversations", ["widget_key_id"])
    op.add_column("conversations", sa.Column("widget_session_hash", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uq_conversations_widget_session_hash", "conversations", ["widget_session_hash"]
    )
    op.add_column("conversations", sa.Column("page_url", sa.String(2048), nullable=True))
    op.add_column("conversations", sa.Column("utm", postgresql.JSONB(), nullable=True))
    op.add_column("conversations", sa.Column("language", sa.String(16), nullable=True))
    op.add_column("conversations", sa.Column("assistant_state", postgresql.JSONB(), nullable=True))
    op.add_column("conversations", sa.Column("assistant_version", sa.Integer(), nullable=True))

    op.create_table(
        "assistant_turns",
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
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("query_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("assistant_version", sa.Integer(), nullable=True),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("label_source", sa.String(10), nullable=False),
        sa.Column("label_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("script", sa.String(12), nullable=False),
        sa.Column("route", sa.String(24), nullable=False),
        sa.Column("topic_id", sa.String(40), nullable=True),
        sa.Column("top_score", sa.Float(), nullable=True),
        sa.Column("gate_passed", sa.Boolean(), nullable=True),
        sa.Column("claims_removed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claims_flagged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "rules_fired",
            postgresql.ARRAY(sa.String(40)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column(
            "offers",
            postgresql.ARRAY(sa.String(20)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("no_answer", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("question_masked", sa.Text(), nullable=False),
        sa.Column("is_preview", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timings", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_assistant_turns_tenant_id", "assistant_turns", ["tenant_id"])
    op.create_index("ix_assistant_turns_conversation_id", "assistant_turns", ["conversation_id"])
    op.create_index("ix_assistant_turns_query_id", "assistant_turns", ["query_id"])
    op.create_index(
        "ix_assistant_turns_corpus_created", "assistant_turns", ["corpus_id", "created_at"]
    )
    op.create_index("ix_assistant_turns_corpus_label", "assistant_turns", ["corpus_id", "label"])
    op.create_index(
        "ix_assistant_turns_no_answer",
        "assistant_turns",
        ["corpus_id", "created_at"],
        postgresql_where=sa.text("no_answer"),
    )


def downgrade() -> None:
    op.drop_table("assistant_turns")
    for column in ("assistant_version", "assistant_state", "language", "utm", "page_url"):
        op.drop_column("conversations", column)
    op.drop_constraint("uq_conversations_widget_session_hash", "conversations", type_="unique")
    op.drop_column("conversations", "widget_session_hash")
    op.drop_index("ix_conversations_widget_key_id", table_name="conversations")
    op.drop_constraint("fk_conversations_widget_key", "conversations", type_="foreignkey")
    op.drop_column("conversations", "widget_key_id")
    op.drop_column("conversations", "channel")
