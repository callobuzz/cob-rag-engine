"""Handoff schemas (conversation layer C5)."""

import re
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from callobuzz_rag.core.schemas import StrictRequest

_HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
HandoffStatus = Literal["new", "handled"]


class CallbackWindow(StrictRequest):
    """When the visitor wants the call, in the business's time zone."""

    date: date
    start: str = Field(..., description="HH:MM, 24-hour")
    end: str = Field(..., description="HH:MM, 24-hour, after start")

    @field_validator("start", "end")
    @classmethod
    def _hm(cls, value: str) -> str:
        if not _HM.match(value):
            raise ValueError("use HH:MM, 24-hour (for example 16:30)")
        return value


class WidgetHandoffRequest(StrictRequest):
    """The visitor tapped a channel on the handoff card (a callback goes through
    /widget/lead). The page is the conversation's own, never a client-supplied URL."""

    session: str = Field(..., min_length=10, max_length=200)
    channel: Literal["whatsapp", "call", "email", "contact_page", "booking"]


class WidgetHandoffResponse(BaseModel):
    handoff_id: uuid.UUID
    status: Literal["recorded"] = "recorded"


class HandoffResponse(BaseModel):
    id: uuid.UUID
    corpus_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    channel: str
    reason: str
    reason_label: str
    language: str | None = None
    page_url: str | None = None
    office_open: bool | None = None
    status: HandoffStatus
    handled_at: datetime | None = None
    notified_at: datetime | None = None
    created_at: datetime
    ref_code: str | None = Field(
        None, description="The conversation's reference code (e.g. SD-4F7K), if it has one"
    )  # TV2 reference


class HandoffTranscriptMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class HandoffLead(BaseModel):
    id: uuid.UUID
    name: str | None = None
    phone: str | None = None
    email: str | None = None


class HandoffDetailResponse(HandoffResponse):
    summary: str | None = Field(None, description="2-3 lines; null when it was not stored")
    transcript: list[HandoffTranscriptMessage] = Field(
        default_factory=list, description="Masked, oldest first, at most 200"
    )
    lead: HandoffLead | None = Field(None, description="The lead of a callback request")


class HandoffListResponse(BaseModel):
    items: list[HandoffResponse]
    total: int
    offset: int
    limit: int


class UpdateHandoffRequest(StrictRequest):
    status: HandoffStatus


# TV2 reference
class ReferenceMatch(BaseModel):
    corpus_id: uuid.UUID
    conversation_id: uuid.UUID
    handoff_ids: list[uuid.UUID]
    lead_ids: list[uuid.UUID]


class ReferenceLookupResponse(BaseModel):
    ref: str = Field(..., description="The reference as written in full, e.g. SD-4F7K")
    matches: list[ReferenceMatch] = Field(
        ..., description="One per knowledge base that holds it, newest first"
    )


# /TV2 reference
