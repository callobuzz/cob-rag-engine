"""A named API key of one knowledge base (`ck_`) or one customer (`tk_`).

Before this table a knowledge base and a customer each had exactly one key, a hash column on
the owner. Now each owner has any number of named keys, so one agent or integration can be
cut off without breaking the others, and each key carries a permission.

Exactly one of `corpus_id` / `tenant_id` is set (a `ck_` key belongs to a knowledge base, a
`tk_` key to a customer); both cascade, so deleting the owner deletes its keys. Only the
bcrypt hash is stored. The key itself is `{ck|tk}_{id_short}_{random}`: `id_short` is the first
8 characters of the owner's id and `lookup` the first 8 of `random`, both non-secret, so a
request finds its candidate rows by index and runs bcrypt on at most one or two of them.
`lookup` is NULL for a key migrated from the old single-key column (its plaintext was never
kept); such a key is a candidate for every request to its owner until it is rotated. Revoking
deletes the row (the audit log keeps the history).

`permission`: `read` (search, ask, list, get), `write` (read plus add and change content and
non-destructive settings) or `full` (write plus delete and key management). The route-level
classification is `api/permissions.py`.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

PERMISSIONS = ("read", "write", "full")


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint(
            "(corpus_id IS NOT NULL) <> (tenant_id IS NOT NULL)", name="ck_api_keys_one_owner"
        ),
        CheckConstraint("permission in ('read','write','full')", name="ck_api_keys_permission"),
        Index("ix_api_keys_id_short", "id_short"),
        Index("ix_api_keys_corpus_id", "corpus_id"),
        Index("ix_api_keys_tenant_id", "tenant_id"),
        # At most one default key per owner: what the single-key rotate routes replace.
        Index(
            "uq_api_keys_default_corpus",
            "corpus_id",
            unique=True,
            postgresql_where=text("is_default AND corpus_id IS NOT NULL"),
        ),
        Index(
            "uq_api_keys_default_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_default AND tenant_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpora.id", ondelete="CASCADE"), nullable=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    id_short: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="First 8 chars of the owner's id (in the key)"
    )
    lookup: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        comment="First 8 chars of the key's random part (not a secret); NULL if migrated",
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_prefix: Mapped[str] = mapped_column(
        String(40), nullable=False, comment="What the dashboard shows of the key, never the key"
    )
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    permission: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'full'")
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_by: Mapped[str | None] = mapped_column(
        String(320), nullable=True, comment="Who made it: an e-mail, 'admin key' or a key's name"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Updated at most once a minute"
    )
