"""Manual entries and the owner's transactions (BIL3): validation, confirmation, idempotency,
refund limit, audit, scoping. The ledger SQL itself runs on PostgreSQL in
`tests/integration/test_billing_ledger_api_db.py`; here `append_entries` is a stand-in that keeps
rows in the in-memory database."""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.billing import config as billing_config
from callobuzz_rag.billing import ledger
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.wallet_ledger import WalletLedger
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import ADMIN, World, auth_app, req

LEDGER = "/api/v1/admin/billing/accounts/{}/ledger"
KEY = "0123456789abcdef"


@pytest.fixture
def world() -> World:
    return World()


def fake_append(world: World):  # type: ignore[no-untyped-def]
    async def append(session, account_id, entries, *, actor, now=None):  # type: ignore[no-untyped-def]
        out = []
        for e in entries:
            rows = [r for r in world.db.all(WalletLedger) if r.account_id == account_id]
            existing = next((r for r in rows if r.idempotency_key == e.idempotency_key), None)
            if existing is not None:
                if (
                    existing.entry_type != e.entry_type.value
                    or existing.amount_minor != e.amount_minor
                ):
                    raise ledger.IdempotencyConflict(e.idempotency_key)
                out.append(
                    ledger.AppendResult(
                        existing.id, existing.seq, existing.balance_after_minor, False
                    )
                )
                continue
            account = next(a for a in world.db.all(BillingAccount) if a.id == account_id)
            account.balance_minor += e.amount_minor
            account.ledger_seq += 1
            row = world.db.add(
                WalletLedger(
                    id=uuid.uuid4(),
                    account_id=account_id,
                    tenant_id=account.tenant_id,
                    seq=account.ledger_seq,
                    entry_type=e.entry_type.value,
                    amount_minor=e.amount_minor,
                    currency=account.currency,
                    balance_after_minor=account.balance_minor,
                    idempotency_key=e.idempotency_key,
                    memo=e.memo,
                    memo_public=e.memo_public,
                    period=date(2026, 10, 1),
                    actor_kind=actor[0],
                    actor_label=actor[2],
                    created_at=datetime(2026, 10, 4, tzinfo=UTC),
                )
            )
            out.append(ledger.AppendResult(row.id, row.seq, row.balance_after_minor, True))
        return out

    return append


@contextmanager
def app_for(
    world: World, *, available: int = 100_000, live_credit: int = 0, paid: int = 10**9
) -> Iterator[Any]:
    with (
        auth_app(world) as app,
        fake_accounts(world.db),
        patch.object(ledger, "append_entries", fake_append(world)),
        patch.object(ledger, "available_minor", AsyncMock(return_value=available)),
        patch("callobuzz_rag.billing.lots.live_credit", AsyncMock(return_value=live_credit)),
        patch.object(ledger, "paid_cash_minor", AsyncMock(return_value=paid)),
        patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()) as hook,
    ):
        app.state.hook = hook
        yield app


async def post(app: Any, tenant: Any, body: dict[str, Any]) -> Any:
    return await req(app, "POST", LEDGER.format(tenant.id), key=ADMIN, json=body)


def top_up(amount: int = 50_000, **kw: Any) -> dict[str, Any]:
    return {"entry_type": "top_up", "amount_minor": amount, "idempotency_key": KEY, **kw}


async def test_a_top_up_is_recorded_audited_and_wakes_the_hook(world: World) -> None:
    world.db.add(stored_pricing(world.db.default_plan, currency="INR"))
    with app_for(world) as app:
        res = await post(app, world.shree, top_up(memo_public="Top-up (UPI)"))
        hook_calls = app.state.hook.await_count
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["entry_type"] == "top_up" and body["amount_minor"] == 50_000
    assert body["balance_after_minor"] == 50_000 and body["currency"] == "INR"
    assert body["memo_public"] == "Top-up (UPI)"
    assert body["idempotency_key"] == f"topup:{KEY}"
    assert hook_calls == 1
    [audit] = world.audits("billing.ledger_entry")
    assert audit.details["amount_minor"] == 50_000 and audit.details["currency"] == "INR"
    assert audit.details["entry_id"] == body["id"]
    assert "memo" not in audit.details


async def test_the_same_request_again_is_a_replay_not_a_second_entry(world: World) -> None:
    with app_for(world) as app:
        first = await post(app, world.shree, top_up())
        again = await post(app, world.shree, top_up())
        other_amount = await post(app, world.shree, top_up(50_001))
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert other_amount.status_code == 409 and other_amount.json()["code"] == "idempotency_conflict"
    assert len(world.audits("billing.ledger_entry")) == 1  # the replay wrote no second row


