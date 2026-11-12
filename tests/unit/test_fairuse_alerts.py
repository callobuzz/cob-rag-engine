"""Alerts, the stored state and the decision counters (PIP3 task 6).

The database steps (claim a crossing, move the state, bump a counter, write the audit row) are
replaced by in-memory versions with the same contracts, so the tests show the logic: one
notice per level per period however many processes race, the state moving once per transition,
nothing about AI spend in a customer's mail. The real SQL runs in
`tests/integration/test_fair_use_flow.py`."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts.email.render import render_email
from callobuzz_rag.accounts.settings_store import Branding
from callobuzz_rag.billing.enforcement import NOT_BILLED
from callobuzz_rag.core.events import WebhookEvent
from callobuzz_rag.fairuse import alerts
from callobuzz_rag.fairuse.decision import decide, evaluate
from callobuzz_rag.fairuse.limits import Breach, EffectiveFairUse, FairUseLimits, FairUsePolicy
from callobuzz_rag.fairuse.usage import FairUseUsage
from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig

TENANT = uuid.uuid4()
START = datetime(2026, 10, 5, 14, 20, tzinfo=UTC)


def prof(name: str, route: str = "engine") -> EffectiveProfile:
    return EffectiveProfile(
        uuid.uuid4(),
        name,
        name,
        ProfileConfig(answer_route=route, overrides_kb_settings=route == "local"),  # type: ignore[arg-type]
        "plan",
        True,
    )


STD, FREE = prof("Standard"), prof("Free", "local")


class Clock:
    def __init__(self) -> None:
        self.now = START

    def next_hour(self) -> None:
        self.now = self.now.replace(minute=5) + timedelta(hours=1)


class AlertEnv:
    def __init__(self) -> None:
        self.clock = Clock()
        self.claimed: set[tuple[Any, ...]] = set()
        self.state = "ok"
        self.mails: list[str] = []
        self.mail_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.webhooks: list[tuple[str, str, str]] = []
        self.webhook_data: list[dict[str, Any]] = []
        self.audit: list[str] = []
        self.audit_details: list[dict[str, Any]] = []
        self.bumps: list[str] = []
        self.engine_alert_pct = 80
        self.factory = lambda: _Ctx()

    # -- the fakes -------------------------------------------------------------------------

    async def claim(self, session: Any, tenant_id: Any, crossing: Any, key: str) -> bool:
        ident = (tenant_id, crossing.metric, key, crossing.level_pct)
        if ident in self.claimed:
            return False
        self.claimed.add(ident)
        return True

    async def move_state(self, session: Any, tenant_id: Any, old: str, new: str, now: Any) -> bool:
        if self.state != old:
            return False
        self.state = new
        return True

    async def bump(
        self, session: Any, tenant_id: Any, metric: str, n: int = 1, day: Any = None
    ) -> None:
        self.bumps.append(metric)

    async def record_audit(self, session: Any, request: Any, action: str, **kw: Any) -> None:
        self.audit.append(action)
        self.audit_details.append(kw.get("details") or {})

    def enqueue(self, template: str, to: str, ctx: dict[str, Any], *, expires_s: int) -> None:
        who = (
            "owner"
            if template == "fair_use_alert"
            else "admin"
            if template == "fair_use_alert_admin"
            else "engine"
        )
        self.mails.append(f"{who}:{ctx['level_pct']}")
        self.mail_calls.append((template, to, dict(ctx)))

    async def dispatch(self, event: WebhookEvent, data: dict[str, Any]) -> None:
        assert event == WebhookEvent.FAIR_USE_LIMIT_REACHED
        self.webhooks.append((data["metric"], data["level"], data["action"]))
        self.webhook_data.append(data)

    # -- driving ---------------------------------------------------------------------------

    def settings(self) -> Any:
        return SimpleNamespace(
            superadmin_email="admin@callobuzz.example", dashboard_url="https://dash.example"
        )

    async def decide_and_alert(
        self,
        metric: str,
        *,
        used: int,
        limit: int,
        policy: FairUsePolicy | None = None,
        engine: Breach | None = None,
        target: EffectiveProfile = FREE,
        **kw: Any,
    ) -> Any:
        policy = policy or FairUsePolicy()
        field = {"ai_spend_per_day": "ai_spend_per_day_usd_micro"}.get(metric, metric)
        limits = FairUseLimits(**{field: limit})
        usage_field = {"ai_spend_per_day": "spend_day_usd_micro"}.get(metric, metric)
        usage = FairUseUsage(**{usage_field: used})
        breaches = evaluate(limits, usage, policy, [metric], now=self.clock.now)
        if engine is not None:
            breaches.append(engine)
        decision = decide(
            cap_blocked=False,
            billing=NOT_BILLED,
            breaches=breaches,
            policy=policy,
            plan_profile=STD,
            downgrade_to=target,
            exempt=False,
            now=self.clock.now,
        )
        effective = EffectiveFairUse(
            limits,
            policy,
            False,
            frozenset(),
            state=self.state,  # type: ignore[arg-type]
            tenant_name="Shree Dental Clinic, Pune",
        )
        return await alerts.after_decision(
            self.factory,
            TENANT,
            decision,
            effective,
            now=self.clock.now,
            settings=self.settings(),
            enqueue=self.enqueue,
            dispatch=self.dispatch,
            **kw,
        )

    async def usage_alert(self, metric: str, *, used: int, limit: int, **limit_kw: int) -> Any:
        """`after_usage` for a limit that is not about answers, with `used` of `limit`."""
        field = {"storage_mb": "storage_mb"}.get(metric, metric)
        limits = FairUseLimits(**{field: limit}, **limit_kw)
        usage = FairUseUsage(
            **{("storage_bytes" if metric == "storage_mb" else metric): used}  # type: ignore[arg-type]
        )
        effective = EffectiveFairUse(
            limits, FairUsePolicy(), False, frozenset(), tenant_name="Shree Dental Clinic, Pune"
        )
        with (
            patch(
                "callobuzz_rag.fairuse.limits.effective_fair_use",
                AsyncMock(return_value=effective),
            ),
            patch(
                "callobuzz_rag.fairuse.usage.read_usage",
                AsyncMock(return_value=usage),
            ),
        ):
            return await alerts.after_usage(
                self.factory,
                TENANT,
                [metric],
                now=self.clock.now,
                settings=self.settings(),
                enqueue=self.enqueue,
                dispatch=self.dispatch,
            )


class _Ctx:
    async def __aenter__(self) -> Any:
        return SimpleNamespace(commit=AsyncMock())

    async def __aexit__(self, *exc: Any) -> None:
        return None


@pytest.fixture
def alert_env(monkeypatch: pytest.MonkeyPatch) -> AlertEnv:
    env = AlertEnv()
    monkeypatch.setattr(alerts, "_claim", env.claim)
    monkeypatch.setattr(alerts, "_move_state", env.move_state)
    monkeypatch.setattr(alerts.counters, "bump", env.bump)
    monkeypatch.setattr(alerts, "record_audit", env.record_audit)
    monkeypatch.setattr(
        "callobuzz_rag.accounts.lifecycle.owner_emails",
        AsyncMock(return_value=["owner@shree.example"]),
    )
    monkeypatch.setattr(
        "callobuzz_rag.accounts.settings_store.get_engine_settings",
        AsyncMock(
            side_effect=lambda f: SimpleNamespace(
                pipelines=SimpleNamespace(engine_spend_alert_pct=env.engine_alert_pct)
            )
        ),
    )
    return env


async def test_one_alert_per_level_per_period(alert_env: AlertEnv) -> None:
    for used in (20, 24, 25, 29, 30, 31, 44, 45, 46):  # limit 30 an hour, hard line 45
        await alert_env.decide_and_alert("questions_per_hour", used=used, limit=30)
    assert alert_env.mails == [
        "owner:80",
        "admin:80",
        "owner:100",
        "admin:100",
        "owner:150",
        "admin:150",
    ]
    assert alert_env.webhooks == [
        ("questions_per_hour", "soft", "downgrade"),
        ("questions_per_hour", "hard", "pause"),
    ]
    assert alert_env.audit == ["fair_use.throttled", "fair_use.stopped"]
    assert alert_env.state == "stopped"
    alert_env.clock.next_hour()
    await alert_env.decide_and_alert("questions_per_hour", used=1, limit=30)
    assert alert_env.audit[-1] == "fair_use.resumed" and alert_env.state == "ok"


async def test_the_next_period_alerts_again(alert_env: AlertEnv) -> None:
    await alert_env.decide_and_alert("questions_per_hour", used=30, limit=30)
    first = len(alert_env.mails)
    alert_env.clock.next_hour()
    await alert_env.decide_and_alert("questions_per_hour", used=30, limit=30)
    assert len(alert_env.mails) == 2 * first  # a new hour is a new crossing


async def test_two_processes_send_one_mail(alert_env: AlertEnv) -> None:
    await asyncio.gather(
        *(alert_env.decide_and_alert("questions_per_day", used=300, limit=300) for _ in range(5))
    )
    assert alert_env.mails.count("owner:100") == 1 and alert_env.mails.count("admin:100") == 1
    assert len(alert_env.webhooks) == 1


async def test_the_state_moves_once_per_transition_and_only_the_big_ones_are_audited(
    alert_env: AlertEnv,
) -> None:
    await alert_env.decide_and_alert("questions_per_hour", used=25, limit=30)
    assert (alert_env.state, alert_env.audit) == ("near", [])  # a heads-up is not audited
    await alert_env.decide_and_alert("questions_per_hour", used=2, limit=30)
    assert (alert_env.state, alert_env.audit) == ("ok", [])  # back to ok from near: quiet too
    await asyncio.gather(
        *(alert_env.decide_and_alert("questions_per_hour", used=31, limit=30) for _ in range(4))
    )
    assert alert_env.audit == ["fair_use.throttled"]  # four racing processes, one audit row


async def test_the_audit_row_names_the_metric_and_the_profile(alert_env: AlertEnv) -> None:
    await alert_env.decide_and_alert("questions_per_hour", used=31, limit=30)
    details = alert_env.audit_details[0]
    assert (
        details["metric"] == "questions_per_hour"
        and details["limit"] == 30
        and details["used"] == 31
    )
    assert details["profile"] == "Free" and details["period_key"] == "2026-10-05T14"


async def test_the_minute_window_never_mails_or_audits(alert_env: AlertEnv) -> None:
    await alert_env.decide_and_alert("questions_per_minute", used=500, limit=5)
    assert alert_env.mails == [] and alert_env.webhooks == []
    assert alert_env.audit == ["fair_use.stopped"]  # the state still follows what the engine did


async def test_decision_metrics_are_counted_off_the_answer_path(alert_env: AlertEnv) -> None:
    for used in (10, 31, 32, 46):
        await alert_env.decide_and_alert("questions_per_hour", used=used, limit=30)
    assert alert_env.bumps == ["decisions_downgraded", "decisions_downgraded", "decisions_paused"]


async def test_owner_mail_never_shows_spend_amounts(alert_env: AlertEnv) -> None:
    await alert_env.decide_and_alert("ai_spend_per_day", used=4_000_000, limit=5_000_000)
    owner = next(c for c in alert_env.mail_calls if c[0] == "fair_use_alert")
    ctx = owner[2]
    assert (ctx["used"], ctx["limit"], ctx["metric_label"]) == ("80%", "100%", "AI usage today")
    email = render_email("fair_use_alert", "o@x.in", ctx, Branding(), "https://dash.example")
    assert "$" not in email.text and "₹" not in email.text and "$" not in email.html
    admin = next(c for c in alert_env.mail_calls if c[0] == "fair_use_alert_admin")
    assert admin[2]["used"] == "$4.00" and admin[2]["limit"] == "$5.00"  # the deployer sees money


async def test_spend_at_its_ceiling_downgrades_and_says_so_in_one_sentence(
    alert_env: AlertEnv,
) -> None:
    await alert_env.decide_and_alert("ai_spend_per_day", used=5_000_000, limit=5_000_000)
    assert ("ai_spend_per_day", "hard", "downgrade") in alert_env.webhooks
    owner = next(
        c for c in alert_env.mail_calls if c[0] == "fair_use_alert" and c[2]["level_pct"] == 100
    )
    assert "more slowly" in owner[2]["what_happens"]


async def test_the_webhook_carries_the_period_and_the_business(alert_env: AlertEnv) -> None:
    await alert_env.decide_and_alert("questions_per_hour", used=30, limit=30)
    data = alert_env.webhook_data[0]
    assert data == {
        "tenant_id": str(TENANT),
        "tenant_name": "Shree Dental Clinic, Pune",
        "metric": "questions_per_hour",
        "level": "soft",
        "limit": 30,
        "used": 30,
        "period_key": "2026-10-05T14",
        "action": "downgrade",
    }


async def test_alert_levels_come_from_the_policy(alert_env: AlertEnv) -> None:
    policy = FairUsePolicy(alert_thresholds_pct=[50, 90, 100])
    await alert_env.decide_and_alert("questions_per_day", used=95, limit=100, policy=policy)
    owners = [m for m in alert_env.mails if m.startswith("owner")]
    assert owners == ["owner:50", "owner:90"]  # 95 % has reached 50 and 90, not 100


async def test_the_mail_goes_to_every_owner_and_the_deployer(
    alert_env: AlertEnv, monkeypatch
) -> None:
    monkeypatch.setattr(
        "callobuzz_rag.accounts.lifecycle.owner_emails",
        AsyncMock(return_value=["a@shree.example", "b@shree.example"]),
    )
    await alert_env.decide_and_alert("questions_per_day", used=240, limit=300)
    recipients = sorted(to for _, to, _ in alert_env.mail_calls)
    assert recipients == ["a@shree.example", "admin@callobuzz.example", "b@shree.example"]


# ------------------------------------------------------------------------ the engine rail


async def test_engine_spend_rail_alerts_the_deployer_downgrades_everyone(
    alert_env: AlertEnv,
) -> None:
    limit = 5_000_000
    near = Breach("engine_ai_spend_per_day", "near", limit, 4_100_000, None)
    hard = Breach("engine_ai_spend_per_day", "hard", limit, 5_000_000, None)
    await alert_env.decide_and_alert("questions_per_day", used=1, limit=300, engine=near)
    assert alert_env.mails == ["engine:80"]
    assert alert_env.mail_calls[0][1] == "admin@callobuzz.example"
    assert alert_env.audit == []  # a heads-up
    await alert_env.decide_and_alert("questions_per_day", used=1, limit=300, engine=hard)
    assert alert_env.mails == ["engine:80", "engine:100"]
    assert "fair_use.engine_spend_reached" in alert_env.audit
    tenantless = [d for d in alert_env.webhook_data if d["tenant_id"] is None]
    assert tenantless and tenantless[0]["metric"] == "engine_ai_spend_per_day"
    assert tenantless[0]["action"] == "downgrade" and tenantless[0]["tenant_name"] is None
    # the business's own state is not touched by the deployer's rail
    assert alert_env.state == "ok"
    # and the same crossing is not announced twice in a day
    await alert_env.decide_and_alert("questions_per_day", used=1, limit=300, engine=hard)
    assert alert_env.mails == ["engine:80", "engine:100"]


async def test_the_engine_alert_level_is_the_deployers(alert_env: AlertEnv) -> None:
    alert_env.engine_alert_pct = 50
    near = Breach("engine_ai_spend_per_day", "near", 10_000_000, 6_000_000, None)
    await alert_env.decide_and_alert("questions_per_day", used=1, limit=300, engine=near)
    assert alert_env.mails == ["engine:50"]


# --------------------------------------------------------------------------- scheduling


async def test_alert_task_failure_never_touches_the_answer(caplog) -> None:
    app = SimpleNamespace(
        state=SimpleNamespace(
            session_factory=object(),
            settings=SimpleNamespace(fair_use_alert_task_timeout_seconds=1),
        )
    )
    decision = decide(
        cap_blocked=False,
        billing=NOT_BILLED,
        breaches=[],
        policy=FairUsePolicy(),
        plan_profile=STD,
        downgrade_to=FREE,
        exempt=False,
    )
    eff = EffectiveFairUse(FairUseLimits(), FairUsePolicy(), False, frozenset())
    boom = AsyncMock(side_effect=ConnectionError("database is down"))
    with patch.object(alerts, "after_decision", boom):
        alerts.schedule(app, TENANT, decision, eff)  # returns at once, never raises
        await asyncio.gather(*app.state.fair_use_tasks)
    assert "Fair-use alert task failed" in caplog.text
    assert app.state.fair_use_tasks == set() or all(t.done() for t in app.state.fair_use_tasks)


async def test_the_alert_task_has_a_time_budget(caplog) -> None:
    app = SimpleNamespace(
        state=SimpleNamespace(
            session_factory=object(),
            settings=SimpleNamespace(fair_use_alert_task_timeout_seconds=0),
        )
    )

    async def slow(*a: Any, **k: Any) -> None:
        await asyncio.sleep(5)

    decision = decide(
        cap_blocked=False,
        billing=NOT_BILLED,
        breaches=[],
        policy=FairUsePolicy(),
        plan_profile=STD,
        downgrade_to=FREE,
        exempt=False,
    )
    eff = EffectiveFairUse(FairUseLimits(), FairUsePolicy(), False, frozenset())
    app.state.settings.fair_use_alert_task_timeout_seconds = 0.05
    with patch.object(alerts, "after_decision", slow):
        alerts.schedule(app, TENANT, decision, eff)
        await asyncio.wait_for(asyncio.gather(*app.state.fair_use_tasks), 2)
    assert "Fair-use alert task failed" in caplog.text


def test_without_a_session_factory_nothing_is_scheduled() -> None:
    app = SimpleNamespace(state=SimpleNamespace())
    decision = decide(
        cap_blocked=False,
        billing=NOT_BILLED,
        breaches=[],
        policy=FairUsePolicy(),
        plan_profile=STD,
        downgrade_to=FREE,
        exempt=False,
    )
    eff = EffectiveFairUse(FairUseLimits(), FairUsePolicy(), False, frozenset())
    alerts.schedule(app, TENANT, decision, eff)  # a no-op: no task, no error


def test_each_period_has_its_own_key() -> None:
    now = datetime(2026, 10, 5, 14, 20, tzinfo=UTC)
    assert alerts.period_key("questions_per_hour", now) == "2026-10-05T14"
    assert alerts.period_key("questions_per_day", now) == "2026-10-05"
    assert alerts.period_key("ai_spend_per_month", now) == "2026-10"
    assert alerts.period_key("storage_mb", now) == "total"
    assert alerts.period_key("website_pages_total", now) == "total"


def test_crossings_name_the_levels_a_breach_has_reached() -> None:
    policy = FairUsePolicy()
    b = Breach("questions_per_hour", "hard", 30, 46, None)
    levels = [(c.level_pct, c.kind) for c in alerts.crossings(b, policy)]
    assert levels == [(80, "near"), (100, "soft"), (150, "hard")]
    assert (
        alerts.crossings(Breach("questions_per_hour", "hard", 30, 0, None, unknown=True), policy)
        == []
    )
    assert alerts.crossings(Breach("questions_per_minute", "hard", 5, 9, None), policy) == []


async def test_a_busy_reply_is_counted_in_the_background_and_never_fails_the_reply(
    alert_env: AlertEnv, caplog
) -> None:
    alerts.schedule_busy(alert_env.factory, TENANT)
    await asyncio.gather(*alerts._busy_tasks)
    assert alert_env.bumps == ["decisions_busy"]
    alerts.schedule_busy(None, TENANT)  # no database to write to: nothing happens

    def broken() -> Any:
        raise ConnectionError("database is down")

    alerts.schedule_busy(broken, TENANT)
    await asyncio.gather(*alerts._busy_tasks)
    assert "Could not count a busy reply" in caplog.text


# ------------------------------------------------------- limits that are not about answers (FU-6)


async def test_uploads_alert_the_owner_and_the_deployer_at_80_and_at_the_limit(
    alert_env: AlertEnv,
) -> None:
    """FU-6: only the answer gate scheduled alerts, so a business that hit its uploads, storage,
    website reads or COB questions was told nothing and the customers list showed it as fine."""
    await alert_env.usage_alert("uploads_per_day", used=7, limit=10)
    assert alert_env.mails == []
    await alert_env.usage_alert("uploads_per_day", used=8, limit=10)
    assert alert_env.mails == ["owner:80", "admin:80"] and alert_env.webhooks == []
    await alert_env.usage_alert("uploads_per_day", used=10, limit=10)
    assert alert_env.mails[2:] == ["owner:100", "admin:100"]
    assert alert_env.webhooks == [("uploads_per_day", "soft", "alert")]
    await alert_env.usage_alert("uploads_per_day", used=10, limit=10)  # the same period: quiet
    assert len(alert_env.mails) == 4 and len(alert_env.webhooks) == 1


async def test_a_usage_alert_says_what_the_limit_does_not_what_an_answer_limit_does(
    alert_env: AlertEnv,
) -> None:
    await alert_env.usage_alert("uploads_per_day", used=10, limit=10)
    template, _, ctx = next(c for c in alert_env.mail_calls if c[2]["level_pct"] == 100)
    assert template == "fair_use_alert"
    assert "New uploads are refused" in ctx["what_happens"]
    assert "contact options" not in ctx["what_happens"]
    assert ctx["metric_label"] == "uploads today"


@pytest.mark.parametrize(
    ("metric", "limit", "used", "words"),
    [
        ("storage_mb", 1, 1024 * 1024, "New uploads are refused"),
        ("fetches_per_day", 50, 50, "Reading your website pauses"),
        ("website_pages_per_day", 10, 10, "Reading your website pauses"),
        ("website_pages_total", 100, 100, "No more website pages"),
        ("cob_questions_per_day", 20, 20, "COB answers with search results only"),
    ],
)
async def test_every_non_answer_limit_alerts(
    alert_env: AlertEnv, metric: str, limit: int, used: int, words: str
) -> None:
    await alert_env.usage_alert(metric, used=used, limit=limit)
    assert "owner:100" in alert_env.mails and "admin:100" in alert_env.mails
    assert words in alert_env.mail_calls[-1][2]["what_happens"]
    assert [w[0] for w in alert_env.webhooks] == [metric]
    assert alert_env.state == "ok" and alert_env.audit == []  # no decision, nothing stored


async def test_an_unlimited_or_exempt_business_costs_no_usage_read(alert_env: AlertEnv) -> None:
    reader = AsyncMock()
    effective = EffectiveFairUse(FairUseLimits(), FairUsePolicy(), False, frozenset())
    with (
        patch("callobuzz_rag.fairuse.limits.effective_fair_use", AsyncMock(return_value=effective)),
        patch("callobuzz_rag.fairuse.usage.read_usage", reader),
    ):
        got = await alerts.after_usage(
            alert_env.factory, TENANT, ["uploads_per_day"], enqueue=alert_env.enqueue
        )
    assert got == [] and reader.await_count == 0


async def test_a_failing_usage_alert_never_raises(alert_env: AlertEnv, caplog) -> None:
    boom = AsyncMock(side_effect=ConnectionError("database is down"))
    with patch("callobuzz_rag.fairuse.limits.effective_fair_use", boom):
        got = await alerts.after_usage(alert_env.factory, TENANT, ["uploads_per_day"])
    assert got == [] and "Fair-use usage alert failed" in caplog.text


# ------------------------------------------------------------- webhooks never hold back mail (FU-7)


async def test_every_mail_is_queued_before_any_webhook_and_without_one_on_request(
    alert_env: AlertEnv,
) -> None:
    """FU-7: after_decision(webhooks=False) leaves the webhook to the caller, so a burst that
    crosses 100 % and 150 % at once has all of its mail queued before any webhook is awaited."""
    notices = await alert_env.decide_and_alert(
        "questions_per_hour", used=46, limit=30, webhooks=False
    )
    assert alert_env.mails == [
        "owner:80",
        "admin:80",
        "owner:100",
        "admin:100",
        "owner:150",
        "admin:150",
    ]
    assert alert_env.webhooks == []  # not sent here
    await alerts.deliver_webhooks(
        alert_env.factory, notices, alert_env.settings(), alert_env.dispatch
    )
    assert [w[1] for w in alert_env.webhooks] == ["soft", "hard"]


async def test_one_slow_or_failing_webhook_never_holds_back_another(caplog) -> None:
    breach = Breach("questions_per_hour", "hard", 30, 46, None)
    pol = FairUsePolicy()
    notices = [
        alerts.Notice(TENANT, "Shree", c, "2026-10-05T14", "pause")
        for c in alerts.crossings(breach, pol)
        if c.kind in ("soft", "hard")
    ]
    assert len(notices) == 2
    seen: list[str] = []

    async def dispatch(event: WebhookEvent, data: dict[str, Any]) -> None:
        if data["level"] == "soft":
            await asyncio.sleep(0.3)  # a subscriber that answers slowly
            raise ConnectionError("it never answers")
        seen.append(data["level"])

    started = asyncio.get_running_loop().time()
    await alerts.deliver_webhooks(object(), notices, SimpleNamespace(), dispatch)
    assert seen == ["hard"]  # the other one's trouble did not stop it
    assert "Fair-use notice webhook failed" in caplog.text
    assert asyncio.get_running_loop().time() - started < 1


async def test_the_webhook_is_not_cut_off_by_the_alert_budget() -> None:
    """FU-7: the 10 s budget used to cover the webhook's retries too, so a failing subscriber
    cancelled the task and every later notice was lost for good (their claims were committed)."""
    app = SimpleNamespace(
        state=SimpleNamespace(
            session_factory=object(),
            settings=SimpleNamespace(fair_use_alert_task_timeout_seconds=0.05),
        )
    )
    delivered: list[str] = []
    notice = alerts.Notice(
        TENANT,
        "Shree",
        alerts.Crossing(
            "questions_per_hour", 100, "soft", Breach("questions_per_hour", "soft", 30, 31, None)
        ),
        "k",
        "downgrade",
    )

    async def after(*a: Any, **k: Any) -> list[Any]:
        assert k["webhooks"] is False  # the write and the mail only
        return [notice]

    async def slow_webhooks(factory: Any, notices: Any, settings: Any = None, dispatch: Any = None):
        await asyncio.sleep(0.2)  # four times the alert budget
        delivered.append("sent")

    decision = decide(
        cap_blocked=False,
        billing=NOT_BILLED,
        breaches=[],
        policy=FairUsePolicy(),
        plan_profile=STD,
        downgrade_to=FREE,
        exempt=False,
    )
    eff = EffectiveFairUse(FairUseLimits(), FairUsePolicy(), False, frozenset())
    with (
        patch.object(alerts, "after_decision", after),
        patch.object(alerts, "deliver_webhooks", slow_webhooks),
    ):
        alerts.schedule(app, TENANT, decision, eff)
        await asyncio.wait_for(asyncio.gather(*app.state.fair_use_tasks), 3)
    assert delivered == ["sent"]


async def test_a_worker_drains_the_alert_tasks_before_it_closes_its_engine() -> None:
    app = SimpleNamespace(
        state=SimpleNamespace(
            session_factory=object(),
            settings=SimpleNamespace(fair_use_alert_task_timeout_seconds=5),
        )
    )
    finished: list[str] = []

    async def after(*a: Any, **k: Any) -> list[Any]:
        await asyncio.sleep(0.1)
        finished.append("mail")
        return []

    decision = decide(
        cap_blocked=False,
        billing=NOT_BILLED,
        breaches=[],
        policy=FairUsePolicy(),
        plan_profile=STD,
        downgrade_to=FREE,
        exempt=False,
    )
    eff = EffectiveFairUse(FairUseLimits(), FairUsePolicy(), False, frozenset())
    with patch.object(alerts, "after_decision", after):
        alerts.schedule(app, TENANT, decision, eff)
        assert finished == []  # still in flight: this is what a loop shutdown would cancel
        await alerts.drain(app, timeout=2)
    assert finished == ["mail"]


async def test_draining_is_bounded(caplog) -> None:
    async def forever() -> None:
        await asyncio.sleep(30)

    task = asyncio.get_running_loop().create_task(forever())
    app = SimpleNamespace(state=SimpleNamespace(fair_use_tasks={task}))
    started = asyncio.get_running_loop().time()
    await alerts.drain(app, timeout=0.05)
    task.cancel()
    assert asyncio.get_running_loop().time() - started < 1
    assert "still running" in caplog.text
