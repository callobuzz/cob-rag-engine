"""POST /api/v1/auth/signup and the verify step's use of the stored mode (accounts A4).

Through the real app and middleware, on the in-memory database of `accounts_fakes.py`.
"""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.core.exceptions import EmailQueueError
from callobuzz_rag.db.models.email_code import EmailCode
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from tests.unit.test_auth_routes import SA_EMAIL, World, auth_app, req

PW = "saffron-kettle-orbit"
# A production engine: real SMTP, and a super-admin address on a real domain.
PROD: dict[str, Any] = {
    "env": "prod",
    "superadmin_email": "boss@operator.in",
    "smtp_host": "smtp.operator.in",
    "smtp_from": "no-reply@operator.in",
}


@pytest.fixture(autouse=True)
def _fresh_settings_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


@pytest.fixture
def world() -> World:
    return World()


def set_mode(world: World, mode: str, **branding: Any) -> None:
    world.db.rows[EngineSettingsRow] = []
    world.db.add(EngineSettingsRow(id=1, value={"signup_mode": mode, "branding": branding}))
    invalidate_engine_settings_cache()


async def signup(app: Any, email: str = "asha@shree.example", **extra: Any) -> Any:
    body = {"business_name": "Kaveri Foods", "name": "Asha", "email": email, "password": PW}
    return await req(app, "POST", "/api/v1/auth/signup", json={**body, **extra})


def code_for(world: World, email: str, template: str = "verify_code") -> str:
    sent = [c for t, to, c in world.mail if t == template and to == email]
    assert sent, world.mail
    return str(sent[-1]["code"])


async def verify(app: Any, world: World, email: str = "asha@shree.example") -> Any:
    code = code_for(world, email)
    return await req(app, "POST", "/api/v1/auth/verify", json={"email": email, "code": code})


def user(world: World, email: str) -> User | None:
    return next((u for u in world.db.all(User) if u.email == email), None)


# -------------------------------------------------------------------- modes


async def test_open_mode_signs_in_to_an_active_workspace(world: World) -> None:
    set_mode(world, "open")
    with auth_app(world) as app:
        res = await signup(app)
        assert res.status_code == 202, res.text
        assert res.json() == {"status": "verification_sent", "email": "asha@shree.example"}
        asha = user(world, "asha@shree.example")
        assert asha is not None and asha.email_verified_at is None
        assert not [t for t in world.db.all(Tenant) if t.name == "Kaveri Foods"]  # not yet
        done = await verify(app, world)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["access_token"] and body["refresh_token"].startswith("rt_")
    assert body["tenant"]["name"] == "Kaveri Foods"
    assert body["tenant"]["status"] == "active" and body["tenant"]["role"] == "owner"
    (tenant,) = [t for t in world.db.all(Tenant) if t.name == "Kaveri Foods"]
    assert tenant.status == "active" and tenant.activated_at is not None
    assert tenant.api_key_hash == ""  # no key nobody saw
    assert not [m for m in world.mail if m[0] == "signup_notice"]


async def test_approval_mode_is_pending_and_notifies_the_super_admin(world: World) -> None:
    set_mode(world, "approval")
    with auth_app(world) as app:
        assert (await signup(app)).status_code == 202
        done = await verify(app, world)
    assert done.status_code == 200, done.text
    assert done.json()["tenant"]["status"] == "pending"
    (tenant,) = [t for t in world.db.all(Tenant) if t.name == "Kaveri Foods"]
    assert tenant.status == "pending" and tenant.activated_at is None
    (notice,) = [m for m in world.mail if m[0] == "signup_notice"]
    assert notice[1] == SA_EMAIL
    assert notice[2]["tenant_name"] == "Kaveri Foods" and notice[2]["owner_email"] == (
        "asha@shree.example"
    )
    assert notice[2]["customers_url"].endswith("/admin/customers?status=pending")


async def test_closed_mode_refuses_with_the_contact_text(world: World) -> None:
    set_mode(
        world,
        "closed",
        contact_text="Write to us for pricing.",
        contact_url="https://sahayak.example/pricing",
    )
    with auth_app(world) as app:
        res = await signup(app)
    assert res.status_code == 403
    assert res.json() == {
        "detail": "Write to us for pricing.",
        "code": "signup_closed",
        "contact_url": "https://sahayak.example/pricing",
    }
    assert world.db.all(User) == [] and world.mail == []


async def test_the_mode_stored_at_signup_wins_at_verify(world: World) -> None:
    set_mode(world, "open")
    with auth_app(world) as app:
        assert (await signup(app)).status_code == 202
        set_mode(world, "approval")  # the operator switches before the code is typed
        done = await verify(app, world)
    assert done.json()["tenant"]["status"] == "active"


async def test_approval_signup_then_open_switch_stays_pending(world: World) -> None:
    set_mode(world, "approval")
    with auth_app(world) as app:
        assert (await signup(app)).status_code == 202
        set_mode(world, "open")
        done = await verify(app, world)
    assert done.json()["tenant"]["status"] == "pending"


# ------------------------------------------------------------- re-sign-up


