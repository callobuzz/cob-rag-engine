"""Request and response bodies of the first-run onboarding and avatar routes (BIZ4)."""

import datetime as dt
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from callobuzz_rag.conversation.schema import DayHours, TemplateKey, normalize_phone
from callobuzz_rag.core.schemas import StrictRequest


class AvatarOut(BaseModel):
    avatar_url: str = Field(
        description="The engine's own path for the picture; put it in the assistant's identity."
    )


class SuggestionOut(BaseModel):
    text: str = Field(description="A question a visitor could ask, e.g. What are your prices?")


class SuggestionsOut(BaseModel):
    items: list[SuggestionOut] = Field(description="At most six, best first")


class StartOnboardingRequest(StrictRequest):
    website: str = Field(
        ...,
        min_length=4,
        max_length=255,
        description="The business's website as typed: shreedental.in, www.shreedental.in or a "
        "full address.",
    )
    assistant_name: str | None = Field(
        None, min_length=1, max_length=40, description='e.g. "Shree Dental Assistant"'
    )
    avatar_url: str | None = Field(
        None, max_length=300, description="From POST .../assistant/avatar."
    )
    business_type: TemplateKey | None = Field(
        None, description='One of the eight templates; default "generic_support".'
    )
    whatsapp: str | None = Field(None, max_length=20)
    hours: list[DayHours] | None = Field(None, max_length=21)

    @field_validator("whatsapp")
    @classmethod
    def _whatsapp(cls, value: str | None) -> str | None:
        return normalize_phone(value)

    @field_validator("avatar_url")
    @classmethod
    def _avatar(cls, value: str | None) -> str | None:
        from callobuzz_rag.conversation.schema import Identity

        return Identity(avatar_url=value or None).avatar_url


class UpdateOnboardingRequest(StrictRequest):
    step: int | None = Field(  # TV5 brochure: website, brochures, install, try
        None, ge=1, le=4, description="Where the owner is in the wizard (1 to 4)."
    )
    status: Literal["dismissed", "done"] | None = Field(
        None, description="Skip the wizard, or finish it."
    )


class RefusedOrigin(BaseModel):
    origin: str = Field(description="A site the assistant loaded on that the key does not allow")
    last_seen_at: dt.datetime


class ConnectionOut(BaseModel):
    status: Literal["waiting", "connected"] = Field(
        description="connected: the assistant has loaded on an allowed site"
    )
    origin: str | None = Field(
        description="The allowed site it loaded on, e.g. https://shreedental.in"
    )
    first_seen_at: dt.datetime | None
    refused: list[RefusedOrigin] = Field(
        description="Sites it loaded on that the key does not allow yet (a www mismatch)"
    )


class LearningProgressOut(BaseModel):
    status: str = Field(description="idle, learning, ready, paused, blocked or failed")
    pages_learned: int
    pages_found: int
    first_pass_done: bool


# TV5 brochure
class FilesOut(BaseModel):
    count: int = Field(description="Files the business uploaded (brochures, price lists, menus)")
    learned: int = Field(description="How many of them the assistant has finished reading")


# /TV5 brochure


class OnboardingOut(BaseModel):
    step: int = Field(description="1 to 4: website, brochures, install, try")
    status: Literal["new", "in_progress", "done", "dismissed"]
    website: str | None
    kb_id: UUID | None
    widget_key_id: UUID | None
    snippet: str | None = Field(description="The script to paste into the website")
    connection: ConnectionOut
    learning: LearningProgressOut | None
    files: FilesOut = Field(  # TV5 brochure
        default_factory=lambda: FilesOut(count=0, learned=0),
        description="The files uploaded to the knowledge base, for the brochure step",
    )
