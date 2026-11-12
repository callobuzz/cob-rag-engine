"""Support requests, the business's side (TV3): open one with the workspace information, read the
thread, follow up, close, the daily limit and the provider's switch."""

import itertools
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.support import SupportMessage, SupportRequest
from tests.unit.test_auth_routes import ADMIN, SA_EMAIL, World, auth_app, login, req

BASE = "/api/v1/support"


@pytest.fixture
def world() -> World:
    w = World()
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("ravi@shree.example"), w.shree, "member")
    w.member(w.user("dev@pune.example"), w.pune, "owner")
    return w


@contextmanager
def app_for(
    world: World, *, support_email: str | None = "help@xyz-ai.in", **trust: Any
) -> Iterator[Any]:
    from callobuzz_rag.accounts.settings_store import (
        SETTINGS_ROW_ID,
        invalidate_engine_settings_cache,
    )

    invalidate_engine_settings_cache()
    branding: dict[str, Any] = {"product_name": "XYZ AI"}
    if support_email:
        branding["support_email"] = support_email
    value: dict[str, Any] = {"branding": branding}
    if trust:
        value["trust"] = trust
    world.db.add(EngineSettingsRow(id=SETTINGS_ROW_ID, value=value))
    numbers = itertools.count(1042)

    async def next_number(session: Any) -> int:
        return next(numbers)

    with (
        auth_app(world) as app,
        patch("callobuzz_rag.support.service.next_number", next_number),
    ):
        yield app
    invalidate_engine_settings_cache()


async def call(app: Any, email: str, method: str, path: str, **kw: Any) -> Any:
    token = (await login(app, email)).json()["access_token"]
    return await req(app, method, BASE + path, token=token, **kw)


NEW = {
    "subject": "WhatsApp button not showing",
    "message": "Since yesterday the widget has no button.",
    "page": "/widget",
    "client_errors": ["assistant_disabled"],
}


async def test_a_business_creates_a_request_with_context(world: World) -> None:
    world.db.add(BillingAccount(tenant_id=world.shree.id, currency="INR", balance_minor=4200))
    world.db.add(Corpus(name="Clinic site", tenant_id=world.shree.id, api_key_hash="h"))
    with app_for(world) as app:
        res = await call(app, "asha@shree.example", "POST", "/requests", json=NEW)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "open" and body["number"] == 1042
    [row] = world.db.all(SupportRequest)
    ctx = row.context
    assert ctx["plan"] == "Standard" and ctx["page"] == "/widget"
    assert ctx["client_errors"] == ["assistant_disabled"]
    assert ctx["kb_count"] == 1 and ctx["workspace_status"] == "active"
    assert ctx["paused"] is False and ctx["product_version"]
    text = json.dumps(ctx)
    assert "balance" not in text and "api_key" not in text and "4200" not in text
    assert world.mail == [
        (
            "support_request_new",
            "help@xyz-ai.in",
            {
                "number": 1042,
                "request_subject": "WhatsApp button not showing",
                "business_name": "Shree Labs",
                "from_email": "asha@shree.example",
                "message": "Since yesterday the widget has no button.",
                "page": "/widget",
                "admin_url": world.mail[0][2]["admin_url"],
            },
        )
    ]
    [audit] = world.audits("support.request_created")
    assert audit.details == {"request_id": str(row.id), "number": 1042}
    assert "Since yesterday" not in json.dumps(audit.details)  # never the text


async def test_without_a_support_address_the_super_admin_is_mailed(world: World) -> None:
    with app_for(world, support_email=None) as app:
        res = await call(app, "asha@shree.example", "POST", "/requests", json=NEW)
    assert res.status_code == 201
    assert [to for _, to, _ in world.mail] == [SA_EMAIL]


