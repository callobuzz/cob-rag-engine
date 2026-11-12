"""Pipeline profiles API (PIP1): CRUD, the one default, clone, delete rules, a business's own
profile, the lanes view and the plan field. Through the real app and middleware on the in-memory
database of `accounts_fakes.py`."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from callobuzz_rag.db.models.pipeline_profile import PipelineProfile, TenantPipelineSettings
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.tiers.lanes import AnswerLanes, lane_configs
from callobuzz_rag.tiers.seeds import SEEDS
from tests.unit.accounts_fakes import FakeRedis
from tests.unit.test_auth_routes import ADMIN, World, req
from tests.unit.test_plans_admin import app_for

BASE = "/api/v1/admin/pipelines"


@pytest.fixture
def world() -> World:
    return World()


def seed(world: World) -> dict[str, PipelineProfile]:
    """What `phase16_001` seeds: three profiles, Standard the default."""
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
def pipelines_app(world: World) -> Iterator[Any]:
    with app_for(world) as app:
        yield app


async def api(app: Any, method: str, path: str = "", **kw: Any) -> Any:
    return await req(app, method, f"{BASE}{path}", key=ADMIN, **kw)


def names(res: Any) -> list[str]:
    return [p["name"] for p in res.json()["items"]]


# -------------------------------------------------------------------- the catalogue


async def test_list_shows_the_seeds_default_first_with_what_blank_means(world: World) -> None:
    seed(world)
    with pipelines_app(world) as app:
        res = await api(app, "GET")
    assert res.status_code == 200, res.text
    body = res.json()
    assert names(res) == ["Standard", "Free", "Premium"]  # the default first, then by name
    standard, free, premium = body["items"]
    assert body["default_id"] == standard["id"] and standard["is_default"] is True
    assert (free["label"], free["builtin_key"]) == ("Economy", "free")
    assert free["available"] is False and standard["available"] is True  # no local model yet
    assert free["config"]["answer_route"] == "local" and free["config"]["priority"] == "low"
    # what each blank field means right now
    resolved = standard["resolved"]
    assert resolved["answer_model"] and resolved["top_k"] >= 1
    assert resolved["priority"] == "normal" and resolved["followups"] is True
    assert premium["resolved"]["reranker"] == "llm"
    assert free["resolved"]["reranker"] == "off" and free["resolved"]["max_answer_tokens"] == 600
    # the world's one plan has no profile of its own, so it uses the default
    assert standard["plan_count"] == 1 and free["plan_count"] == 0 and premium["tenant_count"] == 0


async def test_create_validates_stores_only_what_differs_and_audits(world: World) -> None:
    seed(world)
    body = {
        "name": "Fast and cheap",
        "label": "Economy plus",
        "description": "For small clinics",
        "config": {"reranker": "off", "max_answer_tokens": 400, "priority": "low"},
    }
    with pipelines_app(world) as app:
        res = await api(app, "POST", json=body)
        one = await api(app, "GET", f"/{res.json()['id']}")
    assert res.status_code == 201, res.text
    made = res.json()
    assert made["is_default"] is False and made["builtin_key"] is None
    assert made["config"]["max_answer_tokens"] == 400 and made["config"]["followups"] is True
    assert one.json()["id"] == made["id"]
    stored = next(p for p in world.db.all(PipelineProfile) if p.name == "Fast and cheap")
    assert stored.config == {"reranker": "off", "max_answer_tokens": 400, "priority": "low"}
    assert stored.updated_by == "admin key"
    (audit,) = world.audits("pipeline.profile_created")
    assert audit.details == {"profile_id": made["id"], "name": "Fast and cheap"}
    assert audit.actor_kind == "admin_key"


@pytest.mark.parametrize(
    ("config", "words"),
    [
        ({"answer_route": "local"}, "local model"),
        ({"reranker": "api"}, "rerank_model"),
        ({"top_k": 5, "final_k": 8}, "final_k"),
        ({"language_models": {"xx": "m"}}, "language"),
        ({"priority": "urgent"}, "priority"),
        ({"nonsense": 1}, "extra field"),
    ],
)
async def test_a_profile_that_does_not_fit_together_names_the_rule(
    world: World, config: dict[str, Any], words: str
) -> None:
    seed(world)
    with pipelines_app(world) as app:
        res = await api(app, "POST", json={"name": "Bad", "label": "Bad", "config": config})
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "invalid_profile"
    assert words in res.json()["detail"]
    assert not world.audits("pipeline.profile_created")


async def test_request_bodies_reject_unknown_fields(world: World) -> None:
    seed(world)
    with pipelines_app(world) as app:
        res = await api(app, "POST", json={"name": "X", "label": "X", "surprise": 1})
    assert res.status_code == 422


async def test_names_are_unique(world: World) -> None:
    seed(world)
    with pipelines_app(world) as app:
        res = await api(app, "POST", json={"name": "Premium", "label": "P"})
    assert res.status_code == 409 and res.json()["code"] == "profile_name_taken"


async def test_unknown_profile_is_404(world: World) -> None:
    seed(world)
    with pipelines_app(world) as app:
        for method, path, kw in (
            ("GET", f"/{uuid.uuid4()}", {}),
            ("PATCH", f"/{uuid.uuid4()}", {"json": {"label": "x"}}),
            ("DELETE", f"/{uuid.uuid4()}", {}),
            ("POST", f"/{uuid.uuid4()}/default", {}),
            ("POST", f"/{uuid.uuid4()}/clone", {"json": {"name": "c"}}),
        ):
            res = await api(app, method, path, **kw)
            assert res.status_code == 404 and res.json()["code"] == "profile_not_found", path


# -------------------------------------------------------------------- editing


async def test_patch_changes_what_was_sent_and_config_replaces_the_whole(world: World) -> None:
    profiles = seed(world)
    free = profiles["free"]
    with pipelines_app(world) as app:
        res = await api(
            app,
            "PATCH",
            f"/{free.id}",
            json={"label": "Basic", "config": {"max_answer_tokens": 300}},
        )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["label"] == "Basic"
    assert out["config"]["max_answer_tokens"] == 300
    assert out["config"]["answer_route"] == "engine"  # the old config is gone, not merged
    assert out["name"] == "Free" and out["builtin_key"] == "free"
    (audit,) = world.audits("pipeline.profile_updated")
    assert audit.details["changed"] == ["label", "config"]


async def test_patch_that_changes_nothing_writes_no_audit(world: World) -> None:
    profiles = seed(world)
    with pipelines_app(world) as app:
        res = await api(
            app,
            "PATCH",
            f"/{profiles['premium'].id}",
            json={"label": "Premium", "config": dict(SEEDS["premium"]["config"])},
        )
    assert res.status_code == 200
    assert not world.audits("pipeline.profile_updated")


async def test_patch_rejects_a_rename_to_a_taken_name_and_a_bad_config(world: World) -> None:
    profiles = seed(world)
    with pipelines_app(world) as app:
        taken = await api(app, "PATCH", f"/{profiles['free'].id}", json={"name": "Premium"})
        bad = await api(
            app, "PATCH", f"/{profiles['free'].id}", json={"config": {"answer_route": "local"}}
        )
    assert taken.status_code == 409 and taken.json()["code"] == "profile_name_taken"
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_profile"
    assert profiles["free"].name == "Free"


async def test_clone_copies_the_settings_under_a_new_name(world: World) -> None:
    profiles = seed(world)
    with pipelines_app(world) as app:
        res = await api(app, "POST", f"/{profiles['free'].id}/clone", json={"name": "Free copy"})
        again = await api(app, "POST", f"/{profiles['free'].id}/clone", json={"name": "Free copy"})
    assert res.status_code == 201, res.text
    copy = res.json()
    assert copy["name"] == "Free copy" and copy["label"] == "Economy"
    assert copy["is_default"] is False and copy["builtin_key"] is None
    assert copy["config"] == res.json()["config"]
    assert copy["config"]["answer_route"] == "local" and copy["config"]["max_concurrency"] == 2
    assert again.status_code == 409


async def test_make_default_swaps_atomically(world: World) -> None:
    profiles = seed(world)
    with pipelines_app(world) as app:
        res = await api(app, "POST", f"/{profiles['premium'].id}/default")
        listed = await api(app, "GET")
        same = await api(app, "POST", f"/{profiles['premium'].id}/default")
    assert res.status_code == 200 and res.json()["is_default"] is True
    assert [p["name"] for p in listed.json()["items"] if p["is_default"]] == ["Premium"]
    assert listed.json()["default_id"] == str(profiles["premium"].id)
    assert same.status_code == 200
    (audit,) = world.audits("pipeline.default_set")  # the repeat wrote nothing
    assert audit.details == {
        "profile_id": str(profiles["premium"].id),
        "previous_id": str(profiles["standard"].id),
    }


# -------------------------------------------------------------------- deleting


async def test_the_default_cannot_be_deleted(world: World) -> None:
    profiles = seed(world)
    with pipelines_app(world) as app:
        res = await api(app, "DELETE", f"/{profiles['standard'].id}")
    assert res.status_code == 409 and res.json()["code"] == "profile_is_default"


async def test_a_profile_in_use_cannot_be_deleted_and_the_counts_are_returned(
    world: World,
) -> None:
    profiles = seed(world)
    plan = world.db.add(
        Plan(name="Free", is_default=False, is_active=True, pipeline_profile_id=profiles["free"].id)
    )
    world.db.add(TenantPipelineSettings(tenant_id=world.shree.id, profile_id=profiles["free"].id))
    with pipelines_app(world) as app:
        res = await api(app, "DELETE", f"/{profiles['free'].id}")
    assert res.status_code == 409, res.text
    body = res.json()
    assert body["code"] == "profile_in_use"
    assert (body["plans"], body["tenants"], body["downgrade_targets"]) == (1, 1, 0)
    assert plan in world.db.all(Plan) and profiles["free"] in world.db.all(PipelineProfile)


async def test_an_unused_profile_is_deleted_and_audited(world: World) -> None:
    seed(world)
    with pipelines_app(world) as app:
        made = await api(app, "POST", json={"name": "Spare", "label": "Spare"})
        res = await api(app, "DELETE", f"/{made.json()['id']}")
        gone = await api(app, "GET", f"/{made.json()['id']}")
    assert res.status_code == 204 and gone.status_code == 404
    assert world.audits("pipeline.profile_deleted")[0].details["name"] == "Spare"


# -------------------------------------------------------------------- one business


async def test_a_business_follows_its_plan_until_given_its_own_profile(world: World) -> None:
    profiles = seed(world)
    pro = world.db.add(
        Plan(
            name="Pro", is_default=False, is_active=True, pipeline_profile_id=profiles["premium"].id
        )
    )
    world.shree.plan_id = pro.id
    path = f"/tenants/{world.shree.id}"
    with pipelines_app(world) as app:
        plan_only = await api(app, "GET", path)
        given = await api(app, "PUT", path, json={"profile_id": str(profiles["free"].id)})
        read = await api(app, "GET", path)
        cleared = await api(app, "PUT", path, json={"profile_id": None})
    assert plan_only.status_code == 200, plan_only.text
    assert plan_only.json()["effective"]["name"] == "Premium"
    assert plan_only.json()["plan_profile_id"] == str(profiles["premium"].id)
    assert plan_only.json()["override_profile_id"] is None
    assert given.json()["effective"]["name"] == "Free"  # the business's own beats its plan's
    assert given.json()["override_profile_id"] == str(profiles["free"].id)
    assert read.json()["effective"]["name"] == "Free"
    assert cleared.json()["effective"]["name"] == "Premium"
    assert cleared.json()["override_profile_id"] is None
    # the row stays (PIP3 keeps the business's fair-use overrides and state in it)
    (kept,) = world.db.all(TenantPipelineSettings)
    assert kept.tenant_id == world.shree.id and kept.profile_id is None
    audits = world.audits("pipeline.tenant_profile_set")
    assert [a.details["profile_id"] for a in audits] == [str(profiles["free"].id), None]
    assert audits[1].details["previous_id"] == str(profiles["free"].id)
    assert audits[0].tenant_label == "Shree Labs"


async def test_a_business_on_the_default_plan_answers_with_the_default_profile(
    world: World,
) -> None:
    seed(world)
    with pipelines_app(world) as app:
        res = await api(app, "GET", f"/tenants/{world.shree.id}")
    assert res.json()["effective"]["name"] == "Standard"
    assert res.json()["plan_profile_id"] is None


async def test_the_operator_workspace_is_always_premium(world: World) -> None:
    seed(world)
    world.shree.is_operator = True
    with pipelines_app(world) as app:
        res = await api(app, "GET", f"/tenants/{world.shree.id}")
    assert res.json()["effective"]["name"] == "Premium"


async def test_giving_a_business_the_same_profile_twice_writes_one_audit_row(
    world: World,
) -> None:
    profiles = seed(world)
    path = f"/tenants/{world.shree.id}"
    with pipelines_app(world) as app:
        for _ in range(2):
            await api(app, "PUT", path, json={"profile_id": str(profiles["free"].id)})
    assert len(world.audits("pipeline.tenant_profile_set")) == 1


async def test_unknown_business_or_profile(world: World) -> None:
    seed(world)
    with pipelines_app(world) as app:
        no_tenant = await api(app, "GET", f"/tenants/{uuid.uuid4()}")
        no_profile = await api(
            app, "PUT", f"/tenants/{world.shree.id}", json={"profile_id": str(uuid.uuid4())}
        )
    assert no_tenant.status_code == 404 and no_tenant.json()["code"] == "tenant_not_found"
    assert no_profile.status_code == 404 and no_profile.json()["code"] == "profile_not_found"


# -------------------------------------------------------------------- lanes


async def test_lanes_reports_the_pools(world: World) -> None:
    seed(world)
    with pipelines_app(world) as app:
        configs = lane_configs(app.state.settings)
        lanes = AnswerLanes(FakeRedis(), configs)
        app.state.answer_lanes = lanes
        off = await api(app, "GET", "/lanes")
        held = await lanes.acquire(
            pool="local", tenant_id=uuid.uuid4(), priority="low", max_concurrency=None
        )
        busy = await api(app, "GET", "/lanes")
        await held.release()
    assert off.status_code == 200, off.text
    assert off.json()["enabled"] is False  # RAG_ANSWER_SLOTS=0: no engine-wide queue
    assert off.json()["pools"]["engine"] == {
        "slots": 0,
        "busy": 0,
        "by_priority": {"high": 0, "normal": 0, "low": 0},
    }
    assert busy.json()["pools"]["local"]["busy"] == 1
    assert busy.json()["pools"]["local"]["by_priority"]["low"] == 1


# -------------------------------------------------------------------- the plan's profile


async def test_a_plan_picks_a_profile_and_the_screens_show_its_label(world: World) -> None:
    profiles = seed(world)
    free_id = str(profiles["free"].id)
    with pipelines_app(world) as app:
        made = await req(
            app,
            "POST",
            "/api/v1/admin/plans",
            key=ADMIN,
            json={"name": "Free tier", "pipeline_profile_id": free_id},
        )
        listed = await req(app, "GET", "/api/v1/admin/plans", key=ADMIN)
        reset = await req(
            app,
            "PATCH",
            f"/api/v1/admin/plans/{made.json()['id']}",
            key=ADMIN,
            json={"pipeline_profile_id": None},
        )
        again = await req(
            app,
            "PATCH",
            f"/api/v1/admin/plans/{made.json()['id']}",
            key=ADMIN,
            json={"pipeline_profile_id": free_id},
        )
    assert made.status_code == 201, made.text
    assert made.json()["pipeline_profile_id"] == free_id
    assert made.json()["pipeline_profile_label"] == "Economy"
    by_name = {p["name"]: p for p in listed.json()["items"]}
    assert by_name["Free tier"]["pipeline_profile_label"] == "Economy"
    # a plan with no profile shows the default's label
    assert by_name["Standard"]["pipeline_profile_id"] is None
    assert by_name["Standard"]["pipeline_profile_label"] == "Standard"
    assert reset.json()["pipeline_profile_id"] is None
    assert again.json()["pipeline_profile_id"] == free_id


async def test_a_plan_cannot_pick_a_profile_that_does_not_exist(world: World) -> None:
    seed(world)
    with pipelines_app(world) as app:
        made = await req(
            app,
            "POST",
            "/api/v1/admin/plans",
            key=ADMIN,
            json={"name": "Ghost", "pipeline_profile_id": str(uuid.uuid4())},
        )
        pro = world.db.add(Plan(name="Pro", is_default=False, is_active=True))
        patched = await req(
            app,
            "PATCH",
            f"/api/v1/admin/plans/{pro.id}",
            key=ADMIN,
            json={"pipeline_profile_id": str(uuid.uuid4())},
        )
    assert made.status_code == 404 and made.json()["code"] == "profile_not_found"
    assert patched.status_code == 404 and patched.json()["code"] == "profile_not_found"
    assert not any(p.name == "Ghost" for p in world.db.all(Plan))


async def test_the_internal_plan_keeps_its_profile_locked(world: World) -> None:
    profiles = seed(world)
    internal = world.db.add(
        Plan(
            name="Internal",
            is_default=False,
            is_active=True,
            is_internal=True,
            pipeline_profile_id=profiles["premium"].id,
        )
    )
    with pipelines_app(world) as app:
        res = await req(
            app,
            "PATCH",
            f"/api/v1/admin/plans/{internal.id}",
            key=ADMIN,
            json={"pipeline_profile_id": str(profiles["free"].id)},
        )
    assert res.status_code == 409 and res.json()["code"] == "plan_internal_locked"


async def test_changing_a_plans_profile_is_audited_in_plan_updated(world: World) -> None:
    profiles = seed(world)
    pro = world.db.add(Plan(name="Pro", is_default=False, is_active=True))
    with pipelines_app(world) as app:
        await req(
            app,
            "PATCH",
            f"/api/v1/admin/plans/{pro.id}",
            key=ADMIN,
            json={"pipeline_profile_id": str(profiles["free"].id)},
        )
    (audit,) = world.audits("plan.updated")
    assert audit.details["changes"]["pipeline_profile_id"] == {
        "from": None,
        "to": str(profiles["free"].id),
    }
    assert pro.pipeline_profile_id == profiles["free"].id  # stored as a UUID, not the audit's text
