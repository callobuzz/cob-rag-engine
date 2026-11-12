"""Self-service plan choice (P6): which plans a business may pick, and the plan it asked for.

Revision ID: phase20_002
Revises: phase20_001
Create Date: 2026-10-16

- `plans.self_service` (default false): a plan a business may choose itself, from the sign-up
  link (`/signup?plan=...`) and the Plan & billing page. Every plan that exists today stays
  operator-assigned only; the seed command marks the seeded Free, Standard and Pro.
- `tenants.requested_plan_id` (nullable, SET NULL): the plan chosen at sign-up while the
  workspace waits for approval; the operator's activation applies it. Deleting the plan only
  forgets the wish.

Downgrade drops both columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase20_002"
down_revision: str | None = "phase20_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("self_service", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "tenants",
        sa.Column("requested_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_tenants_requested_plan_id_plans",
        "tenants",
        "plans",
        ["requested_plan_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_tenants_requested_plan_id_plans", "tenants", type_="foreignkey")
    op.drop_column("tenants", "requested_plan_id")
    op.drop_column("plans", "self_service")
