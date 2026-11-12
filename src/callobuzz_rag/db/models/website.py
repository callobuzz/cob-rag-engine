"""Website learning: the business's own site, read by the engine (BIZ1).

`website_settings` is one row per knowledge base (made lazily). `website_sources` is one site
(scope) of that knowledge base; `website_pages` every URL found on it and what became of it;
`website_runs` one pass over a site (a heartbeat tells a crashed worker from a running one).
Volatile per-page fields (ETag, hash, last seen) live here, never in the ingested record, so an
unchanged page keeps its fingerprint and is never embedded twice.
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
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

REFRESH_MODES = ("off", "monthly", "weekly", "daily", "6h", "hourly")
SOURCE_STATUSES = ("idle", "discovering", "learning", "ready", "paused", "blocked", "failed")
PAGE_STATUSES = ("discovered", "queued", "learned", "skipped", "excluded", "failed", "removed")
RUN_KINDS = ("first", "full", "check", "push", "sensor", "page")
RUN_STATUSES = ("running", "done", "failed", "stopped")

_NOW = text("now()")


def in_values(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({','.join(repr(v) for v in values)})"


class WebsiteSettings(Base):
    __tablename__ = "website_settings"
    __table_args__ = (
        CheckConstraint(
            in_values("refresh_mode", REFRESH_MODES), name="ck_website_settings_refresh"
        ),
        Index("uq_website_settings_learn_key", "learn_key_hash", unique=True),
    )

    corpus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpora.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    exclude_patterns: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    refresh_mode: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'daily'"), default="daily"
    )
    adaptive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    learn_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    learn_key_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)
    learn_key_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reread_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class WebsiteSource(Base):
    __tablename__ = "website_sources"
    __table_args__ = (
        UniqueConstraint("corpus_id", "scope", name="uq_website_sources_corpus_scope"),
        CheckConstraint(in_values("status", SOURCE_STATUSES), name="ck_website_sources_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corpora.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    origin: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'idle'"), default="idle"
    )
    pause_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pages_found: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    pages_learned: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    pages_skipped: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    first_pass_done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sitemap_urls: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    sitemap_etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sitemap_last_modified: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class WebsitePage(Base):
    __tablename__ = "website_pages"
    __table_args__ = (
        UniqueConstraint("source_id", "url_hash", name="uq_website_pages_source_url"),
        CheckConstraint(in_values("status", PAGE_STATUSES), name="ck_website_pages_status"),
        CheckConstraint(
            "sitemap_priority IS NULL OR sitemap_priority BETWEEN 0 AND 100",
            name="ck_website_pages_priority",
        ),
        Index("ix_website_pages_corpus_status", "corpus_id", "status"),
        Index(
            "ix_website_pages_next_check",
            "next_check_at",
            postgresql_where=text("status = 'learned' AND next_check_at IS NOT NULL"),
        ),
        Index("ix_website_pages_document", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("website_sources.id", ondelete="CASCADE"), nullable=False
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'discovered'"), default="discovered"
    )
    skip_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    depth: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    rank_score: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    in_sitemap: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    sitemap_lastmod: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sitemap_priority: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    http_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    etag: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    simhash: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    words: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excluded_by_owner: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    check_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    change_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    missing_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sensor_fingerprint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sensor_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class WebsiteRun(Base):
    __tablename__ = "website_runs"
    __table_args__ = (
        CheckConstraint(in_values("kind", RUN_KINDS), name="ck_website_runs_kind"),
        CheckConstraint(in_values("status", RUN_STATUSES), name="ck_website_runs_status"),
        Index(
            "uq_website_runs_open_discovery",
            "source_id",
            unique=True,
            postgresql_where=text("finished_at IS NULL AND kind IN ('first','full')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("website_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'running'"), default="running"
    )
    counts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
