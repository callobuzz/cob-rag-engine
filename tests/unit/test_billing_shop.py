"""P5 T7: the business owner's add-on shop, and the owner's own-key status.

Through the real app on the in-memory database; the own-key lookup (one SQL statement the fake
database cannot run) and support request numbers (a PostgreSQL sequence) are stood in for, the
ledger is the recording stand-in of `test_billing_addons_api.py`.
"""

import itertools
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts.settings_store import SETTINGS_ROW_ID, invalidate_engine_settings_cache
from callobuzz_rag.billing import addons, ledger
from callobuzz_rag.billing.payments.fake import FakePaymentProvider
from callobuzz_rag.db.models.add_on import AddOn, AddOnGrant
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.payment import BillingProfile
from callobuzz_rag.db.models.service_order import ServiceOrder
from callobuzz_rag.demo.guard import refusal
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import World, auth_app, login, req
from tests.unit.test_billing_ledger_api import fake_append

SHOP = "/api/v1/billing/add-ons"
KEY = "shop-test-key-0001"
SELLER = {
    "company": {"legal_name": "Callobuzz", "state_code": "27", "city": "Pune"},
    "invoicing": {"gstin": "27AAPFU0939F1ZV"},
}


@pytest.fixture
def world() -> World:
    w = World()
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("ravi@shree.example"), w.shree, "member")
    w.member(w.user("meena@pune.example"), w.pune, "owner")
    w.db.add(stored_pricing(w.db.default_plan, currency="INR", subscription_fee_minor=0))
    invalidate_engine_settings_cache()
    w.db.add(EngineSettingsRow(id=SETTINGS_ROW_ID, value=SELLER))
    w.db.add(
        BillingProfile(
            tenant_id=w.shree.id,
            legal_name="Sharma Furniture",
            address_line1="Paud Road",
            city="Pune",
            state_code="27",
            country="IN",
        )
    )
    return w


@contextmanager
def app_for(
    world: World, *, available: int = 500_000, allowed: bool = False, online: bool = True
) -> Iterator[Any]:
    entries: list[ledger.NewEntry] = []
    real = fake_append(world)
    numbers = itertools.count(3001)

    async def recording(session: Any, account_id: Any, new: Any, **kw: Any) -> Any:
        entries.extend(new)
        return await real(session, account_id, new, **kw)

    async def next_number(session: Any) -> int:
        return next(numbers)

    status = AsyncMock(return_value=(allowed, "plan" if allowed else None))
    with (
        auth_app(world) as app,
        fake_accounts(world.db),
        patch.object(ledger, "append_entries", recording),
        patch.object(ledger, "available_minor", AsyncMock(return_value=available)),
        patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()),
        patch("callobuzz_rag.support.service.next_number", next_number),
        patch.object(addons, "byok_status", status),
        patch("callobuzz_rag.api.routes.billing_shop.byok_status", status),
        patch("callobuzz_rag.api.routes.payments.byok_status", status),
        patch("callobuzz_rag.api.routes.billing_shop.announce_new_orders", AsyncMock()) as announce,
    ):
        app.state.entries = entries
        app.state.announce = announce
        if online:
            app.state.payment_provider_override = FakePaymentProvider()
        yield app
    invalidate_engine_settings_cache()


def item(world: World, **kw: Any) -> AddOn:
    values: dict[str, Any] = {
        "kind": "service",
        "name": "Installation help",
        "currency": "INR",
        "price_minor": 199_900,
        "includes_support_months": 3,
        "sac_code": "998313",
        "is_active": True,
        "sold_to_owners": True,
        "renews": False,
        "bonus_pct": 0,
        "placeholder": False,
    }
    return world.db.add(AddOn(**(values | kw)))


def own_key(world: World, **kw: Any) -> AddOn:
    return item(
        world,
        kind="feature_unlock",
        name="Use your own AI key",
        price_minor=49_900,
        feature="byok",
        duration="period",
        renews=True,
        includes_support_months=None,
        sac_code=None,
        **kw,
    )


