"""The public website (LP1): one site row, frozen published versions, uploaded assets.

Exactly one `landing_sites` row (`id = 1`, a check enforces it) per install: one deployer per
install. It is created lazily by `landing.store.ensure_site`, never by the migration, so a
change of the shipped default never needs a data migration. The only links to other tables are
the two nullable "Ask it yourself" references; deleting the knowledge base or the key only
switches that button off. No corpus affinity otherwise.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

ASSET_KINDS = ("logo", "poster", "video", "og")
ASSET_CONTENT_TYPES = ("image/png", "image/webp", "video/mp4", "video/webm")

_NOW = text("now()")


def _in_list(values: tuple[str, ...]) -> str:
    return ",".join(f"'{v}'" for v in values)


class LandingSite(Base):
    __tablename__ = "landing_sites"
    __table_args__ = (CheckConstraint("id = 1", name="ck_landing_sites_single"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    draft: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    draft_theme: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ask_corpus_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpora.id", ondelete="SET NULL"), nullable=True
    )
    ask_widget_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("widget_keys.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW, nullable=False
    )
    updated_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class LandingVersion(Base):
    __tablename__ = "landing_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    theme: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    theme_css: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW, nullable=False
    )
    published_by: Mapped[str] = mapped_column(String(320), nullable=False)


class LandingAsset(Base):
    __tablename__ = "landing_assets"
    __table_args__ = (
        CheckConstraint(f"kind in ({_in_list(ASSET_KINDS)})", name="ck_landing_assets_kind"),
        CheckConstraint(
            f"content_type in ({_in_list(ASSET_CONTENT_TYPES)})",
            name="ck_landing_assets_content_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_NOW, nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
