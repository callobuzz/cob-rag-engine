"""The WhatsApp plan flag, the engine's fee rates and `features.whatsapp` in /info (TV6)."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr, ValidationError

from callobuzz_rag.accounts.settings_store import EngineSettings, apply_changes, settings_diff
from callobuzz_rag.channels.policy import ChannelsPolicy, normalize_channels, parse_channels
from callobuzz_rag.channels.whatsapp import availability
from tests.unit.scope_harness import call, scoped_app
from tests.unit.test_auth_routes import World
from tests.unit.test_plans_admin import api, app_for, plan_named

# ------------------------------------------------------------------ the policy


def test_defaults_are_off_and_pass_through() -> None:
    policy = ChannelsPolicy()
    assert policy.whatsapp is False and policy.whatsapp_fees == "pass_through"
    assert parse_channels(None) == policy and parse_channels({}) == policy


def test_a_stored_dict_is_normalised_and_bad_values_fall_back() -> None:
    assert normalize_channels({}) == {"whatsapp": False, "whatsapp_fees": "pass_through"}
    assert parse_channels({"whatsapp": True, "junk": 1}).whatsapp is True
    bad = parse_channels({"whatsapp": True, "whatsapp_fees": "free"})
    assert bad.whatsapp is True and bad.whatsapp_fees == "pass_through"


def test_the_request_model_refuses_unknown_fees() -> None:
    with pytest.raises(ValidationError):
        ChannelsPolicy(whatsapp_fees="free")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ChannelsPolicy(extra=1)  # type: ignore[call-arg]


# --------------------------------------------------------------- the plan editor


@pytest.fixture
def world() -> World:
    return World()


async def test_a_plan_is_created_with_the_defaults_and_edited_with_an_audit(world: World) -> None:
    with app_for(world) as app:
        made = await api(app, "POST", json={"name": "Chat"})
        assert made.status_code == 201, made.text
        assert made.json()["channels"] == {"whatsapp": False, "whatsapp_fees": "pass_through"}
        plan_id = made.json()["id"]
        edited = await api(app, "PATCH", f"/{plan_id}", json={"channels": {"whatsapp": True}})
        assert edited.status_code == 200, edited.text
        assert edited.json()["channels"]["whatsapp"] is True
        again = await api(app, "PATCH", f"/{plan_id}", json={"channels": {"whatsapp": True}})
        assert again.status_code == 200
        bad = await api(app, "PATCH", f"/{plan_id}", json={"channels": {"whatsapp_fees": "x"}})
        assert bad.status_code == 422
        unknown = await api(app, "PATCH", f"/{plan_id}", json={"channels": {"telegram": True}})
        assert unknown.status_code == 422
    audits = world.audits("plan.updated")
    assert len(audits) == 1  # the unchanged second PATCH wrote nothing
    assert audits[0].details["changes"] == {"channels.whatsapp": {"from": False, "to": True}}
    assert plan_named(world, "Chat").channels["whatsapp"] is True


async def test_a_plan_can_be_created_with_whatsapp_on(world: World) -> None:
    with app_for(world) as app:
        made = await api(
            app,
            "POST",
            json={"name": "Chat+", "channels": {"whatsapp": True, "whatsapp_fees": "included"}},
        )
    assert made.status_code == 201, made.text
    assert made.json()["channels"] == {"whatsapp": True, "whatsapp_fees": "included"}


# ----------------------------------------------------------- the fee rates setting


def test_fee_rates_default_to_nothing() -> None:
    settings = EngineSettings()
    assert settings.whatsapp.rates_minor == {} and settings.whatsapp.free_service_per_month == 0


def test_rates_are_patched_whole_and_a_null_resets() -> None:
    patched = apply_changes(
        EngineSettings(),
        {
            "whatsapp": {
                "rates_minor": {"INR": {"service": 12, "utility": 15}},
                "free_service_per_month": 1000,
            }
        },
        allow_http_logo=False,
    )
    assert patched.whatsapp.rates_minor["INR"].utility == 15
    assert patched.whatsapp.rates_minor["INR"].marketing == 0
    diff = settings_diff(EngineSettings(), patched)
    assert set(diff) == {"whatsapp.rates_minor", "whatsapp.free_service_per_month"}
    reset = apply_changes(
        patched, {"whatsapp": {"free_service_per_month": None}}, allow_http_logo=False
    )
    assert reset.whatsapp.free_service_per_month == 0
    assert reset.whatsapp.rates_minor == patched.whatsapp.rates_minor


def test_negative_or_unknown_rates_are_refused() -> None:
    with pytest.raises(ValidationError):
        apply_changes(
            EngineSettings(),
            {"whatsapp": {"rates_minor": {"INR": {"service": -1}}}},
            allow_http_logo=False,
        )
    with pytest.raises(ValidationError):
        apply_changes(
            EngineSettings(),
            {"whatsapp": {"rates_minor": {"EUR": {"service": 1}}}},
            allow_http_logo=False,
        )


async def test_the_settings_route_round_trips_the_rates(world: World) -> None:
    from tests.unit.test_auth_routes import ADMIN, req
    from tests.unit.test_tenant_lifecycle import admin_app

    with admin_app(world) as (app, _):
        res = await req(
            app,
            "PATCH",
            "/api/v1/admin/settings",
            key=ADMIN,
            json={"whatsapp": {"rates_minor": {"INR": {"service": 12}}}},
        )
        assert res.status_code == 200, res.text
        assert res.json()["whatsapp"]["rates_minor"]["INR"]["service"] == 12
        bad = await req(
            app, "PATCH", "/api/v1/admin/settings", key=ADMIN, json={"whatsapp": {"price": 1}}
        )
        assert bad.status_code == 422


# ------------------------------------------------------------------- /info


def _ready(settings: SimpleNamespace | object) -> None:
    settings.whatsapp_enabled = True  # type: ignore[attr-defined]
    settings.whatsapp_app_id = "123456789"  # type: ignore[attr-defined]
    settings.whatsapp_app_secret = SecretStr("s")  # type: ignore[attr-defined]
    settings.whatsapp_verify_token = SecretStr("v")  # type: ignore[attr-defined]
    settings.whatsapp_config_id = "9999"  # type: ignore[attr-defined]


async def _info(key: str, *, ready: bool, plan_allows: bool = True, **extra: str) -> dict:
    with scoped_app() as (app, _):
        if ready:
            _ready(app.state.settings)
            app.state.settings.public_url = "https://chat.sharmafurniture.in"
        for name, value in extra.items():
            setattr(app.state.settings, name, value)
        with patch.object(availability, "plan_allows", AsyncMock(return_value=plan_allows)):
            res = await call(app, key, "GET", "/api/v1/info")
    assert res.status_code == 200, res.text
    return res.json()["features"]["whatsapp"]


async def test_off_when_the_engine_is_off() -> None:
    assert await _info("tk_a", ready=False) == {
        "enabled": False,
        "available": False,
        "embedded_signup": False,
        "public_address_missing": False,
    }


async def test_off_when_the_meta_app_is_incomplete() -> None:
    info = await _info("tk_a", ready=True, whatsapp_app_secret=SecretStr(""))
    assert info["enabled"] is False and info["available"] is False


async def test_off_when_the_plan_lacks_the_flag() -> None:
    info = await _info("tk_a", ready=True, plan_allows=False)
    assert info == {
        "enabled": True,
        "available": False,
        "embedded_signup": False,
        "public_address_missing": False,
    }


async def test_on_with_the_flag_and_embedded_signup_needs_the_config_id() -> None:
    assert await _info("tk_a", ready=True) == {
        "enabled": True,
        "available": True,
        "embedded_signup": True,
        "public_address_missing": False,
    }
    info = await _info("tk_a", ready=True, whatsapp_config_id="")
    assert info["available"] is True and info["embedded_signup"] is False


async def test_the_admin_principal_sees_available_when_the_engine_is_on() -> None:
    info = await _info("admin", ready=True, plan_allows=False)
    assert info["available"] is True


# ------------------------------------------------------- plan_allows itself


async def test_plan_allows_reads_the_flag_and_the_internal_plan_always_may() -> None:
    def session_with(plan: object) -> MagicMock:
        result = MagicMock()
        result.scalar_one_or_none.return_value = plan
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        return session

    tenant = uuid.uuid4()
    off = SimpleNamespace(is_internal=False, channels={})
    on = SimpleNamespace(is_internal=False, channels={"whatsapp": True})
    internal = SimpleNamespace(is_internal=True, channels={})
    assert await availability.plan_allows(session_with(off), tenant) is False
    assert await availability.plan_allows(session_with(on), tenant) is True
    assert await availability.plan_allows(session_with(internal), tenant) is True
    assert await availability.plan_allows(session_with(None), tenant) is False
