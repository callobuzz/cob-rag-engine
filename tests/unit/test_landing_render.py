"""LP1 T5: the server-rendered pages, language prefixes, caching and the CSP."""

import base64
import hashlib
import re
from typing import Any

import pytest

from tests.unit.landing_fakes import ADDRESS, content_with, make_client, make_env, make_site


def _hero(data: dict[str, Any]) -> dict[str, Any]:
    return next(s for s in data["home"] if s["type"] == "hero")


@pytest.fixture
def client_on(monkeypatch: pytest.MonkeyPatch) -> Any:
    return make_client(monkeypatch, site=make_site())


async def test_off_means_404_with_the_fallback_header(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(monkeypatch, site=None)
    for path in ("/", "/pricing", "/features", "/about", "/contact", "/hi/", "/hi"):
        res = await client.get(path)
        assert res.status_code == 404 and res.headers["x-cob-landing"] == "off", path
        assert res.headers["cache-control"] == "no-store"


async def test_canonical_and_links_never_come_from_the_host_header(client_on: Any) -> None:
    res = await client_on.get(
        "/", headers={"Host": "evil.example", "X-Forwarded-Host": "evil.example"}
    )
    assert res.status_code == 200
    assert f'<link rel="canonical" href="{ADDRESS}/">' in res.text
    assert "evil.example" not in res.text


async def test_one_h1_real_text_and_content_without_js(client_on: Any) -> None:
    html = (await client_on.get("/")).text
    assert html.count("<h1") == 1
    assert "Fees kitni hai?" in html  # the scripted conversation is in the HTML
    assert 'class="motion-ok"' not in html.split("<body")[0]  # opted into by JS, never default
    assert "Answers customers day and night" in html
    assert "<title>" in html and "Sahayak" in html.split("</title>")[0]


async def test_every_section_of_the_default_renders(client_on: Any) -> None:
    html = (await client_on.get("/")).text
    for kind in (
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
        "pricing_teaser",
        "privacy",
        "faq",
        "final_cta",
    ):
        assert f'data-section="{kind}"' in html, kind
    assert 'data-section="proof"' not in html  # switched off in the default


async def test_csp_hashes_every_inline_script_and_nothing_else(client_on: Any) -> None:
    res = await client_on.get("/")
    csp = res.headers["content-security-policy"]
    scripts = re.findall(
        r'<script(?![^>]*\bsrc=)(?![^>]*type="application/(?:ld\+)?json")[^>]*>(.*?)</script>',
        res.text,
        re.S,
    )
    assert scripts
    for body in scripts:
        digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
        assert f"'sha256-{digest}'" in csp
    assert "'unsafe-eval'" not in csp and "frame-ancestors 'none'" in csp
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["referrer-policy"] == "strict-origin-when-cross-origin"


async def test_deployer_text_is_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    def mutate(data: dict[str, Any]) -> None:
        _hero(data)["headline"] = {"en": "<img src=x onerror=alert(1)>"}
        _hero(data)["conversation"]["business_name"] = {"en": "A & B <script>x</script>"}

    client = make_client(monkeypatch, site=make_site(content_with(mutate)))
    html = (await client.get("/")).text
    assert "<img src=x" not in html and "<script>x</script>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


async def test_the_product_name_fills_the_placeholder_after_escaping(client_on: Any) -> None:
    html = (await client_on.get("/")).text
    assert "{product}" not in html
    assert "Sahayak reads your website" in html


async def test_languages_get_their_prefix_and_hreflang(monkeypatch: pytest.MonkeyPatch) -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["languages"] = ["en", "hi"]

    client = make_client(monkeypatch, site=make_site(content_with(mutate)))
    res = await client.get("/hi/pricing")
    assert res.status_code == 200 and 'lang="hi"' in res.text
    assert f'hreflang="en" href="{ADDRESS}/pricing"' in res.text
    assert f'hreflang="hi" href="{ADDRESS}/hi/pricing"' in res.text
    assert f'hreflang="x-default" href="{ADDRESS}/pricing"' in res.text
    assert f'<link rel="canonical" href="{ADDRESS}/hi/pricing">' in res.text
    assert (await client.get("/hi-latn/pricing")).status_code == 404  # not in `languages`
    assert (await client.get("/hi")).status_code == 200 and (
        await client.get("/hi/")
    ).status_code == 200
    assert "सामग्री पर जाएँ" in res.text  # the chrome words are in the page language


async def test_a_disabled_page_is_a_site_404_not_the_off_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["pricing"]["enabled"] = False

    client = make_client(monkeypatch, site=make_site(content_with(mutate)))
    res = await client.get("/pricing")
    assert res.status_code == 404 and "x-cob-landing" not in res.headers
    assert 'name="robots" content="noindex' in res.text
    nav = (await client.get("/")).text
    assert 'href="/pricing"' not in nav.split("<main")[0]


async def test_etag_and_cache_headers(client_on: Any) -> None:
    first = await client_on.get("/")
    again = await client_on.get("/", headers={"If-None-Match": first.headers["etag"]})
    assert first.headers["cache-control"] == "public, max-age=60"
    assert again.status_code == 304 and again.content == b""
    other = await client_on.get("/pricing")
    assert other.headers["etag"] != first.headers["etag"]


async def test_signed_in_redirect_script_only_on_the_home_page(client_on: Any) -> None:
    assert "cob.rt" in (await client_on.get("/")).text
    assert "cob.rt" not in (await client_on.get("/pricing")).text


async def test_whatsapp_is_not_mentioned_when_no_plan_sells_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(monkeypatch, site=make_site(), env=make_env(whatsapp_on_sale=False))
    html = (await client.get("/")).text
    main = html.split("<main")[1].split("</main>")[0]
    # LP2: the chapter is told as an e-mail hand-off, and its words never name the channel
    assert 'data-variant="email_handoff"' in main and 'data-variant="whatsapp"' not in main
    assert "wa.me" not in html and "From WhatsApp" not in main


async def test_the_footer_has_legal_links_company_and_the_constant_powered_by(
    client_on: Any,
) -> None:
    html = (await client_on.get("/")).text
    footer = html.split("<footer")[1]
    assert 'href="/legal/terms"' in footer and "Privacy Notice" in footer
    assert "Powered by cob-rag-engine by Callobuzz" in footer
    assert "12 MG Road" in footer and "Pune" in footer
    assert 'href="https://wa.me/919822012345"' in footer


async def test_signup_links_follow_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(monkeypatch, site=make_site(), env=make_env(signup_available=False))
    html = (await client.get("/")).text
    assert 'href="/signup"' not in html and 'href="/login"' in html


async def test_pricing_page_lists_plans_from_the_price_sheet(client_on: Any) -> None:
    html = (await client_on.get("/pricing")).text
    assert "Standard" in html and "₹999 a month" in html and "incl. GST" in html
    assert html.count("<h1") == 1


async def test_billing_off_shows_the_contact_text_instead_of_prices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(monkeypatch, site=make_site(), env=make_env(billing_on=False, plans=[]))
    html = (await client.get("/pricing")).text
    assert "Contact us for pricing." in html and "₹999" not in html


async def test_other_pages_render(client_on: Any) -> None:
    for path, needle in (
        ("/features", "Everything an assistant"),
        ("/about", "We help businesses"),
        ("/contact", "Talk to us."),
    ):
        res = await client_on.get(path)
        assert res.status_code == 200 and needle in res.text, path
        assert res.text.count("<h1") == 1


async def test_ask_it_yourself_button_only_when_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["ask"]["enabled"] = True

    content = content_with(mutate)
    ready = make_client(
        monkeypatch, site=make_site(content), env=make_env(ask_public_key="pk_demo")
    )
    assert 'data-ask-key="pk_demo"' in (await ready.get("/")).text
    off = make_client(monkeypatch, site=make_site(content), env=make_env(ask_public_key=None))
    assert "data-ask" not in (await off.get("/")).text


# P6 plan choice
def _self_service_pricing() -> Any:
    import dataclasses

    from callobuzz_rag.landing import snapshot

    data = snapshot.example_pricing_data()
    plans = tuple(dataclasses.replace(p, self_service=True) for p in data.plans)
    return dataclasses.replace(data, plans=plans)


async def test_pricing_buttons_carry_the_chosen_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(pricing=_self_service_pricing())
    html = (await make_client(monkeypatch, site=make_site(), env=env).get("/pricing")).text
    buttons = re.findall(r'class="btn [^"]*plan-cta" href="([^"]+)">([^<]+)<', html)
    assert ("/signup?plan=free", "Start free") in buttons
    assert any(h.startswith("/signup?plan=") and t.startswith("Start with ") for h, t in buttons)


async def test_pricing_buttons_go_to_sign_in_when_sign_up_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = make_env(pricing=_self_service_pricing(), signup_available=False)
    html = (await make_client(monkeypatch, site=make_site(), env=env).get("/pricing")).text
    buttons = re.findall(r'plan-cta" href="([^"]+)"', html)
    assert buttons and all(h == "/login" for h in buttons)
    assert "/signup" not in html


# /P6 plan choice
