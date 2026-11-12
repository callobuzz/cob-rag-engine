"""Licensing: the one-row `license_state` table (phase18_001).

Revision ID: phase18_001
Revises: phase17_006
Create Date: 2026-10-07

- `license_state` holds this install's id (made here, so it survives image rebuilds and container
  recreation), the signed token and nonce the licence server returned, a signed refusal if one was
  received, and the bookkeeping of the weekly re-check. Exactly one row (`id = 1`).
- No tenant or corpus affinity and no foreign keys: it describes the engine, not a customer.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase18_001"
down_revision: str | None = "phase17_006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_table("license_state")
