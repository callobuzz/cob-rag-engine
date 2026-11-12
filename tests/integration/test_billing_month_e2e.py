"""A month of billing on real PostgreSQL (BIL7). Needs Docker.

One customer on a prepaid "Pro" plan (Rs 999 a month, Rs 2 a question on the operator's key up to
the plan's 1,000 a month, then Rs 3, Rs 200 of included credit that expires, her own AI key free
and not counted). The super admin and the owner act through the real HTTP routes (plan, pricing,
top-ups, add-ons, the answering routes, the widget, statement, P&L); the AI is a stub with fixed
costs (no model is called). Money moves on an October clock: the ledger reads a frozen "now"
while the routes run, and the schedule and posting take `now=`, so October is October whatever
day the test runs.

In order: the balance after the subscription, included credit and the first answers (a replayed
request id is charged once); a "500 extra questions" add-on; 2,000 questions through
`POST /api/v1/query` on a knowledge base whose assistant is off, posted every 50 - the plan's
allowance runs out (overage at Rs 3), the balance runs out, and from then on every ask is a 402
(the account is paused, the owner got the 20 % and 5 % notices and one paused notice, the
operator got the paused one too); the widget of the other knowledge base answers a question with
the pause message and the handoff card only, and still answers a greeting; a top-up through the
admin route (a tenant key is refused, a large amount needs typing twice, a repeated key is one
ledger row, one audit row per money entry) lifts the pause; a credit pack; November's
subscription closes October; the owner's statement for October opens at 0, adds up and closes on
the ledger's balance; the P&L's billed and collected columns equal what happened; reconciliation
finds nothing wrong for October 31 and for the real day.
"""

import asyncio
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.analytics.usage import PAYER_BYOK, record_usage
from callobuzz_rag.api.schemas.billing_addons import ApplyAddOnRequest, CreateAddOnRequest
from callobuzz_rag.billing import addons, hooks, reconcile, statements
from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.enforcement import billing_state
from callobuzz_rag.billing.posting import post_pending
from callobuzz_rag.billing.subscriptions import run_due
from callobuzz_rag.conversation.schema import DEFAULT_PAUSED
from callobuzz_rag.llm.cost import CallCost, CostLedger
from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

# the website's assistant answers through the director, so the autouse `_assistant_off` must not
# replace it (tests/conftest.py)
pytestmark = [pytest.mark.integration, pytest.mark.assistant_on]

ADMIN = "admin-key-integration"
ORIGIN = "https://acme.example"
OCT_1 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
OCT_20 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
OCT_25 = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
NOV_1 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
OCT_31 = date(2026, 10, 31)
MINUTE = timedelta(minutes=1)

# 1,000 questions a month on the plan, +500 from the add-on; Rs 2 each, then Rs 3 each.
PLAN_QUESTIONS = 1_000
PACK_QUESTIONS = 500
ASKS = 2_000

PRO = {
    "currency": "INR",
    "subscription_fee_minor": 99_900,
    "billing_period": "monthly",
    "usage_mode": "per_question",
    "per_question_minor": 200,
    "byok_mode": "free",
    "byok_counts_toward_caps": False,
    "overage": "charge",
    "overage_mode": "per_question",
    "overage_per_question_minor": 300,
    "payment_type": "prepaid",
    "grace_minor": 0,
    "included_credit_minor": 20_000,
    "included_credit_expires": True,
    "alert_thresholds_pct": [20, 5, 0],
}

# What the flow charges, worked out here and not read back from the system under test.
FIRST_ANSWERS = 191  # 150 api + 40 widget + 1 replayed id (charged once); 5 on her own key are free
BASE_PRICE, OVERAGE_PRICE = 200, 300
ALLOWANCE_LEFT = PLAN_QUESTIONS + PACK_QUESTIONS - FIRST_ANSWERS  # questions still at Rs 2
START_BALANCE = 500_000 - 99_900 + 20_000 - FIRST_ANSWERS * BASE_PRICE
AFTER_ALLOWANCE = START_BALANCE - ALLOWANCE_LEFT * BASE_PRICE
OVERAGE_ANSWERS = AFTER_ALLOWANCE // OVERAGE_PRICE + 1  # the answer that takes it to <= 0 is served
ANSWERED = ALLOWANCE_LEFT + OVERAGE_ANSWERS
PAUSED_BALANCE = AFTER_ALLOWANCE - OVERAGE_ANSWERS * OVERAGE_PRICE
USAGE_MINOR = (
    FIRST_ANSWERS * BASE_PRICE + ALLOWANCE_LEFT * BASE_PRICE + OVERAGE_ANSWERS * OVERAGE_PRICE
)
TOP_UP_1, TOP_UP_2, PACK_CREDIT = 500_000, 1_200_000, 100_000  # Rs 12,000 needs typing twice


