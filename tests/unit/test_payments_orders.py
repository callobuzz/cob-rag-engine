"""Quotes and checkout orders through the real app (P1 T5). The provider is the fake."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.accounts.settings_store import SETTINGS_ROW_ID, invalidate_engine_settings_cache
from callobuzz_rag.billing import config as billing_config
from callobuzz_rag.billing.payments.fake import FakePaymentProvider
from callobuzz_rag.db.models.add_on import AddOn
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.payment import BillingProfile, PaymentOrder
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import ADMIN, World, auth_app, login, req

QUOTE = "/api/v1/billing/payments/quote"
ORDERS = "/api/v1/billing/payments/orders"
CONFIG = "/api/v1/billing/payments/config"
SELLER = {
    "company": {"legal_name": "Callobuzz", "state_code": "27", "city": "Pune"},
    "invoicing": {"gstin": "27AAPFU0939F1ZV"},
}


def engine_settings(world: World, value: dict[str, Any]) -> None:
    invalidate_engine_settings_cache()
    world.db.add(EngineSettingsRow(id=SETTINGS_ROW_ID, value=value))


def profile(world: World, *, state: str = "27", **kw: Any) -> BillingProfile:
    return world.db.add(
        BillingProfile(
            tenant_id=world.shree.id,
            legal_name="Sharma Furniture",
            address_line1="Paud Road, Kothrud",
            city="Pune",
            state_code=state,
            country="IN",
            **kw,
        )
    )


@pytest.fixture
def world() -> World:
    w = World()
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("ravi@shree.example"), w.shree, "member")
    w.member(w.user("meena@pune.example"), w.pune, "owner")
    w.db.add(
        stored_pricing(
            w.db.default_plan,
            subscription_fee_minor=99_900,
            usage_mode="per_question",
            per_question_minor=200,
        )
    )
    engine_settings(w, SELLER)
    profile(w)
    return w


@contextmanager
def app_for(world: World, *, fake: FakePaymentProvider | None = None) -> Iterator[Any]:
    provider = fake if fake is not None else FakePaymentProvider()
    with auth_app(world) as app, fake_accounts(world.db):
        app.state.payment_provider_override = provider
        app.state.fake = provider
        yield app


async def post(app: Any, url: str, body: dict[str, Any], email: str = "asha@shree.example") -> Any:
    token = (await login(app, email)).json()["access_token"]
    return await req(app, "POST", url, token=token, json=body)


def top_up(amount: int = 100_000, key: str = "order-key-0001") -> dict[str, Any]:
    return {"purpose": "top_up", "amount_minor": amount, "idempotency_key": key}


async def test_top_up_in_the_sellers_state_splits_cgst_and_sgst(world: World) -> None:
    with app_for(world) as app:
        res = await post(app, QUOTE, {"purpose": "top_up", "amount_minor": 100_000})
    q = res.json()
    assert res.status_code == 200, res.text
    assert (q["taxable_minor"], q["cgst_minor"], q["sgst_minor"], q["igst_minor"]) == (
        100_000,
        9_000,
        9_000,
        0,
    )
    assert q["gross_minor"] == 118_000 and q["tax_kind"] == "intra" and q["place_of_supply"] == "27"


async def test_top_up_in_another_state_is_igst(world: World) -> None:
    world.db.rows[BillingProfile].clear()
    profile(world, state="29")
    with app_for(world) as app:
        q = (await post(app, QUOTE, {"purpose": "top_up", "amount_minor": 100_000})).json()
    assert (q["igst_minor"], q["gross_minor"], q["tax_kind"]) == (18_000, 118_000, "inter")


async def test_a_seller_without_a_gstin_charges_no_tax(world: World) -> None:
    engine_settings_clear(world)
    engine_settings(world, {"company": SELLER["company"]})
    with app_for(world) as app:
        q = (await post(app, QUOTE, {"purpose": "top_up", "amount_minor": 100_000})).json()
    assert (q["tax_kind"], q["gross_minor"], q["tax_minor"]) == ("no_gst", 100_000, 0)


def engine_settings_clear(world: World) -> None:
    world.db.rows[EngineSettingsRow].clear()


async def test_inclusive_prices_take_the_typed_amount_as_the_total(world: World) -> None:
    engine_settings_clear(world)
    engine_settings(
        world, {**SELLER, "invoicing": {**SELLER["invoicing"], "prices_include_gst": True}}
    )
    with app_for(world) as app:
        q = (await post(app, QUOTE, {"purpose": "top_up", "amount_minor": 118_000})).json()
    assert (q["taxable_minor"], q["gross_minor"]) == (100_000, 118_000)


@pytest.mark.parametrize("amount", [49_900, 100_000_000])
async def test_amounts_out_of_range_are_422_with_the_limits(world: World, amount: int) -> None:
    with app_for(world) as app:
        res = await post(app, QUOTE, {"purpose": "top_up", "amount_minor": amount})
    assert res.status_code == 422 and res.json()["code"] == "amount_out_of_range"
    assert res.json()["min_minor"] == 50_000 and res.json()["max_minor"] == 20_000_000


async def test_the_plan_fee_is_a_quote_named_after_the_plan_and_month(world: World) -> None:
    with app_for(world) as app:
        q = (await post(app, QUOTE, {"purpose": "plan"})).json()
    assert q["taxable_minor"] == 99_900 and q["gross_minor"] == 117_882
    assert q["description"] == f"Standard plan, {datetime.now(UTC):%b %Y}"


async def test_a_plan_without_a_fee_has_nothing_to_pay(world: World) -> None:
    world.db.rows.pop(next(k for k in world.db.rows if k.__name__ == "PlanPricing"))
    world.db.add(stored_pricing(world.db.default_plan, subscription_fee_minor=0))
    with app_for(world) as app:
        res = await post(app, QUOTE, {"purpose": "plan"})
    assert res.status_code == 409 and res.json()["code"] == "plan_has_no_fee"


def add_on(world: World, **kw: Any) -> AddOn:
    values: dict[str, Any] = {
        "kind": "credit_pack",
        "name": "Credit pack",
        "currency": "INR",
        "price_minor": 90_000,
        "credit_minor": 100_000,
        "bonus_pct": 0,
        "is_active": True,
        "sold_to_owners": True,  # P5 plans: an owner pays only for what is in the shop
    }
    return world.db.add(AddOn(**{**values, **kw}))


async def test_a_credit_pack_is_quoted_at_its_price(world: World) -> None:
    pack = add_on(world)
    with app_for(world) as app:
        q = (await post(app, QUOTE, {"purpose": "add_on", "add_on_id": str(pack.id)})).json()
    assert q["taxable_minor"] == 90_000 and q["description"].startswith("Credit pack ₹1,000")


async def test_an_inactive_add_on_cannot_be_bought(world: World) -> None:
    pack = add_on(world, is_active=False)
    with app_for(world) as app:
        res = await post(app, QUOTE, {"purpose": "add_on", "add_on_id": str(pack.id)})
        missing = await post(app, QUOTE, {"purpose": "add_on"})
    assert res.status_code == 409 and res.json()["code"] == "add_on_not_sellable"
    assert missing.json()["code"] == "add_on_not_sellable"


async def test_no_tax_details_no_payment(world: World) -> None:
    world.db.rows[BillingProfile].clear()
    with app_for(world) as app:
        res = await post(app, ORDERS, top_up())
        config = await post_get(app, CONFIG)
    assert res.status_code == 409 and res.json()["code"] == "tax_details_required"
    assert config.json()["tax_details_complete"] is False
    assert not world.db.all(PaymentOrder)


async def post_get(app: Any, url: str, email: str = "asha@shree.example") -> Any:
    token = (await login(app, email)).json()["access_token"]
    return await req(app, "GET", url, token=token)


async def test_a_workspace_billed_in_dollars_is_refused(world: World) -> None:
    with app_for(world) as app:
        await post(app, QUOTE, {"purpose": "plan"})  # creates the account
        [account] = world.db.all(BillingAccount)
        account.currency = "USD"
        account.ledger_seq = 1  # the currency is locked once there is an entry
        res = await post(app, QUOTE, {"purpose": "plan"})
    assert res.status_code == 409 and res.json()["code"] == "payments_currency_unsupported"


async def test_the_operators_own_workspace_is_never_billed(world: World) -> None:
    world.shree.is_operator = True
    with app_for(world) as app:
        res = await post(app, QUOTE, {"purpose": "plan"})
    assert res.status_code == 409 and res.json()["code"] == "billing_exempt"


async def test_payments_off_is_404(world: World) -> None:
    with auth_app(world) as app, fake_accounts(world.db):
        res = await post(app, ORDERS, top_up())
        config = await post_get(app, CONFIG)
    assert res.status_code == 404 and res.json()["code"] == "payments_disabled"
    assert config.status_code == 200 and config.json()["enabled"] is False


async def test_billing_off_is_404(world: World) -> None:
    with patch.dict(os.environ, {"RAG_BILLING_ENABLED": "false"}):
        billing_config.reset_billing_config_cache()
        try:
            with app_for(world) as app:
                res = await post(app, ORDERS, top_up())
        finally:
            billing_config.reset_billing_config_cache()
    assert res.status_code == 404 and res.json()["code"] == "billing_disabled"


async def test_an_order_is_created_once_per_idempotency_key(world: World) -> None:
    fake = FakePaymentProvider()
    with app_for(world, fake=fake) as app:
        first = await post(app, ORDERS, top_up())
        again = await post(app, ORDERS, top_up())
        other_amount = await post(app, ORDERS, top_up(amount=200_000))
    assert first.status_code == 201, first.text
    assert again.json()["order_id"] == first.json()["order_id"]
    assert fake.api_calls.count("create_order") == 1
    assert other_amount.status_code == 409 and other_amount.json()["code"] == "idempotency_conflict"
    [order] = world.db.all(PaymentOrder)
    assert order.status == "created" and order.gross_minor == 118_000
    assert order.provider_order_id in fake.orders


async def test_the_provider_order_carries_our_ids_and_the_receipt(world: World) -> None:
    fake = FakePaymentProvider()
    with app_for(world, fake=fake) as app:
        res = await post(app, ORDERS, top_up())
    [order] = world.db.all(PaymentOrder)
    held = fake.orders[order.provider_order_id]
    assert held.order.receipt == str(order.id) and len(held.order.receipt) <= 40
    assert held.notes == {"cob_order": str(order.id), "cob_account": str(order.account_id)}
    assert held.order.amount_minor == 118_000
    assert res.json()["order_id"] == str(order.id)


async def test_checkout_params_have_the_public_key_and_no_secret(world: World) -> None:
    engine_settings_clear(world)
    engine_settings(
        world, {**SELLER, "branding": {"product_name": "Sahayak", "primary_color": "#ff6600"}}
    )
    fake = FakePaymentProvider()
    with app_for(world, fake=fake) as app:
        res = await post(app, ORDERS, top_up())
    body = res.json()
    c = body["checkout"]
    assert c["key"] == "rzp_test_fake" and c["amount"] == 118_000 and c["currency"] == "INR"
    assert c["name"] == "Sahayak" and c["theme"] == {"color": "#ff6600"}
    assert c["prefill"]["email"] == "asha@shree.example"
    assert c["description"] == "Add money to your balance"
    assert c["notes"]["cob_order"] == body["order_id"]
    assert "key_secret_fake" not in res.text and "whsec_fake" not in res.text
    assert body["mode"] == "test" and body["quote"]["gross_minor"] == 118_000


async def test_a_provider_failure_keeps_no_order_and_the_key_is_reusable(world: World) -> None:
    from callobuzz_rag.billing.payments.base import ProviderError

    fake = FakePaymentProvider()
    fake.down = ProviderError("unreachable", "down")
    with app_for(world, fake=fake) as app:
        failed = await post(app, ORDERS, top_up())
        fake.down = None
        retried = await post(app, ORDERS, top_up())
    assert failed.status_code == 502 and failed.json()["code"] == "payments_unavailable"
    assert retried.status_code == 201 and len(world.db.all(PaymentOrder)) == 1


async def test_orders_are_per_workspace(world: World) -> None:
    profile_pune = BillingProfile(
        tenant_id=world.pune.id,
        legal_name="Pune Agency",
        address_line1="FC Road",
        city="Pune",
        state_code="27",
        country="IN",
    )
    world.db.add(profile_pune)
    with app_for(world) as app:
        a = await post(app, ORDERS, top_up(key="shared-key-0001"))
        b = await post(app, ORDERS, top_up(key="shared-key-0001"), email="meena@pune.example")
    assert a.status_code == b.status_code == 201
    assert a.json()["order_id"] != b.json()["order_id"]


async def test_a_member_and_the_admin_cannot_pay(world: World) -> None:
    with app_for(world) as app:
        member = await post(app, ORDERS, top_up(), email="ravi@shree.example")
        admin = await req(app, "POST", ORDERS, key=ADMIN, json=top_up())
    assert member.status_code == 403 and member.json()["code"] == "owner_required"
    assert admin.status_code == 403 and admin.json()["code"] == "no_workspace"


@pytest.mark.parametrize(
    "body",
    [
        {"purpose": "top_up", "amount_minor": 1.5, "idempotency_key": "order-key-0001"},
        {"purpose": "top_up", "amount_minor": 0, "idempotency_key": "order-key-0001"},
        {"purpose": "gift", "amount_minor": 50_000, "idempotency_key": "order-key-0001"},
        {"purpose": "top_up", "amount_minor": 50_000, "idempotency_key": "short"},
        {"purpose": "top_up", "amount_minor": 50_000, "idempotency_key": "order-key-0001", "x": 1},
    ],
)
async def test_bad_bodies_are_422(world: World, body: dict[str, Any]) -> None:
    with app_for(world) as app:
        res = await post(app, ORDERS, body)
    assert res.status_code == 422


async def test_the_plan_fee_is_taxed_on_top_even_when_other_prices_include_gst(
    world: World,
) -> None:
    engine_settings_clear(world)
    engine_settings(
        world, {**SELLER, "invoicing": {**SELLER["invoicing"], "prices_include_gst": True}}
    )
    with app_for(world) as app:
        q = (await post(app, QUOTE, {"purpose": "plan"})).json()
    assert q["taxable_minor"] == 99_900 and q["gross_minor"] == 117_882
