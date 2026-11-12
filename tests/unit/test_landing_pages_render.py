"""LP3 T3, T4, T6: the pricing, features, about and contact pages as a visitor's browser gets them."""

import dataclasses
import json
import re
import uuid
from typing import Any

import pytest

from callobuzz_rag.landing import snapshot
from callobuzz_rag.landing.copy_rules import copy_warnings_in_text
from tests.unit.landing_fakes import content_with, make_client, make_env, make_site


def plan_uuid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"{name}.sharmadental.in")


def main_of(html: str) -> str:
    return html.split("<main")[1].split("</main>")[0]


@pytest.fixture
def client_on(monkeypatch: pytest.MonkeyPatch) -> Any:
    return make_client(monkeypatch, site=make_site())


def two_currency_env() -> Any:
    """The example environment: it also holds a dollar plan and a yearly rupee plan, which only a
    card that names them shows (the toggles)."""
    return make_env()


def toggle_content() -> Any:
    def cards(d: dict[str, Any]) -> None:
        d["pricing"]["cards"] = [
            {
                "who_for": {"en": "For a clinic"},
                "highlight": {"en": "Most chosen"},
                "variants": {
                    "INR": {
                        "monthly": str(plan_uuid("Standard")),
                        "yearly": str(plan_uuid("Standard yearly")),
                    },
                    "USD": {"monthly": str(plan_uuid("Standard USD"))},
                },
            },
            {
                "who_for": {"en": "For a group"},
                "variants": {"INR": {"monthly": str(plan_uuid("Pro"))}},
            },
        ]

    return content_with(cards)


# ----------------------------------------------------------------- pricing


async def test_cards_in_order_with_who_for_and_credit_box(client_on: Any) -> None:
    html = main_of((await client_on.get("/pricing")).text)
    assert html.count("<h1") == 1
    order = [html.index(f'class="plan-name">{n}<') for n in ("Free", "Standard", "Pro")]
    assert order == sorted(order)
    assert "When leads matter as much as answers" in html
    assert "₹999" in html and "₹1,178.82 a month incl. GST" in html and "+ GST" in html
    assert "What is a credit?" in html and 'class="credit-box"' in html
    assert "Prices exclude 18% GST. A GST invoice for every payment." in html


