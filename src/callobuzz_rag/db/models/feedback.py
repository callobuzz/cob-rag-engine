"""Feedback model — user feedback on query responses."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    query_log_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("query_log.id", ondelete="CASCADE"), nullable=True
    )
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    correction: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'explicit'")
    )
    session_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
