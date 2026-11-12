"""Every audited operation writes exactly one row with its catalog action (accounts A8).

`PRODUCERS` maps each action in `AUDIT_ACTIONS` to a driver that performs the operation
through the app (the super-admin bootstrap is a CLI, so through its `run`), on the
in-memory database of `accounts_fakes.py`. An action added to the catalog without a
driver here fails `test_every_catalog_action_has_a_producer`.
"""

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.accounts.audit import AUDIT_ACTIONS
from callobuzz_rag.accounts.bootstrap import run as bootstrap
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.invitation import Invitation
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from tests.unit.test_auth_routes import (
    ADMIN,
    NEW_PASSWORD,
    PASSWORD,
    SA_EMAIL,
    SECRET,
    World,
    login,
    req,
)
from tests.unit.test_plans_admin import app_for
from tests.unit.test_signup import code_for, signup

Driver = Callable[[World, Any], Awaitable[None]]


def _ok(res: Any, *codes: int) -> None:
    assert res.status_code in (codes or (200, 201, 202, 204)), res.text


async def _admin(app: Any, method: str, path: str, **kw: Any) -> Any:
    return await req(app, method, path, key=ADMIN, **kw)


def _asha(world: World, role: str = "owner") -> Any:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree, role)
    return asha


async def _token(app: Any, email: str = "asha@shree.example") -> dict[str, Any]:
    res = await login(app, email)
    _ok(res, 200)
    body: dict[str, Any] = res.json()
    return body


# ------------------------------------------------------------------ drivers


async def settings_updated(world: World, app: Any) -> None:
    _ok(
        await _admin(
            app, "PATCH", "/api/v1/admin/settings", json={"branding": {"product_name": "Sahayak"}}
        )
    )


# BIL1 provider balances
async def provider_updated(world: World, app: Any) -> None:
    from unittest.mock import AsyncMock, patch

    # The stand-in database has no SUM(): only the spend query is replaced.
    with patch(
        "callobuzz_rag.analytics.provider_balances.operator_spend_since",
        AsyncMock(return_value=(0, 0)),
    ):
        res = await _admin(
            app,
            "PUT",
            "/api/v1/admin/billing/providers/manual:new",
            json={"label": "Groq", "manual_balance_usd": "50.00"},
        )
    _ok(res)


# /BIL1 provider balances


# BIL2 billing
async def billing_pricing_updated(world: World, app: Any) -> None:
    plan = world.db.add(Plan(name="Empty", is_active=True))  # nobody on it: starts at once (TV3)
    _ok(
        await _admin(
            app,
            "PUT",
            f"/api/v1/admin/billing/plans/{plan.id}/pricing",
            json={"usage_mode": "per_question", "per_question_minor": 200},
        )
    )


async def billing_account_updated(world: World, app: Any) -> None:
    from tests.unit.billing_fakes import fake_accounts

    with fake_accounts(world.db):
        _ok(
            await _admin(
                app,
                "PATCH",
                f"/api/v1/admin/billing/accounts/{world.shree.id}",
                json={"pause_override": "never"},
            )
        )


# /BIL2 billing


# TV3 notice
async def billing_price_change_scheduled(world: World, app: Any) -> None:
    from datetime import UTC, datetime, timedelta

    from tests.unit.billing_fakes import stored_pricing

    world.db.add(
        stored_pricing(world.db.default_plan, usage_mode="per_question", per_question_minor=200)
    )
    later = (datetime.now(UTC) + timedelta(days=60)).isoformat()
    _ok(
        await _admin(
            app,
            "PUT",
            f"/api/v1/admin/billing/plans/{world.db.default_plan.id}/pricing",
            json={
                "usage_mode": "per_question",
                "per_question_minor": 300,
                "apply": "scheduled",
                "effective_at": later,
            },
        )
    )


async def billing_price_change_cancelled(world: World, app: Any) -> None:
    from datetime import UTC, datetime, timedelta

    from tests.unit.billing_fakes import stored_pricing

    world.db.add(
        stored_pricing(world.db.default_plan, usage_mode="per_question", per_question_minor=200)
    )
    coming = stored_pricing(
        world.db.default_plan, version=2, usage_mode="per_question", per_question_minor=300
    )
    coming.is_current = False
    coming.effective_at = datetime.now(UTC) + timedelta(days=60)
    world.db.add(coming)
    res = await _admin(
        app, "DELETE", f"/api/v1/admin/billing/plans/{world.db.default_plan.id}/pricing/scheduled"
    )
    assert res.status_code == 204, res.text


def _billed_shree(world: World) -> Any:
    from datetime import date, timedelta

    from callobuzz_rag.db.models.billing_account import BillingAccount
    from tests.unit.billing_fakes import stored_pricing

    world.db.add(
        stored_pricing(world.db.default_plan, usage_mode="per_question", per_question_minor=200)
    )
    return world.db.add(
        BillingAccount(
            tenant_id=world.shree.id,
            currency="INR",
            ledger_seq=5,
            billing_day=12,
            next_charge_on=date.today() + timedelta(days=9),
        )
    )


async def _raise_per_question(world: World, app: Any, **extra: Any) -> Any:
    from tests.unit.billing_fakes import fake_accounts

    with fake_accounts(world.db):
        return await _admin(
            app,
            "PATCH",
            f"/api/v1/admin/billing/accounts/{world.shree.id}",
            json={"overrides": {"per_question_minor": 300}, **extra},
        )


async def billing_pending_change_scheduled(world: World, app: Any) -> None:
    _billed_shree(world)
    _ok(await _raise_per_question(world, app))


async def billing_change_agreed(world: World, app: Any) -> None:
    _billed_shree(world)
    _ok(
        await _raise_per_question(
            world, app, business_agreed=True, reason="Owner asked on a call on 5 Oct"
        )
    )


async def billing_pending_change_cancelled(world: World, app: Any) -> None:
    from tests.unit.billing_fakes import fake_accounts

    _billed_shree(world).pending_change = {"overrides": {"per_question_minor": 300}}
    with fake_accounts(world.db):
        res = await _admin(
            app, "DELETE", f"/api/v1/admin/billing/accounts/{world.shree.id}/pending-change"
        )
    assert res.status_code == 204, res.text


async def billing_pending_change_applied(world: World, app: Any) -> None:
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.billing.notice import apply_due_account_changes
    from tests.unit.billing_fakes import fake_accounts

    account = _billed_shree(world)
    account.pending_change = {"overrides": {"per_question_minor": 300}}
    account.pending_change_at = datetime.now(UTC) - timedelta(hours=1)
    with (
        fake_accounts(world.db),
        patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()),
    ):
        assert await apply_due_account_changes(world.db.factory(), datetime.now(UTC)) == 1


# P6 plan choice
async def _owner_moves_plan(world: World, app: Any, *, fee_now: int, fee_new: int) -> None:
    from datetime import date, timedelta

    from callobuzz_rag.db.models.billing_account import BillingAccount
    from tests.unit.billing_fakes import fake_accounts, stored_pricing

    _asha(world)
    world.db.add(stored_pricing(world.db.default_plan, subscription_fee_minor=fee_now))
    target = world.db.add(Plan(name="Chosen", is_active=True, self_service=True))
    world.db.add(stored_pricing(target, subscription_fee_minor=fee_new))
    world.db.add(
        BillingAccount(
            tenant_id=world.shree.id,
            currency="INR",
            ledger_seq=0,
            billing_day=1,
            next_charge_on=date.today() + timedelta(days=9),
        )
    )
    token = (await _token(app))["access_token"]
    with fake_accounts(world.db):
        res = await req(
            app, "POST", "/api/v1/billing/plan", token=token, json={"plan_id": str(target.id)}
        )
    _ok(res, 200)


async def billing_plan_change_applied(world: World, app: Any) -> None:
    await _owner_moves_plan(world, app, fee_now=0, fee_new=0)


async def billing_plan_change_awaiting_payment(world: World, app: Any) -> None:
    await _owner_moves_plan(world, app, fee_now=0, fee_new=99_900)


async def billing_plan_change_scheduled(world: World, app: Any) -> None:
    await _owner_moves_plan(world, app, fee_now=99_900, fee_new=0)


# /P6 plan choice


async def billing_spend_cap_set(world: World, app: Any) -> None:
    from callobuzz_rag.billing import accounts, spend_cap
    from tests.unit.billing_fakes import fake_accounts

    _billed_shree(world)
    with fake_accounts(world.db):
        session = world.db.session()
        ctx = await accounts.load_context(session, world.shree.id)
        await spend_cap.set_cap(session, ctx, 100_000, max_minor=None, request=None)


# P1 payments
async def billing_tax_details_updated(world: World, app: Any) -> None:
    asha = _asha(world)
    token = (await _token(app, asha.email))["access_token"]
    _ok(
        await req(
            app,
            "PUT",
            "/api/v1/billing/tax-details",
            token=token,
            json={
                "legal_name": "Shree Labs",
                "address_line1": "Paud Road",
                "city": "Pune",
                "state_code": "27",
            },
        )
    )


