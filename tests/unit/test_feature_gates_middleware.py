"""The session branch of APIKeyAuthMiddleware applies a business's plan features (BIZ3).

Real middleware over the in-memory database: the plan's flags arrive with the session
lookup (one SELECT), gated routes are refused before routing, the operator's workspace is
never gated.
"""

from typing import Any

import pytest
from fastapi import Request

from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from tests.unit import accounts_fakes
from tests.unit.test_auth_middleware_bearer import World, bearer, get


@pytest.fixture
def world() -> World:
    return World()


def _with_plan(world: World, tenant: Tenant, features: dict[str, Any]) -> None:
    from callobuzz_rag.db.models.plan import Plan

    plan = world.db.add(Plan(name=f"Plan for {tenant.name}", dashboard_features=features))
    tenant.plan_id = plan.id


def _app(world: World) -> Any:
    app = world.app()

    @app.get("/api/v1/gates")
    async def gates(request: Request) -> dict[str, Any]:
        state = request.state
        return {
            "hidden": sorted(state.hidden_gates),
            "dashboard": state.dashboard_features.model_dump()
            if state.dashboard_features is not None
            else None,
        }

    return app


async def test_hidden_route_is_refused_before_routing(world: World) -> None:
    _with_plan(world, world.tenant_a, {"webhooks": False})
    token = world.token(world.owner, world.tenant_a)
    res = await get(_app(world), "/api/v1/webhooks", **bearer(token))
    assert res.status_code == 403
    assert res.json()["code"] == "feature_not_in_plan"
    assert res.json()["feature"] == "webhooks"


async def test_a_plan_that_shows_it_lets_the_request_through(world: World) -> None:
    token = world.token(world.owner, world.tenant_a)
    res = await get(_app(world), "/api/v1/webhooks", **bearer(token))
    assert res.status_code == 404  # no such route in this tiny app: not refused by the gate


async def test_state_carries_hidden_gates_and_flags(world: World) -> None:
    _with_plan(world, world.tenant_a, {"models": False, "widget_code_only": True})
    token = world.token(world.owner, world.tenant_a)
    res = await get(_app(world), "/api/v1/gates", **bearer(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["hidden"] == ["models", "widget_settings"]
    assert body["dashboard"]["models"] is False and body["dashboard"]["advanced"] is True


async def test_the_operator_workspace_is_never_gated(world: World) -> None:
    operator = world.db.add(
        Tenant(name="Operator", status="active", api_key_hash="", is_operator=True)
    )
    world.db.add(TenantMember(tenant_id=operator.id, user_id=world.sa.id, role="owner"))
    _with_plan(world, operator, {"advanced": False, "webhooks": False})
    token = world.token(world.sa, operator)
    res = await get(_app(world), "/api/v1/gates", **bearer(token))
    assert res.status_code == 200, res.text
    assert res.json() == {"hidden": [], "dashboard": None}
    refused = await get(_app(world), "/api/v1/webhooks", **bearer(token))
    assert refused.status_code == 404  # reaches routing


async def test_api_keys_and_admin_have_no_gates(world: World) -> None:
    res = await get(_app(world), "/api/v1/gates", **{"X-API-Key": "admin-key-for-bearer-tests"})
    assert res.json() == {"hidden": [], "dashboard": None}


async def test_session_lookup_is_still_one_query(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_plan(world, world.tenant_a, {"quality": False})
    token = world.token(world.owner, world.tenant_a)
    statements: list[Any] = []
    original = accounts_fakes.FakeSession.execute

    async def counting(self: Any, stmt: Any, params: Any = None) -> Any:
        statements.append(stmt)
        return await original(self, stmt, params)

    monkeypatch.setattr(accounts_fakes.FakeSession, "execute", counting)
    res = await get(_app(world), "/api/v1/gates", **bearer(token))
    assert res.status_code == 200, res.text
    selects = [s for s in statements if getattr(s, "__visit_name__", "") == "select"]
    assert len(selects) == 1, [str(s)[:80] for s in selects]
