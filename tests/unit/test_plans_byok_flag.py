"""P5 T8: a plan says whether its businesses may use their own AI key (`byok_allowed`)."""

from typing import Any

from tests.unit.test_auth_routes import World
from tests.unit.test_plans_admin import api, app_for, plan_named
from tests.unit.test_plans_admin import world as world


async def test_a_new_plan_allows_the_own_key_unless_told_otherwise(world: World) -> None:
    with app_for(world) as app:
        plain = await api(app, "POST", json={"name": "Plain"})
        free = await api(app, "POST", json={"name": "Starter", "byok_allowed": False})
    assert plain.status_code == 201 and plain.json()["byok_allowed"] is True
    assert free.status_code == 201 and free.json()["byok_allowed"] is False
    assert plan_named(world, "Starter").byok_allowed is False
    created = [a.details for a in world.audits("plan.created") if a.details["name"] == "Starter"]
    assert created[0]["byok_allowed"] is False


async def test_the_flag_changes_with_a_patch_and_is_audited(world: World) -> None:
    with app_for(world) as app:
        made = (await api(app, "POST", json={"name": "Starter"})).json()
        off = await api(app, "PATCH", f"/{made['id']}", json={"byok_allowed": False})
        same = await api(app, "PATCH", f"/{made['id']}", json={"byok_allowed": False})
        unchanged = await api(app, "PATCH", f"/{made['id']}", json={"byok_allowed": None})
        read = await api(app, "GET", f"/{made['id']}")
    assert off.json()["byok_allowed"] is False and same.status_code == 200
    assert unchanged.json()["byok_allowed"] is False  # null means unchanged, never "off"
    assert read.json()["byok_allowed"] is False
    [audit] = [a for a in world.audits("plan.updated") if "byok_allowed" in a.details["changes"]]
    assert audit.details["changes"]["byok_allowed"] == {"from": True, "to": False}


async def test_an_existing_plan_reads_as_allowed(world: World) -> None:
    from callobuzz_rag.db.models.plan import Plan

    row: Any = world.db.add(Plan(name="Old", is_default=False, is_active=True))
    with app_for(world) as app:
        res = await api(app, "GET", f"/{row.id}")
    assert res.json()["byok_allowed"] is True  # the model's default, as the migration's