class FrozenDateTime(datetime):
    """The ledger's `datetime.now()`, held at the test's current October moment."""

    now_is: datetime = OCT_1

    @classmethod
    def now(cls, tz: Any = None) -> "FrozenDateTime":  # type: ignore[override]
        return cls.now_is  # type: ignore[return-value]


def _orchestrator() -> MagicMock:
    """Answers at once with one fixed-cost model call on its ledger, like a configured LLM."""
    orch = MagicMock()
    orch.llm = SimpleNamespace(cost_ledger=CostLedger())

    async def answer(**_: Any) -> dict[str, Any]:
        orch.llm.cost_ledger.calls.clear()
        orch.llm.cost_ledger.add(
            CallCost(model="stub-model", prompt_tokens=10, completion_tokens=10, cost_usd=0.001)
        )
        return {
            "answer": "42",
            "citations": [],
            "confidence": 0.9,
            "query_id": uuid.uuid4(),
            "model_used": "stub-model",
            "latency_ms": 1,
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }

    orch.query = AsyncMock(side_effect=answer)
    return orch


def _frames(body: str) -> list[tuple[str, dict[str, Any]]]:
    """The Server-Sent Events of a widget reply as (event, data)."""
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        event, data = None, {}
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if event:
            frames.append((event, data))
    return frames


def _reply(body: str) -> dict[str, Any]:
    frames = _frames(body)
    return {
        "text": "".join(d.get("content", "") for e, d in frames if e == "token"),
        "route": next((d["route"] for e, d in frames if e == "done"), None),
        "offers": [d["kind"] for e, d in frames if e == "ui"],
    }


