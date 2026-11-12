"""The operator's runtime settings: sign-up mode and white-label branding.

Stored in the single `engine_settings` row (`db/models/engine_settings.py`) and
validated as one `EngineSettings` model. Stored JSON is merged over the defaults on
read, so a missing key means the default and a setting added later needs no data
migration; unknown keys are dropped on the next write.

Reads are cached per process for `CACHE_TTL_S` seconds. A write in this process
invalidates the cache at once; other API workers and Celery see it within the TTL.
`GET /api/v1/public/branding` and every e-mail render read through the cache.

"cob-rag-engine by Callobuzz" is `POWERED_BY`, a constant (plain text, never a link),
deliberately not a field of `EngineSettings` or `Branding`: there is no setting that changes it.
"""

import asyncio
import logging
import time
import uuid  # TV trust
import weakref
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)
from sqlalchemy import select

from callobuzz_rag.accounts.email_rules import ValidatedEmail
from callobuzz_rag.billing.money import Currency  # TV trust
from callobuzz_rag.core.company import CompanyDetails  # P1 payments
from callobuzz_rag.core.product import POWERED_BY_TEXT  # L1 release
from callobuzz_rag.core.schemas import StrictRequest  # P1 payments
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

SignupMode = Literal["open", "approval", "closed"]

# L1 release
POWERED_BY: Final[Mapping[str, str]] = MappingProxyType({"text": POWERED_BY_TEXT})
# /L1 release

CACHE_TTL_S: Final = 10.0
SETTINGS_ROW_ID: Final = 1
URL_MAX_LENGTH: Final = 2048


def check_url(value: str | None, *, allow_http: bool) -> str | None:
    """An absolute http(s) URL, or None. Refuses `javascript:`, `data:` and relative URLs
    so an operator-supplied value can safely become an `href` or `src`."""
    if value is None:
        return None
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise ValueError("URL must not contain whitespace or control characters")
    parts = urlsplit(value)
    allowed = {"https", "http"} if allow_http else {"https"}
    if parts.scheme.lower() not in allowed or not parts.netloc:
        raise ValueError(
            "must be an absolute https:// URL"
            if not allow_http
            else "must be an absolute http:// or https:// URL"
        )
    return value


def _allow_http_logo(info: ValidationInfo) -> bool:
    """http:// logos are refused in production (mixed content); the caller says which."""
    context = info.context if isinstance(info.context, dict) else {}
    return bool(context.get("allow_http_logo", True))


class Branding(BaseModel):
    """White-label branding shown on public pages, the dashboard shell and e-mails."""

    model_config = ConfigDict(extra="forbid")

    product_name: str = Field("cob-rag-engine", min_length=1, max_length=80)
    logo_url: str | None = Field(None, max_length=URL_MAX_LENGTH)
    primary_color: str = Field("#2f8cff", pattern=r"^#[0-9a-fA-F]{6}$")
    support_email: ValidatedEmail | None = None
    contact_text: str = Field("Contact us for pricing.", min_length=1, max_length=500)
    contact_url: str | None = Field(None, max_length=URL_MAX_LENGTH)
    # Hide the "Powered by" line (any operator).
    hide_powered_by: bool = False

    @field_validator("logo_url")
    @classmethod
    def _logo_url(cls, value: str | None, info: ValidationInfo) -> str | None:
        return check_url(value, allow_http=_allow_http_logo(info))

    @field_validator("contact_url")
    @classmethod
    def _contact_url(cls, value: str | None) -> str | None:
        return check_url(value, allow_http=True)


def powered_by_for(branding: Branding) -> Mapping[str, str] | None:
    """The "Powered by" line to show, or None when the operator hides it in Branding."""
    if branding.hide_powered_by:
        return None
    return POWERED_BY


def powered_text_for(branding: Branding) -> str | None:
    shown = powered_by_for(branding)
    return shown["text"] if shown is not None else None


# BIL2 billing
class BillingEngineSettings(BaseModel):
    """Runtime billing settings: the currency a new account gets, and the amount at or above
    which a manual money entry needs the typed amount."""

    model_config = ConfigDict(extra="forbid")

    default_currency: Literal["INR", "USD"] = "INR"
    confirm_above_minor: dict[str, int] = Field(
        default_factory=lambda: {"INR": 1_000_000, "USD": 10_000}
    )

    @field_validator("confirm_above_minor")
    @classmethod
    def _confirm_above(cls, value: dict[str, int]) -> dict[str, int]:
        for currency, minor in value.items():
            if currency not in ("INR", "USD"):
                raise ValueError(f"unknown currency {currency!r}")
            if type(minor) is not int or minor < 1:
                raise ValueError("amounts are whole minor units of at least 1")
        return value


