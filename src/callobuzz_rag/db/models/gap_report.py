"""GapReport model — stores knowledge gap detection results."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class GapReport(Base):
    __tablename__ = "gap_report"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Nullable because a run with no corpus_id spans every corpus, and that
    # "global run" state has to stay representable. Everything else about a
    # report is scoped by this column, including tenant access.
    corpus_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corpora.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", doc="queued | running | completed | failed"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    query_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cluster_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clusters: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
