"""Engine settings (admin) and public branding (no credential).

- `GET/PATCH /api/v1/admin/settings`: the operator's sign-up mode and branding.
- `GET /api/v1/public/branding`: what the sign-up/login pages, the dashboard shell and
  e-mails show. Served under `OPEN_PREFIXES` (api/auth.py): no key at all.
"""

import asyncio
import logging  # P3 legal
import uuid  # TV trust
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.accounts.email_rules import ValidatedEmail
from callobuzz_rag.accounts.settings_store import (
    URL_MAX_LENGTH,
    Branding,
    EngineSettings,
    PipelineRails,  # PIP3 rails
    SignupMode,
    TrustSettings,  # TV trust
    WhatsAppEngineSettings,  # TV6 whatsapp
    WhatsAppRates,  # TV6 whatsapp
    check_url,
    get_engine_settings,
    invalidate_engine_settings_cache,
    powered_by_for,
    read_engine_settings_row,
    update_engine_settings,
)
from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.schemas.landing import LandingPublic  # LP1 landing
from callobuzz_rag.billing.money import Currency  # TV trust
from callobuzz_rag.landing.cache import landing_is_on  # LP1 landing

logger = logging.getLogger(__name__)  # P3 legal

admin_router = APIRouter(prefix="/api/v1/admin/settings", tags=["settings"])
public_router = APIRouter(prefix="/api/v1/public", tags=["public"])


# ----------------------------------------------------------------- schemas


class BrandingPatch(BaseModel):
    """Branding fields to change. A field sent as null resets it to its default."""

    model_config = ConfigDict(extra="forbid")

    product_name: str | None = Field(None, min_length=1, max_length=80)
    logo_url: str | None = Field(
        None,
        max_length=URL_MAX_LENGTH,
        description="https:// URL (http:// allowed outside production)",
    )
    primary_color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    support_email: ValidatedEmail | None = None
    contact_text: str | None = Field(None, min_length=1, max_length=500)
    contact_url: str | None = Field(None, max_length=URL_MAX_LENGTH)
    hide_powered_by: bool | None = None

    @field_validator("logo_url", "contact_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        # http:// logos are refused in production by the store (it knows RAG_ENV).
        return check_url(value, allow_http=True)


# TV trust
class PriceSheetPatch(BaseModel):
    """Price-list fields to change. The public link is not here: it is set by its own routes."""

    model_config = ConfigDict(extra="forbid")

    plan_ids: list[uuid.UUID] | None = Field(None, max_length=12)
    show_add_ons: bool | None = None
    note: str | None = Field(None, max_length=500)


class TrustPatch(BaseModel):
    """Trust and value switches to change. A field sent as null goes back to its default."""

    model_config = ConfigDict(extra="forbid")

    allow_hide_human_button: bool | None = None
    require_ai_disclosure: bool | None = None
    disclosure_wording_editable: bool | None = None
    renewal_reminder_days: int | None = Field(None, ge=0, le=30)
    price_change_notice_days: int | None = Field(None, ge=7, le=90)
    spend_cap_max_minor: dict[Currency, int] | None = None
    support_enabled: bool | None = None
    support_sla_hours: int | None = Field(None, ge=1, le=168)
    price_sheet: PriceSheetPatch | None = None
    value_report_enabled: bool | None = None
    value_report_cc_deployer: bool | None = None


# /TV trust


# TV6 whatsapp
class WhatsAppPatch(BaseModel):
    """WhatsApp fee rates to change. A field sent as null goes back to its default (no rates, no
    free allowance)."""

    model_config = ConfigDict(extra="forbid")

    rates_minor: dict[Currency, WhatsAppRates] | None = Field(
        None,
        description="What Meta charges per message of each category, in minor units of the "
        "currency (paise, cents), from Meta's current price list; the whole map is replaced",
    )
    free_service_per_month: int | None = Field(
        None,
        ge=0,
        le=10_000_000,
        description="Service replies per number and month that are not charged on",
    )


# /TV6 whatsapp


# PIP3 rails
class PipelinesPatch(BaseModel):
    """Engine-wide safety rails to change. A field sent as null goes back to its default (no
    ceiling, 80 %, no crawl limit)."""

    model_config = ConfigDict(extra="forbid")

    engine_ai_spend_per_day_usd_micro: int | None = Field(
        None,
        ge=1,
        description="The most the deployer's own AI key may spend on businesses in a UTC day, "
        "in USD micro (millionths of a dollar); null = no ceiling",
    )
    engine_spend_alert_pct: int | None = Field(
        None, ge=1, le=100, description="Warn the deployer at this share of the ceiling"
    )
    crawl_concurrency: int | None = Field(
        None, ge=1, le=64, description="Website reads running at once, all businesses together"
    )


# /PIP3 rails


class SettingsPatch(BaseModel):
    """Only the fields sent change; `{}` changes nothing. `branding: null` resets all
    branding, `signup_mode: null` resets the mode to `approval`."""

    model_config = ConfigDict(extra="forbid")

    signup_mode: SignupMode | None = None
    branding: BrandingPatch | None = None
    cob_public_sign_in: bool | None = Field(
        None, description='Show "Ask COB" on the sign-in pages (null resets to on)'
    )
    pipelines: PipelinesPatch | None = Field(
        None, description="Engine-wide safety rails: AI spend per day and crawl concurrency"
    )  # PIP3 rails
    trust: TrustPatch | None = Field(
        None, description="Trust and value switches (human button, price notice, value report)"
    )  # TV trust
    whatsapp: WhatsAppPatch | None = Field(
        None, description="Meta's per-message fee rates for the WhatsApp pass-through"
    )  # TV6 whatsapp


class SettingsResponse(BaseModel):
    signup_mode: SignupMode
    branding: Branding
    cob_public_sign_in: bool
    pipelines: PipelineRails  # PIP3 rails
    trust: TrustSettings  # TV trust
    whatsapp: WhatsAppEngineSettings  # TV6 whatsapp
    updated_at: datetime | None
    updated_by: str | None


class PoweredBy(BaseModel):
    text: str
    url: str | None = None  # L1 release: always null, the line is plain text


def _powered_by_out(branding: Branding) -> PoweredBy | None:
    shown = powered_by_for(branding)
    return PoweredBy(text=shown["text"], url=None) if shown is not None else None


class PasswordPolicy(BaseModel):
    min_length: int
    max_length: int


# L5a demo
class DemoLogin(BaseModel):
    role: Literal["operator", "owner"]
    email: str
    password: str


class DemoAnswers(BaseModel):
    """Today's demo answers, numbers only (no addresses, no visitors)."""

    limit: int = Field(description="The demo's answers per day (RAG_DEMO_ANSWERS_PER_DAY)")
    left: int = Field(description="What is left today; 0 when the demo is used up")
    per_visitor: int = Field(
        description="One visitor's answers per day (RAG_DEMO_ANSWERS_PER_VISITOR_PER_DAY)"
    )
    resets_at: datetime = Field(description="When the day ends and the counters start again (UTC)")


class DemoInfo(BaseModel):
    """The demo's sign-ins. Public by design: the sign-in page shows them to every visitor."""

    logins: list[DemoLogin]
    resets_at_utc: str = Field(description="When the demo is put back each night, HH:MM UTC")
    answers: DemoAnswers | None = Field(
        None, description="Today's demo answers; null when the counter cannot be read"
    )


# /L5a demo
# P3 legal
class LegalLink(BaseModel):
    slug: str
    title: str
    url: str


async def _legal_links(request: Request, show: bool) -> list[LegalLink]:
    """The published legal pages for the footers. Never fails the page: unreadable is none."""
    if not show:
        return []
    from callobuzz_rag.api.routes.widget_keys import engine_public_url
    from callobuzz_rag.legal.store import published_all

    try:
        async with request.app.state.session_factory() as session:
            pages = await published_all(session)
    except Exception:
        logger.warning("public branding: legal pages unreadable", exc_info=True)
        return []
    base = engine_public_url(request)
    return [LegalLink(slug=p.slug, title=p.title, url=f"{base}/legal/{p.slug}") for p in pages]


# /P3 legal
class PublicBrandingResponse(BaseModel):
    product_name: str
    logo_url: str | None
    primary_color: str
    support_email: str | None
    contact_text: str
    contact_url: str | None
    signup_mode: SignupMode
    powered_by: PoweredBy | None = Field(
        description="Constant, never a link; null when the operator hides it in Branding"
    )
    accounts_enabled: bool = Field(description="Sign-in is on (RAG_SESSION_SECRET is set)")
    password_policy: PasswordPolicy
    signup_available: bool = Field(
        description="A sign-up form can work: mode not closed, accounts on, e-mail configured"
    )
    special_use_emails_allowed: bool = Field(
        description=(
            "Addresses on reserved names (.local, .test, .example, .localhost, .invalid) are "
            "accepted: true only when RAG_ENV=dev. Forms use it to apply the engine's rule."
        )
    )
    cob_public: bool = Field(
        description=(
            "Visitors can ask COB on the sign-in pages: RAG_COB_ENABLED and "
            "RAG_COB_PUBLIC_ENABLED are on and the operator has not switched it off"
        )
    )
    demo: DemoInfo | None = Field(  # L5a demo
        None, description="Only on a demo server (RAG_DEMO_MODE): the demo sign-ins"
    )
    legal: list[LegalLink] = Field(  # P3 legal
        default_factory=list,
        description="The legal pages the deployer has published (for the footers); empty when "
        "none are published or the footer links are switched off",
    )
    # LP1 landing
    landing: LandingPublic = Field(
        default_factory=lambda: LandingPublic(enabled=False),
        description="The deployer's public website (the sign-in pages offer a way back to it)",
    )
    # /LP1 landing


# L5a demo
def _demo_info(engine_config: Any) -> DemoInfo | None:
    if not engine_config.demo_mode:
        return None
    return DemoInfo(
        logins=[
            DemoLogin(
                role="operator",
                email=engine_config.superadmin_email,
                password=engine_config.demo_password,
            ),
            DemoLogin(
                role="owner",
                email=engine_config.demo_owner_email,
                password=engine_config.demo_password,
            ),
        ],
        resets_at_utc=engine_config.demo_reset_at_utc,
    )


async def _demo_answers(request: Request) -> DemoAnswers | None:
    """What is left of the demo's answers today. Never fails the page: unreadable is None."""
    from callobuzz_rag.demo import limits, runtime

    engine_config = request.app.state.settings
    try:
        limiter = limits.limiter_from(runtime.get_redis(request.app), engine_config)
        counts = await asyncio.wait_for(limiter.counts(), timeout=1.0)
    except Exception:  # includes a timeout: the public page never waits on the counters
        return None
    if counts is None:
        return None
    return DemoAnswers(
        limit=counts[1],
        left=max(0, counts[1] - counts[0]),
        per_visitor=engine_config.demo_answers_per_visitor_per_day,
        resets_at=limiter.resets_at(),
    )


# /L5a demo


def _response(
    settings: EngineSettings, updated_at: datetime | None, updated_by: str | None
) -> SettingsResponse:
    return SettingsResponse(
        signup_mode=settings.signup_mode,
        branding=settings.branding,
        cob_public_sign_in=settings.cob_public_sign_in,
        pipelines=settings.pipelines,  # PIP3 rails
        trust=settings.trust,  # TV trust
        whatsapp=settings.whatsapp,  # TV6 whatsapp
        updated_at=updated_at,
        updated_by=updated_by,
    )


# ------------------------------------------------------------------ routes


@admin_router.get("", response_model=SettingsResponse, dependencies=[Depends(require_admin)])
async def get_settings_route(session: AsyncSession = Depends(get_session)) -> SettingsResponse:
    """Sign-up mode and branding, with who changed them last."""
    settings, row = await read_engine_settings_row(session)
    return _response(
        settings,
        row.updated_at if row is not None else None,
        row.updated_by if row is not None else None,
    )


@admin_router.patch("", response_model=SettingsResponse, dependencies=[Depends(require_admin)])
async def patch_settings_route(
    body: SettingsPatch,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SettingsResponse:
    """Change the sign-up mode and/or branding.

    A field sent sets it, a field sent as null resets it to its default, an omitted
    field stays. `{}` is a no-op. "cob-rag-engine by Callobuzz" is not a setting.
    """
    changes: dict[str, Any] = body.model_dump(exclude_unset=True)
    engine_config = request.app.state.settings
    _, _, actor_label = actor_from_request(request)
    try:
        settings, changed = await update_engine_settings(
            session,
            changes,
            actor_label=actor_label,
            allow_http_logo=not engine_config.is_prod,
        )
    except ValidationError as exc:
        raise RequestValidationError(
            [{**e, "loc": ("body", *e["loc"])} for e in exc.errors(include_url=False)]
        ) from exc
    if changed:
        await record_audit(
            session,
            request,
            "settings.updated",
            target_type="settings",
            target_id="engine",
            details={"changed": changed},
        )
        await session.commit()
        invalidate_engine_settings_cache()
    settings, row = await read_engine_settings_row(session)
    return _response(
        settings,
        row.updated_at if row is not None else None,
        row.updated_by if row is not None else None,
    )


@public_router.get("/branding", response_model=PublicBrandingResponse)
async def public_branding(request: Request, response: Response) -> PublicBrandingResponse:
    """Branding for the public pages, the dashboard shell and e-mails. No key needed.

    Never carries anything secret or any settings metadata.
    """
    from callobuzz_rag.accounts.email.factory import email_configured
    from callobuzz_rag.accounts.passwords import POLICY
    from callobuzz_rag.accounts.tokens import accounts_enabled

    settings = await get_engine_settings(request.app.state.session_factory)
    branding = settings.branding
    engine_config = request.app.state.settings
    demo = _demo_info(engine_config)  # L5a demo
    if demo is not None:
        demo.answers = await _demo_answers(request)
    # a demo's "answers left" moves, so it is cached for seconds, not a minute
    response.headers["Cache-Control"] = "public, max-age=10" if demo else "public, max-age=60"
    enabled = accounts_enabled(engine_config)
    return PublicBrandingResponse(
        product_name=branding.product_name,
        logo_url=branding.logo_url,
        primary_color=branding.primary_color,
        support_email=branding.support_email,
        contact_text=branding.contact_text,
        contact_url=branding.contact_url,
        signup_mode=settings.signup_mode,
        powered_by=_powered_by_out(branding),
        accounts_enabled=enabled,
        password_policy=PasswordPolicy(**POLICY),
        signup_available=(
            settings.signup_mode != "closed"
            and enabled
            and email_configured(engine_config)
            and not engine_config.demo_mode  # L5a demo: nobody signs up to a demo
        ),
        special_use_emails_allowed=not engine_config.is_prod,
        cob_public=(
            engine_config.cob_enabled
            and engine_config.cob_public_enabled
            and settings.cob_public_sign_in
        ),
        demo=demo,  # L5a demo
        legal=await _legal_links(request, settings.legal.show_in_footer),  # P3 legal
        # LP1 landing
        landing=LandingPublic(enabled=await landing_is_on(request.app.state.session_factory)),
        # /LP1 landing
    )
