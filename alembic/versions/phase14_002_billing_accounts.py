"""Billing: versioned plan pricing and per-workspace billing accounts.

Revision ID: phase14_002
Revises: phase14_001
Create Date: 2026-10-04

`plan_pricing` holds how customers on a plan pay; every change inserts a new version and moves
`is_current` (one current row per plan, a partial unique index). A plan with no row is not
billed, so installs upgrade with no behaviour change. `billing_accounts` is one row per tenant,
created lazily by the first admin or owner read (no backfill over every tenant): the currency,
the customer's overrides of the plan's pricing, limit overrides, the pause override and the
columns the wallet ledger (a later migration) keeps up to date.

Downgrade drops both tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase14_002"
down_revision: str | None = "phase14_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = (
    "subscription_fee_minor",
    "per_question_minor",
    "byok_per_question_minor",
    "overage_per_question_minor",
    "credit_limit_minor",
    "grace_minor",
    "included_credit_minor",
)


def _zero_bigint(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.BigInteger(), server_default=sa.text("0"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "plan_pricing",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        _zero_bigint("subscription_fee_minor"),
        sa.Column(
            "billing_period", sa.String(8), server_default=sa.text("'monthly'"), nullable=False
        ),
        sa.Column("trial_days", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "usage_mode", sa.String(12), server_default=sa.text("'included'"), nullable=False
        ),
        _zero_bigint("per_question_minor"),
        sa.Column("markup_bp", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("byok_mode", sa.String(12), server_default=sa.text("'free'"), nullable=False),
        _zero_bigint("byok_per_question_minor"),
        sa.Column(
            "byok_counts_toward_caps", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("overage", sa.String(8), server_default=sa.text("'block'"), nullable=False),
        sa.Column(
            "overage_mode", sa.String(12), server_default=sa.text("'per_question'"), nullable=False
        ),
        _zero_bigint("overage_per_question_minor"),
        sa.Column(
            "payment_type", sa.String(8), server_default=sa.text("'prepaid'"), nullable=False
        ),
        _zero_bigint("credit_limit_minor"),
        _zero_bigint("grace_minor"),
        _zero_bigint("included_credit_minor"),
        sa.Column(
            "included_credit_expires", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "alert_thresholds_pct",
            postgresql.ARRAY(sa.SmallInteger()),
            server_default=sa.text("'{20,5,0}'::smallint[]"),
            nullable=False,
        ),
        sa.Column(
            "members_see_money", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column("created_by", sa.String(320), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["plans.id"], name="fk_plan_pricing_plan_id_plans", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("plan_id", "version", name="uq_plan_pricing_plan_version"),
        sa.CheckConstraint("currency in ('INR','USD')", name="ck_plan_pricing_currency"),
        sa.CheckConstraint("billing_period in ('monthly','yearly')", name="ck_plan_pricing_period"),
        sa.CheckConstraint(
            "usage_mode in ('included','per_question','cost_plus')", name="ck_plan_pricing_usage"
        ),
        sa.CheckConstraint("byok_mode in ('free','per_question')", name="ck_plan_pricing_byok"),
        sa.CheckConstraint("overage in ('block','charge','allow')", name="ck_plan_pricing_overage"),
        sa.CheckConstraint(
            "overage_mode in ('per_question','cost_plus')", name="ck_plan_pricing_overage_mode"
        ),
        sa.CheckConstraint(
            "payment_type in ('prepaid','postpaid')", name="ck_plan_pricing_payment_type"
        ),
        sa.CheckConstraint("version >= 1", name="ck_plan_pricing_version"),
        sa.CheckConstraint("trial_days BETWEEN 0 AND 365", name="ck_plan_pricing_trial_days"),
        sa.CheckConstraint("markup_bp BETWEEN 0 AND 100000", name="ck_plan_pricing_markup"),
        *(sa.CheckConstraint(f"{c} >= 0", name=f"ck_plan_pricing_{c}") for c in _MONEY),
    )
    op.create_index(
        "uq_plan_pricing_current",
        "plan_pricing",
        ["plan_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )

    op.create_table(
        "billing_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "overrides",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "limit_overrides",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "pause_override", sa.String(12), server_default=sa.text("'auto'"), nullable=False
        ),
        sa.Column("billing_day", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("period_anchor", sa.Date(), nullable=True),
        sa.Column("next_charge_on", sa.Date(), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        _zero_bigint("balance_minor"),
        _zero_bigint("ledger_seq"),
        sa.Column("usage_residue_micro", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("alert_level", sa.SmallInteger(), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        _zero_bigint("last_top_up_minor"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_billing_accounts_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", name="uq_billing_accounts_tenant"),
        sa.CheckConstraint("currency in ('INR','USD')", name="ck_billing_accounts_currency"),
        sa.CheckConstraint(
            "pause_override in ('auto','never','force')", name="ck_billing_accounts_pause"
        ),
        sa.CheckConstraint("billing_day BETWEEN 1 AND 28", name="ck_billing_accounts_day"),
        sa.CheckConstraint(
            "usage_residue_micro BETWEEN 0 AND 9999", name="ck_billing_accounts_residue"
        ),
    )


def downgrade() -> None:
    op.drop_table("billing_accounts")
    op.drop_index("uq_plan_pricing_current", table_name="plan_pricing")
    op.drop_table("plan_pricing")
