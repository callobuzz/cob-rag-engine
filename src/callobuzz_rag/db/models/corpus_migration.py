"""CorpusMigration model — embedding migration job tracking."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base

if TYPE_CHECKING:
    from callobuzz_rag.db.models.corpus import Corpus


class CorpusMigration(Base):
    __tablename__ = "corpus_migrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    from_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    to_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    old_collection: Mapped[str] = mapped_column(String(255), nullable=False)
    new_collection: Mapped[str] = mapped_column(String(255), nullable=False)
    chunks_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    chunks_done: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    memories_total: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    memories_done: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prune_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_corpus_migrations_non_terminal",
            "corpus_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending_approval', 'queued', 'running', 'verifying', 'rolling_back')"
            ),
        ),
    )

    corpus: Mapped["Corpus"] = relationship(back_populates="migrations")
