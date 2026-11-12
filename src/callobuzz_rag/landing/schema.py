"""The content of the public website: one validated document (LP1).

`LandingContent` is the whole draft and every published snapshot. Every text a visitor reads is
an `LText` (one string per language) with a maximum length; every link is site-relative or
https; every section type has its own model and unknown fields are refused, so a stored
document can never carry markup, a script URL or a field the renderer does not know. The shape
is pinned by `docs/internal/plans/epics/landing/README.md` ("Content schema") and LP3's "Page
models"; a change needs every lane to agree.
"""

import functools
import json
import re
import uuid
from collections.abc import Iterator
from importlib import resources
from typing import Annotated, Any, Final, Literal, get_args

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from callobuzz_rag.core.schemas import StrictRequest

LandingLang = Literal["en", "hi", "hi-Latn"]
LANGS: Final[tuple[str, ...]] = get_args(LandingLang)
LANG_PREFIX: Final[dict[str, str]] = {"en": "", "hi": "/hi", "hi-Latn": "/hi-latn"}
PageKey = Literal["home", "pricing", "features", "about", "contact"]
PAGE_KEYS: Final[tuple[str, ...]] = get_args(PageKey)
SectionType = Literal[
    "hero",
    "trust_strip",
    "pain_wall",
    "how_it_works",
    "grounded",
    "languages",
    "whatsapp_journey",
    "leads",
    "human_handoff",
    "value_report",
    "bento",
    "proof",
    "pricing_teaser",
    "privacy",
    "faq",
    "video",
    "final_cta",
]
REQUIRED_SECTIONS: Final = frozenset({"hero", "final_cta"})
FontPairing = Literal["neutral", "engineered", "editorial", "warm", "bold", "house"]
BaseTheme = Literal["light", "dark", "auto"]
Currency = Literal["INR", "USD"]
IconKey = Literal[
    "chat",
    "clock",
    "shield",
    "globe",
    "chart",
    "lock",
    "users",
    "bolt",
    "book",
    "phone",
    "mail",
    "check",
    "star",
    "heart",
    "tag",
    "rupee",
]

LText = dict[LandingLang, str]

