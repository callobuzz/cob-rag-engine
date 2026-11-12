"""The fair-use gate on real PostgreSQL and Redis (PIP3). Needs Docker (skipped with the
integration suite when it is down).

A business on a plan of 30 questions an hour, "Shree Dental Clinic, Pune":

1. its first 30 questions are answered on the plan's profile;
2. the 31st is answered slower, on the fallback profile (one mail to the owner and one to the
   deployer at 80 %, and at 100 %, one webhook `fair_use.limit_reached` at the limit);
3. the 46th (the hard line, 150 %) is paused with the pause message;
4. the next hour it answers on the plan's profile again and the business's state returns to ok,
   with `fair_use.throttled`, `fair_use.stopped` and `fair_use.resumed` in the audit log.

Also on the real database: the counters and alert rows are written by the real SQL (upserts, the
unique crossing index with `NULLS NOT DISTINCT`), and the sums over `usage_daily` the gate reads.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.fairuse import alerts, gate
from callobuzz_rag.fairuse.limits import FairUseLimits
from callobuzz_rag.fairuse.usage import read_usage
from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _redis_url

pytestmark = pytest.mark.integration


class Flow:
    def __init__(
        self, factory: Any, redis: Any, tenant_id: uuid.UUID, corpus_id: uuid.UUID
    ) -> None:
        self.factory, self.redis = factory, redis
        self.tenant_id, self.corpus_id = tenant_id, corpus_id
        self.mails: list[tuple[str, str, str]] = []
        self.webhooks: list[tuple[str, str, str]] = []
        settings = Settings(_env_file=None, superadmin_email="admin@callobuzz.example")  # type: ignore[call-arg]
        self.app = SimpleNamespace(
            state=SimpleNamespace(settings=settings, session_factory=factory, fair_use_redis=redis)
        )

    async def ask(self) -> gate.GateDecision:
        request = SimpleNamespace(state=SimpleNamespace(is_admin=False), app=self.app)
        async with self.factory() as session:
            corpus = await session.get(Corpus, self.corpus_id)
            decision = await gate.answer_gate(
                session,
                request,
                corpus,
                payer="operator",
                surface="api",  # type: ignore[arg-type]
            )
            await session.commit()
        tasks = list(getattr(self.app.state, "fair_use_tasks", ()))
        if tasks:
            await asyncio.gather(*tasks)  # the alerts the answer scheduled
        return decision

    async def audit(self) -> list[str]:
        async with self.factory() as s:
            rows = await s.execute(
                text(
                    "SELECT action FROM audit_log WHERE tenant_id = :t AND action LIKE 'fair_use.%' "
                    "ORDER BY created_at, id"
                ),
                {"t": self.tenant_id},
            )
            return [r[0] for r in rows]

    async def state(self) -> str:
        async with self.factory() as s:
            row = await s.execute(
                text("SELECT state FROM tenant_pipeline_settings WHERE tenant_id = :t"),
                {"t": self.tenant_id},
            )
            found = row.first()
            return found[0] if found else "ok"

    async def next_hour(self) -> None:
        for key in [k async for k in self.redis.scan_iter("fu:q:*")]:
            await self.redis.delete(key)


@pytest_asyncio.fixture
async def flow(migrated_postgres, redis_container, monkeypatch):  # type: ignore[no-untyped-def]
    engine = create_async_engine(_async_url(migrated_postgres), pool_size=10, max_overflow=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = aioredis.from_url(_redis_url(redis_container))  # type: ignore[no-untyped-call]
    await redis.flushdb()
    async with factory() as s:
        fallback_id = uuid.uuid4()
        await s.execute(
            text(
                "INSERT INTO pipeline_profiles (id, name, label, config, updated_by) "
                "VALUES (:i, :n, 'Economy', '{\"priority\": \"low\"}'::jsonb, 'test')"
            ),
            {"i": fallback_id, "n": f"fallback-{fallback_id.hex[:8]}"},
        )
        plan_id = uuid.uuid4()
        await s.execute(
            text(
                "INSERT INTO plans (id, name, fair_use_limits, fair_use_policy) VALUES "
                "(:i, :n, CAST(:limits AS jsonb), CAST(:policy AS jsonb))"
            ),
            {
                "i": plan_id,
                "n": f"hour-{plan_id.hex[:8]}",
                "limits": '{"questions_per_hour": 30}',
                "policy": json.dumps({"downgrade_profile_id": str(fallback_id)}),
            },
        )
        tenant = Tenant(name="Shree Dental Clinic, Pune", plan_id=plan_id)
        s.add(tenant)
        await s.flush()
        corpus = Corpus(name="KB", tenant_id=tenant.id)
        s.add(corpus)
        await s.flush()
        await s.commit()
    out = Flow(factory, redis, tenant.id, corpus.id)

    async def owners(session: Any, tenant_id: Any) -> list[str]:
        return ["owner@shree-dental.example"]

    monkeypatch.setattr("callobuzz_rag.accounts.lifecycle.owner_emails", owners)

    def enqueue(template: str, to: str, context: Any, **_: Any) -> None:
        out.mails.append((template, to, str(context["level_pct"])))

    monkeypatch.setattr("callobuzz_rag.accounts.email.dispatch.enqueue_email", enqueue)

    class Dispatcher:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def dispatch(self, event: Any, data: dict[str, Any]) -> None:
            out.webhooks.append((event.value, data["level"], data["action"]))

    monkeypatch.setattr("callobuzz_rag.core.webhooks.WebhookDispatcher", Dispatcher)
    yield out
    await redis.aclose()
    await engine.dispose()


async def test_thirty_an_hour_then_slower_then_paused_then_back(flow: Flow) -> None:
    first = [await flow.ask() for _ in range(30)]
    assert {d.action for d in first} == {"allow"}
    assert {d.profile.name for d in first} == {first[0].profile.name}  # the plan's own profile

    thirty_first = await flow.ask()  # used = 30 = the limit
    assert thirty_first.action == "downgrade" and thirty_first.profile.config.priority == "low"
    assert thirty_first.profile.source == "downgrade"
    assert await flow.state() == "throttled"
    owner_mails = [m for m in flow.mails if m[0] == "fair_use_alert"]
    admin_mails = [m for m in flow.mails if m[0] == "fair_use_alert_admin"]
    assert [m[2] for m in owner_mails] == ["80", "100"]  # exactly one at each level
    assert [m[2] for m in admin_mails] == ["80", "100"]
    assert flow.webhooks == [("fair_use.limit_reached", "soft", "downgrade")]

    for _ in range(14):  # 32nd .. 45th: still answered, slower
        assert (await flow.ask()).action == "downgrade"
    forty_sixth = await flow.ask()  # used = 45 = the hard line (150 %)
    assert (forty_sixth.action, forty_sixth.paused_by) == ("pause", "fair_use")
    assert await flow.state() == "stopped"
    assert flow.webhooks[-1] == ("fair_use.limit_reached", "hard", "pause")
    assert [m[2] for m in flow.mails if m[0] == "fair_use_alert"] == ["80", "100", "150"]

    await flow.next_hour()  # the hour is over: the counters start again
    back = await flow.ask()
    assert back.action == "allow" and back.profile.name == first[0].profile.name
    assert await flow.state() == "ok"
    assert await flow.audit() == ["fair_use.throttled", "fair_use.stopped", "fair_use.resumed"]


async def test_the_decisions_and_the_crossings_are_written_once(flow: Flow) -> None:
    for _ in range(33):
        await flow.ask()
    async with flow.factory() as s:
        counters = dict(
            (
                await s.execute(
                    text(
                        "SELECT metric, value FROM fair_use_counters WHERE tenant_id = :t "
                        "AND metric LIKE 'decisions_%'"
                    ),
                    {"t": flow.tenant_id},
                )
            ).all()
        )
        crossings = (
            await s.execute(
                text(
                    "SELECT metric, level_pct FROM fair_use_alerts WHERE tenant_id = :t "
                    "ORDER BY level_pct"
                ),
                {"t": flow.tenant_id},
            )
        ).all()
    assert counters == {"decisions_downgraded": 3}  # the 31st, 32nd and 33rd
    assert [tuple(r) for r in crossings] == [
        ("questions_per_hour", 80),
        ("questions_per_hour", 100),
    ]


async def test_racing_processes_claim_a_crossing_once(flow: Flow) -> None:
    for _ in range(23):
        await flow.ask()
    # five requests race over the 80 % line at the same moment: one mail each way
    await asyncio.gather(*(flow.ask() for _ in range(5)))
    assert [m[2] for m in flow.mails if m[0] == "fair_use_alert"].count("80") == 1


async def test_the_engine_rail_row_with_no_business_is_unique_too(flow: Flow) -> None:
    async with flow.factory() as s:
        from callobuzz_rag.fairuse.decision import period_of  # noqa: F401  (the rail's period)

        stmt = text(
            "INSERT INTO fair_use_alerts (tenant_id, metric, period_key, level_pct) "
            "VALUES (NULL, 'engine_ai_spend_per_day', '2026-10-05', 80) ON CONFLICT DO NOTHING "
            "RETURNING id"
        )
        first = (await s.execute(stmt)).first()
        second = (await s.execute(stmt)).first()
        await s.commit()
    assert first is not None and second is None  # NULLS NOT DISTINCT: one row, not two


async def test_spend_and_day_questions_are_the_sums_the_gate_reads(flow: Flow) -> None:
    today = datetime.now(UTC).date()
    async with flow.factory() as s:
        for source, requests, payer, cost in (
            ("widget:k1", 3, "operator", 500),
            ("cob", 2, "operator", 100),
            ("api", 1, "byok", 9_999),
        ):
            await s.execute(
                text(
                    "INSERT INTO usage_daily (day, corpus_id, tenant_id, source, requests, payer, "
                    "model, cost_micro) VALUES (:d, :c, :t, :s, :r, :p, :m, :k)"
                ),
                {
                    "d": today,
                    "c": flow.corpus_id,
                    "t": flow.tenant_id,
                    "s": source,
                    "r": requests,
                    "p": payer,
                    "m": f"m-{source}",
                    "k": cost,
                },
            )
        await s.commit()
    async with flow.factory() as s:
        usage = await read_usage(
            s,
            flow.redis,
            flow.tenant_id,
            datetime.now(UTC),
            metrics={
                "questions_per_day",
                "cob_questions_per_day",
                "ai_spend_per_day",
                "ai_spend_per_month",
            },
        )
    assert usage.questions_per_day == 4 and usage.cob_questions_per_day == 2
    assert usage.spend_day_usd_micro == 600 and usage.spend_month_usd_micro >= 600  # operator only
    assert usage.spend_unknown is False


async def test_the_limits_come_from_the_plan_through_the_real_join(flow: Flow) -> None:
    from callobuzz_rag.fairuse.limits import effective_fair_use

    async with flow.factory() as s:
        effective = await effective_fair_use(s, flow.tenant_id)
        assert effective.limits == FairUseLimits(questions_per_hour=30)
        await s.execute(
            text(
                "INSERT INTO tenant_pipeline_settings (tenant_id, fair_use_overrides) "
                "VALUES (:t, CAST('{\"questions_per_hour\": null}' AS jsonb))"
            ),
            {"t": flow.tenant_id},
        )
        await s.commit()
    async with flow.factory() as s:
        assert (await effective_fair_use(s, flow.tenant_id)).limits.questions_per_hour is None
    assert alerts.period_key("questions_per_hour", datetime(2026, 10, 5, 14, tzinfo=UTC)).endswith(
        "T14"
    )