def pack(world: World, **kw: Any) -> AddOn:
    return item(
        world,
        kind="credit_pack",
        name="Credit pack ₹1,000",
        price_minor=100_000,
        credit_minor=100_000,
        includes_support_months=None,
        sac_code=None,
        **kw,
    )


async def call(
    app: Any, method: str, path: str, email: str = "asha@shree.example", **kw: Any
) -> Any:
    token = (await login(app, email)).json()["access_token"]
    return await req(app, method, path, token=token, **kw)


# ------------------------------------------------------------------------------- the list


async def test_the_shop_lists_what_the_deployer_put_in_it_with_gst(world: World) -> None:
    key, help_, credits = own_key(world), item(world), pack(world)
    item(world, name="Operator-only extra", sold_to_owners=False)
    item(world, name="Retired", is_active=False)
    with app_for(world) as app:
        res = await call(app, "GET", SHOP)
    assert res.status_code == 200, res.text
    by_id = {i["id"]: i for i in res.json()["items"]}
    assert set(by_id) == {str(key.id), str(help_.id), str(credits.id)}
    own = by_id[str(key.id)]
    assert (own["kind_label"], own["price_minor"], own["inclusive_total_minor"]) == (
        "Your own AI key",
        49_900,
        58_882,
    )
    assert (
        own["renews"] is True
        and own["owned"] is False
        and own["buy_with"]
        == [
            "online",
            "balance",
        ]
    )
    assert by_id[str(help_.id)]["kind_label"] == "Services"
    assert by_id[str(help_.id)]["gives"] == "A service, includes 3 months of support"
    assert by_id[str(credits.id)]["buy_with"] == ["online"]  # not from the balance it adds to
    assert by_id[str(credits.id)]["kind_label"] == "Credit packs"


async def test_a_seeded_placeholder_price_is_not_for_sale_until_the_deployer_sets_it(
    world: World,
) -> None:
    shown = item(world, name="Website design", placeholder=True)
    real = item(world, name="Installation help")
    with app_for(world) as app:
        ids = {i["id"] for i in (await call(app, "GET", SHOP)).json()["items"]}
        bought = await call(
            app,
            "POST",
            f"{SHOP}/{shown.id}/buy",
            json={"idempotency_key": "k-placeholder-1"},
        )
    assert ids == {str(real.id)}
    assert bought.status_code == 404 and bought.json()["code"] == "add_on_not_found"


async def test_an_item_is_not_affordable_when_the_price_would_reach_the_pause_point(
    world: World,
) -> None:
    row = own_key(world)  # 49,900 before GST
    flags = {}
    for available in (49_899, 49_900, 49_901):  # prepaid, no grace: leaving 0 already pauses
        with app_for(world, available=available) as app:
            (shown,) = (await call(app, "GET", SHOP)).json()["items"]
        flags[available] = shown["affordable"]
    assert flags == {49_899: False, 49_900: False, 49_901: True}
    assert row.price_minor == 49_900


async def test_without_online_payments_only_the_balance_is_offered(world: World) -> None:
    own_key(world)
    with app_for(world, online=False) as app:
        (shown,) = (await call(app, "GET", SHOP)).json()["items"]
    assert shown["buy_with"] == ["balance"]


async def test_a_price_that_already_includes_gst_is_not_taxed_again(world: World) -> None:
    world.db.rows[EngineSettingsRow].clear()
    invalidate_engine_settings_cache()
    world.db.add(
        EngineSettingsRow(
            id=SETTINGS_ROW_ID,
            value={**SELLER, "invoicing": {**SELLER["invoicing"], "prices_include_gst": True}},
        )
    )
    own_key(world)
    with app_for(world) as app:
        (shown,) = (await call(app, "GET", SHOP)).json()["items"]
    assert shown["inclusive_total_minor"] == 49_900


async def test_a_feature_the_plan_includes_is_shown_as_owned_and_not_for_sale(
    world: World,
) -> None:
    own_key(world)
    with app_for(world, allowed=True) as app:
        (shown,) = (await call(app, "GET", SHOP)).json()["items"]
    assert shown["owned"] is True and shown["buy_with"] == [] and shown["grant_id"] is None


async def test_an_active_renewing_purchase_shows_until_when_and_cancelled_or_not(
    world: World,
) -> None:
    from datetime import UTC, datetime, timedelta

    row = own_key(world)
    now = datetime.now(UTC)
    grant = world.db.add(
        AddOnGrant(
            id=uuid.uuid4(),
            tenant_id=world.shree.id,
            add_on_id=row.id,
            kind="feature_unlock",
            name=row.name,
            currency="INR",
            price_minor=49_900,
            feature="byok",
            renews=True,
            payment="wallet",
            granted_by="asha@shree.example",
            starts_at=now - timedelta(days=3),
            ends_at=now + timedelta(days=20),
        )
    )
    with app_for(world) as app:
        (shown,) = (await call(app, "GET", SHOP)).json()["items"]
        grant.cancelled_at = now
        (cancelled,) = (await call(app, "GET", SHOP)).json()["items"]
    assert shown["owned"] and shown["grant_id"] == str(grant.id) and shown["owned_until"]
    assert shown["renewal_cancelled"] is False and cancelled["renewal_cancelled"] is True


async def test_another_workspaces_purchase_does_not_make_it_owned(world: World) -> None:
    from datetime import UTC, datetime, timedelta

    row = own_key(world)
    now = datetime.now(UTC)
    world.db.add(
        AddOnGrant(
            id=uuid.uuid4(),
            tenant_id=world.pune.id,
            add_on_id=row.id,
            kind="feature_unlock",
            name=row.name,
            currency="INR",
            price_minor=49_900,
            feature="byok",
            renews=True,
            payment="wallet",
            granted_by="meena@pune.example",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=20),
        )
    )
    with app_for(world) as app:
        (shown,) = (await call(app, "GET", SHOP)).json()["items"]
    assert shown["owned"] is False


async def test_members_may_look(world: World) -> None:
    own_key(world)
    with app_for(world) as app:
        res = await call(app, "GET", SHOP, email="ravi@shree.example")
    assert res.status_code == 200 and len(res.json()["items"]) == 1


# --------------------------------------------------------------------------------- buying


async def test_an_owner_buys_a_service_from_the_balance_and_gets_an_order(world: World) -> None:
    row = item(world)
    with app_for(world) as app:
        res = await call(
            app,
            "POST",
            f"{SHOP}/{row.id}/buy",
            json={"payment": "wallet", "idempotency_key": KEY, "note": "Please call before 11"},
        )
        again = await call(
            app, "POST", f"{SHOP}/{row.id}/buy", json={"payment": "wallet", "idempotency_key": KEY}
        )
        entries = list(app.state.entries)
        announced = app.state.announce.await_count
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["kind"] == "service" and body["name"] == "Installation help"
    assert "price_minor" not in body and "payment" not in body  # an owner's view of a purchase
    assert again.status_code == 200 and again.json()["id"] == body["id"]  # one purchase
    assert [e.amount_minor for e in entries] == [-199_900]  # one entry
    (order,) = world.db.all(ServiceOrder)
    assert order.owner_note == "Please call before 11" and order.status == "requested"
    assert announced == 1  # the provider is told once


async def test_an_owner_buys_the_own_key_add_on_and_it_renews(world: World) -> None:
    row = own_key(world)
    with app_for(world) as app:
        res = await call(
            app, "POST", f"{SHOP}/{row.id}/buy", json={"payment": "wallet", "idempotency_key": KEY}
        )
    assert res.status_code == 201, res.text
    assert res.json()["renews"] is True and res.json()["ends_at"] is not None


async def test_a_member_cannot_buy(world: World) -> None:
    row = item(world)
    with app_for(world) as app:
        res = await call(
            app,
            "POST",
            f"{SHOP}/{row.id}/buy",
            email="ravi@shree.example",
            json={"idempotency_key": KEY},
        )
    assert res.status_code == 403 and res.json()["code"] == "owner_required"
    assert world.db.all(AddOnGrant) == []


