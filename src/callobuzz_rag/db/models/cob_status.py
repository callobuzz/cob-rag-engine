"""COB's docs sync status: one row (`id = 1`).

Records which docs version the system knowledge base holds, which one is requested and whether
a sync is queued or running. Several app workers start together; the single
`UPDATE ... RETURNING` in `cob/sync.py` decides which of them enqueues the sync.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class CobStatus(Base):
    __tablename__ = "cob_status"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_cob_status_single_row"),
        CheckConstraint(
            "sync_state in ('idle','queued','running','failed')", name="ck_cob_status_sync_state"
        ),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    docs_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sync_state: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'idle'"), default="idle"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    document_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # COB7's latest evaluation summary.
    eval: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
