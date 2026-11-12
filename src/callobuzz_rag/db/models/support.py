"""Support requests from a business to the deployer (TV3; the tables are made by TV4's step 0).

`number` comes from the sequence `support_request_number_seq` and is shown with a hash sign.
Status: open = waiting for the deployer, answered = the deployer replied, waiting = the deployer
asked the business something, closed. `context` is the auto-attached workspace information.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

SUPPORT_NUMBER_SEQ = Sequence("support_request_number_seq", metadata=Base.metadata)


class SupportRequest(Base):
    __tablename__ = "support_requests"
    __table_args__ = (
        CheckConstraint(
            "status in ('open','answered','waiting','closed')", name="ck_support_requests_status"
        ),
        Index("ix_support_requests_tenant_created", "tenant_id", "created_at"),
        Index("ix_support_requests_status_last", "status", "last_message_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_support_requests_tenant_id_tenants"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(
        Integer, server_default=SUPPORT_NUMBER_SEQ.next_value(), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'open'"), default="open"
    )
    page: Mapped[str | None] = mapped_column(String(300), nullable=True)
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_support_requests_user_id_users"),
        nullable=True,
    )
    created_by_email: Mapped[str] = mapped_column(String(320), nullable=False)
    first_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"
    __table_args__ = (
        CheckConstraint(
            "author_kind in ('business','deployer')", name="ck_support_messages_author_kind"
        ),
        Index("ix_support_messages_request_created", "request_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_requests.id", ondelete="CASCADE", name="fk_support_messages_request"),
        nullable=False,
    )
    author_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    author_email: Mapped[str] = mapped_column(String(320), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
