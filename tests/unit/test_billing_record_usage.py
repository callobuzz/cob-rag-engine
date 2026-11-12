"""`record_usage` and the charge inside it: order, savepoint, duplicates, failures, BYOK count,
the admin waiver and its ContextVar through a streamed response."""

import logging
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from callobuzz_rag.analytics.usage import record_usage, record_usage_new_session
from callobuzz_rag.billing import charging
from callobuzz_rag.billing.context import ADMIN_ANSWER, mark_admin_answer
from callobuzz_rag.billing.pricing import PricingFields, PricingOverrides, combine
from callobuzz_rag.llm.cost import RequestCost

TENANT = uuid.uuid4()
CORPUS = uuid.uuid4()


class FakeResult:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class FakeSession:
    """Records statements and savepoints in order; `charge_row` is what the charge insert's
    RETURNING gives back (None = the request id was already there)."""

    def __init__(self, charge_row: Any = ("rid",)) -> None:
        self.log: list[str] = []
        self.inserts: dict[str, dict[str, Any]] = {}
        self.charge_row = charge_row
        self.commits = 0

    @asynccontextmanager
    async def begin_nested(self):  # type: ignore[no-untyped-def]
        self.log.append("savepoint")
        try:
            yield
        finally:
            self.log.append("release")

    async def execute(self, stmt: Any) -> FakeResult:
        table = stmt.table.name
        self.log.append(table)
        self.inserts[table] = stmt.compile(dialect=postgresql.dialect()).params
        return FakeResult(self.charge_row if table == "usage_charges" else None)

    async def commit(self) -> None:
        self.commits += 1


def _eff(**fields: Any):  # type: ignore[no-untyped-def]
    return combine(PricingFields(**fields), PricingOverrides(), currency="INR", exempt=False)


def _billed(monkeypatch: pytest.MonkeyPatch, effective: Any, *, quota: Any = None) -> None:
    async def fake_effective(session: Any, tenant_id: Any) -> Any:
        return effective

    async def fake_account(session: Any, tenant_id: Any) -> Any:
        return SimpleNamespace(id=uuid.uuid4(), currency="INR", ledger_seq=1)

    async def fake_quota(session: Any, tenant_id: Any, month: Any, **kw: Any) -> Any:
        return quota

    monkeypatch.setattr(charging, "effective_pricing", fake_effective)
    monkeypatch.setattr(charging, "ensure_account", fake_account)
    monkeypatch.setattr("callobuzz_rag.accounts.caps.question_quota", fake_quota)
    monkeypatch.setattr(charging, "billing_enabled", lambda: True)
    monkeypatch.setattr(charging, "usd_inr_rate", lambda: 84.0)


async def _record(session: Any, **kw: Any) -> None:
    await record_usage(
        session,
        corpus_id=CORPUS,
        tenant_id=TENANT,
        source="api",
        prompt_tokens=10,
        completion_tokens=5,
        model="openai/gpt-4o-mini",
        **kw,
    )


async def test_unbilled_tenant_writes_only_todays_statements(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _billed(monkeypatch, _eff())  # no subscription, included usage, overage block
    session = FakeSession()
    await _record(session)
    assert session.log == ["savepoint", "release", "usage_daily", "tenant_question_counts"]
    assert "usage_charges" not in session.inserts


async def test_billed_tenant_inserts_the_charge_before_the_upsert_in_a_savepoint(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    _billed(monkeypatch, _eff(usage_mode="per_question", per_question_minor=200))
    session = FakeSession()
    await _record(session)
    assert session.log == [
        "savepoint",
        "usage_charges",
        "release",
        "usage_daily",
        "tenant_question_counts",
    ]
    row = session.inserts["usage_charges"]
    assert row["amount_micro"] == 2_000_000 and row["currency"] == "INR"
    assert row["questions"] == 1 and row["waived"] is None and row["fx_ppm"] == 84_000_000


async def test_a_duplicate_request_skips_the_usage_upsert_and_the_count(
    monkeypatch, caplog
) -> None:  # type: ignore[no-untyped-def]
    _billed(monkeypatch, _eff(usage_mode="per_question", per_question_minor=200))
    session = FakeSession(charge_row=None)
    with caplog.at_level(logging.DEBUG):
        await _record(session, request_id=uuid.uuid4())
    assert session.log == ["savepoint", "usage_charges", "release"]
    assert session.commits == 1


async def test_a_failing_charge_is_logged_and_metering_goes_on(monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    _billed(monkeypatch, _eff(usage_mode="per_question", per_question_minor=200))

    async def boom(session: Any, tenant_id: Any) -> Any:
        raise RuntimeError("database says no")

    monkeypatch.setattr(charging, "effective_pricing", boom)
    session = FakeSession()
    with caplog.at_level(logging.WARNING):
        await _record(session)
    assert "billing charge failed" in caplog.text and "RuntimeError" in caplog.text
    assert "database says no" not in caplog.text  # the class only, never a message
    assert session.log[-2:] == ["usage_daily", "tenant_question_counts"]


async def test_byok_questions_not_counted_toward_caps_leave_the_count_alone(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _billed(monkeypatch, _eff(byok_mode="free", byok_counts_toward_caps=False))
    session = FakeSession()
    await _record(session, payer="byok")
    assert session.log == ["savepoint", "release", "usage_daily"]  # no monthly count


async def test_byok_counts_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _billed(monkeypatch, _eff(byok_mode="free"))
    session = FakeSession()
    await _record(session, payer="byok")
    assert session.log[-1] == "tenant_question_counts"


async def test_without_a_request_id_every_call_is_its_own_request(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _billed(monkeypatch, _eff(usage_mode="per_question", per_question_minor=1))
    ids = set()
    for _ in range(2):
        session = FakeSession()
        await _record(session)
        ids.add(session.inserts["usage_charges"]["request_id"])
    assert len(ids) == 2


async def test_an_included_question_of_a_chargeable_account_still_writes_a_zero_row(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    _billed(monkeypatch, _eff(subscription_fee_minor=49_900))  # a subscription, usage included
    session = FakeSession()
    await _record(session)
    assert session.inserts["usage_charges"]["amount_micro"] == 0
    assert session.log.index("usage_charges") < session.log.index("usage_daily")


async def test_an_overage_question_is_priced_from_the_count_inside_the_transaction(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    _billed(
        monkeypatch,
        _eff(usage_mode="included", overage="charge", overage_per_question_minor=100),
        quota=(1000, 1000),
    )
    session = FakeSession()
    await _record(session)
    row = session.inserts["usage_charges"]
    assert row["amount_micro"] == 1_000_000 and row["overage"] is True


async def test_the_admin_principal_is_metered_but_waived(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _billed(monkeypatch, _eff(usage_mode="per_question", per_question_minor=200))
    session = FakeSession()
    token = ADMIN_ANSWER.set(True)
    try:
        await _record(session)
    finally:
        ADMIN_ANSWER.reset(token)
    row = session.inserts["usage_charges"]
    assert row["waived"] == "admin" and row["amount_micro"] == 0
    assert session.log[-2:] == ["usage_daily", "tenant_question_counts"]


async def test_cost_plus_charges_the_recorded_cost_at_the_snapshotted_rate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _billed(monkeypatch, _eff(usage_mode="cost_plus", markup_bp=2500))
    session = FakeSession()
    cost = RequestCost(
        prompt_tokens=1, completion_tokens=1, cost_micro=1_000, cost_known=True, calls=1
    )
    await _record(session, cost=cost)
    row = session.inserts["usage_charges"]
    assert row["amount_micro"] == 105_000 and row["cost_usd_micro"] == 1_000
    assert row["fx_ppm"] == 84_000_000 and row["cost_known"] is True


async def test_billing_off_changes_nothing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _billed(monkeypatch, _eff(usage_mode="per_question", per_question_minor=200))
    monkeypatch.setattr(charging, "billing_enabled", lambda: False)
    session = FakeSession()
    await _record(session)
    assert session.log == ["usage_daily", "tenant_question_counts"]


# ---- the admin flag through a real streamed response


def _app(seen: list[bool]) -> FastAPI:
    app = FastAPI()

    @asynccontextmanager
    async def factory():  # type: ignore[no-untyped-def]
        yield FakeSession()

    async def fake_charge(session: Any, event: Any) -> Any:
        seen.append(ADMIN_ANSWER.get())
        return charging.ChargeResult("unbilled", 0, False)

    @app.get("/answer")
    async def answer(request: Request) -> StreamingResponse:
        mark_admin_answer(request)

        async def gen():  # type: ignore[no-untyped-def]
            yield b"tok"
            with patch("callobuzz_rag.billing.charging.charge_usage", fake_charge):
                await record_usage_new_session(
                    factory,
                    corpus_id=CORPUS,
                    tenant_id=TENANT,
                    source="stream",
                    prompt_tokens=1,
                    completion_tokens=1,
                    model="m",
                )

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.middleware("http")
    async def principal(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.is_admin = request.headers.get("x-admin") == "1"
        return await call_next(request)

    return app


async def test_the_admin_flag_survives_a_stream_and_never_leaks_to_the_next_request() -> None:
    seen: list[bool] = []
    async with AsyncClient(transport=ASGITransport(app=_app(seen)), base_url="http://t") as c:
        await c.get("/answer", headers={"x-admin": "1"})
        await c.get("/answer")
    assert seen == [True, False]


async def test_the_charge_prices_by_the_count_it_locked(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[dict[str, Any]] = []

    async def fake_quota(session: Any, tenant_id: Any, month: Any, **kw: Any) -> Any:
        calls.append(kw)
        return (1000, 999)

    _billed(monkeypatch, _eff(usage_mode="per_question", per_question_minor=200))
    monkeypatch.setattr("callobuzz_rag.accounts.caps.question_quota", fake_quota)
    await _record(FakeSession())
    assert calls == [{"lock": True}]  # two answers at the edge cannot read the same count


# PIP3 waiver
async def test_an_answer_downgraded_for_the_balance_is_metered_but_not_charged(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from callobuzz_rag.billing.context import WAIVED_REASON

    _billed(monkeypatch, _eff(usage_mode="per_question", per_question_minor=200))
    session = FakeSession()
    token = WAIVED_REASON.set("downgraded")
    try:
        await _record(session)
    finally:
        WAIVED_REASON.reset(token)
    row = session.inserts["usage_charges"]
    assert row["waived"] == "downgraded" and row["amount_micro"] == 0
    assert session.log[-2:] == ["usage_daily", "tenant_question_counts"]  # still counted


async def test_the_operators_own_test_stays_admin_waived_even_when_downgraded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from callobuzz_rag.billing.context import WAIVED_REASON

    _billed(monkeypatch, _eff(usage_mode="per_question", per_question_minor=200))
    session = FakeSession()
    admin, down = ADMIN_ANSWER.set(True), WAIVED_REASON.set("downgraded")
    try:
        await _record(session)
    finally:
        WAIVED_REASON.reset(down)
        ADMIN_ANSWER.reset(admin)
    assert session.inserts["usage_charges"]["waived"] == "admin"


# /PIP3 waiver
