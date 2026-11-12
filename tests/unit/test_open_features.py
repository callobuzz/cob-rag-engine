"""Every feature a licence used to gate is on, under its own rules only."""

import pytest

from callobuzz_rag.billing import config as billing_config


@pytest.mark.parametrize("switched_on", [True, False])
def test_billing_follows_the_setting_only(
    monkeypatch: pytest.MonkeyPatch, switched_on: bool
) -> None:
    monkeypatch.setenv("RAG_BILLING_ENABLED", "true" if switched_on else "false")
    billing_config.reset_billing_config_cache()
    try:
        assert billing_config.billing_enabled() is switched_on
        assert not hasattr(billing_config, "billing_license_locked")
    finally:
        billing_config.reset_billing_config_cache()


def test_the_info_billing_block_has_no_licence_field() -> None:
    from callobuzz_rag.api.schemas.identity import BillingInfo

    assert set(BillingInfo.model_fields) == {"enabled"}


@pytest.fixture
def _billing_cache():  # type: ignore[no-untyped-def]
    billing_config.reset_billing_config_cache()
    yield
    billing_config.reset_billing_config_cache()


@pytest.mark.usefixtures("_billing_cache")
@pytest.mark.parametrize("switched_on", [True, False])
async def test_billing_routes_follow_the_setting(
    monkeypatch: pytest.MonkeyPatch, switched_on: bool
) -> None:
    from tests.unit.billing_fakes import fake_accounts
    from tests.unit.test_auth_routes import ADMIN, World, auth_app, login, req

    monkeypatch.setenv("RAG_BILLING_ENABLED", "true" if switched_on else "false")
    billing_config.reset_billing_config_cache()
    world = World()
    owner = world.user("asha@shree.example")
    world.member(owner, world.shree, "owner")
    with auth_app(world) as app, fake_accounts(world.db):
        admin = await req(app, "GET", "/api/v1/admin/billing/accounts", key=ADMIN)
        token = (await login(app, "asha@shree.example")).json()["access_token"]
        as_owner = await req(app, "GET", "/api/v1/billing/account", token=token)
    if switched_on:
        assert admin.status_code == 200, admin.text
        assert as_owner.status_code == 200, as_owner.text
    else:
        for res in (admin, as_owner):
            assert res.status_code == 404, res.text
            assert res.json()["code"] == "billing_disabled"


# --- branding, the footer and the widget ---------------------------------------------------

from typing import Any  # noqa: E402

from callobuzz_rag.accounts.settings_store import (  # noqa: E402
    POWERED_BY,
    Branding,
    invalidate_engine_settings_cache,
    powered_by_for,
    powered_text_for,
)
from tests.unit import test_widget as _w  # noqa: E402


def test_the_footer_follows_the_branding_switch_only() -> None:
    assert powered_by_for(Branding()) == POWERED_BY
    assert powered_by_for(Branding(hide_powered_by=True)) is None
    assert powered_text_for(Branding(hide_powered_by=True)) is None


async def _patch(app: Any, body: dict[str, Any]) -> Any:
    from tests.unit.test_auth_routes import ADMIN, req

    invalidate_engine_settings_cache()
    return await req(app, "PATCH", "/api/v1/admin/settings", key=ADMIN, json=body)


async def test_any_operator_can_rebrand_and_hide_the_footer() -> None:
    from tests.unit.test_auth_routes import World, auth_app, req
    from tests.unit.test_signup import set_mode

    world = World()
    set_mode(world, "approval", product_name="Sahayak")
    with auth_app(world) as app:
        renamed = await _patch(
            app, {"branding": {"product_name": "Vyapar Mitra", "primary_color": "#e4572e"}}
        )
        hidden = await _patch(app, {"branding": {"hide_powered_by": True}})
        invalidate_engine_settings_cache()
        public = await req(app, "GET", "/api/v1/public/branding")
        shown = await _patch(app, {"branding": {"hide_powered_by": False}})
    assert renamed.status_code == 200, renamed.text
    assert hidden.status_code == 200 and hidden.json()["branding"]["hide_powered_by"] is True
    assert public.json()["powered_by"] is None
    assert shown.status_code == 200


