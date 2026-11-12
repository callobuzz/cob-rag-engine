"""Request and response bodies of the value report routes (TV4)."""

from typing import Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt

from callobuzz_rag.conversation.schema import TemplateKey
from callobuzz_rag.core.schemas import StrictRequest

MAX_MINOR = 10**10


class ValueSettingsRequest(StrictRequest):
    """The owner's own numbers, in paise (cents): what a qualified lead and a request to talk
    to a person are worth, what an hour of staff time costs, and how many minutes a person would
    have spent on one answered question."""

    lead_value_minor: StrictInt = Field(..., ge=0, le=MAX_MINOR)
    handoff_value_minor: StrictInt = Field(..., ge=0, le=MAX_MINOR)
    staff_hour_minor: StrictInt = Field(..., ge=0, le=MAX_MINOR)
    minutes_per_answer: StrictInt = Field(..., ge=0, le=120)
    email_enabled: StrictBool = True
    industry_defaults: TemplateKey | None = Field(
        None, description="The kind of business whose suggested values were applied, if any"
    )


class ValueSettingsValues(BaseModel):
    lead_value_minor: int
    handoff_value_minor: int
    staff_hour_minor: int
    minutes_per_answer: int


class ValueSettingsOut(ValueSettingsValues):
    saved: bool = Field(description="False until the owner saves; the values are then suggested")
    currency: Literal["INR", "USD"]
    email_enabled: bool
    industry_defaults: str | None
    business_type: str = Field(description="The kind of business this workspace's main page says")
    suggested: ValueSettingsValues


class FormulaLineOut(BaseModel):
    key: Literal["leads", "handoffs", "time"]
    inputs: dict[str, int]
    amount_minor: int
    text: str = Field(description="The line in words, with the money already formatted")


class BreakdownOut(BaseModel):
    leads_minor: int
    handoffs_minor: int
    minutes_saved: int
    time_minor: int
    total_minor: int
    formula: list[FormulaLineOut]


class CountedQuestion(BaseModel):
    question: str
    count: int


class ValueInputsOut(BaseModel):
    conversations: int
    questions: int
    answered: int
    answered_after_hours: int
    leads_total: int
    leads_qualified: int = Field(
        description="Leads that count toward the estimate: qualified by the owner's rules, or "
        "every lead (spam excluded) when no qualification rules were defined"
    )
    leads_unrated: int = Field(
        0, description="Of `leads_qualified`, the leads of a knowledge base with no rules"
    )
    leads_partial: int
    handoffs: int
    bookings: int
    median_first_reply_ms: int | None
    top_questions: list[CountedQuestion]
    unanswered: list[CountedQuestion]


class ValueReportOut(BaseModel):
    month: str = Field(description="YYYY-MM, in the business's own time zone")
    live: bool = Field(description="True for the current month; closed months are frozen")
    currency: Literal["INR", "USD"]
    saved_settings: bool = Field(description="False when the owner has not set values yet")
    settings_used: ValueSettingsValues
    inputs: ValueInputsOut
    breakdown: BreakdownOut
    formula_text: list[str]
    after_hours_since: str | None = Field(
        description="The first day after-hours answers were counted (YYYY-MM-DD), when it falls "
        "inside or after this month; null once the month is fully counted"
    )


class ValueReportMonth(BaseModel):
    month: str
    total_minor: int
    currency: Literal["INR", "USD"]


class ValueReportsOut(BaseModel):
    items: list[ValueReportMonth]
