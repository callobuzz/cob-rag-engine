"""Plans CRUD, the single-default rule, and a tenant's plan (accounts A6).

Through the real app and middleware on the in-memory database of `accounts_fakes.py`.
The per-plan tenant count is a GROUP BY, which the fake cannot evaluate, so it is
computed from the fake's rows here; the integration test runs it on PostgreSQL.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts import plans
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from tests.unit.test_auth_routes import ADMIN, World, login, req
from tests.unit.test_tenant_lifecycle import admin_app


@pytest.fixture
def world() -> World:
    return World()


@contextmanager
def app_for(world: World) -> Iterator[Any]:
    async def counts(session: Any, plan_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        out: dict[uuid.UUID, int] = {}
        for t in world.db.all(Tenant):
            if t.plan_id in plan_ids:
                out[t.plan_id] = out.get(t.plan_id, 0) + 1
        return out

    with admin_app(world) as (app, _), patch.object(plans, "tenant_counts", counts):
        yield app


async def api(app: Any, method: str, path: str = "", **kw: Any) -> Any:
    return await req(app, method, f"/api/v1/admin/plans{path}", key=ADMIN, **kw)


def plan_named(world: World, name: str) -> Plan:
    return next(p for p in world.db.all(Plan) if p.name == name)


def defaults(world: World) -> list[str]:
    return [p.name for p in world.db.all(Plan) if p.is_default]


def add_plan(world: World, name: str, **fields: Any) -> Plan:
    return world.db.add(Plan(name=name, is_default=False, is_active=True, **fields))


# -------------------------------------------------------------------- CRUD


async def test_create_list_read_and_audit(world: World) -> None:
    with app_for(world) as app:
        made = await api(
            app,
            "POST",
            json={"name": "Starter", "max_knowledge_bases": 1, "monthly_questions": 1000},
        )
        listed = await api(app, "GET")
        one = await api(app, "GET", f"/{made.json()['id']}")
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["name"] == "Starter"
    assert body["max_knowledge_bases"] == 1 and body["monthly_questions"] == 1000
    assert body["max_documents"] is None and body["max_widget_keys"] is None  # unlimited
    assert body["is_default"] is False and body["is_active"] is True
    assert body["tenant_count"] == 0
    assert listed.json()["count"] == 2
    standard = listed.json()["items"][0]  # the default first
    assert standard["name"] == "Standard" and standard["is_default"] is True
    assert standard["tenant_count"] == 2  # the world's two tenants
    assert one.json()["id"] == body["id"]
    (audit,) = world.audits("plan.created")
    assert audit.details["name"] == "Starter" and audit.details["monthly_questions"] == 1000
    assert audit.actor_kind == "admin_key"


async def test_only_the_seeded_plan_is_builtin_and_the_flag_is_read_only(world: World) -> None:
    """`is_builtin` (A8): the plan the engine seeded; the API never sets it."""
    standard = plan_named(world, "Standard")
    with app_for(world) as app:
        made = await api(app, "POST", json={"name": "Starter"})
        listed = await api(app, "GET")
        create_flagged = await api(app, "POST", json={"name": "Fake", "is_builtin": True})
        unflag = await api(app, "PATCH", f"/{standard.id}", json={"is_builtin": False})
        flag = await api(app, "PATCH", f"/{made.json()['id']}", json={"is_builtin": True})
    assert made.status_code == 201 and made.json()["is_builtin"] is False
    flags = {p["name"]: p["is_builtin"] for p in listed.json()["items"]}
    assert flags == {"Standard": True, "Starter": False}
    assert create_flagged.status_code == 422
    assert unflag.status_code == 422 and flag.status_code == 422
    assert standard.is_builtin is True
    assert [p.name for p in world.db.all(type(standard)) if p.is_builtin] == ["Standard"]


async def test_unknown_plan_is_404(world: World) -> None:
    with app_for(world) as app:
        res = await api(app, "GET", f"/{uuid.uuid4()}")
    assert res.status_code == 404
    assert res.json()["code"] == "plan_not_found"


async def test_duplicate_name_is_409(world: World) -> None:
    add_plan(world, "Pro")
    with app_for(world) as app:
        created = await api(app, "POST", json={"name": "Pro"})
        renamed = await api(
            app, "PATCH", f"/{plan_named(world, 'Standard').id}", json={"name": "Pro"}
        )
    assert created.status_code == 409 and created.json()["code"] == "plan_name_taken"
    assert renamed.status_code == 409 and renamed.json()["code"] == "plan_name_taken"


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "x", "monthly_questions": 0},
        {"name": "x", "max_documents": -1},
        {"name": "x", "allowed_models": ["gpt"]},  # not a field until it is enforced
    ],
)
async def test_create_validation(world: World, body: dict[str, Any]) -> None:
    with app_for(world) as app:
        res = await api(app, "POST", json=body)
    assert res.status_code == 422


async def test_update_sets_caps_and_null_means_unlimited(world: World) -> None:
    pro = add_plan(world, "Pro", monthly_questions=500, max_widget_keys=2)
    with app_for(world) as app:
        res = await api(
            app,
            "PATCH",
            f"/{pro.id}",
            json={"monthly_questions": None, "max_documents": 300, "description": "Mid tier"},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["monthly_questions"] is None
    assert body["max_documents"] == 300
    assert body["max_widget_keys"] == 2  # not sent: unchanged
    (audit,) = world.audits("plan.updated")
    assert audit.details["changes"]["monthly_questions"] == {"from": 500, "to": None}
    assert audit.details["changes"]["max_documents"] == {"from": None, "to": 300}
    assert "max_widget_keys" not in audit.details["changes"]


async def test_update_with_nothing_changed_writes_no_audit(world: World) -> None:
    pro = add_plan(world, "Pro", monthly_questions=500)
    with app_for(world) as app:
        res = await api(app, "PATCH", f"/{pro.id}", json={"monthly_questions": 500})
    assert res.status_code == 200
    assert world.audits("plan.updated") == []


# ------------------------------------------------------------ the default


async def test_a_new_default_unsets_the_old_one(world: World) -> None:
    with app_for(world) as app:
        res = await api(app, "POST", json={"name": "Starter", "is_default": True})
    assert res.status_code == 201
    assert defaults(world) == ["Starter"]


async def test_making_an_existing_plan_the_default(world: World) -> None:
    pro = add_plan(world, "Pro")
    with app_for(world) as app:
        res = await api(app, "PATCH", f"/{pro.id}", json={"is_default": True})
    assert res.status_code == 200 and res.json()["is_default"] is True
    assert defaults(world) == ["Pro"]


@pytest.mark.parametrize("body", [{"is_default": False}, {"is_active": False}])
async def test_the_default_cannot_be_undefaulted_or_deactivated(
    world: World, body: dict[str, Any]
) -> None:
    standard = plan_named(world, "Standard")
    with app_for(world) as app:
        res = await api(app, "PATCH", f"/{standard.id}", json=body)
    assert res.status_code == 409
    assert res.json()["code"] == "plan_is_default"
    assert defaults(world) == ["Standard"] and standard.is_active is True


async def test_an_inactive_plan_cannot_become_the_default(world: World) -> None:
    old = add_plan(world, "Legacy")
    old.is_active = False
    with app_for(world) as app:
        res = await api(app, "PATCH", f"/{old.id}", json={"is_default": True})
    assert res.status_code == 409 and res.json()["code"] == "plan_is_default"
    assert defaults(world) == ["Standard"]


# ---------------------------------------------------------------- delete


async def test_delete_the_default_is_refused(world: World) -> None:
    with app_for(world) as app:
        res = await api(app, "DELETE", f"/{plan_named(world, 'Standard').id}")
    assert res.status_code == 409 and res.json()["code"] == "plan_is_default"


async def test_delete_a_plan_in_use_is_refused(world: World) -> None:
    pro = add_plan(world, "Pro")
    world.shree.plan_id = pro.id
    with app_for(world) as app:
        res = await api(app, "DELETE", f"/{pro.id}")
    assert res.status_code == 409
    assert res.json()["code"] == "plan_in_use" and res.json()["tenant_count"] == 1
    assert pro in world.db.all(Plan)


async def test_delete_an_unused_plan(world: World) -> None:
    pro = add_plan(world, "Pro")
    with app_for(world) as app:
        res = await api(app, "DELETE", f"/{pro.id}")
    assert res.status_code == 204
    assert pro not in world.db.all(Plan)
    (audit,) = world.audits("plan.deleted")
    assert audit.details == {"name": "Pro"}


# ----------------------------------------------------------- tenant's plan


async def test_set_tenant_plan_and_audit(world: World) -> None:
    pro = add_plan(world, "Pro")
    standard = plan_named(world, "Standard")
    with app_for(world) as app:
        res = await req(
            app,
            "POST",
            f"/api/v1/admin/tenants/{world.shree.id}/plan",
            key=ADMIN,
            json={"plan_id": str(pro.id)},
        )
        again = await req(
            app,
            "POST",
            f"/api/v1/admin/tenants/{world.shree.id}/plan",
            key=ADMIN,
            json={"plan_id": str(pro.id)},
        )
    assert res.status_code == 200, res.text
    assert res.json()["plan_id"] == str(pro.id)
    assert again.status_code == 200
    assert world.shree.plan_id == pro.id
    (audit,) = world.audits("tenant.plan_changed")  # the repeat is a no-op
    assert audit.details == {
        "from": {"id": str(standard.id), "name": "Standard"},
        "to": {"id": str(pro.id), "name": "Pro"},
    }
    assert audit.tenant_id == world.shree.id


async def test_an_inactive_or_unknown_plan_is_not_assignable(world: World) -> None:
    old = add_plan(world, "Legacy")
    old.is_active = False
    before = world.shree.plan_id
    with app_for(world) as app:
        inactive = await req(
            app,
            "POST",
            f"/api/v1/admin/tenants/{world.shree.id}/plan",
            key=ADMIN,
            json={"plan_id": str(old.id)},
        )
        unknown = await req(
            app,
            "POST",
            f"/api/v1/admin/tenants/{world.shree.id}/plan",
            key=ADMIN,
            json={"plan_id": str(uuid.uuid4())},
        )
    assert inactive.status_code == 409 and inactive.json()["code"] == "plan_inactive"
    assert unknown.status_code == 404 and unknown.json()["code"] == "plan_not_found"
    assert world.shree.plan_id == before


async def test_tenants_on_a_deactivated_plan_keep_it(world: World) -> None:
    pro = add_plan(world, "Pro")
    world.shree.plan_id = pro.id
    with app_for(world) as app:
        res = await api(app, "PATCH", f"/{pro.id}", json={"is_active": False})
    assert res.status_code == 200 and res.json()["is_active"] is False
    assert world.shree.plan_id == pro.id


async def test_activate_on_a_plan_names_it_in_the_mail(world: World) -> None:
    pro = add_plan(world, "Pro")
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree)
    world.shree.status = "pending"
    with app_for(world) as app:
        res = await req(
            app,
            "POST",
            f"/api/v1/admin/tenants/{world.shree.id}/activate",
            key=ADMIN,
            json={"plan_id": str(pro.id), "note": "paid"},
        )
    assert res.status_code == 200, res.text
    assert world.shree.status == "active" and world.shree.plan_id == pro.id
    assert world.mail[-1][0] == "activated" and world.mail[-1][2]["plan_name"] == "Pro"
    assert len(world.audits("tenant.plan_changed")) == 1
    (activated,) = world.audits("tenant.activated")
    assert activated.details == {"plan_id": str(pro.id), "note": "paid"}


async def test_activate_on_an_inactive_plan_changes_nothing(world: World) -> None:
    old = add_plan(world, "Legacy")
    old.is_active = False
    world.shree.status = "pending"
    with app_for(world) as app:
        res = await req(
            app,
            "POST",
            f"/api/v1/admin/tenants/{world.shree.id}/activate",
            key=ADMIN,
            json={"plan_id": str(old.id)},
        )
    assert res.status_code == 409 and res.json()["code"] == "plan_inactive"
    assert world.shree.status == "pending"


async def test_create_tenant_on_the_default_or_a_named_plan(world: World) -> None:
    pro = add_plan(world, "Pro")
    with app_for(world) as app:
        default = await req(app, "POST", "/api/v1/admin/tenants", key=ADMIN, json={"name": "A"})
        named = await req(
            app,
            "POST",
            "/api/v1/admin/tenants",
            key=ADMIN,
            json={"name": "B", "plan_id": str(pro.id)},
        )
        override = await req(
            app,
            "POST",
            "/api/v1/admin/tenants",
            key=ADMIN,
            json={"name": "C", "max_corpora": 5000},
        )
    assert default.status_code == 201, default.text
    assert default.json()["plan_id"] == str(plan_named(world, "Standard").id)
    assert default.json()["max_corpora"] is None  # follows the plan
    assert named.json()["plan_id"] == str(pro.id)
    assert override.json()["max_corpora"] == 5000


async def test_update_tenant_null_max_corpora_follows_the_plan(world: World) -> None:
    world.shree.max_corpora = 10
    with app_for(world) as app:
        kept = await req(
            app,
            "PUT",
            f"/api/v1/admin/tenants/{world.shree.id}",
            key=ADMIN,
            json={"name": "Shree Labs"},
        )
        assert world.shree.max_corpora == 10  # not sent: unchanged
        cleared = await req(
            app,
            "PUT",
            f"/api/v1/admin/tenants/{world.shree.id}",
            key=ADMIN,
            json={"max_corpora": None},
        )
    assert kept.status_code == 200 and cleared.status_code == 200
    assert world.shree.max_corpora is None


# ------------------------------------------------------------------ access


async def test_plans_are_admin_only(world: World) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree)
    with app_for(world) as app:
        token = (await login(app, asha.email)).json()["access_token"]
        listed = await req(app, "GET", "/api/v1/admin/plans", token=token)
        made = await req(app, "POST", "/api/v1/admin/plans", token=token, json={"name": "X"})
        moved = await req(
            app,
            "POST",
            f"/api/v1/admin/tenants/{world.shree.id}/plan",
            token=token,
            json={"plan_id": str(plan_named(world, "Standard").id)},
        )
        anonymous = await req(app, "GET", "/api/v1/admin/plans")
    assert listed.status_code == 403 and made.status_code == 403 and moved.status_code == 403
    assert anonymous.status_code == 401
    assert [p.name for p in world.db.all(Plan)] == ["Standard"]


# ------------------------------------------------------- size caps (upload, text)


async def test_size_caps_round_trip_and_default_to_the_engines(world: World) -> None:
    with app_for(world) as app:
        plain = await api(app, "POST", json={"name": "Plain"})
        made = await api(
            app, "POST", json={"name": "Small", "max_upload_mb": 10, "max_document_tokens": 100000}
        )
        changed = await api(app, "PATCH", f"/{made.json()['id']}", json={"max_upload_mb": 5})
        cleared = await api(
            app, "PATCH", f"/{made.json()['id']}", json={"max_document_tokens": None}
        )
        bad = await api(app, "POST", json={"name": "Bad", "max_upload_mb": 0})
    assert plain.json()["max_upload_mb"] is None and plain.json()["max_document_tokens"] is None
    assert made.json()["max_upload_mb"] == 10 and made.json()["max_document_tokens"] == 100000
    assert changed.json()["max_upload_mb"] == 5 and changed.json()["max_document_tokens"] == 100000
    assert cleared.json()["max_document_tokens"] is None and cleared.json()["max_upload_mb"] == 5
    assert bad.status_code == 422
    created = [a for a in world.audits("plan.created") if a.details["name"] == "Small"]
    assert created[0].details["max_upload_mb"] == 10


async def test_count_cached_answers_defaults_on_and_round_trips(world: World) -> None:
    with app_for(world) as app:
        plain = await api(app, "POST", json={"name": "Plain"})
        free = await api(app, "POST", json={"name": "Free cache", "count_cached_answers": False})
        flipped = await api(
            app, "PATCH", f"/{plain.json()['id']}", json={"count_cached_answers": False}
        )
        unchanged = await api(
            app, "PATCH", f"/{plain.json()['id']}", json={"count_cached_answers": None}
        )
        bad = await api(app, "POST", json={"name": "Bad", "count_cached_answers": "sometimes"})
    assert plain.json()["count_cached_answers"] is True
    assert free.json()["count_cached_answers"] is False
    assert flipped.json()["count_cached_answers"] is False
    assert unchanged.json()["count_cached_answers"] is False  # null means unchanged
    assert bad.status_code == 422


# BIL2 currency
async def test_plan_priced_in_another_currency_than_a_locked_account_is_refused(
    world: World,
) -> None:
    from callobuzz_rag.billing.pricing import PricingFields
    from callobuzz_rag.db.models.billing_account import BillingAccount
    from callobuzz_rag.db.models.plan_pricing import PlanPricing

    usd = add_plan(world, "Dollar")
    inr = add_plan(world, "Rupee")
    nothing = add_plan(world, "Unpriced")
    for plan, currency in ((usd, "USD"), (inr, "INR")):
        values = PricingFields(currency=currency).model_dump()
        world.db.add(PlanPricing(plan_id=plan.id, version=1, is_current=True, **values))
    account = world.db.add(BillingAccount(tenant_id=world.shree.id, currency="INR", ledger_seq=2))

    async def move(app: Any, plan: Plan) -> Any:
        return await req(
            app,
            "POST",
            f"/api/v1/admin/tenants/{world.shree.id}/plan",
            key=ADMIN,
            json={"plan_id": str(plan.id)},
        )

    owed = AsyncMock(return_value=0)  # usage charged but not yet posted (micro units)
    with app_for(world) as app, patch("callobuzz_rag.billing.ledger.owed_micro", owed):
        refused = await move(app, usd)
        to_unpriced = await move(app, nothing)
        account.ledger_seq = 0  # nothing booked yet, but usage is waiting to be posted
        owed.return_value = 5_000_000
        waiting = await move(app, usd)
        owed.return_value = 0  # nothing booked and nothing waiting: the currency is free
        to_usd = await move(app, usd)
    assert waiting.status_code == 409 and waiting.json()["code"] == "currency_mismatch"
    assert refused.status_code == 409 and refused.json()["code"] == "currency_mismatch"
    assert refused.json()["account_currency"] == "INR" and refused.json()["currency"] == "USD"
    assert to_unpriced.status_code == 200
    assert to_usd.status_code == 200
    assert world.shree.plan_id == usd.id


# /BIL2 currency


# BIZ3 plan
async def test_plan_carries_website_and_dashboard_settings(world: World) -> None:
    with app_for(world) as app:
        made = await api(
            app,
            "POST",
            json={
                "name": "Simple",
                "website_pages_max": 50,
                "dashboard_features": {"advanced": False, "widget_code_only": True},
            },
        )
        plain = await api(app, "POST", json={"name": "Plain"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["website_learning_enabled"] is True and body["website_pages_max"] == 50
    flags = body["dashboard_features"]
    assert flags["advanced"] is False and flags["widget_code_only"] is True
    assert flags["models"] is True and flags["api_docs"] is True  # the full set, defaults filled
    assert len(flags) == 9
    assert plain.json()["website_pages_max"] is None
    assert plain.json()["dashboard_features"]["advanced"] is True
    assert plain.json()["dashboard_features"]["widget_code_only"] is False


async def test_a_plan_stored_with_no_flags_returns_every_default(world: World) -> None:
    with app_for(world) as app:
        listed = await api(app, "GET")
    standard = listed.json()["items"][0]
    assert standard["website_learning_enabled"] is True
    assert standard["dashboard_features"]["advanced"] is True
    assert standard["dashboard_features"]["widget_code_only"] is False


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "dashboard_features": {"bogus": True}},
        {"name": "x", "dashboard_features": {"models": "sometimes"}},
        {"name": "x", "website_pages_max": 0},
        {"name": "x", "website_learning_enabled": "sometimes"},
    ],
)
async def test_plan_settings_validation(world: World, body: dict[str, Any]) -> None:
    with app_for(world) as app:
        res = await api(app, "POST", json=body)
    assert res.status_code == 422, res.text


async def test_update_changes_the_new_fields_and_audits_their_names(world: World) -> None:
    plan = add_plan(world, "Growth")
    with app_for(world) as app:
        res = await api(
            app,
            "PATCH",
            f"/{plan.id}",
            json={
                "website_learning_enabled": False,
                "website_pages_max": 200,
                "dashboard_features": {"webhooks": False},
            },
        )
        again = await api(app, "PATCH", f"/{plan.id}", json={"website_pages_max": None})
        zero = await api(app, "PATCH", f"/{plan.id}", json={"website_pages_max": 0})
        keep = await api(
            app, "PATCH", f"/{plan.id}", json={"website_learning_enabled": None}
        )  # not nullable: unchanged
    assert res.status_code == 200, res.text
    assert res.json()["website_learning_enabled"] is False
    assert res.json()["dashboard_features"]["webhooks"] is False
    assert res.json()["dashboard_features"]["models"] is True
    assert again.json()["website_pages_max"] is None  # null = unlimited
    assert zero.status_code == 422
    assert keep.json()["website_learning_enabled"] is False
    changed = next(a for a in world.audits("plan.updated") if a.details["name"] == "Growth")
    assert set(changed.details["changes"]) == {
        "website_learning_enabled",
        "website_pages_max",
        "dashboard_features",
    }


async def test_resending_the_defaults_is_not_a_change(world: World) -> None:
    plan = add_plan(world, "Same")  # stored flags: {} = all defaults
    with app_for(world) as app:
        res = await api(
            app, "PATCH", f"/{plan.id}", json={"dashboard_features": {"advanced": True}}
        )
    assert res.status_code == 200, res.text
    assert not [a for a in world.audits("plan.updated") if a.details["name"] == "Same"]


async def test_the_internal_plan_stays_locked(world: World) -> None:
    internal = world.db.add(
        Plan(name="Internal", is_internal=True, is_active=True, is_default=False)
    )
    with app_for(world) as app:
        res = await api(
            app, "PATCH", f"/{internal.id}", json={"dashboard_features": {"advanced": False}}
        )
    assert res.status_code == 409, res.text


# /BIZ3 plan
