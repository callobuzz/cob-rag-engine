"""Request and response models for the legal pages (P3)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from callobuzz_rag.core.schemas import StrictRequest


class LegalPageAdmin(BaseModel):
    slug: str
    title: str
    published_version: int | None = Field(description="The newest published version, or null")
    published_at: datetime | None
    has_draft: bool
    draft_changed: bool = Field(
        description="The draft differs from what is published (or nothing is published yet)"
    )
    draft_revision: int = Field(description="0 when there is no draft yet")
    default_available: bool = Field(description="A starting template ships for this page")
    company_changed: bool = Field(
        description="Your company details changed since this page was published: publish again"
    )
    review_note: str


class LegalPageList(BaseModel):
    items: list[LegalPageAdmin]


class LegalVersionOut(BaseModel):
    version: int
    published_at: datetime
    published_by: str | None
    change_note: str | None


class LegalPageDetail(BaseModel):
    slug: str
    title: str
    markdown: str = Field(description="The draft, or the shipped template when there is none")
    revision: int = Field(description="Send back as base_revision when saving; 0 = no draft yet")
    is_default_template: bool = Field(description="markdown is the unedited shipped template")
    published: LegalVersionOut | None
    history: list[LegalVersionOut]
    missing: list[str] = Field(description="Placeholders in the text that have no value yet")
    company_changed: bool
    review_note: str


class LegalDraftRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=120)
    markdown: str = Field(max_length=100_000)
    base_revision: int = Field(ge=0, description="The revision you loaded; 0 when no draft exists")


class LegalPreviewRequest(StrictRequest):
    markdown: str = Field(max_length=100_000)


class LegalPreviewOut(BaseModel):
    html: str
    missing: list[str]


class LegalPublishRequest(StrictRequest):
    change_note: str | None = Field(None, max_length=300)
    confirm_reviewed: bool = Field(False, description="You have had this page reviewed")


class LegalAcceptanceOut(BaseModel):
    id: uuid.UUID
    accepted_at: datetime
    email: str
    tenant_id: uuid.UUID | None
    workspace: str | None
    page: str
    version: int
    via: str


class LegalAcceptancesOut(BaseModel):
    items: list[LegalAcceptanceOut]
    total: int


class PublicLegalItem(BaseModel):
    slug: str
    title: str
    version: int
    published_at: datetime
    url: str


class PublicLegalList(BaseModel):
    items: list[PublicLegalItem]


class PublicLegalPage(BaseModel):
    slug: str
    title: str
    version: int
    published_at: datetime
    html: str
    markdown: str


class AcceptLegal(StrictRequest):
    """The versions a person agreed to at sign-up."""

    terms: int | None = Field(None, ge=1)
    privacy: int | None = Field(None, ge=1)
