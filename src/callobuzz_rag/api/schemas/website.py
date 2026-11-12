"""Request and response bodies of the website-learning routes (BIZ1)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from callobuzz_rag.core.schemas import StrictRequest

MAX_PATTERNS = 50
Pattern = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class WebsitePlanOut(BaseModel):
    learning_enabled: bool = Field(description="Whether the plan (and the engine) allows learning.")
    pages_cap: int = Field(description="Most pages the workspace's sites may hold together.")
    pages_learned: int = Field(description="Pages the workspace's sites hold now.")
    reason: str | None = Field(
        default=None,
        description="Why learning is off: engine, system, tenant, plan or kb; null when on.",
    )


class WebsiteSettingsOut(BaseModel):
    enabled: bool
    exclude_patterns: list[str]
    last_reread_at: datetime | None = None
    # BIZ2 freshness
    refresh_mode: str = Field(
        default="daily", description="off, monthly, weekly, daily, 6h or hourly."
    )
    adaptive: bool = Field(
        default=True, description="Check pages that change often more often (and quiet ones less)."
    )
    # /BIZ2 freshness


class SourceOut(BaseModel):
    id: uuid.UUID
    origin: str
    scope: str
    status: str = Field(
        description="idle, discovering, learning, ready, paused, blocked or failed."
    )
    pause_reason: str | None = None
    pages_found: int
    pages_learned: int
    pages_skipped: int
    first_pass_done_at: datetime | None = None
    last_run_started_at: datetime | None = None
    last_run_finished_at: datetime | None = None
    last_error_code: str | None = None


# BIZ2 freshness
class LearnKeyStateOut(BaseModel):
    set: bool = Field(description="Whether this knowledge base has a push link.")
    hint: str | None = Field(default=None, description="The last four characters of the key.")
    created_at: datetime | None = None


class LearnKeyOut(BaseModel):
    key: str = Field(description="The push key. Shown once; make a new link to see another.")
    push_url: str = Field(description="The address a website posts changed pages to.")
    hint: str


# /BIZ2 freshness


class WebsiteOut(BaseModel):
    settings: WebsiteSettingsOut
    plan: WebsitePlanOut
    sources: list[SourceOut]
    learn_key: LearnKeyStateOut = Field(  # BIZ2 freshness
        default_factory=lambda: LearnKeyStateOut(set=False),
        description="Whether the push link exists (never the key itself).",
    )
    unlearned_origins: list[str] = Field(
        description="Allowed sites of this knowledge base's widget keys that are not learned yet."
    )


class UpdateWebsiteRequest(StrictRequest):
    enabled: bool | None = None
    exclude_patterns: list[Pattern] | None = Field(
        default=None,
        max_length=MAX_PATTERNS,
        description="Paths to leave out, one per entry: `/blog/*`, `/careers`.",
    )
    # BIZ2 freshness
    refresh_mode: Literal["off", "monthly", "weekly", "daily", "6h", "hourly"] | None = Field(
        default=None, description="How often learned pages are checked for changes."
    )
    adaptive: bool | None = Field(
        default=None, description="Check pages that change often more often (and quiet ones less)."
    )
    # /BIZ2 freshness

    @field_validator("exclude_patterns")
    @classmethod
    def _unique(cls, value: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(value)) if value is not None else None


class LearnRequest(StrictRequest):
    origin: str = Field(
        ...,
        min_length=3,
        max_length=255,
        description="One of the allowed sites of this knowledge base's widget keys.",
    )


class RereadOut(BaseModel):
    queued: int


class PageOut(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    url: str
    title: str | None = None
    status: str = Field(
        description="discovered, queued, learned, skipped, excluded, failed or removed."
    )
    skip_reason: str | None = None
    duplicate_of: uuid.UUID | None = None
    http_status: int | None = None
    depth: int
    words: int | None = None
    in_sitemap: bool
    excluded_by_owner: bool
    document_id: uuid.UUID | None = None
    etag: str | None = None
    last_modified: str | None = None
    content_hash: str | None = None
    last_seen_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_changed_at: datetime | None = None
    check_count: int
    change_count: int


class PagesOut(BaseModel):
    items: list[PageOut]
    total: int
    counts: dict[str, int] = Field(description="Pages per status, for the filter chips.")
    reasons: dict[str, int] = Field(
        default_factory=dict,
        description="Skipped or left-out pages per skip reason, across the whole knowledge base.",
    )


class UpdatePageRequest(StrictRequest):
    excluded: bool
