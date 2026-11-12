"""LeadConsent - the record of what a visitor agreed to (conversation layer C4).

Kept (without contact data) when the lead is erased: `lead_id` goes NULL. The retention
task removes it a year after `erased_at`, the day the lead went.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class LeadConsent(Base):
    __tablename__ = "lead_consents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False, index=True
    )
    notice_version: Mapped[int] = mapped_column(Integer, nullable=False)
    wording_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(300), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
