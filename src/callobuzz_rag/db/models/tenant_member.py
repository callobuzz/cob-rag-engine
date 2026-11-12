"""Which users belong to which tenant, and as what (accounts A3; managed by A5).

The primary key `(tenant_id, user_id)` is the unique pair. Both foreign keys cascade:
deleting a tenant or a user removes the membership.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class TenantMember(Base):
    __tablename__ = "tenant_members"
    __table_args__ = (
        CheckConstraint("role in ('owner','member')", name="ck_tenant_members_role"),
        Index("ix_tenant_members_user_id", "user_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
