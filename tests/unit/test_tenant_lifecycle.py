"""Tenant lifecycle: activate, suspend, reactivate, decline, hard delete (accounts A4).

The admin routes through the real app and middleware on the in-memory database of
`accounts_fakes.py`; the response's aggregate columns (owner e-mails, counts) are
PostgreSQL subqueries, so `_admin_tenant` is replaced by a plain read here and covered by
the integration test.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts import lifecycle
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.tenant import AdminTenantResponse
from callobuzz_rag.db.models.api_key import ApiKey
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from callobuzz_rag.db.models.user_session import UserSession
from tests.unit.accounts_fakes import request_stub
from tests.unit.test_auth_routes import ADMIN, SA_EMAIL, World, auth_app, login, req


@pytest.fixture
def world() -> World:
    return World()


@contextmanager
def admin_app(world: World, **settings: Any) -> Iterator[tuple[Any, AsyncMock]]:
    async def plain(session: Any, tenant_id: uuid.UUID) -> AdminTenantResponse:
        t = await session.get(Tenant, tenant_id)
        return AdminTenantResponse(
            id=t.id,
            name=t.name,
            description=t.description,
            corpus_count=0,
            max_corpora=t.max_corpora,
            effective_max_corpora=t.max_corpora,
            plan_id=t.plan_id,
            plan_name=world.db.default_plan.name,
            status=t.status,
            is_active=t.is_active,
            is_operator=t.is_operator,
            has_api_key=any(k.tenant_id == t.id for k in world.db.all(ApiKey)),
            activated_at=t.activated_at,
            created_at=t.created_at,
            activation_note=t.activation_note,
            created_by_user_id=t.created_by_user_id,
            owner_emails=[],
            member_count=0,
            monthly_questions=None,
            questions_this_month=0,
        )

    cleanup = AsyncMock(return_value=True)
    with (
        auth_app(world, **settings) as app,
        patch("callobuzz_rag.api.routes.tenants._admin_tenant", plain),
        patch("callobuzz_rag.storage.cleanup.schedule_prefix_cleanup", cleanup),
    ):
        app.state.vector_store = AsyncMock()
        yield app, cleanup


async def admin(app: Any, method: str, path: str, **kw: Any) -> Any:
    return await req(app, method, f"/api/v1/admin/tenants{path}", key=ADMIN, **kw)


def owner_of(world: World, tenant: Tenant, email: str, *, role: str = "owner") -> User:
    u = world.user(email)
    world.member(u, tenant, role)
    return u


def status_of(world: World, tenant: Tenant) -> str | None:
    found = next((t for t in world.db.all(Tenant) if t.id == tenant.id), None)
    return found.status if found else None


# ------------------------------------------------------------- transitions


@pytest.mark.parametrize(
    ("start", "action", "body", "outcome"),
    [
        ("pending", "activate", {}, "active"),
        ("active", "activate", {}, 409),
        ("suspended", "activate", {}, 409),
        ("active", "suspend", {"confirm_name": "Shree Labs"}, "suspended"),
        ("pending", "suspend", {"confirm_name": "Shree Labs"}, 409),
        ("suspended", "suspend", {"confirm_name": "Shree Labs"}, 409),
        ("suspended", "reactivate", None, "active"),
        ("active", "reactivate", None, 409),
        ("pending", "reactivate", None, 409),
        ("active", "decline", None, 409),
        ("suspended", "decline", None, 409),
    ],
)
async def test_transitions(
    world: World, start: str, action: str, body: dict[str, Any] | None, outcome: Any
) -> None:
    world.shree.status = start
    with admin_app(world) as (app, _):
        res = await admin(app, "POST", f"/{world.shree.id}/{action}", json=body)
    if outcome == 409:
        assert res.status_code == 409, res.text
        assert res.json()["code"] == "invalid_status_transition"
        assert res.json()["from"] == start
        assert status_of(world, world.shree) == start
    else:
        assert res.status_code == 200, res.text
        assert res.json()["status"] == outcome
        assert res.json()["is_active"] is (outcome == "active")


async def test_unknown_tenant_is_404(world: World) -> None:
    with admin_app(world) as (app, _):
        for action in ("activate", "reactivate", "decline"):
            res = await admin(app, "POST", f"/{uuid.uuid4()}/{action}")
            assert res.status_code == 404, action


async def test_activate_with_a_note_mails_the_owners(world: World) -> None:
    world.shree.status = "pending"
    owner_of(world, world.shree, "asha@shree.example")
    owner_of(world, world.shree, "ravi@shree.example", role="member")
    with admin_app(world) as (app, _):
        res = await admin(
            app, "POST", f"/{world.shree.id}/activate", json={"note": "paid via UPI 29 Sep"}
        )
    assert res.status_code == 200, res.text
    assert res.json()["activation_note"] == "paid via UPI 29 Sep"
    assert world.shree.activated_at is not None
    assert [(m[0], m[1]) for m in world.mail] == [("activated", "asha@shree.example")]
    # The mail names the plan (accounts A6): the default plan it signed up on.
    assert world.mail[0][2]["reactivated"] is False and world.mail[0][2]["plan_name"] == "Standard"
    (audit,) = world.audits("tenant.activated")
    assert audit.details == {"note": "paid via UPI 29 Sep"}


async def test_activate_note_is_bounded(world: World) -> None:
    world.shree.status = "pending"
    with admin_app(world) as (app, _):
        res = await admin(app, "POST", f"/{world.shree.id}/activate", json={"note": "x" * 1001})
    assert res.status_code == 422


# ------------------------------------------------------------ confirm name


@pytest.mark.parametrize(
    "typed", [None, "", "shree labs", "SHREE LABS", "Shree Lab", "Pune Agency"]
)
async def test_suspend_needs_the_exact_name(world: World, typed: str | None) -> None:
    body = {} if typed is None else {"confirm_name": typed}
    with admin_app(world) as (app, _):
        res = await admin(app, "POST", f"/{world.shree.id}/suspend", json=body)
    assert res.status_code == 422, res.text
    if typed is not None:
        assert res.json()["code"] == "confirm_mismatch"
    assert status_of(world, world.shree) == "active"


async def test_suspend_forgives_surrounding_spaces(world: World) -> None:
    with admin_app(world) as (app, _):
        res = await admin(
            app, "POST", f"/{world.shree.id}/suspend", json={"confirm_name": "  Shree Labs \n"}
        )
    assert res.status_code == 200 and res.json()["status"] == "suspended"


async def test_put_is_active_false_needs_confirm_name(world: World) -> None:
    with admin_app(world) as (app, _):
        missing = await admin(app, "PUT", f"/{world.shree.id}", json={"is_active": False})
        wrong = await admin(
            app, "PUT", f"/{world.shree.id}", json={"is_active": False, "confirm_name": "x"}
        )
        assert status_of(world, world.shree) == "active"
        ok = await admin(
            app,
            "PUT",
            f"/{world.shree.id}",
            json={"is_active": False, "confirm_name": "Shree Labs", "name": "Shree Labs Pvt"},
        )
    assert missing.status_code == 422 and missing.json()["code"] == "confirm_mismatch"
    assert wrong.status_code == 422 and wrong.json()["code"] == "confirm_mismatch"
    assert ok.status_code == 200 and ok.json()["status"] == "suspended"
    assert ok.json()["name"] == "Shree Labs Pvt"  # the name is checked before the rename


async def test_put_is_active_true_reactivates_and_repeats_are_no_ops(world: World) -> None:
    world.shree.status = "suspended"
    with admin_app(world) as (app, _):
        res = await admin(app, "PUT", f"/{world.shree.id}", json={"is_active": True})
        again = await admin(app, "PUT", f"/{world.shree.id}", json={"is_active": True})
    assert res.json()["status"] == "active" and again.status_code == 200
    assert len(world.audits("tenant.reactivated")) == 1


async def test_put_cannot_activate_a_pending_tenant(world: World) -> None:
    world.shree.status = "pending"
    with admin_app(world) as (app, _):
        res = await admin(app, "PUT", f"/{world.shree.id}", json={"is_active": True})
    assert res.status_code == 409 and res.json()["code"] == "invalid_status_transition"


async def test_reactivate_needs_no_name_and_mails(world: World) -> None:
    world.shree.status = "suspended"
    owner_of(world, world.shree, "asha@shree.example")
    with admin_app(world) as (app, _):
        res = await admin(app, "POST", f"/{world.shree.id}/reactivate")
    assert res.status_code == 200
    assert [(m[0], m[2]["reactivated"]) for m in world.mail] == [("activated", True)]


# ------------------------------------------------------------------ suspend


async def test_suspend_revokes_sessions_and_mails_owners(world: World) -> None:
    asha = owner_of(world, world.shree, "asha@shree.example")
    with admin_app(world) as (app, _):
        signed_in = await login(app, asha.email)
        token = signed_in.json()["access_token"]
        before = await req(app, "GET", "/api/v1/whoami", token=token)
        res = await admin(
            app,
            "POST",
            f"/{world.shree.id}/suspend",
            json={"confirm_name": "Shree Labs", "reason": "unpaid"},
        )
        after = await req(app, "GET", "/api/v1/whoami", token=token)
    assert before.status_code == 200 and res.status_code == 200
    assert after.status_code == 401 and after.json()["code"] == "session_revoked"
    (row,) = world.sessions(asha)
    assert row.revoked_reason == "tenant_suspended"
    assert [(m[0], m[1]) for m in world.mail] == [("suspended", "asha@shree.example")]
    (audit,) = world.audits("tenant.suspended")
    assert audit.details == {"sessions_revoked": 1, "reason": "unpaid"}


async def test_no_mail_when_the_transaction_rolls_back(world: World) -> None:
    owner_of(world, world.shree, "asha@shree.example")

    async def failing_commit(self: Any) -> None:
        raise RuntimeError("database went away")

    with (
        admin_app(world) as (app, _),
        patch("tests.unit.accounts_fakes.FakeSession.commit", failing_commit),
        pytest.raises(RuntimeError),
    ):
        await admin(app, "POST", f"/{world.shree.id}/suspend", json={"confirm_name": "Shree Labs"})
    assert world.mail == []


async def test_service_rules_hold_without_the_routes(world: World) -> None:
    """A future payment webhook calls the service directly: same rules."""
    from callobuzz_rag.core.config import Settings

    request = request_stub(Settings(_env_file=None))
    session = world.db.session()
    with pytest.raises(APIError) as caught:
        await lifecycle.suspend(
            session, world.shree, confirm_name="shree labs", reason=None, request=request
        )
    assert caught.value.code == "confirm_mismatch"
    with pytest.raises(APIError) as caught:
        await lifecycle.delete_tenant(session, world.shree, confirm_name=None, request=request)
    assert caught.value.code == "confirm_mismatch"
    assert status_of(world, world.shree) == "active"


# ------------------------------------------------------------------ decline


async def test_decline_removes_the_tenant_and_its_orphan_signup(world: World) -> None:
    world.shree.status = "pending"
    asha = owner_of(world, world.shree, "asha@shree.example")
    world.shree.created_by_user_id = asha.id
    both = owner_of(world, world.shree, "both@shree.example")
    world.member(both, world.pune, "member")  # still belongs elsewhere: kept
    corpus = world.db.add(
        Corpus(name="kb", tenant_id=world.shree.id, qdrant_collection="corpus_x", api_key_hash="h")
    )
    with admin_app(world) as (app, cleanup):
        res = await admin(app, "POST", f"/{world.shree.id}/decline", json={"reason": "spam"})
        gone = await login(app, asha.email)
    assert res.status_code == 204, res.text
    assert status_of(world, world.shree) is None
    assert world.db.all(Corpus) == []
    app.state.vector_store.delete_collection.assert_awaited_once_with("corpus_x")
    cleanup.assert_awaited_once()
    assert cleanup.call_args.args[1] == [f"corpus/{corpus.id}/", f"portal/{corpus.id}/"]
    emails = {u.email for u in world.db.all(User)}
    assert "asha@shree.example" not in emails and "both@shree.example" in emails
    assert gone.status_code == 401 and gone.json()["code"] == "invalid_credentials"
    assert sorted((m[0], m[1]) for m in world.mail) == [
        ("declined", "asha@shree.example"),
        ("declined", "both@shree.example"),
    ]
    assert "spam" not in str(world.mail)  # the reason is not e-mailed
    (audit,) = world.audits("tenant.declined")
    assert audit.details["reason"] == "spam" and audit.tenant_label == "Shree Labs"
    assert world.audits("tenant.deleted") == []


async def test_decline_needs_no_body(world: World) -> None:
    world.shree.status = "pending"
    with admin_app(world) as (app, _):
        res = await admin(app, "POST", f"/{world.shree.id}/decline")
    assert res.status_code == 204


# ------------------------------------------------------------------- delete


async def test_delete_without_or_with_a_wrong_name_deletes_nothing(world: World) -> None:
    with admin_app(world) as (app, _):
        legacy = await admin(app, "DELETE", f"/{world.shree.id}")
        wrong = await admin(app, "DELETE", f"/{world.shree.id}", params={"confirm_name": "shree"})
    assert legacy.status_code == 422  # FastAPI's own: the parameter is required
    assert wrong.status_code == 422 and wrong.json()["code"] == "confirm_mismatch"
    assert status_of(world, world.shree) == "active"


@pytest.mark.parametrize("start", ["pending", "active", "suspended"])
async def test_delete_removes_everything_it_owns(world: World, start: str) -> None:
    world.shree.status = start
    asha = owner_of(world, world.shree, "asha@shree.example")
    shared = owner_of(world, world.shree, "shared@shree.example")
    world.member(shared, world.pune, "member")
    sa = world.user(SA_EMAIL, superadmin=True)
    world.member(sa, world.shree, "member")  # not possible via bootstrap; never deleted anyway
    kbs = [
        world.db.add(
            Corpus(
                name=n, tenant_id=world.shree.id, qdrant_collection=f"corpus_{n}", api_key_hash=""
            )
        )
        for n in ("a", "b")
    ]
    other = world.db.add(
        Corpus(name="p", tenant_id=world.pune.id, qdrant_collection="corpus_p", api_key_hash="")
    )
    world.db.add(
        UserSession(
            user_id=asha.id,
            tenant_id=world.shree.id,
            refresh_token_hash="x",
            expires_at=asha.created_at,
            absolute_expires_at=asha.created_at,
        )
    )
    with admin_app(world) as (app, cleanup):
        res = await admin(
            app, "DELETE", f"/{world.shree.id}", params={"confirm_name": "Shree Labs"}
        )
    assert res.status_code == 204, res.text
    assert status_of(world, world.shree) is None
    assert world.db.all(Corpus) == [other]
    dropped = sorted(c.args[0] for c in app.state.vector_store.delete_collection.await_args_list)
    assert dropped == ["corpus_a", "corpus_b"]
    assert cleanup.await_count == 2
    assert {tuple(c.args[1]) for c in cleanup.await_args_list} == {
        (f"corpus/{k.id}/", f"portal/{k.id}/") for k in kbs
    }
    emails = {u.email for u in world.db.all(User)}
    assert "asha@shree.example" not in emails  # orphan: removed
    assert {"shared@shree.example", SA_EMAIL} <= emails  # other workspace / super admin: kept
    assert not [m for m in world.db.all(TenantMember) if m.tenant_id == world.shree.id]
    (audit,) = world.audits("tenant.deleted")
    assert audit.details == {"knowledge_bases_deleted": 2, "accounts_deleted": 1}
    assert world.mail == []


async def test_delete_does_not_touch_storage_before_the_commit(world: World) -> None:
    world.db.add(
        Corpus(name="a", tenant_id=world.shree.id, qdrant_collection="corpus_a", api_key_hash="")
    )
    order: list[str] = []
    from tests.unit.accounts_fakes import FakeSession

    original = FakeSession.commit

    async def commit(self: Any) -> None:
        order.append("commit")
        await original(self)

    with admin_app(world) as (app, _), patch.object(FakeSession, "commit", commit):
        app.state.vector_store.delete_collection = AsyncMock(
            side_effect=lambda name: order.append(f"drop {name}")
        )
        res = await admin(
            app, "DELETE", f"/{world.shree.id}", params={"confirm_name": "Shree Labs"}
        )
    assert res.status_code == 204
    assert order == ["commit", "drop corpus_a"]


async def test_lifecycle_routes_are_admin_only(world: World) -> None:
    asha = owner_of(world, world.shree, "asha@shree.example")
    with admin_app(world) as (app, _):
        token = (await login(app, asha.email)).json()["access_token"]
        res = await req(
            app,
            "POST",
            f"/api/v1/admin/tenants/{world.shree.id}/suspend",
            token=token,
            json={"confirm_name": "Shree Labs"},
        )
    assert res.status_code == 403
