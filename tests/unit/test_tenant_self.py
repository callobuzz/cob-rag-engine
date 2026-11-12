"""GET /api/v1/tenants/me and POST /api/v1/tenants/me/rotate-key (G-3).

A tenant key could not read its own record or rotate its own key: every tenant
route needed the admin key.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.core.secrets import hash_api_key, verify_api_key
from tests.unit.scope_harness import call, scoped_app


def _row(tenant: SimpleNamespace, corpus_count: int) -> MagicMock:
    result = MagicMock()
    result.one_or_none.return_value = SimpleNamespace(
        Tenant=tenant,
        corpus_count=corpus_count,
        key_count=1,
        plan_name="Starter",
        plan_max_kbs=3,
        limit_overrides=None,
        grant_max_kbs=0,
        grant_questions=0,
    )
    return result


async def test_tenant_reads_its_own_record() -> None:
    with scoped_app() as (app, world):
        world.session.execute = AsyncMock(return_value=_row(world.tenant_a, 2))
        res = await call(app, "tk_a", "GET", "/api/v1/tenants/me")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == str(world.tenant_a.id)
    assert body["name"] == "Tenant A"
    assert body["corpus_count"] == 2
    assert body["max_corpora"] == 10
    # accounts A6: the plan, and the override winning over the plan's limit of 3
    assert body["plan_id"] == str(world.tenant_a.plan_id)
    assert body["plan_name"] == "Starter"
    assert body["effective_max_corpora"] == 10
    assert body["is_active"] is True
    assert body["status"] == "active"  # accounts A4
    assert "activation_note" not in body and "owner_emails" not in body  # admin-only fields
    # The query asked for tenant A, never another.
    stmt = world.session.execute.await_args.args[0]
    assert str(world.tenant_a.id).replace("-", "") in str(
        stmt.compile(compile_kwargs={"literal_binds": True})
    ).replace("-", "")


@pytest.mark.parametrize(("key", "status"), [("admin", 403), ("ck_a", 403), (None, 401)])
@pytest.mark.parametrize(("method", "path"), [("GET", "/me"), ("POST", "/me/rotate-key")])
async def test_only_tenant_keys(key, status, method, path) -> None:
    with scoped_app() as (app, _):
        res = await call(app, key, method, f"/api/v1/tenants{path}")
    assert res.status_code == status, res.text


async def test_rotate_issues_the_default_key_once() -> None:
    """The tenant has no default key row in this stand-in database, so rotating issues one:
    a full-permission "Default key" whose hash verifies the key in the response."""
    from callobuzz_rag.db.models.api_key import ApiKey

    with scoped_app() as (app, world):
        res = await call(app, "tk_a", "POST", "/api/v1/tenants/me/rotate-key")
    assert res.status_code == 200, res.text
    new_key = res.json()["api_key"]
    assert new_key.startswith(f"tk_{str(world.tenant_a.id)[:8]}_")
    (row,) = [c.args[0] for c in world.session.add.call_args_list if isinstance(c.args[0], ApiKey)]
    assert (row.name, row.permission, row.is_default) == ("Default key", "full", True)
    assert row.tenant_id == world.tenant_a.id and row.corpus_id is None
    assert verify_api_key(new_key, row.key_hash)
    assert row.key_hash not in res.text and new_key not in row.key_prefix
    world.session.commit.assert_awaited()


def _starter(world: SimpleNamespace) -> SimpleNamespace:
    """Tenant A's plan, as `GET /tenants/me/plan` loads it (accounts A6)."""
    from callobuzz_rag.db.models.plan import Plan
    from tests.unit.scope_harness import NOW

    plan = SimpleNamespace(
        id=world.tenant_a.plan_id,
        name="Starter",
        description=None,
        max_knowledge_bases=3,
        monthly_questions=1000,
        max_documents=None,
        max_widget_keys=2,
        max_upload_mb=None,
        max_document_tokens=None,
        is_default=False,
        is_active=True,
        is_builtin=False,
        is_internal=False,
        created_at=NOW,
        updated_at=NOW,
    )
    world.add_row(Plan, plan)
    return plan