_HEX = r"^#[0-9a-fA-F]{6}$"
_TIME = r"^([01]\d|2[0-3]):[0-5]\d$"
_REF = r"^[23456789ABCDEFGHJKMNPQRSTVWXYZ]{2}-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{4}$"
_SITE_PATH = re.compile(r"^/(?!/)[^\s\\]*$")
_HTTPS = re.compile(r"^https://[^\s/\\][^\s\\]*$")
_CONTACT = re.compile(r"^(mailto:[^\s<>]+|tel:\+?[0-9 ()-]{5,24})$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class MaxLen:
    """Validator for one `LText`: strips, drops empty languages, enforces a maximum length.

    The class (not a closure) so `limits()` can read the maximum back from the field metadata.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit

    def __call__(self, value: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for lang, text in value.items():
            clean = text.strip()
            if _CONTROL.search(clean):
                raise ValueError(f"text must not contain control characters ({lang})")
            if len(clean) > self.limit:
                raise ValueError(f"text must be at most {self.limit} characters ({lang})")
            if clean:
                out[lang] = clean
        return out


def _lt(limit: int) -> Any:
    return AfterValidator(MaxLen(limit))


L20 = Annotated[LText, _lt(20)]
L24 = Annotated[LText, _lt(24)]
L30 = Annotated[LText, _lt(30)]
L40 = Annotated[LText, _lt(40)]
L60 = Annotated[LText, _lt(60)]
L70 = Annotated[LText, _lt(70)]
L80 = Annotated[LText, _lt(80)]
L90 = Annotated[LText, _lt(90)]
L120 = Annotated[LText, _lt(120)]
L140 = Annotated[LText, _lt(140)]
L160 = Annotated[LText, _lt(160)]
L200 = Annotated[LText, _lt(200)]
L240 = Annotated[LText, _lt(240)]
L280 = Annotated[LText, _lt(280)]
L300 = Annotated[LText, _lt(300)]
L400 = Annotated[LText, _lt(400)]
L600 = Annotated[LText, _lt(600)]


def _check_href(value: str, *, contact: bool) -> str:
    if len(value) > 300:
        raise ValueError("link is too long")
    if _SITE_PATH.match(value) or _HTTPS.match(value):
        return value
    if contact and _CONTACT.match(value):
        return value
    raise ValueError(
        "a link must start with / (a page of this site) or https://"
        + (", or be a mailto: or tel: link" if contact else "")
    )


Href = Annotated[str, AfterValidator(lambda v: _check_href(v, contact=False))]
ContactHref = Annotated[str, AfterValidator(lambda v: _check_href(v, contact=True))]


class _Model(StrictRequest):
    """Every landing model: unknown fields refused, aliases written back out (`from`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


# ---------------------------------------------------------------- theme and conversation


class ThemeSettings(_Model):
    brand_color: str = Field("#0f766e", pattern=_HEX)
    accent_color: str | None = Field(None, pattern=_HEX)
    font_pairing: FontPairing = "neutral"
    base: BaseTheme = "light"
    button_shape: Literal["soft", "round"] = "round"
    logo_asset_id: uuid.UUID | None = None


class ChatTurn(_Model):
    role: Literal["visitor", "assistant"]
    text: L280
    source: L60 | None = None

    @model_validator(mode="after")
    def _limits_by_role(self) -> "ChatTurn":
        if self.role == "visitor":
            for lang, text in self.text.items():
                if len(text) > 140:
                    raise ValueError(f"a visitor line is at most 140 characters ({lang})")
            if self.source:
                raise ValueError("only the assistant's lines can name a source")
        return self


class LeadCardMock(_Model):
    name: str = Field(..., max_length=40)
    phone_masked: str = Field(..., max_length=20, pattern=r"^[+0-9 •x*-]+$")
    looking_for: L60
    came_from: Literal["website", "whatsapp"] = "website"
    consent: L160


class ScriptedConversation(_Model):
    preset: str | None = Field(None, max_length=40)
    assistant_name: L30
    business_name: L60
    turns: list[ChatTurn] = Field(..., min_length=2, max_length=6)
    lead: LeadCardMock | None = None
    generated: bool = False

    @model_validator(mode="after")
    def _alternates(self) -> "ScriptedConversation":
        for index, turn in enumerate(self.turns):
            want = "visitor" if index % 2 == 0 else "assistant"
            if turn.role != want:
                raise ValueError(
                    "a conversation starts with the visitor and then alternates "
                    "visitor, assistant, visitor, assistant"
                )
        return self


# ---------------------------------------------------------------- small shared items


class TitleText(_Model):
    title: L40
    text: L160


class FaqItem(_Model):
    q: L120
    a: L600


class NavLink(_Model):
    label: L24
    page: PageKey | None = None
    href: ContactHref | None = None

    @model_validator(mode="after")
    def _one_target(self) -> "NavLink":
        if (self.page is None) == (self.href is None):
            raise ValueError("a link points at a page of this site or at an address, not both")
        return self


# ---------------------------------------------------------------- sections


class SectionBase(_Model):
    id: str = Field(..., pattern=r"^[a-z0-9-]{1,40}$")
    enabled: bool = True
    narrator: L140 | None = None
    eyebrow: L40 | None = None
    heading: L90 | None = None
    body: L400 | None = None


class HeroSection(SectionBase):
    type: Literal["hero"] = "hero"
    headline: L60
    sub: L160 | None = None
    primary_label: L30 | None = None
    primary_href: Href = "/signup"
    microcopy: L80 | None = None
    ask_label: L30 | None = None
    whatsapp_label: L30 | None = None
    show_pricing_link: bool = True
    conversation: ScriptedConversation
    visual: Literal["both", "website", "whatsapp"] = "both"
    backdrop_video: bool = False


class TrustStripSection(SectionBase):
    type: Literal["trust_strip"] = "trust_strip"
    chips: list[L24] = Field(default_factory=list, max_length=8)
    statement: L80 | None = None


class PainQuestion(_Model):
    text: L60
    channel: Literal["website", "whatsapp"] = "website"
    time: str = Field(..., pattern=_TIME)


class PainWallSection(SectionBase):
    type: Literal["pain_wall"] = "pain_wall"
    questions: list[PainQuestion] = Field(default_factory=list, max_length=12)
    punchline: L140 | None = None


class HowItWorksSection(SectionBase):
    type: Literal["how_it_works"] = "how_it_works"
    steps: list[TitleText] = Field(..., min_length=3, max_length=3)
    promise: L60 | None = None


class CaughtExample(_Model):
    guess: L140
    replaced_by: L140


class GroundedSection(SectionBase):
    type: Literal["grounded"] = "grounded"
    behaviours: list[TitleText] = Field(..., min_length=3, max_length=3)
    fact_check_line: L160
    caught_example: CaughtExample


class LanguageSample(_Model):
    lang: LandingLang
    question: str = Field(..., max_length=80)
    answer: str = Field(..., max_length=160)


class LanguagesSection(SectionBase):
    type: Literal["languages"] = "languages"
    samples: list[LanguageSample] = Field(..., min_length=3, max_length=3)


class JourneyMessage(_Model):
    sender: Literal["visitor", "assistant", "owner"] = Field(..., alias="from")
    text: L140
    attachment: L40 | None = None


class JourneyChapter(_Model):
    time: str = Field(..., pattern=_TIME)
    title: L40
    messages: list[JourneyMessage] = Field(..., min_length=1, max_length=4)


class WhatsAppJourneySection(SectionBase):
    type: Literal["whatsapp_journey"] = "whatsapp_journey"
    chapters: list[JourneyChapter] = Field(..., min_length=2, max_length=5)
    fallback: Literal["email_handoff"] = "email_handoff"


class LeadsSection(SectionBase):
    type: Literal["leads"] = "leads"
    lead: LeadCardMock
    inbox_count: int = Field(0, ge=0, le=99)


class HumanHandoffSection(SectionBase):
    type: Literal["human_handoff"] = "human_handoff"
    ref_code: str = Field(..., pattern=_REF)
    prefill: L300
    channels: list[Literal["whatsapp", "call", "email"]] = Field(..., min_length=1, max_length=3)


class ValueReportSection(SectionBase):
    type: Literal["value_report"] = "value_report"
    questions: int = Field(..., ge=0, le=1_000_000)
    after_hours: int = Field(..., ge=0, le=1_000_000)
    leads: int = Field(..., ge=0, le=1_000_000)
    value_text: L40
    caption: L120

    @model_validator(mode="after")
    def _says_estimated(self) -> "ValueReportSection":
        if "estimated" not in self.caption.get("en", "").casefold():
            raise ValueError("the caption must say the value is estimated")
        return self


class BentoTile(_Model):
    icon: IconKey
    title: L40
    text: L120


class BentoSection(SectionBase):
    type: Literal["bento"] = "bento"
    tiles: list[BentoTile] = Field(..., min_length=3, max_length=7)


class ProofCase(_Model):
    quote: L200
    number: str = Field(..., max_length=20)
    method: L120
    name: str = Field(..., max_length=60)


class ProofLink(_Model):
    label: L40
    href: Href


class ProofSection(SectionBase):
    type: Literal["proof"] = "proof"
    cases: list[ProofCase] = Field(default_factory=list, max_length=3)
    gallery: list[ProofLink] = Field(default_factory=list, max_length=3)


class PricingTeaserSection(SectionBase):
    type: Literal["pricing_teaser"] = "pricing_teaser"
    plan_ids: list[uuid.UUID] = Field(default_factory=list, max_length=4)
    note: L120 | None = None


class PrivacySection(SectionBase):
    type: Literal["privacy"] = "privacy"
    sentences: list[L120] = Field(default_factory=list, max_length=8)


class FaqSection(SectionBase):
    type: Literal["faq"] = "faq"
    items: list[FaqItem] = Field(default_factory=list, max_length=12)
    structured_data: bool = False


class VideoSection(SectionBase):
    type: Literal["video"] = "video"
    video_asset_id: uuid.UUID
    poster_asset_id: uuid.UUID
    caption: L120 | None = None
    placement: Literal["section", "hero_backdrop"] = "section"


class FinalCtaSection(SectionBase):
    type: Literal["final_cta"] = "final_cta"
    bubble: L140
    button_label: L30
    sub: L120 | None = None
    whatsapp_label: L30 | None = None


Section = Annotated[
    HeroSection
    | TrustStripSection
    | PainWallSection
    | HowItWorksSection
    | GroundedSection
    | LanguagesSection
    | WhatsAppJourneySection
    | LeadsSection
    | HumanHandoffSection
    | ValueReportSection
    | BentoSection
    | ProofSection
    | PricingTeaserSection
    | PrivacySection
    | FaqSection
    | VideoSection
    | FinalCtaSection,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------- pages


class CalculatorSettings(_Model):
    enabled: bool = True
    slider_max: int = Field(5000, ge=100, le=1_000_000)
    slider_default: int = Field(900, ge=10, le=1_000_000)
    est_answer_minor: dict[Currency, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _sane(self) -> "CalculatorSettings":
        if self.slider_default > self.slider_max:
            raise ValueError("the calculator's starting value cannot be above its maximum")
        if any(v < 1 for v in self.est_answer_minor.values()):
            raise ValueError("the cost of one answer must be at least 1")
        return self


class PricingCard(_Model):
    label: L40 | None = None
    who_for: L80
    highlight: L20 | None = None
    variants: dict[Currency, dict[Literal["monthly", "yearly"], uuid.UUID]] = Field(
        ..., min_length=1
    )
    cta: Literal["signup", "contact"] = "signup"

    @model_validator(mode="after")
    def _each_currency_has_a_plan(self) -> "PricingCard":
        if any(not periods for periods in self.variants.values()):
            raise ValueError("each currency needs at least one plan")
        return self


class PricingPage(_Model):
    enabled: bool = True
    headline: L90
    sub: L200 | None = None
    tax_line_in: L120 | None = None
    tax_line_global: L120 | None = None
    cards: list[PricingCard] = Field(default_factory=list, max_length=4)
    credit_box: L600 | None = None
    credit_examples: list[L120] = Field(default_factory=list, max_length=3)
    calculator: CalculatorSettings = Field(default_factory=CalculatorSettings)
    show_packs: bool = True
    show_services: bool = True
    show_whatsapp_fees: bool = True
    show_spend_cap: bool = True
    comparison_table: bool = True
    faq: list[FaqItem] = Field(default_factory=list, max_length=10)


class FeatureItem(_Model):
    icon: IconKey
    title: L40
    text: L200


class FeatureGroup(_Model):
    title: L40
    items: list[FeatureItem] = Field(..., min_length=1, max_length=8)


class ChecklistItem(_Model):
    q: L80
    a: L240


class FeaturesPage(_Model):
    enabled: bool = True
    headline: L90
    sub: L200 | None = None
    groups: list[FeatureGroup] = Field(default_factory=list, max_length=6)
    checklist: list[ChecklistItem] = Field(default_factory=list, max_length=6)
    cta_label: L30


class ValueItem(_Model):
    title: L40
    text: L200


class TeamMember(_Model):
    name: str = Field(..., max_length=60)
    role: L60


class AboutPage(_Model):
    enabled: bool = True
    headline: L90
    story: list[L600] = Field(default_factory=list, max_length=6)
    values: list[ValueItem] = Field(default_factory=list, max_length=4)
    team: list[TeamMember] = Field(default_factory=list, max_length=8)
    show_company_details: bool = True


class ContactPage(_Model):
    enabled: bool = True
    headline: L90
    sub: L200 | None = None
    form_enabled: bool = True
    topics: dict[Literal["sales", "support", "partnership", "other"], L40] = Field(
        default_factory=dict
    )
    show_whatsapp: bool = True
    show_phone: bool = True
    show_email: bool = True
    show_address: bool = True
    response_time: L60 | None = None
    consent: L200
    success: L200


# ---------------------------------------------------------------- site-wide


class NavSettings(_Model):
    links: list[NavLink] = Field(default_factory=list, max_length=6)
    show_sign_in: bool = True
    show_sign_up: bool = True


class FooterColumn(_Model):
    title: L40
    links: list[NavLink] = Field(default_factory=list, max_length=6)


class FooterSettings(_Model):
    columns: list[FooterColumn] = Field(default_factory=list, max_length=4)
    show_legal: bool = True
    note: L200 | None = None


class PageSeo(_Model):
    title: L60
    description: L160


class SeoSettings(_Model):
    home: PageSeo
    pricing: PageSeo
    features: PageSeo
    about: PageSeo
    contact: PageSeo
    og_headline: L70


class AnnouncementBar(_Model):
    enabled: bool = False
    text: L90 | None = None
    href: Href | None = None


class AskSettings(_Model):
    enabled: bool = False
    label: L30 = Field(default={"en": "Ask it yourself"})


class LandingContent(_Model):
    schema_version: Literal[1] = 1
    default_language: LandingLang = "en"
    languages: list[LandingLang] = Field(default=["en"], min_length=1)
    theme: ThemeSettings = Field(default_factory=ThemeSettings)
    home: list[Section] = Field(..., min_length=2, max_length=24)
    pricing: PricingPage
    features: FeaturesPage
    about: AboutPage
    contact: ContactPage
    nav: NavSettings = Field(default_factory=NavSettings)
    footer: FooterSettings = Field(default_factory=FooterSettings)
    seo: SeoSettings
    announcement: AnnouncementBar = Field(default_factory=AnnouncementBar)
    ask: AskSettings = Field(default_factory=AskSettings)

    @model_validator(mode="after")
    def _whole_document(self) -> "LandingContent":
        if len(set(self.languages)) != len(self.languages):
            raise ValueError("a language is listed twice")
        if self.default_language not in self.languages:
            raise ValueError("the default language must be one of the site's languages")
        ids = [s.id for s in self.home]
        if len(set(ids)) != len(ids):
            raise ValueError("two sections share the same id")
        for required in sorted(REQUIRED_SECTIONS):
            found = [s for s in self.home if s.type == required]
            if len(found) != 1 or not found[0].enabled:
                raise ValueError(
                    f"the {required} section is required, appears once and cannot be switched off"
                )
        missing = [
            path
            for path, text in _walk(self.model_dump(mode="json"))
            if self.default_language not in text
        ]
        if missing:
            raise ValueError(
                f"texts must have the default language ({self.default_language}): "
                + ", ".join(missing[:5])
            )
        return self


# ---------------------------------------------------------------- walking the texts


def is_ltext(value: object) -> bool:
    return (
        isinstance(value, dict)
        and bool(value)
        and all(k in LANGS and isinstance(v, str) for k, v in value.items())
    )


def _walk(data: Any, path: str = "") -> Iterator[tuple[str, dict[str, str]]]:
    """Every `LText` of a dumped document with its path (`home.hero.headline`)."""
    if is_ltext(data):
        yield path, data
    elif isinstance(data, dict):
        for key, value in data.items():
            yield from _walk(value, f"{path}.{key}" if path else str(key))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            label = f"[{index}]"
            if isinstance(item, dict) and isinstance(item.get("id"), str) and "type" in item:
                yield from _walk(
                    {k: v for k, v in item.items() if k not in ("id", "type")},
                    f"{path}.{item['id']}" if path else item["id"],
                )
            else:
                yield from _walk(item, f"{path}{label}")


def _enabled_parts(content: LandingContent) -> dict[str, Any]:
    dump = content.model_dump(mode="json")
    parts: dict[str, Any] = {
        "home": [s for s in dump["home"] if s.get("enabled", True)],
        "nav": dump["nav"],
        "footer": dump["footer"],
        "seo": dump["seo"],
    }
    for page in ("pricing", "features", "about", "contact"):
        if dump[page].get("enabled", True):
            parts[page] = dump[page]
    if dump["announcement"]["enabled"]:
        parts["announcement"] = dump["announcement"]
    if dump["ask"]["enabled"]:
        parts["ask"] = dump["ask"]
    return parts


def all_texts(content: LandingContent) -> list[tuple[str, dict[str, str]]]:
    """Every text of the enabled parts, in page order, with its path."""
    return [item for key, part in _enabled_parts(content).items() for item in _walk(part, key)]


def translation_gaps(content: LandingContent, lang: str) -> list[str]:
    """Paths of enabled texts that have no `lang` version (publishing needs none)."""
    if lang == content.default_language:
        return []
    return [path for path, text in all_texts(content) if not text.get(lang)]


# ---------------------------------------------------------------- limits for the editor


def text_limits() -> dict[str, int]:
    """Every text's maximum length, read from the field metadata: `hero.headline` -> 60.

    Keys are `<section type>.<field>` for sections and `<page>.<field>` for pages; list items
    add the field name (`faq.items.q`). The editor builds its forms and counters from this.
    """
    out: dict[str, int] = {}
    for model, prefix in _roots():
        _collect(model, prefix, out)
    return out


def _roots() -> list[tuple[type[_Model], str]]:
    roots: list[tuple[type[_Model], str]] = []
    for member in get_args(get_args(Section)[0]):
        roots.append((member, member.model_fields["type"].default))
    roots += [
        (PricingPage, "pricing"),
        (FeaturesPage, "features"),
        (AboutPage, "about"),
        (ContactPage, "contact"),
        (NavSettings, "nav"),
        (FooterSettings, "footer"),
        (SeoSettings, "seo"),
        (AnnouncementBar, "announcement"),
        (AskSettings, "ask"),
    ]
    return roots


def _max_of(metadata: list[Any]) -> int | None:
    for item in metadata:
        func = getattr(item, "func", None)
        if isinstance(func, MaxLen):
            return func.limit
    return None


def _models_in(annotation: Any) -> Iterator[type[_Model]]:
    if isinstance(annotation, type) and issubclass(annotation, _Model):
        yield annotation
        return
    for arg in get_args(annotation):
        yield from _models_in(arg)


def _collect(model: type[_Model], prefix: str, out: dict[str, int]) -> None:
    for name, info in model.model_fields.items():
        if name in ("id", "type"):
            continue
        path = f"{prefix}.{name}"
        limit = _max_of(info.metadata)
        if limit is None:
            # Optional / list-of / dict-of an annotated text
            limit = _max_in_annotation(info.annotation)
        if limit is not None:
            out[path] = limit
        for sub in _models_in(info.annotation):
            if sub is not model:
                _collect(sub, path, out)


def _max_in_annotation(annotation: Any) -> int | None:
    found = _max_of(list(getattr(annotation, "__metadata__", ())))
    if found is not None:
        return found
    for arg in get_args(annotation):
        found = _max_in_annotation(arg)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------- the shipped default


@functools.cache
def _default_dict() -> str:
    return (
        resources.files("callobuzz_rag.landing")
        .joinpath("data/default_site.json")
        .read_text(encoding="utf-8")
    )


def default_content() -> LandingContent:
    """The shipped English default, validated. A fresh copy on every call."""
    return LandingContent.model_validate(json.loads(_default_dict()))
