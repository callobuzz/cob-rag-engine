"""COB action proposals: the cards COB shows before it changes anything.

Revision ID: phase13_003
Revises: phase13_002
Create Date: 2026-10-04

COB never changes anything by itself. It stores a proposal (who, which workspace, which
conversation, which tool, which ids) and shows the person a card; only a confirm from the same
person, in the same workspace, within ten minutes, runs it, once. `status` moves
`pending` -> `executing` in a single UPDATE, which is what makes a card single use. The table
holds ids and names only; a rotated key's secret is never stored here.

Downgrade drops the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase13_003"
down_revision: str | None = "phase13_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cob_action_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("args", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("risk", sa.String(8), nullable=False),
        sa.Column("confirm_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(12), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status in ('pending','executing','done','failed','cancelled','expired')",
            name="ck_cob_action_proposals_status",
        ),
    )
    op.create_index(
        "ix_cob_action_proposals_user_created", "cob_action_proposals", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_cob_action_proposals_user_created", table_name="cob_action_proposals")
    op.drop_table("cob_action_proposals")