# /BIL2 billing


# TV trust
class PriceSheetSettings(BaseModel):
    """What the shareable price list shows (TV3). `public_token` is set and rotated only by the
    price-sheet link routes, never by a settings patch."""

    model_config = ConfigDict(extra="forbid")

    plan_ids: list[uuid.UUID] = Field(default_factory=list, max_length=12)
    show_add_ons: bool = True
    note: str = Field("", max_length=500)
    public_token: str | None = None


class TrustSettings(BaseModel):
    """The deployer's trust and value switches (TV1, TV3, TV4, TV5). Every default is the safe,
    customer-friendly one."""

    model_config = ConfigDict(extra="forbid")

    allow_hide_human_button: bool = False
    require_ai_disclosure: bool = True
    disclosure_wording_editable: bool = True
    renewal_reminder_days: int = Field(7, ge=0, le=30)
    price_change_notice_days: int = Field(30, ge=7, le=90)
    spend_cap_max_minor: dict[Currency, int] = Field(default_factory=dict)
    support_enabled: bool = True
    support_sla_hours: int | None = Field(None, ge=1, le=168)
    price_sheet: PriceSheetSettings = Field(default_factory=PriceSheetSettings)
    value_report_enabled: bool = True
    value_report_cc_deployer: bool = False

    @field_validator("spend_cap_max_minor")
    @classmethod
    def _cap_max(cls, value: dict[Currency, int]) -> dict[Currency, int]:
        for minor in value.values():
            if type(minor) is not int or minor < 1:
                raise ValueError("amounts are whole minor units of at least 1")
        return value


# /TV trust


# TV6 whatsapp
class WhatsAppRates(BaseModel):
    """What Meta charges per message of each category, in minor units of one currency (paise,
    cents). The deployer types these in from Meta's current price list; none is built in."""

    model_config = ConfigDict(extra="forbid")

    service: int = Field(0, ge=0, le=1_000_000)
    utility: int = Field(0, ge=0, le=1_000_000)
    marketing: int = Field(0, ge=0, le=1_000_000)
    authentication: int = Field(0, ge=0, le=1_000_000)


class WhatsAppEngineSettings(BaseModel):
    """WhatsApp pass-through fee rates (TV6). Every default is 0: nothing is charged until the
    deployer sets a rate."""

    model_config = ConfigDict(extra="forbid")

    rates_minor: dict[Currency, WhatsAppRates] = Field(default_factory=dict)
    free_service_per_month: int = Field(0, ge=0, le=10_000_000)


# /TV6 whatsapp


# PIP3 rails
class PipelineRails(BaseModel):
    """Engine-wide safety rails: the most AI the deployer's own key may spend in a UTC day
    (USD micro, None = no ceiling), the percentage of it at which the deployer is told, and how
    many website crawls run at once (None = no limit)."""

    model_config = ConfigDict(extra="forbid")

    engine_ai_spend_per_day_usd_micro: int | None = Field(None, ge=1)
    engine_spend_alert_pct: int = Field(80, ge=1, le=100)
    crawl_concurrency: int | None = Field(None, ge=1, le=64)


# /PIP3 rails


# P3 legal
class LegalSettings(BaseModel):
    """What the legal pages need that is not the company's own details: the grievance officer
    (DPDP / Consumer Protection rules), the city whose courts the Terms name, and whether the
    published pages are linked in the footers."""

    model_config = ConfigDict(extra="forbid")

    grievance_name: str | None = Field(None, max_length=120)
    grievance_email: ValidatedEmail | None = None
    grievance_address: str | None = Field(None, max_length=300)
    jurisdiction_city: str | None = Field(None, max_length=80)
    website: str | None = Field(None, max_length=300)
    show_in_footer: bool = True

    @field_validator("website")
    @classmethod
    def _website(cls, value: str | None) -> str | None:
        return check_url(value, allow_http=False)


# /P3 legal


# P1 payments
class SellerTaxProfile(StrictRequest):
    """The deployer's GST details as they appear on a tax invoice (P1 holds the data, P2 issues
    the documents). No GSTIN means a bill of supply with no tax lines."""

    model_config = ConfigDict(extra="forbid")

    gstin: str | None = None
    sac_default: str = Field("998315", pattern=r"^99[0-9]{4}$")
    invoice_prefix: str = Field("INV", pattern=r"^[A-Z0-9]{1,4}$")
    credit_note_prefix: str = Field("CN", pattern=r"^[A-Z0-9]{1,4}$")
    lut_number: str | None = Field(None, max_length=40)
    lut_valid_until: date | None = None
    prices_include_gst: bool = False
    footer_text: str | None = Field(None, max_length=500)
    signature_line: str = Field("This is a computer-generated invoice.", max_length=200)

    @field_validator("gstin")
    @classmethod
    def _gstin(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        from callobuzz_rag.billing.invoicing.gstin import GstinError, check

        try:
            return check(value)
        except GstinError as exc:
            raise ValueError(f"is not a valid GSTIN ({exc.reason})") from exc


class PaymentEngineSettings(StrictRequest):
    """Limits for online payments (minor units: paise)."""

    model_config = ConfigDict(extra="forbid")

    min_top_up_minor: int = Field(50_000, ge=100)
    max_payment_minor: int = Field(20_000_000, ge=100)
    link_expiry_days: int = Field(7, ge=1, le=30)


# /P1 payments


class EngineSettings(BaseModel):
    """Every runtime setting. A missing stored key means the default here."""

    model_config = ConfigDict(extra="forbid")

    signup_mode: SignupMode = "approval"
    branding: Branding = Field(default_factory=Branding)
    # COB6: "Ask COB" on the sign-in pages (also needs RAG_COB_ENABLED and RAG_COB_PUBLIC_ENABLED)
    cob_public_sign_in: bool = True
    # BIL2 billing
    billing: BillingEngineSettings = Field(default_factory=BillingEngineSettings)
    # /BIL2 billing
    pipelines: PipelineRails = Field(default_factory=PipelineRails)  # PIP3 rails
    # TV trust
    trust: TrustSettings = Field(default_factory=TrustSettings)
    # /TV trust
    whatsapp: WhatsAppEngineSettings = Field(default_factory=WhatsAppEngineSettings)  # TV6 whatsapp
    legal: LegalSettings = Field(default_factory=LegalSettings)  # P3 legal
    # P1 payments
    company: CompanyDetails = Field(default_factory=CompanyDetails)
    invoicing: SellerTaxProfile = Field(default_factory=SellerTaxProfile)
    payments: PaymentEngineSettings = Field(default_factory=PaymentEngineSettings)
    # /P1 payments


# P1 payments
_P1_SECTIONS: Final[Mapping[str, type[BaseModel]]] = MappingProxyType(
    {"company": CompanyDetails, "invoicing": SellerTaxProfile, "payments": PaymentEngineSettings}
)
# /P1 payments


def _parse_stored(value: Any) -> EngineSettings:
    """Stored JSON merged over the defaults.

    Unknown keys (a removed setting) are ignored, and a stored value that no longer
    validates (hand-edited, or a rule tightened since) falls back to its default with a
    warning, so one bad field can never take the public pages or e-mails down.
    """
    stored = value if isinstance(value, dict) else {}
    top = {k: v for k, v in stored.items() if k in EngineSettings.model_fields}
    branding = top.get("branding")
    if "branding" in top:
        top["branding"] = (
            {k: v for k, v in branding.items() if k in Branding.model_fields}
            if isinstance(branding, dict)
            else {}
        )
    # BIL2 billing
    billing = top.get("billing")
    if "billing" in top:
        top["billing"] = (
            {k: v for k, v in billing.items() if k in BillingEngineSettings.model_fields}
            if isinstance(billing, dict)
            else {}
        )
    # /BIL2 billing
    # PIP3 rails
    pipelines = top.get("pipelines")
    if "pipelines" in top:
        top["pipelines"] = (
            {k: v for k, v in pipelines.items() if k in PipelineRails.model_fields}
            if isinstance(pipelines, dict)
            else {}
        )
    # /PIP3 rails
    # TV trust
    trust = top.get("trust")
    if "trust" in top:
        if isinstance(trust, dict):
            trust = {k: v for k, v in trust.items() if k in TrustSettings.model_fields}
            sheet = trust.get("price_sheet")
            if "price_sheet" in trust:
                trust["price_sheet"] = (
                    {k: v for k, v in sheet.items() if k in PriceSheetSettings.model_fields}
                    if isinstance(sheet, dict)
                    else {}
                )
            top["trust"] = trust
        else:
            top["trust"] = {}
    # /TV trust
    # TV6 whatsapp
    wa = top.get("whatsapp")
    if "whatsapp" in top:
        top["whatsapp"] = (
            {k: v for k, v in wa.items() if k in WhatsAppEngineSettings.model_fields}
            if isinstance(wa, dict)
            else {}
        )
    # /TV6 whatsapp
    # P3 legal
    legal = top.get("legal")
    if "legal" in top:
        top["legal"] = (
            {k: v for k, v in legal.items() if k in LegalSettings.model_fields}
            if isinstance(legal, dict)
            else {}
        )
    # /P3 legal
    # P1 payments
    for section, model in _P1_SECTIONS.items():
        if section in top:
            raw = top[section]
            top[section] = (
                {k: v for k, v in raw.items() if k in model.model_fields}
                if isinstance(raw, dict)
                else {}
            )
    # /P1 payments
    try:
        return EngineSettings.model_validate(top)
    except ValidationError as exc:
        for error in exc.errors():
            loc = error["loc"]
            logger.warning("engine_settings: ignoring invalid stored value at %s", loc)
            if len(loc) >= 2 and loc[0] == "branding" and isinstance(top.get("branding"), dict):
                top["branding"].pop(loc[1], None)
            elif len(loc) >= 2 and loc[0] == "billing" and isinstance(top.get("billing"), dict):
                top["billing"].pop(loc[1], None)  # BIL2
            elif len(loc) >= 2 and loc[0] == "pipelines" and isinstance(top.get("pipelines"), dict):
                top["pipelines"].pop(loc[1], None)  # PIP3 rails
            elif (  # TV trust
                len(loc) >= 3
                and loc[:2] == ("trust", "price_sheet")
                and isinstance(top.get("trust"), dict)
                and isinstance(top["trust"].get("price_sheet"), dict)
            ):
                top["trust"]["price_sheet"].pop(loc[2], None)
            elif len(loc) >= 2 and loc[0] == "trust" and isinstance(top.get("trust"), dict):
                top["trust"].pop(loc[1], None)  # TV trust
            elif (  # TV6 whatsapp
                len(loc) >= 2 and loc[0] == "whatsapp" and isinstance(top.get("whatsapp"), dict)
            ):
                top["whatsapp"].pop(loc[1], None)
            elif len(loc) >= 2 and loc[0] == "legal" and isinstance(top.get("legal"), dict):
                top["legal"].pop(loc[1], None)  # P3 legal
            elif (  # P1 payments
                len(loc) >= 2 and loc[0] in _P1_SECTIONS and isinstance(top.get(loc[0]), dict)
            ):
                top[loc[0]].pop(loc[1], None)
            elif loc:
                top.pop(loc[0], None)
    try:
        return EngineSettings.model_validate(top)
    except ValidationError:
        logger.warning("engine_settings: stored value unusable; using defaults")
        return EngineSettings()


# ------------------------------------------------------------------ cache

_cache: tuple[EngineSettings, float] | None = None
_generation = 0  # bumped by every invalidation, so a read racing a write never caches stale
# One lock per event loop: Celery runs each task under its own asyncio.run(), and an
# asyncio.Lock that once waited on one loop cannot be awaited on another.
_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


def invalidate_engine_settings_cache() -> None:
    """Forget the cached settings; the next read goes to the database."""
    global _cache, _generation
    _cache = None
    _generation += 1


async def get_engine_settings(session_factory: Callable[[], Any]) -> EngineSettings:
    """The current settings, cached for `CACHE_TTL_S` seconds in this process."""
    global _cache
    cached = _cache
    if cached is not None and cached[1] > time.monotonic():
        return cached[0]
    async with _lock():
        cached = _cache
        if cached is not None and cached[1] > time.monotonic():
            return cached[0]
        generation = _generation
        async with session_factory() as session:
            row = await session.get(EngineSettingsRow, SETTINGS_ROW_ID)
            settings = _parse_stored(row.value if row is not None else None)
        if generation == _generation:  # no write invalidated the cache meanwhile
            _cache = (settings, time.monotonic() + CACHE_TTL_S)
        return settings


async def read_engine_settings_row(
    session: "AsyncSession",
) -> tuple[EngineSettings, EngineSettingsRow | None]:
    """Uncached: the settings and their row (for `updated_at` / `updated_by`)."""
    row = await session.get(EngineSettingsRow, SETTINGS_ROW_ID)
    return _parse_stored(row.value if row is not None else None), row


# ------------------------------------------------------------------ writes


def apply_changes(
    current: EngineSettings, changes: Mapping[str, Any], *, allow_http_logo: bool
) -> EngineSettings:
    """`current` with `changes` applied, PATCH style.

    A field sent sets it; a field sent as null resets it to its default (null for
    nullable fields); an omitted field is unchanged; a whole section (`branding`) sent
    as null resets the section. Raises `ValidationError` for an invalid result.
    """
    merged = current.model_dump(mode="json")
    defaults = EngineSettings().model_dump(mode="json")
    for key, value in changes.items():
        if key == "branding" and isinstance(value, Mapping):
            section = dict(merged["branding"])
            for field, field_value in value.items():
                section[field] = (
                    defaults["branding"].get(field) if field_value is None else field_value
                )
            merged["branding"] = section
        elif key == "billing" and isinstance(value, Mapping):  # BIL2
            billing = dict(merged["billing"])
            for field, field_value in value.items():
                billing[field] = (
                    defaults["billing"].get(field) if field_value is None else field_value
                )
            merged["billing"] = billing
        elif key == "pipelines" and isinstance(value, Mapping):  # PIP3 rails
            rails = dict(merged["pipelines"])
            for field, field_value in value.items():
                rails[field] = (
                    defaults["pipelines"].get(field) if field_value is None else field_value
                )
            merged["pipelines"] = rails
        elif key == "trust" and isinstance(value, Mapping):  # TV trust
            trust = dict(merged["trust"])
            for field, field_value in value.items():
                if field == "price_sheet" and (
                    field_value is None or isinstance(field_value, Mapping)
                ):
                    sheet = dict(trust["price_sheet"])
                    sheet_defaults = defaults["trust"]["price_sheet"]
                    # null resets what the list shows, field by field: the link is not one of
                    # them, only the link routes set or end it
                    items: Mapping[str, Any] = (
                        {name: None for name in sheet_defaults}
                        if field_value is None
                        else field_value
                    )
                    for name, item in items.items():
                        if name == "public_token":  # only the link routes set it
                            continue
                        sheet[name] = sheet_defaults.get(name) if item is None else item
                    trust["price_sheet"] = sheet
                else:
                    trust[field] = (
                        defaults["trust"].get(field) if field_value is None else field_value
                    )
            merged["trust"] = trust
        elif key == "whatsapp" and isinstance(value, Mapping):  # TV6 whatsapp
            wa = dict(merged["whatsapp"])
            for field, field_value in value.items():
                wa[field] = defaults["whatsapp"].get(field) if field_value is None else field_value
            merged["whatsapp"] = wa
        elif key == "legal" and isinstance(value, Mapping):  # P3 legal
            legal = dict(merged["legal"])
            for field, field_value in value.items():
                legal[field] = defaults["legal"].get(field) if field_value is None else field_value
            merged["legal"] = legal
        elif key in _P1_SECTIONS and isinstance(value, Mapping):  # P1 payments
            section_now = dict(merged[key])
            for field, field_value in value.items():
                section_now[field] = (
                    defaults[key].get(field) if field_value is None else field_value
                )
            merged[key] = section_now
        else:
            merged[key] = defaults.get(key) if value is None else value
    return EngineSettings.model_validate(merged, context={"allow_http_logo": allow_http_logo})


def settings_diff(old: EngineSettings, new: EngineSettings) -> dict[str, dict[str, Any]]:
    """`{"signup_mode": {"from": .., "to": ..}, "branding.product_name": {...}}` for
    every field that changed."""
    before = old.model_dump(mode="json")
    after = new.model_dump(mode="json")
    changed: dict[str, dict[str, Any]] = {}
    for key in after:
        if key == "branding":
            for field in after["branding"]:
                if before["branding"].get(field) != after["branding"][field]:
                    changed[f"branding.{field}"] = {
                        "from": before["branding"].get(field),
                        "to": after["branding"][field],
                    }
        elif key == "billing":  # BIL2
            for field in after["billing"]:
                if before["billing"].get(field) != after["billing"][field]:
                    changed[f"billing.{field}"] = {
                        "from": before["billing"].get(field),
                        "to": after["billing"][field],
                    }
        elif key == "pipelines":  # PIP3 rails
            for field in after["pipelines"]:
                if before["pipelines"].get(field) != after["pipelines"][field]:
                    changed[f"pipelines.{field}"] = {
                        "from": before["pipelines"].get(field),
                        "to": after["pipelines"][field],
                    }
        elif key == "trust":  # TV trust
            for field in after["trust"]:
                if field == "price_sheet":
                    for name in after["trust"]["price_sheet"]:
                        if name == "public_token":  # a secret link: never in the audit log
                            continue
                        old_v = before["trust"]["price_sheet"].get(name)
                        new_v = after["trust"]["price_sheet"][name]
                        if old_v != new_v:
                            changed[f"trust.price_sheet.{name}"] = {"from": old_v, "to": new_v}
                elif before["trust"].get(field) != after["trust"][field]:
                    changed[f"trust.{field}"] = {
                        "from": before["trust"].get(field),
                        "to": after["trust"][field],
                    }
        elif key == "whatsapp":  # TV6 whatsapp
            for field in after["whatsapp"]:
                if before["whatsapp"].get(field) != after["whatsapp"][field]:
                    changed[f"whatsapp.{field}"] = {
                        "from": before["whatsapp"].get(field),
                        "to": after["whatsapp"][field],
                    }
        elif key == "legal":  # P3 legal
            for field in after["legal"]:
                if before["legal"].get(field) != after["legal"][field]:
                    changed[f"legal.{field}"] = {
                        "from": before["legal"].get(field),
                        "to": after["legal"][field],
                    }
        elif key in _P1_SECTIONS:  # P1 payments
            for field in after[key]:
                if before[key].get(field) != after[key][field]:
                    changed[f"{key}.{field}"] = {
                        "from": before[key].get(field),
                        "to": after[key][field],
                    }
        elif before.get(key) != after[key]:
            changed[key] = {"from": before.get(key), "to": after[key]}
    return changed


async def update_engine_settings(
    session: "AsyncSession",
    changes: Mapping[str, Any],
    *,
    actor_label: str,
    allow_http_logo: bool = False,
) -> tuple[EngineSettings, dict[str, dict[str, Any]]]:
    """Apply `changes` under a row lock; returns the new settings and what changed.

    Nothing is written when nothing changes (`{}`, or values equal to the current
    ones). The caller records the audit row and commits; the cache is invalidated here
    and should be invalidated again by the caller after the commit.

    Raises:
        ValidationError: the merged settings are invalid (the route turns it into 422).
    """
    row = (
        await session.execute(
            select(EngineSettingsRow)
            .where(EngineSettingsRow.id == SETTINGS_ROW_ID)
            .with_for_update()
        )
    ).scalar_one_or_none()
    current = _parse_stored(row.value if row is not None else None)
    if not changes:
        return current, {}
    new = apply_changes(current, changes, allow_http_logo=allow_http_logo)
    changed = settings_diff(current, new)
    if not changed:
        return current, {}
    if row is None:  # the migration seeds it; recreate it if someone deleted it
        row = EngineSettingsRow(id=SETTINGS_ROW_ID)
        session.add(row)
    row.value = new.model_dump(mode="json")
    row.updated_at = datetime.now(UTC)
    row.updated_by = actor_label
    await session.flush()
    invalidate_engine_settings_cache()
    return new, changed


# TV3 price sheet
async def set_price_sheet_token(
    session: "AsyncSession", token: str | None, *, actor_label: str
) -> str | None:
    """Store (or, with None, remove) the price list's public link token under the row lock;
    returns the previous token. The only writer of `trust.price_sheet.public_token`: a settings
    patch never sets it. The caller audits and commits."""
    row = (
        await session.execute(
            select(EngineSettingsRow)
            .where(EngineSettingsRow.id == SETTINGS_ROW_ID)
            .with_for_update()
        )
    ).scalar_one_or_none()
    current = _parse_stored(row.value if row is not None else None)
    previous = current.trust.price_sheet.public_token
    if previous == token:
        return previous
    new = current.model_copy(deep=True)
    new.trust.price_sheet.public_token = token
    if row is None:
        row = EngineSettingsRow(id=SETTINGS_ROW_ID)
        session.add(row)
    row.value = new.model_dump(mode="json")
    row.updated_at = datetime.now(UTC)
    row.updated_by = actor_label
    await session.flush()
    invalidate_engine_settings_cache()
    return previous


# /TV3 price sheet
