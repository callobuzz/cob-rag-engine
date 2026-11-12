"""Scoping of the trust and value routes (TV2, TV3, TV4 each in its own block).

Every principal of the shared world (`scope_harness.py`) calls them: workspace routes answer for
the caller's own workspace only (they take no workspace id, so another one cannot be named),
the operator and knowledge-base keys have no workspace, and `/admin/...` routes are for the
admin principal alone.
"""

from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts.settings_store import EngineSettings
from callobuzz_rag.business.value import ValueInputs
from callobuzz_rag.business.value_report import WorkspaceContext
from tests.unit.scope_harness import KEYS, call, scoped_app

# TV4 value
_EMPTY = ValueInputs(0, 0, 0, 0, 0, 0, 0, 0, 0, None, [], [])
_CTX = WorkspaceContext("clinic", "Asia/Kolkata", "INR")
_WORKSPACE_KEYS = {"tk_a", "tk_b", "owner_a", "member_a"}


def _patches() -> list:
    return [
        patch(
            "callobuzz_rag.business.value_report.workspace_context", AsyncMock(return_value=_CTX)
        ),
        patch("callobuzz_rag.business.value_report.fetch_inputs", AsyncMock(return_value=_EMPTY)),
        patch(
            "callobuzz_rag.business.value_report.after_hours_since", AsyncMock(return_value=None)
        ),
        patch(
            "callobuzz_rag.accounts.settings_store.read_engine_settings_row",
            AsyncMock(return_value=(EngineSettings(), None)),
        ),
    ]


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("path", ["/api/v1/value/settings", "/api/v1/value/report"])
async def test_value_reads_belong_to_a_workspace(key: str, path: str) -> None:
    with scoped_app() as (app, _world):
        ps = _patches()
        for p in ps:
            p.start()
        try:
            res = await call(app, key, "GET", path)
        finally:
            for p in ps:
                p.stop()
    if key in _WORKSPACE_KEYS:
        assert res.status_code == 200, res.text
    elif key in ("ck_a", "ck_b"):
        assert res.status_code == 403 and res.json()["code"] == "scope_forbidden", res.text
    else:  # the operator: no workspace
        assert res.status_code == 403 and res.json()["code"] == "no_workspace", res.text


@pytest.mark.parametrize("key", KEYS)
async def test_the_deployers_view_is_admin_only(key: str) -> None:
    with scoped_app() as (app, world):
        ps = _patches()
        for p in ps:
            p.start()
        try:
            res = await call(
                app, key, "GET", f"/api/v1/admin/tenants/{world.tenant_a.id}/value-report"
            )
        finally:
            for p in ps:
                p.stop()
    if key in ("admin", "sa"):
        assert res.status_code == 200, res.text
    else:
        assert res.status_code == 403, res.text


# /TV4 value


# TV2 reference
@pytest.mark.parametrize("key", KEYS)
async def test_the_reference_lookup_searches_only_what_the_caller_may_read(key: str) -> None:
    with scoped_app() as (app, world):
        find = AsyncMock(return_value=[])
        with patch("callobuzz_rag.api.routes.handoffs.find_matches", find):
            res = await call(app, key, "GET", "/api/v1/handoffs/lookup", params={"ref": "SD-4F7K"})
    assert res.status_code == 404 and res.json()["code"] == "reference_not_found", res.text
    searched = find.await_args.args[2]
    if key in ("admin", "sa"):
        assert searched is None
    elif key in ("ck_a", "ck_b"):
        own = world.corpus_a if key == "ck_a" else world.corpus_b
        assert list(searched) == [own.id]
    else:  # a workspace: its own knowledge bases and never the other workspace's
        other = world.corpus_b if key.endswith("a") else world.corpus_a
        assert other.id not in set(searched)


# /TV2 reference


# TV3 spend cap
@pytest.mark.parametrize("key", KEYS)
async def test_the_spend_cap_belongs_to_a_workspace_and_to_its_owner(key: str) -> None:
    from tests.unit.test_scope_billing_routes import billing_world

    with (
        billing_world() as (app, _world),
        patch("callobuzz_rag.billing.spend_cap.set_cap", AsyncMock(return_value=None)),
        patch(
            "callobuzz_rag.accounts.settings_store.read_engine_settings_row",
            AsyncMock(return_value=(EngineSettings(), None)),
        ),
    ):
        res = await call(app, key, "PUT", "/api/v1/billing/spend-cap", json={"cap_minor": 100})
    if key in ("tk_a", "tk_b", "owner_a"):
        assert res.status_code == 200, res.text
        assert res.json()["cap_minor"] is None  # nothing was stored: set_cap is stubbed
    elif key == "member_a":
        assert res.status_code == 403 and res.json()["code"] == "owner_required", res.text
    elif key in ("ck_a", "ck_b"):
        assert res.status_code == 403 and res.json()["code"] == "scope_forbidden", res.text
    else:  # the operator: no workspace
        assert res.status_code == 403 and res.json()["code"] == "no_workspace", res.text


# /TV3 spend cap


# TV3 support
_SUPPORT_TENANT_ROUTES = [
    ("GET", "/api/v1/support/info", None),
    ("GET", "/api/v1/support/requests", None),
    ("POST", "/api/v1/support/requests", {"subject": "Widget has no button", "message": "Hi"}),
]
_SUPPORT_ADMIN_ROUTES = [
    ("GET", "/api/v1/admin/support/requests", None),
    ("GET", "/api/v1/admin/support/stats", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _SUPPORT_TENANT_ROUTES)
@pytest.mark.parametrize("key", KEYS)
async def test_support_requests_belong_to_a_workspace(
    key: str, method: str, path: str, body: dict | None
) -> None:
    async def number(session, **_):  # type: ignore[no-untyped-def]
        return 7

    with (
        scoped_app() as (app, _world),
        patch("callobuzz_rag.support.service.next_number", number),
        patch(
            "callobuzz_rag.api.routes.support.read_engine_settings_row",
            AsyncMock(return_value=(EngineSettings(), None)),
        ),
        patch("callobuzz_rag.support.service.create_request", AsyncMock(side_effect=_Stop)),
        patch("callobuzz_rag.support.service.list_requests", AsyncMock(return_value=([], 0))),
        patch("callobuzz_rag.support.service.open_count", AsyncMock(return_value=0)),
    ):
        try:
            res = await call(app, key, method, path, json=body)
        except _Stop:
            return  # a workspace principal got as far as creating the request
    if key in _WORKSPACE_KEYS:
        assert res.status_code == 200, res.text
    elif key in ("ck_a", "ck_b"):
        assert res.status_code == 403 and res.json()["code"] == "scope_forbidden", res.text
    else:  # the operator: no workspace
        assert res.status_code == 403 and res.json()["code"] == "no_workspace", res.text


class _Stop(Exception):  # noqa: N818
    """Raised by the stubbed `create_request`: scoping let the call through."""


@pytest.mark.parametrize(("method", "path", "body"), _SUPPORT_ADMIN_ROUTES)
@pytest.mark.parametrize("key", KEYS)
async def test_the_support_inbox_is_for_the_admin_alone(
    key: str, method: str, path: str, body: dict | None
) -> None:
    with (
        scoped_app() as (app, _world),
        patch("callobuzz_rag.support.service.list_requests", AsyncMock(return_value=([], 0))),
        patch(
            "callobuzz_rag.api.routes.support_admin.read_engine_settings_row",
            AsyncMock(return_value=(EngineSettings(), None)),
        ),
    ):
        res = await call(app, key, method, path, json=body)
    if key in ("admin", "sa"):
        assert res.status_code == 200, res.text
    else:
        assert res.status_code == 403, res.text


# /TV3 support
