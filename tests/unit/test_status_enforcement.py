"""A pending or suspended tenant is locked out everywhere at once (accounts A4).

The real middleware on the in-memory database: `tk_` and `ck_` keys get 403
`workspace_not_active` with the status on every route; a signed-in user still reaches
the status page's routes (`/whoami`, `/tenants/me`, `/auth/*`) and nothing else;
reactivation works on the very next request (no cache to wait for). The widget's polite
refusal is in `test_widget.py`.
"""

from importlib import resources
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.core.secrets import hash_api_key
from callobuzz_rag.db.models.api_key import ApiKey
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant
from tests.unit.test_auth_routes import World, auth_app, login, req

STATUSES = ["pending", "suspended"]


@pytest.fixture
def world() -> World:
    w = World()
    w.tk = f"tk_{str(w.shree.id)[:8]}_secret"  # type: ignore[attr-defined]
    w.db.add(
        ApiKey(
            tenant_id=w.shree.id,
            id_short=str(w.shree.id)[:8],
            name="Default key",
            key_prefix="tk_...",
            key_hash=hash_api_key(w.tk),  # type: ignore[attr-defined]
            permission="full",
            is_default=True,
        )
    )
    corpus = w.db.add(
        Corpus(
            name="kb",
            tenant_id=w.shree.id,
            qdrant_collection="corpus_abcd1234",
            api_key_hash="",
        )
    )
    w.ck = "ck_abcd1234_secret"  # type: ignore[attr-defined]
    w.db.add(
        ApiKey(
            corpus_id=corpus.id,
            id_short="abcd1234",
            name="Default key",
            key_prefix="ck_...",
            key_hash=hash_api_key(w.ck),  # type: ignore[attr-defined]
            permission="full",
            is_default=True,
        )
    )
    return w


def refused(res: Any, status: str) -> None:
    assert res.status_code == 403, res.text
    assert res.json() == {
        "detail": "This workspace is not active",
        "code": "workspace_not_active",
        "status": status,
    }


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize("which", ["tk", "ck"])
async def test_keys_are_refused_everywhere_then_restored(
    world: World, status: str, which: str
) -> None:
    key = getattr(world, which)
    with auth_app(world) as app:
        before = await req(app, "GET", "/api/v1/whoami", key=key)
        world.shree.status = status
        on_whoami = await req(app, "GET", "/api/v1/whoami", key=key)
        on_data = await req(app, "GET", "/api/v1/corpora", key=key)
        world.shree.status = "active"
        after = await req(app, "GET", "/api/v1/whoami", key=key)
    assert before.status_code == 200, before.text
    refused(on_whoami, status)  # keys have no status-page allow-list
    refused(on_data, status)
    assert after.status_code == 200  # the next request, no cache wait


async def test_a_wrong_key_of_an_inactive_tenant_is_still_401(world: World) -> None:
    world.shree.status = "suspended"
    with auth_app(world) as app:
        res = await req(app, "GET", "/api/v1/whoami", key=world.tk + "x")  # type: ignore[attr-defined]
    assert res.status_code == 401 and res.json()["code"] == "invalid_api_key"


@pytest.mark.parametrize("status", STATUSES)
async def test_sessions_see_only_the_status_page(world: World, status: str) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree)
    with auth_app(world) as app:
        token = (await login(app, asha.email)).json()["access_token"]
        world.shree.status = status
        data = await req(app, "GET", "/api/v1/corpora", token=token)
        rotate = await req(app, "POST", "/api/v1/tenants/me/rotate-key", token=token)
        whoami = await req(app, "GET", "/api/v1/whoami", token=token)
        me = await req(app, "GET", "/api/v1/auth/me", token=token)
        sessions = await req(app, "GET", "/api/v1/auth/sessions", token=token)
        world.shree.status = "active"
        rotated = await req(app, "POST", "/api/v1/tenants/me/rotate-key", token=token)
    refused(data, status)
    refused(rotate, status)
    assert whoami.status_code == 200 and whoami.json()["kind"] == "tenant"
    assert me.status_code == 200 and me.json()["tenant"]["status"] == status
    assert sessions.status_code == 200
    assert rotated.status_code == 200  # restored at the next request


@pytest.mark.parametrize("status", STATUSES)
async def test_session_reads_its_own_tenant_record(world: World, status: str) -> None:
    """GET /tenants/me is on the allow-list. (Its corpus-count subquery is not something
    the fake database answers, so the record is built from the tenant row here.)"""
    from callobuzz_rag.api.routes import tenants as routes
    from callobuzz_rag.api.schemas.tenant import TenantResponse

    async def plain(session: Any, tenant_id: Any) -> TenantResponse:
        t = await session.get(Tenant, tenant_id)
        row = SimpleNamespace(
            Tenant=t,
            corpus_count=0,
            key_count=1,
            plan_name="Standard",
            plan_max_kbs=None,
            limit_overrides=None,
            grant_max_kbs=0,
            grant_questions=0,
        )
        return TenantResponse(**routes._tenant_fields(row))

    asha = world.user("asha@shree.example")
    world.member(asha, world.shree)
    with auth_app(world) as app, patch.object(routes, "_tenant_response", plain):
        token = (await login(app, asha.email)).json()["access_token"]
        world.shree.status = status
        res = await req(app, "GET", "/api/v1/tenants/me", token=token)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == status and res.json()["is_active"] is False
    assert "activation_note" not in res.json() and "owner_emails" not in res.json()


def test_widget_js_shows_the_polite_codes_as_a_bot_reply() -> None:
    script = resources.files("callobuzz_rag.widget").joinpath("static/widget.js").read_text("utf-8")
    assert (
        'var POLITE_CODES = ["workspace_not_active", "question_cap_reached", '
        '"handoff_rate_limited", "demo_limit", "demo_unavailable"];' in script
    )
    assert 'if (err.polite) bubble("bot", esc(err.message));' in script
