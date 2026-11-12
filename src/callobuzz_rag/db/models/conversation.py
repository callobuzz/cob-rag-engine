"""Conversation model — persistent conversation threads, corpus-scoped."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("kind in ('assistant','cob')", name="ck_conversations_kind"),
        Index("ix_conversations_user_kind_updated", "user_id", "kind", "updated_at"),
        # TV4 schema
        Index(
            "uq_conversations_corpus_ref",
            "corpus_id",
            "ref_code",
            unique=True,
            postgresql_where=text("ref_code IS NOT NULL"),
        ),
        # /TV4 schema
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # C1: where the chat happens and what the director remembers about it.
    channel: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'api'"))
    widget_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("widget_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    widget_session_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    utm: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    assistant_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    assistant_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # COB: 'assistant' = a knowledge base's own chats, 'cob' = a person talking to COB. COB chats
    # belong to their author (`user_id`) in the workspace they started in (`scope_tenant_id`,
    # NULL for the operator view) and never show in the knowledge base's conversation routes.
    kind: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'assistant'")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    scope_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    # TV6: the WhatsApp chat this conversation belongs to (a chat has one conversation per 24-hour
    # window); deleting the chat deletes them all (DPDP erasure).
    channel_thread_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "channel_threads.id", ondelete="CASCADE", name="fk_conversations_channel_thread"
        ),
        nullable=True,
        index=True,
    )
    # TV4 schema: the short reference a visitor can quote (TV2 assigns it)
    ref_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # /TV4 schema
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )
