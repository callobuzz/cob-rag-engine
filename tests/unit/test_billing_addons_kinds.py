"""P5 T4: feature-unlock and service add-ons in the catalogue, and applying them.

Extends `test_billing_addons_api.py` (same fake world); the SQL runs on PostgreSQL in
`tests/integration/test_saas_plans_db.py`.
"""

import itertools
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.billing import addons
from callobuzz_rag.db.models.add_on import AddOn, AddOnGrant
from callobuzz_rag.db.models.service_order import ServiceOrder
from callobuzz_rag.db.models.support import SupportRequest
from callobuzz_rag.support import service as support_service
from tests.unit.test_billing_addons_api import (
    ADD_ONS,
    GRANTS,
    World,
    admin,
    app_for,
    apply_body,
    credit,
    limit,
    make,
    pack,
    priced,
)
from tests.unit.test_billing_addons_api import world as world


def unlock(**kw: Any) -> dict[str, Any]:
    return {
        "kind": "feature_unlock",
        "name": "Use your own AI key",
        "currency": "INR",
        "price_minor": 49_900,
        "feature": "byok",
        "duration": "period",
        "renews": True,
    } | kw


def service(**kw: Any) -> dict[str, Any]:
    return {
        "kind": "service",
        "name": "Installation help",
        "currency": "INR",
        "price_minor": 199_900,
        "includes_support_months": 3,
        "sac_code": "998313",
    } | kw


@contextmanager
def lookups(*, allowed: bool = False) -> Iterator[None]:
    """The own-key lookup is one SQL statement the fake database cannot run; support request
    numbers come from a PostgreSQL sequence."""
    numbers = itertools.count(1042)
    with (
        patch.object(addons, "byok_status", AsyncMock(return_value=(allowed, "plan"))),
        patch.object(
            support_service, "next_number", AsyncMock(side_effect=lambda s: next(numbers))
        ),
    ):
        yield


# ---------------------------------------------------------------------------- the catalogue


async def test_the_new_kinds_are_created_with_their_fields(world: World) -> None:
    with app_for(world) as app:
        key = await make(app, unlock())
        help_ = await make(app, service())
    assert key["feature"] == "byok" and key["duration"] == "period" and key["renews"] is True
    assert key["sold_to_owners"] is False and key["placeholder"] is False
    assert help_["includes_support_months"] == 3 and help_["sac_code"] == "998313"
    assert help_["renews"] is False and help_["feature"] is None


@pytest.mark.parametrize(
    "body",
    [
        unlock(feature=None),  # an unlock names its feature
        unlock(duration=None),
        unlock(duration="permanent", renews=True),  # only a monthly unlock renews
        unlock(credit_minor=5),
        unlock(includes_support_months=2),
        service(credit_minor=100),
        service(feature="byok"),
        service(duration="period"),
        service(sac_code="12345"),
        service(sac_code="AB1234"),
        service(includes_support_months=0),
        service(includes_support_months=37),
        credit(renews=True),  # a credit pack never renews
        pack(feature="byok"),
        limit(renews=True),
    ],
)
async def test_a_wrong_field_for_the_new_kinds_is_422(
    world: World,
    body: dict[str, Any],
) -> None:
    with app_for(world) as app:
        res = await admin(app, "POST", ADD_ONS, json=body)
    assert res.status_code == 422, res.text


async def test_sold_to_owners_is_a_field_and_a_new_price_clears_the_placeholder(
    world: World,
) -> None:
    with app_for(world) as app:
        row = await make(app, service(sold_to_owners=True))
        next(a for a in world.db.all(AddOn) if str(a.id) == row["id"]).placeholder = True
        url = f"{ADD_ONS}/{row['id']}"
        other = await admin(app, "PATCH", url, json={"description": "Call and chat"})
        priced_ = await admin(app, "PATCH", url, json={"price_minor": 149_900})
        hidden = await admin(app, "PATCH", url, json={"sold_to_owners": False})
    assert row["sold_to_owners"] is True
    assert other.json()["placeholder"] is True  # not a price edit
    assert priced_.json()["placeholder"] is False
    assert hidden.json()["sold_to_owners"] is False


async def test_keeping_the_seeded_price_makes_it_real(world: World) -> None:
    with app_for(world) as app:
        row = await make(app, service(sold_to_owners=True))
        add_on = next(a for a in world.db.all(AddOn) if str(a.id) == row["id"])
        add_on.placeholder = True
        url = f"{ADD_ONS}/{row['id']}"
        same_price = await admin(app, "PATCH", url, json={"price_minor": add_on.price_minor})
        add_on.placeholder = True
        keep = await admin(app, "PATCH", url, json={"placeholder": False})
        add_on.placeholder = True
        wrong = await admin(app, "PATCH", url, json={"placeholder": True})
    assert same_price.json()["placeholder"] is False
    assert keep.status_code == 200 and keep.json()["placeholder"] is False
    assert wrong.status_code == 422


# ------------------------------------------------------------------------------- applying


async def test_a_monthly_unlock_runs_one_full_month(world: World) -> None:
    with app_for(world) as app, lookups():
        row = await make(app, unlock())
        res = await admin(
            app,
            "POST",
            GRANTS.format(world.shree.id),
            json=apply_body(row, payment="free"),
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["kind"] == "feature_unlock" and body["feature"] == "byok"
    assert body["renews"] is True and body["active"] is True
    # one full month from now
    assert body["ends_at"].startswith(addons.period_end(datetime.now(UTC)).date().isoformat())
    assert body["summary"].startswith("Your own AI key until ")


def test_a_monthly_add_on_always_lasts_one_full_month_whatever_the_accounts_period() -> None:
    # bought on 10 Oct: ends 10 Nov, on a yearly account and on one charged tomorrow alike
    assert addons.period_end(datetime(2026, 10, 10, 12, tzinfo=UTC)) == datetime(
        2026, 11, 10, tzinfo=UTC
    )
    assert addons.period_end(datetime(2026, 12, 20, tzinfo=UTC)) == datetime(
        2027, 1, 20, tzinfo=UTC
    )


def test_the_pause_line_rule_is_the_one_the_shop_and_renewals_share() -> None:
    from callobuzz_rag.billing.pricing import PricingFields

    prepaid = PricingFields(payment_type="prepaid", grace_minor=0)
    assert addons.past_the_pause_line(prepaid, 1_000, 1_000)  # landing on zero already pauses
    assert not addons.past_the_pause_line(prepaid, 1_001, 1_000)
    postpaid = PricingFields(payment_type="postpaid", credit_limit_minor=5_000)
    assert addons.past_the_pause_line(postpaid, 0, 5_000)
    assert not addons.past_the_pause_line(postpaid, 0, 4_999)


async def test_applying_a_feature_the_plan_already_has_is_409(world: World) -> None:
    with app_for(world) as app, lookups(allowed=True):
        row = await make(app, unlock())
        res = await admin(
            app, "POST", GRANTS.format(world.shree.id), json=apply_body(row, payment="free")
        )
    assert res.status_code == 409 and res.json()["code"] == "feature_already_included"
    assert world.db.all(AddOnGrant) == []


async def test_a_service_opens_one_order_and_a_support_request(world: World) -> None:
    priced(world, subscription_fee_minor=0)
    with app_for(world) as app, lookups():
        row = await make(app, service())
        first = await admin(
            app,
            "POST",
            GRANTS.format(world.shree.id),
            json=apply_body(row, payment="free", memo="Please set it up on Monday"),
        )
        again = await admin(
            app,
            "POST",
            GRANTS.format(world.shree.id),
            json=apply_body(row, payment="free", memo="Please set it up on Monday"),
        )
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["ends_at"] is None  # a one-time service has no period
    (order,) = world.db.all(ServiceOrder)
    assert order.status == "requested" and order.name == "Installation help"
    assert order.includes_support_months == 3 and order.owner_note == "Please set it up on Monday"
    (request,) = world.db.all(SupportRequest)
    assert request.subject == "Service order: Installation help"
    assert request.context["service_order"]["order_id"] == str(order.id)
    assert order.support_request_id == request.id
    assert len(world.audits("billing.add_on_granted")) == 1


async def test_a_service_paid_from_the_balance_takes_its_price_and_opens_the_order(
    world: World,
) -> None:
    priced(world, subscription_fee_minor=0)
    with app_for(world, available=500_000) as app, lookups():
        row = await make(app, service())
        res = await admin(
            app,
            "POST",
            GRANTS.format(world.shree.id),
            json=apply_body(row, payment="wallet"),
        )
        entries = list(app.state.entries)
    assert res.status_code == 201, res.text
    assert [e.amount_minor for e in entries] == [-199_900]
    assert len(world.db.all(ServiceOrder)) == 1


async def test_paid_online_works_the_same(world: World) -> None:
    priced(world, subscription_fee_minor=0)
    with app_for(world, available=0) as app, lookups():
        row = await make(app, service())
        session = world.db.session()
        grant, created = await addons.apply_paid_add_on(
            session,
            world.shree.id,
            uuid.UUID(row["id"]),
            gateway=addons.GatewayPayment("pay_Test0001", "rzp:pay_Test0001"),
            price_minor=199_900,
            actor=("system", None, "payment pay_Test0001"),
        )
        entries = list(app.state.entries)
    assert created and grant.payment == "gateway"
    assert [e.amount_minor for e in entries] == [-199_900]  # the funded purchase
    (order,) = world.db.all(ServiceOrder)
    assert order.name == "Installation help"
