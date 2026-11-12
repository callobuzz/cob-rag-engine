"""Tenant model — multi-tenant isolation above corpora.

`status` (accounts A4) is the one source of truth for whether a tenant may work:
`pending` (signed up, waiting for the operator), `active`, `suspended`. `is_active` is a
read-only property kept so responses and older callers reading it keep working.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base
from callobuzz_rag.db.models.plan import Plan

if TYPE_CHECKING:
    from callobuzz_rag.db.models.user import User

TENANT_STATUSES: Final = ("pending", "active", "suspended")


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("status in ('pending','active','suspended')", name="ck_tenants_status"),
        # At most one operator workspace (the house account).
        Index(
            "uq_tenants_operator",
            "is_operator",
            unique=True,
            postgresql_where=text("is_operator"),
        ),
        # At most one system workspace (COB's own, never listed or counted).
        Index(
            "uq_tenants_system",
            "is_system",
            unique=True,
            postgresql_where=text("is_system"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Superseded by `api_keys` (phase11_012: many named keys, each with a permission). Always
    # blank now; kept so the migration can be reversed. Nothing reads it.
    api_key_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        server_default=text("''"),
        comment="bcrypt hash of the tenant-level API key (tk_xxx)",
    )
    max_corpora: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Per-tenant knowledge-base limit overriding the plan's; NULL follows the plan",
    )
    # A6. Code that creates a tenant sets it (the requested or the default plan); the
    # INSERT-time default below is the safety net for any other insert through the ORM.
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="RESTRICT", name="fk_tenants_plan_id_plans"),
        nullable=False,
        index=True,
        default=select(Plan.id).where(Plan.is_default.is_(True)).scalar_subquery(),
    )
    # P6 plan choice: the self-service plan chosen at sign-up while the workspace waits for
    # approval; applied (and cleared) when the operator activates it. SET NULL: a deleted plan
    # only forgets the wish.
    requested_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="SET NULL", name="fk_tenants_requested_plan_id_plans"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
        default="active",
        index=True,
    )
    # The operator's own workspace (the house account): created with the super admin, on
    # the Internal plan, never suspended, deleted only with an extra confirmation. Set by
    # `accounts/operator.py`; the API never writes it.
    is_operator: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    # COB's built-in workspace (cob/system.py): owns the system knowledge base, never listed,
    # counted or changed through the API.
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_tenants_created_by_user_id_users"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    # Relationships
    # passive_deletes="all": the ORM never nulls a corpus's tenant_id (NOT NULL); the
    # database's RESTRICT refuses deleting a tenant that still owns corpora.
    corpora: Mapped[list["Corpus"]] = relationship(  # type: ignore[name-defined]
        back_populates="tenant", passive_deletes="all"
    )
    # The signer-up. lazy="raise": never loaded implicitly (async sessions cannot).
    created_by: Mapped["User | None"] = relationship(
        foreign_keys=[created_by_user_id], lazy="raise"
    )
    # lazy="raise": load explicitly (select the Plan by `plan_id`).
    plan: Mapped[Plan] = relationship(foreign_keys=[plan_id], lazy="raise")

    @property
    def is_active(self) -> bool:
        """True only for `active`: pending and suspended tenants cannot work."""
        return self.status == "active"
