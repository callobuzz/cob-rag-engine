"""Channels: a knowledge base's WhatsApp number, its chats and the webhook events (TV6).

`channel_connections`: one number per knowledge base and kind, and a number belongs to one
knowledge base only (`phone_number_id` is unique across the engine, so an inbound message is
routed unambiguously). The access token, and the PIN of an Embedded Signup number, are Fernet
tokens (`core/secrets.py`). `channel_threads`: one chat per (number, contact); the contact is
stored encrypted with a keyed hash for lookup. `channel_events`: every webhook event, stored
(payload encrypted) before the fast 200 and processed by a worker; unique per external id, so a
retry by Meta is stored once. Purged after seven days.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class ChannelConnection(Base):
    __tablename__ = "channel_connections"
    __table_args__ = (
        UniqueConstraint("corpus_id", "kind", name="uq_channel_connections_corpus_kind"),
        UniqueConstraint("phone_number_id", name="uq_channel_connections_phone_number_id"),
        CheckConstraint("kind in ('whatsapp')", name="ck_channel_connections_kind"),
        CheckConstraint(
            "status in ('pending','active','error','disabled')",
            name="ck_channel_connections_status",
        ),
        CheckConstraint("setup in ('embedded','manual')", name="ck_channel_connections_setup"),
        CheckConstraint(
            "meta_billing in ('business','deployer')", name="ck_channel_connections_meta_billing"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE", name="fk_channel_connections_corpus_id"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_channel_connections_tenant_id"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'whatsapp'"), default="whatsapp"
    )
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'pending'"), default="pending"
    )
    phone_number_id: Mapped[str] = mapped_column(String(20), nullable=False)
    waba_id: Mapped[str] = mapped_column(String(20), nullable=False)
    display_phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    verified_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    token_hint: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    pin_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    setup: Mapped[str] = mapped_column(String(10), nullable=False)
    coexistence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    meta_billing: Mapped[str] = mapped_column(String(10), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    last_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class ChannelThread(Base):
    __tablename__ = "channel_threads"
    __table_args__ = (
        UniqueConstraint("connection_id", "contact_hash", name="uq_channel_threads_contact"),
        CheckConstraint(
            "paused_by is null or paused_by in ('echo','dashboard')",
            name="ck_channel_threads_paused_by",
        ),
        Index("ix_channel_threads_corpus_inbound", "corpus_id", text("last_inbound_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_connections.id", ondelete="CASCADE", name="fk_channel_threads_conn"),
        nullable=False,
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE", name="fk_channel_threads_corpus_id"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_channel_threads_tenant_id"),
        nullable=False,
    )
    contact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contact_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    profile_name_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL", name="fk_channel_threads_conv"),
        nullable=True,
    )
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_outbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    bot_paused_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paused_by: Mapped[str | None] = mapped_column(String(10), nullable=True)
    opted_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opt_in_asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    follow_up_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # A follow-up that can never be sent (the owner replied): out of the queue for good.
    follow_up_skipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    unread_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class ChannelEvent(Base):
    __tablename__ = "channel_events"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", name="uq_channel_events_external"),
        CheckConstraint(
            "kind in ('message','echo','status','other')", name="ck_channel_events_kind"
        ),
        CheckConstraint(
            "status in ('received','processing','done','ignored','failed')",
            name="ck_channel_events_status",
        ),
        Index("ix_channel_events_thread_status", "thread_id", "status"),
        Index("ix_channel_events_received_at", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_connections.id", ondelete="CASCADE", name="fk_channel_events_conn"),
        nullable=False,
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE", name="fk_channel_events_corpus_id"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channel_threads.id", ondelete="CASCADE", name="fk_channel_events_thread"),
        nullable=True,
    )
    payload_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'received'"), default="received"
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When a worker took the event: the stall sweep counts from here, not from `received_at`.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When a failed send is tried again (Meta said "not now"): the sweep wakes the chat then.
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # The reply the model already wrote (encrypted JSON: the messages and how many went out), kept
    # while a throttled send waits for its retry so the model is paid for once. Cleared at the end.
    pending_reply_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
