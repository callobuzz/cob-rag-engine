"""One run of the daily billing reconciliation (billing BIL3): whether every balance still
equals its ledger, every charge was posted once, and every metered request has a charge.
`findings` is a list (at most 500) of `{check, account_id, tenant_id, detail}`."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class BillingReconciliation(Base):
    __tablename__ = "billing_reconciliations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    checked_accounts: Mapped[int] = mapped_column(Integer, nullable=False)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
