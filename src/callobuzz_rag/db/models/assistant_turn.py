"""One row per assistant turn: what the director decided and why (C1).

Read by analytics, the review inbox and per-rule stats (C7). `question_masked` is the
visitor's text after PII masking; nothing else here is personal data.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class AssistantTurn(Base):
    __tablename__ = "assistant_turns"
    __table_args__ = (
        Index("ix_assistant_turns_corpus_created", "corpus_id", "created_at"),
        Index("ix_assistant_turns_corpus_label", "corpus_id", "label"),
        Index(
            "ix_assistant_turns_no_answer",
            "corpus_id",
            "created_at",
            postgresql_where=text("no_answer"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    query_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    assistant_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    label_source: Mapped[str] = mapped_column(String(10), nullable=False)
    label_confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    script: Mapped[str] = mapped_column(String(12), nullable=False)
    route: Mapped[str] = mapped_column(String(24), nullable=False)
    topic_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    top_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    gate_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    claims_removed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    claims_flagged: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rules_fired: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    offers: Mapped[list[str]] = mapped_column(
        ARRAY(String(20)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    no_answer: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    question_masked: Mapped[str] = mapped_column(Text, nullable=False)
    is_preview: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    timings: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # TV4 schema: was the business open when the visitor asked (NULL = hours unknown)
    office_open: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # /TV4 schema
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