@pytest.mark.parametrize("key", ["tk_a", "owner_a", "member_a"])
async def test_every_tenant_principal_reads_its_plan_and_usage(key: str) -> None:
    from unittest.mock import patch

    from callobuzz_rag.accounts import caps

    with scoped_app() as (app, world):
        _starter(world)
        limits = caps.Limits(max_knowledge_bases=10, monthly_questions=1000, max_widget_keys=2)
        usage = caps.Usage(
            month=caps.current_month(),
            questions=412,
            knowledge_bases=1,
            documents=37,
            widget_keys=1,
        )
        with (
            patch.object(caps, "effective_limits", AsyncMock(return_value=limits)) as lim,
            patch.object(caps, "usage_snapshot", AsyncMock(return_value=usage)) as use,
        ):
            res = await call(app, key, "GET", "/api/v1/tenants/me/plan")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["plan"]["name"] == "Starter" and "tenant_count" not in body["plan"]
    assert body["limits"] == {
        "max_knowledge_bases": 10,  # effective: tenant A's override of 10, not the plan's 3
        "monthly_questions": 1000,
        "max_documents": None,
        "max_widget_keys": 2,
        "max_upload_mb": None,
        "max_document_tokens": None,
    }
    assert body["usage"]["questions"] == 412 and body["usage"]["documents"] == 37
    assert body["usage"]["month"] == caps.current_month().strftime("%Y-%m")
    assert body["resets_at"].startswith(caps.next_month_start().strftime("%Y-%m-%dT00:00:00"))
    assert lim.await_args.args[1] == world.tenant_a.id  # its own tenant, never another
    assert use.await_args.args[1] == world.tenant_a.id


@pytest.mark.parametrize(("key", "status"), [("admin", 403), ("ck_a", 403), (None, 401)])
async def test_own_plan_is_for_tenant_principals(key: str | None, status: int) -> None:
    with scoped_app() as (app, _):
        res = await call(app, key, "GET", "/api/v1/tenants/me/plan")
    assert res.status_code == status, res.text


async def test_rotation_ends_a_cached_verification() -> None:
    """The middleware caches a verified key; rotating must still end it at once."""
    import uuid

    from callobuzz_rag.api.auth import APIKeyAuthMiddleware

    tenant = SimpleNamespace(id=uuid.uuid4(), is_active=True, status="active")
    old_key = f"tk_{str(tenant.id)[:8]}_old"
    key_row = SimpleNamespace(
        id=uuid.uuid4(),
        key_hash=hash_api_key(old_key),
        name="Default key",
        permission="full",
        lookup=None,
    )

    session = AsyncMock()
    session.get = AsyncMock(return_value=tenant)
    result = MagicMock()
    result.first.return_value = (key_row, tenant)
    result.all.return_value = [(key_row, tenant)]
    session.execute = AsyncMock(return_value=result)
    session.expunge = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    request = MagicMock()
    request.app.state.session_factory = MagicMock(return_value=ctx)

    mw = APIKeyAuthMiddleware(app=MagicMock())
    assert await mw._find_tenant_by_key(request, old_key) is tenant  # verified, now cached

    new_key = f"tk_{str(tenant.id)[:8]}_new"
    key_row.key_hash = hash_api_key(new_key)  # what rotate-key does

    assert await mw._find_tenant_by_key(request, old_key) is None
    assert await mw._find_tenant_by_key(request, new_key) is tenant


# BIZ3 plan
@pytest.mark.parametrize("key", ["owner_a", "member_a"])
async def test_own_plan_shows_the_website_and_dashboard_settings(key: str) -> None:
    from unittest.mock import patch

    from callobuzz_rag.accounts import caps

    with scoped_app() as (app, world):
        plan = _starter(world)
        plan.website_learning_enabled = True
        plan.website_pages_max = 100
        plan.dashboard_features = {"advanced": False}
        usage = caps.Usage(
            month=caps.current_month(), questions=0, knowledge_bases=1, documents=0, widget_keys=1
        )
        with (
            patch.object(caps, "effective_limits", AsyncMock(return_value=caps.Limits())),
            patch.object(caps, "usage_snapshot", AsyncMock(return_value=usage)),
        ):
            res = await call(app, key, "GET", "/api/v1/tenants/me/plan")
    assert res.status_code == 200, res.text
    plan_body = res.json()["plan"]
    assert plan_body["website_pages_max"] == 100 and plan_body["website_learning_enabled"] is True
    assert plan_body["dashboard_features"]["advanced"] is False
    assert plan_body["dashboard_features"]["models"] is True


# /BIZ3 plan
