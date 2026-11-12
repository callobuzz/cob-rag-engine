"""Drop the `license_state` table (phase21_001).

Revision ID: phase21_001
Revises: phase20_003
Create Date: 2026-11-12

The licensing system is gone: the engine is open source (AGPL-3.0) and every feature works for
everyone. Nothing refers to `license_state` (no foreign keys), so the table can go.

The downgrade makes the table again, exactly as `phase18_001` made it, with its one row (a new
install id and no token), because the code before this change reads `id = 1` and expects it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase21_001"
down_revision: str | None = "phase20_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("license_state")


def downgrade() -> None:
    op.create_table(
        "license_state",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("install_id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.Text(), nullable=True),
        sa.Column("nonce", sa.String(64), nullable=True),
        sa.Column("refusal", sa.Text(), nullable=True),
        sa.Column("refusal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(40), nullable=True),
        sa.Column("last_state", sa.String(12), nullable=True),
        sa.Column("latest_version", sa.String(32), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_license_state_single_row"),
    )
    op.execute("INSERT INTO license_state (id, install_id) VALUES (1, gen_random_uuid())")
