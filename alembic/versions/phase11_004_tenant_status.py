"""Tenant status replaces is_active (accounts A4).

`tenants.status` (`pending` | `active` | `suspended`) replaces the `is_active` boolean,
backfilled from it (true -> active, false -> suspended). New columns `activated_at`
(backfilled from `created_at` for active tenants), `activation_note` and
`created_by_user_id` (the signer-up; SET NULL when that user is deleted). Downgrade
restores `is_active` from the status (only `active` is true) and drops the new columns.

Revision ID: phase11_004
Revises: phase11_003
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase11_004"
down_revision: str | None = "phase11_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
    )
    op.add_column("tenants", sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column("activation_note", sa.Text(), nullable=True))
    op.add_column(
        "tenants",
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_tenants_created_by_user_id_users"
            ),
            nullable=True,
        ),
    )
    op.execute("UPDATE tenants SET status = CASE WHEN is_active THEN 'active' ELSE 'suspended' END")
    op.execute("UPDATE tenants SET activated_at = created_at WHERE status = 'active'")
    op.create_check_constraint(
        "ck_tenants_status", "tenants", "status in ('pending','active','suspended')"
    )
    op.create_index("ix_tenants_status", "tenants", ["status"])
    op.drop_column("tenants", "is_active")


def downgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.execute("UPDATE tenants SET is_active = (status = 'active')")
    op.drop_index("ix_tenants_status", table_name="tenants")
    op.drop_constraint("ck_tenants_status", "tenants", type_="check")
    op.drop_constraint("fk_tenants_created_by_user_id_users", "tenants", type_="foreignkey")
    op.drop_column("tenants", "created_by_user_id")
    op.drop_column("tenants", "activation_note")
    op.drop_column("tenants", "activated_at")
    op.drop_column("tenants", "status")
