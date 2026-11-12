"""Lead - contact details a visitor chose to share, with consent (conversation layer C4).

Contact values exist only inside `fields_encrypted` (Fernet). `phone_hash` / `email_hash`
are keyed HMACs for exact search (`conversation/leads/crypto.py`).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

LEAD_STATUSES = ("new", "contacted", "won", "lost", "spam")
LEAD_QUALIFICATIONS = ("qualified", "partial", "unqualified")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new','contacted','won','lost','spam')", name="ck_leads_status"
        ),
        CheckConstraint(
            "qualification IS NULL OR qualification IN ('qualified','partial','unqualified')",
            name="ck_leads_qualification",
        ),
        Index("ix_leads_corpus_created", "corpus_id", "created_at"),
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
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    widget_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("widget_keys.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    fields_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    email_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    utm: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    source_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    callback_window: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default=text("'new'"))
    qualification: Mapped[str | None] = mapped_column(String(12), nullable=True)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_consulted: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    retention_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set when the new-lead notifications (summary, e-mail, webhook) went out, so a task retry
    # never sends them twice.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # sha256 of the secret in the visitor's withdrawal link (never the secret itself).
    withdraw_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )
