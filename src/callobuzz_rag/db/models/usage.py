"""Daily usage rollup — the meter a SaaS built on the engine bills from.

One row per (UTC day, corpus, tenant, source, payer, model). `source` says where the traffic came from:
`api` (POST /query), `stream` (POST /query/stream), `conversation` (conversation
messages) or `widget:<key id>` (the website chat widget), so a founder can bill
each customer — and each of their websites — separately. Rows are incremented with an atomic upsert, never read
and rewritten, so concurrent requests cannot lose counts.

Cost is what the request really cost, in USD micro-units (millionths, integers, so sums are
exact): the provider's own figure where it reports one (OpenRouter `usage.cost`), else a
price list. A request whose price could not be found adds to `unpriced_requests`, never a
made-up zero; `cost_micro` then holds only the known part. `payer` is `operator` (the
engine's own LLM key) or `byok` (a key the knowledge base stores or the request sent), and
`model` is the answer model. `cost_usd` is the old float column, no longer written.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    __table_args__ = (
        UniqueConstraint(
            "day",
            "corpus_id",
            "tenant_id",
            "source",
            "payer",
            "model",
            name="uq_usage_daily_day_corpus_tenant_source_payer_model",
        ),
        Index("ix_usage_daily_tenant_day", "tenant_id", "day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corpora.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised from the corpus so tenant totals need no join. Every corpus
    # belongs to a tenant (accounts A1), so this is always set.
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    payer: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'operator'")
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False, server_default=text("''"))
    reasoning_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    cost_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    unpriced_requests: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