async def _flow(db_url: str) -> dict[str, Any]:
    from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.core.config import Settings

    invalidate_engine_settings_cache()
    engine = create_async_engine(db_url, pool_size=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        admin_api_key=ADMIN,  # type: ignore[arg-type]
        # a real install has one (scripts/generate_secrets.py); without it leads are off, so the
        # handoff card has no callback to offer and the paused visitor would get no card
        encryption_key=Fernet.generate_key().decode(),
    )
    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false"}):
        app = create_app()
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.vector_store = AsyncMock()
    app.state.embedder = None
    app.state.reranker = None
    app.state.assistant_caches = (None, None)
    app.state.widget_rate_limiter = SimpleNamespace(
        check=AsyncMock(return_value=SimpleNamespace(allowed=True, reason="", retry_after=0))
    )
    no_cache = MagicMock()
    no_cache.get = AsyncMock(return_value=None)
    no_cache.set = AsyncMock()
    admin = {"X-API-Key": ADMIN}
    out: dict[str, Any] = {}
    mails: dict[str, list[str]] = {"billing_paused": [], "billing_low_balance": []}

    def enqueue(template: str, to: str, context: Any, **_: Any) -> None:
        mails.setdefault(template, []).append(to)

    async def owners(session: Any, tenant_id: Any) -> list[str]:
        return ["owner@acme.example"]

    operator_settings = settings.model_copy(update={"superadmin_email": "sv@operator.example"})

    async def answer(
        tenant_id: uuid.UUID, corpus_id: uuid.UUID, n: int, source: str, **kw: Any
    ) -> None:
        for _ in range(n):
            async with factory() as s:
                await record_usage(
                    s,
                    corpus_id=corpus_id,
                    tenant_id=tenant_id,
                    source=source,
                    prompt_tokens=10,
                    completion_tokens=10,
                    model="stub-model",
                    request_id=uuid.uuid4(),
                    **kw,
                )

    try:
        with (
            patch(
                "callobuzz_rag.api.routes.query._build_orchestrator",
                side_effect=lambda *a, **k: _orchestrator(),
            ),
            patch("callobuzz_rag.api.routes.query._get_query_cache", return_value=no_cache),
            patch("callobuzz_rag.storage.cleanup.schedule_prefix_cleanup", AsyncMock()),
            patch("callobuzz_rag.worker.tasks.ingest_records_task", MagicMock()),
            patch("callobuzz_rag.billing.ledger.datetime", FrozenDateTime),
            patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", enqueue),
            patch(
                "callobuzz_rag.core.webhooks.WebhookDispatcher",
                lambda *a, **k: SimpleNamespace(dispatch=AsyncMock()),
            ),
            patch("callobuzz_rag.core.config.get_settings", return_value=operator_settings),
            patch.object(hooks, "_owner_emails", owners),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                plan = await c.post(
                    "/api/v1/admin/plans",
                    json={
                        "name": "Pro",
                        "max_documents": 1000,
                        "monthly_questions": PLAN_QUESTIONS,
                    },
                    headers=admin,
                )
                assert plan.status_code in (200, 201), plan.text
                plan_id = plan.json()["id"]
                put = await c.put(
                    f"/api/v1/admin/billing/plans/{plan_id}/pricing", json=PRO, headers=admin
                )
                assert put.status_code == 200, put.text
                made = await c.post("/api/v1/admin/tenants", json={"name": "Acme"}, headers=admin)
                tid = made.json()["id"]
                owner = {"X-API-Key": made.json()["api_key"]}
                # a dearer plan waits for the notice period unless the business asked (TV3)
                moved = await c.post(
                    f"/api/v1/admin/tenants/{tid}/plan",
                    json={
                        "plan_id": plan_id,
                        "business_agreed": True,
                        "reason": "Acme asked to move to Pro.",
                    },
                    headers=admin,
                )
                assert moved.status_code == 200, moved.text
                assert moved.json()["plan_id"] == plan_id, moved.text
                kb = await c.post("/api/v1/corpora", json={"name": "Help"}, headers=owner)
                api_kb = await c.post("/api/v1/corpora", json={"name": "API"}, headers=owner)
                assert kb.status_code == 201 and api_kb.status_code == 201, (kb.text, api_kb.text)
                tenant_id, corpus_id = uuid.UUID(tid), uuid.UUID(kb.json()["id"])
                api_corpus_id = api_kb.json()["id"]
                # the website's assistant is on, the API knowledge base's is off (the legacy path)
                for corpus, enabled in ((corpus_id, True), (api_corpus_id, False)):
                    flipped = await c.post(
                        f"/api/v1/corpora/{corpus}/assistant/enabled",
                        json={"enabled": enabled},
                        headers=owner,
                    )
                    assert flipped.status_code == 200, flipped.text
                key = await c.post(
                    f"/api/v1/corpora/{corpus_id}/widget-keys",
                    json={"name": "Site", "allowed_origins": [ORIGIN]},
                    headers=owner,
                )
                assert key.status_code == 201, key.text
                widget = {"X-Widget-Key": key.json()["public_key"], "Origin": ORIGIN}
                async with factory() as s:
                    account = await ensure_account(s, tenant_id)
                    account_id = account.id
                    await s.commit()

                async def balance() -> int:
                    async with factory() as s:
                        row = await s.execute(
                            text("SELECT balance_minor FROM billing_accounts WHERE id = :a"),
                            {"a": account_id},
                        )
                        return int(row.scalar_one())

                async def scalar(sql: str, **params: Any) -> Any:
                    async with factory() as s:
                        return (await s.execute(text(sql), params)).scalar_one()

                async def paused() -> bool:
                    async with factory() as s:
                        state = await billing_state(s, tenant_id, payer="operator")
                        return state.paused

                async def top_up(
                    amount: int, key: str, at: datetime, confirm: int | None = None
                ) -> Any:
                    FrozenDateTime.now_is = at
                    body: dict[str, Any] = {
                        "entry_type": "top_up",
                        "amount_minor": amount,
                        "idempotency_key": key,
                    }
                    if confirm is not None:
                        body["confirm_amount_minor"] = confirm
                    return await c.post(
                        f"/api/v1/admin/billing/accounts/{tid}/ledger", json=body, headers=admin
                    )

                async def say(message: str) -> dict[str, Any]:
                    r = await c.post(
                        "/api/v1/widget/chat", json={"message": message}, headers=widget
                    )
                    assert r.status_code == 200, r.text
                    return _reply(r.text)

                # ---- October 1: Rs 5,000 paid in by the super admin, then the schedule runs
                first = await top_up(TOP_UP_1, "topup-oct-0001", OCT_1)
                assert first.status_code == 201, first.text
                await run_due(factory, now=OCT_1)
                out["after_schedule"] = await balance()  # 5,000 - 999 + 200

                # ---- 190 answers on the operator's key, 5 on her own (free), one replayed id
                await answer(tenant_id, corpus_id, 150, "api")
                await answer(tenant_id, corpus_id, 40, f"widget:{uuid.uuid4()}")
                await answer(tenant_id, corpus_id, 5, "conversation", payer=PAYER_BYOK)
                retried = uuid.uuid4()
                for _ in range(3):
                    async with factory() as s:
                        await record_usage(
                            s,
                            corpus_id=corpus_id,
                            tenant_id=tenant_id,
                            source="api",
                            prompt_tokens=1,
                            completion_tokens=1,
                            model="stub-model",
                            request_id=retried,
                        )
                await post_pending(factory, now=OCT_1 + MINUTE)
                out["after_first_usage"] = await balance()  # 191 charged questions x Rs 2

                # ---- the "500 extra questions" add-on, paid outside the engine
                made_pack = await c.post(
                    "/api/v1/admin/billing/add-ons",
                    json={
                        "kind": "question_pack",
                        "name": "500 extra questions",
                        "currency": "INR",
                        "price_minor": 50_000,
                        "questions": PACK_QUESTIONS,
                    },
                    headers=admin,
                )
                assert made_pack.status_code == 201, made_pack.text
                granted = await c.post(
                    f"/api/v1/admin/billing/accounts/{tid}/grants",
                    json={
                        "add_on_id": made_pack.json()["id"],
                        "payment": "external",
                        "idempotency_key": "qpack-oct-0001",
                    },
                    headers=admin,
                )
                assert granted.status_code == 201, granted.text
                assert await balance() == out["after_first_usage"]  # no money moved

                # ---- 2,000 questions through the real route, posted every 50
                statuses: list[int] = []
                for n in range(1, ASKS + 1):
                    r = await c.post(
                        "/api/v1/query",
                        json={"query": f"question {n}", "corpus_id": api_corpus_id},
                        headers=owner,
                    )
                    statuses.append(r.status_code)
                    if n % 50 == 0:
                        await post_pending(factory, now=OCT_20)
                await post_pending(factory, now=OCT_20)
                out["statuses"] = statuses
                out["paused_codes"] = {await _one_paused(c, api_corpus_id, owner)}
                out["paused_after_asks"] = await paused()
                out["paused_at_stored"] = await scalar(
                    "SELECT paused_at IS NOT NULL FROM billing_accounts WHERE id = :a",
                    a=account_id,
                )
                out["balance_paused"] = await balance()
                out["mails_when_paused"] = {k: list(v) for k, v in mails.items()}

                # ---- visitors on the website: the pause message and the handoff card; a
                # greeting is free and is still answered
                charges_before = await scalar(
                    "SELECT count(*) FROM usage_charges WHERE account_id = :a", a=account_id
                )
                out["widget_paused"] = await say("what is the price?")
                out["widget_hello"] = await say("hello")
                out["widget_charges"] = (
                    await scalar(
                        "SELECT count(*) FROM usage_charges WHERE account_id = :a", a=account_id
                    )
                ) - charges_before

                # ---- the super admin tops up: a tenant key is refused, a large amount must be
                # typed twice, a repeated key is one ledger row
                FrozenDateTime.now_is = OCT_25
                refused = await c.post(
                    f"/api/v1/admin/billing/accounts/{tid}/ledger",
                    json={
                        "entry_type": "top_up",
                        "amount_minor": 100,
                        "idempotency_key": "topup-owner-01",
                    },
                    headers=owner,
                )
                out["owner_top_up"] = refused.status_code
                unconfirmed = await top_up(TOP_UP_2, "topup-oct-0002", OCT_25)
                out["unconfirmed"] = (unconfirmed.status_code, unconfirmed.json().get("code"))
                out["paused_until_confirmed"] = await paused()
                typed = await top_up(TOP_UP_2, "topup-oct-0002", OCT_25, confirm=TOP_UP_2)
                assert typed.status_code == 201, typed.text
                repeat = await top_up(TOP_UP_2, "topup-oct-0002", OCT_25, confirm=TOP_UP_2)
                out["repeat"] = (repeat.status_code, repeat.json()["id"] == typed.json()["id"])
                out["top_up_rows"] = await scalar(
                    "SELECT count(*) FROM wallet_ledger WHERE account_id = :a "
                    "AND idempotency_key = 'topup:topup-oct-0002'",
                    a=account_id,
                )
                out["audit_rows"] = await scalar(
                    "SELECT count(*) FROM audit_log WHERE tenant_id = :t "
                    "AND action = 'billing.ledger_entry'",
                    t=tenant_id,
                )
                out["paused_after_top_up"] = await paused()
                out["paused_at_cleared"] = await scalar(
                    "SELECT paused_at IS NULL FROM billing_accounts WHERE id = :a", a=account_id
                )
                out["after_top_up_answer"] = (
                    await c.post(
                        "/api/v1/query",
                        json={"query": "back again", "corpus_id": api_corpus_id},
                        headers=owner,
                    )
                ).status_code
                await post_pending(factory, now=OCT_25)

                # ---- a credit pack sold outside the engine
                async with factory() as s:
                    pack = await addons.create_add_on(
                        s,
                        CreateAddOnRequest(
                            kind="credit_pack",
                            name="Rs 1,000 pack",
                            currency="INR",
                            price_minor=PACK_CREDIT,
                            credit_minor=PACK_CREDIT,
                        ),
                        request=None,
                    )
                    await s.commit()
                async with factory() as s:
                    await addons.apply_add_on(
                        s,
                        tenant_id,
                        ApplyAddOnRequest(
                            add_on_id=pack.id, payment="external", idempotency_key="pack-oct-0001"
                        ),
                        request=None,
                        now=OCT_25 + MINUTE,
                    )
                    await s.commit()
                out["october_closing"] = await balance()

                # ---- November 1: the next subscription and credit; October is frozen
                await run_due(factory, now=NOV_1)
                await statements.snapshot_closed_months(factory, now=NOV_1 + MINUTE)

                statement = await c.get("/api/v1/billing/statements/2026-10", headers=owner)
                out["statement_status"] = statement.status_code
                october = statement.json()
                out["statement"] = {
                    "opening": october["opening_minor"],
                    "closing": october["closing_minor"],
                    "lines": sum(x["amount_minor"] for x in october["lines"]),
                }
                pnl = await c.get(
                    "/api/v1/admin/billing/pnl?start=2026-10&end=2026-10&currency=INR",
                    headers=admin,
                )
                assert pnl.status_code == 200, pnl.text
                [row] = [r for r in pnl.json()["rows"] if r["tenant_id"] == tid]
                out["pnl"] = (row["billed_minor"], row["collected_minor"])

        async with factory() as s:
            rows = await s.execute(
                text("SELECT entry_type, amount_minor FROM wallet_ledger WHERE account_id = :a"),
                {"a": account_id},
            )
            ledger_rows = [(r[0], r[1]) for r in rows]
            out["ledger_sum"] = sum(r[1] for r in ledger_rows)
            out["expiry_entries"] = [r for r in ledger_rows if r[0] == "expiry"]
            out["charge_rows"] = (
                await s.execute(
                    text("SELECT count(*) FROM usage_charges WHERE account_id = :a"),
                    {"a": account_id},
                )
            ).scalar_one()
            out["retried_rows"] = (
                await s.execute(
                    text("SELECT count(*) FROM usage_charges WHERE request_id = :r"),
                    {"r": retried},
                )
            ).scalar_one()
        out["final_balance"] = await balance()
        # October 31 holds no usage on either side; the day usage was really metered (today,
        # `record_usage` stamps the wall clock) is checked as well
        for name, day in (
            ("reconciliation", OCT_31),
            ("reconciliation_today", datetime.now(UTC).date()),
        ):
            result = await reconcile.run(factory, day=day)
            out[name] = None if result is None else (result.ok, [f.check for f in result.findings])
    finally:
        await engine.dispose()
    return out


async def _one_paused(c: AsyncClient, corpus_id: str, headers: dict[str, str]) -> tuple[int, str]:
    r = await c.post(
        "/api/v1/query", json={"query": "still paused?", "corpus_id": corpus_id}, headers=headers
    )
    return r.status_code, r.json().get("code")


def test_a_month_of_billing_adds_up_to_the_paisa() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        out = asyncio.run(_flow(url))

    assert out["after_schedule"] == 500_000 - 99_900 + 20_000
    # 150 + 40 operator-key answers + the replayed one, once: 191 x Rs 2; her own key is free
    assert out["after_first_usage"] == out["after_schedule"] - FIRST_ANSWERS * BASE_PRICE
    assert out["retried_rows"] == 1  # a replayed request id is charged once

    # 2,000 asks: Rs 2 up to the 1,500 allowed, Rs 3 after that, until the balance is gone; every
    # ask after the last served one is a 402 and nothing was refused before it
    statuses = out["statuses"]
    assert statuses.count(200) == ANSWERED
    assert statuses[:ANSWERED] == [200] * ANSWERED
    assert set(statuses[ANSWERED:]) == {402}
    assert len(statuses) == ASKS
    assert out["paused_codes"] == {(402, "billing_paused")}
    assert out["paused_after_asks"] and out["paused_at_stored"]
    assert out["balance_paused"] == PAUSED_BALANCE <= 0
    # the owner heard at 20 % and at 5 % of the Rs 5,000 top-up, and both of them and the
    # operator heard it paused
    assert out["mails_when_paused"] == {
        "billing_paused": ["owner@acme.example", "sv@operator.example"],
        "billing_low_balance": ["owner@acme.example"] * 2,
    }

    # a visitor gets the pause message and the handoff card, never an error; a greeting is free
    assert out["widget_paused"]["text"].startswith(DEFAULT_PAUSED["en"][:20])
    assert out["widget_paused"]["offers"] == ["handoff_card"]
    assert out["widget_hello"]["route"] == "smalltalk" and out["widget_hello"]["text"]
    assert out["widget_charges"] == 0  # neither reply cost anything

    # the top-up: only the super admin, typed twice, once per key, one audit row per entry
    assert out["owner_top_up"] == 403
    assert out["unconfirmed"] == (422, "confirm_mismatch")
    assert out["paused_until_confirmed"] is True
    assert out["repeat"] == (200, True)
    assert out["top_up_rows"] == 1
    assert out["audit_rows"] == 2  # Rs 5,000 and Rs 12,000, not the replay
    assert not out["paused_after_top_up"] and out["paused_at_cleared"]
    assert out["after_top_up_answer"] == 200

    # + Rs 12,000, - the one answer after the pause lifted, + the Rs 1,000 pack
    assert out["october_closing"] == PAUSED_BALANCE + TOP_UP_2 - OVERAGE_PRICE + PACK_CREDIT
    # November: a new subscription and a new included credit; the old credit was spent first
    assert out["expiry_entries"] == []
    assert out["final_balance"] == out["october_closing"] - 99_900 + 20_000
    assert out["ledger_sum"] == out["final_balance"]
    assert out["charge_rows"] == ANSWERED + FIRST_ANSWERS + 1

    assert out["statement_status"] == 200
    assert out["statement"]["opening"] == 0
    assert out["statement"]["opening"] + out["statement"]["lines"] == out["statement"]["closing"]
    assert out["statement"]["closing"] == out["october_closing"]

    # billed: the subscription and the usage; collected: the cash that came in (pack at its price)
    assert out["pnl"] == (
        99_900 + USAGE_MINOR + OVERAGE_PRICE,
        TOP_UP_1 + TOP_UP_2 + PACK_CREDIT,
    )
    assert out["reconciliation"] == (True, [])
    assert out["reconciliation_today"] == (True, [])
