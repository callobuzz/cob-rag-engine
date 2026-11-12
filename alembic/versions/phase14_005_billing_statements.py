"""Billing: frozen monthly statements.

Revision ID: phase14_005
Revises: phase14_004
Create Date: 2026-10-04

`billing_statements` holds one snapshot per workspace and closed UTC month, written once by the
beat on the 1st so a statement that was sent never changes (the current month is built live).
`data` is the whole statement document; `opening_minor` and `closing_minor` are copied out so a
list of statements needs no JSON. The row belongs to the billing account and goes with it.

Downgrade drops the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase14_005"
down_revision: str | None = "phase14_004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "billing_statements",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("opening_minor", sa.BigInteger(), nullable=False),
        sa.Column("closing_minor", sa.BigInteger(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["billing_accounts.id"],
            name="fk_billing_statements_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("account_id", "month", name="uq_billing_statements_account_month"),
        sa.CheckConstraint("currency in ('INR','USD')", name="ck_billing_statements_currency"),
        sa.CheckConstraint(
            "extract(day from month) = 1", name="ck_billing_statements_month_first_day"
        ),
    )
    op.create_index(
        "ix_billing_statements_tenant_month", "billing_statements", ["tenant_id", "month"]
    )


def downgrade() -> None:
    op.drop_index("ix_billing_statements_tenant_month", table_name="billing_statements")
    op.drop_table("billing_statements")
