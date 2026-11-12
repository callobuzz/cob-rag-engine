"""The add-on catalogue and grants (BIL5): kind rules, name uniqueness, in-use deletes, applying
and revoking with the ledger, idempotency, audit, scoping.

Through the real app on the in-memory database of `accounts_fakes.py`; `append_entries` is the
stand-in of `test_billing_ledger_api.py` (it records the entries it is given). The SQL of the
ledger, of the grant sums and of the concurrent double click runs on PostgreSQL in
`tests/integration/test_billing_addons_db.py`.
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.billing import config as billing_config
from callobuzz_rag.billing import ledger
from callobuzz_rag.db.models.add_on import AddOn, AddOnGrant
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.wallet_ledger import WalletLedger
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import ADMIN, World, auth_app, login, req
from tests.unit.test_billing_ledger_api import fake_append

ADD_ONS = "/api/v1/admin/billing/add-ons"
GRANTS = "/api/v1/admin/billing/accounts/{}/grants"
KEY = "0123456789abcdef"


@pytest.fixture
def world() -> World:
    return World()


@contextmanager
def app_for(world: World, *, available: int = 100_000) -> Iterator[Any]:
    entries: list[ledger.NewEntry] = []
    real = fake_append(world)

    async def recording(session: Any, account_id: Any, new: Any, **kw: Any) -> Any:
        entries.extend(new)
        return await real(session, account_id, new, **kw)

    with (
        auth_app(world) as app,
        fake_accounts(world.db),
        patch.object(ledger, "append_entries", recording),
        patch.object(ledger, "available_minor", AsyncMock(return_value=available)),
        patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()) as hook,
    ):
        app.state.hook = hook
        app.state.entries = entries
        yield app


async def admin(app: Any, method: str, path: str, **kw: Any) -> Any:
    return await req(app, method, path, key=ADMIN, **kw)


def pack(**kw: Any) -> dict[str, Any]:
    return {
        "kind": "question_pack",
        "name": "500 extra questions",
        "currency": "INR",
        "price_minor": 29_900,
        "questions": 500,
    } | kw


def credit(**kw: Any) -> dict[str, Any]:
    return {
        "kind": "credit_pack",
        "name": "Credit ₹1,000",
        "currency": "INR",
        "price_minor": 100_000,
        "credit_minor": 100_000,
        "bonus_pct": 10,
        "expiry_days": 90,
    } | kw


def limit(**kw: Any) -> dict[str, Any]:
    return {
        "kind": "extra_limit",
        "name": "+1 widget key",
        "currency": "INR",
        "price_minor": 10_000,
        "limit_field": "max_widget_keys",
        "limit_delta": 1,
        "duration": "permanent",
    } | kw


async def make(app: Any, body: dict[str, Any]) -> dict[str, Any]:
    res = await admin(app, "POST", ADD_ONS, json=body)
    assert res.status_code == 201, res.text
    return res.json()


def apply_body(add_on: dict[str, Any], key: str = KEY, **kw: Any) -> dict[str, Any]:
    return {"add_on_id": add_on["id"], "idempotency_key": key} | kw


def priced(world: World, **fields: Any) -> None:
    world.db.add(stored_pricing(world.db.default_plan, currency="INR", **fields))


# ---------------------------------------------------------------------------- catalogue


async def test_each_kind_is_created_with_exactly_its_fields(world: World) -> None:
    with app_for(world) as app:
        credit_pack = await make(app, credit())
        extra = await make(app, limit())
        questions = await make(app, pack())
    assert credit_pack["credit_minor"] == 100_000 and credit_pack["bonus_pct"] == 10
    assert credit_pack["expiry_days"] == 90 and credit_pack["questions"] is None
    assert extra["limit_field"] == "max_widget_keys" and extra["duration"] == "permanent"
    assert questions["questions"] == 500 and questions["limit_delta"] is None
    assert all(a["is_active"] for a in (credit_pack, extra, questions))
    assert len(world.audits("billing.add_on_created")) == 3
    audit = world.audits("billing.add_on_created")[0]
    assert audit.details == {
        "add_on_id": credit_pack["id"],
        "kind": "credit_pack",
        "name": credit_pack["name"],
    }


@pytest.mark.parametrize(
    "body",
    [
        credit(credit_minor=None),  # a credit pack needs its credit
        credit(questions=5),  # ... and has no question count
        credit(limit_field="max_documents", limit_delta=1, duration="period"),
        limit(limit_field="monthly_questions"),  # question packs exist for that
        limit(limit_delta=None),
        limit(duration=None),
        limit(credit_minor=5),
        limit(bonus_pct=10),
        pack(questions=None),
        pack(limit_field="max_documents"),
        pack(duration="period"),
        pack(expiry_days=30),
        pack(price_minor=-1),
        pack(currency="EUR"),
        pack(typo_field=1),
        pack(name=""),
    ],
)
async def test_a_wrong_field_for_the_kind_is_422(world: World, body: dict[str, Any]) -> None:
    with app_for(world) as app:
        res = await admin(app, "POST", ADD_ONS, json=body)
    assert res.status_code == 422, res.text
    assert world.audits("billing.add_on_created") == []


async def test_a_taken_name_is_409(world: World) -> None:
    with app_for(world) as app:
        await make(app, pack())
        again = await admin(app, "POST", ADD_ONS, json=pack(questions=900))
        other = await make(app, pack(name="900 extra questions", questions=900))
        rename = await admin(
            app, "PATCH", f"{ADD_ONS}/{other['id']}", json={"name": pack()["name"]}
        )
    assert again.status_code == 409 and again.json()["code"] == "add_on_name_taken"
    assert rename.status_code == 409 and rename.json()["code"] == "add_on_name_taken"


async def test_patch_changes_only_what_was_sent_and_keeps_the_kind_valid(world: World) -> None:
    with app_for(world) as app:
        row = await make(app, pack())
        url = f"{ADD_ONS}/{row['id']}"
        price = await admin(app, "PATCH", url, json={"price_minor": 39_900})
        same = await admin(app, "PATCH", url, json={"price_minor": 39_900})
        wrong = await admin(app, "PATCH", url, json={"limit_field": "max_documents"})
        no_q = await admin(app, "PATCH", url, json={"questions": None})
        off = await admin(app, "PATCH", url, json={"is_active": False})
        missing = await admin(app, "PATCH", f"{ADD_ONS}/{uuid.uuid4()}", json={"is_active": False})
    assert price.status_code == 200 and price.json()["price_minor"] == 39_900
    assert price.json()["questions"] == 500
    assert same.status_code == 200
    assert wrong.status_code == 422 and no_q.status_code == 422
    assert off.json()["is_active"] is False
    assert missing.status_code == 404 and missing.json()["code"] == "add_on_not_found"
    updated = world.audits("billing.add_on_updated")
    assert [a.details["changed"] for a in updated] == [["price_minor"], ["is_active"]]


async def test_the_list_hides_deactivated_add_ons_unless_asked(world: World) -> None:
    with app_for(world) as app:
        keep = await make(app, pack())
        gone = await make(app, pack(name="old pack"))
        await admin(app, "PATCH", f"{ADD_ONS}/{gone['id']}", json={"is_active": False})
        shown = await admin(app, "GET", ADD_ONS)
        everything = await admin(app, "GET", f"{ADD_ONS}?include_inactive=true")
    assert [i["id"] for i in shown.json()["items"]] == [keep["id"]]
    assert {i["id"] for i in everything.json()["items"]} == {keep["id"], gone["id"]}


async def test_delete_works_only_for_an_add_on_never_granted(world: World) -> None:
    priced(world)
    with app_for(world) as app:
        free = await make(app, pack(name="never sold"))
        sold = await make(app, pack())
        granted = await admin(app, "POST", GRANTS.format(world.shree.id), json=apply_body(sold))
        deleted = await admin(app, "DELETE", f"{ADD_ONS}/{free['id']}")
        refused = await admin(app, "DELETE", f"{ADD_ONS}/{sold['id']}")
        missing = await admin(app, "DELETE", f"{ADD_ONS}/{free['id']}")
    assert granted.status_code == 201, granted.text
    assert deleted.status_code == 204 and deleted.content == b""
    assert refused.status_code == 409 and refused.json()["code"] == "add_on_in_use"
    assert missing.status_code == 404
    assert [a.details["add_on_id"] for a in world.audits("billing.add_on_deleted")] == [free["id"]]


async def test_catalogue_is_admin_only_and_respects_the_switch(world: World) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree, "owner")
    with app_for(world) as app:
        token = (await login(app, asha.email)).json()["access_token"]
        as_owner = await req(app, "GET", ADD_ONS, token=token)
        as_owner_post = await req(app, "POST", ADD_ONS, token=token, json=pack())
        anonymous = await req(app, "GET", ADD_ONS)
    assert as_owner.status_code == 403 and as_owner_post.status_code == 403
    assert anonymous.status_code in (401, 403)
    with patch.dict(os.environ, {"RAG_BILLING_ENABLED": "false"}):
        billing_config.reset_billing_config_cache()
        try:
            with app_for(world) as app:
                off = await admin(app, "GET", ADD_ONS)
                off_post = await admin(app, "POST", ADD_ONS, json=pack())
        finally:
            billing_config.reset_billing_config_cache()
    assert off.status_code == 404 and off.json()["code"] == "billing_disabled"
    assert off_post.status_code == 404


# ------------------------------------------------------------------------------- grants


async def test_a_credit_pack_puts_credit_and_bonus_in_the_ledger_with_an_expiry(
    world: World,
) -> None:
    priced(world)
    with app_for(world) as app:
        row = await make(app, credit())
        res = await admin(app, "POST", GRANTS.format(world.shree.id), json=apply_body(row))
        entries, hook_calls = app.state.entries, app.state.hook.await_count
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["kind"] == "credit_pack" and body["payment"] == "external"
    assert body["active"] is False  # a credit pack changes the balance, not a limit
    assert body["summary"] == "₹1,000.00 credit (+10 % bonus)"
    [entry] = entries
    assert entry.entry_type is ledger.EntryType.ADD_ON_PURCHASE and entry.amount_minor == 110_000
    assert entry.memo_public == "Credit pack ₹1,000.00 (+10 % bonus)"
    assert entry.idempotency_key == f"addon:{body['id']}"
    assert entry.reference_type == "add_on_grant" and entry.reference_id == body["id"]
    assert entry.expires_at is not None
    assert 89 <= (entry.expires_at - datetime.now(UTC)).days <= 90
    [audit] = world.audits("billing.add_on_granted")
    assert audit.details["amount_minor"] == 110_000 and audit.details["entry_id"]
    assert audit.details["grant_id"] == body["id"] and audit.tenant_id == world.shree.id
    assert hook_calls == 1
    assert [a.balance_minor for a in world.db.all(BillingAccount)] == [110_000]


async def test_a_credit_pack_without_expiry_or_bonus_has_no_lot(world: World) -> None:
    priced(world)
    with app_for(world) as app:
        row = await make(app, credit(name="plain", bonus_pct=0, expiry_days=None))
        res = await admin(app, "POST", GRANTS.format(world.shree.id), json=apply_body(row))
        [entry] = app.state.entries
    assert res.status_code == 201
    assert entry.amount_minor == 100_000 and entry.expires_at is None
    assert entry.memo_public == "Credit pack ₹1,000.00"


async def test_a_credit_pack_cannot_be_paid_from_the_balance(world: World) -> None:
    priced(world)
    with app_for(world) as app:
        row = await make(app, credit())
        res = await admin(
            app, "POST", GRANTS.format(world.shree.id), json=apply_body(row, payment="wallet")
        )
        entries = list(app.state.entries)
    assert res.status_code == 422
    assert entries == [] and world.audits("billing.add_on_granted") == []


async def test_a_question_pack_paid_outside_writes_no_money_and_lasts_the_month(
    world: World,
) -> None:
    priced(world)
    with app_for(world) as app:
        row = await make(app, pack())
        res = await admin(
            app,
            "POST",
            GRANTS.format(world.shree.id),
            json=apply_body(row, memo="paid by UPI"),
        )
        listed = await admin(app, "GET", GRANTS.format(world.shree.id) + "?active_only=true")
        entries, hook_calls = list(app.state.entries), app.state.hook.await_count
    assert res.status_code == 201, res.text
    body = res.json()
    assert entries == [] and body["entry_id"] is None and hook_calls == 0
    assert body["active"] is True and body["memo"] == "paid by UPI"
    assert body["questions"] == 500 and body["price_minor"] == 29_900
    starts = datetime.fromisoformat(body["starts_at"])
    ends = datetime.fromisoformat(body["ends_at"])
    assert ends.day == 1 and ends > starts and (ends - starts).days <= 31
    assert body["summary"].startswith("+500 questions until 1 ")
    assert [g["id"] for g in listed.json()["items"]] == [body["id"]]
    [audit] = world.audits("billing.add_on_granted")
    assert "entry_id" not in audit.details and audit.details["payment"] == "external"


async def test_a_permanent_extra_limit_has_no_end(world: World) -> None:
    priced(world)
    with app_for(world) as app:
        row = await make(app, limit())
        res = await admin(
            app, "POST", GRANTS.format(world.shree.id), json=apply_body(row, payment="free")
        )
    assert res.status_code == 201
    assert res.json()["ends_at"] is None and res.json()["summary"] == "+1 widget keys"
    assert res.json()["payment"] == "free" and res.json()["entry_id"] is None


async def test_paying_from_the_balance_takes_the_price_out_or_is_refused(world: World) -> None:
    priced(world, payment_type="prepaid")
    with app_for(world, available=5_000) as app:
        row = await make(app, limit(price_minor=10_000))
        short = await admin(
            app, "POST", GRANTS.format(world.shree.id), json=apply_body(row, payment="wallet")
        )
    assert short.status_code == 409 and short.json()["code"] == "insufficient_balance"
    assert short.json()["available_minor"] == 5_000 and short.json()["price_minor"] == 10_000
    assert world.audits("billing.add_on_granted") == []
    with app_for(world, available=50_000) as app:
        row = await make(app, limit(name="again", price_minor=10_000))
        ok = await admin(
            app, "POST", GRANTS.format(world.shree.id), json=apply_body(row, payment="wallet")
        )
        [entry] = app.state.entries
    assert ok.status_code == 201, ok.text
    assert entry.amount_minor == -10_000 and entry.memo_public == "Add-on: again"
    assert ok.json()["entry_id"] is not None


async def test_a_postpaid_credit_limit_extends_what_the_balance_can_cover(world: World) -> None:
    priced(world, payment_type="postpaid", credit_limit_minor=20_000)
    with app_for(world, available=5_000) as app:  # 5,000 - 10,000 = -5,000 >= -20,000
        row = await make(app, limit(price_minor=10_000))
        ok = await admin(
            app, "POST", GRANTS.format(world.shree.id), json=apply_body(row, payment="wallet")
        )
        big = await make(app, limit(name="big", price_minor=30_000))
        refused = await admin(
            app,
            "POST",
            GRANTS.format(world.shree.id),
            json=apply_body(big, key="another-key-1", payment="wallet"),
        )
    assert ok.status_code == 201, ok.text
    assert refused.status_code == 409 and refused.json()["code"] == "insufficient_balance"


async def test_the_same_key_twice_is_one_grant_and_one_entry(world: World) -> None:
    priced(world)
    with app_for(world) as app:
        row = await make(app, credit())
        other = await make(app, pack())
        first = await admin(app, "POST", GRANTS.format(world.shree.id), json=apply_body(row))
        again = await admin(app, "POST", GRANTS.format(world.shree.id), json=apply_body(row))
        different = await admin(app, "POST", GRANTS.format(world.shree.id), json=apply_body(other))
        entries = list(app.state.entries)
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert different.status_code == 409 and different.json()["code"] == "idempotency_conflict"
    assert len(world.db.all(AddOnGrant)) == 1 and len(entries) == 1
    assert len(world.audits("billing.add_on_granted")) == 1
    assert len(world.db.all(WalletLedger)) == 1


async def test_refusals_inactive_currency_exempt_unknown(world: World) -> None:
    priced(world)
    world.shree.is_operator = False
    with app_for(world) as app:
        row = await make(app, pack())
        dollars = await make(app, pack(name="usd pack", currency="USD"))
        await admin(app, "PATCH", f"{ADD_ONS}/{row['id']}", json={"is_active": False})
        inactive = await admin(app, "POST", GRANTS.format(world.shree.id), json=apply_body(row))
        mismatch = await admin(
            app, "POST", GRANTS.format(world.shree.id), json=apply_body(dollars, key="usd-key-123")
        )
        unknown_add_on = await admin(
            app,
            "POST",
            GRANTS.format(world.shree.id),
            json={"add_on_id": str(uuid.uuid4()), "idempotency_key": "unknown-key"},
        )
        unknown_tenant = await admin(
            app, "POST", GRANTS.format(uuid.uuid4()), json=apply_body(dollars)
        )
        world.pune.is_operator = True
        exempt = await admin(app, "POST", GRANTS.format(world.pune.id), json=apply_body(dollars))
        bad_key = await admin(
            app, "POST", GRANTS.format(world.shree.id), json=apply_body(dollars, key="short")
        )
    assert inactive.status_code == 409 and inactive.json()["code"] == "add_on_inactive"
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "currency_mismatch"
    assert unknown_add_on.status_code == 404 and unknown_add_on.json()["code"] == "add_on_not_found"
    assert unknown_tenant.status_code == 404 and unknown_tenant.json()["code"] == "tenant_not_found"
    assert exempt.status_code == 409 and exempt.json()["code"] == "billing_exempt"
    assert bad_key.status_code == 422
    assert world.db.all(AddOnGrant) == []


async def test_a_grant_keeps_its_snapshot_when_the_catalogue_changes(world: World) -> None:
    priced(world)
    with app_for(world) as app:
        row = await make(app, pack())
        granted = (
            await admin(app, "POST", GRANTS.format(world.shree.id), json=apply_body(row))
        ).json()
        await admin(
            app, "PATCH", f"{ADD_ONS}/{row['id']}", json={"questions": 5, "name": "renamed"}
        )
        listed = await admin(app, "GET", GRANTS.format(world.shree.id))
    [item] = listed.json()["items"]
    assert item["id"] == granted["id"] and item["questions"] == 500
    assert item["name"] == "500 extra questions"


async def test_revoking_ends_the_grant_now_and_only_once(world: World) -> None:
    priced(world)
    with app_for(world) as app:
        row = await make(app, pack())
        credit_row = await make(app, credit())
        grant = (
            await admin(app, "POST", GRANTS.format(world.shree.id), json=apply_body(row))
        ).json()
        pack_grant = (
            await admin(
                app,
                "POST",
                GRANTS.format(world.shree.id),
                json=apply_body(credit_row, key="credit-key-1"),
            )
        ).json()
        url = GRANTS.format(world.shree.id) + f"/{grant['id']}/revoke"
        revoked = await admin(app, "POST", url, json={"memo": "refunded outside"})
        twice = await admin(app, "POST", url, json={})
        no_body = await admin(
            app, "POST", GRANTS.format(world.shree.id) + f"/{pack_grant['id']}/revoke"
        )
        other_tenant = await admin(
            app, "POST", GRANTS.format(world.pune.id) + f"/{grant['id']}/revoke", json={}
        )
        unknown = await admin(
            app, "POST", GRANTS.format(world.shree.id) + f"/{uuid.uuid4()}/revoke", json={}
        )
        active = await admin(app, "GET", GRANTS.format(world.shree.id) + "?active_only=true")
        history = await admin(app, "GET", GRANTS.format(world.shree.id))
    assert revoked.status_code == 200, revoked.text
    body = revoked.json()
    assert body["active"] is False and body["revoke_memo"] == "refunded outside"
    assert body["revoked_at"] is not None and body["revoked_by"] == "admin key"
    assert datetime.fromisoformat(body["ends_at"]) <= datetime.now(UTC)
    assert twice.status_code == 409 and twice.json()["code"] == "grant_already_revoked"
    assert no_body.status_code == 409 and no_body.json()["code"] == "grant_not_revocable"
    assert other_tenant.status_code == 404 and other_tenant.json()["code"] == "grant_not_found"
    assert unknown.status_code == 404 and unknown.json()["code"] == "grant_not_found"
    assert active.json()["items"] == []
    assert len(history.json()["items"]) == 2
    [audit] = world.audits("billing.grant_revoked")
    assert audit.details["grant_id"] == grant["id"] and audit.tenant_id == world.shree.id


async def test_grants_are_admin_only_and_unknown_tenants_are_404(world: World) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree, "owner")
    with app_for(world) as app:
        token = (await login(app, asha.email)).json()["access_token"]
        owner_get = await req(app, "GET", GRANTS.format(world.shree.id), token=token)
        owner_post = await req(app, "POST", GRANTS.format(world.shree.id), token=token, json={})
        missing = await admin(app, "GET", GRANTS.format(uuid.uuid4()))
    assert owner_get.status_code == 403 and owner_post.status_code == 403
    assert missing.status_code == 404 and missing.json()["code"] == "tenant_not_found"


def test_add_on_rows_have_the_models_defaults() -> None:
    assert AddOn.__tablename__ == "add_on_catalogue" and AddOnGrant.__tablename__ == "add_on_grants"


async def test_a_purchase_may_not_land_on_the_pause_line(world: World) -> None:
    priced(world, payment_type="prepaid", grace_minor=0)
    with app_for(world, available=10_000) as app:  # 10,000 - 10,000 = 0: prepaid_empty
        row = await make(app, limit(price_minor=10_000))
        edge = await admin(
            app, "POST", GRANTS.format(world.shree.id), json=apply_body(row, payment="wallet")
        )
    assert edge.status_code == 409 and edge.json()["code"] == "insufficient_balance"
    assert world.audits("billing.add_on_granted") == []
    with app_for(world, available=10_001) as app:
        row = await make(app, limit(name="above", price_minor=10_000))
        above = await admin(
            app, "POST", GRANTS.format(world.shree.id), json=apply_body(row, payment="wallet")
        )
    assert above.status_code == 201, above.text


async def test_a_postpaid_purchase_may_not_land_on_the_credit_limit(world: World) -> None:
    priced(world, payment_type="postpaid", credit_limit_minor=20_000)
    with app_for(world, available=-10_000) as app:  # -10,000 - 10,000 = -20,000: credit_limit
        row = await make(app, limit(price_minor=10_000))
        edge = await admin(
            app, "POST", GRANTS.format(world.shree.id), json=apply_body(row, payment="wallet")
        )
    assert edge.status_code == 409 and edge.json()["code"] == "insufficient_balance"
    with app_for(world, available=-9_999) as app:
        row = await make(app, limit(name="above", price_minor=10_000))
        above = await admin(
            app, "POST", GRANTS.format(world.shree.id), json=apply_body(row, payment="wallet")
        )
    assert above.status_code == 201, above.text


async def test_a_blank_name_is_422_on_create_and_patch(world: World) -> None:
    with app_for(world) as app:
        blank = await admin(app, "POST", ADD_ONS, json=pack(name="   "))
        row = await make(app, pack())
        patch_blank = await admin(app, "PATCH", f"{ADD_ONS}/{row['id']}", json={"name": " \t "})
    assert blank.status_code == 422 and patch_blank.status_code == 422
    assert world.audits("billing.add_on_created") != []  # only the valid one
    assert len(world.audits("billing.add_on_created")) == 1
