"""One row of a workspace's wallet ledger (billing BIL3).

Append-only: a database trigger refuses every UPDATE and every DELETE except the cascade of a
deleted billing account. The balance is the sum of `amount_minor`; a mistake is corrected with a
new `adjustment` or `refund` row. Written only by `billing/ledger.py:append_entries`.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class WalletLedger(Base):
    __tablename__ = "wallet_ledger"
    __table_args__ = (
        UniqueConstraint("account_id", "seq", name="uq_wallet_ledger_account_seq"),
        UniqueConstraint("account_id", "idempotency_key", name="uq_wallet_ledger_account_key"),
        CheckConstraint(
            "entry_type in ('top_up','subscription_charge','usage_charge','add_on_purchase',"
            "'adjustment','refund','included_credit','expiry')",
            name="ck_wallet_ledger_type",
        ),
        CheckConstraint("amount_minor <> 0", name="ck_wallet_ledger_amount"),
        CheckConstraint(
            "(entry_type in ('top_up','included_credit') and amount_minor > 0)"
            " or (entry_type in ('subscription_charge','usage_charge','refund','expiry')"
            " and amount_minor < 0)"
            " or entry_type in ('add_on_purchase','adjustment')",
            name="ck_wallet_ledger_sign",
        ),
        CheckConstraint("currency in ('INR','USD')", name="ck_wallet_ledger_currency"),
        Index("ix_wallet_ledger_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "billing_accounts.id", ondelete="CASCADE", name="fk_wallet_ledger_account_id_accounts"
        ),
        nullable=False,
    )
    # Denormalised from the account so the customer's own listing needs no join.
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(24), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance_after_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)  # admin only
    memo_public: Mapped[str | None] = mapped_column(String(200), nullable=True)
    period: Mapped[date] = mapped_column(Date, nullable=False)  # first day of the month
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_label: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
