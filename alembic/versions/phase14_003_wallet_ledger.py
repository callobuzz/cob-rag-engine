"""Billing: the append-only wallet ledger, usage charges, credit lots, reconciliations.

Revision ID: phase14_003
Revises: phase14_002
Create Date: 2026-10-04

`wallet_ledger` is the money: a balance is the sum of its rows, never edited, only corrected with
a new `adjustment` or `refund` row. A trigger enforces that in the database: every UPDATE is
refused, and a DELETE is refused unless the owning `billing_accounts` row is already gone (the
cascade of a deleted workspace). `usage_charges` holds what each metered request costs, one row
per request id (the primary key makes a retry harmless), in account-currency micro units; the
minute posting turns them into ledger entries. `credit_lots` tracks credit that expires.
`billing_reconciliations` stores each daily check. `usage_charges.posted_*` and
`credit_lots.remaining_minor` stay mutable on purpose: they are allocation state, not money.

Downgrade drops the trigger, its function and the four tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase14_003"
down_revision: str | None = "phase14_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "wallet_ledger",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("entry_type", sa.String(24), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("balance_after_minor", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("reference_type", sa.String(24), nullable=True),
        sa.Column("reference_id", sa.String(64), nullable=True),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("memo_public", sa.String(200), nullable=True),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_user_id", _UUID, nullable=True),
        sa.Column("actor_label", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["billing_accounts.id"],
            name="fk_wallet_ledger_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("account_id", "seq", name="uq_wallet_ledger_account_seq"),
        sa.UniqueConstraint("account_id", "idempotency_key", name="uq_wallet_ledger_account_key"),
        sa.CheckConstraint(
            "entry_type in ('top_up','subscription_charge','usage_charge','add_on_purchase',"
            "'adjustment','refund','included_credit','expiry')",
            name="ck_wallet_ledger_type",
        ),
        sa.CheckConstraint("amount_minor <> 0", name="ck_wallet_ledger_amount"),
        sa.CheckConstraint(
            "(entry_type in ('top_up','included_credit') and amount_minor > 0)"
            " or (entry_type in ('subscription_charge','usage_charge','refund','expiry')"
            " and amount_minor < 0)"
            " or entry_type in ('add_on_purchase','adjustment')",
            name="ck_wallet_ledger_sign",
        ),
        sa.CheckConstraint("currency in ('INR','USD')", name="ck_wallet_ledger_currency"),
    )
    op.create_index("ix_wallet_ledger_tenant_created", "wallet_ledger", ["tenant_id", "created_at"])

    op.create_table(
        "usage_charges",
        sa.Column("request_id", _UUID, nullable=False),
        sa.Column("account_id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("corpus_id", _UUID, nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("payer", sa.String(16), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("questions", sa.SmallInteger(), nullable=False),
        sa.Column("overage", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("cost_usd_micro", sa.BigInteger(), nullable=False),
        sa.Column("cost_known", sa.Boolean(), nullable=False),
        sa.Column("pricing_id", _UUID, nullable=True),
        sa.Column("usage_mode", sa.String(16), nullable=False),
        sa.Column("fx_ppm", sa.BigInteger(), nullable=False),
        sa.Column("amount_micro", sa.BigInteger(), nullable=False),
        sa.Column("waived", sa.String(16), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_entry_id", _UUID, nullable=True),
        sa.PrimaryKeyConstraint("request_id"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["billing_accounts.id"],
            name="fk_usage_charges_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pricing_id"],
            ["plan_pricing.id"],
            name="fk_usage_charges_pricing_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["posted_entry_id"],
            ["wallet_ledger.id"],
            name="fk_usage_charges_posted_entry_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("amount_micro >= 0", name="ck_usage_charges_amount"),
        sa.CheckConstraint("waived is null or waived in ('admin')", name="ck_usage_charges_waived"),
        sa.CheckConstraint(
            "waived is null or amount_micro = 0", name="ck_usage_charges_waived_zero"
        ),
        sa.CheckConstraint("currency in ('INR','USD')", name="ck_usage_charges_currency"),
    )
    op.create_index(
        "ix_usage_charges_unposted",
        "usage_charges",
        ["account_id", "created_at"],
        postgresql_where=sa.text("posted_at IS NULL"),
    )
    op.create_index("ix_usage_charges_tenant_day", "usage_charges", ["tenant_id", "day"])

    op.create_table(
        "credit_lots",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("account_id", _UUID, nullable=False),
        sa.Column("entry_id", _UUID, nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("remaining_minor", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_entry_id", _UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["billing_accounts.id"],
            name="fk_credit_lots_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"], ["wallet_ledger.id"], name="fk_credit_lots_entry_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["expired_entry_id"],
            ["wallet_ledger.id"],
            name="fk_credit_lots_expired_entry_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("amount_minor > 0", name="ck_credit_lots_amount"),
        sa.CheckConstraint(
            "remaining_minor >= 0 and remaining_minor <= amount_minor", name="ck_credit_lots_left"
        ),
    )
    op.create_index(
        "ix_credit_lots_live",
        "credit_lots",
        ["account_id", "expires_at"],
        postgresql_where=sa.text("remaining_minor > 0"),
    )

    op.create_table(
        "billing_reconciliations",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ran_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("checked_accounts", sa.Integer(), nullable=False),
        sa.Column(
            "findings",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.execute(
        sa.text(
            """
            CREATE FUNCTION wallet_ledger_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'wallet_ledger is append-only (correct with an adjustment)';
              END IF;
              IF EXISTS (SELECT 1 FROM billing_accounts WHERE id = OLD.account_id) THEN
                RAISE EXCEPTION 'wallet_ledger rows are deleted only with their workspace';
              END IF;
              RETURN OLD;
            END $$
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER wallet_ledger_append_only BEFORE UPDATE OR DELETE ON wallet_ledger "
            "FOR EACH ROW EXECUTE FUNCTION wallet_ledger_append_only()"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS wallet_ledger_append_only ON wallet_ledger"))
    op.drop_table("billing_reconciliations")
    op.drop_index("ix_credit_lots_live", table_name="credit_lots")
    op.drop_table("credit_lots")
    op.drop_index("ix_usage_charges_tenant_day", table_name="usage_charges")
    op.drop_index("ix_usage_charges_unposted", table_name="usage_charges")
    op.drop_table("usage_charges")
    op.drop_index("ix_wallet_ledger_tenant_created", table_name="wallet_ledger")
    op.drop_table("wallet_ledger")
    op.execute(sa.text("DROP FUNCTION IF EXISTS wallet_ledger_append_only()"))
