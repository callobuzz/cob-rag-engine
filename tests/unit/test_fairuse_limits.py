"""Fair-use limits and policy (PIP3 task 2): what a plan stores, how a business's own values lay
over it, and how the effective values are found. The lookup runs on the in-memory database of
`accounts_fakes.py` (outer join included)."""

import uuid

import pytest
from pydantic import ValidationError

from callobuzz_rag.db.models.pipeline_profile import TenantPipelineSettings
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.fairuse.limits import (
    FairUseLimits,
    FairUsePolicy,
    effective_fair_use,
    merge_limits,
    merge_policy,
    normalize_limits,
    normalize_policy,
    parse_limits,
    parse_policy,
)
from tests.unit.accounts_fakes import FakeDB, FakeSession


def test_override_rules() -> None:
    plan = FairUseLimits(questions_per_hour=30, questions_per_day=300)
    eff = merge_limits(plan, {"questions_per_hour": 100, "questions_per_day": None})
    assert (eff.questions_per_hour, eff.questions_per_day) == (100, None)  # null = unlimited
    assert merge_limits(plan, {}).questions_per_hour == 30  # absent = follow the plan
    assert merge_limits(plan, {"nonsense": 1}).questions_per_hour == 30  # a stale key is ignored


@pytest.mark.parametrize(
    "bad",
    [
        {"alert_thresholds_pct": [80, 80]},
        {"alert_thresholds_pct": [0]},
        {"alert_thresholds_pct": [101]},
        {"hard_limit_pct": 99},
        {"on_fair_use_exceeded": "explode"},
        {"on_billing_block": "explode"},
        {"unknown": 1},
    ],
)
def test_invalid_policy(bad: dict) -> None:
    with pytest.raises(ValidationError):
        FairUsePolicy.model_validate(bad)


def test_policy_defaults_are_the_spec_defaults() -> None:
    p = FairUsePolicy()
    assert (p.on_billing_block, p.on_fair_use_exceeded) == (
        "downgrade_profile",
        "throttle_then_stop",
    )
    assert (p.hard_limit_pct, p.alert_thresholds_pct, p.downgrade_profile_id) == (
        150,
        [80, 100],
        None,
    )


def test_thresholds_are_stored_sorted() -> None:
    assert FairUsePolicy(alert_thresholds_pct=[100, 50]).alert_thresholds_pct == [50, 100]


def test_a_stored_value_that_stopped_validating_is_dropped_not_fatal(caplog) -> None:
    limits = parse_limits({"questions_per_hour": 30, "questions_per_day": -4, "old_setting": 1})
    assert limits.questions_per_hour == 30 and limits.questions_per_day is None
    assert "dropping invalid keys" in caplog.text
    assert parse_policy({"hard_limit_pct": 3}).hard_limit_pct == 150
    assert parse_limits(None) == FairUseLimits() and parse_policy("nonsense") == FairUsePolicy()


def test_policy_merge_follows_the_plan_unless_set() -> None:
    plan = parse_policy({"on_fair_use_exceeded": "hard_stop", "hard_limit_pct": 200})
    merged = merge_policy(plan, {"hard_limit_pct": 120})
    assert (merged.on_fair_use_exceeded, merged.hard_limit_pct) == ("hard_stop", 120)


def test_plans_store_only_what_is_set() -> None:
    assert normalize_limits({"questions_per_hour": 30, "questions_per_day": None}) == {
        "questions_per_hour": 30
    }
    # D9: the plan's page total is `website_pages_max`, never stored a second time
    assert normalize_limits({"website_pages_total": 50, "fetches_per_day": 9}) == {
        "fetches_per_day": 9
    }
    assert normalize_policy({"hard_limit_pct": 150, "on_billing_block": "pause_handoff"}) == {
        "on_billing_block": "pause_handoff"
    }
    pid = uuid.uuid4()
    assert normalize_policy({"downgrade_profile_id": pid}) == {"downgrade_profile_id": str(pid)}


# ------------------------------------------------------------------ the lookup


def world() -> tuple[FakeDB, Tenant, Plan]:
    db = FakeDB()
    plan = db.default_plan
    plan.fair_use_limits = {"questions_per_hour": 30, "questions_per_day": 300}
    plan.fair_use_policy = {"on_fair_use_exceeded": "hard_stop"}
    plan.website_pages_max = 200
    tenant = db.add(Tenant(name="Shree Dental Clinic, Pune", status="active", api_key_hash=""))
    return db, tenant, plan


async def test_plan_values_without_an_override() -> None:
    db, tenant, _ = world()
    eff = await effective_fair_use(FakeSession(db), tenant.id)
    assert eff.limits.questions_per_hour == 30 and eff.limits.questions_per_day == 300
    assert eff.limits.website_pages_total == 200  # D9: the plan's website_pages_max
    assert eff.policy.on_fair_use_exceeded == "hard_stop"
    assert (eff.exempt, eff.overridden, eff.state) == (False, frozenset(), "ok")
    assert eff.tenant_name == "Shree Dental Clinic, Pune"


async def test_a_business_override_beats_the_plan_and_null_means_unlimited() -> None:
    db, tenant, _ = world()
    db.add(
        TenantPipelineSettings(
            tenant_id=tenant.id,
            fair_use_overrides={
                "questions_per_hour": 100,
                "questions_per_day": None,
                "website_pages_total": 500,
            },
            policy_overrides={"hard_limit_pct": 200},
            state="throttled",
        )
    )
    eff = await effective_fair_use(FakeSession(db), tenant.id)
    assert eff.limits.questions_per_hour == 100
    assert eff.limits.questions_per_day is None
    assert eff.limits.website_pages_total == 500
    assert eff.policy.hard_limit_pct == 200 and eff.policy.on_fair_use_exceeded == "hard_stop"
    assert eff.overridden == {
        "questions_per_hour",
        "questions_per_day",
        "website_pages_total",
        "hard_limit_pct",
    }
    assert eff.state == "throttled"


async def test_the_operator_the_system_workspace_and_the_internal_plan_are_exempt() -> None:
    db, tenant, _ = world()
    operator = db.add(Tenant(name="Callobuzz", status="active", api_key_hash="", is_operator=True))
    system = db.add(Tenant(name="COB", status="active", api_key_hash="", is_system=True))
    internal_plan = db.add(
        Plan(name="Internal", is_internal=True, is_active=True, is_default=False)
    )
    internal = db.add(
        Tenant(name="Own", status="active", api_key_hash="", plan_id=internal_plan.id)
    )
    session = FakeSession(db)
    assert not (await effective_fair_use(session, tenant.id)).exempt
    for t in (operator, system, internal):
        assert (await effective_fair_use(session, t.id)).exempt, t.name


async def test_an_unknown_business_is_exempt_so_the_route_reports_it() -> None:
    db, _, _ = world()
    assert (await effective_fair_use(FakeSession(db), uuid.uuid4())).exempt


async def test_only_limited_metrics_are_listed() -> None:
    db, tenant, _ = world()
    eff = await effective_fair_use(FakeSession(db), tenant.id)
    asked = ["questions_per_hour", "questions_per_minute", "ai_spend_per_day", "questions_per_day"]
    assert eff.limited(asked) == ["questions_per_hour", "questions_per_day"]
    assert eff.limited(["engine_ai_spend_per_day"]) == []  # the engine rail is not a business limit
