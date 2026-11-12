"""The 1st-of-the-month value report job (TV4): freeze once, e-mail once, retry on a queue
failure, skip who should be skipped."""

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts.email.render import TEMPLATES, validate_context
from callobuzz_rag.accounts.settings_store import EngineSettings
from callobuzz_rag.business.value import ValueInputs
from callobuzz_rag.business.value_freeze import freeze_and_send
from callobuzz_rag.business.value_report import WorkspaceContext
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from callobuzz_rag.db.models.value import ValueReport, ValueSettings
from tests.unit.accounts_fakes import FakeDB

MODULE = "callobuzz_rag.business.value_report."
SETTINGS = SimpleNamespace(dashboard_url="https://app.sahayak.example")
CTX = WorkspaceContext("clinic", "Asia/Kolkata", "INR")
# 02:45 IST on 1 November
FIRST = datetime(2026, 10, 31, 21, 15, tzinfo=UTC)


def _inputs(conversations: int = 188, leads_unrated: int = 0) -> ValueInputs:
    return ValueInputs(
        conversations=conversations,
        questions=300,
        answered=412 if conversations else 0,
        answered_after_hours=61 if conversations else 0,
        leads_total=12,
        leads_qualified=9 if conversations else 0,
        leads_partial=2,
        leads_unrated=leads_unrated,
        handoffs=23 if conversations else 0,
        bookings=14,
        median_first_reply_ms=800,
        top_questions=[("root canal ka kitna charge hai?", 17)] if conversations else [],
        unanswered=[("do you take cards", 4)] if conversations else [],
    )


class Env:
    def __init__(self) -> None:
        self.db = FakeDB()
        self.mails: list[tuple[str, str, dict[str, Any]]] = []
        self.fail = False
        self.fail_for: set[str] = set()
        self.conversations = 188

    def enqueue(self, template: str, to: str, context: Any, **_: Any) -> None:
        if self.fail or to in self.fail_for:
            raise RuntimeError("broker down")
        self.mails.append((template, to, dict(context)))

    def tenant(self, name: str = "Shree Dental Clinic, Pune", **kw: Any) -> Tenant:
        defaults: dict[str, Any] = {
            "status": "active",
            "max_corpora": 5,
            "api_key_hash": "",
            "created_at": datetime(2026, 10, 5, tzinfo=UTC),
        }
        return self.db.add(Tenant(name=name, **{**defaults, **kw}))

    def owner(self, tenant: Tenant, email: str = "owner@shreedental.in") -> User:
        user = self.db.add(User(email=email, name="Owner", is_active=True))
        self.db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))
        return user

    async def run(
        self, now: datetime = FIRST, engine: EngineSettings | None = None
    ) -> dict[str, int]:
        return await freeze_and_send(
            self.db.factory(),
            SETTINGS,
            now=now,
            enqueue=self.enqueue,
            engine_settings=engine or EngineSettings(),
        )


@pytest.fixture
def env() -> Iterator[Env]:
    e = Env()
    with (
        patch(MODULE + "workspace_context", AsyncMock(return_value=CTX)),
        patch(
            MODULE + "fetch_inputs",
            AsyncMock(side_effect=lambda *_a, **_k: _inputs(e.conversations)),
        ),
        patch(MODULE + "after_hours_since", AsyncMock(return_value=None)),
    ):
        yield e


async def test_october_is_frozen_once_and_mailed_once(env: Env) -> None:
    tenant = env.tenant()
    env.owner(tenant)
    first = await env.run()
    assert first == {"frozen": 1, "emailed": 1, "failed": 0}
    (row,) = env.db.all(ValueReport)
    assert row.month.isoformat() == "2026-10-01" and row.emailed_at is not None
    assert row.data["breakdown"]["total_minor"] > 0
    again = await env.run()
    assert again == {"frozen": 0, "emailed": 0, "failed": 0}
    assert len(env.db.all(ValueReport)) == 1 and len(env.mails) == 1


async def test_the_email_goes_to_owners_with_the_templates_exact_context(env: Env) -> None:
    tenant = env.tenant()
    env.owner(tenant, "owner@shreedental.in")
    env.owner(tenant, "partner@shreedental.in")
    await env.run()
    assert sorted(to for _, to, _ in env.mails) == [
        "owner@shreedental.in",
        "partner@shreedental.in",
    ]
    template, to, context = env.mails[0]
    assert template == "value_report"
    assert set(context) == TEMPLATES["value_report"].required
    validate_context(template, to, context)
    assert context["month_label"] == "October 2026"
    assert context["business_name"] == "Shree Dental Clinic, Pune"
    assert context["total"].startswith("₹")
    assert context["parts"][0]["amount"] == "₹13,500"
    assert context["report_url"] == "https://app.sahayak.example/value?month=2026-10"
    assert context["first_reply"] == "under a second"
    assert context["saved_settings"] is False  # no row: the suggested values were used


