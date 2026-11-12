"""Lead, withdrawal and knowledge-base webhook schemas (conversation layer C4)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator

from callobuzz_rag.api.schemas.handoffs import CallbackWindow
from callobuzz_rag.core.schemas import StrictRequest

LeadStatus = Literal["new", "contacted", "won", "lost", "spam"]
Qualification = Literal["qualified", "partial", "unqualified"]
CorpusWebhookEvent = Literal["lead.created", "handoff.requested"]


class WidgetLeadRequest(StrictRequest):
    session: str = Field(..., min_length=10, max_length=200)
    form_id: str = Field(..., pattern=r"^lf_[0-9a-f]{16}$")
    fields: dict[str, str] = Field(default_factory=dict, max_length=12)
    consent: bool = False
    website: str = Field("", max_length=500, description="Leave empty (spam trap)")
    callback_window: CallbackWindow | None = Field(
        None, description="Callback request (handoff card): preferred day and time window"
    )

    @field_validator("fields")
    @classmethod
    def _short_values(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if len(key) > 40 or len(item) > 500:
                raise ValueError("field names are at most 40 and values at most 500 characters")
        return value


class LeadConfirmation(BaseModel):
    message: str
    withdraw_url: str
    booking_url: str | None = None


class WidgetLeadResponse(BaseModel):
    status: Literal["received"] = "received"
    confirmation: LeadConfirmation


class LeadContact(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    custom: dict[str, Any] = Field(default_factory=dict)


class LeadSummary(BaseModel):
    id: UUID
    corpus_id: UUID
    knowledge_base: str
    contact: LeadContact
    status: LeadStatus
    qualification: Qualification | None = None
    trigger: str
    tags: list[str] = Field(default_factory=list)
    language: str | None = None
    page_url: str | None = None
    assignee_user_id: UUID | None = None
    created_at: datetime


class LeadListResponse(BaseModel):
    items: list[LeadSummary]
    total: int
    offset: int
    limit: int


class LeadConsentOut(BaseModel):
    notice_version: int
    wording_hash: str
    purpose: str
    language: str
    consented_at: datetime
    withdrawn_at: datetime | None = None


class TranscriptMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class LeadDetail(LeadSummary):
    summary: str | None = None
    notes: str | None = None
    utm: dict[str, str] | None = None
    callback_window: dict[str, Any] | None = None
    sources_consulted: list[str] = Field(default_factory=list)
    conversation_id: UUID | None = None
    ref_code: str | None = None  # TV2 reference: the conversation's reference code
    consents: list[LeadConsentOut] = Field(default_factory=list)
    transcript: list[TranscriptMessage] = Field(default_factory=list)
    retention_until: datetime
    contacted_at: datetime | None = None
    updated_at: datetime


class LeadUpdateRequest(StrictRequest):
    status: LeadStatus | None = None
    assignee_user_id: UUID | None = None
    notes: str | None = Field(None, max_length=5000)
    qualification: Qualification | None = None


class WithdrawRequest(StrictRequest):
    token: str = Field(..., min_length=20, max_length=512)


class WithdrawResponse(BaseModel):
    status: Literal["withdrawn"] = "withdrawn"


class CorpusWebhookCreateRequest(StrictRequest):
    url: HttpUrl
    events: list[CorpusWebhookEvent] = Field(..., min_length=1, max_length=2)
    secret: str = Field(..., min_length=16, max_length=256)

    @field_validator("url")
    @classmethod
    def _https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("Knowledge-base webhooks must use https")
        return value


class CorpusWebhookUpdateRequest(StrictRequest):
    enabled: bool


class CorpusWebhookResponse(BaseModel):
    id: UUID
    corpus_id: UUID
    url: str
    events: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class CorpusWebhookListResponse(BaseModel):
    webhooks: list[CorpusWebhookResponse]
    count: int
