"""Review inbox and outcome analytics schemas (conversation C7)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from callobuzz_rag.conversation.types import LangKey
from callobuzz_rag.core.schemas import StrictRequest

ReviewKind = Literal["no_answer", "low_confidence", "thumbs_down", "handoff"]
ReviewActionName = Literal["dismissed", "added_document", "added_answer", "added_rule"]


class ReviewExample(BaseModel):
    turn_id: UUID | None
    question: str
    at: datetime
    conversation_id: UUID | None


class ReviewItemOut(BaseModel):
    item_key: str
    kind: ReviewKind
    title: str
    count: int
    last_at: datetime
    topic_id: str | None
    examples: list[ReviewExample]
    suggested: list[ReviewActionName]


class ReviewList(BaseModel):
    items: list[ReviewItemOut]
    count: int


class AnswerPayload(StrictRequest):
    """An exact answer to add to the assistant: becomes a topic with a verbatim answer."""

    topic_name: str = Field(..., min_length=1, max_length=60)
    question: str = Field(..., min_length=1, max_length=200)
    answer: str = Field(..., min_length=1, max_length=2000)
    language: LangKey = "en"


class RulePayload(StrictRequest):
    instruction: str = Field(..., min_length=1, max_length=500)
    type: Literal[
        "communication", "context", "clarification", "escalation", "sales", "compliance"
    ] = "context"


class ReviewActionRequest(StrictRequest):
    item_key: str = Field(..., min_length=32, max_length=64)
    action: ReviewActionName
    answer: AnswerPayload | None = None
    rule: RulePayload | None = None
    base_revision: int | None = Field(
        None, description="The draft revision you edited; 0 when there is no draft yet."
    )


class ReviewActionOut(BaseModel):
    item_key: str
    action: str
    target: str | None
    draft_revision: int | None


class CountItem(BaseModel):
    key: str
    count: int


class OutcomesResponse(BaseModel):
    """What the assistant achieved over a period. `handoffs` and `leads` are null (and `notes`
    says why) until those tables exist; `cost_known` false means the cost is a floor (an
    unpriced model). `cost_usd` covers whole UTC days touching the period. `leads` counts every
    lead including spam; `lead_conversion_rate` leaves spam out of its denominator."""

    start: datetime
    end: datetime
    conversations: int
    turns: int
    resolved: int
    resolution_rate: float | None
    handoffs: int | None = None
    handoff_rate: float | None = None
    handoff_reasons: list[CountItem] = Field(default_factory=list)
    no_answer_turns: int
    no_answer_rate: float | None
    top_unanswered: list[CountItem]
    leads: int | None = None
    leads_by_status: list[CountItem] = Field(default_factory=list)
    leads_by_page: list[CountItem] = Field(default_factory=list)
    lead_conversion_rate: float | None = None
    languages: list[CountItem]
    labels: list[CountItem]
    off_topic: int
    abuse: int
    gate_pass_rate: float | None
    gate_near_misses: int = 0
    claims_removed: int
    rules_fired: list[CountItem]
    cost_usd: float
    cost_known: bool
    cost_per_conversation_usd: float | None
    notes: list[str] = Field(default_factory=list)
