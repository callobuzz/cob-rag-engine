"""Handoff: a visitor offered (or taking) a way to reach a person (conversation layer C5)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

HANDOFF_STATUSES = ("new", "handled")


class Handoff(Base):
    __tablename__ = "handoffs"
    __table_args__ = (
        CheckConstraint("status IN ('new','handled')", name="ck_handoffs_status"),
        Index("ix_handoffs_corpus_created", "corpus_id", "created_at"),
        Index("ix_handoffs_tenant_created", "tenant_id", "created_at"),
        Index("ix_handoffs_new", "corpus_id", postgresql_where=text("status = 'new'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    # whatsapp | call | callback | email | contact_page | booking | offered (a card shown on an
    # API surface: nothing to click, so no channel was chosen)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    summary_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    office_open: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default="new", server_default=text("'new'")
    )
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
