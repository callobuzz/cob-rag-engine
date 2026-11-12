"""Service orders (P5): a business bought a service add-on (website design, installation help,
priority support); the deployer does the work and moves the order along.

One order per `service` grant. `public_note` is what the owner reads, `internal_note` never leaves
the operator's screens. The order opens a support request, so it is also in the Support inbox.
Goes with the workspace (CASCADE); the grant it belongs to is RESTRICT so a paid order's grant is
never deleted under it.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

ORDER_STATUSES = ("requested", "in_progress", "done", "cancelled")


class ServiceOrder(Base):
    __tablename__ = "service_orders"
    __table_args__ = (
        UniqueConstraint("grant_id", name="uq_service_orders_grant_id"),
        CheckConstraint(
            "status in ('requested','in_progress','done','cancelled')",
            name="ck_service_orders_status",
        ),
        CheckConstraint(
            "includes_support_months IS NULL OR includes_support_months BETWEEN 1 AND 36",
            name="ck_service_orders_support_months",
        ),
        CheckConstraint(
            "(owner_note IS NULL OR char_length(owner_note) <= 1000)"
            " AND (public_note IS NULL OR char_length(public_note) <= 1000)",
            name="ck_service_orders_notes",
        ),
        Index("ix_service_orders_tenant_created", "tenant_id", "created_at"),
        Index("ix_service_orders_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_service_orders_tenant_id_tenants"),
        nullable=False,
    )
    grant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("add_on_grants.id", ondelete="RESTRICT", name="fk_service_orders_grant_id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'requested'"), default="requested"
    )
    owner_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    public_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    support_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "support_requests.id", ondelete="SET NULL", name="fk_service_orders_support_request"
        ),
        nullable=True,
    )
    includes_support_months: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    support_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When the provider was e-mailed about this new order (claimed once; the hourly schedule
    # picks up an order whose purchase had no request to send it from, e.g. a gateway payment).
    provider_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