async def test_one_card_carries_the_highlight(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(monkeypatch, site=make_site(toggle_content()), env=two_currency_env())
    html = main_of((await client.get("/pricing")).text)
    assert html.count("Most chosen") == 1 and "is-highlight" in html


async def test_the_calculator_data_parses_and_holds_only_calculator_fields(client_on: Any) -> None:
    html = (await client_on.get("/pricing")).text
    match = re.search(
        r'<script type="application/json" id="pricing-data">(.*?)</script>', html, re.S
    )
    assert match
    data = json.loads(match.group(1))
    assert set(data["plans"][0]) == {
        "key",
        "fee_minor",
        "included_credit_minor",
        "usage_mode",
        "per_question_minor",
        "question_cap",
        "overage_charge_minor",
        "est_answer_minor",
    }
    assert data["gst"] is True and data["slider_default"] == 900
    assert 'type="range"' in html and 'aria-live="polite"' in html and "Estimated cost" in html
    for word in ("markup", "cost_usd", "grace", "credit_limit"):
        assert word not in html


async def test_blocks_follow_the_settings(monkeypatch: pytest.MonkeyPatch, client_on: Any) -> None:
    on = main_of((await client_on.get("/pricing")).text)
    assert "Top-up 1000" in on and "Website design" in on
    assert "Authentication" in on and "passed on at cost." in on
    assert "Your assistant never goes silent" in on and "Add your own AI key: ₹499 a month" in on

    def off(d: dict[str, Any]) -> None:
        d["pricing"].update(
            show_packs=False,
            show_services=False,
            show_whatsapp_fees=False,
            show_spend_cap=False,
            comparison_table=False,
        )
        d["pricing"]["calculator"]["enabled"] = False

    client = make_client(monkeypatch, site=make_site(content_with(off)))
    html = main_of((await client.get("/pricing")).text)
    for gone in (
        "Top-up 1000",
        "Website design",
        "Authentication",
        "Your assistant never goes silent",
        "compare-table",
        "calc-range",
    ):
        assert gone not in html


async def test_whatsapp_fees_need_whatsapp_on_sale(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(monkeypatch, site=make_site(), env=make_env(whatsapp_on_sale=False))
    assert "passed on at cost" not in (await client.get("/pricing")).text


async def test_the_comparison_is_a_real_table_that_can_collapse(client_on: Any) -> None:
    html = main_of((await client_on.get("/pricing")).text)
    assert '<table class="compare-table"' in html
    assert html.count('<th scope="col">') == 4 and '<th scope="row">' in html
    assert '<details class="compare" open>' in html


async def test_the_faq_uses_details(client_on: Any) -> None:
    html = main_of((await client_on.get("/pricing")).text)
    assert html.count('<details class="faq-item">') == 5


async def test_a_query_picks_the_variant_and_the_canonical_stays_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(monkeypatch, site=make_site(toggle_content()), env=two_currency_env())
    res = await client.get("/pricing?currency=USD&period=yearly")
    html = res.text
    assert 'data-pricing data-currency="USD" data-period="yearly"' in html
    assert '<link rel="canonical" href="https://www.sharmadental.in/pricing">' in html
    assert "currency=USD" not in html.split("</title>")[0]
    shown = re.findall(
        r'<div class="plan-variant"[^>]*data-currency="(\w+)" data-period="(\w+)"( hidden)?', html
    )
    visible = [(c, p) for c, p, hidden in shown if not hidden]
    assert ("USD", "monthly") in visible  # no yearly dollar plan: the same currency, monthly
    default = await client.get("/pricing")
    assert default.headers["etag"] != res.headers["etag"]


async def test_an_unknown_query_value_renders_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(monkeypatch, site=make_site(toggle_content()), env=two_currency_env())
    res = await client.get("/pricing?currency=EUR&period=weekly")
    assert (
        res.status_code == 200
        and 'data-pricing data-currency="INR" data-period="monthly"' in res.text
    )


async def test_the_toggles_are_links_and_only_appear_when_they_change_something(
    monkeypatch: pytest.MonkeyPatch, client_on: Any
) -> None:
    assert "data-toggles" not in (await client_on.get("/pricing")).text
    client = make_client(monkeypatch, site=make_site(toggle_content()), env=two_currency_env())
    html = (await client.get("/pricing")).text
    assert 'class="toggle-opt" rel="nofollow" href="?currency=USD&amp;period=monthly"' in html
    assert 'data-value="yearly"' in html


async def test_yearly_prices_show_the_monthly_share_and_the_billed_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(monkeypatch, site=make_site(toggle_content()), env=two_currency_env())
    html = (await client.get("/pricing?period=yearly")).text
    assert "billed ₹9,900 yearly" in html and "2 months free" in html


async def test_billing_off_shows_the_contact_text_and_no_money(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(monkeypatch, site=make_site(), env=make_env(billing_on=False))
    html = main_of((await client.get("/pricing")).text)
    assert "Contact us for pricing." in html
    for gone in ("₹999", "calc-range", "Top-up", "Your assistant never goes silent", "credit-box"):
        assert gone not in html


async def test_a_label_cannot_close_the_calculator_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evil = "</script><img src=x onerror=a()>&"

    def cards(d: dict[str, Any]) -> None:
        d["pricing"]["cards"] = [
            {
                "label": {"en": evil},
                "who_for": {"en": "For a clinic"},
                "variants": {"INR": {"monthly": str(plan_uuid("Standard"))}},
            }
        ]

    client = make_client(monkeypatch, site=make_site(content_with(cards)))
    html = (await client.get("/pricing")).text
    start = html.index('id="pricing-data">') + len('id="pricing-data">')
    block = html[start : html.index("</script>", start)]
    assert "<" not in block and ">" not in block and "&" not in block
    assert evil in json.loads(block)["names"].values()
    assert "<img src=x" not in html


async def test_placeholder_prices_read_price_on_request(monkeypatch: pytest.MonkeyPatch) -> None:
    data = snapshot.example_pricing_data()
    pro = dataclasses.replace(
        data.plans[2], price=dataclasses.replace(data.plans[2].price, placeholder=True)
    )  # type: ignore[type-var]
    env = make_env(pricing=dataclasses.replace(data, plans=(*data.plans[:2], pro)))
    client = make_client(monkeypatch, site=make_site(), env=env)
    html = main_of((await client.get("/pricing")).text)
    assert "Price on request" in html and "₹4,999" not in html


async def test_the_page_speaks_hinglish(monkeypatch: pytest.MonkeyPatch) -> None:
    def langs(d: dict[str, Any]) -> None:
        d["languages"] = ["en", "hi-Latn"]
        d["pricing"]["headline"]["hi-Latn"] = "Saral plans."

    client = make_client(monkeypatch, site=make_site(content_with(langs)))
    html = main_of((await client.get("/hi-latn/pricing")).text)
    assert "Saral plans." in html and "₹999" in html and "har mahine" in html


async def test_the_teaser_on_the_home_page_is_compact_and_links_to_pricing(client_on: Any) -> None:
    html = (await client_on.get("/")).text
    teaser = html.split('data-section="pricing_teaser"')[1].split("</section>")[0]
    assert teaser.count('class="plan-card') <= 4 and 'href="/pricing"' in teaser
    assert "Standard" in teaser and "plan-limits" not in teaser


async def test_every_page_text_passes_the_copy_rules(client_on: Any) -> None:
    for path in ("/pricing", "/features", "/about", "/contact"):
        text = re.sub(r"<[^>]+>", " ", main_of((await client_on.get(path)).text))
        assert copy_warnings_in_text(text) == [], path


# ----------------------------------------------------------------- features and about


async def test_features_render_groups_items_and_the_checklist(client_on: Any) -> None:
    html = main_of((await client_on.get("/features")).text)
    assert html.count("<h1") == 1 and 'class="tile"' in html
    assert "What to check before you sign" in html and 'id="checklist"' in html


async def test_about_shows_company_details_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.core.company import CompanyDetails

    env = make_env(
        company=CompanyDetails(
            legal_name="Sharma Dental Clinic",
            address_line1="12 MG Road",
            city="Pune",
            state_code="27",
            pincode="411001",
            email="hello@sharmadental.in",
            phone="+91 98220 12345",
        )
    )
    client = make_client(monkeypatch, site=make_site(), env=env)
    html = main_of((await client.get("/about")).text)
    assert "Sharma Dental Clinic" in html and "12 MG Road" in html and "Pune" in html
    assert 'href="mailto:hello@sharmadental.in"' in html and 'href="tel:+919822012345"' in html

    bare = make_client(monkeypatch, site=make_site(), env=make_env(company=CompanyDetails()))
    assert 'class="section company"' not in main_of((await bare.get("/about")).text)

    def hide(d: dict[str, Any]) -> None:
        d["about"]["show_company_details"] = False

    hidden = make_client(monkeypatch, site=make_site(content_with(hide)), env=env)
    assert 'class="section company"' not in main_of((await hidden.get("/about")).text)


async def test_pages_that_are_switched_off_are_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    def off(d: dict[str, Any]) -> None:
        d["features"]["enabled"] = False
        d["about"]["enabled"] = False

    client = make_client(monkeypatch, site=make_site(content_with(off)))
    for path in ("/features", "/about"):
        assert (await client.get(path)).status_code == 404
    home = (await client.get("/")).text
    assert (
        'href="/features"' not in home.split("<main")[0]
        and 'href="/about"' not in home.split("<main")[0]
    )
    sitemap = (await client.get("/sitemap.xml")).text
    assert "/features" not in sitemap and "/about" not in sitemap
