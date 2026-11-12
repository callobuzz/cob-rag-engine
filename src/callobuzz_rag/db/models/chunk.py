"""Chunk model — processed text chunks."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=sa_text("gen_random_uuid()")
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"))
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    context_preamble: Mapped[str | None] = mapped_column(Text, nullable=True)
    structure_path: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    length_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_table: Mapped[bool] = mapped_column(Boolean, server_default=sa_text("false"))
    is_code: Mapped[bool] = mapped_column(Boolean, server_default=sa_text("false"))
    parent_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), nullable=True
    )
    # Hash of the chunk's own text (overlap removed, `pipeline/text_hash.py`): equal hashes
    # in one corpus are the same passage stored in more than one document. NULL on chunks
    # written before the column existed (`reindex --payload-hash` fills it).
    text_hash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa_text("now()")
    )

    __table_args__ = (
        Index("ix_chunks_document_version_id", "document_version_id"),
        Index("ix_chunks_corpus_id", "corpus_id"),
        Index("ix_chunks_corpus_text_hash", "corpus_id", "text_hash"),
    )

    # Relationships
    document_version: Mapped["DocumentVersion"] = relationship(back_populates="chunks")  # type: ignore[name-defined]
    corpus: Mapped["Corpus"] = relationship(back_populates="chunks")  # type: ignore[name-defined]
    metadata_record: Mapped["ChunkMetadata | None"] = relationship(  # type: ignore[name-defined]
        back_populates="chunk", cascade="all, delete-orphan"
    )
    embeddings: Mapped[list["Embedding"]] = relationship(  # type: ignore[name-defined]
        back_populates="chunk", cascade="all, delete-orphan"
    )
    children: Mapped[list["Chunk"]] = relationship(back_populates="parent")
    parent: Mapped["Chunk | None"] = relationship(back_populates="children", remote_side="Chunk.id")
