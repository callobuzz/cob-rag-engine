"""GoldenPair model — ground-truth questions for evaluation and assistant tests (C7)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base
from callobuzz_rag.db.models.golden_set import (
    DIFFICULTIES,
    EXPECTED_BEHAVIOURS,
    ORIGINS,
    in_list,
)


class GoldenPair(Base):
    __tablename__ = "golden_pair"
    __table_args__ = (
        CheckConstraint(
            "expected_answer IS NOT NULL OR expected_behaviour IS NOT NULL",
            name="ck_golden_pair_expectation",
        ),
        CheckConstraint(
            "expected_behaviour IS NULL OR " + in_list("expected_behaviour", EXPECTED_BEHAVIOURS),
            name="ck_golden_pair_behaviour",
        ),
        CheckConstraint(in_list("origin", ORIGINS), name="ck_golden_pair_origin"),
        CheckConstraint(
            "difficulty IS NULL OR " + in_list("difficulty", DIFFICULTIES),
            name="ck_golden_pair_difficulty",
        ),
        CheckConstraint(
            "office_hours IS NULL OR office_hours IN ('open', 'closed')",
            name="ck_golden_pair_office_hours",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Table is "corpora" (Corpus.__tablename__), not "corpus". The DB constraint
    # was always right — `golden_pair_corpus_id_fkey REFERENCES corpora(id)` — so
    # only this declaration was wrong, and no migration is needed. Reads never
    # noticed because they don't sort tables; every write raised
    # NoReferencedTableError at flush, for admin keys too.
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False
    )
    set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("golden_sets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    expected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_chunk_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )
    pair_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'manual'"))
    # Assistant behaviour (conversation C7): checked without a model.
    expected_behaviour: Mapped[str | None] = mapped_column(String(12), nullable=True)
    expected_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    must_include: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    must_cite: Mapped[str | None] = mapped_column(String(500), nullable=True)
    history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    page_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    office_hours: Mapped[str | None] = mapped_column(String(6), nullable=True)
    # Tags (conversation C7).
    origin: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'human'"))
    difficulty: Mapped[str | None] = mapped_column(String(8), nullable=True)
    topic: Mapped[str | None] = mapped_column(String(60), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )
