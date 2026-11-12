"""P5 T5: service orders tracked in the Support inbox.

The orders are made the way a purchase makes them (`open_for_grant`), then moved along through the
operator's route; owners and members read them through theirs. Through the real app on the
in-memory database of `accounts_fakes.py`.
"""

import itertools
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.billing import service_orders
from callobuzz_rag.db.models.add_on import AddOnGrant
from callobuzz_rag.db.models.service_order import ServiceOrder
from callobuzz_rag.db.models.support import SupportMessage, SupportRequest
from callobuzz_rag.db.models.tenant import Tenant
from tests.unit.test_auth_routes import ADMIN, SA_EMAIL, World, auth_app, login, req

ADMIN_URL = "/api/v1/admin/billing/service-orders"
OWNER_URL = "/api/v1/billing/service-orders"
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


@pytest.fixture
def world() -> World:
    w = World()
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("ravi@shree.example"), w.shree, "member")
    w.member(w.user("meena@pune.example"), w.pune, "owner")
    return w


@pytest.fixture(autouse=True)
def request_numbers() -> Iterator[None]:
    """Support request numbers come from a PostgreSQL sequence."""
    numbers = itertools.count(2001)

    async def next_number(session: Any) -> int:
        return next(numbers)

    with patch("callobuzz_rag.support.service.next_number", next_number):
        yield


@contextmanager
def app_for(world: World) -> Iterator[Any]:
    with auth_app(world) as app:
        yield app


async def order_for(
    world: World, tenant: Tenant, name: str = "Installation help", months: int | None = 3
) -> ServiceOrder:
    grant = world.db.add(
        AddOnGrant(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            add_on_id=uuid.uuid4(),
            kind="service",
            name=name,
            currency="INR",
            price_minor=199_900,
            payment="free",
            granted_by="ops@callobuzz.test",
            includes_support_months=months,
            starts_at=NOW,
        )
    )
    return await service_orders.open_for_grant(
        world.db.session(), tenant, grant, owner_note="Please call before 11", now=NOW
    )


async def patch_order(app: Any, order: ServiceOrder, **body: Any) -> Any:
    return await req(app, "PATCH", f"{ADMIN_URL}/{order.id}", key=ADMIN, json=body)


async def owner_get(app: Any, email: str) -> Any:
    token = (await login(app, email)).json()["access_token"]
    return await req(app, "GET", OWNER_URL, token=token)


# --------------------------------------------------------------------------- the transitions


async def test_an_order_moves_requested_to_in_progress_to_done(world: World) -> None:
    order = await order_for(world, world.shree)
    with app_for(world) as app:
        started = await patch_order(app, order, status="in_progress", public_note="On it")
        done = await patch_order(app, order, status="done")
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "in_progress" and started.json()["public_note"] == "On it"
    body = done.json()
    assert body["status"] == "done" and body["completed_at"] is not None
    completed = datetime.fromisoformat(body["completed_at"])
    expected = service_orders.add_months(completed.date(), 3)
    assert body["support_until"] == expected.isoformat()


@pytest.mark.parametrize(
    ("start", "to"),
    [
        ("requested", "done"),
        ("requested", "requested"),
        ("in_progress", "requested"),
        ("done", "requested"),
        ("done", "in_progress"),
        ("done", "cancelled"),
        ("cancelled", "in_progress"),
        ("cancelled", "done"),
    ],
)
async def test_the_other_transitions_are_409(world: World, start: str, to: str) -> None:
    order = await order_for(world, world.shree)
    order.status = start
    with app_for(world) as app:
        res = await patch_order(app, order, status=to)
    if start == to:
        assert res.status_code == 200  # no change is not a move
        return
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "invalid_service_order_transition"
    assert order.status == start


@pytest.mark.parametrize("start", ["requested", "in_progress"])
async def test_an_open_order_can_be_cancelled_without_a_refund(world: World, start: str) -> None:
    order = await order_for(world, world.shree)
    order.status = start
    with app_for(world) as app:
        res = await patch_order(app, order, status="cancelled", public_note="Refunded by UPI")
    assert res.json()["status"] == "cancelled"
    assert world.audits("billing.add_on_granted") == []  # nothing moved in the books here


def test_support_months_are_calendar_months_with_the_day_clamped() -> None:
    assert service_orders.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert service_orders.add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert service_orders.add_months(date(2026, 10, 15), 12) == date(2027, 10, 15)
    assert service_orders.support_until(NOW, None) is None


async def test_no_support_months_means_no_support_date(world: World) -> None:
    order = await order_for(world, world.shree, name="Priority support", months=None)
    with app_for(world) as app:
        await patch_order(app, order, status="in_progress")
        done = await patch_order(app, order, status="done")
    assert done.json()["support_until"] is None


# -------------------------------------------------------------------------------- the notes


async def test_the_owner_sees_the_public_note_and_never_the_internal_one(world: World) -> None:
    order = await order_for(world, world.shree)
    with app_for(world) as app:
        res = await patch_order(
            app,
            order,
            status="in_progress",
            public_note="We start on Monday",
            internal_note="Customer is slow to reply; vendor quote 12k",
        )
        admin_json = res.json()
        owner = await owner_get(app, "asha@shree.example")
        member = await owner_get(app, "ravi@shree.example")
    assert admin_json["internal_note"].startswith("Customer is slow")
    for seen in (owner, member):
        assert seen.status_code == 200, seen.text
        assert "vendor quote" not in seen.text and "internal_note" not in seen.text
        (item,) = seen.json()["items"]
        assert item["public_note"] == "We start on Monday" and item["status_label"] == "In progress"
        assert item["owner_note"] == "Please call before 11"


async def test_a_note_can_be_cleared_with_null(world: World) -> None:
    order = await order_for(world, world.shree)
    with app_for(world) as app:
        await patch_order(app, order, public_note="One")
        cleared = await patch_order(app, order, public_note=None)
    assert cleared.json()["public_note"] is None


# ------------------------------------------------------------------ the inbox and the e-mails


async def test_a_status_change_posts_on_the_support_request_and_mails_the_owners(
    world: World,
) -> None:
    order = await order_for(world, world.shree)
    with app_for(world) as app:
        await patch_order(app, order, status="in_progress", public_note="Starting now")
        await patch_order(app, order, status="done")
    (request,) = world.db.all(SupportRequest)
    bodies = [m.body for m in world.db.all(SupportMessage) if m.author_kind == "deployer"]
    assert bodies[0] == "Status: In progress — Starting now"
    assert bodies[1].startswith("Status: Done")
    assert request.status == "closed"  # the order is over
    mails = [m for m in world.mail if m[0] == "service_order_update"]
    assert [(m[1], m[2]["status_label"]) for m in mails] == [
        ("asha@shree.example", "In progress"),
        ("asha@shree.example", "Done"),
    ]  # owners only: the member is not mailed
    assert mails[0][2]["public_note"] == "Starting now" and mails[0][2]["service_name"]


async def test_a_note_only_change_posts_and_mails_nothing(world: World) -> None:
    order = await order_for(world, world.shree)
    with app_for(world) as app:
        await patch_order(app, order, internal_note="just for us")
    assert world.mail == []
    assert world.audits("billing.service_order_updated") == []
    assert [m for m in world.db.all(SupportMessage) if m.author_kind == "deployer"] == []


async def test_the_audit_row_has_the_order_and_the_move(world: World) -> None:
    order = await order_for(world, world.shree)
    with app_for(world) as app:
        await patch_order(app, order, status="in_progress")
    [audit] = world.audits("billing.service_order_updated")
    assert audit.details == {"order_id": str(order.id), "from": "requested", "to": "in_progress"}


async def test_a_new_order_is_announced_once_to_the_provider(world: World) -> None:
    order = await order_for(world, world.shree)
    sent: list[tuple[Any, ...]] = []

    def enqueue(template: str, to: str, context: dict[str, Any], *, expires_s: float) -> None:
        sent.append((template, to, context))

    async def announce() -> int:
        return await service_orders.notify_provider_of_new_orders(
            world.db.factory(),
            enqueue=enqueue,
            support_email="",
            superadmin_email=SA_EMAIL,
            dashboard_url="https://app.xyz.in",
        )

    assert await announce() == 1
    assert await announce() == 0  # claimed: the hourly schedule never sends it again
    [(template, to, context)] = sent
    assert (template, to) == ("service_order_update", SA_EMAIL)
    assert context["status_label"] == "New order" and context["tenant_name"] == world.shree.name
    assert context["public_note"] == "Please call before 11"
    assert context["orders_url"].endswith("/admin/billing?tab=service-orders")
    assert order.provider_notified_at is not None


async def test_a_failed_queue_rolls_the_claim_back_so_the_next_run_sends_it(
    world: World,
) -> None:
    order = await order_for(world, world.shree)
    calls: list[str] = []

    def flaky(template: str, to: str, context: dict[str, Any], *, expires_s: float) -> None:
        calls.append(template)
        if len(calls) == 1:
            raise ConnectionError("broker down")

    async def announce() -> int:
        return await service_orders.notify_provider_of_new_orders(
            world.db.factory(),
            enqueue=flaky,
            support_email="",
            superadmin_email=SA_EMAIL,
            dashboard_url="https://app.xyz.in",
        )

    # the stand-in session has no transactions, so what is pinned is the order of events: the
    # claim is rolled back (never committed) when queueing fails, and committed once it worked
    commits = world.db.commits
    assert await announce() == 0
    assert (world.db.rollbacks, world.db.commits) == (1, commits)
    world.db.rollbacks = 0
    order.provider_notified_at = None  # what the rollback does to the real row
    assert await announce() == 1
    assert (world.db.rollbacks, world.db.commits) == (0, commits + 1)
    assert order.provider_notified_at is not None


async def test_with_nowhere_to_send_it_the_order_stays_unannounced(world: World) -> None:
    order = await order_for(world, world.shree)

    def enqueue(*a: Any, **k: Any) -> None:
        raise AssertionError("nothing to send to")

    got = await service_orders.notify_provider_of_new_orders(
        world.db.factory(),
        enqueue=enqueue,
        support_email="",
        superadmin_email="",
        dashboard_url="https://app.xyz.in",
    )
    assert got == 0 and order.provider_notified_at is None


# ------------------------------------------------------------------------------ who may see


async def test_an_owner_lists_only_their_own_workspaces_orders(world: World) -> None:
    mine = await order_for(world, world.shree, name="Installation help")
    await order_for(world, world.pune, name="Website design")
    with app_for(world) as app:
        res = await owner_get(app, "asha@shree.example")
    assert [i["id"] for i in res.json()["items"]] == [str(mine.id)]


async def test_a_knowledge_base_key_and_the_admin_principal_are_refused_the_owner_list(
    world: World,
) -> None:
    with app_for(world) as app:
        admin = await req(app, "GET", OWNER_URL, key=ADMIN)
    assert admin.status_code == 403 and admin.json()["code"] == "no_workspace"


async def test_the_operator_route_is_for_the_admin_and_filters(world: World) -> None:
    a = await order_for(world, world.shree)
    b = await order_for(world, world.pune, name="Website design")
    b.status = "in_progress"
    with app_for(world) as app:
        everything = await req(app, "GET", ADMIN_URL, key=ADMIN)
        started = await req(app, "GET", f"{ADMIN_URL}?status=in_progress", key=ADMIN)
        pune = await req(app, "GET", f"{ADMIN_URL}?tenant_id={world.pune.id}", key=ADMIN)
        owner_token = (await login(app, "asha@shree.example")).json()["access_token"]
        refused = await req(app, "GET", ADMIN_URL, token=owner_token)
    assert everything.json()["total"] == 2
    assert [i["id"] for i in started.json()["items"]] == [str(b.id)]
    assert [i["tenant_name"] for i in pune.json()["items"]] == [world.pune.name]
    assert refused.status_code == 403
    assert a.status == "requested"


async def test_an_unknown_order_is_404(world: World) -> None:
    with app_for(world) as app:
        res = await req(
            app, "PATCH", f"{ADMIN_URL}/{uuid.uuid4()}", key=ADMIN, json={"status": "done"}
        )
    assert res.status_code == 404 and res.json()["code"] == "service_order_not_found"


async def test_notes_are_capped(world: World) -> None:
    order = await order_for(world, world.shree)
    with app_for(world) as app:
        res = await patch_order(app, order, public_note="x" * 1001)
        typo = await patch_order(app, order, statuz="done")
    assert res.status_code == 422 and typo.status_code == 422