LOCAL, PK = "http://localhost:5173", "pk_" + "a" * 32
app, corpus = _w.app, _w.corpus  # the widget tests' fixtures, shared as they are


async def _widget_config(app: Any, corpus: Any, origin: str) -> Any:
    _w._use_session(app, _w._session(_w._key(corpus.id, allowed_origins=[_w.SITE, LOCAL]), corpus))
    async with await _w._client(app) as c:
        return await c.get("/api/v1/widget/config", headers={"X-Widget-Key": PK, "Origin": origin})


@pytest.mark.usefixtures("unlimited_plan")
@pytest.mark.parametrize("origin", [LOCAL, _w.SITE])
async def test_the_widget_answers_every_allowed_origin_and_shows_no_notice(
    app: Any, corpus: Any, origin: str
) -> None:
    res = await _widget_config(app, corpus, origin)
    assert res.status_code == 200, res.text
    body = res.json()
    assert "license" + "_notice" not in body
    assert body["powered_by"] == {"text": "cob-rag-engine by Callobuzz"}


@pytest.mark.usefixtures("unlimited_plan")
async def test_the_widget_hides_the_footer_when_branding_hides_it(app: Any, corpus: Any) -> None:
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.accounts.settings_store import EngineSettings

    settings = EngineSettings.model_validate({"branding": {"hide_powered_by": True}})
    _w._use_session(app, _w._session(_w._key(corpus.id, allowed_origins=[_w.SITE]), corpus))
    with patch(
        "callobuzz_rag.accounts.settings_store.get_engine_settings",
        AsyncMock(return_value=settings),
    ):
        async with await _w._client(app) as c:
            res = await c.get(
                "/api/v1/widget/config", headers={"X-Widget-Key": PK, "Origin": _w.SITE}
            )
    assert res.status_code == 200, res.text
    assert res.json()["powered_by"] is None


# --- workspaces, WhatsApp and answers ------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from callobuzz_rag.db.models.tenant import Tenant  # noqa: E402


async def test_the_operator_creates_any_number_of_workspaces() -> None:
    from tests.unit.test_auth_routes import World
    from tests.unit.test_tenant_lifecycle import admin, admin_app

    world = World()
    world.db.rows[Tenant] = []
    world.db.add(Tenant(name="Operator", is_operator=True, status="active", api_key_hash=""))
    world.db.add(Tenant(name="COB", is_system=True, status="active", api_key_hash=""))
    with admin_app(world) as (app, _):
        for name in ("Sharma Furniture", "Kaveri Foods", "Annapurna Clinic"):
            res = await admin(app, "POST", "", json={"name": name})
            assert res.status_code in (200, 201), res.text
    assert len([t for t in world.db.all(Tenant) if not t.is_operator and not t.is_system]) == 3


async def test_public_sign_up_works_for_a_second_and_third_business() -> None:
    from tests.unit.test_auth_routes import World, auth_app
    from tests.unit.test_signup import set_mode, signup

    world = World()
    world.db.rows[Tenant] = []
    world.db.add(Tenant(name="Operator", is_operator=True, status="active", api_key_hash=""))
    for n in range(2):
        world.db.add(Tenant(name=f"Business {n}", status="active", api_key_hash=""))
    set_mode(world, "open")
    with auth_app(world) as app:
        res = await signup(app)
    assert res.status_code < 400, res.text


def test_caps_has_no_workspace_room_check() -> None:
    from callobuzz_rag.accounts import caps

    assert not hasattr(caps, "check_workspace_room")
    assert not hasattr(caps, "NEUTRAL_WORKSPACE_REFUSAL")