async def test_the_deployer_is_copied_only_when_the_switch_is_on(env: Env) -> None:
    tenant = env.tenant()
    env.owner(tenant)
    engine = EngineSettings.model_validate(
        {
            "branding": {"support_email": "help@sahayak.example"},
            "trust": {"value_report_cc_deployer": True},
        }
    )
    await env.run(engine=engine)
    assert sorted(to for _, to, _ in env.mails) == ["help@sahayak.example", "owner@shreedental.in"]


async def test_no_copy_to_the_deployer_by_default(env: Env) -> None:
    tenant = env.tenant()
    env.owner(tenant)
    engine = EngineSettings.model_validate({"branding": {"support_email": "help@sahayak.example"}})
    await env.run(engine=engine)
    assert [to for _, to, _ in env.mails] == ["owner@shreedental.in"]


async def test_a_month_with_no_conversations_is_frozen_but_not_mailed(env: Env) -> None:
    env.conversations = 0
    env.owner(env.tenant())
    assert await env.run() == {"frozen": 1, "emailed": 0, "failed": 0}
    assert env.mails == []
    (row,) = env.db.all(ValueReport)
    assert row.data["breakdown"]["total_minor"] == 0 and row.emailed_at is None


async def test_the_deployer_can_switch_the_email_off(env: Env) -> None:
    env.owner(env.tenant())
    engine = EngineSettings.model_validate({"trust": {"value_report_enabled": False}})
    assert await env.run(engine=engine) == {"frozen": 1, "emailed": 0, "failed": 0}
    assert env.mails == []


async def test_an_owner_can_switch_their_own_email_off(env: Env) -> None:
    tenant = env.tenant()
    env.owner(tenant)
    env.db.add(
        ValueSettings(
            tenant_id=tenant.id,
            currency="INR",
            lead_value_minor=1,
            handoff_value_minor=1,
            staff_hour_minor=1,
            minutes_per_answer=3,
            email_enabled=False,
        )
    )
    assert (await env.run())["emailed"] == 0
    assert env.mails == []


async def test_saved_values_are_reported_as_the_owners_own(env: Env) -> None:
    from callobuzz_rag.business.value import ValueSettingsIn
    from callobuzz_rag.business.value_report import SettingsState

    env.owner(env.tenant())
    state = SettingsState(
        values=ValueSettingsIn(
            lead_value_minor=150000,
            handoff_value_minor=80000,
            staff_hour_minor=25000,
            minutes_per_answer=3,
        ),
        saved=True,
        currency="INR",
        email_enabled=True,
        industry_defaults=None,
        business_type=None,
    )
    with patch(MODULE + "settings_state", AsyncMock(return_value=state)):
        await env.run()
    assert env.mails[0][2]["saved_settings"] is True


@pytest.mark.parametrize(
    "kw",
    [{"status": "pending"}, {"status": "suspended"}, {"is_operator": True}, {"is_system": True}],
)
async def test_workspaces_that_get_nothing(env: Env, kw: dict[str, Any]) -> None:
    env.owner(env.tenant(**kw))
    assert await env.run() == {"frozen": 0, "emailed": 0, "failed": 0}
    assert env.db.all(ValueReport) == [] and env.mails == []


async def test_a_queue_failure_leaves_the_stamp_empty_and_the_next_run_retries(env: Env) -> None:
    env.owner(env.tenant())
    env.fail = True
    assert await env.run() == {"frozen": 1, "emailed": 0, "failed": 0}
    (row,) = env.db.all(ValueReport)
    assert row.emailed_at is None
    env.fail = False
    assert await env.run(now=datetime(2026, 11, 1, 22, 15, tzinfo=UTC)) == {
        "frozen": 0,
        "emailed": 1,
        "failed": 0,
    }
    assert len(env.mails) == 1 and row.emailed_at is not None
    assert len(env.db.all(ValueReport)) == 1


async def test_outside_the_first_days_a_missing_month_is_frozen_but_not_mailed(env: Env) -> None:
    """The job runs every hour; a month missed while the worker was down is still frozen, but
    its e-mail goes only in the first week."""
    env.owner(env.tenant())
    assert await env.run(now=datetime(2026, 11, 10, 6, 0, tzinfo=UTC)) == {
        "frozen": 1,
        "emailed": 0,
        "failed": 0,
    }
    (row,) = env.db.all(ValueReport)
    assert row.emailed_at is None and env.mails == []


