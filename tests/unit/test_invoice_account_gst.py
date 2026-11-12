# ruff: noqa: F811, F401
"""P2 T7: the account view says whether prices carry GST; a quote says which document it makes."""

from callobuzz_rag.billing import config as billing_config
from tests.unit.test_auth_routes import World, login, req
from tests.unit.test_billing_owner_api import URL as ACCOUNT_URL
from tests.unit.test_billing_owner_api import app_for as account_app
from tests.unit.test_billing_owner_api import world as account_world
from tests.unit.test_payments_orders import QUOTE, SELLER, app_for, engine_settings, post
from tests.unit.test_payments_orders import world as orders_world


async def test_account_view_carries_the_gst_flags(account_world: World) -> None:
    account_world.member(account_world.user("asha@shree.example"), account_world.shree, "owner")
    engine_settings_value = {"invoicing": {"gstin": "27AAPFU0939F1ZV", "prices_include_gst": True}}
    from callobuzz_rag.accounts.settings_store import (
        SETTINGS_ROW_ID,
        invalidate_engine_settings_cache,
    )
    from callobuzz_rag.db.models.engine_settings import EngineSettingsRow

    invalidate_engine_settings_cache()
    account_world.db.add(EngineSettingsRow(id=SETTINGS_ROW_ID, value=engine_settings_value))
    with account_app(account_world) as app:
        token = (await login(app, "asha@shree.example")).json()["access_token"]
        res = await req(app, "GET", ACCOUNT_URL, token=token)
    assert res.status_code == 200, res.text
    assert res.json()["gst"] == {"registered": True, "prices_include_gst": True}


async def test_a_seller_without_a_gstin_is_not_registered(account_world: World) -> None:
    account_world.member(account_world.user("asha@shree.example"), account_world.shree, "owner")
    with account_app(account_world) as app:
        token = (await login(app, "asha@shree.example")).json()["access_token"]
        res = await req(app, "GET", ACCOUNT_URL, token=token)
    assert res.json()["gst"] == {"registered": False, "prices_include_gst": False}


async def test_a_quote_names_the_document_it_will_produce(orders_world: World) -> None:
    with app_for(orders_world) as app:
        invoice = await post(app, QUOTE, {"purpose": "top_up", "amount_minor": 100_000})
    assert invoice.json()["document_type"] == "invoice"
    # no GST number: replace the seller the fixture saved
    from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
    from callobuzz_rag.db.models.engine_settings import EngineSettingsRow

    orders_world.db.all(EngineSettingsRow)[0].value = {"company": SELLER["company"]}
    invalidate_engine_settings_cache()
    with app_for(orders_world) as app:
        bill = await post(app, QUOTE, {"purpose": "top_up", "amount_minor": 100_000})
    assert bill.json()["tax_kind"] == "no_gst" and bill.json()["document_type"] == "bill_of_supply"