async def test_other_workspaces_requests_are_invisible(world: World) -> None:
    with app_for(world) as app:
        created = await call(app, "asha@shree.example", "POST", "/requests", json=NEW)
        rid = created.json()["id"]
        other = await call(app, "dev@pune.example", "GET", f"/requests/{rid}")
        other_msg = await call(
            app, "dev@pune.example", "POST", f"/requests/{rid}/messages", json={"body": "hi"}
        )
        other_close = await call(app, "dev@pune.example", "POST", f"/requests/{rid}/close")
        listing = await call(app, "dev@pune.example", "GET", "/requests")
    for res in (other, other_msg, other_close):
        assert res.status_code == 404 and res.json()["code"] == "support_request_not_found"
    assert listing.json() == {"items": [], "total": 0}


async def test_a_member_can_open_and_follow_up(world: World) -> None:
    with app_for(world) as app:
        created = await call(app, "ravi@shree.example", "POST", "/requests", json=NEW)
        rid = created.json()["id"]
        owner_sees = await call(app, "asha@shree.example", "GET", f"/requests/{rid}")
        follow = await call(
            app,
            "ravi@shree.example",
            "POST",
            f"/requests/{rid}/messages",
            json={"body": "Still the same this morning."},
        )
    assert created.status_code == 201 and owner_sees.status_code == 200
    assert follow.status_code == 201 and follow.json()["author"] == "ravi@shree.example"
    assert [m["author_kind"] for m in owner_sees.json()["messages"]] == ["business"]


async def test_a_follow_up_reopens_a_closed_request_and_tells_the_provider(world: World) -> None:
    with app_for(world) as app:
        created = await call(app, "asha@shree.example", "POST", "/requests", json=NEW)
        rid = created.json()["id"]
        closed = await call(app, "asha@shree.example", "POST", f"/requests/{rid}/close")
        world.mail.clear()
        await call(
            app,
            "asha@shree.example",
            "POST",
            f"/requests/{rid}/messages",
            json={"body": "It came back."},
        )
        detail = await call(app, "asha@shree.example", "GET", f"/requests/{rid}")
    assert closed.json()["status"] == "closed" and closed.json()["closed_at"]
    assert detail.json()["status"] == "open" and detail.json()["closed_at"] is None
    assert [(t, to) for t, to, _ in world.mail] == [("support_request_new", "help@xyz-ai.in")]
    assert world.mail[0][2]["request_subject"].startswith("Re: ")
    changes = [a.details["status"] for a in world.audits("support.request_status_changed")]
    assert changes == ["closed", "open"]


async def test_the_eleventh_request_in_a_day_is_429_with_retry_after(world: World) -> None:
    now = datetime.now(UTC)
    for i in range(10):
        world.db.add(
            SupportRequest(
                tenant_id=world.shree.id,
                number=i + 1,
                subject=f"old {i}",
                status="open",
                context={},
                created_by_email="asha@shree.example",
                created_at=now - timedelta(hours=1 + i),
                last_message_at=now,
            )
        )
    with app_for(world) as app:
        res = await call(app, "asha@shree.example", "POST", "/requests", json=NEW)
        other = await call(app, "dev@pune.example", "POST", "/requests", json=NEW)
    assert res.status_code == 429 and res.json()["code"] == "support_rate_limited"
    assert 0 < int(res.headers["retry-after"]) <= 24 * 3600
    assert other.status_code == 201  # the limit is per workspace


async def test_support_off_is_409_and_info_says_so(world: World) -> None:
    with app_for(world, support_enabled=False) as app:
        info = await call(app, "asha@shree.example", "GET", "/info")
        res = await call(app, "asha@shree.example", "POST", "/requests", json=NEW)
    assert info.json()["enabled"] is False
    assert res.status_code == 409 and res.json()["code"] == "support_disabled"
    assert world.db.all(SupportRequest) == []


async def test_info_carries_the_promise_and_the_open_count(world: World) -> None:
    with app_for(world, support_sla_hours=4) as app:
        await call(app, "asha@shree.example", "POST", "/requests", json=NEW)
        info = await call(app, "asha@shree.example", "GET", "/info")
    assert info.json() == {
        "enabled": True,
        "provider_name": "XYZ AI",
        "sla_hours": 4,
        "open_count": 1,
    }


