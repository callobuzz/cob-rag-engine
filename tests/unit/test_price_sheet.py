"""The white-label price list (TV3): what it lists, what it never shows, the public link and its
rotation."""

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from callobuzz_rag.accounts.settings_store import (
    SETTINGS_ROW_ID,
    Branding,
    PriceSheetSettings,
    invalidate_engine_settings_cache,
)
from callobuzz_rag.billing.notice import ist_midnight
from callobuzz_rag.billing.price_sheet import as_dict, build_sheet, render_html
from callobuzz_rag.db.models.add_on import AddOn
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.plan import Plan
from tests.unit.billing_fakes import stored_pricing
from tests.unit.test_auth_routes import ADMIN, World, auth_app, login, req

NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
BRANDING = Branding(
    product_name="Sahayak AI",
    logo_url="https://cdn.sahayak.example/logo.png",
    primary_color="#0a7d5a",
)


def plan(world: World, name: str, *, fee: int = 0, **extra: Any) -> Plan:
    row = world.db.add(Plan(name=name, is_active=True, **extra))
    world.db.add(
        stored_pricing(
            row,
            subscription_fee_minor=fee,
            usage_mode="per_question",
            per_question_minor=200,
            markup_bp=2500,
            included_credit_minor=50_000 if fee else 0,
            overage="charge",
            overage_per_question_minor=300,
        )
    )
    return row


@pytest.fixture
def world() -> World:
    w = World()
    w.growth = plan(w, "Growth", fee=99_900, max_knowledge_bases=3, monthly_questions=2_000)  # type: ignore[attr-defined]
    w.starter = plan(w, "Starter", fee=49_900, monthly_questions=500)  # type: ignore[attr-defined]
    w.internal = w.db.add(Plan(name="Internal", is_active=True, is_internal=True))  # type: ignore[attr-defined]
    w.db.add(
        stored_pricing(w.internal, subscription_fee_minor=1)  # type: ignore[attr-defined]
    )
    w.db.default_plan.is_active = False  # "Standard" has no pricing and is not for sale
    return w


async def sheet(world: World, **settings: Any) -> Any:
    return await build_sheet(world.db.session(), PriceSheetSettings(**settings), BRANDING, now=NOW)


async def test_by_default_every_active_priced_plan_by_fee_and_never_an_internal_one(
    world: World,
) -> None:
    result = await sheet(world)
    assert [p.name for p in result.plans] == ["Starter", "Growth"]
    assert result.product_name == "Sahayak AI" and result.generated_on == "5 October 2026"


async def test_picked_plans_keep_the_deployers_order_and_skip_internal_and_unknown(
    world: World,
) -> None:
    result = await sheet(
        world,
        plan_ids=[world.growth.id, world.internal.id, uuid.uuid4(), world.starter.id],  # type: ignore[attr-defined]
    )
    assert [p.name for p in result.plans] == ["Growth", "Starter"]


async def test_each_plan_shows_fee_credit_price_and_limits_in_words(world: World) -> None:
    [starter, growth] = (await sheet(world)).plans
    assert growth.price == "₹999 a month"
    assert growth.credit == "₹500 of credit included every month"
    assert growth.usage == "₹2 per question"
    assert growth.beyond_limit == "₹3 per question beyond your limits"
    assert dict(growth.limits) == {
        "Knowledge bases": "3",
        "Questions each month": "2,000",
        "Documents": "Unlimited",
        "Website chat widgets": "Unlimited",
    }
    assert dict(starter.limits)["Questions each month"] == "500"


async def test_an_announced_price_is_shown_as_from_its_date(world: World) -> None:
    coming = stored_pricing(
        world.growth,  # type: ignore[attr-defined]
        version=2,
        subscription_fee_minor=129_900,
        usage_mode="per_question",
        per_question_minor=300,
    )
    coming.is_current = False
    coming.effective_at = ist_midnight(NOW.date() + timedelta(days=31))
    world.db.add(coming)
    growth = next(p for p in (await sheet(world)).plans if p.name == "Growth")
    assert growth.price == "₹999 a month"  # what is in force now
    assert growth.upcoming == "₹1,299 a month from 5 November 2026"


