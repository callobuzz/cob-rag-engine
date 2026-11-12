"""One assistant configuration version of a knowledge base (conversation layer C2).

Exactly one `published` row per corpus (partial unique index), at most one `draft`
(version NULL); older published rows are `archived`. Publishing assigns max(version)+1;
restoring copies an older config into a new published row (`restored_from`).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_ARCHIVED = "archived"


class AssistantConfigVersion(Base):
    __tablename__ = "assistant_configs"
    __table_args__ = (
        UniqueConstraint("corpus_id", "version", name="uq_assistant_configs_corpus_version"),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')", name="ck_assistant_configs_status"
        ),
        CheckConstraint(
            "(status = 'draft') = (version IS NULL)", name="ck_assistant_configs_draft_version"
        ),
        Index(
            "uq_assistant_configs_published",
            "corpus_id",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
        Index(
            "uq_assistant_configs_draft",
            "corpus_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_label: Mapped[str | None] = mapped_column(String(320), nullable=True)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    restored_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
