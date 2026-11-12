"""A positive ledger entry that can expire (billing BIL3): included credit, a credit pack with
an expiry, a goodwill adjustment with one. Only usage charges consume lots (earliest expiry
first); `remaining_minor` is allocation state, not money, so it stays mutable.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class CreditLot(Base):
    __tablename__ = "credit_lots"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_credit_lots_amount"),
        CheckConstraint(
            "remaining_minor >= 0 and remaining_minor <= amount_minor", name="ck_credit_lots_left"
        ),
        Index(
            "ix_credit_lots_live",
            "account_id",
            "expires_at",
            postgresql_where=text("remaining_minor > 0"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "billing_accounts.id", ondelete="CASCADE", name="fk_credit_lots_account_id_accounts"
        ),
        nullable=False,
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wallet_ledger.id", ondelete="CASCADE", name="fk_credit_lots_entry_id"),
        nullable=False,
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remaining_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expired_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wallet_ledger.id", ondelete="SET NULL", name="fk_credit_lots_expired_entry_id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
