"""The white-label price list (TV3, D13): the plans the provider picks and the add-ons, as a page
to share or print, with the provider's own name, logo and colour.

What a prospect sees is what a business would pay: the fee, the credit included, the price per
question, the four limits in words and an announced price as "from <date>". The provider's markup
and AI cost never enter this module (it reads only the fields of a pricing version a customer can
see, and no cost column), and Internal plans are never listed.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from typing import Any, Final

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.settings_store import (
    POWERED_BY,
    Branding,
    PriceSheetSettings,
    powered_text_for,
)
from callobuzz_rag.billing import addons
from callobuzz_rag.billing.notice import (
    ist_date,
    long_date,
    money_text,
    pending_scheduled,
    plan_versions,
)
from callobuzz_rag.billing.pricing import PricingFields, fields_from_row
from callobuzz_rag.db.models.add_on import AddOn
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing

HTML_CSP: Final = "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:"
MAX_PLANS: Final = 12

_LIMIT_ROWS: Final[tuple[tuple[str, str, str], ...]] = (
    ("max_knowledge_bases", "Knowledge bases", "{n:,}"),
    ("monthly_questions", "Questions each month", "{n:,}"),
    ("max_documents", "Documents", "{n:,}"),
    ("max_widget_keys", "Website chat widgets", "{n:,}"),
)


@dataclass
class PlanEntry:
    id: uuid.UUID
    name: str
    description: str
    price: str  # "₹999 a month", "Free"
    credit: str | None  # "₹500 of credit included every month"
    usage: str  # "₹2 per question", "Questions included in the plan"
    beyond_limit: str | None  # "₹3 per question beyond your limits"
    limits: list[tuple[str, str]] = field(default_factory=list)
    upcoming: str | None = None  # "₹1,299 a month from 5 November 2026"
    # P2 invoices: the fee in numbers, and the GST-inclusive text filled in by add_inclusive_totals
    fee_minor: int = 0
    fee_currency: str = "INR"
    fee_period: str = "month"
    price_incl: str | None = None  # "₹1,178.82 a month incl. GST"
    # /P2 invoices


@dataclass
class AddOnEntry:
    name: str
    description: str
    price: str
    gives: str
    # P2 invoices
    price_minor: int = 0
    currency: str = "INR"
    price_incl: str | None = None  # "₹1,180.00 incl. GST"
    # /P2 invoices


@dataclass
class PriceSheet:
    product_name: str
    logo_url: str | None
    color: str
    note: str
    plans: list[PlanEntry]
    add_ons: list[AddOnEntry]
    generated_on: str
    powered_text: str | None = POWERED_BY["text"]  # None when Branding hides it


def _period_word(fields: PricingFields) -> str:
    return "year" if fields.billing_period == "yearly" else "month"


def fee_text(fields: PricingFields) -> str:
    if fields.subscription_fee_minor <= 0:
        return "Free" if fields.usage_mode == "included" else "No monthly fee"
    return f"{money_text(fields.subscription_fee_minor, fields.currency)} a {_period_word(fields)}"


def _usage_text(fields: PricingFields) -> str:
    if fields.usage_mode == "per_question":
        return f"{money_text(fields.per_question_minor, fields.currency)} per question"
    if fields.usage_mode == "cost_plus":
        return "Pay for what your assistant uses"
    return "Questions are included in the plan"


def _entry(plan: Plan, fields: PricingFields | None, coming: PlanPricing | None) -> PlanEntry:
    limits = []
    for attr, label, template in _LIMIT_ROWS:
        value = getattr(plan, attr)
        limits.append((label, "Unlimited" if value is None else template.format(n=value)))
    if fields is None:
        return PlanEntry(
            plan.id,
            plan.name,
            plan.description or "",
            "Free",
            None,
            "Questions are included in the plan",
            None,
            limits,
        )
    credit = (
        f"{money_text(fields.included_credit_minor, fields.currency)} of credit included "
        f"every {_period_word(fields)}"
        if fields.included_credit_minor > 0
        else None
    )
    beyond = (
        f"{money_text(fields.overage_per_question_minor, fields.currency)} per question "
        "beyond your limits"
        if fields.overage == "charge" and fields.overage_mode == "per_question"
        else None
    )
    upcoming = None
    if coming is not None and coming.effective_at is not None:
        later = fields_from_row(coming)
        upcoming = f"{fee_text(later)} from {long_date(ist_date(coming.effective_at))}"
    return PlanEntry(
        plan.id,
        plan.name,
        plan.description or "",
        fee_text(fields),
        credit,
        _usage_text(fields),
        beyond,
        limits,
        upcoming,
        fee_minor=fields.subscription_fee_minor,  # P2 invoices
        fee_currency=fields.currency,  # P2 invoices
        fee_period=_period_word(fields),  # P2 invoices
    )


def _monthly(fields: PricingFields | None) -> int:
    if fields is None:
        return 0
    fee = fields.subscription_fee_minor
    return fee // 12 if fields.billing_period == "yearly" else fee


async def _plans(
    session: AsyncSession, settings: PriceSheetSettings, now: datetime
) -> list[PlanEntry]:
    rows = list((await session.execute(select(Plan))).scalars().all())
    by_id = {p.id: p for p in rows if p.is_active and not p.is_internal}
    picked = [by_id[i] for i in settings.plan_ids if i in by_id]
    explicit = bool(settings.plan_ids)
    candidates = picked if explicit else sorted(by_id.values(), key=lambda p: p.name)
    entries: list[tuple[int, PlanEntry]] = []
    for plan in candidates[:MAX_PLANS]:
        versions = await plan_versions(session, plan.id)
        current = next((v for v in versions if v.is_current), None)
        if current is None and not explicit:
            continue  # without picked plans only priced ones are listed
        coming = next(
            (
                v
                for v in pending_scheduled(versions)
                if v.effective_at is not None and v.effective_at > now
            ),
            None,
        )
        fields = fields_from_row(current) if current is not None else None
        entries.append((_monthly(fields), _entry(plan, fields, coming)))
    if not explicit:
        entries.sort(key=lambda pair: (pair[0], pair[1].name))
    return [entry for _, entry in entries]


async def _add_ons(session: AsyncSession) -> list[AddOnEntry]:
    rows = (await session.execute(select(AddOn).where(AddOn.is_active.is_(True)))).scalars().all()
    out = []
    for row in sorted(rows, key=lambda r: r.name):
        gives = addons.summary(
            kind=row.kind,
            credit_minor=row.credit_minor,
            bonus_pct=row.bonus_pct,
            currency=row.currency,
            limit_field=row.limit_field,
            limit_delta=row.limit_delta,
            questions=row.questions,
            ends_at=None,
        )
        out.append(
            AddOnEntry(
                row.name,
                row.description or "",
                money_text(row.price_minor, row.currency),
                gives,
                price_minor=row.price_minor,  # P2 invoices
                currency=row.currency,  # P2 invoices
            )
        )
    return out


# P2 invoices
def add_inclusive_totals(sheet: PriceSheet, invoicing: Any) -> PriceSheet:
    """Put the GST-inclusive total next to every price when the seller is GST-registered (no drip
    pricing). Free plans and a seller without a GSTIN show no extra figure."""
    from callobuzz_rag.billing.invoicing.display import inclusive_total

    if invoicing is None or not invoicing.gstin:
        return sheet
    included = bool(invoicing.prices_include_gst)
    for plan in sheet.plans:
        if plan.fee_minor > 0:
            total = inclusive_total(plan.fee_minor, registered=True, prices_include_gst=included)
            plan.price_incl = (
                f"{money_text(total, plan.fee_currency)} a {plan.fee_period} incl. GST"
            )
    for add_on in sheet.add_ons:
        if add_on.price_minor > 0:
            total = inclusive_total(
                add_on.price_minor, registered=True, prices_include_gst=included
            )
            add_on.price_incl = f"{money_text(total, add_on.currency)} incl. GST"
    return sheet


# /P2 invoices


async def build_sheet(
    session: AsyncSession,
    settings: PriceSheetSettings,
    branding: Branding,
    *,
    now: datetime | None = None,
    invoicing: Any = None,  # P2 invoices: the seller's GST profile (inclusive totals)
) -> PriceSheet:
    """The price list as it stands now."""
    moment = now or datetime.now(UTC)
    sheet = PriceSheet(
        product_name=branding.product_name,
        logo_url=branding.logo_url,
        color=branding.primary_color,
        note=settings.note,
        plans=await _plans(session, settings, moment),
        add_ons=await _add_ons(session) if settings.show_add_ons else [],
        generated_on=long_date(ist_date(moment)),
        powered_text=powered_text_for(branding),
    )
    return add_inclusive_totals(sheet, invoicing)  # P2 invoices


def as_dict(sheet: PriceSheet) -> dict[str, Any]:
    """The sheet as plain JSON: names, texts and amounts a visitor reads, nothing else."""
    return {
        "product_name": sheet.product_name,
        "note": sheet.note,
        "generated_on": sheet.generated_on,
        "plans": [
            {
                "name": p.name,
                "description": p.description,
                "price": p.price,
                "credit": p.credit,
                "usage": p.usage,
                "beyond_limit": p.beyond_limit,
                "limits": [{"label": label, "value": value} for label, value in p.limits],
                "upcoming": p.upcoming,
                "price_incl": p.price_incl,  # P2 invoices
            }
            for p in sheet.plans
        ],
        "add_ons": [
            {
                "name": a.name,
                "description": a.description,
                "price": a.price,
                "price_incl": a.price_incl,  # P2 invoices
                "gives": a.gives,
            }
            for a in sheet.add_ons
        ],
    }


@cache
def _env() -> Environment:
    return Environment(
        loader=PackageLoader("callobuzz_rag.billing", "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_html(sheet: PriceSheet) -> str:
    """A standalone printable page (the browser's print dialog saves it as a PDF). Every value
    reaches the template as text and is escaped; the provider's colour is validated as `#rrggbb`
    by the branding settings."""
    return (
        _env().get_template("price_sheet.html").render(sheet=sheet, powered_text=sheet.powered_text)
    )
