"""Request and response models for the operator's view of COB's docs sync."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from callobuzz_rag.core.schemas import StrictRequest


class CobStatusOut(BaseModel):
    docs_version: str | None = Field(
        None,
        description="The docs version the COB knowledge base holds now (null before the first sync)",
    )
    current_version: str = Field(
        description="The docs version shipped with this engine; the knowledge base is synced to it"
    )
    up_to_date: bool = Field(description="True when the knowledge base holds the shipped docs")
    sync_state: Literal["idle", "queued", "running", "failed"]
    started_at: datetime | None = None
    synced_at: datetime | None = None
    document_count: int = 0
    last_error: str | None = None
    eval: dict[str, Any] | None = Field(None, description="The latest COB evaluation summary")
    corpus_id: uuid.UUID | None = Field(None, description="The COB knowledge base's id")


class CobResyncRequest(StrictRequest):
    eval: bool = Field(True, description="Run the COB evaluation after the sync")


class CobResyncResponse(BaseModel):
    queued: bool = Field(
        description="False when a sync was already queued or running, so no second one was sent"
    )


# COB7: the review list
class CobReviewItem(BaseModel):
    group: str = Field(description="Stable key of the question group (reworded questions share it)")
    question: str = Field(description="The question, masked of personal identifiers")
    count: int = Field(description="How many answers in this group were weak")
    last_at: datetime
    role: str = Field(description="Who asked, most often: admin, operator_owner, owner, member")
    language: str = Field(description="Language of the question: en, hi or hi-Latn")
    top_slug: str | None = Field(
        None, description="The help page COB cited most often, or null when it cited none"
    )
    reasons: list[Literal["no_answer", "claims_removed", "thumbs_down"]]
    comments: list[str] = Field(description="Comments left with thumbs down, masked")


class CobReviewResponse(BaseModel):
    items: list[CobReviewItem]


# COB9: platform docs
class CobDocumentOut(BaseModel):
    id: uuid.UUID
    name: str = Field(description="File name or record id")
    source: Literal["builtin", "platform"] = Field(
        description="`builtin` comes with the engine and is updated automatically; `platform` was added by the operator"
    )
    audience: Literal["admin", "customer", "public"] = Field(
        description="The least privileged role that COB may answer this document to"
    )
    uploaded_by: uuid.UUID | None = Field(None, description="The person who added a platform doc")
    ingested_at: datetime


class CobDocumentList(BaseModel):
    items: list[CobDocumentOut]
    corpus_id: uuid.UUID | None = Field(None, description="COB's knowledge base id (for uploads)")


class CobDocumentAudienceRequest(StrictRequest):
    audience: Literal["admin", "customer", "public"]
