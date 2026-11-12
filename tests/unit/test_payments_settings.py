"""Operator payment settings: encrypted keys, env override, test connection, the fake (P1 T4)."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest
from cryptography.fernet import Fernet

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.api.routes import payments_admin
from callobuzz_rag.billing.payments import factory
from callobuzz_rag.billing.payments import settings as payment_settings
from callobuzz_rag.billing.payments.base import ProviderError, SettlementLine
from callobuzz_rag.billing.payments.fake import FakePaymentProvider
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.db.models.payment import PaymentProviderSetting
from tests.unit.test_auth_routes import ADMIN, World, auth_app, req

URL = "/api/v1/admin/billing/payments/settings"
ENC_KEY = Fernet.generate_key().decode()
SECRET = "sup3r-secret-key-value"
WEBHOOK_SECRET = "whsec_sup3r"
KEY_ID = "rzp_test_AbCdEf123456"


@pytest.fixture
def world() -> World:
    invalidate_engine_settings_cache()
    payments_admin._last_test[0] = 0.0
    return World()


@contextmanager
def app_for(world: World, **settings: Any) -> Iterator[Any]:
    with auth_app(world, encryption_key=ENC_KEY, **settings) as app:
        yield app


async def put(app: Any, **body: Any) -> Any:
    return await req(app, "PUT", URL, key=ADMIN, json={"provider": "razorpay", **body})


async def test_keys_are_stored_encrypted_and_never_returned(world: World) -> None:
    with app_for(world) as app:
        res = await put(app, key_id=KEY_ID, key_secret=SECRET, webhook_secret=WEBHOOK_SECRET)
        got = await req(app, "GET", URL, key=ADMIN)
    assert res.status_code == 200, res.text
    for body in (res.text, got.text):
        assert SECRET not in body and WEBHOOK_SECRET not in body and 'key_secret":' not in body
    out = got.json()
    assert out["key_secret_set"] and out["webhook_secret_set"] and out["mode"] == "test"
    assert out["source"] == "stored" and out["enabled"] is True
    assert out["webhook_url"].endswith("/api/v1/public/payments/razorpay/webhook")
    assert "payment.captured" in out["events"] and "refund.processed" in out["events"]
    [row] = world.db.all(PaymentProviderSetting)
    assert SECRET not in (row.key_secret_encrypted or "")
    enc = FernetEncryptor(ENC_KEY)
    assert enc.decrypt(row.key_secret_encrypted) == SECRET
    assert enc.decrypt(row.webhook_secret_encrypted) == WEBHOOK_SECRET
    [audit] = world.audits("payments.settings_updated")
    assert audit.details["key_secret"] == "set" and audit.details["webhook_secret"] == "set"
    assert SECRET not in json.dumps(audit.details) and "key_id" in audit.details["changed"]


async def test_omitting_a_secret_keeps_it_and_empty_clears_it(world: World) -> None:
    with app_for(world) as app:
        await put(app, key_id=KEY_ID, key_secret=SECRET, webhook_secret=WEBHOOK_SECRET)
        kept = await put(app, min_top_up_minor=100_000)
        cleared = await put(app, webhook_secret="")
    assert kept.json()["key_secret_set"] and kept.json()["webhook_secret_set"]
    assert cleared.json()["key_secret_set"] and not cleared.json()["webhook_secret_set"]
    assert kept.json()["min_top_up_minor"] == 100_000


async def test_env_wins_over_the_stored_row(world: World) -> None:
    with app_for(
        world,
        payments_provider="razorpay",
        razorpay_key_id="rzp_live_EnvKey123456",
        razorpay_key_secret="env-secret",
    ) as app:
        await put(app, key_id=KEY_ID, key_secret=SECRET)
        got = await req(app, "GET", URL, key=ADMIN)
    out = got.json()
    assert out["source"] == "env" and out["key_id"] == "rzp_live_EnvKey123456"
    assert out["mode"] == "live"
    assert "env-secret" not in got.text


async def test_saving_secrets_without_an_encryption_key_is_503(world: World) -> None:
    with auth_app(world) as app:
        res = await put(app, key_id=KEY_ID, key_secret=SECRET)
    assert res.status_code == 503 and res.json()["code"] == "encryption_unavailable"
    assert not world.db.all(PaymentProviderSetting)


@pytest.mark.parametrize(
    "body",
    [
        {"key_id": "pk_live_whatever"},
        {"min_top_up_minor": 50},
        {"min_top_up_minor": 900_000, "max_payment_minor": 500_000},
        {"surprise": 1},
        {"provider": "stripe"},
    ],
)
async def test_bad_settings_are_422(world: World, body: dict[str, Any]) -> None:
    with app_for(world) as app:
        res = await req(app, "PUT", URL, key=ADMIN, json={"provider": "razorpay", **body})
    assert res.status_code == 422, res.text


async def test_only_the_admin_may_read_or_write(world: World) -> None:
    owner = world.user("asha@shree.example")
    world.member(owner, world.shree, "owner")
    from tests.unit.test_auth_routes import login

    with app_for(world) as app:
        token = (await login(app, "asha@shree.example")).json()["access_token"]
        res = await req(app, "GET", URL, token=token)
        anon = await req(app, "GET", URL)
    assert res.status_code == 403 and anon.status_code == 401


async def test_test_connection_uses_the_injected_provider_and_is_rate_limited(
    world: World,
) -> None:
    fake = FakePaymentProvider()
    with app_for(world) as app:
        app.state.payment_provider_override = fake
        ok = await req(app, "POST", URL + "/test", key=ADMIN)
        again = await req(app, "POST", URL + "/test", key=ADMIN)
        payments_admin._last_test[0] = 0.0
        fake.bad_auth = True
        bad = await req(app, "POST", URL + "/test", key=ADMIN)
    assert ok.status_code == 200 and ok.json() == {"ok": True, "mode": "test"}
    assert again.status_code == 429 and again.json()["code"] == "payments_test_too_soon"
    assert "retry-after" in {k.lower() for k in again.headers}
    assert bad.status_code == 422 and bad.json()["code"] == "payments_test_failed"
    assert bad.json()["reason"] == "auth"


async def test_test_connection_without_keys_says_so(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "POST", URL + "/test", key=ADMIN)
    assert res.status_code == 422 and res.json()["reason"] == "not_configured"


def test_resolved_settings_need_provider_and_keys() -> None:
    off = payment_settings.ResolvedSettings("none", KEY_ID, SECRET, "", "stored")
    on = payment_settings.ResolvedSettings("razorpay", KEY_ID, SECRET, "", "stored")
    missing = payment_settings.ResolvedSettings("razorpay", KEY_ID, "", "", "stored")
    assert factory.payment_provider(off) is None and factory.payment_provider(missing) is None
    provider = factory.payment_provider(on)
    assert provider is not None and provider.mode == "test"
    bad = payment_settings.ResolvedSettings("razorpay", "nonsense", SECRET, "", "stored")
    assert factory.payment_provider(bad) is None


# ------------------------------------------------------------------ the fake


async def test_the_fake_signs_exactly_like_the_real_client() -> None:
    fake = FakePaymentProvider()
    order = await fake.create_order(
        amount_minor=118_000, currency="INR", receipt="r1", notes={"cob_order": "o1"}
    )
    info = fake.capture(order.provider_order_id, method="upi")
    assert fake.verify_checkout(
        provider_order_id=order.provider_order_id,
        provider_payment_id=info.provider_payment_id,
        signature=fake.sign_checkout(order.provider_order_id, info.provider_payment_id),
    )
    event = fake.event("payment.captured", payment=info.provider_payment_id)
    assert fake.verify_webhook(event.body, event.headers["x-razorpay-signature"])
    parsed = fake.parse_webhook(event.body, event.headers)
    assert parsed is not None and parsed.event_id == event.event_id
    assert parsed.entity_ids["payment"] == info.provider_payment_id
    assert info.fee_minor == 2_360 + 425 and info.notes == {"cob_order": "o1"}
    assert (await fake.fetch_payment(info.provider_payment_id)) == info


async def test_the_fake_records_calls_and_can_be_taken_down() -> None:
    fake = FakePaymentProvider()
    fake.down = ProviderError("unreachable", "down")
    with pytest.raises(ProviderError):
        await fake.fetch_payment("pay_x")
    assert fake.api_calls == ["fetch_payment"]
    fake.down = None
    fake.add_settlement(
        date(2026, 10, 7), SettlementLine("payment", "pay_1", 100, 2, 0, "setl_1", None)
    )
    assert len(await fake.settlement_lines(date(2026, 10, 7))) == 1
