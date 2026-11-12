"""Low-balance levels, pause transitions and their notices (BIL4 task 5)."""

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.billing import alerts, hooks
from callobuzz_rag.core.events import WebhookEvent
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.tenant import Tenant
from tests.unit.accounts_fakes import FakeDB
from tests.unit.billing_fakes import fake_accounts, stored_pricing

# ------------------------------------------------------------------------ pure levels


def test_levels_prepaid() -> None:
    assert alerts.alert_level(1_900, 10_000, [20, 5, 0], paused=False) == 20
    assert alerts.alert_level(400, 10_000, [20, 5, 0], paused=False) == 5
    assert alerts.alert_level(0, 10_000, [20, 5, 0], paused=True) == 0
    assert alerts.alert_level(2_500, 10_000, [20, 5, 0], paused=False) is None
    assert alerts.alert_level(2_000, 10_000, [20, 5, 0], paused=False) == 20  # at the line
    assert alerts.alert_level(5, 0, [20, 5, 0], paused=False) is None  # no basis, still positive
    assert alerts.alert_level(0, 0, [20, 5], paused=False) == 5  # nothing left on no basis


def test_basis_is_the_last_top_up_or_the_included_credit_or_the_credit_limit() -> None:
    kw: dict[str, Any] = {"credit_limit_minor": 50_000, "included_credit_minor": 3_000}
    assert alerts.alert_basis(payment_type="prepaid", last_top_up_minor=10_000, **kw) == 10_000
    assert alerts.alert_basis(payment_type="prepaid", last_top_up_minor=0, **kw) == 3_000
    assert alerts.alert_basis(payment_type="postpaid", last_top_up_minor=10_000, **kw) == 50_000


def test_one_notice_per_downward_crossing_and_rearm() -> None:
    prev: int | None = None
    was_paused = False
    sent: list[str] = []
    for level, paused in [
        (None, False),
        (20, False),
        (20, False),
        (5, False),
        (5, False),
        (0, True),
        (0, True),
        (None, False),
        (20, False),
    ]:
        t = alerts.plan_transition(
            was_paused=was_paused, previous_level=prev, paused=paused, level=level
        )
        if t.paused_at_set:
            sent.append("paused")
        if t.paused_at_cleared:
            sent.append("resumed")
        if t.low_level is not None:
            sent.append(f"low:{t.low_level}")
        prev, was_paused = t.new_alert_level, paused
    assert sent == ["low:20", "low:5", "paused", "resumed", "low:20"]


def test_rising_inside_the_levels_keeps_the_memory() -> None:
    t = alerts.plan_transition(was_paused=False, previous_level=5, paused=False, level=20)
    assert t.low_level is None and t.new_alert_level == 5


def test_resuming_into_a_band_stores_the_band_and_sends_no_low_notice() -> None:
    t = alerts.plan_transition(was_paused=True, previous_level=0, paused=False, level=5)
    assert t.paused_at_cleared and t.low_level is None and t.new_alert_level == 5
    t = alerts.plan_transition(was_paused=True, previous_level=0, paused=False, level=None)
    assert t.paused_at_cleared and t.new_alert_level is None


# ------------------------------------------------------------------- the hook, end to end


class HookEnv:
    def __init__(self, db: FakeDB, account: BillingAccount, tenant: Tenant) -> None:
        self.db = db
        self.account = account
        self.tenant = tenant
        self.mails: list[tuple[str, str]] = []
        self.mail_contexts: list[dict[str, Any]] = []
        self.webhooks: list[tuple[str, dict[str, Any]]] = []

    def set_available(self, minor: int) -> None:
        self.account.balance_minor = minor

    async def run(self) -> None:
        await hooks.after_balance_change(
            self.db.factory(), [self.account.id], dispatch=self._dispatch, enqueue=self._enqueue
        )

    async def _dispatch(self, event: WebhookEvent, data: dict[str, Any]) -> None:
        self.webhooks.append((event.value, data))

    def _enqueue(self, template: str, to: str, context: Any, **_: Any) -> None:
        self.mails.append((template, to))
        self.mail_contexts.append(dict(context))

    @property
    def audit(self) -> list[str]:
        return [a.action for a in self.db.all(AuditLog)]


@pytest.fixture
def hook_env() -> Iterator[HookEnv]:
    db = FakeDB()
    tenant = db.add(Tenant(name="Shree Labs", status="active", max_corpora=5, api_key_hash=""))
    db.add(
        stored_pricing(
            db.default_plan,
            usage_mode="per_question",
            per_question_minor=200,
            payment_type="prepaid",
        )
    )
    account = db.add(
        BillingAccount(
            tenant_id=tenant.id,
            currency="INR",
            ledger_seq=1,
            balance_minor=5_000,
            last_top_up_minor=10_000,
            pause_override="auto",
            usage_residue_micro=0,
        )
    )
    settings = SimpleNamespace(
        dashboard_url="https://app.example", superadmin_email="sa@example.com"
    )
    with (
        fake_accounts(db),
        patch.object(hooks, "billing_enabled", lambda: True),
        patch.object(hooks, "_owner_emails", AsyncMock(return_value=["owner@shree.example"])),
        patch.object(hooks, "_support_email", AsyncMock(return_value="help@op.example")),
        patch("callobuzz_rag.core.config.get_settings", lambda: settings),
    ):
        yield HookEnv(db, account, tenant)


