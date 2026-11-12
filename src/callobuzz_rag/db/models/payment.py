"""Online payments (P1): provider settings, workspace tax details, orders, payments, events, refunds.

Razorpay only moves money; the ledger decides access (`billing/payments/capture.py`). Financial rows
(`payment_orders`, `payments`, `payment_refunds`, `payment_events`) carry `tenant_id` and
`account_id` WITHOUT foreign keys: they outlive a deleted workspace (GST records are kept for years),
like `audit_log`. `billing_profiles` is the workspace's own tax details and goes with the workspace.
Card and UPI details are never stored.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

_NOW = text("now()")
_GEN = text("gen_random_uuid()")


class PaymentProviderSetting(Base):
    """The one row of operator payment settings. Secrets are Fernet tokens, never plain."""

    __tablename__ = "payment_provider_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_payment_provider_settings_single_row"),
        CheckConstraint(
            "provider in ('none','razorpay')", name="ck_payment_provider_settings_provider"
        ),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    provider: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'none'"), default="none"
    )
    key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    key_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    updated_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class BillingProfile(Base):
    """A workspace's tax details: who the tax invoice is made out to (one row per workspace)."""

    __tablename__ = "billing_profiles"
    __table_args__ = (
        CheckConstraint(
            "country <> 'IN' OR state_code IS NOT NULL", name="ck_billing_profiles_in_state"
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_billing_profiles_tenant_id_tenants"),
        primary_key=True,
    )
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    address_line1: Mapped[str] = mapped_column(String(200), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(6), nullable=True)
    country: Mapped[str] = mapped_column(
        String(2), nullable=False, server_default=text("'IN'"), default="IN"
    )
    email_for_invoices: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class PaymentOrder(Base):
    """What a business is about to pay for: the quote frozen, and the provider's order id."""

    __tablename__ = "payment_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_payment_orders_tenant_key"),
        UniqueConstraint("provider_order_id", name="uq_payment_orders_provider_order_id"),
        CheckConstraint("purpose in ('top_up','plan','add_on')", name="ck_payment_orders_purpose"),
        CheckConstraint("channel in ('checkout','link')", name="ck_payment_orders_channel"),
        CheckConstraint(
            "status in ('created','paid','failed','expired')", name="ck_payment_orders_status"
        ),
        CheckConstraint(
            "taxable_minor >= 0 AND cgst_minor >= 0 AND sgst_minor >= 0 AND igst_minor >= 0"
            " AND gross_minor >= 0",
            name="ck_payment_orders_amounts",
        ),
        CheckConstraint(
            "gross_minor = taxable_minor + cgst_minor + sgst_minor + igst_minor",
            name="ck_payment_orders_gross_sum",
        ),
        Index("ix_payment_orders_tenant_created", "tenant_id", "created_at"),
        Index(
            "ix_payment_orders_open",
            "created_at",
            postgresql_where=text("status = 'created'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    purpose: Mapped[str] = mapped_column(String(10), nullable=False)
    add_on_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    channel: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'checkout'"), default="checkout"
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    taxable_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    igst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tax_kind: Mapped[str] = mapped_column(String(12), nullable=False)
    place_of_supply: Mapped[str] = mapped_column(String(2), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    sac_code: Mapped[str] = mapped_column(String(6), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    link_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    short_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'created'"), default="created"
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Payment(Base):
    """One payment the provider says reached us. `provider_payment_id` is unique: that is the guard."""

    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("provider_payment_id", name="uq_payments_provider_payment_id"),
        CheckConstraint(
            "status in ('captured','authorized','failed','mismatch','refunded',"
            "'partially_refunded')",
            name="ck_payments_status",
        ),
        Index("ix_payments_tenant_captured", "tenant_id", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT", name="fk_payments_order_id_orders"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(40), nullable=False)
    method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    taxable_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    igst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    refunded_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    fee_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fee_tax_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    settlement_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class PaymentEvent(Base):
    """A webhook delivery, stored by the provider's event id and processed by the worker."""

    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_payment_events_event_id"),
        CheckConstraint(
            "status in ('pending','processing','done','ignored','failed')",
            name="ck_payment_events_status",
        ),
        Index(
            "ix_payment_events_due",
            "received_at",
            postgresql_where=text("status IN ('pending','failed')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_ids: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    payload_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'pending'"), default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentRefund(Base):
    """A refund from unused balance to the original payment."""

    __tablename__ = "payment_refunds"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payment_refunds_idempotency_key"),
        UniqueConstraint("provider_refund_id", name="uq_payment_refunds_provider_refund_id"),
        CheckConstraint(
            "status in ('requested','pending','processed','failed')",
            name="ck_payment_refunds_status",
        ),
        Index("ix_payment_refunds_payment", "payment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "payments.id", ondelete="RESTRICT", name="fk_payment_refunds_payment_id_payments"
        ),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    taxable_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    igst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    gross_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'requested'"), default="requested"
    )
    provider_refund_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reversal_entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    memo: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(320), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # P2 invoices
    credit_note_skipped: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # /P2 invoices
