"""Mark the plan the engine seeded as built in (accounts A8).

`plans.is_builtin` (bool, default false; at most one true, partial unique index
`uq_plans_builtin`): the "Standard" plan that `phase11_006` inserted, so a UI can label it
and tell it apart from the operator's own plans. Read-only in the API.

The seeded row is found by what `phase11_006` wrote: its exact description, else its name,
the earliest such row. An operator who renamed it and changed its description keeps an
unmarked plan (nothing else depends on the flag); one who deleted it has none.

Revision ID: phase11_008
Revises: phase11_007
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase11_008"
down_revision: str | None = "phase11_007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEED_NAME = "Standard"
SEED_DESCRIPTION = "Unlimited — the default until you define your own plans"


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "uq_plans_builtin",
        "plans",
        ["is_builtin"],
        unique=True,
        postgresql_where=sa.text("is_builtin"),
    )
    op.execute(
        sa.text(
            """
            UPDATE plans SET is_builtin = true WHERE id = (
                SELECT id FROM plans
                WHERE description = :description OR name = :name
                ORDER BY (description = :description) DESC, created_at, id
                LIMIT 1
            )
            """
        ).bindparams(description=SEED_DESCRIPTION, name=SEED_NAME)
    )


def downgrade() -> None:
    op.drop_index("uq_plans_builtin", table_name="plans")
    op.drop_column("plans", "is_builtin")
