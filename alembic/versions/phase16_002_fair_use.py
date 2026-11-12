"""Fair use: per-plan limits and policy, per-business overrides, counters and alerts (PIP3).

Revision ID: phase16_002
Revises: phase16_001
Create Date: 2026-10-05

`plans.fair_use_limits` / `fair_use_policy` hold a plan's limits and what happens past them
(JSONB, a missing key = the default in `fairuse/limits.py`, so a new limit needs no migration).
`tenant_pipeline_settings` gains the business's own overrides and the state the alert task last
stored. `fair_use_counters` counts what only the engine can count (pages learned, fetches,
uploads, decisions) per business and UTC day. `fair_use_alerts` keeps one row per crossing of a
limit level in a period, so a notice goes out once however many processes race (`tenant_id`
NULL is the engine-wide spend rail; NULLS NOT DISTINCT makes that row unique too).
`usage_charges.waived` also accepts 'downgraded' (answered on the fallback profile because the
balance ran out: metered, not charged).

Data: the seeded "Free" plan (phase16_001) gets 30 questions an hour, 300 a day and 50 website
fetches a day. A plan the deployer already had is never touched.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase16_002"
down_revision: str | None = "phase16_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_EMPTY = sa.text("'{}'::jsonb")
_FREE_LIMITS = {"questions_per_hour": 30, "questions_per_day": 300, "fetches_per_day": 50}


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("fair_use_limits", postgresql.JSONB(), server_default=_EMPTY, nullable=False),
    )
    op.add_column(
        "plans",
        sa.Column("fair_use_policy", postgresql.JSONB(), server_default=_EMPTY, nullable=False),
    )
    op.add_column(
        "tenant_pipeline_settings",
        sa.Column("fair_use_overrides", postgresql.JSONB(), server_default=_EMPTY, nullable=False),
    )
    op.add_column(
        "tenant_pipeline_settings",
        sa.Column("policy_overrides", postgresql.JSONB(), server_default=_EMPTY, nullable=False),
    )
    op.add_column(
        "tenant_pipeline_settings",
        sa.Column("state", sa.String(12), server_default=sa.text("'ok'"), nullable=False),
    )
    op.add_column(
        "tenant_pipeline_settings",
        sa.Column("state_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_tenant_pipeline_settings_state",
        "tenant_pipeline_settings",
        "state in ('ok','near','throttled','stopped')",
    )

    op.create_table(
        "fair_use_counters",
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("value", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "day", "metric"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "fair_use_alerts",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=True),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("period_key", sa.String(16), nullable=False),
        sa.Column("level_pct", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_fair_use_alerts_crossing",
        "fair_use_alerts",
        ["tenant_id", "metric", "period_key", "level_pct"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.drop_constraint("ck_usage_charges_waived", "usage_charges", type_="check")
    op.create_check_constraint(
        "ck_usage_charges_waived",
        "usage_charges",
        "waived is null or waived in ('admin','downgraded')",
    )

    op.get_bind().execute(
        sa.text(
            "UPDATE plans SET fair_use_limits = CAST(:limits AS jsonb) "
            "WHERE name = 'Free' AND fair_use_limits = '{}'::jsonb "
            "AND pipeline_profile_id = "
            "(SELECT id FROM pipeline_profiles WHERE builtin_key = 'free')"
        ),
        {"limits": json.dumps(_FREE_LIMITS)},
    )


def downgrade() -> None:
    # A charge written as 'downgraded' cannot satisfy the old rule: it becomes 'admin'
    # (also a zero-amount waived charge), which is the nearest thing the old schema had.
    op.get_bind().execute(
        sa.text("UPDATE usage_charges SET waived = 'admin' WHERE waived = 'downgraded'")
    )
    op.drop_constraint("ck_usage_charges_waived", "usage_charges", type_="check")
    op.create_check_constraint(
        "ck_usage_charges_waived", "usage_charges", "waived is null or waived in ('admin')"
    )
    op.drop_index("uq_fair_use_alerts_crossing", table_name="fair_use_alerts")
    op.drop_table("fair_use_alerts")
    op.drop_table("fair_use_counters")
    op.drop_constraint(
        "ck_tenant_pipeline_settings_state", "tenant_pipeline_settings", type_="check"
    )
    for col in ("state_since", "state", "policy_overrides", "fair_use_overrides"):
        op.drop_column("tenant_pipeline_settings", col)
    op.drop_column("plans", "fair_use_policy")
    op.drop_column("plans", "fair_use_limits")
