"""Fair-use counters and alert rows (PIP3).

`fair_use_counters`: one row per business, UTC day and metric (`pages_learned`, `fetches`,
`uploads`, `decisions_downgraded`, `decisions_paused`, `decisions_busy`), upserted atomically.
`fair_use_alerts`: one row per (business, metric, period, level) the first time a limit crosses
that level in that period, so a notice is sent exactly once per crossing even with several
processes racing. `tenant_id` NULL is the engine-wide rail. Both cascade with the business.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class FairUseCounter(Base):
    __tablename__ = "fair_use_counters"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    metric: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )


class FairUseAlert(Base):
    __tablename__ = "fair_use_alerts"
    __table_args__ = (
        Index(
            "uq_fair_use_alerts_crossing",
            "tenant_id",
            "metric",
            "period_key",
            "level_pct",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)
    level_pct: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
