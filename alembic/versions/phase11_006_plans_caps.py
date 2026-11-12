"""Plans and caps (accounts A6).

- `plans`: four limits, each NULL = unlimited (knowledge bases, questions per month,
  active documents, widget keys); `is_default` under a partial unique index, so at most
  one plan is the default. Seeds "Standard" (every limit unlimited) as the default.
- `tenants.plan_id` (FK RESTRICT, NOT NULL): every existing tenant gets Standard.
- `tenants.max_corpora` becomes nullable with no default: NULL follows the plan, a value
  overrides it (existing values are kept, so existing tenants behave exactly as before).
- `tenant_question_counts(tenant_id, month)`: answered questions per UTC month, CASCADE
  with the tenant, backfilled for the current month from `usage_daily`.

Revision ID: phase11_006
Revises: phase11_005
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase11_006"
down_revision: str | None = "phase11_005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CAPS = ("max_knowledge_bases", "monthly_questions", "max_documents", "max_widget_keys")


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *(sa.Column(cap, sa.Integer(), nullable=True) for cap in _CAPS),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("name", name="uq_plans_name"),
        *(
            sa.CheckConstraint(f"{cap} IS NULL OR {cap} >= 1", name=f"ck_plans_{cap}")
            for cap in _CAPS
        ),
    )
    op.create_index(
        "uq_plans_default",
        "plans",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.execute(
        "INSERT INTO plans (name, description, is_default, is_active) VALUES "
        "('Standard', 'Unlimited — the default until you define your own plans', true, true)"
    )

    op.add_column("tenants", sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute("UPDATE tenants SET plan_id = (SELECT id FROM plans WHERE is_default)")
    op.alter_column("tenants", "plan_id", nullable=False)
    op.create_foreign_key(
        "fk_tenants_plan_id_plans", "tenants", "plans", ["plan_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("ix_tenants_plan_id", "tenants", ["plan_id"])

    op.alter_column(
        "tenants",
        "max_corpora",
        existing_type=sa.Integer(),
        nullable=True,
        server_default=None,
        comment="Per-tenant knowledge-base limit overriding the plan's; NULL follows the plan",
        existing_comment="Maximum number of corpora this tenant may own",
    )

    op.create_table(
        "tenant_question_counts",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("month", sa.Date(), primary_key=True, comment="First day of the month (UTC)"),
        sa.Column("questions", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
    )
    # The current month so far, so an operator who adds a cap today counts from the 1st.
    op.execute(
        "INSERT INTO tenant_question_counts (tenant_id, month, questions) "
        "SELECT tenant_id, date_trunc('month', (now() AT TIME ZONE 'utc'))::date, "
        "sum(requests) FROM usage_daily "
        "WHERE day >= date_trunc('month', (now() AT TIME ZONE 'utc'))::date "
        "GROUP BY tenant_id HAVING sum(requests) > 0"
    )


def downgrade() -> None:
    op.drop_table("tenant_question_counts")
    op.execute("UPDATE tenants SET max_corpora = 10 WHERE max_corpora IS NULL")
    op.alter_column(
        "tenants",
        "max_corpora",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("10"),
        comment="Maximum number of corpora this tenant may own",
        existing_comment=(
            "Per-tenant knowledge-base limit overriding the plan's; NULL follows the plan"
        ),
    )
    op.drop_index("ix_tenants_plan_id", table_name="tenants")
    op.drop_constraint("fk_tenants_plan_id_plans", "tenants", type_="foreignkey")
    op.drop_column("tenants", "plan_id")
    op.drop_index("uq_plans_default", table_name="plans")
    op.drop_table("plans")
