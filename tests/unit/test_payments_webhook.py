"""The signed webhook route (P1 T7): verify, store by event id, answer 200, do nothing else."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.api.permissions import PUBLIC_ROUTES
from callobuzz_rag.billing.payments import events
from callobuzz_rag.billing.payments.fake import FakePaymentProvider
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.db.models.payment import PaymentEvent
from callobuzz_rag.db.models.wallet_ledger import WalletLedger
from tests.unit.test_auth_routes import World, auth_app

URL = "/api/v1/public/payments/razorpay/webhook"
ENC_KEY = Fernet.generate_key().decode()


@pytest.fixture
def world() -> World:
    invalidate_engine_settings_cache()
    return World()


@contextmanager
def app_for(
    world: World, fake: FakePaymentProvider | None, monkeypatch: pytest.MonkeyPatch, **settings: Any
) -> Iterator[tuple[Any, list[str]]]:
    queued: list[str] = []
    monkeypatch.setattr(events, "queue_event", queued.append)
    with auth_app(world, **settings) as app:
        if fake is not None:
            app.state.payment_provider_override = fake
        yield app, queued


async def deliver(app: Any, body: bytes, headers: dict[str, str]) -> Any:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(URL, content=body, headers=headers)


async def test_a_signed_event_is_stored_encrypted_and_queued_once(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakePaymentProvider()
    event = fake.event("payment.captured", payment="pay_abc123")
    with app_for(world, fake, monkeypatch, encryption_key=ENC_KEY) as (app, queued):
        res = await deliver(app, event.body, event.headers)
        again = await deliver(app, event.body, event.headers)
    assert res.status_code == 200 and res.json() == {}
    assert again.status_code == 200
    [row] = world.db.all(PaymentEvent)
    assert row.status == "pending" and row.event == "payment.captured"
    assert row.event_id == event.event_id and row.entity_ids["payment"] == "pay_abc123"
    assert "asha@shree.example" not in row.payload_encrypted  # the customer's e-mail is encrypted
    assert FernetEncryptor(ENC_KEY).decrypt(row.payload_encrypted).startswith('{"entity"')
    assert queued == [event.event_id]  # the duplicate queued nothing


async def test_the_route_makes_no_provider_call_and_writes_no_money(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakePaymentProvider()
    event = fake.event("payment.captured", payment="pay_abc123", amount_override=1)
    with app_for(world, fake, monkeypatch, encryption_key=ENC_KEY) as (app, _):
        await deliver(app, event.body, event.headers)
    assert fake.api_calls == []
    assert not world.db.all(WalletLedger)


@pytest.mark.parametrize("which", ["wrong", "missing", "altered_body", "no_event_id"])
async def test_a_bad_signature_or_no_event_id_is_400_and_nothing_is_stored(
    world: World, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    fake = FakePaymentProvider()
    event = fake.event("payment.captured", payment="pay_abc123")
    headers = dict(event.headers)
    body = event.body
    if which == "wrong":
        headers["x-razorpay-signature"] = "0" * 64
    elif which == "missing":
        del headers["x-razorpay-signature"]
    elif which == "altered_body":
        body = body + b" "
    else:
        del headers["x-razorpay-event-id"]
    with app_for(world, fake, monkeypatch, encryption_key=ENC_KEY) as (app, queued):
        res = await deliver(app, body, headers)
    assert res.status_code == 400 and res.json()["code"] == "payments_signature_invalid"
    assert not world.db.all(PaymentEvent) and queued == []


async def test_a_body_over_one_mebibyte_is_413(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakePaymentProvider()
    event = fake.event("payment.captured", payment="pay_abc123")
    big = b"x" * (1024 * 1024 + 1)
    with app_for(world, fake, monkeypatch, encryption_key=ENC_KEY) as (app, _):
        res = await deliver(app, big, event.headers)
    assert res.status_code == 413 and res.json()["code"] == "payload_too_large"
    assert not world.db.all(PaymentEvent)


async def test_payments_off_is_404(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePaymentProvider()
    event = fake.event("payment.captured", payment="pay_abc123")
    with app_for(world, None, monkeypatch, encryption_key=ENC_KEY) as (app, _):
        res = await deliver(app, event.body, event.headers)
    assert res.status_code == 404 and res.json()["code"] == "payments_disabled"


async def test_no_encryption_key_is_503(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePaymentProvider()
    event = fake.event("payment.captured", payment="pay_abc123")
    with app_for(world, fake, monkeypatch) as (app, queued):
        res = await deliver(app, event.body, event.headers)
    assert res.status_code == 503 and res.json()["code"] == "encryption_unavailable"
    assert not world.db.all(PaymentEvent) and queued == []


async def test_it_needs_no_key_and_is_a_public_route(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert ("POST", "/api/v1/public/payments/razorpay/webhook") in PUBLIC_ROUTES
    fake = FakePaymentProvider()
    event = fake.event("payment.captured", payment="pay_abc123")
    headers = {k: v for k, v in event.headers.items() if k != "authorization"}
    with app_for(world, fake, monkeypatch, encryption_key=ENC_KEY) as (app, _):
        res = await deliver(app, event.body, headers)  # no X-API-Key, no bearer token
    assert res.status_code == 200
