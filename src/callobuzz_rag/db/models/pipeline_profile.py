"""Pipeline profiles and the per-business profile choice (PIP1).

A profile bundles how a business's questions are answered (see `tiers/schema.py`); `config` is
one JSONB re-validated on read. `builtin_key` marks the three seeded profiles (free, standard,
premium; set by migration `phase16_001` only). Exactly one profile is the default. Plans and
`tenant_pipeline_settings` reference a profile with RESTRICT, so a profile in use cannot be
deleted; the settings row cascades with its business (PIP3 adds its fair-use columns there).
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
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

BUILTIN_KEYS = ("free", "standard", "premium")


class PipelineProfile(Base):
    __tablename__ = "pipeline_profiles"
    __table_args__ = (
        UniqueConstraint("name", name="uq_pipeline_profiles_name"),
        CheckConstraint(
            "builtin_key IS NULL OR builtin_key in ('free','standard','premium')",
            name="ck_pipeline_profiles_builtin_key",
        ),
        Index(
            "uq_pipeline_profiles_builtin_key",
            "builtin_key",
            unique=True,
            postgresql_where=text("builtin_key IS NOT NULL"),
        ),
        Index(
            "uq_pipeline_profiles_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    builtin_key: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class TenantPipelineSettings(Base):
    """One row per business the deployer gave a profile other than its plan's."""

    __tablename__ = "tenant_pipeline_settings"
    __table_args__ = (
        Index("ix_tenant_pipeline_settings_profile_id", "profile_id"),
        CheckConstraint(  # PIP3 fair use
            "state in ('ok','near','throttled','stopped')",
            name="ck_tenant_pipeline_settings_state",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_profiles.id", ondelete="RESTRICT"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # PIP3 fair use: the business's own limits and policy (absent key = follow the plan, JSON
    # null = unlimited), and the state the alert task last stored.
    fair_use_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    policy_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    state: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'ok'"), default="ok"
    )
    state_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # /PIP3 fair use
