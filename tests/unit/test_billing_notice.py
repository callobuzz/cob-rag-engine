"""Price changes with notice (TV3): the impact of a change, the earliest start, the period a
workspace pays for, and the saving rules, the cancel and the hourly activation."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.notice import (
    activate_due,
    cancel_scheduled,
    current_period_start,
    decide,
    earliest_effective,
    impact,
    ist_midnight,
    overall_impact,
    period_start,
    preview_change,
    save_with_notice,
    version_in_force,
)
from callobuzz_rag.billing.pricing import PricingFields
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from tests.unit.accounts_fakes import FakeDB
from tests.unit.billing_fakes import stored_pricing

BASE = PricingFields(
    currency="INR",
    subscription_fee_minor=99_900,
    usage_mode="per_question",
    per_question_minor=200,
    included_credit_minor=50_000,
)
NOW = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # 19:30 IST


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"subscription_fee_minor": 79_900}, "lower"),
        ({"subscription_fee_minor": 99_900}, "same"),
        ({"subscription_fee_minor": 129_900}, "higher"),
        ({"per_question_minor": 150, "included_credit_minor": 40_000}, "mixed"),
        ({"included_credit_minor": 60_000}, "lower"),
        ({"overage": "charge"}, "higher"),
        ({"usage_mode": "cost_plus", "markup_bp": 2000}, "mixed"),
        ({"billing_period": "yearly"}, "mixed"),
        ({"credit_limit_minor": 100}, "lower"),
        ({"included_credit_expires": False}, "lower"),
    ],
)
def test_impact(change: dict[str, Any], expected: str) -> None:
    assert impact(BASE, BASE.model_copy(update=change)) == expected


def test_earliest_effective_is_the_next_ist_midnight_after_the_notice() -> None:
    assert earliest_effective(NOW, 30) == datetime(2026, 11, 4, 18, 30, tzinfo=UTC)  # 5 Nov 00:00


@pytest.mark.parametrize(
    "next_charge,today,period,day,start",
    [
        (date(2026, 10, 12), date(2026, 10, 5), "monthly", 12, date(2026, 9, 12)),
        (date(2026, 10, 5), date(2026, 10, 5), "monthly", 5, date(2026, 10, 5)),  # renews today
        (date(2027, 3, 1), date(2026, 10, 5), "yearly", 1, date(2026, 3, 1)),
        (None, date(2026, 10, 5), "monthly", 1, date(2026, 10, 1)),  # no subscription
        (date(2026, 11, 3), date(2026, 10, 5), "monthly", 3, date(2026, 10, 3)),
        (date(2026, 1, 20), date(2026, 1, 5), "monthly", 20, date(2025, 12, 20)),  # year boundary
        (
            date(2027, 3, 1),
            date(2026, 11, 10),
            "monthly",
            1,
            date(2026, 11, 1),
        ),  # never in the future
    ],
)
def test_current_period_start(
    next_charge: date | None, today: date, period: str, day: int, start: date
) -> None:
    assert current_period_start(next_charge, today, period, day) == start


# ---------------------------------------------------------------- the pure decision


def _decide(result: str, apply: str, effective_at: datetime | None = None, **kw: Any) -> Any:
    args: dict[str, Any] = {
        "result": result,
        "workspaces": 3,
        "apply": apply,
        "effective_at": effective_at,
        "now": NOW,
        "notice_days": 30,
    }
    return decide(**{**args, **kw})


def test_a_raise_cannot_start_now() -> None:
    with pytest.raises(APIError) as err:
        _decide("higher", "now")
    assert err.value.code == "price_change_not_lower" and err.value.status_code == 422


def test_a_raise_needs_the_notice_period() -> None:
    with pytest.raises(APIError) as err:
        _decide("mixed", "scheduled", ist_midnight(date(2026, 11, 1)))
    assert err.value.code == "price_change_notice_too_short"
    assert err.value.extra["earliest"] == earliest_effective(NOW, 30).isoformat()


def test_a_raise_defaults_to_the_earliest_day_and_rounds_up_to_midnight() -> None:
    assert _decide("higher", "scheduled").effective_at == earliest_effective(NOW, 30)
    odd = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)  # 11:30 IST
    assert _decide("higher", "scheduled", odd).effective_at == ist_midnight(date(2026, 11, 11))


def test_a_drop_may_start_now_or_later() -> None:
    assert _decide("lower", "now").scheduled is False
    assert _decide("same", "now").scheduled is False
    later = _decide("lower", "scheduled", ist_midnight(date(2026, 10, 20)))
    assert later.scheduled and later.effective_at == ist_midnight(date(2026, 10, 20))


def test_a_plan_nobody_is_on_starts_at_once() -> None:
    assert _decide("higher", "now", workspaces=0).scheduled is False


def test_a_first_version_on_a_plan_with_businesses_needs_notice_like_any_raise() -> None:
    with pytest.raises(APIError) as err:
        _decide("higher", "now", workspaces=40)
    assert err.value.code == "price_change_not_lower"


# ---------------------------------------------------------------- versions in force


def _row(version: int, effective_at: datetime | None, *, current: bool = False, **f: Any) -> Any:
    row = stored_pricing(
        _PLAN, version=version, usage_mode="per_question", per_question_minor=f.pop("pq", 200), **f
    )
    row.is_current = current
    row.effective_at = effective_at
    return row


class _Plan:
    id = None
    is_internal = False
    name = "Growth"


_PLAN: Any = _Plan()


def test_the_version_in_force_is_the_newest_not_after_the_period_start() -> None:
    v1 = _row(1, None)
    v2 = _row(2, ist_midnight(date(2026, 11, 5)))
    assert version_in_force([v1, v2], ist_midnight(date(2026, 10, 12))) is v1
    assert version_in_force([v1, v2], ist_midnight(date(2026, 11, 5))) is v2
    assert version_in_force([v1, v2], ist_midnight(date(2026, 11, 12))) is v2
    assert version_in_force([v2], ist_midnight(date(2026, 10, 12))) is None


def test_impact_looks_back_while_a_raise_is_still_rolling_out() -> None:
    v1 = _row(1, None, current=False, subscription_fee_minor=99_900)
    v2 = _row(
        2,
        NOW - timedelta(days=3),
        current=True,
        subscription_fee_minor=129_900,
    )
    cheaper_than_v2_but_dearer_than_v1 = PricingFields(
        usage_mode="per_question", per_question_minor=200, subscription_fee_minor=119_900
    )
    assert impact(PricingFields(**{**_f(v2)}), cheaper_than_v2_but_dearer_than_v1) == "lower"
    assert overall_impact([v1, v2], v2, cheaper_than_v2_but_dearer_than_v1, NOW) == "higher"


def _f(row: Any) -> dict[str, Any]:
    from callobuzz_rag.billing.pricing import fields_from_row

    return fields_from_row(row).model_dump()


# ---------------------------------------------------------------- saving, cancelling, activating


class Env:
    def __init__(self, *, workspaces: int = 2) -> None:
        from callobuzz_rag.db.models.plan import Plan
        from callobuzz_rag.db.models.tenant import Tenant

        self.db = FakeDB()
        self.plan = self.db.add(Plan(name="Growth", is_active=True))
        for i in range(workspaces):
            self.db.add(
                Tenant(
                    name=f"Clinic {i}",
                    status="active",
                    max_corpora=3,
                    api_key_hash="",
                    plan_id=self.plan.id,
                )
            )
        self.v1 = self.db.add(
            stored_pricing(
                self.plan,
                subscription_fee_minor=99_900,
                usage_mode="per_question",
                per_question_minor=200,
            )
        )
        self.v1.effective_at = None

    def versions(self) -> list[PlanPricing]:
        return sorted(self.db.all(PlanPricing), key=lambda r: r.version)

    async def save(self, **change: Any) -> Any:
        fields = PricingFields(
            **{
                **BASE.model_dump(),
                "included_credit_minor": 0,
                "subscription_fee_minor": 99_900,
                **change.pop("fields", {}),
            }
        )
        return await save_with_notice(
            self.db.session(),
            self.plan,
            fields,
            created_by="admin",
            apply=change.pop("apply", "now"),
            effective_at=change.pop("effective_at", None),
            notice_days=30,
            now=NOW,
        )


async def test_a_raise_is_stored_scheduled_and_the_current_version_does_not_change() -> None:
    env = Env()
    saved = await env.save(fields={"subscription_fee_minor": 129_900}, apply="scheduled")
    assert saved.scheduled and saved.impact == "higher" and saved.workspaces == 2
    assert saved.row.is_current is False and saved.row.version == 2
    assert saved.row.effective_at == earliest_effective(NOW, 30)
    assert env.v1.is_current is True


async def test_a_raise_applied_now_is_refused_and_writes_nothing() -> None:
    env = Env()
    with pytest.raises(APIError) as err:
        await env.save(fields={"subscription_fee_minor": 129_900}, apply="now")
    assert err.value.code == "price_change_not_lower"
    assert len(env.versions()) == 1


async def test_a_drop_applies_now_and_becomes_current() -> None:
    env = Env()
    saved = await env.save(fields={"subscription_fee_minor": 79_900})
    assert not saved.scheduled and saved.impact == "lower"
    assert saved.row.is_current and not env.v1.is_current


async def test_a_plan_with_no_workspaces_changes_at_once_whatever_the_impact() -> None:
    env = Env(workspaces=0)
    saved = await env.save(fields={"subscription_fee_minor": 500_000})
    assert not saved.scheduled and saved.row.is_current


async def test_saving_again_replaces_the_scheduled_version() -> None:
    env = Env()
    first = await env.save(fields={"subscription_fee_minor": 129_900}, apply="scheduled")
    second = await env.save(fields={"subscription_fee_minor": 119_900}, apply="scheduled")
    assert second.replaced == [first.row]
    assert [v.version for v in env.versions()] == [1, 2]
    assert env.versions()[-1].subscription_fee_minor == 119_900


async def test_cancel_deletes_the_scheduled_version_and_says_when_nothing_is_scheduled() -> None:
    env = Env()
    await env.save(fields={"subscription_fee_minor": 129_900}, apply="scheduled")
    gone = await cancel_scheduled(env.db.session(), env.plan)
    assert [g.version for g in gone] == [2] and len(env.versions()) == 1
    with pytest.raises(APIError) as err:
        await cancel_scheduled(env.db.session(), env.plan)
    assert err.value.code == "scheduled_pricing_not_found" and err.value.status_code == 404


async def test_preview_writes_nothing_and_reports_the_reach() -> None:
    env = Env()
    p = await preview_change(
        env.db.session(),
        env.plan,
        PricingFields(
            usage_mode="per_question", per_question_minor=200, subscription_fee_minor=129_900
        ),
        notice_days=30,
        now=NOW,
    )
    assert (p.impact, p.workspaces, p.changed) == ("higher", 2, ["subscription_fee_minor"])
    assert p.earliest_effective_at == earliest_effective(NOW, 30)
    assert len(env.versions()) == 1


async def test_the_beat_activates_a_due_version_once() -> None:
    env = Env()
    await env.save(fields={"subscription_fee_minor": 129_900}, apply="scheduled")
    before = earliest_effective(NOW, 30) - timedelta(hours=1)
    after = earliest_effective(NOW, 30) + timedelta(hours=1)
    assert await activate_due(env.db.factory(), before) == 0
    assert await activate_due(env.db.factory(), after) == 1
    assert await activate_due(env.db.factory(), after) == 0
    v1, v2 = env.versions()
    assert (v1.is_current, v2.is_current) == (False, True)
    assert sum(v.is_current for v in env.versions()) == 1


# ---------------------------------------------------------------- what a workspace pays


async def test_two_businesses_move_to_a_raise_at_their_own_renewal() -> None:
    """Shree Dental renews on the 12th, Cafe Mocha on the 25th; Growth goes from Rs 2 to Rs 3 a
    question from 5 Nov 00:00 IST (announced 5 Oct). Nobody pays Rs 3 before their own renewal."""
    from callobuzz_rag.billing.pricing import effective_pricing
    from callobuzz_rag.db.models.billing_account import BillingAccount

    env = Env(workspaces=2)
    shree, cafe = env.db.all(Tenant)
    env.db.add(BillingAccount(tenant_id=shree.id, currency="INR", ledger_seq=3, billing_day=12))
    env.db.add(BillingAccount(tenant_id=cafe.id, currency="INR", ledger_seq=3, billing_day=25))
    accounts = {a.tenant_id: a for a in env.db.all(BillingAccount)}
    env.v1.is_current = False
    env.v1.effective_at = None
    v2 = stored_pricing(
        env.plan,
        version=2,
        subscription_fee_minor=129_900,
        usage_mode="per_question",
        per_question_minor=300,
    )
    v2.effective_at = ist_midnight(date(2026, 11, 5))
    env.db.add(v2)

    async def paying(tenant: Any, today: date, next_charge: date) -> int:
        accounts[tenant.id].next_charge_on = next_charge
        eff = await effective_pricing(
            env.db.session(),
            tenant.id,
            now=datetime(today.year, today.month, today.day, 6, tzinfo=UTC),
        )
        return eff.fields.per_question_minor

    assert await paying(shree, date(2026, 11, 6), date(2026, 11, 12)) == 200  # began 12 Oct
    assert await paying(cafe, date(2026, 11, 6), date(2026, 11, 25)) == 200  # began 25 Oct
    assert await paying(shree, date(2026, 11, 13), date(2026, 12, 12)) == 300  # began 12 Nov
    assert await paying(cafe, date(2026, 11, 13), date(2026, 11, 25)) == 200  # still 25 Oct
    assert await paying(cafe, date(2026, 11, 26), date(2026, 12, 25)) == 300  # began 25 Nov


async def test_each_period_is_charged_at_the_price_in_force_when_it_started() -> None:
    from callobuzz_rag.billing.pricing import effective_pricing

    env = Env(workspaces=1)
    env.v1.is_current = False
    v2 = stored_pricing(
        env.plan,
        version=2,
        subscription_fee_minor=129_900,
        usage_mode="per_question",
        per_question_minor=300,
    )
    v2.effective_at = ist_midnight(date(2026, 11, 5))
    env.db.add(v2)
    tenant = env.db.all(Tenant)[0]
    oct_charge = await effective_pricing(
        env.db.session(), tenant.id, at=ist_midnight(date(2026, 10, 12))
    )
    nov_charge = await effective_pricing(
        env.db.session(), tenant.id, at=ist_midnight(date(2026, 11, 12))
    )
    assert oct_charge.fields.subscription_fee_minor == 99_900
    assert nov_charge.fields.subscription_fee_minor == 129_900
    assert oct_charge.pricing_id == env.v1.id and nov_charge.pricing_id == v2.id


def test_a_yearly_workspace_is_judged_by_the_period_it_paid_not_the_new_one() -> None:
    """A change from yearly to monthly that applies in November must not reach a workspace in
    the middle of the yearly period it already paid."""
    from types import SimpleNamespace

    account = SimpleNamespace(next_charge_on=date(2027, 3, 1), billing_day=1)
    now = datetime(2026, 11, 10, 6, tzinfo=UTC)
    start = period_start(account, now, billing_period="monthly", alternatives=["monthly", "yearly"])  # type: ignore[arg-type]
    assert start == ist_midnight(date(2026, 3, 1))
    assert start <= now
    assert ist_midnight(date(2026, 11, 5)) > start  # the 5 Nov price change waits for March


async def test_the_beat_skips_plans_whose_old_versions_are_already_replaced() -> None:
    """An activated version that was later replaced stays `effective_at <= now` for ever; it
    must not make every hourly run lock the plan for nothing."""
    from unittest.mock import AsyncMock, patch

    env = Env()
    await env.save(fields={"subscription_fee_minor": 129_900}, apply="scheduled")
    after = earliest_effective(NOW, 30) + timedelta(hours=1)
    assert await activate_due(env.db.factory(), after) == 1
    with patch("callobuzz_rag.billing.notice._activate_plan", AsyncMock(return_value=False)) as hit:
        assert await activate_due(env.db.factory(), after + timedelta(days=5)) == 0
    hit.assert_not_awaited()