async def test_one_mail_per_crossing_and_rearm(hook_env: HookEnv) -> None:
    for available in (5_000, 1_900, 1_800, 400, 300, 0, -10, 6_000, 1_500):
        hook_env.set_available(available)
        await hook_env.run()
    templates = [m[0] for m in hook_env.mails]
    assert templates == [
        "billing_low_balance",
        "billing_low_balance",
        "billing_paused",  # the owner ...
        "billing_paused",  # ... and the super admin
        "billing_low_balance",
    ]
    assert [c.get("level_pct") for c in hook_env.mail_contexts if "level_pct" in c] == [20, 5, 20]
    assert [w[0] for w in hook_env.webhooks] == [
        "billing.low_balance",
        "billing.low_balance",
        "billing.paused",
        "billing.low_balance",
    ]
    assert hook_env.audit == ["billing.paused", "billing.resumed"]
    assert hook_env.account.alert_level == 20 and hook_env.account.paused_at is None


async def test_paused_mail_goes_to_owners_and_the_super_admin(hook_env: HookEnv) -> None:
    hook_env.set_available(0)
    await hook_env.run()
    assert {to for tpl, to in hook_env.mails if tpl == "billing_paused"} == {
        "owner@shree.example",
        "sa@example.com",
    }
    ctx = hook_env.mail_contexts[0]
    assert ctx["billing_url"] == "https://app.example/plan"
    assert ctx["support_email"] == "help@op.example"
    event, data = hook_env.webhooks[0]
    assert event == "billing.paused"
    assert data["reason"] == "prepaid_empty" and data["currency"] == "INR"
    assert data["tenant_id"] == str(hook_env.tenant.id) and data["available_minor"] == 0
    assert hook_env.account.paused_at is not None and hook_env.account.alert_level == 0


async def test_low_mail_says_how_much_is_left_and_only_owners_get_it(hook_env: HookEnv) -> None:
    hook_env.set_available(1_900)
    await hook_env.run()
    assert hook_env.mails == [("billing_low_balance", "owner@shree.example")]
    assert hook_env.mail_contexts[0]["balance"] == "₹19.00"
    assert hook_env.webhooks[0][1]["level_pct"] == 20


async def test_postpaid_uses_the_remaining_credit(hook_env: HookEnv) -> None:
    from callobuzz_rag.db.models.plan_pricing import PlanPricing

    row = next(p for p in hook_env.db.all(PlanPricing))
    row.payment_type = "postpaid"
    row.credit_limit_minor = 100_000
    hook_env.set_available(-85_000)  # 15 % of the credit left
    await hook_env.run()
    assert hook_env.mail_contexts[0]["level_pct"] == 20
    assert hook_env.mail_contexts[0]["balance"] == "₹150.00"
    hook_env.set_available(-100_000)
    await hook_env.run()
    assert hook_env.audit == ["billing.paused"]
    assert hook_env.webhooks[-1][1]["reason"] == "credit_limit"


async def test_an_unchanged_state_is_silent(hook_env: HookEnv) -> None:
    hook_env.set_available(5_000)
    await hook_env.run()
    assert hook_env.mails == [] and hook_env.webhooks == [] and hook_env.audit == []


async def test_a_forced_pause_is_audited_as_the_actor_and_resumes_when_lifted(
    hook_env: HookEnv,
) -> None:
    hook_env.account.pause_override = "force"
    await hooks.after_balance_change(
        hook_env.db.factory(),
        [hook_env.account.id],
        actor=("admin_key", None, "admin key"),
        dispatch=hook_env._dispatch,
        enqueue=hook_env._enqueue,
    )
    (row,) = hook_env.db.all(AuditLog)
    assert row.action == "billing.paused" and row.actor_label == "admin key"
    assert row.details["reason"] == "forced"
    hook_env.account.pause_override = "auto"
    await hook_env.run()
    assert hook_env.audit == ["billing.paused", "billing.resumed"]


async def test_a_failing_hook_never_raises(hook_env: HookEnv) -> None:
    with patch.object(alerts, "snapshot", AsyncMock(side_effect=RuntimeError("db down"))):
        await hook_env.run()  # logged, not raised
    assert hook_env.mails == []


async def test_exempt_and_unbilled_accounts_are_ignored(hook_env: HookEnv) -> None:
    from callobuzz_rag.db.models.plan_pricing import PlanPricing

    next(iter(hook_env.db.all(PlanPricing))).usage_mode = "included"
    hook_env.set_available(-50)
    await hook_env.run()
    assert hook_env.mails == [] and hook_env.audit == []
    hook_env.tenant.is_operator = True
    await hook_env.run()
    assert hook_env.audit == []


async def test_a_paused_account_that_stops_being_billed_is_resumed(hook_env: HookEnv) -> None:
    from datetime import UTC, datetime

    from callobuzz_rag.db.models.plan_pricing import PlanPricing

    hook_env.account.paused_at = datetime.now(UTC)
    hook_env.account.alert_level = 0
    next(iter(hook_env.db.all(PlanPricing))).usage_mode = "included"  # nothing is charged now
    await hook_env.run()
    assert hook_env.account.paused_at is None and hook_env.account.alert_level is None
    assert hook_env.audit == ["billing.resumed"]
    assert hook_env.mails == []  # nobody is told "resumed"


async def test_a_paused_account_that_becomes_exempt_is_resumed(hook_env: HookEnv) -> None:
    from datetime import UTC, datetime

    hook_env.account.paused_at = datetime.now(UTC)
    hook_env.account.alert_level = 0
    hook_env.tenant.is_operator = True
    await hook_env.run()
    assert hook_env.account.paused_at is None
    assert hook_env.audit == ["billing.resumed"]


async def test_no_session_factory_is_a_no_op() -> None:
    await hooks.after_balance_change(None, [uuid.uuid4()])


async def test_the_paused_mail_carries_the_reason(hook_env: HookEnv) -> None:
    hook_env.account.pause_override = "force"
    await hook_env.run()
    ctx = next(c for c in hook_env.mail_contexts if "reason" in c)
    assert ctx["reason"] == "forced"
