"""Online payments: provider settings, workspace tax details, orders, payments, events, refunds (P1).

Revision ID: phase19_001
Revises: phase18_001
Create Date: 2026-10-08

- `payment_provider_settings`: the one row of operator payment settings (Razorpay keys as Fernet
  tokens; the key id is public in checkout and stays plain).
- `billing_profiles`: a workspace's own tax details (who a tax invoice is made out to); goes with
  the workspace.
- `payment_orders`, `payments`, `payment_events`, `payment_refunds`: the money that reached us.
  `tenant_id` / `account_id` have NO foreign keys on purpose: financial records outlive a deleted
  workspace (GST records are kept for years), like `audit_log`. Card and UPI details are never stored.
- `add_on_grants.payment` also allows `gateway`, and `provider_payment_id` names the payment.

Downgrade drops the tables in reverse order and restores the grant check; it refuses while a
`gateway` grant exists, because those grants could not be kept.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase19_001"
down_revision: str | None = "phase18_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_GEN = sa.text("gen_random_uuid()")


def _ts(name: str, *, nullable: bool = True, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=_NOW if default else None,
        nullable=nullable,
    )


def _minor(name: str, *, nullable: bool = False, zero: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.BigInteger(),
        server_default=sa.text("0") if zero else None,
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "payment_provider_settings",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("provider", sa.String(16), server_default=sa.text("'none'"), nullable=False),
        sa.Column("key_id", sa.String(64), nullable=True),
        sa.Column("key_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True),
        _ts("updated_at", nullable=False, default=True),
        sa.Column("updated_by", sa.String(320), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_payment_provider_settings_single_row"),
        sa.CheckConstraint(
            "provider in ('none','razorpay')", name="ck_payment_provider_settings_provider"
        ),
    )

    op.create_table(
        "billing_profiles",
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("legal_name", sa.String(200), nullable=False),
        sa.Column("gstin", sa.String(15), nullable=True),
        sa.Column("address_line1", sa.String(200), nullable=False),
        sa.Column("address_line2", sa.String(200), nullable=True),
        sa.Column("city", sa.String(100), nullable=False),
        sa.Column("state_code", sa.String(2), nullable=True),
        sa.Column("pincode", sa.String(6), nullable=True),
        sa.Column("country", sa.String(2), server_default=sa.text("'IN'"), nullable=False),
        sa.Column("email_for_invoices", sa.String(320), nullable=True),
        _ts("created_at", nullable=False, default=True),
        _ts("updated_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_billing_profiles_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "country <> 'IN' OR state_code IS NOT NULL", name="ck_billing_profiles_in_state"
        ),
    )

    op.create_table(
        "payment_orders",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("account_id", _UUID, nullable=False),
        sa.Column("purpose", sa.String(10), nullable=False),
        sa.Column("add_on_id", _UUID, nullable=True),
        sa.Column("channel", sa.String(10), server_default=sa.text("'checkout'"), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        _minor("taxable_minor"),
        _minor("cgst_minor", zero=True),
        _minor("sgst_minor", zero=True),
        _minor("igst_minor", zero=True),
        _minor("gross_minor"),
        sa.Column("tax_kind", sa.String(12), nullable=False),
        sa.Column("place_of_supply", sa.String(2), nullable=False),
        sa.Column("description", sa.String(200), nullable=False),
        sa.Column("sac_code", sa.String(6), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_order_id", sa.String(40), nullable=True),
        sa.Column("link_id", sa.String(40), nullable=True),
        sa.Column("short_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(10), server_default=sa.text("'created'"), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        _ts("created_at", nullable=False, default=True),
        _ts("expires_at"),
        _ts("paid_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_payment_orders_tenant_key"),
        sa.UniqueConstraint("provider_order_id", name="uq_payment_orders_provider_order_id"),
        sa.CheckConstraint(
            "purpose in ('top_up','plan','add_on')", name="ck_payment_orders_purpose"
        ),
        sa.CheckConstraint("channel in ('checkout','link')", name="ck_payment_orders_channel"),
        sa.CheckConstraint(
            "status in ('created','paid','failed','expired')", name="ck_payment_orders_status"
        ),
        sa.CheckConstraint(
            "taxable_minor >= 0 AND cgst_minor >= 0 AND sgst_minor >= 0 AND igst_minor >= 0"
            " AND gross_minor >= 0",
            name="ck_payment_orders_amounts",
        ),
        sa.CheckConstraint(
            "gross_minor = taxable_minor + cgst_minor + sgst_minor + igst_minor",
            name="ck_payment_orders_gross_sum",
        ),
    )
    op.create_index(
        "ix_payment_orders_tenant_created", "payment_orders", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_payment_orders_open",
        "payment_orders",
        ["created_at"],
        postgresql_where=sa.text("status = 'created'"),
    )

    op.create_table(
        "payments",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("order_id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("account_id", _UUID, nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("provider_payment_id", sa.String(40), nullable=False),
        sa.Column("method", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        _minor("gross_minor"),
        _minor("taxable_minor"),
        _minor("cgst_minor", zero=True),
        _minor("sgst_minor", zero=True),
        _minor("igst_minor", zero=True),
        _minor("refunded_minor", zero=True),
        _minor("fee_minor", nullable=True),
        _minor("fee_tax_minor", nullable=True),
        sa.Column("entry_id", _UUID, nullable=True),
        sa.Column("settlement_id", sa.String(40), nullable=True),
        _ts("settled_at"),
        _ts("captured_at"),
        sa.Column("source", sa.String(10), nullable=False),
        _ts("created_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["payment_orders.id"],
            name="fk_payments_order_id_orders",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("provider_payment_id", name="uq_payments_provider_payment_id"),
        sa.CheckConstraint(
            "status in ('captured','authorized','failed','mismatch','refunded',"
            "'partially_refunded')",
            name="ck_payments_status",
        ),
    )
    op.create_index("ix_payments_tenant_captured", "payments", ["tenant_id", "captured_at"])

    op.create_table(
        "payment_events",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("event", sa.String(60), nullable=False),
        sa.Column(
            "entity_ids",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("status", sa.String(12), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.String(200), nullable=True),
        _ts("received_at", nullable=False, default=True),
        _ts("processed_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_payment_events_event_id"),
        sa.CheckConstraint(
            "status in ('pending','processing','done','ignored','failed')",
            name="ck_payment_events_status",
        ),
    )
    op.create_index(
        "ix_payment_events_due",
        "payment_events",
        ["received_at"],
        postgresql_where=sa.text("status IN ('pending','failed')"),
    )

    op.create_table(
        "payment_refunds",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("payment_id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("account_id", _UUID, nullable=False),
        _minor("taxable_minor"),
        _minor("cgst_minor", zero=True),
        _minor("sgst_minor", zero=True),
        _minor("igst_minor", zero=True),
        _minor("gross_minor"),
        sa.Column("status", sa.String(12), server_default=sa.text("'requested'"), nullable=False),
        sa.Column("provider_refund_id", sa.String(40), nullable=True),
        sa.Column("entry_id", _UUID, nullable=True),
        sa.Column("reversal_entry_id", _UUID, nullable=True),
        sa.Column("memo", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(320), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        _ts("created_at", nullable=False, default=True),
        _ts("processed_at"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["payments.id"],
            name="fk_payment_refunds_payment_id_payments",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_payment_refunds_idempotency_key"),
        sa.UniqueConstraint("provider_refund_id", name="uq_payment_refunds_provider_refund_id"),
        sa.CheckConstraint(
            "status in ('requested','pending','processed','failed')",
            name="ck_payment_refunds_status",
        ),
    )
    op.create_index("ix_payment_refunds_payment", "payment_refunds", ["payment_id"])

    op.drop_constraint("ck_add_on_grant_payment", "add_on_grants", type_="check")
    op.create_check_constraint(
        "ck_add_on_grant_payment",
        "add_on_grants",
        "payment in ('wallet','external','free','gateway')",
    )
    op.add_column("add_on_grants", sa.Column("provider_payment_id", sa.String(40), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    found = bind.execute(
        sa.text("SELECT count(*) FROM add_on_grants WHERE payment = 'gateway'")
    ).scalar_one()
    if found:
        raise RuntimeError(
            f"{found} add-on grant(s) were paid through the payment gateway; "
            "they cannot be kept after downgrading. Remove or convert them first."
        )
    op.drop_column("add_on_grants", "provider_payment_id")
    op.drop_constraint("ck_add_on_grant_payment", "add_on_grants", type_="check")
    op.create_check_constraint(
        "ck_add_on_grant_payment", "add_on_grants", "payment in ('wallet','external','free')"
    )
    op.drop_index("ix_payment_refunds_payment", table_name="payment_refunds")
    op.drop_table("payment_refunds")
    op.drop_index("ix_payment_events_due", table_name="payment_events")
    op.drop_table("payment_events")
    op.drop_index("ix_payments_tenant_captured", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_payment_orders_open", table_name="payment_orders")
    op.drop_index("ix_payment_orders_tenant_created", table_name="payment_orders")
    op.drop_table("payment_orders")
    op.drop_table("billing_profiles")
    op.drop_table("payment_provider_settings")