@pytest.mark.parametrize(
    "make", [lambda w: item(w, sold_to_owners=False), lambda w: item(w, is_active=False)]
)
async def test_an_item_not_in_the_shop_is_the_same_404_as_an_unknown_one(
    world: World, make: Any
) -> None:
    row = make(world)
    with app_for(world) as app:
        hidden = await call(app, "POST", f"{SHOP}/{row.id}/buy", json={"idempotency_key": KEY})
        unknown = await call(
            app, "POST", f"{SHOP}/{uuid.uuid4()}/buy", json={"idempotency_key": KEY}
        )
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json()["code"] == "add_on_not_found"


async def test_a_balance_that_cannot_cover_it_is_409(world: World) -> None:
    row = item(world)
    with app_for(world, available=1_000) as app:
        res = await call(app, "POST", f"{SHOP}/{row.id}/buy", json={"idempotency_key": KEY})
    assert res.status_code == 409 and res.json()["code"] == "insufficient_balance"
    assert world.db.all(AddOnGrant) == []


async def test_a_credit_pack_cannot_be_paid_from_the_balance_it_adds_to(world: World) -> None:
    row = pack(world)
    with app_for(world) as app:
        res = await call(app, "POST", f"{SHOP}/{row.id}/buy", json={"idempotency_key": KEY})
    assert res.status_code == 422, res.text


async def test_buying_a_feature_the_plan_has_is_409(world: World) -> None:
    row = own_key(world)
    with app_for(world, allowed=True) as app:
        res = await call(app, "POST", f"{SHOP}/{row.id}/buy", json={"idempotency_key": KEY})
    assert res.status_code == 409 and res.json()["code"] == "feature_already_included"


async def test_the_operators_own_workspace_is_not_sold_to(world: World) -> None:
    row = item(world)
    world.shree.is_operator = True
    with app_for(world) as app:
        listed = await call(app, "GET", SHOP)
        res = await call(app, "POST", f"{SHOP}/{row.id}/buy", json={"idempotency_key": KEY})
    assert listed.json() == {"items": []} and res.status_code in (404, 409)


# ------------------------------------------------------------------------------ cancelling


async def test_cancelling_a_renewal_keeps_it_until_the_period_ends(world: World) -> None:
    row = own_key(world)
    with app_for(world) as app:
        bought = (
            await call(
                app,
                "POST",
                f"{SHOP}/{row.id}/buy",
                json={"payment": "wallet", "idempotency_key": KEY},
            )
        ).json()
        res = await call(app, "POST", f"{SHOP}/grants/{bought['id']}/cancel")
        twice = await call(app, "POST", f"{SHOP}/grants/{bought['id']}/cancel")
        member = await call(
            app, "POST", f"{SHOP}/grants/{bought['id']}/cancel", email="ravi@shree.example"
        )
    assert res.status_code == 200, res.text
    assert res.json()["cancelled_at"] is not None and res.json()["active"] is True
    assert res.json()["ends_at"] == bought["ends_at"]  # still in force
    assert twice.status_code == 409 and twice.json()["code"] == "add_on_not_renewing"
    assert member.status_code == 403 and member.json()["code"] == "owner_required"
    assert [a.details["grant_id"] for a in world.audits("billing.add_on_cancelled")] == [
        bought["id"]
    ]


async def test_something_that_does_not_renew_has_nothing_to_cancel(world: World) -> None:
    row = item(world)
    with app_for(world) as app:
        bought = (
            await call(app, "POST", f"{SHOP}/{row.id}/buy", json={"idempotency_key": KEY})
        ).json()
        res = await call(app, "POST", f"{SHOP}/grants/{bought['id']}/cancel")
        other = await call(
            app, "POST", f"{SHOP}/grants/{bought['id']}/cancel", email="meena@pune.example"
        )
    assert res.status_code == 409 and res.json()["code"] == "add_on_not_renewing"
    assert other.status_code == 404 and other.json()["code"] == "grant_not_found"


# --------------------------------------------------------------- paying online (P1 orders)


