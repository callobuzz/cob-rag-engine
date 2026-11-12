"""LP3 T1: the pricing view from plans, pricing versions and add-ons."""

import json
from pathlib import Path
from typing import Any

import pytest

from callobuzz_rag.accounts.settings_store import (
    EngineSettings,
    PriceSheetSettings,
    SellerTaxProfile,
    TrustSettings,
    WhatsAppEngineSettings,
    WhatsAppRates,
)
from callobuzz_rag.billing.seed_saas import PLACEHOLDER_BY
from callobuzz_rag.db.models.add_on import AddOn
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.landing import pricing
from callobuzz_rag.landing.schema import default_content
from tests.unit.billing_fakes import stored_pricing
from tests.unit.landing_fakes import content_with
from tests.unit.test_auth_routes import World

GSTIN = "27AAPFU0939F1ZV"


def make_world(
    *, placeholder_pro: bool = False, usd: bool = False
) -> tuple[World, dict[str, Plan]]:
    w = World()
    free = w.db.add(
        Plan(
            name="Free", is_active=True, monthly_questions=100, max_documents=20, byok_allowed=False
        )
    )
    w.db.add(stored_pricing(free, usage_mode="included"))
    std = w.db.add(
        Plan(
            name="Standard",
            is_active=True,
            description="When leads matter",
            max_knowledge_bases=3,
            byok_allowed=False,
        )
    )
    w.db.add(
        stored_pricing(
            std,
            subscription_fee_minor=99_900,
            usage_mode="per_question",
            per_question_minor=200,
            markup_bp=2500,
            included_credit_minor=50_000,
            grace_minor=77_700,
            credit_limit_minor=88_800,
        )
    )
    pro = w.db.add(Plan(name="Pro", is_active=True, byok_allowed=True, max_knowledge_bases=20))
    version = stored_pricing(
        pro,
        subscription_fee_minor=499_900,
        usage_mode="per_question",
        per_question_minor=150,
        included_credit_minor=300_000,
        overage="charge",
        overage_per_question_minor=300,
    )
    if placeholder_pro:
        version.created_by = PLACEHOLDER_BY
    w.db.add(version)
    plans = {"free": free, "std": std, "pro": pro}
    if usd:
        std_usd = w.db.add(Plan(name="Standard USD", is_active=True))
        w.db.add(
            stored_pricing(
                std_usd,
                currency="USD",
                subscription_fee_minor=1_500,
                usage_mode="per_question",
                per_question_minor=3,
            )
        )
        plans["std_usd"] = std_usd
    w.db.default_plan.is_active = False
    w.db.add(
        AddOn(
            kind="feature_unlock",
            name="Own AI key",
            currency="INR",
            price_minor=49_900,
            duration="period",
            feature="byok",
            renews=True,
            is_active=True,
        )
    )
    w.db.add(
        AddOn(
            kind="credit_pack",
            name="Top-up 1000",
            currency="INR",
            price_minor=100_000,
            credit_minor=110_000,
            bonus_pct=10,
            is_active=True,
        )
    )
    w.db.add(
        AddOn(
            kind="service",
            name="Website design",
            description="We build it for you",
            currency="INR",
            price_minor=1_500_000,
            is_active=True,
        )
    )
    w.db.add(
        AddOn(
            kind="service",
            name="Seed service",
            currency="INR",
            price_minor=1,
            is_active=True,
            placeholder=True,
        )
    )
    return w, plans


def engine_for(plans: dict[str, Plan], *, gst: bool = True) -> EngineSettings:
    return EngineSettings(
        invoicing=SellerTaxProfile(gstin=GSTIN) if gst else SellerTaxProfile(),
        whatsapp=WhatsAppEngineSettings(
            rates_minor={
                "INR": WhatsAppRates(service=0, utility=12, marketing=78, authentication=12)
            }
        ),
        trust=TrustSettings(
            price_sheet=PriceSheetSettings(
                plan_ids=[plans["free"].id, plans["std"].id, plans["pro"].id]
            )
        ),
    )


async def data_for(w: World, plans: dict[str, Plan], *, billing_on: bool = True, **kw: Any) -> Any:
    return await pricing.load_data(w.db.session(), engine_for(plans, **kw), billing_on=billing_on)


def view_of(data: Any, **kw: Any) -> pricing.PricingView:
    kw.setdefault("whatsapp_on_sale", True)
    lang = kw.pop("lang", "en")
    content = kw.pop("content", default_content())
    return pricing.build_view(data, content, lang, product="Sahayak", **kw)


@pytest.fixture
async def view() -> pricing.PricingView:
    w, plans = make_world()
    return view_of(await data_for(w, plans))


async def test_cards_follow_the_price_sheet_selection_and_show_inclusive_totals(
    view: pricing.PricingView,
) -> None:
    assert [c.name for c in view.cards] == ["Free", "Standard", "Pro"]
    standard = view.cards[1].variant("INR", "monthly")
    assert standard.fee_text == "₹999 a month"
    assert standard.fee_incl_text == "₹1,178.82 a month incl. GST"
    assert standard.gst_tag == "+ GST"
    assert standard.credit_text == "₹500 of credit included every month"
    assert standard.usage_text == "₹2 per question"
    assert view.cards[0].variant_default().fee_text == "Free"


async def test_nothing_about_cost_or_markup_reaches_the_view(view: pricing.PricingView) -> None:
    dumped = json.dumps(view.as_public_dict(), ensure_ascii=False, default=str)
    for word in (
        "markup",
        "cost_usd",
        "cost_micro",
        "fx_ppm",
        "byok_per_question",
        "credit_limit",
        "grace",
        "77700",
        "88800",
    ):
        assert word not in dumped


def test_the_module_never_reads_cost_columns() -> None:
    source = Path(pricing.__file__).read_text("utf-8")
    assert "markup_bp" not in source and "cost_usd" not in source
    assert "grace_minor" not in source and "credit_limit_minor" not in source


async def test_placeholder_prices_are_on_request_and_placeholder_add_ons_hidden() -> None:
    w, plans = make_world(placeholder_pro=True)
    view = view_of(await data_for(w, plans))
    pro = next(c for c in view.cards if c.name == "Pro")
    assert pro.variant("INR", "monthly").fee_text == "Price on request"
    assert pro.variant_default().credit_text is None and pro.variant_default().usage_text is None
    assert all(s.name != "Seed service" for s in view.services)
    assert view.calculator is not None
    assert all(not p.key.startswith("2:") for p in view.calculator.plans)  # left out of the maths


async def test_toggles_appear_only_when_the_data_has_both() -> None:
    w, plans = make_world()
    one = view_of(await data_for(w, plans))
    assert one.show_currency_toggle is False and one.show_period_toggle is False

    w2, plans2 = make_world(usd=True)
    data = await data_for(w2, plans2)
    both = pricing.build_view(
        data,
        content_with(
            lambda d: d["pricing"].update(
                cards=[
                    {
                        "who_for": {"en": "For a clinic"},
                        "variants": {
                            "INR": {"monthly": str(plans2["std"].id)},
                            "USD": {"monthly": str(plans2["std_usd"].id)},
                        },
                    }
                ]
            )
        ),
        "en",
        product="Sahayak",
    )
    assert both.show_currency_toggle is True and both.show_period_toggle is False
    assert both.currency == "INR"
    usd = pricing.build_view(data, both_content(plans2), "en", currency="USD", product="Sahayak")
    assert usd.currency == "USD" and usd.cards[0].variant_default().fee_text == "$15 a month"


def both_content(plans: dict[str, Plan]) -> Any:
    return content_with(
        lambda d: d["pricing"].update(
            cards=[
                {
                    "who_for": {"en": "For a clinic"},
                    "variants": {
                        "INR": {"monthly": str(plans["std"].id)},
                        "USD": {"monthly": str(plans["std_usd"].id)},
                    },
                }
            ]
        )
    )


async def test_an_unknown_currency_or_period_shows_the_default_never_an_error() -> None:
    w, plans = make_world()
    view = view_of(await data_for(w, plans), currency="EUR", period="weekly")
    assert (view.currency, view.period) == ("INR", "monthly")


async def test_billing_off_shows_the_contact_text_and_no_calculator() -> None:
    w, plans = make_world()
    view = view_of(
        await data_for(w, plans, billing_on=False),
        billing_on=False,
        contact_text="Contact us for pricing.",
    )
    assert all(c.variant_default().fee_text == "Contact us for pricing." for c in view.cards)
    assert view.calculator is None and not view.packs and not view.services
    assert view.spend_cap_text is None and view.whatsapp_fees == ()


async def test_byok_note_and_whatsapp_fees_are_pass_through(view: pricing.PricingView) -> None:
    assert view.cards[2].byok_included is True and view.cards[1].byok_included is False
    assert view.byok_add_on_text == "Add your own AI key: ₹499 a month"
    assert [r.label for r in view.whatsapp_fees] == [
        "Service replies",
        "Utility",
        "Marketing",
        "Authentication",
    ]
    assert view.whatsapp_fees_note.endswith("passed on at cost.")
    assert view.spend_cap_text


async def test_whatsapp_fees_are_hidden_when_whatsapp_is_not_on_sale() -> None:
    w, plans = make_world()
    view = view_of(await data_for(w, plans), whatsapp_on_sale=False)
    assert view.whatsapp_fees == ()


async def test_packs_and_services(view: pricing.PricingView) -> None:
    [pack] = view.packs
    assert pack.price_text == "₹1,000" and pack.price_incl_text == "₹1,180 incl. GST"
    assert pack.gives == "₹1,100 credit, +10% bonus" and pack.never_expires is True
    [service] = view.services
    assert service.query == "topic=sales&service=Website+design"


async def test_yearly_plan_shows_the_monthly_share_and_a_computed_badge() -> None:
    w, plans = make_world()
    yearly = w.db.add(Plan(name="Standard yearly", is_active=True))
    w.db.add(
        stored_pricing(
            yearly,
            subscription_fee_minor=990_000,
            billing_period="yearly",
            usage_mode="per_question",
            per_question_minor=200,
        )
    )
    content = content_with(
        lambda d: d["pricing"].update(
            cards=[
                {
                    "who_for": {"en": "For a clinic"},
                    "variants": {
                        "INR": {
                            "monthly": str(plans["std"].id),
                            "yearly": str(yearly.id),
                        }
                    },
                }
            ]
        )
    )
    data = await data_for(w, plans)
    view = pricing.build_view(data, content, "en", period="yearly", product="Sahayak")
    assert view.show_period_toggle is True and view.period == "yearly"
    v = view.cards[0].variant("INR", "yearly")
    assert v.fee_text == "₹825 a month" and v.billed_text == "billed ₹9,900 yearly"
    assert v.badge == "2 months free"  # 9900 <= 10 x 999
    assert view.cards[0].variant("INR", "monthly").badge is None


async def test_no_badge_when_the_yearly_price_is_not_cheaper() -> None:
    w, plans = make_world()
    dear = w.db.add(Plan(name="Dear yearly", is_active=True))
    w.db.add(
        stored_pricing(
            dear, subscription_fee_minor=1_199_900, billing_period="yearly", usage_mode="included"
        )
    )
    content = content_with(
        lambda d: d["pricing"].update(
            cards=[
                {
                    "who_for": {"en": "x"},
                    "variants": {"INR": {"monthly": str(plans["std"].id), "yearly": str(dear.id)}},
                }
            ]
        )
    )
    view = pricing.build_view(await data_for(w, plans), content, "en", product="Sahayak")
    assert view.cards[0].variant("INR", "yearly").badge is None


async def test_the_calculator_data_holds_only_calculator_fields(view: pricing.PricingView) -> None:
    calc = view.calculator
    assert calc is not None
    allowed = {
        "key",
        "fee_minor",
        "included_credit_minor",
        "usage_mode",
        "per_question_minor",
        "question_cap",
        "overage_charge_minor",
        "est_answer_minor",
    }
    import dataclasses

    for plan in calc.plans:
        assert set(dataclasses.asdict(plan)) == allowed
    assert calc.gst is True
    assert "Estimated cost" in calc.initial and "with GST" in calc.initial
    assert calc.initial_value == 900


