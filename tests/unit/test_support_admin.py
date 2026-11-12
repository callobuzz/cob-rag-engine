"""The provider's support inbox (TV3): list and read with the workspace information, reply (first
response time, status, mail both ways), set the status, the numbers, and who may use it."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.support import SupportMessage, SupportRequest
from callobuzz_rag.support import stats
from tests.unit.test_auth_routes import ADMIN, World, auth_app, login, req

BASE = "/api/v1/admin/support"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


@pytest.fixture
def world() -> World:
    w = World()
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("meera@shree.example"), w.shree, "owner")
    w.member(w.user("dev@pune.example"), w.pune, "owner")
    return w


@contextmanager
def app_for(world: World, **trust: Any) -> Iterator[Any]:
    from callobuzz_rag.accounts.settings_store import (
        SETTINGS_ROW_ID,
        invalidate_engine_settings_cache,
    )

    invalidate_engine_settings_cache()
    value: dict[str, Any] = {"branding": {"product_name": "XYZ AI"}}
    if trust:
        value["trust"] = trust
    world.db.add(EngineSettingsRow(id=SETTINGS_ROW_ID, value=value))
    with auth_app(world) as app:
        yield app
    invalidate_engine_settings_cache()


def make_request(
    world: World,
    number: int,
    *,
    tenant: Any = None,
    subject: str = "WhatsApp button not showing",
    status: str = "open",
    age_minutes: int = 120,
    answered_after: int | None = None,
) -> SupportRequest:
    tenant = tenant or world.shree
    created = datetime.now(UTC) - timedelta(minutes=age_minutes)
    row = world.db.add(
        SupportRequest(
            tenant_id=tenant.id,
            number=number,
            subject=subject,
            status=status,
            context={"plan": "Standard", "page": "/widget", "kb_count": 1},
            created_by_email="asha@shree.example",
            created_at=created,
            last_message_at=created,
            first_response_at=(
                created + timedelta(minutes=answered_after) if answered_after is not None else None
            ),
        )
    )
    world.db.add(
        SupportMessage(
            request_id=row.id,
            author_kind="business",
            author_email="asha@shree.example",
            body="Since yesterday the widget has no button.",
            created_at=created,
        )
    )
    return row


async def admin(app: Any, method: str, path: str, **kw: Any) -> Any:
    return await req(app, method, BASE + path, key=ADMIN, **kw)


async def test_the_inbox_lists_every_workspace_with_filters_and_search(world: World) -> None:
    a = make_request(world, 1042, age_minutes=10)
    make_request(world, 1043, tenant=world.pune, subject="Invoice question", status="answered")
    with app_for(world) as app:
        everything = await admin(app, "GET", "/requests")
        only_open = await admin(app, "GET", "/requests?status=open")
        one_workspace = await admin(app, "GET", f"/requests?tenant_id={world.pune.id}")
        by_number = await admin(app, "GET", "/requests?q=%231042")
        by_words = await admin(app, "GET", "/requests?q=invoice")
        by_name = await admin(app, "GET", "/requests?q=pune")
    assert everything.json()["total"] == 2
    assert {i["workspace_name"] for i in everything.json()["items"]} == {
        "Shree Labs",
        "Pune Agency",
    }
    assert [i["id"] for i in only_open.json()["items"]] == [str(a.id)]
    assert one_workspace.json()["total"] == 1
    assert [i["number"] for i in by_number.json()["items"]] == [1042]
    assert [i["number"] for i in by_words.json()["items"]] == [1043]
    assert [i["number"] for i in by_name.json()["items"]] == [1043]


async def test_the_detail_carries_the_workspace_information_the_business_sent(
    world: World,
) -> None:
    row = make_request(world, 1042)
    with app_for(world) as app:
        res = await admin(app, "GET", f"/requests/{row.id}")
    body = res.json()
    assert body["context"] == {"plan": "Standard", "page": "/widget", "kb_count": 1}
    assert body["workspace_name"] == "Shree Labs"
    assert body["messages"][0]["author_email"] == "asha@shree.example"


async def test_a_reply_sets_the_first_response_and_mails_the_business(world: World) -> None:
    row = make_request(world, 1042)
    with app_for(world) as app:
        res = await admin(
            app,
            "POST",
            f"/requests/{row.id}/messages",
            json={"body": "Fixed: your plan now includes the widget.", "status": "answered"},
        )
    assert res.status_code == 201, res.text
    assert row.status == "answered" and row.first_response_at is not None
    sent = sorted((t, to) for t, to, _ in world.mail)
    assert sent == [
        ("support_reply", "asha@shree.example"),
        ("support_reply", "meera@shree.example"),
    ]  # the author is an owner too: one mail each, never twice
    ctx = world.mail[0][2]
    assert ctx["product_name"] == "XYZ AI" and ctx["number"] == 1042
    assert [a.details["status"] for a in world.audits("support.request_status_changed")] == [
        "answered"
    ]
    assert res.json()["messages"][-1]["author_kind"] == "deployer"


async def test_a_second_reply_does_not_move_the_first_response_time(world: World) -> None:
    row = make_request(world, 1042, answered_after=30)
    first = row.first_response_at
    with app_for(world) as app:
        await admin(
            app,
            "POST",
            f"/requests/{row.id}/messages",
            json={"body": "One more thing.", "status": "waiting"},
        )
    assert row.first_response_at == first and row.status == "waiting"


async def test_a_reply_can_close_the_request(world: World) -> None:
    row = make_request(world, 1042)
    with app_for(world) as app:
        await admin(
            app, "POST", f"/requests/{row.id}/messages", json={"body": "Done.", "status": "closed"}
        )
    assert row.status == "closed" and row.closed_at is not None


async def test_the_status_can_be_set_and_an_unchanged_one_is_not_audited(world: World) -> None:
    row = make_request(world, 1042)
    with app_for(world) as app:
        waiting = await admin(app, "PATCH", f"/requests/{row.id}", json={"status": "waiting"})
        again = await admin(app, "PATCH", f"/requests/{row.id}", json={"status": "waiting"})
        bad = await admin(app, "PATCH", f"/requests/{row.id}", json={"status": "done"})
    assert waiting.status_code == 200 and again.status_code == 200 and bad.status_code == 422
    assert len(world.audits("support.request_status_changed")) == 1


async def test_unknown_request_is_404(world: World) -> None:
    import uuid

    with app_for(world) as app:
        res = await admin(app, "GET", f"/requests/{uuid.uuid4()}")
    assert res.status_code == 404 and res.json()["code"] == "support_request_not_found"


async def test_the_numbers_are_median_and_within_the_promise(world: World) -> None:
    for number, wait in ((1, 30), (2, 90), (3, 600)):
        make_request(world, number, status="answered", age_minutes=2000, answered_after=wait)
    make_request(world, 4, age_minutes=5)  # waiting for the provider, not yet late
    with app_for(world, support_sla_hours=4) as app:
        res = await admin(app, "GET", "/stats?days=30")
    assert res.json() == {
        "open": 1,
        "answered": 3,
        "median_first_response_minutes": 90,
        "within_sla_pct": 66,
        "sla_hours": 4,
        "days": 30,
    }


async def test_without_a_promise_there_is_no_percentage(world: World) -> None:
    make_request(world, 1, status="answered", answered_after=30)
    with app_for(world) as app:
        res = await admin(app, "GET", "/stats")
    assert res.json()["within_sla_pct"] is None and res.json()["sla_hours"] is None


async def test_only_the_admin_may_use_the_inbox(world: World) -> None:
    row = make_request(world, 1042)
    with app_for(world) as app:
        token = (await login(app, "asha@shree.example")).json()["access_token"]
        for method, path, body in (
            ("GET", "/requests", None),
            ("GET", f"/requests/{row.id}", None),
            ("POST", f"/requests/{row.id}/messages", {"body": "hello"}),
            ("PATCH", f"/requests/{row.id}", {"status": "closed"}),
            ("GET", "/stats", None),
        ):
            as_owner = await req(app, method, BASE + path, token=token, json=body)
            anonymous = await req(app, method, BASE + path, json=body)
            assert as_owner.status_code == 403, (method, path)
            assert anonymous.status_code in (401, 403), (method, path)
    assert row.status == "open" and world.mail == []


# ---------------------------------------------------------------- the pure numbers


def _row(created_minutes_ago: int, answered_after: int | None) -> stats.Row:
    created = NOW - timedelta(minutes=created_minutes_ago)
    return stats.Row(
        created, created + timedelta(minutes=answered_after) if answered_after is not None else None
    )


def test_median_and_share_within_the_promise() -> None:
    rows = [_row(5000, 30), _row(5000, 90), _row(5000, 600)]
    result = stats.compute(rows, sla_hours=4, now=NOW)
    assert (result.answered, result.median_first_response_minutes, result.within_sla_pct) == (
        3,
        90,
        66,
    )


def test_an_unanswered_request_past_the_promise_is_a_miss_and_one_inside_it_is_not() -> None:
    late = stats.compute([_row(30, 10), _row(300, None)], sla_hours=4, now=NOW)
    assert late.within_sla_pct == 50
    early = stats.compute([_row(30, 10), _row(60, None)], sla_hours=4, now=NOW)
    assert early.within_sla_pct == 100


def test_nothing_answered_has_no_median() -> None:
    result = stats.compute([_row(10, None)], sla_hours=None, now=NOW)
    assert result == stats.Stats(0, None, None)


async def test_the_inbox_pages_in_the_database_newest_activity_first(world: World) -> None:
    """Order, count and page are SQL: with more requests than one page, the newest are the ones
    shown and the total is every match (no arbitrary cap)."""
    for n in range(1, 8):
        make_request(world, 2000 + n, age_minutes=1000 - n * 10)  # higher number = newer
    with app_for(world) as app:
        first = await admin(app, "GET", "/requests?limit=3")
        second = await admin(app, "GET", "/requests?limit=3&offset=3")
    assert first.json()["total"] == 7
    assert [i["number"] for i in first.json()["items"]] == [2007, 2006, 2005]
    assert [i["number"] for i in second.json()["items"]] == [2004, 2003, 2002]


async def test_search_treats_percent_and_underscore_as_plain_text(world: World) -> None:
    make_request(world, 1, subject="Refund of 100% fee")
    make_request(world, 2, subject="Widget not showing")
    with app_for(world) as app:
        pct = await admin(app, "GET", "/requests?q=100%25")
        under = await admin(app, "GET", "/requests?q=_idget")
    assert [i["number"] for i in pct.json()["items"]] == [1]
    assert under.json()["total"] == 0


async def test_the_numbers_count_in_the_database(world: World) -> None:
    make_request(world, 1, status="open", age_minutes=60, answered_after=10)
    make_request(world, 2, status="open", age_minutes=60 * 24 * 90)  # outside 30 days
    make_request(world, 3, status="answered", age_minutes=30)
    with app_for(world) as app:
        res = await admin(app, "GET", "/stats?days=30")
    body = res.json()
    assert body["open"] == 2 and body["answered"] == 1


async def test_a_new_request_locks_the_workspace_before_counting_the_day(world: World) -> None:
    """The daily limit's count and the insert are one step per workspace, so parallel requests
    cannot all read nine."""
    from callobuzz_rag.support import service

    session = world.db.session()
    seen: list[str] = []
    original = session.execute

    async def spy(stmt: Any, params: Any = None) -> Any:
        seen.append(
            str(
                stmt.compile(
                    dialect=__import__("sqlalchemy.dialects.postgresql", fromlist=["x"]).dialect()
                )
            )
        )
        return await original(stmt, params)

    session.execute = spy  # type: ignore[method-assign]
    with (
        patch("callobuzz_rag.support.service.build_context", AsyncMock(return_value={})),
        patch("callobuzz_rag.support.service.next_number", AsyncMock(return_value=77)),
    ):
        await service.create_request(
            session,  # type: ignore[arg-type]
            world.shree,
            subject="Help",
            message="Please",
            page=None,
            client_errors=[],
            author_email="asha@shree.example",
            author_user_id=None,
        )
    assert "FOR UPDATE" in seen[0] and "tenants" in seen[0]
    assert "support_requests" in seen[1]


# ---------------------------------------------------------------- a reply never reaches an ex-member


async def reply_recipients(world: World, row: SupportRequest) -> list[str]:
    from callobuzz_rag.support import notify

    queue: list[str] = []
    await notify.notify_reply(
        world.db.session(),
        row,
        world.db.all(SupportMessage)[0],
        world.shree,
        enqueue=lambda template, to, ctx, **_: queue.append(to),
        product_name="XYZ AI",
        dashboard_url="http://x",
    )
    return sorted(queue)


async def test_a_reply_goes_to_the_author_only_while_still_a_member(world: World) -> None:
    from callobuzz_rag.db.models.tenant_member import TenantMember

    ravi = world.user("ravi@shree.example")
    world.member(ravi, world.shree, "member")
    row = make_request(world, 1042)
    row.created_by_email = "ravi@shree.example"
    row.created_by_user_id = ravi.id
    assert await reply_recipients(world, row) == [
        "asha@shree.example",
        "meera@shree.example",
        "ravi@shree.example",
    ]
    for member in world.db.all(TenantMember):  # ravi leaves the team
        if member.user_id == ravi.id:
            world.db.rows[TenantMember].remove(member)
    assert await reply_recipients(world, row) == ["asha@shree.example", "meera@shree.example"]


async def test_a_reply_never_goes_to_an_author_who_is_not_a_known_member(world: World) -> None:
    """A deleted account (`created_by_user_id` SET NULL) or a key's label is not a member."""
    row = make_request(world, 1042)
    row.created_by_email = "former@shree.example"
    row.created_by_user_id = None
    assert await reply_recipients(world, row) == ["asha@shree.example", "meera@shree.example"]