@pytest.mark.parametrize("flags", [{"sold_to_owners": False}, {"placeholder": True}])
async def test_an_online_order_refuses_what_is_not_in_the_shop(
    world: World, flags: dict[str, bool]
) -> None:
    row = item(world, **flags)
    with app_for(world) as app:
        quote = await call(
            app,
            "POST",
            "/api/v1/billing/payments/quote",
            json={"purpose": "add_on", "add_on_id": str(row.id)},
        )
        order = await call(
            app,
            "POST",
            "/api/v1/billing/payments/orders",
            json={"purpose": "add_on", "add_on_id": str(row.id), "idempotency_key": KEY},
        )
    for res in (quote, order):
        assert res.status_code == 409 and res.json()["code"] == "add_on_not_sellable"


async def test_an_online_order_for_a_shop_service_carries_its_own_sac(world: World) -> None:
    from callobuzz_rag.accounts.settings_store import EngineSettings
    from callobuzz_rag.billing.payments import orders

    row, plain = item(world), item(world, name="Priority support", sac_code=None)
    with app_for(world) as app:
        res = await call(
            app,
            "POST",
            "/api/v1/billing/payments/quote",
            json={"purpose": "add_on", "add_on_id": str(row.id)},
        )
        engine = EngineSettings.model_validate(SELLER)
        own = await orders.quote(
            world.db.session(),
            world.shree.id,
            engine,
            purpose="add_on",
            amount_minor=None,
            add_on_id=row.id,
        )
        default = await orders.quote(
            world.db.session(),
            world.shree.id,
            engine,
            purpose="add_on",
            amount_minor=None,
            add_on_id=plain.id,
        )
    assert res.status_code == 200, res.text
    assert res.json()["taxable_minor"] == 199_900 and res.json()["gross_minor"] == 235_882
    assert own.sac_code == "998313"  # the service's own
    assert default.sac_code == engine.invoicing.sac_default  # else the seller's default


async def test_an_online_order_for_a_feature_the_plan_has_is_409(world: World) -> None:
    row = own_key(world)
    with app_for(world, allowed=True) as app:
        res = await call(
            app,
            "POST",
            "/api/v1/billing/payments/quote",
            json={"purpose": "add_on", "add_on_id": str(row.id)},
        )
    assert res.status_code == 409 and res.json()["code"] == "feature_already_included"


# ------------------------------------------------------------------------ the demo and keys


@pytest.mark.parametrize(
    "path",
    [f"{SHOP}/{{add_on_id}}/buy", f"{SHOP}/grants/{{grant_id}}/cancel"],
)
def test_the_demo_refuses_buying_and_cancelling(path: str) -> None:
    assert (
        refusal("POST", path.format(add_on_id=uuid.uuid4(), grant_id=uuid.uuid4()), admin_key=False)
        == "billing"
    )
    assert refusal("GET", SHOP, admin_key=False) is None


async def test_a_knowledge_base_key_and_the_admin_principal_have_no_shop(world: World) -> None:
    from tests.unit.test_auth_routes import ADMIN

    with app_for(world) as app:
        res = await req(app, "GET", SHOP, key=ADMIN)
    assert res.status_code == 403 and res.json()["code"] == "no_workspace"


# ------------------------------------------------------------------ the owner's own-key status


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            (False, True, None),
            {"allowed": False, "key_saved": True, "source": None, "plans_with_byok": ["Pro"]},
        ),
        (
            (True, False, "plan"),
            {"allowed": True, "key_saved": False, "source": "plan", "plans_with_byok": []},
        ),
        (
            (True, True, "add_on"),
            {"allowed": True, "key_saved": True, "source": "add_on", "plans_with_byok": []},
        ),
    ],
)
async def test_the_billing_view_says_whether_the_own_key_may_be_used_and_is_saved(
    world: World, status: tuple[Any, ...], expected: dict[str, Any]
) -> None:
    with (
        app_for(world) as app,
        patch("callobuzz_rag.api.routes.billing.own_key_status", AsyncMock(return_value=status)),
        patch("callobuzz_rag.api.routes.billing.plans_with_byok", AsyncMock(return_value=["Pro"])),
    ):
        owner = await call(app, "GET", "/api/v1/billing/account")
        member = await call(app, "GET", "/api/v1/billing/account", email="ravi@shree.example")
    assert owner.status_code == 200, owner.text
    assert owner.json()["byok"] == expected
    assert member.json()["byok"] == expected  # a status, not money
