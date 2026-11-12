"""Owner-defined judge criteria and per-knowledge-base evaluation settings (conversation C7)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class EvalCriterion(Base):
    __tablename__ = "eval_criteria"
    __table_args__ = (
        UniqueConstraint("corpus_id", "name", name="uq_eval_criteria_name"),
        CheckConstraint("threshold >= 0 AND threshold <= 1", name="ck_eval_criteria_threshold"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    definition: Mapped[str] = mapped_column(String(500), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.7"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class EvalSettings(Base):
    __tablename__ = "eval_settings"

    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), primary_key=True
    )
    judge_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    judge_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    judge_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thresholds: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )
