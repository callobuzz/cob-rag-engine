"""The pricing page's data, from the plans, pricing versions and add-on catalogue (LP1, LP3).

Two steps. `load_data` reads the rows once (the page environment caches it for a few seconds) and
keeps only what a visitor may see: names, fees, the credit a plan includes, limits and add-on
prices. No cost, markup, credit limit or grace column is ever read, so none can reach a page.
`build_view` is then pure: it turns that data, the deployer's page settings, a language and the
visitor's currency / period choice into cards, toggles, the credit box, the calculator's data and
the packs, services and WhatsApp fee blocks. Money is whole minor units next to a currency; the
words around an amount are the page language's (`ui_pages.py`).

LP1's editor warning (`listed_plan_ids`, `placeholder_priced`) lives here too.
"""

import dataclasses
import json
import urllib.parse
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

from callobuzz_rag.billing.invoicing.display import inclusive_total
from callobuzz_rag.billing.notice import money_text, plan_versions
from callobuzz_rag.billing.pricing import fields_from_row
from callobuzz_rag.billing.seed_saas import PLACEHOLDER_BY
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.landing import ui
from callobuzz_rag.landing.calc import CalcPlan, monthly_cost, recommend
from callobuzz_rag.landing.schema import LandingContent, PricingCard, PricingTeaserSection
from callobuzz_rag.landing.ui_pages import fill

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

Period = Literal["monthly", "yearly"]
MAX_CARDS: Final = 4
CURRENCIES: Final = ("INR", "USD")
PERIODS: Final = ("monthly", "yearly")
EXAMPLE_QUESTIONS: Final = (300, 900, 3000)  # about 10, 30 and 100 questions a day


# ---------------------------------------------------------------- the editor's warning (LP1)


def listed_plan_ids(content: LandingContent) -> list[uuid.UUID]:
    """The plans the draft names (pricing cards, then the home teaser). Empty means the website
    follows the price sheet, which never lists placeholder prices as a price."""
    ids: list[uuid.UUID] = []
    for card in content.pricing.cards:
        for periods in card.variants.values():
            ids += [i for i in periods.values() if i not in ids]
    for section in content.home:
        if isinstance(section, PricingTeaserSection):
            ids += [i for i in section.plan_ids if i not in ids]
    return ids


async def placeholder_priced(session: "AsyncSession", plan_ids: list[uuid.UUID]) -> list[str]:
    """Names of the listed plans whose current price was only seeded as a placeholder."""
    names: list[str] = []
    for plan_id in plan_ids:
        plan = await session.get(Plan, plan_id)
        if plan is None:
            continue
        versions = await plan_versions(session, plan_id)
        current = next((v for v in versions if v.is_current), None)
        if current is not None and current.created_by == PLACEHOLDER_BY:
            names.append(plan.name)
    return names


# ---------------------------------------------------------------- the data (cached by the page)


@dataclass(frozen=True)
class PriceData:
    """A plan's price in force: only fields a customer could see."""

    currency: str
    period: str
    fee_minor: int
    usage_mode: str
    per_question_minor: int
    included_credit_minor: int
    overage: str  # block | charge | allow: what happens beyond the plan's questions
    overage_mode: str
    overage_per_question_minor: int
    placeholder: bool  # seeded, not set by the deployer yet: shown as "Price on request"


@dataclass(frozen=True)
class PlanData:
    id: uuid.UUID
    name: str
    description: str
    knowledge_bases: int | None
    questions: int | None
    documents: int | None
    widgets: int | None
    byok_allowed: bool
    price: PriceData | None
    self_service: bool = False  # P6 plan choice: the card links to `/signup?plan=<slug>`


@dataclass(frozen=True)
class AddOnData:
    kind: str
    name: str
    description: str
    currency: str
    price_minor: int
    credit_minor: int | None
    bonus_pct: int
    expiry_days: int | None
    feature: str | None
    renews: bool


@dataclass(frozen=True)
class RateData:
    currency: str
    service: int
    utility: int
    marketing: int
    authentication: int


@dataclass(frozen=True)
class PricingData:
    """Everything the pricing page reads from the engine, in one cacheable value."""

    plans: tuple[PlanData, ...] = ()
    sheet_plan_ids: tuple[
        uuid.UUID, ...
    ] = ()  # the price sheet's selection, in the deployer's order
    add_ons: tuple[AddOnData, ...] = ()
    rates: tuple[RateData, ...] = ()
    gst_registered: bool = False
    prices_include_gst: bool = False
    default_currency: str = "INR"


def _price(plan: Plan, version: Any) -> PriceData | None:
    if version is None:
        return None
    fields = fields_from_row(version)
    return PriceData(
        currency=fields.currency,
        period=fields.billing_period,
        fee_minor=fields.subscription_fee_minor,
        usage_mode=fields.usage_mode,
        per_question_minor=fields.per_question_minor,
        included_credit_minor=fields.included_credit_minor,
        overage=fields.overage,
        overage_mode=fields.overage_mode,
        overage_per_question_minor=fields.overage_per_question_minor,
        placeholder=version.created_by == PLACEHOLDER_BY,
    )


async def load_data(session: "AsyncSession", engine: Any, *, billing_on: bool) -> PricingData:
    """Read the plans, their prices in force and the add-ons. With billing off no price is read:
    a plan is its name and limits, and the page shows the deployer's contact text."""
    from sqlalchemy import select

    from callobuzz_rag.db.models.add_on import AddOn

    rows = (await session.execute(select(Plan))).scalars().all()
    plans: list[PlanData] = []
    for row in rows:
        if not row.is_active or row.is_internal:
            continue
        version = None
        if billing_on:
            versions = await plan_versions(session, row.id)
            version = next((v for v in versions if v.is_current), None)
        plans.append(
            PlanData(
                id=row.id,
                name=row.name,
                description=row.description or "",
                knowledge_bases=row.max_knowledge_bases,
                questions=row.monthly_questions,
                documents=row.max_documents,
                widgets=row.max_widget_keys,
                byok_allowed=bool(row.byok_allowed),
                price=_price(row, version),
                self_service=bool(row.self_service),  # P6 plan choice
            )
        )
    add_ons: list[AddOnData] = []
    if billing_on:
        found = (
            (await session.execute(select(AddOn).where(AddOn.is_active.is_(True)))).scalars().all()
        )
        add_ons = [
            AddOnData(
                kind=a.kind,
                name=a.name,
                description=a.description or "",
                currency=a.currency,
                price_minor=a.price_minor,
                credit_minor=a.credit_minor,
                bonus_pct=a.bonus_pct,
                expiry_days=a.expiry_days,
                feature=a.feature,
                renews=bool(a.renews),
            )
            for a in sorted(found, key=lambda a: a.name)
            if not a.placeholder
        ]
    rates = tuple(
        RateData(code, r.service, r.utility, r.marketing, r.authentication)
        for code, r in engine.whatsapp.rates_minor.items()
    )
    return PricingData(
        plans=tuple(plans),
        sheet_plan_ids=tuple(engine.trust.price_sheet.plan_ids),
        add_ons=tuple(add_ons),
        rates=rates,
        gst_registered=bool(engine.invoicing.gstin),
        prices_include_gst=bool(engine.invoicing.prices_include_gst),
        default_currency=engine.billing.default_currency,
    )


# ---------------------------------------------------------------- the view


@dataclass(frozen=True)
class VariantView:
    """One plan in one currency and billing period, as a visitor reads it."""

    plan_id: str
    currency: str
    period: str
    fee_minor: int
    fee_text: str  # "₹999 a month" ("₹833 a month" for a yearly plan)
    fee_incl_text: str | None  # "₹1,178.82 a month incl. GST"
    gst_tag: str  # "+ GST" when GST is added on top
    billed_text: str | None  # "billed ₹9,999 yearly"
    badge: str | None  # "2 months free", only when computed
    credit_text: str | None
    usage_text: str | None
    beyond_text: str | None
    on_request: bool
    free: bool
    limits: tuple[tuple[str, str], ...]
    unit_text: str = ""  # "a month": the unit part of `fee_text`, for a smaller type size
    # P6 plan choice: the button. `?plan=<slug>` for a plan a business may choose itself, else
    # empty (the default plan); "Start free", "Start with Pro" or "Get started"
    signup_query: str = ""
    cta_text: str = ""

    @property
    def amount_text(self) -> str:
        """The amount part of `fee_text` ("₹999"), the whole text when it has no unit."""
        return (
            self.fee_text.removesuffix(self.unit_text).strip() if self.unit_text else self.fee_text
        )


@dataclass(frozen=True)
class CardView:
    name: str
    who_for: str
    highlight: str | None
    cta: str
    description: str
    byok_included: bool
    variants: tuple[VariantView, ...]
    selected: int  # index into `variants` for the visitor's currency and period

    def variant(self, currency: str, period: str) -> VariantView:
        for v in self.variants:
            if (v.currency, v.period) == (currency, period):
                return v
        raise KeyError((currency, period))

    def variant_default(self) -> VariantView:
        return self.variants[self.selected]


@dataclass(frozen=True)
class PackView:
    name: str
    description: str
    price_text: str
    price_incl_text: str | None
    gives: str
    never_expires: bool


@dataclass(frozen=True)
class ServiceView:
    name: str
    description: str
    price_text: str
    price_incl_text: str | None
    query: str


@dataclass(frozen=True)
class FeeRow:
    label: str
    text: str


@dataclass(frozen=True)
class CalcBlock:
    plans: tuple[CalcPlan, ...]
    names: dict[str, str]
    slider_max: int
    slider_default: int
    gst: bool
    texts: dict[str, str]
    initial: str  # the sentence for the starting value, rendered here so it works without JS
    initial_value: int


@dataclass(frozen=True)
class PricingView:
    billing_on: bool
    contact_text: str
    cards: tuple[CardView, ...]
    currency: str
    period: str
    currencies: tuple[str, ...]
    periods: tuple[str, ...]
    show_currency_toggle: bool
    show_period_toggle: bool
    tax_line: str | None
    credit_box: str | None
    credit_examples: tuple[str, ...]
    calculator: CalcBlock | None
    packs: tuple[PackView, ...]
    services: tuple[ServiceView, ...]
    whatsapp_fees: tuple[FeeRow, ...]
    whatsapp_fees_note: str
    byok_add_on_text: str | None
    spend_cap_text: str | None
    comparison: bool
    gst_added: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    def as_public_dict(self) -> dict[str, Any]:
        """The view as plain data (the page's ETag and the leak tests read it)."""
        return dataclasses.asdict(self)


def choose_index(options: Sequence[tuple[str, str]], currency: str, period: str) -> int:
    """Which of a card's (currency, period) variants a visitor's choice shows: the exact one,
    else the same currency monthly, else the same currency in any period, else the first.
    `toggles.ts` runs the same order."""
    for wanted in ((currency, period), (currency, "monthly")):
        for i, option in enumerate(options):
            if option == wanted:
                return i
    for i, option in enumerate(options):
        if option[0] == currency:
            return i
    return 0


def _text(value: Mapping[Any, str] | None, lang: str, default_lang: str, product: str) -> str:
    if not value:
        return ""
    text = value.get(lang) or value.get(default_lang) or next(iter(value.values()), "")
    return str(text).replace("{product}", product)


def _limit_value(value: int | None, words: dict[str, str]) -> str:
    return words["unlimited"] if value is None else f"{value:,}"


def _limits(plan: PlanData, words: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return (
        (words["limit_knowledge_bases"], _limit_value(plan.knowledge_bases, words)),
        (words["limit_questions"], _limit_value(plan.questions, words)),
        (words["limit_documents"], _limit_value(plan.documents, words)),
        (words["limit_widgets"], _limit_value(plan.widgets, words)),
    )


# P6 plan choice
def _variant(
    plan: PlanData,
    data: PricingData,
    words: dict[str, str],
    *,
    billing_on: bool,
    contact_text: str,
    paired_monthly: int | None,
) -> VariantView:
    view = _variant_body(
        plan,
        data,
        words,
        billing_on=billing_on,
        contact_text=contact_text,
        paired_monthly=paired_monthly,
    )
    query, text = _cta(plan, view, words, billing_on=billing_on)
    return dataclasses.replace(view, signup_query=query, cta_text=text)


def _cta(
    plan: PlanData, view: VariantView, words: dict[str, str], *, billing_on: bool
) -> tuple[str, str]:
    """The card's button for one variant: a plan a business may choose itself links to
    `/signup?plan=<slug>` ("Start free" or "Start with Pro"); any other plan, a price not set yet,
    or billing off is the plain sign-up ("Get started", the default plan)."""
    from callobuzz_rag.billing.plan_change import plan_slug

    if not (plan.self_service and billing_on) or view.on_request:
        return "", words["get_started"]
    query = "?" + urllib.parse.urlencode({"plan": plan_slug(plan.name)})
    if plan.price is None or plan.price.fee_minor <= 0:
        return query, words["start_free"]
    return query, fill(words["start_with"], plan=plan.name)


# /P6 plan choice


def _variant_body(
    plan: PlanData,
    data: PricingData,
    words: dict[str, str],
    *,
    billing_on: bool,
    contact_text: str,
    paired_monthly: int | None,
) -> VariantView:
    price = plan.price
    limits = _limits(plan, words)
    empty: dict[str, Any] = {
        "fee_incl_text": None,
        "gst_tag": "",
        "billed_text": None,
        "badge": None,
        "credit_text": None,
        "usage_text": None,
        "beyond_text": None,
    }
    if not billing_on:
        return VariantView(
            plan_id=str(plan.id),
            currency=data.default_currency,
            period="monthly",
            fee_minor=0,
            fee_text=contact_text or words["contact_for_pricing"],
            on_request=False,
            free=False,
            limits=limits,
            **empty,
        )
    if price is None:  # a plan listed on purpose without a price version is free
        return VariantView(
            plan_id=str(plan.id),
            currency=data.default_currency,
            period="monthly",
            fee_minor=0,
            fee_text=words["free"],
            on_request=False,
            free=True,
            limits=limits,
            **{**empty, "usage_text": words["questions_included"]},
        )
    if price.placeholder:
        return VariantView(
            plan_id=str(plan.id),
            currency=price.currency,
            period=price.period,
            fee_minor=0,
            fee_text=words["price_on_request"],
            on_request=True,
            free=False,
            limits=limits,
            **empty,
        )
    yearly = price.period == "yearly"
    month_fee = price.fee_minor // 12 if yearly else price.fee_minor
    cur = price.currency
    if price.fee_minor <= 0:
        fee_text = words["free"] if price.usage_mode == "included" else words["no_monthly_fee"]
    else:
        fee_text = f"{money_text(month_fee, cur)} {words['per_month']}"
    fee_incl = None
    gst_tag = ""
    if data.gst_registered and price.fee_minor > 0:
        total = inclusive_total(
            month_fee, registered=True, prices_include_gst=data.prices_include_gst
        )
        fee_incl = f"{money_text(total, cur)} {words['per_month']} {words['incl_gst']}"
        gst_tag = "" if data.prices_include_gst else words["plus_gst"]
    billed = (
        fill(words["billed_yearly"], amount=money_text(price.fee_minor, cur))
        if yearly and price.fee_minor > 0
        else None
    )
    badge = None
    if yearly and paired_monthly and 0 < price.fee_minor <= 10 * paired_monthly:
        badge = words["two_months_free"]
    credit = (
        fill(
            words["credit_included_line"],
            amount=money_text(price.included_credit_minor, cur),
            period=words["period_year"] if yearly else words["period_month"],
        )
        if price.included_credit_minor > 0
        else None
    )
    if price.usage_mode == "per_question":
        usage = fill(words["per_question"], amount=money_text(price.per_question_minor, cur))
    elif price.usage_mode == "cost_plus":
        usage = words["pay_as_used"]
    else:
        usage = words["questions_included"]
    beyond = (
        fill(words["beyond_limit"], amount=money_text(price.overage_per_question_minor, cur))
        if price.overage == "charge" and price.overage_mode == "per_question"
        else None
    )
    return VariantView(
        plan_id=str(plan.id),
        currency=cur,
        period=price.period,
        fee_minor=price.fee_minor,
        fee_text=fee_text,
        unit_text=words["per_month"] if price.fee_minor > 0 else "",
        fee_incl_text=fee_incl,
        gst_tag=gst_tag,
        billed_text=billed,
        badge=badge,
        credit_text=credit,
        usage_text=usage,
        beyond_text=beyond,
        on_request=False,
        free=price.fee_minor <= 0 and price.usage_mode == "included",
        limits=limits,
    )


def _monthly_share(plan: PlanData) -> int:
    price = plan.price
    if price is None or price.placeholder:
        return 1 << 60
    return price.fee_minor // 12 if price.period == "yearly" else price.fee_minor


def _auto_cards(data: PricingData, billing_on: bool) -> list[PricingCard]:
    """One card per plan: the price sheet's selection, else every plan with a price (cheapest
    first; with billing off every plan, smallest question limit first)."""
    by_id = {p.id: p for p in data.plans}
    if data.sheet_plan_ids:
        chosen = [by_id[i] for i in data.sheet_plan_ids if i in by_id]
    elif billing_on:
        chosen = sorted(
            (p for p in data.plans if p.price is not None),
            key=lambda p: (_monthly_share(p), p.name),
        )
    else:
        chosen = sorted(data.plans, key=lambda p: (p.questions is None, p.questions or 0, p.name))
    cards: list[PricingCard] = []
    for plan in chosen[:MAX_CARDS]:
        currency = plan.price.currency if plan.price else data.default_currency
        period: Period = "yearly" if plan.price and plan.price.period == "yearly" else "monthly"
        cards.append(
            PricingCard.model_construct(
                label=None,
                who_for={"en": plan.description},
                highlight=None,
                variants={currency: {period: plan.id}},  # type: ignore[dict-item]
                cta="signup",
            )
        )
    return cards


def _card_view(
    card: PricingCard,
    index: int,
    data: PricingData,
    words: dict[str, str],
    *,
    lang: str,
    default_lang: str,
    product: str,
    billing_on: bool,
    contact_text: str,
    currency: str,
    period: str,
) -> CardView | None:
    by_id = {p.id: p for p in data.plans}
    entries: list[tuple[PlanData, str, str]] = []  # (plan, currency, period)
    for cur, periods in card.variants.items():
        for per, plan_id in periods.items():
            plan = by_id.get(plan_id)
            if plan is None:
                continue
            if billing_on and plan.price is not None:
                cur_real, per_real = plan.price.currency, plan.price.period
                if (cur_real, per_real) != (cur, per):
                    continue  # the plan's price changed currency or period: not this variant
            entries.append((plan, cur, per))
    if not entries:
        return None
    if not billing_on:
        entries = entries[:1]
    monthly_fee: dict[str, int] = {}
    for plan_, cur_, per_ in entries:
        if per_ == "monthly" and plan_.price is not None and not plan_.price.placeholder:
            monthly_fee[cur_] = plan_.price.fee_minor
    variants = tuple(
        _variant(
            plan,
            data,
            words,
            billing_on=billing_on,
            contact_text=contact_text,
            paired_monthly=monthly_fee.get(cur),
        )
        for plan, cur, per in entries
    )
    options = [(v.currency, v.period) for v in variants]
    selected = choose_index(options, currency, period)
    first_plan = entries[selected][0]
    label = _text(card.label, lang, default_lang, product) if card.label else first_plan.name
    who_for = _text(card.who_for, lang, default_lang, product)
    return CardView(
        name=label or first_plan.name,
        who_for=who_for,
        highlight=_text(card.highlight, lang, default_lang, product) if card.highlight else None,
        cta=card.cta,
        description=first_plan.description,
        byok_included=first_plan.byok_allowed,
        variants=variants,
        selected=selected,
    )


def _calc_plan(
    plan: PlanData, variant: VariantView, index: int, est: Mapping[Any, int]
) -> CalcPlan | None:
    price = plan.price
    if price is None or price.placeholder or variant.on_request:
        return None
    yearly = price.period == "yearly"
    cap = plan.questions
    overage: int | None = None
    if price.overage == "allow":
        cap = None
    elif price.overage == "charge" and price.overage_mode == "per_question":
        overage = price.overage_per_question_minor
    return CalcPlan(
        key=f"{index}:{price.currency}:{price.period}",
        fee_minor=price.fee_minor // 12 if yearly else price.fee_minor,
        included_credit_minor=price.included_credit_minor // 12
        if yearly
        else price.included_credit_minor,
        usage_mode=price.usage_mode,  # type: ignore[arg-type]
        per_question_minor=price.per_question_minor,
        question_cap=cap,
        overage_charge_minor=overage,
        est_answer_minor=est.get(price.currency) if price.usage_mode == "cost_plus" else None,
    )


def _tax_line(
    content: LandingContent,
    data: PricingData,
    words: dict[str, str],
    lang: str,
    currency: str,
    product: str,
) -> str | None:
    page = content.pricing
    default_lang = content.default_language
    if currency == "INR":
        if not data.gst_registered:
            return None
        written = _text(page.tax_line_in, lang, default_lang, product)
        return written or words["tax_in"]
    written = _text(page.tax_line_global, lang, default_lang, product)
    return written or words["tax_global"]


def calc_inputs(
    view_cards: Sequence[CardView], data: PricingData, est: Mapping[Any, int]
) -> list[CalcPlan]:
    """Every priced variant of every card as the calculator's plans (all currencies; the page's
    script picks the visitor's)."""
    by_id = {p.id: p for p in data.plans}
    out: list[CalcPlan] = []
    for index, card in enumerate(view_cards):
        for variant in card.variants:
            plan = by_id.get(uuid.UUID(variant.plan_id))
            if plan is None:
                continue
            calc = _calc_plan(plan, variant, index, est)
            if calc is not None:
                out.append(calc)
    return out


def _credit_texts(
    content: LandingContent,
    cards: Sequence[CardView],
    selected_calc: Sequence[CalcPlan],
    data: PricingData,
    words: dict[str, str],
    lang: str,
    currency: str,
    product: str,
) -> tuple[str | None, tuple[str, ...]]:
    page = content.pricing
    dl = content.default_language
    written = _text(page.credit_box, lang, dl, product) if page.credit_box else ""
    examples = tuple(_text(e, lang, dl, product) for e in page.credit_examples)
    names = {c.key: card.name for card, c in _paired(cards, selected_calc)}
    candidates = [
        c
        for c in selected_calc
        if c.usage_mode == "per_question" and c.per_question_minor > 0 and c.fee_minor > 0
    ]
    cheapest = min(candidates, key=lambda c: (c.fee_minor, c.key)) if candidates else None
    if not written and cheapest is not None and cheapest.included_credit_minor > 0:
        answers = cheapest.included_credit_minor // cheapest.per_question_minor
        written = fill(
            words["credit_default"],
            amount=money_text(cheapest.included_credit_minor, currency),
            period=words["period_month"],
            answers=f"{answers:,}",
            price=money_text(cheapest.per_question_minor, currency),
        )
    if not examples and cheapest is not None:
        out = []
        for questions in EXAMPLE_QUESTIONS:
            cost = monthly_cost(cheapest, questions, gst=False)
            out.append(
                fill(
                    words["credit_example"],
                    daily=str(questions // 30),
                    monthly=f"{questions:,}",
                    amount=money_text(cost.monthly_minor, currency),
                    plan=names.get(cheapest.key, ""),
                )
            )
        examples = tuple(out)
    return (written or None), examples


def _paired(
    cards: Sequence[CardView], calc_plans: Sequence[CalcPlan]
) -> list[tuple[CardView, CalcPlan]]:
    out = []
    for calc in calc_plans:
        index = int(calc.key.split(":", 1)[0])
        if index < len(cards):
            out.append((cards[index], calc))
    return out


def _pick_selection(
    cards_in: Sequence[PricingCard],
    data: PricingData,
    billing_on: bool,
    requested_currency: str | None,
    requested_period: str | None,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...], bool, bool]:
    currencies: list[str] = []
    periods: list[str] = []
    show_currency = False
    show_period = False
    for card in cards_in:
        card_currencies = {cur for cur, per in card.variants.items() if per}
        if len(card_currencies) > 1:
            show_currency = True
        for cur, per_map in card.variants.items():
            if cur not in currencies:
                currencies.append(cur)
            if len({p for p in per_map}) > 1:
                show_period = True
            for p in per_map:
                if p not in periods:
                    periods.append(p)
    if not billing_on:
        show_currency = show_period = False
    ordered_currencies = tuple(c for c in CURRENCIES if c in currencies)
    ordered_periods = tuple(p for p in PERIODS if p in periods)
    currency = (
        requested_currency
        if requested_currency in ordered_currencies
        else (data.default_currency if data.default_currency in ordered_currencies else None)
    ) or (ordered_currencies[0] if ordered_currencies else data.default_currency)
    period = (
        requested_period
        if requested_period in ordered_periods
        else (
            "monthly"
            if "monthly" in ordered_periods
            else (ordered_periods[0] if ordered_periods else "monthly")
        )
    )
    return currency, period, ordered_currencies, ordered_periods, show_currency, show_period


def build_view(
    data: PricingData,
    content: LandingContent,
    lang: str,
    *,
    currency: str | None = None,
    period: str | None = None,
    billing_on: bool = True,
    whatsapp_on_sale: bool = False,
    contact_text: str = "",
    product: str = "",
) -> PricingView:
    """The pricing page for a visitor's choice. An unknown currency or period shows the default,
    never an error."""
    words = ui.strings(lang)
    page = content.pricing
    default_lang = content.default_language
    cards_in: list[PricingCard] = list(page.cards) or _auto_cards(data, billing_on)
    cards_in = cards_in[:MAX_CARDS]
    chosen_currency, chosen_period, currencies, periods, show_cur, show_per = _pick_selection(
        cards_in, data, billing_on, currency, period
    )
    cards: list[CardView] = []
    for index, card in enumerate(cards_in):
        view = _card_view(
            card,
            index,
            data,
            words,
            lang=lang,
            default_lang=default_lang,
            product=product,
            billing_on=billing_on,
            contact_text=contact_text,
            currency=chosen_currency,
            period=chosen_period,
        )
        if view is not None:
            cards.append(view)
    cards_t = tuple(cards)
    gst_added = billing_on and data.gst_registered and not data.prices_include_gst

    calc_block: CalcBlock | None = None
    credit_box: str | None = None
    examples: tuple[str, ...] = ()
    packs: list[PackView] = []
    services: list[ServiceView] = []
    fees: list[FeeRow] = []
    byok_text: str | None = None
    spend_cap: str | None = None
    if billing_on:
        est = dict(page.calculator.est_answer_minor)
        all_calc = calc_inputs(cards_t, data, est)
        shown = {_key_of(card, i) for i, card in enumerate(cards_t)}
        selected_calc = [c for c in all_calc if c.key in shown]
        credit_box, examples = _credit_texts(
            content, cards_t, selected_calc, data, words, lang, chosen_currency, product
        )
        if page.calculator.enabled and all_calc:
            calc_block = _calc_block(
                page, cards_t, all_calc, selected_calc, words, gst_added, chosen_currency
            )
        for add_on in data.add_ons:
            if add_on.currency != chosen_currency:
                continue
            price_incl = _incl(add_on.price_minor, add_on.currency, data, words)
            if add_on.kind == "credit_pack" and page.show_packs and add_on.credit_minor:
                bonus = (
                    f", {fill(words['bonus'], pct=str(add_on.bonus_pct))}"
                    if add_on.bonus_pct
                    else ""
                )
                packs.append(
                    PackView(
                        name=add_on.name,
                        description=add_on.description,
                        price_text=money_text(add_on.price_minor, add_on.currency),
                        price_incl_text=price_incl,
                        gives=f"{money_text(add_on.credit_minor, add_on.currency)} "
                        f"{words['credit_word']}{bonus}",
                        never_expires=add_on.expiry_days is None,
                    )
                )
            elif add_on.kind == "service" and page.show_services:
                suffix = f" {words['per_month']}" if add_on.renews else ""
                services.append(
                    ServiceView(
                        name=add_on.name,
                        description=add_on.description,
                        price_text=money_text(add_on.price_minor, add_on.currency) + suffix,
                        price_incl_text=price_incl,
                        query=urllib.parse.urlencode({"topic": "sales", "service": add_on.name}),
                    )
                )
            elif add_on.kind == "feature_unlock" and add_on.feature == "byok" and byok_text is None:
                suffix = f" {words['per_month']}" if add_on.renews else ""
                byok_text = fill(
                    words["own_key_addon"],
                    price=money_text(add_on.price_minor, add_on.currency) + suffix,
                )
        if page.show_whatsapp_fees and whatsapp_on_sale:
            rate = next((r for r in data.rates if r.currency == chosen_currency), None)
            if rate is not None and (
                rate.service or rate.utility or rate.marketing or rate.authentication
            ):
                fees = [
                    FeeRow(words["wa_service"], money_text(rate.service, chosen_currency)),
                    FeeRow(words["wa_utility"], money_text(rate.utility, chosen_currency)),
                    FeeRow(words["wa_marketing"], money_text(rate.marketing, chosen_currency)),
                    FeeRow(
                        words["wa_authentication"],
                        money_text(rate.authentication, chosen_currency),
                    ),
                ]
        if page.show_spend_cap:
            spend_cap = words["spend_cap_text"]
    return PricingView(
        billing_on=billing_on,
        contact_text=contact_text or words["contact_for_pricing"],
        cards=cards_t,
        currency=chosen_currency,
        period=chosen_period,
        currencies=currencies,
        periods=periods,
        show_currency_toggle=show_cur,
        show_period_toggle=show_per,
        tax_line=_tax_line(content, data, words, lang, chosen_currency, product)
        if billing_on
        else None,
        credit_box=credit_box,
        credit_examples=examples,
        calculator=calc_block,
        packs=tuple(packs),
        services=tuple(services),
        whatsapp_fees=tuple(fees),
        whatsapp_fees_note=words["wa_fees_note"],
        byok_add_on_text=byok_text,
        spend_cap_text=spend_cap,
        comparison=page.comparison_table and len(cards_t) > 1,
        gst_added=gst_added,
    )


def _key_of(card: CardView, index: int) -> str:
    v = card.variant_default()
    return f"{index}:{v.currency}:{v.period}"


def _incl(minor: int, currency: str, data: PricingData, words: dict[str, str]) -> str | None:
    if not data.gst_registered or minor <= 0:
        return None
    total = inclusive_total(minor, registered=True, prices_include_gst=data.prices_include_gst)
    return f"{money_text(total, currency)} {words['incl_gst']}"


def _calc_block(
    page: Any,
    cards: Sequence[CardView],
    all_calc: Sequence[CalcPlan],
    selected_calc: Sequence[CalcPlan],
    words: dict[str, str],
    gst: bool,
    currency: str,
) -> CalcBlock:
    settings = page.calculator
    names = {c.key: cards[int(c.key.split(":", 1)[0])].name for c in all_calc}
    texts = {
        "result": words["calc_result"],
        "result_gst": words["calc_result_gst"],
        "none": words["calc_none"],
    }
    start = settings.slider_default
    initial = calc_sentence(selected_calc, names, start, gst, currency, words)
    return CalcBlock(
        plans=tuple(all_calc),
        names=names,
        slider_max=settings.slider_max,
        slider_default=start,
        gst=gst,
        texts=texts,
        initial=initial,
        initial_value=start,
    )


def calc_sentence(
    plans: Sequence[CalcPlan],
    names: dict[str, str],
    questions: int,
    gst: bool,
    currency: str,
    words: dict[str, str],
) -> str:
    """The calculator's sentence for a number of questions (also what the script writes)."""
    best = recommend(plans, questions, gst=gst)
    if best is None:
        return words["calc_none"]
    values = {
        "plan": names.get(best.key, ""),
        "amount": money_text(best.monthly_minor, currency),
        "incl": money_text(best.monthly_incl_minor, currency),
    }
    return fill(words["calc_result_gst" if gst else "calc_result"], **values)


def teaser_view(view: PricingView, plan_ids: Sequence[uuid.UUID]) -> tuple[CardView, ...]:
    """The cards for the home page's compact pricing: the named plans, else the first four."""
    if plan_ids:
        wanted = {str(i) for i in plan_ids}
        picked = tuple(c for c in view.cards if any(v.plan_id in wanted for v in c.variants))
        if picked:
            return picked[:MAX_CARDS]
    return view.cards[:MAX_CARDS]


def view_for_page(
    content: LandingContent,
    env: Any,
    lang: str,
    page: str,
    *,
    currency: str | None = None,
    period: str | None = None,
) -> PricingView | None:
    """The pricing view a page needs: the pricing page, and the home page when it has a pricing
    teaser. Any other page needs none."""
    teaser = any(s.type == "pricing_teaser" and s.enabled for s in content.home)
    if page != "pricing" and not (page == "home" and teaser):
        return None
    return build_view(
        env.pricing or PricingData(),
        content,
        lang,
        currency=currency,
        period=period,
        billing_on=env.billing_on,
        whatsapp_on_sale=env.whatsapp_on_sale,
        contact_text=env.branding.contact_text,
        product=env.branding.product_name,
    )


def calc_json(view: PricingView | None) -> str | None:
    """The calculator's data as one JSON text for `<script type="application/json">`. `<` is
    escaped so the text can never close the script element."""
    if view is None or view.calculator is None:
        return None
    block = view.calculator
    body = {
        "plans": [dataclasses.asdict(p) for p in block.plans],
        "names": block.names,
        "slider_max": block.slider_max,
        "slider_default": block.slider_default,
        "gst": block.gst,
        "texts": block.texts,
        "currency": view.currency,
        "period": view.period,
    }
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    for char, code in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        text = text.replace(char, code)
    return text