@pytest.mark.parametrize(
    ("public_url", "dashboard_url", "ok"),
    [
        ("https://chat.sharmafurniture.in", "", True),
        ("", "https://admin.sharmafurniture.in", True),
        ("http://localhost:8000", "", False),
        ("http://192.168.1.20:8000", "", False),
        ("", "", False),  # no address at all counts as development
    ],
)
def test_whatsapp_needs_a_public_address(public_url: str, dashboard_url: str, ok: bool) -> None:
    from callobuzz_rag.channels.whatsapp.availability import public_address_ok

    settings = SimpleNamespace(public_url=public_url, dashboard_url=dashboard_url)
    assert public_address_ok(settings) is ok


def test_fair_use_has_no_licence_reason() -> None:
    from typing import get_args

    from callobuzz_rag.fairuse import decision, gate

    assert "license_evaluation" not in get_args(decision.Reason)
    assert not hasattr(gate, "_license_pause") and not hasattr(gate, "LICENSE_METRIC")


_WA_URL = "/api/v1/corpora/{}/whatsapp"
_MANUAL = {
    "phone_number_id": "109876543210",
    "waba_id": "5550001",
    "access_token": "EAAG-token-for-tests",
    "meta_billing": "business",
}
_SIGNUP = {"code": "AQBcode", "phone_number_id": "109876543210", "waba_id": "5550001"}


async def test_whatsapp_connect_is_refused_at_a_development_address() -> None:
    from tests.unit.test_auth_routes import ADMIN, login, req
    from tests.unit.whatsapp_world import WhatsAppWorld, whatsapp_app

    world = WhatsAppWorld()
    owner = world.user("asha@shree.example")
    world.member(owner, world.shree, "owner")
    base = _WA_URL.format(world.corpus.id)
    with whatsapp_app(world, public_url="http://localhost:8000") as app:
        token = (await login(app, "asha@shree.example")).json()["access_token"]
        as_owner = await req(app, "POST", base + "/embedded-signup", token=token, json=_SIGNUP)
        as_admin = await req(app, "PUT", base + "/manual", key=ADMIN, json=_MANUAL)
    for res in (as_owner, as_admin):
        assert res.status_code == 403, res.text
        assert res.json()["code"] == "whatsapp_needs_public_address"
    assert "RAG_PUBLIC_URL" in as_admin.json()["detail"]
    assert "RAG_PUBLIC_URL" not in as_owner.json()["detail"]
    assert "licen" not in (as_owner.text + as_admin.text).lower()


@pytest.mark.usefixtures("unlimited_plan")
async def test_info_tells_only_the_operator_that_the_address_is_missing() -> None:
    from unittest.mock import patch

    from tests.unit.test_auth_routes import ADMIN, login, req
    from tests.unit.whatsapp_world import WhatsAppWorld, whatsapp_app

    world = WhatsAppWorld()
    owner = world.user("asha@shree.example")
    world.member(owner, world.shree, "owner")
    with (
        whatsapp_app(world, public_url="http://localhost:8000") as app,
        patch("callobuzz_rag.channels.whatsapp.availability.plan_allows", return_value=True),
    ):
        admin = await req(app, "GET", "/api/v1/info", key=ADMIN)
        token = (await login(app, "asha@shree.example")).json()["access_token"]
        as_owner = await req(app, "GET", "/api/v1/info", token=token)
    a, o = admin.json()["features"]["whatsapp"], as_owner.json()["features"]["whatsapp"]
    assert a["available"] is False and a["public_address_missing"] is True
    assert o["available"] is False and o["public_address_missing"] is False
    assert "license" + "_locked" not in a


async def test_a_message_is_answered_at_a_public_address() -> None:
    from tests.unit.whatsapp_fixtures import inbound_text
    from tests.unit.whatsapp_rig import rig_for
    from tests.unit.whatsapp_world import WhatsAppWorld

    world = WhatsAppWorld()
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(
            inbound_text(wamid="wamid.OS2", text="How much is home collection?")
        )
        await rig.run(thread_id)
        assert len(rig.texts()) == 1
