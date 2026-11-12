"""Request and response models for COB, the built-in assistant (chat, history, starters).

COB's own SSE blocks (`cob_link`, `cob_search_results`) are defined here too: the dashboard
reads them from the `ui` event and from stored messages (`CobMessageOut.ui`).
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from callobuzz_rag.core.schemas import StrictRequest


class CobContext(StrictRequest):
    route: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="The dashboard path the person is on (location.pathname)",
    )
    kb_id: uuid.UUID | None = Field(None, description="The knowledge base selected in the shell")
    docs_slug: str | None = Field(
        None, max_length=200, description="The Docs page the person is reading, if any"
    )


class CobChatRequest(StrictRequest):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = Field(
        None, description="Continue this COB conversation; omit to start one"
    )
    context: CobContext | None = None
    language: Literal["en", "hi", "hi-Latn"] | None = Field(
        None, description="A hint from the interface; the language of the message wins"
    )


# COB5 proposals
class CobConfirmRequest(StrictRequest):
    confirm_name: str | None = Field(
        None,
        max_length=255,
        description="The exact name to type before a destructive action runs",
    )


# end COB5 proposals


class CobFeedbackRequest(StrictRequest):
    rating: Literal[1, -1]
    comment: str | None = Field(None, max_length=500)


class CobSource(BaseModel):
    """One help page COB used. Never a chunk id or a score."""

    index: int
    slug: str
    anchor: str
    title: str
    snippet: str
    route: str | None = None
    public_url: str | None = None


class CobLinkBlock(BaseModel):
    """The deep-link button: opens a dashboard screen or a Docs page."""

    kind: Literal["cob_link"] = "cob_link"
    label: str
    route: str = Field(description="A concrete dashboard path, parameters filled")
    docs_slug: str | None = Field(None, description="Set when the link is to a Docs page")


class CobSearchResultsBlock(BaseModel):
    """Search mode: matching help pages instead of a written answer."""

    kind: Literal["cob_search_results"] = "cob_search_results"
    reason: Literal[
        "question_cap_reached", "llm_unavailable", "docs_only", "balance_paused", "fair_use"
    ]
    items: list[CobSource]


# COB5 proposals
class CobArg(BaseModel):
    label: str
    value: str = Field(description="Display text, already formatted")


class CobActionCardBlock(BaseModel):
    """A change COB proposes. Nothing happens until the person confirms it."""

    kind: Literal["cob_action_card"] = "cob_action_card"
    proposal_id: uuid.UUID
    tool: str
    title: str
    summary: str = Field(description="Markdown, one or two sentences")
    args: list[CobArg]
    risk: Literal["low", "medium", "high"]
    destructive: bool
    confirm_name: str | None = Field(
        None, description="The exact name to type before a destructive action (else null)"
    )
    expires_at: datetime
    status: Literal["pending", "done", "failed", "cancelled", "expired"] = Field(
        "pending", description="Set when the card is read back from history"
    )


class CobActionResult(BaseModel):
    """What happened when a card was confirmed (also when the underlying route refused)."""

    proposal_id: uuid.UUID
    status: Literal["done", "failed"]
    message: str = Field(description="Markdown")
    link: CobLinkBlock | None = None
    secret: str | None = Field(
        None, description="A rotated key, shown once; stored and logged nowhere"
    )
    http_status: int
    code: str | None = Field(None, description="The route's error code when it failed")


class CobCancelResult(BaseModel):
    proposal_id: uuid.UUID
    status: Literal["cancelled"] = "cancelled"


# end COB5 proposals

CobUiBlock = Annotated[
    CobLinkBlock | CobSearchResultsBlock | CobActionCardBlock, Field(discriminator="kind")
]


class CobMessageOut(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    sources: list[CobSource] = Field(default_factory=list)
    ui: list[CobUiBlock] = Field(default_factory=list)
    rating: Literal[1, -1] | None = None


class CobConversationItem(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int


class CobConversationList(BaseModel):
    items: list[CobConversationItem]
    total: int


class CobConversationOut(BaseModel):
    id: uuid.UUID
    title: str | None
    messages: list[CobMessageOut]


class CobStartersOut(BaseModel):
    route: str
    page_slug: str | None = Field(
        None, description="The help page for this screen, null when there is none for the caller"
    )
    starters: list[str]


# COB6 -------------------------------------------------------------------------------------


class CobHistoryItem(StrictRequest):
    """One earlier message of the page view the visitor is in (public COB)."""

    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=2000)


class CobPublicChatRequest(StrictRequest):
    message: str = Field(..., min_length=1, max_length=1000)
    history: list[CobHistoryItem] = Field(
        default_factory=list,
        max_length=6,
        description="Earlier messages of this page view; masked and never stored",
    )
    language: Literal["en", "hi", "hi-Latn"] | None = Field(
        None, description="A hint from the interface; the language of the message wins"
    )


# end COB6