@pytest.mark.parametrize(
    "body",
    [
        {"entry_type": "adjustment", "amount_minor": -500},  # no note
        {"entry_type": "adjustment", "amount_minor": 500, "memo": "  "},
        {"entry_type": "refund", "amount_minor": 500},
        {"entry_type": "adjustment", "amount_minor": 0, "memo": "x"},
        {"entry_type": "top_up", "amount_minor": 0},
        {"entry_type": "top_up", "amount_minor": -5},
        {"entry_type": "refund", "amount_minor": -5, "memo": "x"},
        {"entry_type": "top_up", "amount_minor": 5, "expires_at": "2027-01-01T00:00:00+00:00"},
        {
            "entry_type": "adjustment",
            "amount_minor": -5,
            "memo": "x",
            "expires_at": "2027-01-01T00:00:00Z",
        },
        {
            "entry_type": "adjustment",
            "amount_minor": 5,
            "memo": "x",
            "expires_at": "2027-01-01T00:00:00",
        },
        {"entry_type": "usage_charge", "amount_minor": -5},
        {"entry_type": "top_up", "amount_minor": 5, "surprise": 1},
        {"entry_type": "top_up", "amount_minor": 5.5},
    ],
)
async def test_bodies_that_do_not_fit_are_refused(world: World, body: dict[str, Any]) -> None:
    body = {"idempotency_key": KEY, **body}
    with app_for(world) as app:
        res = await post(app, world.shree, body)
    assert res.status_code == 422, res.text
    assert world.audits("billing.ledger_entry") == []


@pytest.mark.parametrize("key", ["short", "has space 1234", "x" * 65, ""])
async def test_the_idempotency_key_must_be_a_plain_token(world: World, key: str) -> None:
    with app_for(world) as app:
        res = await post(app, world.shree, top_up(idempotency_key=key))
    assert res.status_code == 422


async def test_a_large_amount_needs_the_typed_amount(world: World) -> None:
    big = 1_000_000  # the INR limit
    with app_for(world) as app:
        missing = await post(app, world.shree, top_up(big))
        wrong = await post(app, world.shree, top_up(big, confirm_amount_minor=big + 1))
        below = await post(app, world.shree, top_up(big - 1, idempotency_key="below" * 3))
        ok = await post(app, world.shree, top_up(big, confirm_amount_minor=big))
    assert missing.status_code == 422 and missing.json()["code"] == "confirm_mismatch"
    assert wrong.status_code == 422 and wrong.json()["code"] == "confirm_mismatch"
    assert missing.json()["limit_minor"] == big
    assert below.status_code == 201  # under the limit: no confirmation
    assert ok.status_code == 201


async def test_an_adjustment_may_be_negative_and_a_refund_is_stored_as_money_out(
    world: World,
) -> None:
    with app_for(world, available=10_000, live_credit=0) as app:
        adj = await post(
            app,
            world.shree,
            {
                "entry_type": "adjustment",
                "amount_minor": -700,
                "memo": "double charge",
                "idempotency_key": "adjustment-1",
            },
        )
        refund = await post(
            app,
            world.shree,
            {
                "entry_type": "refund",
                "amount_minor": 300,
                "memo": "asked by e-mail",
                "idempotency_key": "refund-0001",
            },
        )
    assert adj.status_code == 201 and adj.json()["amount_minor"] == -700
    assert adj.json()["memo"] == "double charge"
    assert refund.status_code == 201 and refund.json()["amount_minor"] == -300
    assert refund.json()["entry_type"] == "refund"


async def test_a_refund_is_limited_to_the_paid_balance(world: World) -> None:
    # 1,000 available, 400 of it promotional credit still in lots: 600 is refundable
    body = {"entry_type": "refund", "memo": "m"}
    with app_for(world, available=1_000, live_credit=400) as app:
        too_much = await post(
            app, world.shree, {**body, "amount_minor": 601, "idempotency_key": "refund-0001"}
        )
        exact = await post(
            app, world.shree, {**body, "amount_minor": 600, "idempotency_key": "refund-0002"}
        )
    assert too_much.status_code == 409 and too_much.json()["code"] == "refund_exceeds_balance"
    assert too_much.json()["refundable_minor"] == 600
    assert exact.status_code == 201
    with app_for(world, available=-50, live_credit=0) as app:
        owed = await post(
            app, world.shree, {**body, "amount_minor": 1, "idempotency_key": "refund-0003"}
        )
    assert owed.status_code == 409


