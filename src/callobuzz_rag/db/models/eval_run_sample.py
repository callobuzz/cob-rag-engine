"""One question of an evaluation run, with every score and reason (conversation C7)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

RATINGS = ("good", "acceptable", "poor")


class EvalRunSample(Base):
    __tablename__ = "eval_run_samples"
    __table_args__ = (
        CheckConstraint(
            "rating IS NULL OR rating IN ('good', 'acceptable', 'poor')",
            name="ck_eval_samples_rating",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    golden_pair_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("golden_pair.id", ondelete="SET NULL"), nullable=True
    )
    query_log_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("query_log.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    contexts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    scores: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    reasons: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    behaviour: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tags: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating: Mapped[str | None] = mapped_column(String(10), nullable=True)
    rated_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
