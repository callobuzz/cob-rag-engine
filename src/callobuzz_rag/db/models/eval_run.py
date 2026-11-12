"""EvalRun model — stores RAGAS evaluation run results."""

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


class EvalRun(Base):
    __tablename__ = "eval_run"
    __table_args__ = (
        CheckConstraint("kind IN ('traffic', 'golden')", name="ck_eval_run_kind"),
        CheckConstraint("mode IS NULL OR mode IN ('fast', 'full')", name="ck_eval_run_mode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # The corpus the run was scoped to; NULL for a run over every corpus.
    corpus_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corpora.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    triggered_by: Mapped[str] = mapped_column(
        String(20), nullable=False, doc="manual | schedule | config_change | publish | setup"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", doc="queued | running | completed | failed"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # --- conversation C7: golden runs and experiments ---
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'traffic'"))
    set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("golden_sets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mode: Mapped[str | None] = mapped_column(String(4), nullable=True)
    target: Mapped[str | None] = mapped_column(String(12), nullable=True)
    config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    judge_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    thresholds: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    criteria: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    totals: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # False when some model call had no known price: actual_cost_usd is then a floor.
    actual_cost_known: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
