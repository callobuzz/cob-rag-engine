"""Fair-use API (PIP3 task 7): the deployer's list, view and overrides, the plan fields, the
engine rails, the owner's read-only view and the Home heads-up. Through the real app and
middleware on the in-memory database of `accounts_fakes.py`; the usage readers are patched
(a sum over `usage_daily` is PostgreSQL's job, see `tests/integration/test_fair_use_flow.py`)."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.db.models.pipeline_profile import PipelineProfile, TenantPipelineSettings
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.fairuse import views
from callobuzz_rag.fairuse.usage import FairUseUsage
from callobuzz_rag.tiers.seeds import SEEDS
from tests.unit.test_auth_routes import ADMIN, World, req
from tests.unit.test_plans_admin import app_for

BASE = "/api/v1/admin/fair-use"


@pytest.fixture
def world() -> World:
    return World()


def seed_profiles(world: World) -> dict[str, PipelineProfile]:
    return {
        key: world.db.add(
            PipelineProfile(
                name=s["name"],
                label=s["label"],
                description=s["description"],
                builtin_key=key,
                is_default=s["is_default"],
                config=dict(s["config"]),
                updated_by="system",
            )
        )
        for key, s in SEEDS.items()
    }


@contextmanager
def usage(**fields: int) -> Iterator[AsyncMock]:
    reader = AsyncMock(return_value=FairUseUsage(**fields))
    with patch.object(views, "read_usage", reader):
        yield reader


async def admin(app: Any, method: str, path: str = "", **kw: Any) -> Any:
    return await req(app, method, f"{BASE}{path}", key=ADMIN, **kw)


def limit_plan(world: World, **limits: int) -> None:
    world.db.default_plan.fair_use_limits = dict(limits)


# ------------------------------------------------------------------------------- the list


async def test_list_shows_state_and_the_worst_limit_of_every_business(
    world: World,
) -> None:
    seed_profiles(world)
    limit_plan(world, questions_per_hour=30)
    world.db.add(TenantPipelineSettings(tenant_id=world.pune.id, state="throttled"))
    with app_for(world) as app, usage(questions_per_hour=31):
        res = await admin(app, "GET")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2
    first, second = body["items"]
    assert (first["name"], first["state"]) == ("Pune Agency", "throttled")  # the worrying first
    assert first["worst"]["metric"] == "questions_per_hour" and first["worst"]["level"] == "soft"
    assert first["worst"]["limit"] == 30 and first["worst"]["used"] == 31
    # FU-6: a business whose stored state is ok but whose live usage is past a limit is not
    # shown as fine: the limit is named and the state is lifted to near
    assert (second["name"], second["state"]) == ("Shree Labs", "near")
    assert second["worst"]["metric"] == "questions_per_hour"
    assert first["plan"] == "Standard" and first["profile_label"] == "Standard"


async def test_list_filters_by_state_and_name_and_pages(world: World) -> None:
    seed_profiles(world)
    world.db.add(TenantPipelineSettings(tenant_id=world.shree.id, state="stopped"))
    with app_for(world) as app, usage():
        stopped = await admin(app, "GET", "?state=stopped")
        named = await admin(app, "GET", "?q=pune")
        paged = await admin(app, "GET", "?limit=1&offset=1")
        bad = await admin(app, "GET", "?state=exploded")
    assert [i["name"] for i in stopped.json()["items"]] == ["Shree Labs"]
    assert [i["name"] for i in named.json()["items"]] == ["Pune Agency"]
    assert paged.json()["total"] == 2 and [i["name"] for i in paged.json()["items"]] == [
        "Pune Agency"
    ]
    assert bad.status_code == 422


async def test_the_system_workspace_is_not_listed(world: World) -> None:
    seed_profiles(world)
    world.db.add(Tenant(name="COB", status="active", api_key_hash="", is_system=True))
    with app_for(world) as app, usage():
        res = await admin(app, "GET")
    assert [i["name"] for i in res.json()["items"]] == ["Pune Agency", "Shree Labs"]


# --------------------------------------------------------------------------- one business


async def test_detail_is_the_plan_with_the_businesss_own_values_on_top(world: World) -> None:
    seed_profiles(world)
    limit_plan(world, questions_per_hour=30, questions_per_day=300)
    world.db.add(
        TenantPipelineSettings(
            tenant_id=world.shree.id,
            fair_use_overrides={"questions_per_hour": 100},
            policy_overrides={"hard_limit_pct": 200},
            state="near",
            state_since=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
        )
    )
    with app_for(world) as app, usage(questions_per_hour=85, questions_per_day=10):
        res = await admin(app, "GET", f"/tenants/{world.shree.id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert (
        body["limits"]["questions_per_hour"] == 100 and body["limits"]["questions_per_day"] == 300
    )
    assert body["policy"]["hard_limit_pct"] == 200
    assert body["overridden"] == ["hard_limit_pct", "questions_per_hour"]
    assert (body["state"], body["name"], body["plan"]) == ("near", "Shree Labs", "Standard")
    rows = {u["metric"]: u for u in body["usage"]}
    assert rows["questions_per_hour"]["pct"] == 85 and rows["questions_per_hour"]["level"] == "near"
    assert rows["questions_per_day"]["level"] == "ok"
    assert body["decisions_today"] == {"downgraded": 0, "paused": 0, "busy": 0}
    assert body["state_since"].startswith("2026-10-05T09:00")


async def test_an_unknown_business_is_404(world: World) -> None:
    with app_for(world) as app:
        res = await admin(app, "GET", f"/tenants/{uuid.uuid4()}")
    assert (res.status_code, res.json()["code"]) == (404, "tenant_not_found")


# ----------------------------------------------------------------------------- overrides


async def test_put_sets_a_limit_unlimited_and_clears_back_to_the_plan(world: World) -> None:
    seed_profiles(world)
    limit_plan(world, questions_per_hour=30, questions_per_day=300)
    path = f"/tenants/{world.shree.id}"
    with app_for(world) as app, usage():
        res = await admin(
            app,
            "PUT",
            path,
            json={"limits": {"questions_per_hour": 100, "questions_per_day": None}},
        )
        assert res.status_code == 200, res.text
        limits = res.json()["limits"]
        assert limits["questions_per_hour"] == 100 and limits["questions_per_day"] is None
        assert res.json()["overridden"] == ["questions_per_day", "questions_per_hour"]
        row = next(r for r in world.db.all(TenantPipelineSettings) if r.tenant_id == world.shree.id)
        assert row.fair_use_overrides == {"questions_per_hour": 100, "questions_per_day": None}
        cleared = await admin(app, "PUT", path, json={"clear": ["questions_per_day"]})
    assert cleared.json()["limits"]["questions_per_day"] == 300  # follows the plan again
    assert cleared.json()["overridden"] == ["questions_per_hour"]
    (created, *_) = world.audits("fair_use.overrides_updated")
    assert created.details == {
        "changed": ["questions_per_day", "questions_per_hour"],
        "cleared": [],
    }
    assert created.target_id == str(world.shree.id) and created.actor_kind == "admin_key"
    assert world.audits("fair_use.overrides_updated")[1].details == {
        "changed": [],
        "cleared": ["questions_per_day"],
    }


async def test_put_keeps_the_businesss_profile_and_state(world: World) -> None:
    profiles = seed_profiles(world)
    world.db.add(
        TenantPipelineSettings(
            tenant_id=world.shree.id, profile_id=profiles["premium"].id, state="near"
        )
    )
    with app_for(world) as app, usage():
        await admin(app, "PUT", f"/tenants/{world.shree.id}", json={"limits": {"storage_mb": 50}})
    row = next(r for r in world.db.all(TenantPipelineSettings))
    assert row.profile_id == profiles["premium"].id and row.state == "near"


async def test_a_change_that_changes_nothing_writes_nothing(world: World) -> None:
    seed_profiles(world)
    path = f"/tenants/{world.shree.id}"
    with app_for(world) as app, usage():
        await admin(app, "PUT", path, json={"limits": {"storage_mb": 50}})
        again = await admin(app, "PUT", path, json={"limits": {"storage_mb": 50}})
        empty = await admin(app, "PUT", f"/tenants/{world.pune.id}", json={})
    assert again.status_code == 200 and empty.status_code == 200
    assert len(world.audits("fair_use.overrides_updated")) == 1
    assert not [r for r in world.db.all(TenantPipelineSettings) if r.tenant_id == world.pune.id]


@pytest.mark.parametrize(
    ("body", "words"),
    [
        ({"limits": {"nonsense": 5}}, "nonsense"),
        ({"limits": {"questions_per_hour": 0}}, "questions_per_hour"),
        ({"policy": {"hard_limit_pct": 99}}, "hard_limit_pct"),
        ({"policy": {"alert_thresholds_pct": [80, 80]}}, "different"),
        ({"policy": {"on_fair_use_exceeded": "explode"}}, "on_fair_use_exceeded"),
        ({"clear": ["nonsense"]}, "nonsense"),
        ({"policy": {"downgrade_profile_id": str(uuid.uuid4())}}, "does not exist"),
        ({"policy": {"downgrade_profile_id": "not-a-uuid"}}, "downgrade_profile_id"),
    ],
)
async def test_a_setting_that_does_not_fit_names_the_rule(
    world: World, body: dict, words: str
) -> None:
    seed_profiles(world)
    with app_for(world) as app, usage():
        res = await admin(app, "PUT", f"/tenants/{world.shree.id}", json=body)
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "invalid_fair_use" and words in res.json()["detail"]
    assert world.audits("fair_use.overrides_updated") == []


async def test_downgrading_to_the_profile_it_already_answers_with_is_refused(world: World) -> None:
    profiles = seed_profiles(world)
    path = f"/tenants/{world.shree.id}"
    with app_for(world) as app, usage():
        same = await admin(
            app,
            "PUT",
            path,
            json={"policy": {"downgrade_profile_id": str(profiles["standard"].id)}},
        )
        other = await admin(
            app, "PUT", path, json={"policy": {"downgrade_profile_id": str(profiles["free"].id)}}
        )
    assert same.status_code == 422 and "already answers with" in same.json()["detail"]
    assert other.status_code == 200
    assert other.json()["policy"]["downgrade_profile_id"] == str(profiles["free"].id)


async def test_a_downgrade_target_cannot_be_deleted(world: World) -> None:
    profiles = seed_profiles(world)
    spare = world.db.add(PipelineProfile(name="Spare", label="Spare", config={}, updated_by="x"))
    with app_for(world) as app, usage():
        await admin(
            app,
            "PUT",
            f"/tenants/{world.shree.id}",
            json={"policy": {"downgrade_profile_id": str(spare.id)}},
        )
        res = await req(app, "DELETE", f"/api/v1/admin/pipelines/{spare.id}", key=ADMIN)
    assert res.status_code == 409, res.text
    body = res.json()
    assert body["code"] == "profile_in_use" and body["downgrade_targets"] == 1
    assert body["plans"] == 0 and body["tenants"] == 0
    assert profiles  # the seeds are untouched


# -------------------------------------------------------------------------------- plans


async def test_a_plan_takes_limits_and_policy_and_audits_each_field(world: World) -> None:
    profiles = seed_profiles(world)
    with app_for(world) as app:
        made = await req(
            app,
            "POST",
            "/api/v1/admin/plans",
            key=ADMIN,
            json={
                "name": "Pune Starter",
                "fair_use_limits": {"questions_per_hour": 30, "fetches_per_day": 50},
                "fair_use_policy": {"on_fair_use_exceeded": "hard_stop"},
            },
        )
        assert made.status_code == 201, made.text
        pid = made.json()["id"]
        assert made.json()["fair_use_limits"]["questions_per_hour"] == 30
        assert made.json()["fair_use_policy"]["on_fair_use_exceeded"] == "hard_stop"
        plan = next(p for p in world.db.all(Plan) if p.name == "Pune Starter")
        assert plan.fair_use_limits == {"questions_per_hour": 30, "fetches_per_day": 50}
        assert plan.fair_use_policy == {"on_fair_use_exceeded": "hard_stop"}  # only what differs
        patched = await req(
            app,
            "PATCH",
            f"/api/v1/admin/plans/{pid}",
            key=ADMIN,
            json={
                "fair_use_limits": {"questions_per_hour": 60, "uploads_per_day": 5},
                "fair_use_policy": {"downgrade_profile_id": str(profiles["free"].id)},
            },
        )
    assert patched.status_code == 200, patched.text
    (audit,) = [a for a in world.audits("plan.updated") if a.details["name"] == "Pune Starter"]
    changes = audit.details["changes"]
    assert changes["fair_use_limits.questions_per_hour"] == {"from": 30, "to": 60}
    assert changes["fair_use_limits.uploads_per_day"] == {"from": None, "to": 5}
    assert changes["fair_use_limits.fetches_per_day"] == {"from": 50, "to": None}
    assert changes["fair_use_policy.on_fair_use_exceeded"] == {"from": "hard_stop", "to": None}
    assert changes["fair_use_policy.downgrade_profile_id"] == {
        "from": None,
        "to": str(profiles["free"].id),
    }


async def test_a_plan_cannot_downgrade_to_its_own_profile_or_to_nothing(world: World) -> None:
    profiles = seed_profiles(world)
    with app_for(world) as app:
        own = await req(
            app,
            "POST",
            "/api/v1/admin/plans",
            key=ADMIN,
            json={
                "name": "Loop",
                "pipeline_profile_id": str(profiles["premium"].id),
                "fair_use_policy": {"downgrade_profile_id": str(profiles["premium"].id)},
            },
        )
        default_own = await req(
            app,
            "POST",
            "/api/v1/admin/plans",
            key=ADMIN,
            json={
                "name": "Loop2",
                "fair_use_policy": {"downgrade_profile_id": str(profiles["standard"].id)},
            },
        )
        missing = await req(
            app,
            "POST",
            "/api/v1/admin/plans",
            key=ADMIN,
            json={"name": "Gone", "fair_use_policy": {"downgrade_profile_id": str(uuid.uuid4())}},
        )
        bad = await req(
            app,
            "POST",
            "/api/v1/admin/plans",
            key=ADMIN,
            json={"name": "Odd", "fair_use_policy": {"hard_limit_pct": 5}},
        )
    for res in (own, default_own, missing):
        assert res.status_code == 422 and res.json()["code"] == "invalid_fair_use", res.text
    assert "own profile" in own.json()["detail"] and "does not exist" in missing.json()["detail"]
    assert bad.status_code == 422  # the request model refuses it before the rules run


async def test_changing_a_plans_profile_rechecks_its_downgrade_target(world: World) -> None:
    profiles = seed_profiles(world)
    with app_for(world) as app:
        made = await req(
            app,
            "POST",
            "/api/v1/admin/plans",
            key=ADMIN,
            json={
                "name": "Grow",
                "fair_use_policy": {"downgrade_profile_id": str(profiles["free"].id)},
            },
        )
        res = await req(
            app,
            "PATCH",
            f"/api/v1/admin/plans/{made.json()['id']}",
            key=ADMIN,
            json={"pipeline_profile_id": str(profiles["free"].id)},
        )
    assert res.status_code == 422 and res.json()["code"] == "invalid_fair_use"


async def test_a_stored_plan_with_a_stale_key_still_lists(world: World) -> None:
    seed_profiles(world)
    world.db.default_plan.fair_use_limits = {"questions_per_hour": 30, "retired_limit": 1}
    with app_for(world) as app:
        res = await req(app, "GET", "/api/v1/admin/plans", key=ADMIN)
    assert res.status_code == 200
    assert res.json()["items"][0]["fair_use_limits"]["questions_per_hour"] == 30


# ------------------------------------------------------------------------------- settings


async def test_engine_rails_are_validated_and_audited(world: World) -> None:
    with app_for(world) as app:
        res = await req(
            app,
            "PATCH",
            "/api/v1/admin/settings",
            key=ADMIN,
            json={
                "pipelines": {
                    "engine_ai_spend_per_day_usd_micro": 50_000_000,
                    "crawl_concurrency": 2,
                }
            },
        )
        assert res.status_code == 200, res.text
        rails = res.json()["pipelines"]
        assert rails == {
            "engine_ai_spend_per_day_usd_micro": 50_000_000,
            "engine_spend_alert_pct": 80,
            "crawl_concurrency": 2,
        }
        bad = await req(
            app,
            "PATCH",
            "/api/v1/admin/settings",
            key=ADMIN,
            json={"pipelines": {"engine_spend_alert_pct": 0}},
        )
        worse = await req(
            app, "PATCH", "/api/v1/admin/settings", key=ADMIN, json={"pipelines": {"nope": 1}}
        )
        reset = await req(
            app,
            "PATCH",
            "/api/v1/admin/settings",
            key=ADMIN,
            json={"pipelines": {"crawl_concurrency": None}},
        )
    assert bad.status_code == 422 and worse.status_code == 422
    assert reset.json()["pipelines"]["crawl_concurrency"] is None
    first = world.audits("settings.updated")[0]
    assert first.details["changed"]["pipelines.engine_ai_spend_per_day_usd_micro"] == {
        "from": None,
        "to": 50_000_000,
    }
    assert first.details["changed"]["pipelines.crawl_concurrency"] == {"from": None, "to": 2}


# ---------------------------------------------------------------------------- the owner


async def test_the_owner_sees_the_share_of_ai_usage_and_never_the_amount() -> None:
    from callobuzz_rag.fairuse.limits import EffectiveFairUse, FairUseLimits, FairUsePolicy
    from tests.unit.scope_harness import call, scoped_app

    eff = EffectiveFairUse(
        FairUseLimits(questions_per_hour=30, ai_spend_per_day_usd_micro=5_000_000),
        FairUsePolicy(),
        False,
        frozenset(),
        state="near",
    )
    with (
        scoped_app() as (app, _),
        patch("callobuzz_rag.api.routes.fair_use.effective_fair_use", AsyncMock(return_value=eff)),
        usage(questions_per_hour=25, spend_day_usd_micro=4_000_000),
        patch(
            "callobuzz_rag.tiers.profiles.plan_profile",
            AsyncMock(return_value=_profile("Standard")),
        ),
        patch(
            "callobuzz_rag.tiers.profiles.load_profile",
            AsyncMock(return_value=type("P", (), {"label": "Economy", "available": True})()),
        ),
    ):
        owner = await call(app, "owner_a", "GET", "/api/v1/fair-use")
        member = await call(app, "member_a", "GET", "/api/v1/fair-use")
        tenant_key = await call(app, "tk_a", "GET", "/api/v1/fair-use")
    for res in (owner, member, tenant_key):  # members see the same view as owners
        assert res.status_code == 200, res.text
    body = owner.json()
    assert body["state"] == "near" and body["answer_speed"]
    assert body["billing_block"] == "downgrade_profile"  # the plan's choice for an empty balance
    metrics = {m["metric"]: m for m in body["metrics"]}
    assert (metrics["questions_per_hour"]["used"], metrics["questions_per_hour"]["limit"]) == (
        25,
        30,
    )
    spend = metrics["ai_spend_per_day"]
    assert (spend["used"], spend["limit"], spend["pct"]) == (None, None, 80)
    assert spend["label"] == "AI usage today"
    assert "4000000" not in owner.text and "5000000" not in owner.text and "$" not in owner.text
    assert member.json() == owner.json()


@pytest.mark.parametrize(
    ("key", "status", "code"),
    [
        ("ck_a", 403, "scope_forbidden"),
        ("admin", 403, "no_workspace"),
        ("sa", 403, "no_workspace"),
        (None, 401, None),
    ],
)
async def test_no_workspace_no_view(key: str | None, status: int, code: str | None) -> None:
    from tests.unit.scope_harness import call, scoped_app

    with scoped_app() as (app, _):
        res = await call(app, key, "GET", "/api/v1/fair-use")
    assert res.status_code == status
    if code:
        assert res.json()["code"] == code


async def test_a_workspace_only_ever_reads_its_own_view() -> None:
    from callobuzz_rag.fairuse.limits import EffectiveFairUse, FairUseLimits, FairUsePolicy
    from tests.unit.scope_harness import call, scoped_app

    asked: list[uuid.UUID] = []

    async def effective(session: Any, tenant_id: uuid.UUID) -> Any:
        asked.append(tenant_id)
        return EffectiveFairUse(FairUseLimits(), FairUsePolicy(), False, frozenset())

    with (
        scoped_app() as (app, world),
        patch("callobuzz_rag.api.routes.fair_use.effective_fair_use", effective),
        patch(
            "callobuzz_rag.tiers.profiles.plan_profile",
            AsyncMock(return_value=_profile("Standard")),
        ),
    ):
        await call(app, "tk_b", "GET", "/api/v1/fair-use")
        await call(app, "owner_a", "GET", "/api/v1/fair-use")
    assert asked == [world.tenant_b.id, world.tenant_a.id]


async def test_when_throttled_the_owner_sees_the_slower_speed_label(world: World) -> None:
    from callobuzz_rag.fairuse.limits import EffectiveFairUse, FairUseLimits, FairUsePolicy
    from tests.unit.scope_harness import call, scoped_app

    eff = EffectiveFairUse(FairUseLimits(), FairUsePolicy(), False, frozenset(), state="throttled")
    free = SEEDS["free"]
    with (
        scoped_app() as (app, _),
        patch("callobuzz_rag.api.routes.fair_use.effective_fair_use", AsyncMock(return_value=eff)),
        patch(
            "callobuzz_rag.tiers.profiles.plan_profile",
            AsyncMock(return_value=_profile("Standard")),
        ),
        patch(
            "callobuzz_rag.tiers.profiles.load_profile",
            AsyncMock(return_value=type("P", (), {"label": free["label"]})()),
        ),
    ):
        res = await call(app, "owner_a", "GET", "/api/v1/fair-use")
    assert res.status_code == 200 and res.json()["answer_speed"] == "Economy"


@pytest.mark.parametrize(
    ("policy", "available", "expected"),
    [
        ("downgrade_profile", True, "downgrade_profile"),
        ("downgrade_profile", False, "pause_handoff"),  # D14: an unavailable target pauses
        ("pause_handoff", True, "pause_handoff"),
        ("allow", True, "allow"),
    ],
)
async def test_the_owner_is_told_what_an_empty_balance_really_does(
    policy: str, available: bool, expected: str
) -> None:
    from callobuzz_rag.fairuse.limits import EffectiveFairUse, FairUseLimits, FairUsePolicy
    from tests.unit.scope_harness import call, scoped_app

    eff = EffectiveFairUse(
        FairUseLimits(),
        FairUsePolicy(on_billing_block=policy),
        False,
        frozenset(),  # type: ignore[arg-type]
    )
    target = type("P", (), {"label": "Economy", "available": available})()
    with (
        scoped_app() as (app, _),
        patch("callobuzz_rag.api.routes.fair_use.effective_fair_use", AsyncMock(return_value=eff)),
        patch(
            "callobuzz_rag.tiers.profiles.plan_profile",
            AsyncMock(return_value=_profile("Standard")),
        ),
        patch("callobuzz_rag.tiers.profiles.load_profile", AsyncMock(return_value=target)),
    ):
        res = await call(app, "owner_a", "GET", "/api/v1/fair-use")
    assert res.status_code == 200, res.text
    assert res.json()["billing_block"] == expected


# ------------------------------------------------------------------------------- home


async def test_home_shows_a_heads_up_only_when_a_limit_is_close() -> None:
    from tests.unit.scope_harness import call, scoped_app

    async def tile(session: Any, app: Any, tenant_id: Any) -> Any:
        return ("near", "questions_per_hour", 85, datetime(2026, 10, 5, 11, 0, tzinfo=UTC))

    with (
        scoped_app() as (app, _),
        patch("callobuzz_rag.api.routes.home.home_tile", tile),
        patch("callobuzz_rag.api.routes.home.build_home", AsyncMock(return_value=_home())),
    ):
        res = await call(app, "owner_a", "GET", "/api/v1/home")
    assert res.status_code == 200, res.text
    assert res.json()["fair_use"] == {
        "state": "near",
        "worst_metric": "questions_per_hour",
        "pct": 85,
        "resets_at": "2026-10-05T11:00:00Z",
    }

    with (
        scoped_app() as (app, _),
        patch("callobuzz_rag.api.routes.home.home_tile", AsyncMock(return_value=None)),
        patch("callobuzz_rag.api.routes.home.build_home", AsyncMock(return_value=_home())),
    ):
        quiet = await call(app, "owner_a", "GET", "/api/v1/home")
    assert quiet.json()["fair_use"] is None


def _profile(label: str) -> Any:
    from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig

    return EffectiveProfile(uuid.uuid4(), label, label, ProfileConfig(), "plan", True)


def _home() -> Any:
    from callobuzz_rag.api.schemas.home import HomeAssistant, HomeOut, HomeToday, HomeWebsite

    return HomeOut(
        kb_id=None,
        kb_name=None,
        assistant=HomeAssistant(
            enabled=False, published=False, name=None, avatar_url=None, business_name=None
        ),
        website=HomeWebsite(sites=[], learning=True),
        today=HomeToday(
            date="2026-10-05", timezone="Asia/Kolkata", conversations=0, leads=0, handoffs_open=0
        ),
    )


async def test_the_home_tile_is_quiet_when_nothing_is_close_and_never_fails_the_page(
    world: World,
) -> None:
    from callobuzz_rag.fairuse.limits import EffectiveFairUse, FairUseLimits, FairUsePolicy

    eff = EffectiveFairUse(
        FairUseLimits(questions_per_hour=30), FairUsePolicy(), False, frozenset()
    )
    noon = datetime(2026, 10, 5, 10, 17, tzinfo=UTC)
    app = type("A", (), {"state": type("S", (), {})()})()
    with (
        patch.object(views, "effective_fair_use", AsyncMock(return_value=eff)),
        usage(questions_per_hour=3),
    ):
        assert await views.home_tile(AsyncMock(), app, uuid.uuid4(), now=noon) is None
    with (
        patch.object(views, "effective_fair_use", AsyncMock(return_value=eff)),
        usage(questions_per_hour=26),
    ):
        assert await views.home_tile(AsyncMock(), app, uuid.uuid4(), now=noon) == (
            "near",  # live usage lifts a stored ok: the tile and the state say the same thing
            "questions_per_hour",
            86,
            datetime(2026, 10, 5, 11, 0, tzinfo=UTC),
        )
    with patch.object(views, "effective_fair_use", AsyncMock(side_effect=ConnectionError("down"))):
        assert await views.home_tile(AsyncMock(), app, uuid.uuid4()) is None


# ------------------------------------------------------------------ review fixes (PIP4)


async def test_an_exempt_business_says_so_in_the_list_and_the_detail(world: World) -> None:
    seed_profiles(world)
    world.db.default_plan.is_internal = True
    with app_for(world) as app, usage():
        listed = await admin(app, "GET")
        detail = await admin(app, "GET", f"/tenants/{world.shree.id}")
    assert listed.status_code == 200, listed.text
    assert all(i["exempt"] is True for i in listed.json()["items"])
    assert detail.json()["exempt"] is True


async def test_a_limited_business_is_not_exempt(world: World) -> None:
    seed_profiles(world)
    with app_for(world) as app, usage():
        listed = await admin(app, "GET")
        detail = await admin(app, "GET", f"/tenants/{world.shree.id}")
    assert all(i["exempt"] is False for i in listed.json()["items"])
    assert detail.json()["exempt"] is False


@pytest.mark.parametrize(
    ("route", "available", "expected"),
    [("local", False, "local_unavailable"), ("local", True, None), ("engine", False, None)],
)
async def test_the_owner_is_told_when_the_local_model_is_not_ready(
    route: str, available: bool, expected: str | None
) -> None:
    from callobuzz_rag.fairuse.limits import EffectiveFairUse, FairUseLimits, FairUsePolicy
    from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig
    from tests.unit.scope_harness import call, scoped_app

    eff = EffectiveFairUse(FairUseLimits(), FairUsePolicy(), False, frozenset())
    profile = EffectiveProfile(
        uuid.uuid4(),
        "Free",
        "Economy",
        ProfileConfig(answer_route=route, overrides_kb_settings=True),  # type: ignore[arg-type]
        "plan",
        available,
    )
    with (
        scoped_app() as (app, _),
        patch("callobuzz_rag.api.routes.fair_use.effective_fair_use", AsyncMock(return_value=eff)),
        patch("callobuzz_rag.tiers.profiles.plan_profile", AsyncMock(return_value=profile)),
        patch("callobuzz_rag.tiers.profiles.load_profile", AsyncMock(return_value=profile)),
    ):
        res = await call(app, "owner_a", "GET", "/api/v1/fair-use")
    assert res.status_code == 200, res.text
    assert res.json()["answers_paused"] == expected


async def test_list_can_ask_for_exactly_the_businesses_a_page_shows(world: World) -> None:
    seed_profiles(world)
    with app_for(world) as app, usage():
        res = await admin(app, "GET", f"?ids={world.shree.id}")
    assert [i["name"] for i in res.json()["items"]] == ["Shree Labs"]
    assert res.json()["total"] == 1


async def test_a_business_blocked_on_uploads_is_not_shown_as_fine(world: World) -> None:
    """FU-6: only answers move the stored state, so a business that used its uploads showed as
    `ok` with no limit named in the deployer's list, the detail and the owner's banner."""
    seed_profiles(world)
    limit_plan(world, uploads_per_day=5)
    with app_for(world) as app, usage(uploads_per_day=5):
        res = await admin(app, "GET")
        detail = await admin(app, "GET", f"/tenants/{world.shree.id}")
    rows = {i["name"]: i for i in res.json()["items"]}
    assert rows["Shree Labs"]["state"] == "near"
    assert rows["Shree Labs"]["worst"]["metric"] == "uploads_per_day"
    assert rows["Shree Labs"]["worst"]["level"] == "soft"
    assert detail.json()["state"] == "near"


async def test_a_business_with_room_everywhere_stays_ok(world: World) -> None:
    seed_profiles(world)
    limit_plan(world, uploads_per_day=5)
    with app_for(world) as app, usage(uploads_per_day=1):
        res = await admin(app, "GET")
    assert {(i["state"], i["worst"]) for i in res.json()["items"]} == {("ok", None)}


def test_the_state_a_screen_shows_is_lifted_by_live_usage_only_from_ok() -> None:
    from callobuzz_rag.fairuse.views import MetricRow, live_state

    close = MetricRow("uploads_per_day", "uploads today", 5, 5, 100, "soft", None)
    fine = MetricRow("uploads_per_day", "uploads today", 1, 5, 20, "ok", None)
    assert live_state("ok", [close]) == "near"
    assert live_state("ok", [fine]) == "ok"
    assert live_state("ok", []) == "ok"
    for stored in ("near", "throttled", "stopped"):  # a decision's state is never lowered
        assert live_state(stored, [close]) == stored
        assert live_state(stored, [fine]) == stored
