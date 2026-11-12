"""The operator workspace and the Internal and Pilot plans.

- `tenants.is_operator` (bool, default false; at most one true, partial unique index
  `uq_tenants_operator`): the operator's own workspace (the house account). Bootstrap
  creates it (`accounts/operator.py`), at every start and idempotently, so installs that
  already have a super admin get theirs on their next start; this migration only adds the
  column.
- `plans.is_internal` (bool, default false; at most one true, `uq_plans_internal`) and the
  seeded **Internal** plan: every limit unlimited, locked, assignable only to the operator
  workspace. If a plan already has the name "Internal" the seeded one is called
  "Internal (operator)".
- A seeded **Pilot** plan: 1 knowledge base, 300 questions a month, 50 documents, 1 widget
  key. An ordinary plan (not default, not built in, editable and deletable); skipped if a
  plan named "Pilot" already exists.
- `engine_settings.operator_workspace_removed`: set when the operator deliberately deletes
  their workspace, so bootstrap does not bring it back.

Downgrade drops the flags and the Internal plan (a tenant on it moves to the default plan);
the operator workspace and Pilot stay as the ordinary tenant and plan they are.

Revision ID: phase11_009
Revises: phase11_008
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase11_009"
down_revision: str | None = "phase11_008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INTERNAL_NAME = "Internal"
INTERNAL_FALLBACK_NAME = "Internal (operator)"
INTERNAL_DESCRIPTION = "The operator's own workspace: unlimited and free"
PILOT_NAME = "Pilot"
PILOT_DESCRIPTION = "Free trial: one knowledge base and a small monthly allowance"


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("is_operator", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "uq_tenants_operator",
        "tenants",
        ["is_operator"],
        unique=True,
        postgresql_where=sa.text("is_operator"),
    )
    op.add_column(
        "plans",
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index(
        "uq_plans_internal",
        "plans",
        ["is_internal"],
        unique=True,
        postgresql_where=sa.text("is_internal"),
    )
    op.add_column(
        "engine_settings",
        sa.Column(
            "operator_workspace_removed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    bind = op.get_bind()
    taken = {row[0] for row in bind.execute(sa.text("SELECT name FROM plans")).all()}
    internal_name = INTERNAL_FALLBACK_NAME if INTERNAL_NAME in taken else INTERNAL_NAME
    bind.execute(
        sa.text(
            "INSERT INTO plans (name, description, is_default, is_active, is_internal) "
            "VALUES (:name, :description, false, true, true)"
        ).bindparams(name=internal_name, description=INTERNAL_DESCRIPTION)
    )
    if PILOT_NAME not in taken:
        bind.execute(
            sa.text(
                "INSERT INTO plans (name, description, max_knowledge_bases, monthly_questions, "
                "max_documents, max_widget_keys, is_default, is_active) "
                "VALUES (:name, :description, 1, 300, 50, 1, false, true)"
            ).bindparams(name=PILOT_NAME, description=PILOT_DESCRIPTION)
        )


def downgrade() -> None:
    op.execute(
        "UPDATE tenants SET plan_id = (SELECT id FROM plans WHERE is_default) "
        "WHERE plan_id IN (SELECT id FROM plans WHERE is_internal)"
    )
    op.execute("DELETE FROM plans WHERE is_internal")
    op.drop_column("engine_settings", "operator_workspace_removed")
    op.drop_index("uq_plans_internal", table_name="plans")
    op.drop_column("plans", "is_internal")
    op.drop_index("uq_tenants_operator", table_name="tenants")
    op.drop_column("tenants", "is_operator")
