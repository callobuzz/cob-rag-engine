"""What one metered request costs the customer (billing BIL3).

One row per request id, written inside `record_usage` (the primary key makes a retry a no-op).
`amount_micro` is in micro units of the account's currency, so a fraction of a paisa is never
rounded away; the minute posting turns the unposted sum into ledger minor units and carries the
remainder. `posted_*` is allocation state, not money, so those columns stay mutable.
`corpus_id` has no foreign key: a charge outlives a deleted knowledge base.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class UsageCharge(Base):
    __tablename__ = "usage_charges"
    __table_args__ = (
        CheckConstraint("amount_micro >= 0", name="ck_usage_charges_amount"),
        # PIP3 waiver: 'downgraded' = answered on the fallback profile because money ran out
        CheckConstraint(
            "waived is null or waived in ('admin','downgraded')", name="ck_usage_charges_waived"
        ),
        # /PIP3 waiver
        CheckConstraint("waived is null or amount_micro = 0", name="ck_usage_charges_waived_zero"),
        CheckConstraint("currency in ('INR','USD')", name="ck_usage_charges_currency"),
        Index(
            "ix_usage_charges_unposted",
            "account_id",
            "created_at",
            postgresql_where=text("posted_at IS NULL"),
        ),
        Index("ix_usage_charges_tenant_day", "tenant_id", "day"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "billing_accounts.id", ondelete="CASCADE", name="fk_usage_charges_account_id_accounts"
        ),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    corpus_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    payer: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    questions: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    overage: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    cost_usd_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_known: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pricing_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan_pricing.id", ondelete="SET NULL", name="fk_usage_charges_pricing_id"),
        nullable=True,
    )
    usage_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    fx_ppm: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_micro: Mapped[int] = mapped_column(BigInteger, nullable=False)
    waived: Mapped[str | None] = mapped_column(String(16), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "wallet_ledger.id", ondelete="SET NULL", name="fk_usage_charges_posted_entry_id"
        ),
        nullable=True,
    )