@pytest.mark.parametrize(
    "body",
    [
        {**NEW, "subject": "ab"},
        {**NEW, "subject": "x" * 151},
        {**NEW, "message": ""},
        {**NEW, "message": "x" * 10_001},
        {**NEW, "page": "widget"},
        {**NEW, "page": "/" + "x" * 300},
        {**NEW, "client_errors": ["a", "b", "c", "d"]},
        {**NEW, "client_errors": ["Not Valid!"]},
        {**NEW, "client_errors": ["x" * 41]},
        {**NEW, "attachment": "x"},
    ],
)
async def test_the_form_is_validated(world: World, body: dict[str, Any]) -> None:
    with app_for(world) as app:
        res = await call(app, "asha@shree.example", "POST", "/requests", json=body)
    assert res.status_code == 422


async def test_html_in_a_message_is_stored_as_text_and_escaped_in_mail(world: World) -> None:
    from callobuzz_rag.accounts.email.render import render_email
    from callobuzz_rag.accounts.settings_store import Branding

    evil = {**NEW, "message": "<script>alert(1)</script> hello"}
    with app_for(world) as app:
        res = await call(app, "asha@shree.example", "POST", "/requests", json=evil)
    assert res.status_code == 201
    [stored] = world.db.all(SupportMessage)
    assert stored.body == "<script>alert(1)</script> hello"
    name, to, ctx = world.mail[0]
    mail = render_email(name, to, ctx, Branding(), "https://dash.example")
    assert "<script>" not in mail.html and "&lt;script&gt;" in mail.html


async def test_a_workspace_key_for_a_knowledge_base_and_the_operator_have_no_workspace(
    world: World,
) -> None:
    with app_for(world) as app:
        admin = await req(app, "GET", BASE + "/info", key=ADMIN)
    assert admin.status_code == 403 and admin.json()["code"] == "no_workspace"


async def test_follow_ups_are_limited_per_workspace_per_hour(world: World) -> None:
    now = datetime.now(UTC)
    row = world.db.add(
        SupportRequest(
            tenant_id=world.shree.id,
            number=1,
            subject="old",
            status="open",
            context={},
            created_by_email="asha@shree.example",
            created_at=now - timedelta(days=2),
            last_message_at=now,
        )
    )
    for i in range(20):
        world.db.add(
            SupportMessage(
                request_id=row.id,
                author_kind="business",
                author_email="asha@shree.example",
                body=f"ping {i}",
                created_at=now - timedelta(minutes=1 + i),
            )
        )
    world.db.add(  # an older message does not count
        SupportMessage(
            request_id=row.id,
            author_kind="business",
            author_email="asha@shree.example",
            body="yesterday",
            created_at=now - timedelta(hours=3),
        )
    )
    path = f"/requests/{row.id}/messages"
    with app_for(world) as app:
        refused = await call(app, "asha@shree.example", "POST", path, json={"body": "again"})
        member = await call(app, "ravi@shree.example", "POST", path, json={"body": "again"})
        other = await call(app, "dev@pune.example", "POST", "/requests", json=NEW)
    for res in (refused, member):  # the limit is the workspace's, not the person's
        assert res.status_code == 429 and res.json()["code"] == "support_rate_limited"
        assert 0 < int(res.headers["retry-after"]) <= 3600
    assert other.status_code == 201  # another workspace is not affected
    assert [t for t, _, _ in world.mail] == ["support_request_new"]  # only the other's request
    assert [m for m in world.db.all(SupportMessage) if m.body == "again"] == []


async def test_a_follow_up_under_the_limit_is_accepted(world: World) -> None:
    with app_for(world) as app:
        token = (await login(app, "asha@shree.example")).json()["access_token"]

        async def post(path: str, body: dict[str, Any]) -> Any:
            return await req(app, "POST", BASE + path, token=token, json=body)

        created = await post("/requests", NEW)
        rid = created.json()["id"]
        sent = [await post(f"/requests/{rid}/messages", {"body": f"{i}"}) for i in range(19)]
        over = await post(f"/requests/{rid}/messages", {"body": "x"})
    assert all(r.status_code == 201 for r in sent)  # the first message plus 19 = 20
    assert over.status_code == 429
