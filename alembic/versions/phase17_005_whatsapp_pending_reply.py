"""WhatsApp: keep a written reply while a throttled send waits (phase17_005).

Revision ID: phase17_005
Revises: phase17_004
Create Date: 2026-10-05

- `channel_events.pending_reply_encrypted`: the reply the model already wrote (and how many of its
  messages went out), so a retry after a throttle sends the rest and never runs the model again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase17_005"
down_revision: str | None = "phase17_004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("channel_events", sa.Column("pending_reply_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("channel_events", "pending_reply_encrypted")
