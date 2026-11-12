"""The Plan & billing page's no-surprises fields (TV3): renewal, projection, coming price changes
and the spending cap, through `GET /api/v1/billing/account`."""

from datetime import date, timedelta

from callobuzz_rag.billing.notice import ist_midnight
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from tests.unit.billing_fakes import stored_pricing
from tests.unit.test_auth_routes import World, req
from tests.unit.test_billing_summary import ACCOUNT, app_for, token, world  # noqa: F401


async def test_the_owner_sees_the_renewal_the_projection_and_a_coming_price(
    world: World,  # noqa: F811
) -> None:
    coming = stored_pricing(
        world.db.default_plan,
        version=2,
        subscription_fee_minor=129_900,
        usage_mode="per_question",
        per_question_minor=300,
        payment_type="prepaid",
    )
    coming.is_current = False
    coming.effective_at = ist_midnight(date.today() + timedelta(days=40))
    world.db.add(coming)
    with app_for(world) as app:
        world.db.add(
            BillingAccount(
                tenant_id=world.shree.id,
                currency="INR",
                ledger_seq=4,
                billing_day=12,
                next_charge_on=date.today() + timedelta(days=60),
                spend_cap_minor=10_000,
            )
        )
        res = await req(app, "GET", ACCOUNT, token=await token(app, "asha@shree.example"))
    body = res.json()
    assert res.status_code == 200, res.text
    # the renewal is after the new price starts, so it shows the new fee and says so
    assert body["renewal"]["amount_minor"] == 129_900
    assert body["renewal"]["new_price_from"] == body["next_charge_on"]
    assert body["spend_cap"] == {
        "cap_minor": 10_000,
        "used_minor": 8_400,
        "max_minor": None,
        "state": "near",
    }
    assert body["projected_month_end_minor"] >= 8_400
    [change] = body["price_changes"]
    assert [c["label"] for c in change["changes"]] == ["Plan fee", "Price per question"]
    assert change["changes"][0] == {"label": "Plan fee", "old_minor": 99_900, "new_minor": 129_900}
    assert change["effective_on"] == body["next_charge_on"]


async def test_a_renewal_before_the_new_price_starts_shows_the_old_fee(
    world: World,  # noqa: F811
) -> None:
    coming = stored_pricing(
        world.db.default_plan,
        version=2,
        subscription_fee_minor=129_900,
        usage_mode="per_question",
        per_question_minor=200,
    )
    coming.is_current = False
    coming.effective_at = ist_midnight(date.today() + timedelta(days=40))
    world.db.add(coming)
    with app_for(world) as app:
        world.db.add(
            BillingAccount(
                tenant_id=world.shree.id,
                currency="INR",
                ledger_seq=4,
                billing_day=12,
                next_charge_on=date.today() + timedelta(days=5),
            )
        )
        res = await req(app, "GET", ACCOUNT, token=await token(app, "asha@shree.example"))
    body = res.json()
    assert body["renewal"]["amount_minor"] == 99_900 and body["renewal"]["new_price_from"] is None
    assert len(body["price_changes"]) == 1


async def test_a_member_without_money_rights_gets_nothing_to_see(
    world: World,  # noqa: F811
) -> None:
    with app_for(world) as app:
        world.db.add(
            BillingAccount(
                tenant_id=world.shree.id,
                currency="INR",
                next_charge_on=date(2026, 11, 1),
                spend_cap_minor=5_000,
            )
        )
        res = await req(app, "GET", ACCOUNT, token=await token(app, "ravi@shree.example"))
    body = res.json()
    assert body["renewal"] is None and body["projected_month_end_minor"] is None
    assert body["price_changes"] == [] and body["spend_cap"] is None


async def test_a_workspace_that_is_not_billed_has_no_renewal_or_cap(
    world: World,  # noqa: F811
) -> None:
    with app_for(world) as app:
        world.db.rows[PlanPricing].clear()
        res = await req(app, "GET", ACCOUNT, token=await token(app, "asha@shree.example"))
    body = res.json()
    assert body["renewal"] is None and body["spend_cap"] is None
    assert body["price_changes"] == [] and body["projected_month_end_minor"] is None


async def test_a_pending_change_for_this_workspace_is_listed_with_its_date(
    world: World,  # noqa: F811
) -> None:
    from datetime import UTC, datetime

    with app_for(world) as app:
        world.db.add(
            BillingAccount(
                tenant_id=world.shree.id,
                currency="INR",
                ledger_seq=4,
                billing_day=12,
                next_charge_on=date.today() + timedelta(days=9),
                pending_change={"overrides": {"per_question_minor": 300}},
                pending_change_at=datetime.now(UTC) + timedelta(days=40),
            )
        )
        res = await req(app, "GET", ACCOUNT, token=await token(app, "asha@shree.example"))
    [change] = res.json()["price_changes"]
    assert change["changes"] == [
        {"label": "Price per question", "old_minor": 200, "new_minor": 300}
    ]


async def test_a_pending_change_due_at_the_renewal_sets_the_renewal_amount(
    world: World,  # noqa: F811
) -> None:
    due = date.today() + timedelta(days=5)
    with app_for(world) as app:
        world.db.add(
            BillingAccount(
                tenant_id=world.shree.id,
                currency="INR",
                ledger_seq=4,
                billing_day=12,
                next_charge_on=due,
                pending_change={"overrides": {"subscription_fee_minor": 149_900}},
                pending_change_at=ist_midnight(due),
            )
        )
        res = await req(app, "GET", ACCOUNT, token=await token(app, "asha@shree.example"))
    body = res.json()
    assert body["renewal"]["amount_minor"] == 149_900
    assert body["renewal"]["new_price_from"] == body["next_charge_on"]
