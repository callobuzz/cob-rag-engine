"""The super admin's billing API: plan pricing versions and customers' accounts (BIL2).

Through the real app and middleware on the in-memory database of `accounts_fakes.py`; the
integration tests run the same SQL on PostgreSQL.
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.billing import config as billing_config
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import ADMIN, World, auth_app, login, req

PRICING = "/api/v1/admin/billing/plans/{}/pricing"


@pytest.fixture
def world() -> World:
    return World()


@contextmanager
def app_for(world: World) -> Iterator[Any]:
    with auth_app(world) as app, fake_accounts(world.db):
        yield app


async def admin(app: Any, method: str, path: str, **kw: Any) -> Any:
    return await req(app, method, path, key=ADMIN, **kw)


def standard(world: World) -> Plan:
    return world.db.default_plan


def internal_plan(world: World) -> Plan:
    return world.db.add(Plan(name="Internal", is_internal=True, is_active=True, is_default=False))


# ---------------------------------------------------------------- pricing


async def test_plan_without_pricing_returns_defaults(world: World) -> None:
    with app_for(world) as app:
        res = await admin(app, "GET", PRICING.format(standard(world).id))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["version"] is None and body["is_current"] is False and body["locked"] is False
    assert body["usage_mode"] == "included" and body["subscription_fee_minor"] == 0
    assert body["byok_mode"] == "free" and body["overage"] == "block"
    assert body["alert_thresholds_pct"] == [20, 5, 0]
    assert body["currency"] == "INR"


async def test_put_creates_versions_and_history_is_newest_first(world: World) -> None:
    # a plan nobody is on: its first price starts at once (with businesses on it, TV3 makes it
    # a notice matter, see test_billing_notice_gaps.py)
    plan = world.db.add(Plan(name="Empty", is_active=True))
    with app_for(world) as app:
        first = await admin(
            app,
            "PUT",
            PRICING.format(plan.id),
            json={"usage_mode": "per_question", "per_question_minor": 200},
        )
        second = await admin(
            app,
            "PUT",
            PRICING.format(plan.id),
            json={"usage_mode": "per_question", "per_question_minor": 150, "currency": "INR"},
        )
        history = await admin(app, "GET", PRICING.format(plan.id) + "/history")
        current = await admin(app, "GET", PRICING.format(plan.id))
    assert first.status_code == 200, first.text
    assert (first.json()["version"], first.json()["is_current"]) == (1, True)
    assert set(first.json()["changed"]) == {"usage_mode", "per_question_minor"}
    assert (second.json()["version"], second.json()["changed"]) == (2, ["per_question_minor"])
    assert [i["version"] for i in history.json()["items"]] == [2, 1]
    assert [i["is_current"] for i in history.json()["items"]] == [True, False]
    assert current.json()["per_question_minor"] == 150
    rows = [r for r in world.db.all(PlanPricing) if r.plan_id == plan.id]
    assert sorted(r.version for r in rows) == [1, 2] and sum(r.is_current for r in rows) == 1
    audits = world.audits("billing.pricing_updated")
    assert [a.details["version"] for a in audits] == [1, 2]
    assert audits[1].details == {
        "plan_id": str(plan.id),
        "version": 2,
        "changed": ["per_question_minor"],
    }
    assert audits[0].actor_kind == "admin_key"


async def test_put_is_full_replacement_and_strict(world: World) -> None:
    # a plan nobody is on: a change starts at once whatever it costs (TV3)
    plan = world.db.add(Plan(name="Empty", is_active=True))
    with app_for(world) as app:
        await admin(app, "PUT", PRICING.format(plan.id), json={"subscription_fee_minor": 99900})
        replaced = await admin(app, "PUT", PRICING.format(plan.id), json={"markup_bp": 2500})
        unknown = await admin(app, "PUT", PRICING.format(plan.id), json={"surprise": 1})
        bad_money = await admin(
            app, "PUT", PRICING.format(plan.id), json={"per_question_minor": 1.5}
        )
        bad_thresholds = await admin(
            app, "PUT", PRICING.format(plan.id), json={"alert_thresholds_pct": [5, 20]}
        )
    assert replaced.json()["subscription_fee_minor"] == 0 and replaced.json()["markup_bp"] == 2500
    assert unknown.status_code == 422
    assert bad_money.status_code == 422 and bad_thresholds.status_code == 422


async def test_internal_plan_is_locked(world: World) -> None:
    plan = internal_plan(world)
    with app_for(world) as app:
        put = await admin(app, "PUT", PRICING.format(plan.id), json={})
        got = await admin(app, "GET", PRICING.format(plan.id))
    assert put.status_code == 409 and put.json()["code"] == "pricing_locked"
    assert got.status_code == 200 and got.json()["locked"] is True and got.json()["version"] is None
    assert world.audits("billing.pricing_updated") == []


async def test_unknown_plan_is_404(world: World) -> None:
    with app_for(world) as app:
        res = await admin(app, "GET", PRICING.format(uuid.uuid4()))
        put = await admin(app, "PUT", PRICING.format(uuid.uuid4()), json={})
    assert res.status_code == 404 and res.json()["code"] == "plan_not_found"
    assert put.status_code == 404


async def test_currency_change_with_a_locked_account_in_the_other_currency(world: World) -> None:
    plan = standard(world)
    world.db.add(stored_pricing(plan, currency="INR"))
    world.db.add(BillingAccount(tenant_id=world.shree.id, currency="INR", ledger_seq=3))
    with app_for(world) as app:
        res = await admin(app, "PUT", PRICING.format(plan.id), json={"currency": "USD"})
        same = await admin(app, "PUT", PRICING.format(plan.id), json={"currency": "INR"})
    assert res.status_code == 409 and res.json()["code"] == "currency_mismatch"
    assert res.json()["tenant_ids"] == [str(world.shree.id)]
    assert res.json()["account_currency"] == "INR" and res.json()["currency"] == "USD"
    assert same.status_code == 200


async def test_an_unlocked_account_does_not_block_a_currency_change(world: World) -> None:
    from datetime import UTC, datetime, timedelta

    from callobuzz_rag.billing.notice import activate_due

    plan = standard(world)
    world.db.add(stored_pricing(plan, currency="INR"))
    account = world.db.add(BillingAccount(tenant_id=world.shree.id, currency="INR", ledger_seq=0))
    with app_for(world) as app:
        res = await admin(
            app, "PUT", PRICING.format(plan.id), json={"currency": "USD", "apply": "scheduled"}
        )
    # a currency switch is announced like a raise; nothing changes until its day comes
    assert res.status_code == 200, res.text
    assert res.json()["currency"] == "INR" and res.json()["scheduled"]["currency"] == "USD"
    assert account.currency == "INR"
    later = datetime.now(UTC) + timedelta(days=120)
    # the stand-in database has no SUM(): the "nothing posted yet" answer is given directly
    with patch("callobuzz_rag.billing.notice.currency_frozen", AsyncMock(return_value=False)):
        assert await activate_due(world.db.factory(), later) == 1
    assert account.currency == "USD"


async def test_billing_disabled_answers_404(world: World) -> None:
    with patch.dict(os.environ, {"RAG_BILLING_ENABLED": "false"}):
        billing_config.reset_billing_config_cache()
        try:
            with app_for(world) as app:
                res = await admin(app, "GET", PRICING.format(standard(world).id))
        finally:
            billing_config.reset_billing_config_cache()
    assert res.status_code == 404 and res.json()["code"] == "billing_disabled"


async def test_only_the_admin_may_use_the_pricing_routes(world: World) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree, "owner")
    path = PRICING.format(standard(world).id)
    with app_for(world) as app:
        token = (await login(app, asha.email)).json()["access_token"]
        as_owner = await req(app, "GET", path, token=token)
        as_owner_put = await req(app, "PUT", path, token=token, json={})
        anonymous = await req(app, "GET", path)
        wrong_key = await req(app, "GET", path, key="not-the-key")
    assert as_owner.status_code == 403 and as_owner_put.status_code == 403
    assert anonymous.status_code in (401, 403) and wrong_key.status_code in (401, 403)
    assert world.audits("billing.pricing_updated") == []


# ---------------------------------------------------------------- accounts

ACCOUNTS = "/api/v1/admin/billing/accounts"


def account_of(world: World, tenant: Any) -> BillingAccount | None:
    return next((a for a in world.db.all(BillingAccount) if a.tenant_id == tenant.id), None)


async def test_get_creates_the_account_lazily_and_shows_effective_terms(world: World) -> None:
    plan = standard(world)
    world.db.add(
        stored_pricing(plan, currency="USD", usage_mode="per_question", per_question_minor=7)
    )
    assert account_of(world, world.shree) is None
    with app_for(world) as app:
        res = await admin(app, "GET", f"{ACCOUNTS}/{world.shree.id}")
        again = await admin(app, "GET", f"{ACCOUNTS}/{world.shree.id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tenant_name"] == "Shree Labs" and body["plan"]["name"] == plan.name
    assert body["currency"] == "USD" and body["pricing_version"] == 1 and body["exempt"] is False
    assert body["effective"]["per_question_minor"] == 7 and body["overridden"] == []
    assert body["overrides"] == {} and body["limit_overrides"] == {}
    assert body["pause_override"] == "auto" and body["billing_day"] == 1
    assert body["balance_minor"] == 0 and body["currency_locked"] is False
    assert len(world.db.all(BillingAccount)) == 1 and again.status_code == 200


async def test_unknown_tenant_is_404(world: World) -> None:
    with app_for(world) as app:
        get = await admin(app, "GET", f"{ACCOUNTS}/{uuid.uuid4()}")
        patch_ = await admin(app, "PATCH", f"{ACCOUNTS}/{uuid.uuid4()}", json={"notes": "x"})
    for res in (get, patch_):
        assert res.status_code == 404 and res.json()["code"] == "tenant_not_found"


async def test_patch_sets_and_clears_overrides(world: World) -> None:
    world.shree.status = "pending"  # a workspace not yet live: nothing to be told first (TV3)
    world.db.add(stored_pricing(standard(world), usage_mode="per_question", per_question_minor=200))
    url = f"{ACCOUNTS}/{world.shree.id}"
    with app_for(world) as app:
        first = await admin(
            app,
            "PATCH",
            url,
            json={"overrides": {"per_question_minor": 150, "payment_type": "postpaid"}},
        )
        second = await admin(app, "PATCH", url, json={"overrides": {"payment_type": None}})
        same = await admin(app, "PATCH", url, json={"overrides": {"per_question_minor": 150}})
    assert first.status_code == 200, first.text
    assert first.json()["overridden"] == ["payment_type", "per_question_minor"]
    assert first.json()["effective"]["per_question_minor"] == 150
    assert first.json()["effective"]["payment_type"] == "postpaid"
    assert second.json()["overrides"] == {"per_question_minor": 150}
    assert second.json()["effective"]["payment_type"] == "prepaid"
    assert same.status_code == 200
    audits = world.audits("billing.account_updated")
    assert len(audits) == 2  # the no-op PATCH wrote nothing
    assert set(audits[0].details["changed"]) == {
        "overrides.per_question_minor",
        "overrides.payment_type",
    }
    assert audits[1].details["changed"] == ["overrides.payment_type"]
    assert audits[0].tenant_id == world.shree.id


async def test_patch_refuses_bad_overrides_and_limits(world: World) -> None:
    url = f"{ACCOUNTS}/{world.shree.id}"
    bad_bodies = [
        {"overrides": {"currency": "USD"}},
        {"overrides": {"nonsense": 1}},
        {"overrides": {"per_question_minor": -5}},
        {"overrides": {"alert_thresholds_pct": [1, 5]}},
        {"limit_overrides": {"max_unicorns": 5}},
        {"limit_overrides": {"max_documents": 0}},
        {"billing_day": 29},
        {"pause_override": "sometimes"},
        {"surprise": 1},
    ]
    with app_for(world) as app:
        results = [await admin(app, "PATCH", url, json=b) for b in bad_bodies]
    assert [r.status_code for r in results] == [422] * len(bad_bodies), [r.text for r in results]
    assert world.audits("billing.account_updated") == []


async def test_patch_limit_overrides_pause_day_notes(world: World) -> None:
    url = f"{ACCOUNTS}/{world.shree.id}"
    with app_for(world) as app:
        res = await admin(
            app,
            "PATCH",
            url,
            json={
                "limit_overrides": {"max_documents": 500, "monthly_questions": None},
                "pause_override": "never",
                "billing_day": 15,
                "notes": "Pays by cheque",
            },
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["limit_overrides"] == {"max_documents": 500, "monthly_questions": None}
    assert (body["pause_override"], body["billing_day"], body["notes"]) == (
        "never",
        15,
        "Pays by cheque",
    )
    (audit,) = world.audits("billing.account_updated")
    assert audit.details["pause_override"] == "never"
    assert "Pays by cheque" not in str(audit.details)  # the note text is never audited
    assert set(audit.details["changed"]) == {
        "limit_overrides.max_documents",
        "limit_overrides.monthly_questions",
        "pause_override",
        "billing_day",
        "notes",
    }


async def test_currency_is_free_until_the_first_transaction(world: World) -> None:
    url = f"{ACCOUNTS}/{world.shree.id}"
    with app_for(world) as app:
        free = await admin(app, "PATCH", url, json={"currency": "USD"})
        account = account_of(world, world.shree)
        assert account is not None
        account.ledger_seq = 1
        locked = await admin(app, "PATCH", url, json={"currency": "INR"})
        unchanged = await admin(app, "PATCH", url, json={"currency": "USD"})
    assert free.status_code == 200 and free.json()["currency"] == "USD"
    assert locked.status_code == 409 and locked.json()["code"] == "currency_locked"
    assert unchanged.status_code == 200 and unchanged.json()["currency_locked"] is True


async def test_currency_is_locked_while_usage_waits_to_be_posted(world: World) -> None:
    url = f"{ACCOUNTS}/{world.shree.id}"
    with app_for(world) as app:
        await admin(app, "PATCH", url, json={"currency": "USD"})
        account = account_of(world, world.shree)
        assert account is not None
        account.usage_residue_micro = 400_000  # charged, not yet a whole minor unit in the ledger
        locked = await admin(app, "PATCH", url, json={"currency": "INR"})
    assert account.ledger_seq == 0
    assert locked.status_code == 409 and locked.json()["code"] == "currency_locked"


async def test_exempt_workspace_accepts_only_notes(world: World) -> None:
    world.shree.is_operator = True
    url = f"{ACCOUNTS}/{world.shree.id}"
    with app_for(world) as app:
        got = await admin(app, "GET", url)
        refused = await admin(app, "PATCH", url, json={"pause_override": "force"})
        mixed = await admin(app, "PATCH", url, json={"notes": "mine", "billing_day": 3})
        notes = await admin(app, "PATCH", url, json={"notes": "mine"})
    assert got.json()["exempt"] is True
    assert refused.status_code == 409 and refused.json()["code"] == "billing_exempt"
    assert mixed.status_code == 409
    assert notes.status_code == 200 and notes.json()["notes"] == "mine"


async def test_list_filters_pages_and_marks_the_operator(world: World) -> None:
    world.pune.is_operator = True
    world.shree.status = "pending"  # not yet live: an override applies at once (TV3)
    system = world.tenant("COB system")
    system.is_system = True
    world.db.add(stored_pricing(standard(world), payment_type="prepaid"))
    with app_for(world) as app:
        await admin(
            app,
            "PATCH",
            f"{ACCOUNTS}/{world.shree.id}",
            json={"overrides": {"payment_type": "postpaid"}},
        )
        everyone = await admin(app, "GET", ACCOUNTS)
        postpaid = await admin(app, "GET", ACCOUNTS, params={"payment_type": "postpaid"})
        by_name = await admin(app, "GET", ACCOUNTS, params={"q": "pune"})
        page = await admin(app, "GET", ACCOUNTS, params={"limit": 1, "offset": 1})
        too_big = await admin(app, "GET", ACCOUNTS, params={"limit": 101})
    names = [i["tenant_name"] for i in everyone.json()["items"]]
    assert names == ["Pune Agency", "Shree Labs"] and "COB system" not in names
    assert everyone.json()["total"] == 2
    by = {i["tenant_name"]: i for i in everyone.json()["items"]}
    assert by["Pune Agency"]["exempt"] is True and by["Pune Agency"]["has_account"] is False
    assert by["Shree Labs"]["payment_type"] == "postpaid"
    assert by["Shree Labs"]["overrides_count"] == 1
    assert [i["tenant_name"] for i in postpaid.json()["items"]] == ["Shree Labs"]
    assert postpaid.json()["total"] == 1
    assert [i["tenant_name"] for i in by_name.json()["items"]] == ["Pune Agency"]
    assert [i["tenant_name"] for i in page.json()["items"]] == ["Shree Labs"]
    assert page.json()["total"] == 2
    assert too_big.status_code == 422


async def test_account_routes_are_admin_only_and_respect_the_switch(world: World) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree, "owner")
    url = f"{ACCOUNTS}/{world.shree.id}"
    with app_for(world) as app:
        token = (await login(app, asha.email)).json()["access_token"]
        owner_get = await req(app, "GET", url, token=token)
        owner_patch = await req(app, "PATCH", url, token=token, json={"notes": "x"})
        owner_list = await req(app, "GET", ACCOUNTS, token=token)
    assert (owner_get.status_code, owner_patch.status_code, owner_list.status_code) == (403,) * 3
    assert account_of(world, world.shree) is None  # nothing was created for a refused caller
    with patch.dict(os.environ, {"RAG_BILLING_ENABLED": "false"}):
        billing_config.reset_billing_config_cache()
        try:
            with app_for(world) as app:
                off = await admin(app, "GET", url)
                off_list = await admin(app, "GET", ACCOUNTS)
        finally:
            billing_config.reset_billing_config_cache()
    assert off.json()["code"] == "billing_disabled" and off_list.status_code == 404


async def test_unlocked_account_follows_a_plan_priced_later_in_another_currency(
    world: World,
) -> None:
    plan = standard(world)
    url = f"{ACCOUNTS}/{world.shree.id}"
    with app_for(world) as app:
        first = await admin(app, "GET", url)  # account is created in the default INR
        put = await admin(
            app,
            "PUT",
            PRICING.format(plan.id),
            json={
                "currency": "USD",
                "usage_mode": "per_question",
                "per_question_minor": 5,
                "apply": "scheduled",  # the plan has businesses: a first price is announced (TV3)
            },
        )
        # the day the announced price starts, the unlocked account follows the plan
        from datetime import UTC, datetime, timedelta

        from callobuzz_rag.billing.notice import activate_due

        await activate_due(world.db.factory(), datetime.now(UTC) + timedelta(days=60))
        after = await admin(app, "GET", url)
    assert first.json()["currency"] == "INR"
    assert put.status_code == 200, put.text
    account = account_of(world, world.shree)
    assert account is not None and account.currency == "USD"
    assert after.json()["currency"] == "USD"


async def test_effective_currency_is_the_plans_until_the_account_is_locked(
    world: World,
) -> None:
    plan = standard(world)
    world.db.add(stored_pricing(plan, currency="USD", usage_mode="per_question"))
    account = world.db.add(BillingAccount(tenant_id=world.shree.id, currency="INR", ledger_seq=0))
    with app_for(world) as app:
        unlocked = await admin(app, "GET", f"{ACCOUNTS}/{world.shree.id}")
        account.ledger_seq = 2
        locked = await admin(app, "GET", f"{ACCOUNTS}/{world.shree.id}")
    assert unlocked.json()["currency"] == "USD"
    assert locked.json()["currency"] == "INR"


async def test_account_currency_cannot_differ_from_the_plans_pricing(world: World) -> None:
    plan = standard(world)
    world.db.add(stored_pricing(plan, currency="INR", usage_mode="per_question"))
    world.db.add(BillingAccount(tenant_id=world.shree.id, currency="INR", ledger_seq=0))
    with app_for(world) as app:
        res = await admin(app, "PATCH", f"{ACCOUNTS}/{world.shree.id}", json={"currency": "USD"})
    assert res.status_code == 409 and res.json()["code"] == "currency_mismatch"
    assert res.json()["currency"] == "INR" and res.json()["account_currency"] == "INR"
    account = account_of(world, world.shree)
    assert account is not None and account.currency == "INR"