async def _paid_order(world: World, *, forged_amount: int | None = None, then: Any = None) -> None:
    """One top-up order paid through the fake provider and applied by `apply_captured`."""
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.accounts.settings_store import SETTINGS_ROW_ID, read_engine_settings_row
    from callobuzz_rag.billing import ledger
    from callobuzz_rag.billing.payments import orders
    from callobuzz_rag.billing.payments.capture import apply_captured
    from callobuzz_rag.billing.payments.fake import FakePaymentProvider
    from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
    from callobuzz_rag.db.models.payment import BillingProfile
    from tests.unit.billing_fakes import fake_accounts, stored_pricing
    from tests.unit.test_billing_ledger_api import fake_append

    world.db.add(stored_pricing(world.db.default_plan, subscription_fee_minor=0))
    world.db.add(
        EngineSettingsRow(
            id=SETTINGS_ROW_ID,
            value={
                "company": {"legal_name": "Callobuzz", "state_code": "27"},
                "invoicing": {"gstin": "27AAPFU0939F1ZV"},
            },
        )
    )
    world.db.add(
        BillingProfile(
            tenant_id=world.shree.id,
            legal_name="Shree Labs",
            address_line1="Paud Road",
            city="Pune",
            state_code="27",
            country="IN",
        )
    )
    fake = FakePaymentProvider()
    with (
        fake_accounts(world.db),
        patch.object(ledger, "append_entries", fake_append(world)),
        patch.object(ledger, "available_minor", AsyncMock(return_value=0)),
    ):
        session = world.db.session()
        engine = (await read_engine_settings_row(session))[0]
        order, _, _ = await orders.start_order(
            session,
            fake,
            world.shree.id,
            engine,
            purpose="top_up",
            amount_minor=100_000,
            add_on_id=None,
            idempotency_key="audit-order-0001",
            created_by="asha@shree.example",
        )
        info = fake.capture(order.provider_order_id or "", amount_minor=forged_amount)
        await apply_captured(session, info, source="webhook")
        if then is not None:
            await then(session, info)


async def billing_payment_received(world: World, app: Any) -> None:
    await _paid_order(world)


async def billing_payment_mismatch(world: World, app: Any) -> None:
    await _paid_order(world, forged_amount=1_000)


async def _refund_flow(world: World, finish: str) -> None:
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.accounts.settings_store import read_engine_settings_row
    from callobuzz_rag.billing import ledger, lots
    from callobuzz_rag.billing.payments import refunds
    from callobuzz_rag.db.models.payment import Payment, PaymentRefund

    async def then(session: Any, info: Any) -> None:
        async def nothing_refunded(*_: Any) -> int:
            return 0

        with (
            patch.object(ledger, "available_minor", AsyncMock(return_value=10**6)),
            patch.object(ledger, "paid_cash_minor", AsyncMock(return_value=10**6)),
            patch.object(lots, "live_credit", AsyncMock(return_value=0)),
            patch.object(refunds, "refunded_taxable", nothing_refunded),
        ):
            engine = (await read_engine_settings_row(session))[0]
            payment = next(
                p
                for p in world.db.all(Payment)
                if p.provider_payment_id == info.provider_payment_id
            )
            refund, _ = await refunds.request_refund(
                session,
                engine,
                payment.id,
                amount_minor=40_000,
                memo="Customer cancelled",
                confirm_amount_minor=None,
                idempotency_key="audit-refund-0001",
                actor=("admin_key", None, "admin key"),
            )
            if finish == "processed":
                await refunds._processed(
                    session, refund, payment, ledger.SYSTEM_ACTOR, datetime.now(UTC)
                )
            elif finish == "failed":
                await refunds._reverse(session, refund, payment, "test", ledger.SYSTEM_ACTOR)
            assert isinstance(refund, PaymentRefund)

    await _paid_order(world, then=then)


async def billing_refund_requested(world: World, app: Any) -> None:
    await _refund_flow(world, "requested")


async def billing_refund_issued(world: World, app: Any) -> None:
    await _refund_flow(world, "processed")


async def billing_refund_failed(world: World, app: Any) -> None:
    await _refund_flow(world, "failed")


# P2 invoices
async def billing_invoice_issued(world: World, app: Any) -> None:
    await _paid_order(world)


async def billing_credit_note_issued(world: World, app: Any) -> None:
    await _refund_flow(world, "processed")


# /P2 invoices


async def billing_payment_link_created(world: World, app: Any) -> None:
    from callobuzz_rag.accounts.settings_store import SETTINGS_ROW_ID
    from callobuzz_rag.billing.payments.fake import FakePaymentProvider
    from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
    from callobuzz_rag.db.models.payment import BillingProfile
    from tests.unit.billing_fakes import fake_accounts, stored_pricing

    world.db.add(stored_pricing(world.db.default_plan, subscription_fee_minor=0))
    world.db.add(
        EngineSettingsRow(
            id=SETTINGS_ROW_ID,
            value={
                "company": {"legal_name": "Callobuzz", "state_code": "27"},
                "invoicing": {"gstin": "27AAPFU0939F1ZV"},
            },
        )
    )
    world.db.add(
        BillingProfile(
            tenant_id=world.shree.id,
            legal_name="Shree Labs",
            address_line1="Paud Road",
            city="Pune",
            state_code="27",
            country="IN",
        )
    )
    app.state.payment_provider_override = FakePaymentProvider()
    with fake_accounts(world.db):
        _ok(
            await _admin(
                app,
                "POST",
                f"/api/v1/admin/billing/accounts/{world.shree.id}/payment-links",
                json={"purpose": "top_up", "amount_minor": 100_000, "send_email": False},
            )
        )


async def billing_chargeback(world: World, app: Any) -> None:
    from callobuzz_rag.billing.payments import events

    async def lose(session: Any, info: Any) -> None:
        await events._dispute(
            session,
            "payment.dispute.lost",
            {"payment": info.provider_payment_id, "dispute": "disp_audit"},
        )

    await _paid_order(world, then=lose)


async def payments_settings_updated(world: World, app: Any) -> None:
    _ok(
        await _admin(
            app,
            "PUT",
            "/api/v1/admin/billing/payments/settings",
            json={"provider": "razorpay", "key_id": "rzp_test_AbCdEf123456"},
        )
    )


async def billing_tax_profile_updated(world: World, app: Any) -> None:
    _ok(
        await _admin(app, "PUT", "/api/v1/admin/billing/tax-profile", json={"invoice_prefix": "CB"})
    )


# /P1 payments


async def billing_spend_cap_reached(world: World, app: Any) -> None:
    from datetime import UTC, datetime

    from callobuzz_rag.fairuse import alerts
    from callobuzz_rag.fairuse.limits import Breach, EffectiveFairUse, FairUseLimits, FairUsePolicy

    breach = Breach("spend_cap_per_month", "hard", 10**9, 10**9, datetime.now(UTC))
    effective = EffectiveFairUse(
        FairUseLimits(),
        FairUsePolicy(),
        False,
        frozenset(),
        tenant_name=world.shree.name,
        spend_cap_minor=100_000,
    )
    crossing = alerts.Crossing("spend_cap_per_month", 100, "hard", breach)
    await alerts._audit_cap_reached(
        world.db.session(), world.shree.id, effective, crossing, "2026-10"
    )


# L5a demo
async def demo_reset(world: World, app: Any) -> None:
    from callobuzz_rag.accounts.audit import record_audit
    from callobuzz_rag.demo import reset
    from tests.unit.accounts_fakes import FakeRedis

    class _Client:
        async def request(self, method: str, path: str, **kw: Any) -> reset.Reply:
            return reset.Reply(200, {"items": [], "id": "x", "draft_revision": 1})

    async def write(entry: dict[str, Any]) -> None:
        session = world.db.session()
        await record_audit(
            session, None, entry["action"], details=entry["detail"], actor=reset.ACTOR
        )
        await session.commit()

    deps = reset.ResetDeps.fake()
    deps.write_audit = write
    report = await reset.run_reset(_Client(), FakeRedis(), deps, trigger="manual")
    assert report.result == "ok"


# /L5a demo


async def support_request_created(world: World, app: Any) -> None:
    from unittest.mock import patch

    _asha(world)
    token = (await _token(app))["access_token"]

    async def number(session: Any) -> int:
        return 1042

    with patch("callobuzz_rag.support.service.next_number", number):
        _ok(
            await req(
                app,
                "POST",
                "/api/v1/support/requests",
                token=token,
                json={"subject": "Widget has no button", "message": "Since yesterday."},
            ),
            201,
        )


async def support_request_status_changed(world: World, app: Any) -> None:
    from datetime import UTC, datetime

    from callobuzz_rag.db.models.support import SupportRequest

    row = world.db.add(
        SupportRequest(
            tenant_id=world.shree.id,
            number=1042,
            subject="Widget has no button",
            status="open",
            context={},
            created_by_email="asha@shree.example",
            created_at=datetime.now(UTC),
            last_message_at=datetime.now(UTC),
        )
    )
    _ok(
        await _admin(
            app, "PATCH", f"/api/v1/admin/support/requests/{row.id}", json={"status": "closed"}
        )
    )


async def settings_price_sheet_link_rotated(world: World, app: Any) -> None:
    _ok(await _admin(app, "POST", "/api/v1/admin/price-sheet/link"), 201)


async def settings_price_sheet_link_removed(world: World, app: Any) -> None:
    from callobuzz_rag.accounts.settings_store import (
        SETTINGS_ROW_ID,
        invalidate_engine_settings_cache,
    )
    from callobuzz_rag.db.models.engine_settings import EngineSettingsRow

    world.db.add(
        EngineSettingsRow(
            id=SETTINGS_ROW_ID, value={"trust": {"price_sheet": {"public_token": "tok-123"}}}
        )
    )
    invalidate_engine_settings_cache()
    _ok(await _admin(app, "DELETE", "/api/v1/admin/price-sheet/link"), 204)


