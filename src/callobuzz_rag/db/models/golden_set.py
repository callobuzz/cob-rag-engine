"""A named set of golden questions for one knowledge base (conversation C7)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

EXPECTED_BEHAVIOURS = ("answer", "decline", "handoff", "lead", "smalltalk", "no_answer")
ORIGINS = ("human", "ai_generated", "from_feedback")
DIFFICULTIES = ("easy", "medium", "hard")
SET_SOURCES = ("owner", "template", "generated", "setup", "import")


def in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class GoldenSet(Base):
    __tablename__ = "golden_sets"
    __table_args__ = (
        CheckConstraint(in_list("source", SET_SOURCES), name="ck_golden_sets_source"),
        Index(
            "uq_golden_sets_default",
            "corpus_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    source: Mapped[str] = mapped_column(String(12), nullable=False, server_default=text("'owner'"))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )
