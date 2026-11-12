"""A plan: the four limits a tenant works under (accounts A6).

Each limit may be NULL (unlimited): knowledge bases, answered questions per UTC calendar
month, active documents, website widget keys. Exactly one plan is the default (partial
unique index `uq_plans_default`); new tenants get it unless told otherwise. A fresh
install has one, "Standard", with every limit unlimited, marked `is_builtin` (at most one
plan is; set by migrations only). A plan tenants use cannot be
deleted (`tenants.plan_id` is RESTRICT); a deactivated plan (`is_active = false`) cannot
be assigned, but the tenants on it keep it.
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

LIMIT_FIELDS = (
    "max_knowledge_bases",
    "monthly_questions",
    "max_documents",
    "max_widget_keys",
    "max_upload_mb",
    "max_document_tokens",
)


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("name", name="uq_plans_name"),
        CheckConstraint(
            "max_knowledge_bases IS NULL OR max_knowledge_bases >= 1",
            name="ck_plans_max_knowledge_bases",
        ),
        CheckConstraint(
            "monthly_questions IS NULL OR monthly_questions >= 1",
            name="ck_plans_monthly_questions",
        ),
        CheckConstraint(
            "max_documents IS NULL OR max_documents >= 1", name="ck_plans_max_documents"
        ),
        CheckConstraint(
            "max_widget_keys IS NULL OR max_widget_keys >= 1", name="ck_plans_max_widget_keys"
        ),
        CheckConstraint(
            "max_upload_mb IS NULL OR max_upload_mb >= 1", name="ck_plans_max_upload_mb"
        ),
        CheckConstraint(
            "max_document_tokens IS NULL OR max_document_tokens >= 1",
            name="ck_plans_max_document_tokens",
        ),
        # BIZ1 plan columns
        CheckConstraint(
            "website_pages_max IS NULL OR website_pages_max >= 1", name="ck_plans_website_pages_max"
        ),
        # /BIZ1 plan columns
        Index(
            "uq_plans_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
        Index(
            "uq_plans_internal",
            "is_internal",
            unique=True,
            postgresql_where=text("is_internal"),
        ),
        Index(
            "uq_plans_builtin",
            "is_builtin",
            unique=True,
            postgresql_where=text("is_builtin"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_knowledge_bases: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_questions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_documents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_widget_keys: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Lower per-plan caps on one upload and on one document's text; NULL = the engine default
    # (RAG_MAX_UPLOAD_BYTES, RAG_MAX_DOCUMENT_TOKENS). A plan cannot raise the engine's.
    max_upload_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_document_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Whether an answer served from the query cache counts toward the monthly question cap and
    # counter (and is refused at the cap). false: cached answers are free.
    count_cached_answers: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    # The plan the engine seeded ("Standard", migration phase11_006; marked by phase11_008).
    # Set only by migrations: the API never writes it.
    is_builtin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    # The "Internal" plan of the operator's own workspace (migration phase11_009): unlimited,
    # free, locked (no edits, deactivation or deletion) and assignable only to the operator
    # workspace. Set only by migrations and `accounts/operator.py`; the API never writes it.
    is_internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    # BIZ1 plan columns: the deployer decides whether and how much of a site a business may learn
    # (NULL pages = unlimited); `dashboard_features` is read by BIZ3 (simple business mode).
    website_learning_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    website_pages_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dashboard_features: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    # /BIZ1 plan columns
    # PIP1 profile: how this plan's questions are answered; NULL = the default profile.
    pipeline_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_profiles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # /PIP1 profile
    # PIP3 fair use: per-plan limits and policy (JSONB, missing keys = the defaults in
    # `fairuse/limits.py`); a business may override them in `tenant_pipeline_settings`.
    fair_use_limits: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    fair_use_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    # /PIP3 fair use
    # TV6 channel: what channels the plan includes (`channels/policy.py`; missing keys = defaults)
    channels: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    # /TV6 channel
    # P5 plans: whether businesses on this plan may use their own AI key (`billing/byok.py` is
    # the only reader). True for every plan that existed before; the seeded Free/Standard say no.
    byok_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    # /P5 plans
    # P6 plan choice: businesses may pick this plan themselves (sign-up link, Plan & billing).
    # False for every plan that existed before; the seeded Free/Standard/Pro say yes.
    self_service: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    # /P6 plan choice
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