async def test_the_credit_box_and_examples_are_computed_from_the_cheapest_priced_plan() -> None:
    w, plans = make_world()
    content = content_with(lambda d: d["pricing"].update(credit_box=None, credit_examples=[]))
    view = pricing.build_view(await data_for(w, plans), content, "en", product="Sahayak")
    assert view.credit_box and "₹500" in view.credit_box and "250 answers" in view.credit_box
    assert len(view.credit_examples) == 3
    assert "₹" in view.credit_examples[0]


async def test_the_deployers_credit_text_wins() -> None:
    w, plans = make_world()
    content = content_with(
        lambda d: d["pricing"].update(
            credit_box={"en": "A credit is one answer."}, credit_examples=[{"en": "Ten a day."}]
        )
    )
    view = pricing.build_view(await data_for(w, plans), content, "en", product="Sahayak")
    assert view.credit_box == "A credit is one answer." and view.credit_examples == ("Ten a day.",)


async def test_tax_line_follows_gst_registration_and_currency() -> None:
    w, plans = make_world()
    registered = view_of(await data_for(w, plans))
    assert registered.tax_line == "Prices exclude 18% GST. A GST invoice for every payment."
    plain = view_of(await data_for(w, plans, gst=False))
    assert plain.tax_line is None
    assert all(c.variant_default().fee_incl_text is None for c in plain.cards)


async def test_the_page_speaks_the_visitors_language() -> None:
    w, plans = make_world()
    view = view_of(await data_for(w, plans), lang="hi-Latn")
    assert view.cards[1].variant_default().fee_text == "₹999 har mahine"
    assert view.cards[0].variant_default().fee_text == "Free"


async def test_teaser_picks_the_named_plans_or_the_first_four(view: pricing.PricingView) -> None:
    assert [c.name for c in pricing.teaser_view(view, [])] == ["Free", "Standard", "Pro"]
    std = view.cards[1].variants[0].plan_id
    import uuid

    assert [c.name for c in pricing.teaser_view(view, [uuid.UUID(std)])] == ["Standard"]


def test_choose_index_prefers_the_exact_then_same_currency_monthly() -> None:
    options = [("INR", "yearly"), ("INR", "monthly"), ("USD", "monthly")]
    assert pricing.choose_index(options, "INR", "yearly") == 0
    assert pricing.choose_index(options, "USD", "yearly") == 2
    assert pricing.choose_index([("INR", "yearly")], "INR", "monthly") == 0
    assert pricing.choose_index(options, "EUR", "monthly") == 0


# P6 plan choice
async def test_a_self_service_plan_links_to_sign_up_with_its_name() -> None:
    w, plans = make_world()
    plans["free"].self_service = True
    plans["pro"].self_service = True
    view = view_of(await data_for(w, plans))
    free, std, pro = (c.variant_default() for c in view.cards)
    assert (free.signup_query, free.cta_text) == ("?plan=free", "Start free")
    assert (pro.signup_query, pro.cta_text) == ("?plan=pro", "Start with Pro")
    assert (std.signup_query, std.cta_text) == ("", "Get started")  # not self-service


async def test_no_plan_link_for_a_price_not_set_or_with_billing_off() -> None:
    w, plans = make_world(placeholder_pro=True)
    plans["pro"].self_service = True
    pro = view_of(await data_for(w, plans)).cards[2].variant_default()
    assert (pro.signup_query, pro.cta_text) == ("", "Get started")
    off = view_of(await data_for(w, plans, billing_on=False), billing_on=False)
    assert all(c.variant_default().signup_query == "" for c in off.cards)


async def test_the_plan_button_speaks_the_page_language() -> None:
    w, plans = make_world()
    plans["pro"].self_service = True
    view = view_of(await data_for(w, plans), lang="hi")
    assert view.cards[2].variant_default().cta_text == "Pro से शुरू करें"


# /P6 plan choice
