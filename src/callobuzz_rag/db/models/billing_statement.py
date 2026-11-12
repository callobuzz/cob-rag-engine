"""One frozen monthly statement of a workspace's wallet (billing BIL6).

Written once per closed UTC month by the beat on the 1st (`billing/statements.py`), after the
last usage of the month has been posted; never updated, so a statement that was sent does not
change. `data` is the whole `StatementData` document. The current month is not stored: it is
built live and marked "so far".
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class BillingStatement(Base):
    __tablename__ = "billing_statements"
    __table_args__ = (
        UniqueConstraint("account_id", "month", name="uq_billing_statements_account_month"),
        Index("ix_billing_statements_tenant_month", "tenant_id", "month"),
        CheckConstraint("currency in ('INR','USD')", name="ck_billing_statements_currency"),
        CheckConstraint(
            "extract(day from month) = 1", name="ck_billing_statements_month_first_day"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "billing_accounts.id",
            ondelete="CASCADE",
            name="fk_billing_statements_account_id_accounts",
        ),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)  # first day of the UTC month
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    opening_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    closing_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