async def test_unverified_re_signup_overwrites(world: World) -> None:
    with auth_app(world) as app:
        await signup(app, business_name="First Try", name="A")
        first = user(world, "asha@shree.example")
        assert first is not None
        first_hash = first.password_hash
        again = await signup(app, business_name="Kaveri Foods", name="Asha K", password=PW + "x")
        assert again.status_code == 202
        (only,) = world.db.all(User)
        assert only.name == "Asha K" and only.password_hash != first_hash
        open_codes = [c for c in world.db.all(EmailCode) if c.used_at is None]
        assert len(open_codes) == 1 and open_codes[0].payload["business_name"] == "Kaveri Foods"
        done = await verify(app, world)
    assert done.json()["tenant"]["name"] == "Kaveri Foods"


async def test_verified_address_gets_account_exists_and_an_identical_response(
    world: World,
) -> None:
    world.user("taken@shree.example")
    with auth_app(world) as app:
        new = await signup(app, email="fresh@shree.example")
        taken = await signup(app, email="taken@shree.example")
    assert (new.status_code, new.json()["status"]) == (taken.status_code, taken.json()["status"])
    assert set(new.json()) == set(taken.json())
    assert [m[0] for m in world.mail if m[1] == "taken@shree.example"] == ["account_exists"]
    exists = next(m for m in world.mail if m[1] == "taken@shree.example")[2]
    assert exists["login_url"].endswith("/login") and exists["reset_url"].endswith("/forgot")
    assert not [c for c in world.db.all(EmailCode) if c.email == "taken@shree.example"]


async def test_the_super_admin_address_is_never_created_or_overwritten(world: World) -> None:
    with auth_app(world) as app:
        res = await signup(app, email=SA_EMAIL)  # no super-admin row exists yet
    assert res.status_code == 202
    assert user(world, SA_EMAIL) is None
    assert [m[0] for m in world.mail] == ["account_exists"]


async def test_a_signup_hashes_the_password_in_every_branch(world: World) -> None:
    world.user("taken@shree.example")
    with (
        auth_app(world) as app,
        patch(
            "callobuzz_rag.accounts.signup.hash_password",
            new=AsyncMock(return_value="$argon2id$x"),
        ) as hashed,
    ):
        await signup(app, email="taken@shree.example")
        await signup(app, email="fresh@shree.example")
    assert hashed.await_count == 2


# ------------------------------------------------------------------ refusals


async def test_weak_password(world: World) -> None:
    with auth_app(world) as app:
        res = await signup(app, password="short")
    assert res.status_code == 422
    assert res.json()["code"] == "weak_password" and "too_short" in res.json()["reasons"]
    assert world.db.all(User) == []


@pytest.mark.parametrize(
    "bad", ["asha", "asha@", "asha@shree", "asha shree@kaveri.in", "@kaveri.in"]
)
async def test_a_malformed_address_is_a_422_and_writes_nothing(world: World, bad: str) -> None:
    with auth_app(world) as app:
        res = await signup(app, email=bad)
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "invalid_email"
    assert world.db.all(User) == [] and world.mail == []


async def test_a_reserved_name_signs_up_in_dev(world: World) -> None:
    with auth_app(world, env="dev") as app:
        res = await signup(app, email="asha@kaveri.local")
    assert res.status_code == 202, res.text
    assert res.json()["email"] == "asha@kaveri.local"


async def test_a_reserved_name_is_refused_in_prod_with_a_clear_message(world: World) -> None:
    with auth_app(world, **PROD) as app:
        res = await signup(app, email="asha@kaveri.local")
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["code"] == "invalid_email"
    assert "cannot receive mail" in body["detail"] and "RAG_ENV" not in body["detail"]
    assert world.db.all(User) == [] and world.mail == []


async def test_a_real_domain_signs_up_in_prod(world: World) -> None:
    with auth_app(world, **PROD) as app:
        res = await signup(app, email="asha@kaveri.in")
    assert res.status_code == 202, res.text


async def test_email_not_configured_writes_nothing(world: World) -> None:
    with auth_app(world, email_provider="disabled") as app:
        res = await signup(app)
    assert res.status_code == 503 and res.json()["code"] == "email_not_configured"
    assert world.db.all(User) == [] and world.db.all(EmailCode) == []


async def test_enqueue_failure_after_commit_is_503(world: World) -> None:
    def broken(*_: Any, **__: Any) -> None:
        raise EmailQueueError("broker down")

    with (
        auth_app(world) as app,
        patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", broken),
    ):
        res = await signup(app)
    assert res.status_code == 503 and res.json()["code"] == "email_unavailable"
    assert user(world, "asha@shree.example") is not None  # committed: "resend" works


async def test_signup_rate_limits(world: World) -> None:
    with auth_app(world) as app:
        codes = [(await signup(app)).status_code for _ in range(4)]
    assert codes == [202, 202, 202, 429]  # 3 per address per hour


async def test_signup_fails_closed_without_redis(world: World) -> None:
    world.redis.down = True
    with auth_app(world) as app:
        res = await signup(app)
    assert res.status_code == 503 and res.json()["code"] == "rate_limiter_unavailable"


async def test_accounts_not_configured(world: World) -> None:
    with auth_app(world, session_secret="") as app:
        res = await signup(app)
    assert res.status_code == 503 and res.json()["code"] == "accounts_not_configured"


async def test_verify_creates_one_owner_membership(world: World) -> None:
    with auth_app(world) as app:
        await signup(app)
        await verify(app, world)
    asha = user(world, "asha@shree.example")
    assert asha is not None
    (member,) = [m for m in world.db.all(TenantMember) if m.user_id == asha.id]
    assert member.role == "owner"
