"""`PUT /api/v1/billing/spend-cap`: the owner sets or removes the monthly cap (TV3)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

import pytest

from callobuzz_rag.accounts.settings_store import SETTINGS_ROW_ID
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import World, auth_app, login, req

URL = "/api/v1/billing/spend-cap"


@pytest.fixture
def world() -> World:
    w = World()
    w.db.add(
        stored_pricing(
            w.db.default_plan,
            subscription_fee_minor=99_900,
            usage_mode="per_question",
            per_question_minor=200,
        )
    )
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("ravi@shree.example"), w.shree, "member")
    w.account = w.db.add(  # type: ignore[attr-defined]
        BillingAccount(
            tenant_id=w.shree.id,
            currency="INR",
            ledger_seq=3,
            next_charge_on=date.today() + timedelta(days=9),
        )
    )
    return w


@contextmanager
def app_for(world: World) -> Iterator[Any]:
    with auth_app(world) as app, fake_accounts(world.db):
        yield app


async def put(app: Any, email: str, body: dict[str, Any]) -> Any:
    token = (await login(app, email)).json()["access_token"]
    return await req(app, "PUT", URL, token=token, json=body)


def set_max(world: World, minor: int) -> None:
    from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache

    invalidate_engine_settings_cache()
    world.db.add(
        EngineSettingsRow(
            id=SETTINGS_ROW_ID, value={"trust": {"spend_cap_max_minor": {"INR": minor}}}
        )
    )


async def test_the_owner_sets_a_cap_and_it_is_audited(world: World) -> None:
    with app_for(world) as app:
        res = await put(app, "asha@shree.example", {"cap_minor": 200_000})
    assert res.status_code == 200, res.text
    assert res.json() == {"cap_minor": 200_000, "used_minor": 0, "max_minor": None, "state": "ok"}
    assert world.account.spend_cap_minor == 200_000  # type: ignore[attr-defined]
    [audit] = world.audits("billing.spend_cap_set")
    assert audit.details == {"cap_minor": 200_000, "previous_minor": None, "currency": "INR"}


async def test_null_removes_the_cap_and_setting_the_same_cap_writes_nothing(
    world: World,
) -> None:
    with app_for(world) as app:
        await put(app, "asha@shree.example", {"cap_minor": 200_000})
        again = await put(app, "asha@shree.example", {"cap_minor": 200_000})
        removed = await put(app, "asha@shree.example", {"cap_minor": None})
    assert again.status_code == 200 and removed.json()["cap_minor"] is None
    assert world.account.spend_cap_minor is None  # type: ignore[attr-defined]
    details = [a.details for a in world.audits("billing.spend_cap_set")]
    assert [d["cap_minor"] for d in details] == [200_000, None]
    assert details[1]["previous_minor"] == 200_000


async def test_above_the_providers_ceiling_is_refused_with_the_maximum(world: World) -> None:
    set_max(world, 500_000)
    with app_for(world) as app:
        too_high = await put(app, "asha@shree.example", {"cap_minor": 500_001})
        fine = await put(app, "asha@shree.example", {"cap_minor": 500_000})
    body = too_high.json()
    assert too_high.status_code == 422 and body["code"] == "spend_cap_too_high"
    assert body["max_minor"] == 500_000
    assert fine.status_code == 200 and fine.json()["max_minor"] == 500_000


async def test_a_workspace_that_is_not_billed_has_nothing_to_cap(world: World) -> None:
    world.db.rows[PlanPricing].clear()
    with app_for(world) as app:
        res = await put(app, "asha@shree.example", {"cap_minor": 100_000})
    assert res.status_code == 409 and res.json()["code"] == "spend_cap_not_billed"
    assert world.account.spend_cap_minor is None  # type: ignore[attr-defined]


async def test_a_member_cannot_change_it(world: World) -> None:
    with app_for(world) as app:
        res = await put(app, "ravi@shree.example", {"cap_minor": 100_000})
    assert res.status_code == 403 and res.json()["code"] == "owner_required"
    assert world.account.spend_cap_minor is None  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "body",
    [{"cap_minor": 1.5}, {"cap_minor": 100.0}, {"cap_minor": -5}, {"cap_minor": 0}, {"cap": 5}],
)
async def test_floats_negatives_and_unknown_fields_are_422(
    world: World, body: dict[str, Any]
) -> None:
    with app_for(world) as app:
        res = await put(app, "asha@shree.example", body)
    assert res.status_code == 422


async def test_the_admin_is_sent_to_the_admin_pages(world: World) -> None:
    from tests.unit.test_auth_routes import ADMIN

    with app_for(world) as app:
        res = await req(app, "PUT", URL, key=ADMIN, json={"cap_minor": 5})
    assert res.status_code == 403 and res.json()["code"] == "no_workspace"
