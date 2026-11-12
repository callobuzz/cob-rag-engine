"""Embedding model — vector embedding references."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base


class Embedding(Base):
    __tablename__ = "embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"))
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vector_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    vector_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vector_norm: Mapped[float | None] = mapped_column(Float, nullable=True)
    dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (Index("ix_embeddings_chunk_id", "chunk_id"),)

    # Relationships
    chunk: Mapped["Chunk"] = relationship(back_populates="embeddings")  # type: ignore[name-defined]
