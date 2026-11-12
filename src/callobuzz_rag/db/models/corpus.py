"""Corpus model — document collections."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base


class Corpus(Base):
    __tablename__ = "corpora"
    __table_args__ = (
        # At most one system knowledge base (COB's own docs).
        Index("uq_corpora_system", "is_system", unique=True, postgresql_where=text("is_system")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    # --- Phase 4: Multi-corpus isolation columns ---
    # Superseded by `api_keys` (phase11_012: many named keys, each with a permission). Always
    # blank now; kept so the migration can be reversed. Nothing reads it.
    api_key_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("''"),
        comment="bcrypt hash of the corpus-level API key",
    )
    qdrant_collection: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("'chunks'"),
        comment="Qdrant collection name for this corpus's vectors",
    )
    llm_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Per-corpus LLM overrides: model, api_key (encrypted), temperature, max_tokens",
    )
    prompt_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Per-corpus prompt template overrides: system_prompt, constraints, response_language",
    )
    retrieval_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Per-corpus retrieval overrides: top_k, mmr, reranking, validation, cache, etc.",
    )

    # --- Phase 8: Tenant ownership ---
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Owning tenant. Every corpus belongs to one (accounts A1).",
    )

    # --- Phase 8: Embedding model migration columns ---
    embedding_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Per-corpus embedding overrides: provider, model, dimensions, api_key_encrypted",
    )
    applied_embedding: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="What the live collection actually contains: provider, model, dimensions",
    )

    # COB's built-in knowledge base (cob/system.py): read-only through the API.
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(  # type: ignore[name-defined]
        back_populates="corpora"
    )
    documents: Mapped[list["Document"]] = relationship(  # type: ignore[name-defined]
        back_populates="corpus", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["Chunk"]] = relationship(  # type: ignore[name-defined]
        back_populates="corpus", cascade="all, delete-orphan"
    )
    migrations: Mapped[list["CorpusMigration"]] = relationship(  # type: ignore[name-defined]
        back_populates="corpus", cascade="all, delete-orphan"
    )
