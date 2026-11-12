"""/api/v1/auth/verify, /resend and /forgot (accounts A4)."""

from collections.abc import Iterator
from typing import Any

import pytest

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.db.models.email_code import EmailCode
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from tests.unit.test_auth_routes import SA_EMAIL, World, auth_app, login, req
from tests.unit.test_signup import code_for, signup, user

SENT = {"status": "sent_if_applicable"}


@pytest.fixture(autouse=True)
def _fresh_settings_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


@pytest.fixture
def world() -> World:
    return World()


async def post(app: Any, path: str, **body: Any) -> Any:
    return await req(app, "POST", f"/api/v1/auth/{path}", json=body)


def codes(world: World, email: str, purpose: str) -> list[EmailCode]:
    return [c for c in world.db.all(EmailCode) if c.email == email and c.purpose == purpose]


# ------------------------------------------------------------------- verify


async def test_verify_creates_tenant_owner_and_audit(world: World) -> None:
    with auth_app(world) as app:
        await signup(app)
        res = await post(
            app,
            "verify",
            email="ASHA@shree.example",
            code=code_for(world, "asha@shree.example"),
        )
    assert res.status_code == 200, res.text
    asha = user(world, "asha@shree.example")
    assert asha is not None and asha.email_verified_at is not None
    (tenant,) = [t for t in world.db.all(Tenant) if t.created_by_user_id == asha.id]
    assert tenant.name == "Kaveri Foods"
    (member,) = [m for m in world.db.all(TenantMember) if m.tenant_id == tenant.id]
    assert (member.user_id, member.role) == (asha.id, "owner")
    (audit,) = world.audits("tenant.signed_up")
    assert audit.tenant_id == tenant.id and audit.actor_user_id == asha.id
    assert audit.details["signup_mode"] == "approval"
    # signed in straight away, and login works afterwards too
    assert res.json()["user"]["email"] == "asha@shree.example"
    with auth_app(world) as app:
        again = await login(app, "asha@shree.example", password="saffron-kettle-orbit")
    assert again.status_code == 200 and again.json()["tenant"]["status"] == "pending"


async def test_wrong_code_five_times_expires_it(world: World) -> None:
    with auth_app(world) as app:
        await signup(app)
        right = code_for(world, "asha@shree.example")
        wrong = "000000" if right != "000000" else "111111"
        answers = [
            (await post(app, "verify", email="asha@shree.example", code=wrong)).json()["code"]
            for _ in range(5)
        ]
        late = await post(app, "verify", email="asha@shree.example", code=right)
    assert answers == ["invalid_code"] * 4 + ["code_expired"]
    assert late.status_code == 400 and late.json()["code"] == "code_expired"
    assert not [t for t in world.db.all(Tenant) if t.name == "Kaveri Foods"]


async def test_verify_without_a_code(world: World) -> None:
    with auth_app(world) as app:
        res = await post(app, "verify", email="nobody@shree.example", code="123456")
    assert res.status_code == 400 and res.json()["code"] == "code_expired"


async def test_a_code_works_once(world: World) -> None:
    with auth_app(world) as app:
        await signup(app)
        code = code_for(world, "asha@shree.example")
        first = await post(app, "verify", email="asha@shree.example", code=code)
        second = await post(app, "verify", email="asha@shree.example", code=code)
    assert first.status_code == 200
    assert second.status_code == 400 and second.json()["code"] == "code_expired"
    assert len([t for t in world.db.all(Tenant) if t.name == "Kaveri Foods"]) == 1


# ------------------------------------------------------------------- forgot


@pytest.mark.parametrize("who", ["unknown", "unverified", "inactive"])
async def test_forgot_sends_nothing_but_answers_the_same(world: World, who: str) -> None:
    email = f"{who}@shree.example"
    if who == "unverified":
        world.user(email, verified=False)
    elif who == "inactive":
        world.user(email, active=False)
    with auth_app(world) as app:
        res = await post(app, "forgot", email=email)
    assert (res.status_code, res.json()) == (202, SENT)
    assert codes(world, email, "reset") == [] and world.mail == []


async def test_forgot_for_a_verified_user_sends_a_reset_code(world: World) -> None:
    world.user("asha@shree.example")
    with auth_app(world) as app:
        res = await post(app, "forgot", email="asha@shree.example")
        code = code_for(world, "asha@shree.example", "reset_code")
        reset = await post(
            app,
            "reset",
            email="asha@shree.example",
            code=code,
            new_password="lantern-orchard-pebble",
        )
    assert (res.status_code, res.json()) == (202, SENT)
    assert len(codes(world, "asha@shree.example", "reset")) == 1
    assert reset.status_code == 200, reset.text


async def test_forgot_for_the_super_admin(world: World) -> None:
    world.user(SA_EMAIL, verified=False, superadmin=True)
    with auth_app(world) as app:
        res = await post(app, "forgot", email=SA_EMAIL)
    assert res.status_code == 202
    assert [m[0] for m in world.mail] == ["reset_code"]


# ------------------------------------------------------------------- resend


async def test_resend_verify_keeps_the_payload_and_supersedes(world: World) -> None:
    with auth_app(world) as app:
        await signup(app)
        first = code_for(world, "asha@shree.example")
        res = await post(app, "resend", email="asha@shree.example", purpose="verify")
        second = code_for(world, "asha@shree.example")
        old = await post(app, "verify", email="asha@shree.example", code=first)
        new = await post(app, "verify", email="asha@shree.example", code=second)
    assert (res.status_code, res.json()) == (202, SENT)
    assert old.status_code == 400
    assert new.status_code == 200 and new.json()["tenant"]["name"] == "Kaveri Foods"


@pytest.mark.parametrize("who", ["unknown", "verified"])
async def test_resend_verify_sends_nothing_when_not_applicable(world: World, who: str) -> None:
    email = f"{who}@shree.example"
    if who == "verified":
        world.user(email)
    with auth_app(world) as app:
        res = await post(app, "resend", email=email, purpose="verify")
    assert (res.status_code, res.json()) == (202, SENT)
    assert world.mail == []


async def test_resend_reset_is_forgot(world: World) -> None:
    world.user("asha@shree.example")
    with auth_app(world) as app:
        res = await post(app, "resend", email="asha@shree.example", purpose="reset")
    assert res.status_code == 202
    assert [m[0] for m in world.mail] == ["reset_code"]


async def test_resend_and_forgot_share_the_code_mail_limits(world: World) -> None:
    world.user("asha@shree.example")
    with auth_app(world) as app:
        first = await post(app, "forgot", email="asha@shree.example")
        second = await post(app, "resend", email="asha@shree.example", purpose="reset")
        world.redis.advance(61)
        third = await post(app, "resend", email="asha@shree.example", purpose="verify")
    assert first.status_code == 202
    assert second.status_code == 429 and second.json()["code"] == "rate_limited"
    assert third.status_code == 202  # a minute later


async def test_code_mail_hourly_limit(world: World) -> None:
    with auth_app(world) as app:
        answers = []
        for _ in range(6):
            answers.append((await post(app, "forgot", email="x@shree.example")).status_code)
            world.redis.advance(61)
    assert answers == [202] * 5 + [429]


async def test_forgot_email_not_configured(world: World) -> None:
    with auth_app(world, email_provider="disabled") as app:
        res = await post(app, "forgot", email="x@shree.example")
    assert res.status_code == 503 and res.json()["code"] == "email_not_configured"
