"""Schemas for widget keys (management) and the public widget endpoints."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from callobuzz_rag.conversation.schema import QuickReply
from callobuzz_rag.core.product import POWERED_BY_TEXT
from callobuzz_rag.core.schemas import StrictRequest
from callobuzz_rag.widget.security import normalize_origin


class WidgetAppearance(BaseModel):
    """How the chat bubble looks on the customer's site. Every field is optional."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, max_length=80, description="Header text of the chat panel")
    greeting: str | None = Field(
        None, max_length=500, description="First assistant message a visitor sees"
    )
    placeholder: str | None = Field(None, max_length=120, description="Input placeholder text")
    primary_color: str | None = Field(
        None, pattern=r"^#[0-9a-fA-F]{6}$", description="Brand color, e.g. #4f46e5"
    )
    position: Literal["right", "left"] | None = Field(None, description="Screen corner")
    show_sources: bool | None = Field(None, description="Show the documents an answer used")


def _normalize_origins(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        origin = normalize_origin(value)
        if origin not in seen:
            seen.append(origin)
    return seen


class CreateWidgetKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255, description="e.g. 'Acme website'")
    allowed_origins: list[str] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Sites allowed to use this key, e.g. ['https://acme.com']. '*' allows any.",
    )
    rate_limit_per_minute: int = Field(10, ge=1, le=600, description="Per visitor")
    daily_message_limit: int = Field(1000, ge=0, le=10_000_000, description="0 = unlimited")
    appearance: WidgetAppearance = Field(default_factory=WidgetAppearance)

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, values: list[str]) -> list[str]:
        return _normalize_origins(values)


class UpdateWidgetKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    allowed_origins: list[str] | None = Field(None, min_length=1, max_length=50)
    rate_limit_per_minute: int | None = Field(None, ge=1, le=600)
    daily_message_limit: int | None = Field(None, ge=0, le=10_000_000)
    appearance: WidgetAppearance | None = None
    is_active: bool | None = None

    @field_validator("allowed_origins")
    @classmethod
    def _origins(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else _normalize_origins(values)


class WidgetKeyResponse(BaseModel):
    id: uuid.UUID
    corpus_id: uuid.UUID
    name: str
    public_key: str = Field(..., description="Publishable key — safe to put in a web page")
    allowed_origins: list[str]
    rate_limit_per_minute: int
    daily_message_limit: int
    appearance: WidgetAppearance
    is_active: bool
    created_at: datetime
    embed_snippet: str = Field(..., description="Paste before </body> on the customer's site")


class ProactiveGreetingOut(BaseModel):
    id: str
    page_url_glob: str
    delay_seconds: int
    message: str = Field(..., description="In the assistant's primary language")
    frequency: Literal["once_per_session", "once_per_day", "always"]


# BIZ2 sensor
class WidgetSensorConfig(BaseModel):
    sample: float = Field(..., ge=0, le=1, description="Share of page views that report.")
    skip: list[str] = Field(
        default_factory=list, description="Paths the widget never reports (login, cart, ...)."
    )


# /BIZ2 sensor


class WidgetConfigResponse(BaseModel):
    """What widget.js needs to render, fetched with the pk_ key."""

    title: str
    greeting: str
    placeholder: str
    primary_color: str
    position: Literal["right", "left"]
    show_sources: bool
    assistant_name: str | None = None
    avatar_url: str | None = None
    ai_disclosure: str | None = Field(None, description='Header line, e.g. "AI assistant"')
    language: str | None = Field(None, description="The assistant's primary language")
    starters: list[QuickReply] = Field(
        default_factory=list,
        description="Suggestion chips shown under the greeting until the first message",
    )
    proactive: list[ProactiveGreetingOut] = Field(
        default_factory=list,
        description="Greetings the widget may show by itself, per page (the first due one wins)",
    )
    human_button: bool = Field(  # TV1 human
        default=False,
        description='Whether the header shows a "Talk to a person" button (the assistant is on, '
        "there is a way to reach someone, and the owner has not hidden it where the deployer "
        "allows that).",
    )
    # P3 legal
    privacy_url: str | None = Field(
        None,
        description="The privacy link for the footer: the business's own policy, else the "
        "deployer's published Privacy notice; null when neither exists.",
    )
    # /P3 legal
    # TV5 disclosure
    ai_badge: str | None = Field(
        None, description='The "AI assistant" label next to the name; null when not shown.'
    )
    disclosure_line: str | None = Field(
        None, description="The first line of every conversation, saying it is an AI."
    )
    disclosure_lines: dict[str, str] = Field(
        default_factory=dict,
        description="The first line in each language the assistant speaks, by language key.",
    )
    disclosure_required: bool = Field(
        False, description="The provider requires the badge and the first line."
    )
    # /TV5 disclosure
    sensor: WidgetSensorConfig | None = Field(  # BIZ2 sensor
        default=None,
        description="Present when this knowledge base learns its website and this page is on it: "
        "the widget then reports a short code of the page so a changed page is noticed.",
    )
    powered_by: dict[str, str] | None = Field(
        default_factory=lambda: {"text": POWERED_BY_TEXT},
        description='The footer line, `{"text": ...}`, never a link; null when the provider has '
        "hidden it in Branding. A config without the key shows the line.",
    )


class WidgetTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class WidgetChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, max_length=2000)
    history: list[WidgetTurn] = Field(
        default_factory=list,
        max_length=20,
        description="Earlier turns of this visitor's chat, oldest first (last few are used)",
    )
    session: str | None = Field(
        None,
        pattern=r"^ws_[A-Za-z0-9_-]{20,60}$",
        description="Opaque session token from a previous `meta` event",
    )
    page_url: str | None = Field(None, max_length=2048)
    utm: dict[str, str] | None = Field(None, description="Up to 5 UTM parameters")

    @field_validator("utm")
    @classmethod
    def _utm(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        if len(value) > 5 or any(len(k) > 40 or len(v) > 200 for k, v in value.items()):
            raise ValueError("utm: at most 5 keys; keys up to 40 and values up to 200 characters")
        return value


class WidgetEventRequest(StrictRequest):
    """A visitor's tap on a suggestion chip or a call-to-action button. Counters only: no
    free text is accepted or stored."""

    session: str = Field(
        ...,
        pattern=r"^ws_[A-Za-z0-9_-]{20,60}$",
        description="Opaque session token from a `meta` event",
    )
    type: Literal["cta_click", "cta_dismiss", "chip_click"]
    cta_type: Literal["link", "call", "whatsapp", "booking", "lead_form", "handoff"] | None = None

    @model_validator(mode="after")
    def _cta_needs_type(self) -> "WidgetEventRequest":
        if self.type.startswith("cta_") and self.cta_type is None:
            raise ValueError("cta_type is required for cta_click and cta_dismiss")
        return self
