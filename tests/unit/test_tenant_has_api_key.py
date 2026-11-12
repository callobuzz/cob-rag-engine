"""`has_api_key` on tenant responses (accounts A8, for the dashboard's Create vs Rotate):
true once the tenant has a `tk_` key, never the key or its hash."""

from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.api.routes import tenants as routes
from callobuzz_rag.api.schemas.tenant import AdminTenantResponse, TenantResponse
from callobuzz_rag.db.models.api_key import ApiKey
from tests.unit.test_auth_routes import ADMIN, World, req
from tests.unit.test_tenant_lifecycle import admin_app


def _row(tenant: Any, key_count: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        Tenant=tenant,
        corpus_count=0,
        key_count=key_count,
        plan_name="Standard",
        plan_max_kbs=None,
        limit_overrides=None,
        grant_max_kbs=0,
        grant_questions=0,
    )


@pytest.mark.parametrize(("key_count", "expected"), [(0, False), (1, True), (3, True)])
def test_tenant_fields_say_whether_a_key_exists(key_count: int, expected: bool) -> None:
    world = World()
    fields = routes._tenant_fields(_row(world.shree, key_count))
    assert fields["has_api_key"] is expected
    body = TenantResponse(**fields).model_dump_json()
    assert "api_key_hash" not in body and "$2b$" not in body


def test_the_field_is_on_both_responses_and_required() -> None:
    for model in (TenantResponse, AdminTenantResponse):
        assert model.model_fields["has_api_key"].is_required()
    assert "api_key_hash" not in TenantResponse.model_fields


async def test_rotating_issues_the_first_key() -> None:
    """A sign-up's tenant has no key; the admin's rotate gives it one."""
    world = World()
    with admin_app(world) as (app, _):
        before = await req(app, "GET", f"/api/v1/admin/tenants/{world.shree.id}", key=ADMIN)
        rotated = await req(
            app, "POST", f"/api/v1/admin/tenants/{world.shree.id}/rotate-key", key=ADMIN
        )
        after = await req(app, "GET", f"/api/v1/admin/tenants/{world.shree.id}", key=ADMIN)
    assert before.status_code == 200, before.text
    assert before.json()["has_api_key"] is False
    assert rotated.status_code == 200, rotated.text
    assert after.json()["has_api_key"] is True
    assert "api_key_hash" not in after.text
    (key,) = [k for k in world.db.all(ApiKey) if k.tenant_id == world.shree.id]
    assert key.key_hash and key.key_hash not in after.text
    assert (key.name, key.permission, key.is_default) == ("Default key", "full", True)
