"""Request and response models for the public website's editor API (LP1)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from callobuzz_rag.core.schemas import StrictRequest
from callobuzz_rag.landing.schema import (
    BaseTheme,
    LandingContent,
    LandingLang,
    PageKey,
    ScriptedConversation,
)

AskStatusOut = Literal["ready", "not_connected", "key_inactive", "origin_missing", "kb_missing"]


class LandingSaveDraftRequest(StrictRequest):
    base_revision: int = Field(ge=0, description="The draft revision you loaded")
    content: LandingContent


class LandingSettingsRequest(StrictRequest):
    enabled: bool | None = Field(None, description="Switch the public website on or off")
    address: str | None = Field(
        None, max_length=300, description="https://host[:port], no path; null = use RAG_PUBLIC_URL"
    )


class LandingThemePreviewRequest(StrictRequest):
    brand_color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    accent_color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    base: BaseTheme = "light"


class LandingPublishRequest(StrictRequest):
    base_revision: int = Field(ge=0)
    change_note: str | None = Field(None, max_length=300)


class LandingRestoreRequest(StrictRequest):
    base_revision: int = Field(ge=0)


class LandingContrastRow(BaseModel):
    pair: str
    theme: str
    fg: str
    bg: str
    ratio: float


class LandingThemePreview(BaseModel):
    light: dict[str, str]
    dark: dict[str, str]
    report: list[LandingContrastRow]
    adjusted: list[str] = Field(description="Plain sentences about colours that were adjusted")
    css: str = Field(description="The colour variables as they will be served")


class LandingPublishedInfo(BaseModel):
    version: int
    published_at: datetime
    published_by: str


class LandingAskInfo(BaseModel):
    corpus_id: uuid.UUID | None
    widget_key_id: uuid.UUID | None
    status: AskStatusOut


class LandingWarning(BaseModel):
    code: str
    message: str


class LandingCopyWarning(BaseModel):
    path: str
    word: str
    sentence: str


class LandingAssetOut(BaseModel):
    id: uuid.UUID
    kind: Literal["logo", "poster", "video"]
    content_type: str
    bytes: int
    width: int | None
    height: int | None
    created_at: datetime
    url: str


class LandingState(BaseModel):
    enabled: bool
    address: str | None = Field(description="The address set for the website, if any")
    address_effective: str | None = Field(
        description="The address links and the sitemap use (the one above, else RAG_PUBLIC_URL)"
    )
    draft: LandingContent
    draft_revision: int
    draft_theme: LandingThemePreview
    published: LandingPublishedInfo | None
    ask: LandingAskInfo
    warnings: list[LandingWarning]
    copy_warnings: list[LandingCopyWarning]
    assets: list[LandingAssetOut]
    limits: dict[str, int] = Field(description="Longest allowed text per field, by field path")


class LandingVersionOut(BaseModel):
    version: int
    published_at: datetime
    published_by: str
    change_note: str | None


class LandingVersionList(BaseModel):
    items: list[LandingVersionOut]


class LandingPreviewLinkRequest(StrictRequest):
    page: PageKey
    lang: LandingLang = "en"


class LandingPreviewLinkOut(BaseModel):
    url: str = Field(description="Open this in the editor's preview frame; valid for 15 minutes")
    expires_at: datetime


class LandingPublic(BaseModel):
    """What the signed-out dashboard pages may know about the public website."""

    enabled: bool = Field(description="The public website is live at the root of this address")


# LP2 landing
class LandingPresetInfo(BaseModel):
    key: str
    name: str
    languages: list[LandingLang]


class LandingPresetList(BaseModel):
    items: list[LandingPresetInfo]


class LandingPresetOut(BaseModel):
    key: str
    lang: LandingLang
    conversation: ScriptedConversation
    sections: dict[str, dict[str, Any]] = Field(
        description="pain_wall, languages, whatsapp_journey and faq, as the section's own fields"
    )


class LandingGenerateRequest(StrictRequest):
    corpus_id: uuid.UUID = Field(description="One of the operator's own knowledge bases")
    preset: Literal["clinic", "store", "coaching", "real_estate", "salon", "services"] | None = (
        Field(None, description="The kind of business, to pick which passages to read")
    )
    language: LandingLang = "en"


class LandingGenerateSource(BaseModel):
    title: str
    heading: str


class LandingGenerateOut(BaseModel):
    conversation: ScriptedConversation
    sources: list[LandingGenerateSource]
    cost_note: str = Field(description="One plain sentence about what the call used")


# /LP2 landing


# LP4 landing
class LandingConnectAssistantRequest(StrictRequest):
    corpus_id: uuid.UUID = Field(description="One of the operator's own knowledge bases")


# /LP4 landing