# /TV3 notice


# BIL3 ledger
async def billing_ledger_entry(world: World, app: Any) -> None:
    from unittest.mock import AsyncMock, patch

    from tests.unit.billing_fakes import fake_accounts
    from tests.unit.test_billing_ledger_api import fake_append

    with (
        fake_accounts(world.db),
        patch("callobuzz_rag.billing.ledger.append_entries", fake_append(world)),
        patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()),
    ):
        _ok(
            await _admin(
                app,
                "POST",
                f"/api/v1/admin/billing/accounts/{world.shree.id}/ledger",
                json={
                    "entry_type": "top_up",
                    "amount_minor": 50_000,
                    "idempotency_key": "audit-coverage-1",
                },
            ),
            201,
        )


async def billing_reconciliation_failed(world: World, app: Any) -> None:
    from unittest.mock import patch

    from callobuzz_rag.billing import reconcile

    class _Session:
        def __init__(self) -> None:
            self.db = world.db

        async def __aenter__(self) -> Any:
            return world.db.session()

        async def __aexit__(self, *exc: Any) -> None:
            return None

    async def broken(session: Any, day: Any, now: Any, **_kw: Any) -> Any:
        return 1, [reconcile.Finding("ledger_integrity", detail={"first_bad_seq": 3})]

    with patch.object(reconcile, "gather_findings", broken):
        result = await reconcile.run(lambda: _Session())
    assert result is not None and not result.ok


# /BIL3 ledger


# BIL4 alerts
async def _balance_hook(world: World, *balances: int) -> None:
    """Run the balance hook for Shree Labs at each balance in turn (a per-question plan, 200 a
    question, last top-up 10,000): the first at or below 0 pauses it, a later one above resumes."""
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.billing import hooks
    from callobuzz_rag.db.models.billing_account import BillingAccount
    from tests.unit.billing_fakes import fake_accounts, stored_pricing

    world.db.add(
        stored_pricing(world.db.default_plan, usage_mode="per_question", per_question_minor=200)
    )
    account = world.db.add(
        BillingAccount(
            tenant_id=world.shree.id, currency="INR", ledger_seq=1, last_top_up_minor=10_000
        )
    )
    with (
        fake_accounts(world.db),
        patch.object(hooks, "billing_enabled", lambda: True),
        patch.object(hooks, "notify", AsyncMock()),
    ):
        for balance in balances:
            account.balance_minor = balance
            await hooks.after_balance_change(world.db.factory(), [account.id])


async def billing_paused(world: World, app: Any) -> None:
    await _balance_hook(world, 0)


async def billing_resumed(world: World, app: Any) -> None:
    await _balance_hook(world, 0, 5_000)


# /BIL4 alerts


# BIL5 add-ons
_ADD_ONS = "/api/v1/admin/billing/add-ons"


async def _make_add_on(app: Any) -> dict[str, Any]:
    res = await _admin(
        app,
        "POST",
        _ADD_ONS,
        json={
            "kind": "question_pack",
            "name": "audit pack",
            "currency": "INR",
            "price_minor": 100,
            "questions": 10,
        },
    )
    _ok(res, 201)
    return dict(res.json())


async def billing_add_on_created(world: World, app: Any) -> None:
    await _make_add_on(app)


async def billing_add_on_updated(world: World, app: Any) -> None:
    made = await _make_add_on(app)
    _ok(await _admin(app, "PATCH", f"{_ADD_ONS}/{made['id']}", json={"price_minor": 200}), 200)


async def billing_add_on_deleted(world: World, app: Any) -> None:
    made = await _make_add_on(app)
    _ok(await _admin(app, "DELETE", f"{_ADD_ONS}/{made['id']}"), 204)


async def _grant(world: World, app: Any) -> dict[str, Any]:
    from unittest.mock import AsyncMock, patch

    from tests.unit.billing_fakes import fake_accounts, stored_pricing

    world.db.add(stored_pricing(world.db.default_plan, currency="INR"))
    made = await _make_add_on(app)
    with (
        fake_accounts(world.db),
        patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()),
    ):
        res = await _admin(
            app,
            "POST",
            f"/api/v1/admin/billing/accounts/{world.shree.id}/grants",
            json={"add_on_id": made["id"], "idempotency_key": "audit-coverage-5"},
        )
    _ok(res, 201)
    return dict(res.json())


async def billing_add_on_granted(world: World, app: Any) -> None:
    await _grant(world, app)


async def billing_grant_revoked(world: World, app: Any) -> None:
    from tests.unit.billing_fakes import fake_accounts

    grant = await _grant(world, app)
    with fake_accounts(world.db):
        _ok(
            await _admin(
                app,
                "POST",
                f"/api/v1/admin/billing/accounts/{world.shree.id}/grants/{grant['id']}/revoke",
                json={},
            ),
            200,
        )


# /BIL5 add-ons


# P5 plans
async def _p5_grant(world: World, app: Any, body: dict[str, Any]) -> dict[str, Any]:
    from unittest.mock import AsyncMock, patch

    from tests.unit.billing_fakes import fake_accounts, stored_pricing

    world.db.add(stored_pricing(world.db.default_plan, currency="INR"))
    made = await _admin(app, "POST", _ADD_ONS, json=body)
    _ok(made, 201)
    with (
        fake_accounts(world.db),
        patch("callobuzz_rag.billing.addons.byok_status", AsyncMock(return_value=(False, None))),
        patch("callobuzz_rag.support.service.next_number", AsyncMock(return_value=77)),
        patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()),
    ):
        res = await _admin(
            app,
            "POST",
            f"/api/v1/admin/billing/accounts/{world.shree.id}/grants",
            json={
                "add_on_id": made.json()["id"],
                "payment": "free",
                "idempotency_key": "audit-coverage-p5",
            },
        )
    _ok(res, 201)
    return dict(res.json())


async def billing_service_order_updated(world: World, app: Any) -> None:
    from callobuzz_rag.db.models.service_order import ServiceOrder

    await _p5_grant(
        world,
        app,
        {"kind": "service", "name": "Installation help", "currency": "INR", "price_minor": 100},
    )
    (order,) = world.db.all(ServiceOrder)
    _ok(
        await _admin(
            app,
            "PATCH",
            f"/api/v1/admin/billing/service-orders/{order.id}",
            json={"status": "in_progress"},
        ),
        200,
    )


async def billing_add_on_cancelled(world: World, app: Any) -> None:
    from tests.unit.billing_fakes import fake_accounts

    grant = await _p5_grant(
        world,
        app,
        {
            "kind": "feature_unlock",
            "name": "Own AI key",
            "currency": "INR",
            "price_minor": 100,
            "feature": "byok",
            "duration": "period",
            "renews": True,
        },
    )
    _asha(world)
    token = (await _token(app))["access_token"]
    with fake_accounts(world.db):
        _ok(
            await req(
                app,
                "POST",
                f"/api/v1/billing/add-ons/grants/{grant['id']}/cancel",
                token=token,
            ),
            200,
        )


# /P5 plans


# BIL6 top-up requests
async def billing_top_up_requested(world: World, app: Any) -> None:
    from tests.unit.billing_fakes import fake_accounts

    _asha(world)
    token = (await _token(app))["access_token"]
    with fake_accounts(world.db):
        _ok(
            await req(
                app,
                "POST",
                "/api/v1/billing/top-up-requests",
                token=token,
                json={"amount_minor": 100_000, "note": "paid by UPI"},
            ),
            202,
        )


# /BIL6 top-up requests


async def superadmin_bootstrapped(world: World, app: Any) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        session_secret=SECRET,  # type: ignore[arg-type]
        superadmin_email=SA_EMAIL,
        email_provider="log",
    )
    await bootstrap(settings, world.db.factory(), out=lambda _line: None)


async def password_reset(world: World, app: Any) -> None:
    _asha(world)
    _ok(await req(app, "POST", "/api/v1/auth/forgot", json={"email": "asha@shree.example"}))
    code = code_for(world, "asha@shree.example", "reset_code")
    _ok(
        await req(
            app,
            "POST",
            "/api/v1/auth/reset",
            json={"email": "asha@shree.example", "code": code, "new_password": NEW_PASSWORD},
        )
    )


