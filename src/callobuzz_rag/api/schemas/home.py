"""The business dashboard's Home (BIZ3): assistant, website and today's numbers."""

import datetime as dt
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from callobuzz_rag.api.schemas.fair_use import FairUseTile  # PIP3 fair use


class HomeAssistant(BaseModel):
    enabled: bool = Field(description="The published assistant answers visitors")
    published: bool = Field(description="An assistant has been published for this knowledge base")
    name: str | None = Field(description="The assistant's name, e.g. Shree Dental Assistant")
    avatar_url: str | None
    business_name: str | None
    # TV5 nudges
    human_path: bool | None = Field(
        None,
        description="A visitor who asks for a person can be reached to someone: a number or "
        "address on the business card, a usable contact channel, or the call-back form. "
        "null when there is no published assistant to ask",
    )
    # /TV5 nudges


class HomeSite(BaseModel):
    origin: str = Field(description="e.g. https://shreedental.in")
    status: str = Field(description="idle, discovering, learning, ready, paused, blocked or failed")
    pages_learned: int
    last_updated_at: dt.datetime | None = Field(description="When the site was last read")


class HomeWebsite(BaseModel):
    sites: list[HomeSite]
    learning: bool = Field(
        description="The engine, the plan and this knowledge base all allow learning from the website"
    )
    # BIZ4 onboarding
    connected: bool = Field(
        False, description="The assistant has loaded on an allowed site of the business"
    )
    # /BIZ4 onboarding


class HomeToday(BaseModel):
    date: dt.date = Field(description="Today in the business's time zone")
    timezone: str = Field(description="The business card's time zone (default Asia/Kolkata)")
    conversations: int = Field(description="Conversations started today")
    leads: int = Field(description="Contact details left today")
    handoffs_open: int = Field(description="People still waiting for a call back (all days)")


# BIZ4 onboarding
class HomeOnboarding(BaseModel):
    status: Literal["new", "in_progress", "done", "dismissed"]
    step: int = Field(description="1 to 4: where the owner stopped")


# /BIZ4 onboarding


# TV5 nudges
class HomeNudge(BaseModel):
    kind: Literal["no_human_path", "add_brochures", "set_values"] = Field(
        description="no_human_path: visitors who ask for a person cannot reach anyone; "
        "add_brochures: no uploaded file and few pages read; set_values: lead value never saved"
    )


# /TV5 nudges


# TV4 value
class HomeValueMonth(BaseModel):
    total_minor: int = Field(description="Estimated value so far this month, in minor units")
    currency: Literal["INR", "USD"]
    answered: int = Field(description="Questions answered without staff")
    leads_qualified: int
    handoffs: int = Field(description="Requests to talk to a person")


class HomeValue(BaseModel):
    """The value card: this month so far, from the owner's own settings (an estimate)."""

    month_to_date: HomeValueMonth
    saved_settings: bool = Field(description="False: the owner has not set their own values yet")
    last_month_total_minor: int | None = Field(
        description="The frozen estimate of last month; null when there is none"
    )


# /TV4 value


class HomeOut(BaseModel):
    kb_id: UUID | None = Field(description="null: the business has no knowledge base yet")
    kb_name: str | None
    assistant: HomeAssistant
    website: HomeWebsite
    today: HomeToday
    # BIZ4 onboarding
    onboarding: HomeOnboarding | None = Field(
        None, description="The first-run steps of the workspace; null when not asked for"
    )
    # /BIZ4 onboarding
    # PIP3 fair use
    fair_use: FairUseTile | None = Field(
        None, description="Set only when a usage limit is close or passed; otherwise null"
    )
    # /PIP3 fair use
    # TV4 value
    value: HomeValue | None = Field(
        None,
        description="The value card; null when the deployer switched it off, the workspace has "
        "no knowledge base yet, or it was not asked for",
    )
    # /TV4 value
    # TV5 nudges
    nudges: list[HomeNudge] = Field(
        default_factory=list,
        description="Things worth doing next, most important first; empty when all is well",
    )
    # /TV5 nudges
