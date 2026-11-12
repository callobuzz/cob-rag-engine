"""WhatsApp review fixes: claim time, retry, follow-up skip, chat to conversation (phase17_004).

Revision ID: phase17_004
Revises: phase17_003
Create Date: 2026-10-05

- `channel_events.claimed_at`: when a worker took the event; the stall sweep counts from it.
- `channel_events.retry_at` / `attempts`: a send Meta throttled is queued again after a backoff.
- `channel_threads.follow_up_skipped_at`: a follow-up that can never be sent leaves the queue.
- `conversations.channel_thread_id`: the WhatsApp chat of a conversation (ON DELETE CASCADE), so
  erasing a customer deletes every conversation of the chat, not only the latest. Backfilled
  from `channel_threads.conversation_id`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase17_004"
down_revision: str | None = "phase17_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "channel_events", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "channel_events", sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "channel_events",
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "channel_threads",
        sa.Column("follow_up_skipped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("channel_thread_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_conversations_channel_thread",
        "conversations",
        "channel_threads",
        ["channel_thread_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_conversations_channel_thread_id", "conversations", ["channel_thread_id"])
    op.execute(
        "UPDATE conversations c SET channel_thread_id = t.id "
        "FROM channel_threads t WHERE t.conversation_id = c.id"
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_channel_thread_id", table_name="conversations")
    op.drop_constraint("fk_conversations_channel_thread", "conversations", type_="foreignkey")
    op.drop_column("conversations", "channel_thread_id")
    op.drop_column("channel_threads", "follow_up_skipped_at")
    op.drop_column("channel_events", "attempts")
    op.drop_column("channel_events", "retry_at")
    op.drop_column("channel_events", "claimed_at")