async def test_add_ons_when_asked_for_and_not_otherwise(world: World) -> None:
    world.db.add(
        AddOn(
            kind="question_pack",
            name="500 extra questions",
            description="For a busy month",
            currency="INR",
            price_minor=50_000,
            questions=500,
            is_active=True,
        )
    )
    world.db.add(
        AddOn(
            kind="credit_pack",
            name="Top-up pack",
            currency="INR",
            price_minor=100_000,
            credit_minor=110_000,
            bonus_pct=10,
            is_active=True,
        )
    )
    world.db.add(
        AddOn(
            kind="question_pack",
            name="Retired",
            currency="INR",
            price_minor=1,
            questions=1,
            is_active=False,
        )
    )
    shown = await sheet(world, show_add_ons=True)
    assert [(a.name, a.price, a.gives) for a in shown.add_ons] == [
        ("500 extra questions", "₹500", "+500 questions"),
        ("Top-up pack", "₹1,000", "₹1,100.00 credit (+10 % bonus)"),
    ]
    assert (await sheet(world, show_add_ons=False)).add_ons == []


async def test_neither_the_markup_nor_the_cost_nor_an_internal_plan_is_ever_in_the_output(
    world: World,
) -> None:
    result = await sheet(world, note="Prices exclude GST.")
    text = json.dumps(as_dict(result)) + render_html(result)
    for forbidden in ("markup", "2500", "cost_usd", "Internal", "cost"):
        assert forbidden not in text, forbidden
    assert "Prices exclude GST." in text


def test_the_page_carries_the_providers_name_logo_colour_and_escapes_everything() -> None:
    from callobuzz_rag.billing.price_sheet import PlanEntry, PriceSheet

    nasty = PriceSheet(
        product_name="<b>Sahayak</b>",
        logo_url="https://cdn.sahayak.example/logo.png",
        color="#0a7d5a",
        note="<script>alert(1)</script>",
        plans=[
            PlanEntry(
                uuid.uuid4(), "<i>Growth</i>", "d", "₹999 a month", None, "u", None, [("L", "1")]
            )
        ],
        add_ons=[],
        generated_on="5 October 2026",
    )
    page = render_html(nasty)
    assert "<script>" not in page and "&lt;script&gt;" in page and "&lt;i&gt;Growth" in page
    assert "https://cdn.sahayak.example/logo.png" in page and "#0a7d5a" in page
    assert "Content-Security-Policy" in page


# ---------------------------------------------------------------- the routes


@contextmanager
def app_for(world: World, *, token: str | None = None) -> Iterator[Any]:
    invalidate_engine_settings_cache()
    value: dict[str, Any] = {
        "branding": {"product_name": "Sahayak AI", "primary_color": "#0a7d5a"},
        "trust": {"price_sheet": {"note": "Prices exclude GST.", "public_token": token}},
    }
    world.db.add(EngineSettingsRow(id=SETTINGS_ROW_ID, value=value))
    with auth_app(world, public_url="https://api.sahayak.example") as app:
        yield app
    invalidate_engine_settings_cache()


async def test_the_admin_previews_json_and_html(world: World) -> None:
    with app_for(world) as app:
        as_json = await req(app, "GET", "/api/v1/admin/price-sheet", key=ADMIN)
        as_html = await req(app, "GET", "/api/v1/admin/price-sheet?format=html", key=ADMIN)
    assert as_json.status_code == 200 and as_json.json()["plans"][0]["name"] == "Starter"
    assert "Prices exclude GST." in as_html.text and "Sahayak AI" in as_html.text
    assert as_html.headers["content-type"].startswith("text/html")


async def test_a_new_link_serves_the_page_and_the_old_one_stops(world: World) -> None:
    with app_for(world) as app:
        first = await req(app, "POST", "/api/v1/admin/price-sheet/link", key=ADMIN)
        url = first.json()["url"]
        assert first.status_code == 201
        assert url.startswith("https://api.sahayak.example/api/v1/public/price-sheet/")
        path = url.removeprefix("https://api.sahayak.example")
        page = await req(app, "GET", path)
        as_json = await req(app, "GET", path + "?format=json")
        second = await req(app, "POST", "/api/v1/admin/price-sheet/link", key=ADMIN)
        old = await req(app, "GET", path)
        unknown = await req(app, "GET", "/api/v1/public/price-sheet/not-a-real-token")
        fresh = await req(
            app, "GET", second.json()["url"].removeprefix("https://api.sahayak.example")
        )
    assert page.status_code == 200 and "Sahayak AI" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert as_json.status_code == 200 and as_json.json()["product_name"] == "Sahayak AI"
    assert old.status_code == 404 and old.json()["code"] == "price_sheet_not_found"
    assert old.json() == unknown.json()  # a rotated link and an unknown one look the same
    assert fresh.status_code == 200
    assert len(world.audits("settings.price_sheet_link_rotated")) == 2


async def test_removing_the_link_ends_it_and_a_second_removal_is_quiet(world: World) -> None:
    with app_for(world) as app:
        url = (await req(app, "POST", "/api/v1/admin/price-sheet/link", key=ADMIN)).json()["url"]
        path = url.removeprefix("https://api.sahayak.example")
        gone = await req(app, "DELETE", "/api/v1/admin/price-sheet/link", key=ADMIN)
        again = await req(app, "DELETE", "/api/v1/admin/price-sheet/link", key=ADMIN)
        page = await req(app, "GET", path)
    assert gone.status_code == 204 and again.status_code == 204
    assert page.status_code == 404 and page.json()["code"] == "price_sheet_not_found"
    assert len(world.audits("settings.price_sheet_link_removed")) == 1


async def test_without_any_link_the_public_route_is_a_404(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", "/api/v1/public/price-sheet/anything")
    assert res.status_code == 404


async def test_the_link_is_not_settable_through_a_settings_patch(world: World) -> None:
    with app_for(world) as app:
        res = await req(
            app,
            "PATCH",
            "/api/v1/admin/settings",
            key=ADMIN,
            json={"trust": {"price_sheet": {"public_token": "attacker-chosen", "note": "Hi"}}},
        )
        page = await req(app, "GET", "/api/v1/public/price-sheet/attacker-chosen")
    assert page.status_code == 404
    assert res.status_code == 422  # the patch has no such field


async def test_only_the_admin_manages_it(world: World) -> None:
    world.member(world.user("asha@shree.example"), world.shree, "owner")
    with app_for(world) as app:
        token = (await login(app, "asha@shree.example")).json()["access_token"]
        for method, path in (
            ("GET", "/api/v1/admin/price-sheet"),
            ("POST", "/api/v1/admin/price-sheet/link"),
            ("DELETE", "/api/v1/admin/price-sheet/link"),
        ):
            owner = await req(app, method, path, token=token)
            anonymous = await req(app, method, path)
            assert owner.status_code == 403, (method, path)
            assert anonymous.status_code in (401, 403), (method, path)


async def test_resetting_the_price_list_settings_keeps_the_link_and_the_audit_free_of_it(
    world: World,
) -> None:
    from callobuzz_rag.db.models.audit_log import AuditLog

    with app_for(world, token="live-link-token") as app:
        res = await req(
            app,
            "PATCH",
            "/api/v1/admin/settings",
            key=ADMIN,
            json={"trust": {"price_sheet": None}},
        )
        page = await req(app, "GET", "/api/v1/public/price-sheet/live-link-token")
        shown = await req(app, "GET", "/api/v1/admin/settings", key=ADMIN)
    assert res.status_code == 200, res.text
    assert page.status_code == 200  # the shared link still works
    assert shown.json()["trust"]["price_sheet"]["note"] == ""  # the rest went back to default
    assert world.audits("settings.price_sheet_link_removed") == []
    for row in world.db.all(AuditLog):
        assert "live-link-token" not in str(row.details)


def test_the_diff_never_carries_the_link_token() -> None:
    from callobuzz_rag.accounts.settings_store import EngineSettings, settings_diff

    before = EngineSettings.model_validate({"trust": {"price_sheet": {"public_token": "abc"}}})
    after = EngineSettings.model_validate({"trust": {"price_sheet": {"note": "Hi"}}})
    assert settings_diff(before, after) == {
        "trust.price_sheet.note": {"from": "", "to": "Hi"},
    }
