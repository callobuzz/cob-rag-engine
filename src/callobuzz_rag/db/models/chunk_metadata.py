"""ChunkMetadata model — enriched metadata per chunk."""

import uuid
from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base


class ChunkMetadata(Base):
    __tablename__ = "chunk_metadata"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    hypothetical_questions: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Structured table data (preserved from chunking for Phase 2+ hybrid search)
    table_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    chunk: Mapped["Chunk"] = relationship(back_populates="metadata_record")  # type: ignore[name-defined]
