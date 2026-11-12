"""A tenant's answered questions in one UTC calendar month (accounts A6).

`record_usage` increments it next to `usage_daily` for every answered question, and the
monthly cap reads it. It is keyed by tenant, not corpus: `usage_daily` rows are deleted
with their knowledge base, so a count summed from them could be reset by deleting and
recreating one. Only deleting the tenant removes these rows.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class TenantQuestionCount(Base):
    __tablename__ = "tenant_question_counts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )
    month: Mapped[date] = mapped_column(
        Date, primary_key=True, comment="First day of the month (UTC)"
    )
    questions: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