async def test_a_worker_down_from_the_1st_to_the_4th_still_freezes_and_mails_october(
    env: Env,
) -> None:
    env.owner(env.tenant())
    assert await env.run(now=datetime(2026, 11, 4, 5, 0, tzinfo=UTC)) == {
        "frozen": 1,
        "emailed": 1,
        "failed": 0,
    }
    assert len(env.mails) == 1


async def test_every_closed_month_without_a_row_is_frozen_but_only_the_latest_is_mailed(
    env: Env,
) -> None:
    env.owner(env.tenant(created_at=datetime(2026, 8, 10, tzinfo=UTC)))
    assert await env.run() == {"frozen": 3, "emailed": 1, "failed": 0}
    rows = sorted(env.db.all(ValueReport), key=lambda r: r.month)
    assert [r.month.isoformat() for r in rows] == ["2026-08-01", "2026-09-01", "2026-10-01"]
    assert [r.emailed_at is not None for r in rows] == [False, False, True]
    assert [m[2]["month_label"] for m in env.mails] == ["October 2026"]
    assert await env.run() == {"frozen": 0, "emailed": 0, "failed": 0}


async def test_a_suspended_workspace_is_frozen_when_it_is_active_again(env: Env) -> None:
    tenant = env.tenant(status="suspended")
    env.owner(tenant)
    assert (await env.run())["frozen"] == 0
    tenant.status = "active"
    assert (await env.run(now=datetime(2026, 11, 3, 5, 0, tzinfo=UTC)))["frozen"] == 1


async def test_a_workspace_made_after_the_month_has_nothing_to_freeze(env: Env) -> None:
    env.owner(env.tenant(created_at=datetime(2026, 11, 1, tzinfo=UTC)))
    assert (await env.run())["frozen"] == 0


async def test_one_failing_workspace_does_not_stop_the_others(env: Env) -> None:
    env.owner(env.tenant("A Clinic"), "a@a.example")
    env.owner(env.tenant("B Clinic"), "b@b.example")
    calls = {"n": 0}

    async def flaky(*_a: Any, **_k: Any) -> ValueInputs:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database hiccup")
        return _inputs()

    with patch(MODULE + "fetch_inputs", AsyncMock(side_effect=flaky)):
        result = await env.run()
    assert result == {"frozen": 1, "emailed": 1, "failed": 1}


async def test_the_email_says_leads_when_no_rules_rated_them(env: Env) -> None:
    env.owner(env.tenant())
    with patch(MODULE + "fetch_inputs", AsyncMock(side_effect=lambda *_a, **_k: _inputs(188, 9))):
        await env.run()
    context = env.mails[0][2]
    assert context["leads_label"] == "leads"
    assert context["parts"][0]["label"] == "Leads"
    assert context["parts"][0]["formula"].startswith("9 leads ")


async def test_a_partly_failed_queue_never_mails_the_same_person_twice(env: Env) -> None:
    tenant = env.tenant()
    env.owner(tenant, "a@a.example")
    env.owner(tenant, "b@b.example")
    env.fail_for = {"b@b.example"}
    assert await env.run() == {"frozen": 1, "emailed": 0, "failed": 0}
    (row,) = env.db.all(ValueReport)
    assert row.emailed_at is None and row.emailed_to == ["a@a.example"]
    env.fail_for = set()
    assert await env.run(now=datetime(2026, 11, 1, 22, 15, tzinfo=UTC)) == {
        "frozen": 0,
        "emailed": 1,
        "failed": 0,
    }
    assert sorted(to for _, to, _ in env.mails) == ["a@a.example", "b@b.example"]
    assert row.emailed_at is not None
    assert sorted(row.emailed_to) == ["a@a.example", "b@b.example"]
    assert await env.run(now=datetime(2026, 11, 2, 1, 0, tzinfo=UTC)) == {
        "frozen": 0,
        "emailed": 0,
        "failed": 0,
    }
    assert len(env.mails) == 2


async def test_a_failing_address_keeps_failing_without_blocking_the_others(env: Env) -> None:
    tenant = env.tenant()
    env.owner(tenant, "a@a.example")
    env.owner(tenant, "bad@b.example")
    env.fail_for = {"bad@b.example"}
    for hour in (0, 1):
        await env.run(now=datetime(2026, 11, 1, 21 + hour, 0, tzinfo=UTC))
    assert [to for _, to, _ in env.mails] == ["a@a.example"]


def test_the_row_is_claimed_with_skip_locked_so_overlapping_runs_do_not_both_mail() -> None:
    import uuid
    from datetime import date

    from sqlalchemy.dialects import postgresql

    from callobuzz_rag.business.value_freeze import claim_statement

    sql = str(
        claim_statement(uuid.uuid4(), date(2026, 10, 1)).compile(dialect=postgresql.dialect())
    )
    assert "FOR UPDATE SKIP LOCKED" in sql