async def test_a_retried_refund_replays_even_when_nothing_is_left_to_refund(
    world: World,
) -> None:
    body = {"entry_type": "refund", "memo": "m", "idempotency_key": "refund-0001"}
    with app_for(world, available=500, live_credit=0, paid=500) as app:
        first = await post(app, world.shree, {**body, "amount_minor": 500})
    # the refund is booked: nothing is refundable now, but the same request is still a replay
    with app_for(world, available=0, live_credit=0, paid=0) as app:
        again = await post(app, world.shree, {**body, "amount_minor": 500})
        other = await post(app, world.shree, {**body, "amount_minor": 400})
    assert first.status_code == 201
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    assert other.status_code == 409 and other.json()["code"] == "idempotency_conflict"


async def test_credit_that_never_expires_is_not_refundable_cash(world: World) -> None:
    # 1,000 available, no lots (non-expiring included credit), but nothing was ever paid in
    body = {"entry_type": "refund", "memo": "m", "idempotency_key": "refund-0009"}
    with app_for(world, available=1_000, live_credit=0, paid=0) as app:
        res = await post(app, world.shree, {**body, "amount_minor": 100})
    assert res.status_code == 409 and res.json()["code"] == "refund_exceeds_balance"
    assert res.json()["refundable_minor"] == 0


async def test_a_goodwill_credit_cannot_expire_in_the_past(world: World) -> None:
    body = {
        "entry_type": "adjustment",
        "amount_minor": 500,
        "memo": "sorry",
        "idempotency_key": "adjustment-9",
        "expires_at": "2020-01-01T00:00:00Z",
    }
    with app_for(world) as app:
        res = await post(app, world.shree, body)
    assert res.status_code == 422


async def test_the_operators_own_workspace_takes_no_entries(world: World) -> None:
    world.shree.is_operator = True
    with app_for(world) as app:
        res = await post(app, world.shree, top_up())
    assert res.status_code == 409 and res.json()["code"] == "billing_exempt"
    assert world.audits("billing.ledger_entry") == []


async def test_an_unknown_workspace_is_404(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "POST", LEDGER.format(uuid.uuid4()), key=ADMIN, json=top_up())
    assert res.status_code == 404 and res.json()["code"] == "tenant_not_found"


async def test_with_billing_off_the_routes_answer_404(world: World) -> None:
    with patch.dict(os.environ, {"RAG_BILLING_ENABLED": "false"}):
        billing_config.reset_billing_config_cache()
        try:
            with app_for(world) as app:
                posted = await post(app, world.shree, top_up())
                listed = await req(app, "GET", LEDGER.format(world.shree.id), key=ADMIN)
        finally:
            billing_config.reset_billing_config_cache()
    assert posted.status_code == 404 and posted.json()["code"] == "billing_disabled"
    assert listed.status_code == 404


async def test_the_admin_ledger_routes_are_admin_only(world: World) -> None:
    with app_for(world) as app:
        anon = await req(app, "GET", LEDGER.format(world.shree.id))
        anon_post = await req(app, "POST", LEDGER.format(world.shree.id), json=top_up())
    assert anon.status_code == 401 and anon_post.status_code == 401


async def test_a_reconciliation_can_be_queued_by_the_admin_only(world: World) -> None:
    from unittest.mock import MagicMock

    sent = MagicMock()
    with app_for(world) as app, patch("callobuzz_rag.worker.celery_app.celery_app.send_task", sent):
        queued = await req(app, "POST", "/api/v1/admin/billing/reconciliation", key=ADMIN)
        anon = await req(app, "POST", "/api/v1/admin/billing/reconciliation")
    assert queued.status_code == 202 and queued.json() == {"queued": True}
    sent.assert_called_once_with("callobuzz_rag.reconcile_billing", queue="periodic")
    assert anon.status_code == 401


async def test_an_unreachable_queue_is_a_coded_503(world: World) -> None:
    from unittest.mock import MagicMock

    sent = MagicMock(side_effect=OSError("broker down"))
    with app_for(world) as app, patch("callobuzz_rag.worker.celery_app.celery_app.send_task", sent):
        res = await req(app, "POST", "/api/v1/admin/billing/reconciliation", key=ADMIN)
    assert res.status_code == 503 and res.json()["code"] == "queue_unavailable"
