"""Guided assistant setup schemas (conversation C8).

Names carry a `Setup` prefix: they become OpenAPI components the dashboard imports, and a
generic name (`GapItem`, `ApplyResponse`) would collide with later epics.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from callobuzz_rag.accounts.email_rules import ValidatedEmail
from callobuzz_rag.conversation.schema import DayHours, TemplateKey, normalize_phone
from callobuzz_rag.core.schemas import StrictRequest


def _http_url(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not value.lower().startswith(("https://", "http://")):
        raise ValueError("must be an http(s) URL")
    return value


class SetupStartRequest(StrictRequest):
    business_type: TemplateKey | None = None
    website_url: str | None = Field(None, max_length=2048)

    @field_validator("website_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _http_url(value)


class SetupPage(StrictRequest):
    url: str = Field(..., max_length=2048)
    title: str = Field("", max_length=300)
    text: str = Field(..., min_length=1, max_length=20000)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        checked = _http_url(value)
        if checked is None:
            raise ValueError("must be an http(s) URL")
        return checked


class SetupProposeRequest(StrictRequest):
    crawl_job_id: str | None = Field(None, max_length=64)
    pages: list[SetupPage] = Field(..., min_length=1, max_length=50)


class SetupProfile(StrictRequest):
    """What the owner confirmed about the business; it becomes the business card."""

    business_name: str | None = Field(None, max_length=120)
    phone: str | None = None
    whatsapp: str | None = None
    email: ValidatedEmail | None = None
    address: str | None = Field(None, max_length=300)
    website: str | None = Field(None, max_length=2048)
    hours: list[DayHours] = Field(default_factory=list, max_length=21)

    @field_validator("business_name", "address", "email", mode="before")
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("phone", "whatsapp")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        return normalize_phone(value)

    @field_validator("website")
    @classmethod
    def _site(cls, value: str | None) -> str | None:
        return _http_url(value)


class SetupAnswersRequest(StrictRequest):
    business_type: TemplateKey | None = None
    answers: dict[str, Any]
    profile: SetupProfile | None = None
    partial: bool = Field(
        False,
        description="Save progress: required questions may still be unanswered (generate "
        "checks them). Every answer given must be valid.",
    )


class SetupGenerateRequest(StrictRequest):
    wording: Literal["ai", "template"] = "ai"
    base_revision: int | None = Field(
        None,
        ge=0,
        description="The draft_revision to replace; null replaces whatever draft exists.",
    )


class SetupApplyRequest(StrictRequest):
    base_revision: int = Field(..., ge=0)
    note: str | None = Field(None, max_length=200)


class SetupSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    corpus_id: uuid.UUID
    status: str
    business_type: str | None
    website_url: str | None
    crawl_job_id: str | None
    proposal: dict[str, Any] | None
    profile: SetupProfile | None
    answers: dict[str, Any]
    provenance: dict[str, str]
    golden_set_id: uuid.UUID | None
    eval_run_id: uuid.UUID | None
    embedding_multilingual: bool | None = Field(
        None,
        description="The catalog flag of the knowledge base's embedding model; null = unknown.",
    )
    recommend_multilingual: bool = False
    created_at: datetime
    updated_at: datetime


class SetupProposalResponse(BaseModel):
    session: SetupSessionResponse
    proposal: dict[str, Any]


class SetupQuestion(BaseModel):
    id: str
    kind: str
    title: str
    help: str
    options: list[dict[str, str]]
    other: bool
    required: bool
    max_items: int | None
    prefill: Any = None
    skipped_reason: str | None = None


class SetupQuestionsResponse(BaseModel):
    items: list[SetupQuestion]
    profile: SetupProfile


class SetupGenerateResponse(BaseModel):
    session: SetupSessionResponse
    draft_revision: int
    config: dict[str, Any]
    provenance: dict[str, str]
    wording_used: Literal["ai", "template"]
    warnings: list[str]


class SetupTestsResponse(BaseModel):
    session: SetupSessionResponse
    golden_set_id: uuid.UUID
    eval_run_id: uuid.UUID
    cases: int


class SetupGapItem(BaseModel):
    question: str
    message: str


class SetupGapsResponse(BaseModel):
    run_status: str
    items: list[SetupGapItem]


class SetupApplyResponse(BaseModel):
    session: SetupSessionResponse
    version: int