async def password_changed(world: World, app: Any) -> None:
    _asha(world)
    token = (await _token(app))["access_token"]
    _ok(
        await req(
            app,
            "POST",
            "/api/v1/auth/change-password",
            token=token,
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
    )


async def login_locked(world: World, app: Any) -> None:
    _asha(world)
    for _ in range(5):
        _ok(await login(app, "asha@shree.example", "wrong-password-x"), 401)


async def refresh_reused(world: World, app: Any) -> None:
    _asha(world)
    first = await _token(app)
    body = {"refresh_token": first["refresh_token"]}
    _ok(await req(app, "POST", "/api/v1/auth/refresh", json=body), 200)
    _ok(await req(app, "POST", "/api/v1/auth/refresh", json=body), 401)


async def signed_up(world: World, app: Any) -> None:
    _ok(await signup(app))
    code = code_for(world, "asha@shree.example")
    _ok(
        await req(
            app, "POST", "/api/v1/auth/verify", json={"email": "asha@shree.example", "code": code}
        )
    )


def _tenants(rest: str) -> str:
    return f"/api/v1/admin/tenants{rest}"


async def activated(world: World, app: Any) -> None:
    world.shree.status = "pending"
    _ok(await _admin(app, "POST", _tenants(f"/{world.shree.id}/activate"), json={}))


async def suspended(world: World, app: Any) -> None:
    body = {"confirm_name": world.shree.name}
    _ok(await _admin(app, "POST", _tenants(f"/{world.shree.id}/suspend"), json=body))


async def reactivated(world: World, app: Any) -> None:
    world.shree.status = "suspended"
    _ok(await _admin(app, "POST", _tenants(f"/{world.shree.id}/reactivate")))


async def declined(world: World, app: Any) -> None:
    world.shree.status = "pending"
    _ok(await _admin(app, "POST", _tenants(f"/{world.shree.id}/decline")))


async def deleted(world: World, app: Any) -> None:
    params = {"confirm_name": world.shree.name}
    _ok(await _admin(app, "DELETE", _tenants(f"/{world.shree.id}"), params=params))


async def created(world: World, app: Any) -> None:
    _ok(await _admin(app, "POST", _tenants(""), json={"name": "Kaveri Foods"}))


async def _admin_invite(world: World, app: Any, email: str = "ravi@shree.example") -> str:
    res = await _admin(
        app,
        "POST",
        _tenants(f"/{world.shree.id}/invitations"),
        json={"email": email, "role": "member"},
    )
    _ok(res, 201)
    return str(res.json()["accept_url"].split("#token=")[1])


async def invited(world: World, app: Any) -> None:
    await _admin_invite(world, app)


async def invitation_revoked(world: World, app: Any) -> None:
    await _admin_invite(world, app)
    before = len(world.audits("member.invited"))
    (invitation,) = world.db.all(Invitation)
    _ok(await _admin(app, "DELETE", _tenants(f"/{world.shree.id}/invitations/{invitation.id}")))
    assert len(world.audits("member.invited")) == before


async def invitation_accepted(world: World, app: Any) -> None:
    token = await _admin_invite(world, app)
    body = {"token": token, "name": "Ravi", "password": "copper-meadow-lantern"}
    _ok(await req(app, "POST", "/api/v1/invitations/accept", json=body), 200)


async def role_changed(world: World, app: Any) -> None:
    _asha(world)
    ravi = world.user("ravi@shree.example")
    world.member(ravi, world.shree, "member")
    path = _tenants(f"/{world.shree.id}/members/{ravi.id}")
    _ok(await _admin(app, "PATCH", path, json={"role": "owner"}))


async def removed(world: World, app: Any) -> None:
    _asha(world)
    ravi = world.user("ravi@shree.example")
    world.member(ravi, world.shree, "member")
    _ok(await _admin(app, "DELETE", _tenants(f"/{world.shree.id}/members/{ravi.id}")))


async def plan_created(world: World, app: Any) -> None:
    _ok(await _admin(app, "POST", "/api/v1/admin/plans", json={"name": "Starter"}), 201)


def _pro(world: World) -> Plan:
    return world.db.add(Plan(name="Pro", is_default=False, is_active=True))


async def plan_updated(world: World, app: Any) -> None:
    pro = _pro(world)
    _ok(await _admin(app, "PATCH", f"/api/v1/admin/plans/{pro.id}", json={"max_documents": 9}))


async def plan_deleted(world: World, app: Any) -> None:
    pro = _pro(world)
    _ok(await _admin(app, "DELETE", f"/api/v1/admin/plans/{pro.id}"))


async def plan_changed(world: World, app: Any) -> None:
    pro = _pro(world)
    path = _tenants(f"/{world.shree.id}/plan")
    _ok(await _admin(app, "POST", path, json={"plan_id": str(pro.id)}))


# PIP1 profile
async def _new_profile(app: Any, name: str = "Spare") -> str:
    res = await _admin(app, "POST", "/api/v1/admin/pipelines", json={"name": name, "label": name})
    _ok(res, 201)
    return str(res.json()["id"])


async def pipeline_profile_created(world: World, app: Any) -> None:
    await _new_profile(app)


async def pipeline_profile_updated(world: World, app: Any) -> None:
    pid = await _new_profile(app)
    _ok(await _admin(app, "PATCH", f"/api/v1/admin/pipelines/{pid}", json={"label": "Renamed"}))


async def pipeline_profile_deleted(world: World, app: Any) -> None:
    pid = await _new_profile(app)
    _ok(await _admin(app, "DELETE", f"/api/v1/admin/pipelines/{pid}"))


async def pipeline_default_set(world: World, app: Any) -> None:
    from callobuzz_rag.db.models.pipeline_profile import PipelineProfile

    world.db.add(PipelineProfile(name="Standard", label="Standard", is_default=True, config={}))
    pid = await _new_profile(app)
    _ok(await _admin(app, "POST", f"/api/v1/admin/pipelines/{pid}/default"))


async def pipeline_tenant_profile_set(world: World, app: Any) -> None:
    pid = await _new_profile(app)
    path = f"/api/v1/admin/pipelines/tenants/{world.shree.id}"
    _ok(await _admin(app, "PUT", path, json={"profile_id": pid}))


# /PIP1 profile


# PIP3 fair use
async def fair_use_overrides_updated(world: World, app: Any) -> None:
    path = f"/api/v1/admin/fair-use/tenants/{world.shree.id}"
    _ok(await _admin(app, "PUT", path, json={"limits": {"questions_per_hour": 100}}))


async def _fair_use_transition(world: World, new_state: str) -> None:
    """What the alert task does when the business's state moves (system actor, one audit row)."""
    from datetime import UTC, datetime

    from callobuzz_rag.billing.enforcement import NOT_BILLED
    from callobuzz_rag.fairuse import alerts
    from callobuzz_rag.fairuse.decision import decide
    from callobuzz_rag.fairuse.limits import (
        Breach,
        EffectiveFairUse,
        FairUseLimits,
        FairUsePolicy,
    )
    from callobuzz_rag.tiers.profiles import noop_profile
    from tests.unit.accounts_fakes import FakeSession

    plan = noop_profile()
    breach = Breach("questions_per_hour", "hard", 30, 46, None)
    decision = decide(
        cap_blocked=False,
        billing=NOT_BILLED,
        breaches=[breach],
        policy=FairUsePolicy(),
        plan_profile=plan,
        downgrade_to=plan,
        exempt=False,
    )
    effective = EffectiveFairUse(
        FairUseLimits(), FairUsePolicy(), False, frozenset(), tenant_name=world.shree.name
    )
    now = datetime(2026, 10, 5, 14, 20, tzinfo=UTC)
    await alerts._audit_transition(
        FakeSession(world.db),
        world.shree.id,
        effective,
        decision,
        new_state,
        now,  # type: ignore[arg-type]
    )


async def fair_use_throttled(world: World, app: Any) -> None:
    await _fair_use_transition(world, "throttled")


async def fair_use_stopped(world: World, app: Any) -> None:
    await _fair_use_transition(world, "stopped")


async def fair_use_resumed(world: World, app: Any) -> None:
    await _fair_use_transition(world, "ok")


async def fair_use_engine_spend_reached(world: World, app: Any) -> None:
    from callobuzz_rag.fairuse import alerts
    from callobuzz_rag.fairuse.limits import Breach
    from tests.unit.accounts_fakes import FakeSession

    breach = Breach("engine_ai_spend_per_day", "hard", 5_000_000, 5_000_000, None)
    await alerts._audit_engine_spend(FakeSession(world.db), breach, "2026-10-05")  # type: ignore[arg-type]


# /PIP3 fair use


async def _operator_workspace(world: World) -> Any:
    await superadmin_bootstrapped(world, None)
    return next(t for t in world.db.all(Tenant) if t.is_operator)


async def cob_resync_requested(world: World, app: Any) -> None:
    from unittest.mock import patch

    from callobuzz_rag.db.models.cob_status import CobStatus

    world.db.add(CobStatus(id=1, sync_state="idle", document_count=0))
    world.user(SA_EMAIL, superadmin=True)
    token = (await _token(app, SA_EMAIL))["access_token"]
    with patch("callobuzz_rag.worker.cob_tasks.cob_sync_task.delay"):
        res = await req(app, "POST", "/api/v1/admin/cob/resync", token=token, json={})
    _ok(res, 202)


async def _platform_doc(world: World, app: Any) -> tuple[Any, Any]:
    """COB's knowledge base with one platform doc, and the super admin's token."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from callobuzz_rag.cob.system import ensure_cob_system
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.document import Document

    await ensure_cob_system(
        world.db.session(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        lock=False,
    )
    corpus = next(c for c in world.db.all(Corpus) if c.is_system)
    doc = Document(
        corpus_id=corpus.id,
        file_name="Support hours",
        mime_type="text/plain",
        size_bytes=5,
        content_fingerprint="f" * 64,
        ingested_at=datetime.now(UTC),
        doc_metadata={"cob_source": "platform", "audience": "customer"},
    )
    world.db.add(doc)
    world.user(SA_EMAIL, superadmin=True)
    app.state.vector_store = AsyncMock()
    token = (await _token(app, SA_EMAIL))["access_token"]
    return doc, token


async def cob_platform_doc_audience(world: World, app: Any) -> None:
    doc, token = await _platform_doc(world, app)
    res = await req(
        app,
        "PATCH",
        f"/api/v1/admin/cob/documents/{doc.id}",
        token=token,
        json={"audience": "admin"},
    )
    _ok(res, 200)


async def cob_platform_doc_deleted(world: World, app: Any) -> None:
    from unittest.mock import AsyncMock, patch

    doc, token = await _platform_doc(world, app)
    with patch("callobuzz_rag.api.routes.documents.remove_document", AsyncMock()):
        res = await req(
            app,
            "DELETE",
            f"/api/v1/documents/{doc.id}?corpus_id={doc.corpus_id}",
            token=token,
        )
    _ok(res, 204)


async def cob_action(world: World, app: Any) -> None:
    """COB5: confirming a card runs the tool's route through the caller's own client and writes
    `cob.action` (here the route is a fake; the route's own audit row is its own test)."""
    import uuid
    from datetime import UTC, datetime, timedelta
    from unittest.mock import patch

    from callobuzz_rag.db.models.cob_action_proposal import CobActionProposal
    from callobuzz_rag.db.models.conversation import Conversation
    from tests.unit.cob_tool_helpers import FakeInternalClient

    asha = _asha(world)
    token = (await _token(app))["access_token"]
    conv = world.db.add(
        Conversation(corpus_id=uuid.uuid4(), kind="cob", channel="cob", message_count=0)
    )
    kb = uuid.uuid4()
    now = datetime.now(UTC)
    proposal = world.db.add(
        CobActionProposal(
            id=uuid.uuid4(),
            user_id=asha.id,
            tenant_id=world.shree.id,
            conversation_id=conv.id,
            tool="create_widget_key",
            args={
                "corpus_id": str(kb),
                "kb_name": "Shree Labs",
                "name": "Main site",
                "allowed_origins": ["https://shreelabs.in"],
            },
            summary="s",
            risk="low",
            status="pending",
            created_at=now,
            expires_at=now + timedelta(seconds=600),
        )
    )
    client = FakeInternalClient()
    client.reply("POST", "/api/v1/corpora/{corpus_id}/widget-keys", {"id": str(uuid.uuid4())}, 201)
    with patch("callobuzz_rag.cob.orchestrator.make_internal_client", return_value=client):
        res = await req(
            app, "POST", f"/api/v1/cob/actions/{proposal.id}/confirm", token=token, json={}
        )
    _ok(res, 200)
    assert res.json()["status"] == "done", res.text


async def operator_workspace_created(world: World, app: Any) -> None:
    await _operator_workspace(world)


async def operator_suspend_refused(world: World, app: Any) -> None:
    tenant = await _operator_workspace(world)
    body = {"confirm_name": tenant.name}
    _ok(await _admin(app, "POST", _tenants(f"/{tenant.id}/suspend"), json=body), 409)


async def operator_delete_refused(world: World, app: Any) -> None:
    tenant = await _operator_workspace(world)
    params = {"confirm_name": tenant.name}
    _ok(await _admin(app, "DELETE", _tenants(f"/{tenant.id}"), params=params), 422)


async def _new_key(world: World, app: Any) -> str:
    res = await _admin(
        app, "POST", _tenants(f"/{world.shree.id}/keys"), json={"name": "Agent - n8n"}
    )
    _ok(res, 201)
    return str(res.json()["id"])


async def api_key_created(world: World, app: Any) -> None:
    await _new_key(world, app)


async def api_key_rotated(world: World, app: Any) -> None:
    key_id = await _new_key(world, app)
    _ok(await _admin(app, "POST", _tenants(f"/{world.shree.id}/keys/{key_id}/rotate")))


async def api_key_updated(world: World, app: Any) -> None:
    key_id = await _new_key(world, app)
    path = _tenants(f"/{world.shree.id}/keys/{key_id}")
    _ok(await _admin(app, "PATCH", path, json={"permission": "write"}))


async def api_key_revoked(world: World, app: Any) -> None:
    key_id = await _new_key(world, app)
    _ok(await _admin(app, "DELETE", _tenants(f"/{world.shree.id}/keys/{key_id}")))


async def _assistant_world(world: World) -> tuple[Any, Any]:
    """A knowledge base with a published v1, the audit rows of that seed cleared."""
    from callobuzz_rag.conversation.schema import AssistantConfig
    from callobuzz_rag.conversation.store import AssistantConfigStore
    from callobuzz_rag.db.models.corpus import Corpus
    from tests.unit.accounts_fakes import request_stub

    corpus = world.db.add(
        Corpus(
            name="Patient help",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_aud",
            api_key_hash="",
        )
    )
    settings = Settings(_env_file=None, encryption_key="")
    request = request_stub(settings)
    request.state.is_admin = True
    store = AssistantConfigStore(world.db.session(), request, settings)
    seed = AssistantConfig.model_validate({"leads": {"enabled": False}})
    await store.publish_initial(corpus, seed, "seed")
    world.db.rows[AuditLog] = []  # only the operation under test counts
    return store, corpus


async def assistant_published(world: World, app: Any) -> None:
    store, corpus = await _assistant_world(world)
    await store.patch_draft(corpus, {"voice": {"emoji": "light"}}, base_revision=None)
    await store.publish(corpus, note=None, base_revision=1)


async def assistant_rolled_back(world: World, app: Any) -> None:
    store, corpus = await _assistant_world(world)
    await store.restore(corpus, 1)


async def assistant_enabled_changed(world: World, app: Any) -> None:
    store, corpus = await _assistant_world(world)
    await store.set_enabled(corpus, False)


async def assistant_draft_discarded(world: World, app: Any) -> None:
    store, corpus = await _assistant_world(world)
    await store.patch_draft(corpus, {"voice": {"emoji": "light"}}, base_revision=None)
    await store.discard_draft(corpus)


async def assistant_setup_applied(world: World, app: Any) -> None:
    """C8: applying a guided setup publishes the draft and writes assistant.setup_applied."""
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.conversation.setup.service import SetupService
    from callobuzz_rag.db.models.assistant_setup import AssistantSetupSession
    from tests.unit.accounts_fakes import request_stub

    store, corpus = await _assistant_world(world)
    await store.patch_draft(corpus, {"voice": {"emoji": "light"}}, base_revision=None)
    row = world.db.add(
        AssistantSetupSession(corpus_id=corpus.id, status="generated", created_by="admin key")
    )
    settings = Settings(_env_file=None, encryption_key="")
    request = request_stub(settings)
    request.state.is_admin = True
    with (
        patch("callobuzz_rag.worker.tasks.ingest_records_task"),
        patch(
            "callobuzz_rag.evaluation.golden_runner.enqueue_publish_runs",
            AsyncMock(return_value=0),
        ),
    ):
        await SetupService(world.db.session(), request).apply(
            corpus, row.id, base_revision=1, note=None
        )


async def _webhook_world(world: World) -> Any:
    from callobuzz_rag.db.models.corpus import Corpus

    return world.db.add(
        Corpus(
            name="Patient help",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_hook",
            api_key_hash="",
        )
    )


async def corpus_webhook_created(world: World, app: Any) -> None:
    from unittest.mock import AsyncMock, patch

    corpus = await _webhook_world(world)
    with patch("callobuzz_rag.api.routes.corpus_webhooks.acheck_public_https_url", AsyncMock()):
        res = await _admin(
            app,
            "POST",
            f"/api/v1/corpora/{corpus.id}/webhooks",
            json={
                "url": "https://crm.shree-labs.in/h",
                "events": ["lead.created"],
                "secret": "s" * 20,
            },
        )
    _ok(res, 201)


async def corpus_webhook_deleted(world: World, app: Any) -> None:
    from callobuzz_rag.db.models.webhook_config import WebhookConfig

    corpus = await _webhook_world(world)
    hook = world.db.add(
        WebhookConfig(
            url="https://crm.shree-labs.in/h",
            events=["lead.created"],
            secret="s" * 20,
            enabled=True,
            corpus_id=corpus.id,
        )
    )
    _ok(await _admin(app, "DELETE", f"/api/v1/corpora/{corpus.id}/webhooks/{hook.id}"), 204)


async def _lead_world(world: World, app: Any, *, expired: bool = False) -> Any:
    """A knowledge base with one encrypted lead, and an engine that has the key."""
    import uuid
    from datetime import UTC, datetime, timedelta

    from cryptography.fernet import Fernet

    from callobuzz_rag.conversation.leads.crypto import LeadCrypto
    from callobuzz_rag.core.secrets import FernetEncryptor
    from callobuzz_rag.db.models.lead import Lead

    key = Fernet.generate_key().decode()
    app.state.settings = app.state.settings.model_copy(update={"encryption_key": key})
    corpus = await _webhook_world(world)
    now = datetime.now(UTC)
    crypto = LeadCrypto(FernetEncryptor(key))
    return world.db.add(
        Lead(
            id=uuid.uuid4(),
            corpus_id=corpus.id,
            tenant_id=world.shree.id,
            channel="widget",
            fields_encrypted=crypto.encrypt_json(
                {"name": "Priya", "phone": "+919876543210", "email": None, "custom": {}}
            ),
            trigger="lead_form",
            status="new",
            tags=[],
            sources_consulted=[],
            retention_until=now - timedelta(days=1) if expired else now + timedelta(days=30),
            created_at=now,
            updated_at=now,
        )
    )


async def lead_exported(world: World, app: Any) -> None:
    from unittest.mock import AsyncMock, patch

    lead = await _lead_world(world, app)
    # The stand-in database has no COUNT(*): only the counting and name queries are replaced.
    with (
        patch("callobuzz_rag.api.routes.leads._count_for_export", AsyncMock(return_value=1)),
        patch("callobuzz_rag.api.routes.leads.list_leads", AsyncMock(return_value=([lead], 1))),
        patch("callobuzz_rag.api.routes.leads._corpus_names", AsyncMock(return_value={})),
    ):
        res = await _admin(app, "GET", f"/api/v1/leads/export.csv?corpus_id={lead.corpus_id}")
    _ok(res, 200)


async def lead_deleted(world: World, app: Any) -> None:
    lead = await _lead_world(world, app)
    _ok(await _admin(app, "DELETE", f"/api/v1/leads/{lead.id}"), 204)


async def lead_retention_purged(world: World, app: Any) -> None:
    from callobuzz_rag.conversation.leads.retention import purge_expired_leads
    from tests.unit.accounts_fakes import Result

    await _lead_world(world, app, expired=True)
    execute = world.db.execute

    def execute_without_chat_state(stmt: Any) -> Any:
        # The stand-in has no conversations table or JSONB functions; the statement that
        # clears abandoned lead captures is checked in test_lead_retention.
        if stmt.__visit_name__ == "update" and stmt.table.name == "conversations":
            return Result([], scalar=True, rowcount=0)
        return execute(stmt)

    world.db.execute = execute_without_chat_state  # type: ignore[method-assign]
    result = await purge_expired_leads(world.db.factory())
    assert result["leads"] == 1


async def lead_withdrawn(world: World, app: Any) -> None:
    """A visitor opens a withdrawal link: the lead goes, one system-actor row stays."""
    import uuid
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from callobuzz_rag.conversation.leads.tokens import (
        make_withdraw_token,
        new_withdraw_secret,
        secret_hash,
    )
    from callobuzz_rag.db.models.lead import Lead

    secret = new_withdraw_secret()
    app.state.widget_rate_limiter = SimpleNamespace(_hit=AsyncMock(return_value=(True, 0)))
    corpus = await _webhook_world(world)
    lead = world.db.add(
        Lead(
            id=uuid.uuid4(),
            corpus_id=corpus.id,
            tenant_id=world.shree.id,
            channel="widget",
            fields_encrypted="x",
            trigger="lead_form",
            status="new",
            withdraw_hash=secret_hash(secret),
            retention_until=datetime.now(UTC),
        )
    )
    token = make_withdraw_token(lead.id, secret)
    res = await req(app, "POST", "/api/v1/public/leads/withdraw", json={"token": token})
    _ok(res, 200)


# BIZ2 freshness: the push link
async def website_learn_key_created(world: World, app: Any) -> None:
    corpus, _source, _ = await _website_world(world, app)
    res = await _admin(app, "POST", f"/api/v1/corpora/{corpus.id}/website/learn-key")
    _ok(res, 201)


async def website_learn_key_revoked(world: World, app: Any) -> None:
    corpus, _source, _ = await _website_world(world, app)
    _ok(await _admin(app, "POST", f"/api/v1/corpora/{corpus.id}/website/learn-key"), 201)
    world.db.rows[AuditLog].clear()  # the revoke is the one action under test
    _ok(await _admin(app, "DELETE", f"/api/v1/corpora/{corpus.id}/website/learn-key"), 204)


# /BIZ2 freshness


# BIZ1 learning: a business's website
async def _website_world(world: World, app: Any, *, page_status: str | None = None) -> Any:
    from unittest.mock import AsyncMock

    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.website import WebsitePage, WebsiteSource
    from callobuzz_rag.db.models.widget_key import WidgetKey

    app.state.vector_store = AsyncMock()
    corpus = world.db.add(
        Corpus(
            name="Shree Dental",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_site",
            api_key_hash="",
        )
    )
    world.db.add(
        WidgetKey(
            corpus_id=corpus.id,
            name="Site",
            public_key="pk_site",
            allowed_origins=["https://shreedental.in"],
            is_active=True,
        )
    )
    source = world.db.add(
        WebsiteSource(
            corpus_id=corpus.id,
            tenant_id=world.shree.id,
            origin="https://shreedental.in",
            scope="shreedental.in",
        )
    )
    page = None
    if page_status is not None:
        page = world.db.add(
            WebsitePage(
                source_id=source.id,
                corpus_id=corpus.id,
                url="https://shreedental.in/pricing",
                url_hash="h",
                status=page_status,
            )
        )
    return corpus, source, page


async def website_learning_started(world: World, app: Any) -> None:
    from unittest.mock import AsyncMock, patch

    corpus, source, _ = await _website_world(world, app)
    world.db.rows[type(source)].clear()  # the first start creates the site itself
    with (
        patch("callobuzz_rag.website.service.check_url", AsyncMock()),
        patch("callobuzz_rag.website.service.dispatch_learn", return_value=True),
    ):
        res = await _admin(
            app,
            "POST",
            f"/api/v1/corpora/{corpus.id}/website/learn",
            json={"origin": "shreedental.in"},
        )
    _ok(res, 202)


async def website_settings_updated(world: World, app: Any) -> None:
    corpus, _, _ = await _website_world(world, app)
    res = await _admin(
        app,
        "PATCH",
        f"/api/v1/corpora/{corpus.id}/website",
        json={"exclude_patterns": ["/blog/*"]},
    )
    _ok(res, 200)


async def website_source_removed(world: World, app: Any) -> None:
    corpus, source, _ = await _website_world(world, app)
    _ok(
        await _admin(app, "DELETE", f"/api/v1/corpora/{corpus.id}/website/sources/{source.id}"), 204
    )


async def website_page_excluded(world: World, app: Any) -> None:
    corpus, _, page = await _website_world(world, app, page_status="skipped")
    res = await _admin(
        app,
        "PATCH",
        f"/api/v1/corpora/{corpus.id}/website/pages/{page.id}",
        json={"excluded": True},
    )
    _ok(res, 200)


async def website_page_included(world: World, app: Any) -> None:
    from unittest.mock import patch

    corpus, _, page = await _website_world(world, app, page_status="excluded")
    with patch("callobuzz_rag.website.service.dispatch_urls", return_value=True):
        res = await _admin(
            app,
            "PATCH",
            f"/api/v1/corpora/{corpus.id}/website/pages/{page.id}",
            json={"excluded": False},
        )
    _ok(res, 200)


async def website_reread_requested(world: World, app: Any) -> None:
    from unittest.mock import patch

    corpus, _, _ = await _website_world(world, app)
    with patch("callobuzz_rag.website.service.dispatch_learn", return_value=True):
        res = await _admin(app, "POST", f"/api/v1/corpora/{corpus.id}/website/reread")
    _ok(res, 202)


# /BIZ1 learning


# BIZ4 onboarding: first-run setup and the assistant's picture
async def assistant_avatar_uploaded(world: World, app: Any) -> None:
    import io
    from unittest.mock import AsyncMock, patch

    from PIL import Image

    corpus, _source, _ = await _website_world(world, app)
    picture = io.BytesIO()
    Image.new("RGB", (200, 200), (30, 120, 200)).save(picture, "PNG")
    with patch("callobuzz_rag.assets.avatars.object_storage") as storage:
        storage.return_value.upload = AsyncMock()
        res = await req(
            app,
            "POST",
            f"/api/v1/corpora/{corpus.id}/assistant/avatar",
            key=ADMIN,
            files={"file": ("face.png", picture.getvalue(), "image/png")},
        )
    _ok(res, 201)


async def onboarding_website_started(world: World, app: Any) -> None:
    from unittest.mock import patch

    from callobuzz_rag.website import netguard
    from tests.unit.test_onboarding_api import SITE, FakeEngine, public_dns  # noqa: F401

    async def public(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    _asha(world)
    token = (await _token(app))["access_token"]
    engine = FakeEngine(world, world.shree)
    with (
        patch.object(netguard, "resolve_host", public),
        patch("callobuzz_rag.onboarding.service.make_client", lambda _request: engine),
    ):
        res = await req(
            app,
            "POST",
            "/api/v1/onboarding/website",
            token=token,
            json={"website": SITE},
        )
    _ok(res, 200)


# /BIZ4 onboarding


# TV4 value
async def value_settings_updated(world: World, app: Any) -> None:
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.business.value import ValueSettingsIn
    from callobuzz_rag.business.value_report import SettingsState, WorkspaceContext

    _asha(world)
    token = (await _token(app))["access_token"]
    ctx = WorkspaceContext("clinic", "Asia/Kolkata", "INR")
    values = ValueSettingsIn(150_000, 80_000, 25_000, 3)
    state = SettingsState(values, True, "INR", True, None, "clinic")
    base = "callobuzz_rag.api.routes.value.service."
    with (
        patch(base + "workspace_context", AsyncMock(return_value=ctx)),
        patch(base + "save_settings", AsyncMock(return_value=["lead_value_minor"])),
        patch(base + "settings_state", AsyncMock(return_value=state)),
    ):
        _ok(
            await req(
                app,
                "PUT",
                "/api/v1/value/settings",
                token=token,
                json={
                    "lead_value_minor": 150000,
                    "handoff_value_minor": 80000,
                    "staff_hour_minor": 25000,
                    "minutes_per_answer": 3,
                },
            )
        )


# /TV4 value


# TV6 whatsapp
def _whatsapp_graph(*replies: Any) -> Any:
    from types import SimpleNamespace
    from unittest.mock import patch

    from callobuzz_rag.channels.whatsapp.graph import GraphClient
    from tests.unit.whatsapp_fixtures import FakeGraph

    client = GraphClient(
        settings=SimpleNamespace(
            whatsapp_graph_url="https://graph.facebook.com",
            whatsapp_graph_version="v23.0",
            whatsapp_app_id="1",
            whatsapp_app_secret="s",
        ),
        transport=FakeGraph(script=list(replies), reply={"success": True}).transport,
    )
    return patch("callobuzz_rag.channels.whatsapp.service.make_graph", lambda settings: client)


_WA_NUMBER = (200, {"display_phone_number": "+91 98200 11111", "verified_name": "Shree"})
_WA_MANUAL = {
    "phone_number_id": "1234567890",
    "waba_id": "5550001",
    "access_token": "EAAG-permanent-token-ab12",
    "meta_billing": "deployer",
}


def _whatsapp_app(world: World, app: Any) -> Any:
    from tests.unit.whatsapp_world import seed_whatsapp, whatsapp_settings

    corpus, connection, thread = seed_whatsapp(world)
    app.state.settings = app.state.settings.model_copy(update=whatsapp_settings())
    return corpus, connection, thread


async def whatsapp_connected(world: World, app: Any) -> None:
    corpus, connection, _thread = _whatsapp_app(world, app)
    world.db.rows[type(connection)].remove(connection)  # a knowledge base with no number yet
    with _whatsapp_graph(_WA_NUMBER, (200, {"success": True})):
        _ok(
            await _admin(
                app, "PUT", f"/api/v1/corpora/{corpus.id}/whatsapp/manual", json=_WA_MANUAL
            )
        )


async def whatsapp_token_updated(world: World, app: Any) -> None:
    corpus, _connection, _thread = _whatsapp_app(world, app)
    with _whatsapp_graph(_WA_NUMBER, (200, {"success": True})):
        _ok(
            await _admin(
                app, "PUT", f"/api/v1/corpora/{corpus.id}/whatsapp/manual", json=_WA_MANUAL
            )
        )


async def whatsapp_settings_updated(world: World, app: Any) -> None:
    corpus, _connection, _thread = _whatsapp_app(world, app)
    _asha(world)
    token = (await _token(app))["access_token"]
    _ok(
        await req(
            app,
            "PATCH",
            f"/api/v1/corpora/{corpus.id}/whatsapp",
            token=token,
            json={"human_pause_hours": 6},
        )
    )


async def whatsapp_disconnected(world: World, app: Any) -> None:
    corpus, _connection, _thread = _whatsapp_app(world, app)
    _asha(world)
    token = (await _token(app))["access_token"]
    with _whatsapp_graph():
        _ok(await req(app, "DELETE", f"/api/v1/corpora/{corpus.id}/whatsapp", token=token), 204)


async def whatsapp_reply_sent(world: World, app: Any) -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    from callobuzz_rag.channels.whatsapp.graph import GraphClient
    from tests.unit.whatsapp_fixtures import FakeGraph
    from tests.unit.whatsapp_world import seed_whatsapp, whatsapp_settings

    corpus, _connection, thread = seed_whatsapp(world)
    app.state.settings = app.state.settings.model_copy(update=whatsapp_settings())
    _asha(world)
    token = (await _token(app))["access_token"]
    client = GraphClient(
        settings=SimpleNamespace(
            whatsapp_graph_url="https://graph.facebook.com",
            whatsapp_graph_version="v23.0",
            whatsapp_app_id="1",
            whatsapp_app_secret="s",
        ),
        transport=FakeGraph().transport,
    )
    with patch("callobuzz_rag.channels.whatsapp.service.make_graph", lambda settings: client):
        _ok(
            await req(
                app,
                "POST",
                f"/api/v1/corpora/{corpus.id}/whatsapp/threads/{thread.id}/reply",
                token=token,
                json={"text": "We open at 9."},
            ),
            201,
        )


# /TV6 whatsapp


# P3 legal
async def legal_page_published(world: World, app: Any) -> None:
    from callobuzz_rag.db.models.engine_settings import EngineSettingsRow

    world.db.add(
        EngineSettingsRow(
            id=1,
            value={
                "company": {
                    "legal_name": "Sharma Furniture",
                    "address_line1": "12 MG Road",
                    "city": "Pune",
                    "state_code": "27",
                    "pincode": "411001",
                    "email": "accounts@sharmafurniture.in",
                    "phone": "+91 98765 43210",
                },
                "legal": {
                    "grievance_name": "Asha Sharma",
                    "grievance_email": "grievance@sharmafurniture.in",
                    "grievance_address": "12 MG Road, Pune",
                    "jurisdiction_city": "Pune",
                    "website": "https://sharmafurniture.in",
                },
                "branding": {"support_email": "help@sharmafurniture.in"},
            },
        )
    )
    _ok(
        await _admin(
            app, "POST", "/api/v1/admin/legal/terms/publish", json={"confirm_reviewed": True}
        )
    )


async def legal_settings_updated(world: World, app: Any) -> None:
    _ok(
        await _admin(app, "PUT", "/api/v1/admin/legal/settings", json={"jurisdiction_city": "Pune"})
    )


# /P3 legal


# LP1 landing
async def landing_settings_updated(world: World, app: Any) -> None:
    _ok(
        await _admin(
            app,
            "PUT",
            "/api/v1/admin/landing/settings",
            json={"address": "https://sharmadental.in"},
        )
    )


async def landing_published(world: World, app: Any) -> None:
    _ok(await _admin(app, "POST", "/api/v1/admin/landing/publish", json={"base_revision": 1}))


async def landing_version_restored(world: World, app: Any) -> None:
    await landing_published(world, app)
    _ok(
        await _admin(
            app,
            "POST",
            "/api/v1/admin/landing/versions/1/restore",
            json={"base_revision": 1},
        )
    )


async def landing_asset_uploaded(world: World, app: Any) -> None:
    import io
    import tempfile
    from pathlib import Path
    from unittest.mock import AsyncMock, patch

    from PIL import Image

    picture = io.BytesIO()
    Image.new("RGB", (300, 120), (15, 118, 110)).save(picture, "PNG")
    with (
        tempfile.TemporaryDirectory() as cache,
        patch("callobuzz_rag.landing.assets.object_storage") as storage,
        patch("callobuzz_rag.landing.assets.cache_dir", return_value=Path(cache)),
    ):
        storage.return_value.upload = AsyncMock()
        res = await req(
            app,
            "POST",
            "/api/v1/admin/landing/assets",
            key=ADMIN,
            data={"kind": "logo"},
            files={"file": ("logo.png", picture.getvalue(), "image/png")},
        )
    _ok(res, 200)


async def landing_asset_deleted(world: World, app: Any) -> None:
    import tempfile
    from pathlib import Path
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.db.models.landing import LandingAsset

    asset = world.db.add(
        LandingAsset(
            kind="logo",
            content_type="image/png",
            bytes=10,
            sha256="0" * 64,
            storage_key="landing/logo/x.png",
            created_by="admin key",
        )
    )
    with (
        tempfile.TemporaryDirectory() as cache,
        patch("callobuzz_rag.landing.assets.object_storage") as storage,
        patch("callobuzz_rag.landing.assets.cache_dir", return_value=Path(cache)),
    ):
        storage.return_value.delete = AsyncMock()
        res = await _admin(app, "DELETE", f"/api/v1/admin/landing/assets/{asset.id}")
    _ok(res, 204)


# /LP1 landing


# LP2 landing
async def landing_generated(world: World, app: Any) -> None:
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.landing.generate import Context, Passage

    operator = await _operator_workspace(world)
    corpus = world.db.add(
        Corpus(name="Ours", tenant_id=operator.id, qdrant_collection="ours", api_key_hash="")
    )
    reply = {
        "turns": [
            {"role": "visitor", "text": "Fees kitni hai?"},
            {"role": "assistant", "text": "Consultation is 500 rupees.", "source": 1},
        ]
    }
    llm = SimpleNamespace(
        has_credentials=True,
        fast_call_options=lambda: {},
        complete_json=AsyncMock(return_value=SimpleNamespace(content=json.dumps(reply))),
    )
    context = Context([Passage("Fees", "Fees", "Consultation 500 rupees.")], None)
    with (
        patch("callobuzz_rag.landing.generate.gather_context", AsyncMock(return_value=context)),
        patch(
            "callobuzz_rag.llm.litellm_provider.LiteLLMProvider.from_resolved_config",
            return_value=llm,
        ),
        patch(
            "callobuzz_rag.tiers.resolve.resolve_with_profile",
            return_value=(SimpleNamespace(model="m", payer="operator"), None),
        ),
        patch("callobuzz_rag.tiers.profiles.plan_profile", AsyncMock(return_value=None)),
        patch("callobuzz_rag.api.routes.query._get_config_resolver", return_value=object()),
        patch("callobuzz_rag.llm.cost.request_cost_of", return_value=None),
    ):
        _ok(
            await _admin(
                app,
                "POST",
                "/api/v1/admin/landing/generate",
                json={"corpus_id": str(corpus.id), "language": "en"},
            )
        )


# /LP2 landing


# LP4 landing
async def landing_assistant_connected(world: World, app: Any) -> None:
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.widget_key import WidgetKey

    operator = await _operator_workspace(world)
    corpus = world.db.add(
        Corpus(name="Ours", tenant_id=operator.id, qdrant_collection="ours", api_key_hash="")
    )
    world.db.add(
        WidgetKey(
            corpus_id=corpus.id,
            name="Site",
            public_key="pk_landing_x",
            allowed_origins=["https://sharmadental.in"],
            is_active=True,
        )
    )
    _ok(
        await _admin(
            app,
            "PUT",
            "/api/v1/admin/landing/settings",
            json={"address": "https://sharmadental.in"},
        )
    )
    _ok(
        await _admin(
            app, "POST", "/api/v1/admin/landing/assistant", json={"corpus_id": str(corpus.id)}
        )
    )


async def landing_assistant_disconnected(world: World, app: Any) -> None:
    _ok(await _admin(app, "DELETE", "/api/v1/admin/landing/assistant"))


# /LP4 landing


PRODUCERS: dict[str, Driver] = {
    "landing.settings_updated": landing_settings_updated,  # LP1 landing
    "landing.published": landing_published,  # LP1 landing
    "landing.version_restored": landing_version_restored,  # LP1 landing
    "landing.asset_uploaded": landing_asset_uploaded,  # LP1 landing
    "landing.asset_deleted": landing_asset_deleted,  # LP1 landing
    "landing.generated": landing_generated,  # LP2 landing
    "landing.assistant_connected": landing_assistant_connected,  # LP4 landing
    "landing.assistant_disconnected": landing_assistant_disconnected,  # LP4 landing
    "legal.page_published": legal_page_published,  # P3 legal
    "legal.settings_updated": legal_settings_updated,  # P3 legal
    "settings.updated": settings_updated,
    "whatsapp.reply_sent": whatsapp_reply_sent,  # TV6
    "whatsapp.connected": whatsapp_connected,  # TV6
    "whatsapp.token_updated": whatsapp_token_updated,  # TV6
    "whatsapp.settings_updated": whatsapp_settings_updated,  # TV6
    "whatsapp.disconnected": whatsapp_disconnected,  # TV6
    "value.settings_updated": value_settings_updated,  # TV4
    "billing.provider_updated": provider_updated,  # BIL1
    "billing.pricing_updated": billing_pricing_updated,  # BIL2
    "billing.account_updated": billing_account_updated,  # BIL2
    "billing.ledger_entry": billing_ledger_entry,  # BIL3
    "billing.reconciliation_failed": billing_reconciliation_failed,  # BIL3
    "billing.paused": billing_paused,  # BIL4
    "billing.resumed": billing_resumed,  # BIL4
    "billing.add_on_created": billing_add_on_created,  # BIL5
    "billing.add_on_updated": billing_add_on_updated,  # BIL5
    "billing.add_on_deleted": billing_add_on_deleted,  # BIL5
    "billing.add_on_granted": billing_add_on_granted,  # BIL5
    "billing.grant_revoked": billing_grant_revoked,  # BIL5
    "billing.service_order_updated": billing_service_order_updated,  # P5 plans
    "billing.add_on_cancelled": billing_add_on_cancelled,  # P5 plans
    "billing.top_up_requested": billing_top_up_requested,  # BIL6
    "billing.price_change_scheduled": billing_price_change_scheduled,  # TV3
    "billing.price_change_cancelled": billing_price_change_cancelled,  # TV3
    "billing.pending_change_scheduled": billing_pending_change_scheduled,  # TV3
    "billing.pending_change_applied": billing_pending_change_applied,  # TV3
    "billing.pending_change_cancelled": billing_pending_change_cancelled,  # TV3
    "billing.change_agreed": billing_change_agreed,  # TV3
    "billing.plan_change_applied": billing_plan_change_applied,  # P6 plan choice
    "billing.plan_change_awaiting_payment": billing_plan_change_awaiting_payment,  # P6
    "billing.plan_change_scheduled": billing_plan_change_scheduled,  # P6 plan choice
    "billing.spend_cap_set": billing_spend_cap_set,  # TV3
    "billing.spend_cap_reached": billing_spend_cap_reached,  # TV3
    # P1 payments
    "payments.settings_updated": payments_settings_updated,
    "billing.payment_received": billing_payment_received,
    "billing.payment_mismatch": billing_payment_mismatch,
    "billing.chargeback": billing_chargeback,
    "billing.refund_requested": billing_refund_requested,
    "billing.refund_issued": billing_refund_issued,
    "billing.refund_failed": billing_refund_failed,
    "billing.payment_link_created": billing_payment_link_created,
    "billing.tax_details_updated": billing_tax_details_updated,
    "billing.tax_profile_updated": billing_tax_profile_updated,
    # /P1 payments
    "billing.invoice_issued": billing_invoice_issued,  # P2 invoices
    "billing.credit_note_issued": billing_credit_note_issued,  # P2 invoices
    "support.request_created": support_request_created,  # TV3
    "support.request_status_changed": support_request_status_changed,  # TV3
    "settings.price_sheet_link_rotated": settings_price_sheet_link_rotated,  # TV3
    "settings.price_sheet_link_removed": settings_price_sheet_link_removed,  # TV3
    "superadmin.bootstrapped": superadmin_bootstrapped,
    "demo.reset": demo_reset,  # L5a demo
    "user.password_reset": password_reset,
    "user.password_changed": password_changed,
    "user.login_locked": login_locked,
    "session.refresh_reused": refresh_reused,
    "tenant.signed_up": signed_up,
    "tenant.activated": activated,
    "tenant.suspended": suspended,
    "tenant.reactivated": reactivated,
    "tenant.declined": declined,
    "tenant.deleted": deleted,
    "tenant.created": created,
    "member.invited": invited,
    "invitation.revoked": invitation_revoked,
    "invitation.accepted": invitation_accepted,
    "member.role_changed": role_changed,
    "member.removed": removed,
    "plan.created": plan_created,
    "plan.updated": plan_updated,
    "plan.deleted": plan_deleted,
    "tenant.plan_changed": plan_changed,
    "pipeline.profile_created": pipeline_profile_created,  # PIP1 profile
    "pipeline.profile_updated": pipeline_profile_updated,
    "pipeline.profile_deleted": pipeline_profile_deleted,
    "pipeline.default_set": pipeline_default_set,
    "pipeline.tenant_profile_set": pipeline_tenant_profile_set,
    "fair_use.overrides_updated": fair_use_overrides_updated,  # PIP3 fair use
    "fair_use.throttled": fair_use_throttled,
    "fair_use.stopped": fair_use_stopped,
    "fair_use.resumed": fair_use_resumed,
    "fair_use.engine_spend_reached": fair_use_engine_spend_reached,
    "operator_workspace.created": operator_workspace_created,
    "operator_workspace.suspend_refused": operator_suspend_refused,
    "operator_workspace.delete_refused": operator_delete_refused,
    "api_key.created": api_key_created,
    "api_key.rotated": api_key_rotated,
    "api_key.updated": api_key_updated,
    "api_key.revoked": api_key_revoked,
    "assistant.published": assistant_published,
    "assistant.rolled_back": assistant_rolled_back,
    "assistant.enabled_changed": assistant_enabled_changed,
    "assistant.draft_discarded": assistant_draft_discarded,
    "assistant.setup_applied": assistant_setup_applied,
    "lead.exported": lead_exported,
    "lead.deleted": lead_deleted,
    "lead.withdrawn": lead_withdrawn,
    "lead.retention_purged": lead_retention_purged,
    "corpus_webhook.created": corpus_webhook_created,
    "corpus_webhook.deleted": corpus_webhook_deleted,
    "cob.resync_requested": cob_resync_requested,  # COB1
    "cob.action": cob_action,  # COB5
    "cob.platform_doc_audience": cob_platform_doc_audience,  # COB9
    "cob.platform_doc_deleted": cob_platform_doc_deleted,  # COB9
    # BIZ1 learning
    "website.learning_started": website_learning_started,
    "website.settings_updated": website_settings_updated,
    "website.source_removed": website_source_removed,
    "website.page_excluded": website_page_excluded,
    "website.page_included": website_page_included,
    "website.reread_requested": website_reread_requested,
    # /BIZ1 learning
    # BIZ2 freshness
    "website.learn_key_created": website_learn_key_created,
    "website.learn_key_revoked": website_learn_key_revoked,
    # /BIZ2 freshness
    # BIZ4 onboarding
    "assistant.avatar_uploaded": assistant_avatar_uploaded,
    "onboarding.website_started": onboarding_website_started,
    # /BIZ4 onboarding
}


def test_every_catalog_action_has_a_producer() -> None:
    assert set(PRODUCERS) == set(AUDIT_ACTIONS)


@pytest.mark.parametrize("action", sorted(PRODUCERS))
async def test_operation_writes_exactly_one_row(action: str) -> None:
    from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache

    invalidate_engine_settings_cache()
    world = World()
    with app_for(world) as app:
        await PRODUCERS[action](world, app)
    rows = world.audits(action)
    assert len(rows) == 1, [(a.action, a.details) for a in world.db.all(AuditLog)]
    (row,) = rows
    assert row.actor_label and row.actor_kind in {
        "admin_key",
        "tenant_key",
        "corpus_key",
        "user",
        "system",
    }
    invalidate_engine_settings_cache()
