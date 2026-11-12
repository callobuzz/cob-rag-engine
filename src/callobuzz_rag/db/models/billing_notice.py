"""One row per billing notice e-mail sent, so a retry or a second beat run sends nothing twice (TV3).

`key` names the occasion: a renewal date (`2026-11-05`) or a pricing version id.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class BillingNotice(Base):
    __tablename__ = "billing_notices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "key", name="uq_billing_notices_tenant_kind_key"),
        CheckConstraint(
            "kind in ('renewal','price_change','price_change_cancelled','pending_change',"
            "'plan_lower')",
            name="ck_billing_notices_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_billing_notices_tenant_id_tenants"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
