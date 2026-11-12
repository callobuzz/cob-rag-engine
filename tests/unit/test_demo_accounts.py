from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.accounts.passwords import verify_password
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from callobuzz_rag.demo import accounts
from tests.unit.accounts_fakes import FakeDB

PASSWORD = "Nirmal-Clinic-Demo-2026"
OPERATOR = "op@sahayak.example.com"
OWNER = "owner@nirmalclinic.example.com"


@pytest.fixture
def accounts_db() -> Any:
    db = FakeDB()

    def add_user(email: str, **kw: Any) -> User:
        values: dict[str, Any] = {"name": "x", "is_active": True, "is_superadmin": False, **kw}
        return db.add(User(email=email, **values))

    def add_tenant(name: str) -> Tenant:
        return db.add(Tenant(name=name, status="active", api_key_hash=""))

    def members(tenant_id: Any) -> list[TenantMember]:
        return [m for m in db.all(TenantMember) if m.tenant_id == tenant_id]

    return SimpleNamespace(
        db=db, session=db.session(), add_user=add_user, add_tenant=add_tenant, members=members
    )


async def test_operator_password_is_set_and_verified(accounts_db: Any) -> None:
    op = accounts_db.add_user(OPERATOR, is_superadmin=True, password_hash=None)
    got = await accounts.prepare_operator(accounts_db.session, OPERATOR.upper(), PASSWORD)
    assert got is op
    assert op.email_verified_at is not None and op.is_active and op.is_superadmin
    ok, _ = await verify_password(PASSWORD, op.password_hash)
    assert ok
    bad, _ = await verify_password("not-the-password", op.password_hash)
    assert not bad


async def test_missing_operator_is_an_error(accounts_db: Any) -> None:
    with pytest.raises(accounts.DemoSetupError, match="bootstrap"):
        await accounts.prepare_operator(accounts_db.session, OPERATOR, "x" * 12)


async def test_owner_is_created_once_and_joined(accounts_db: Any) -> None:
    tenant = accounts_db.add_tenant("Nirmal Family Clinic")
    for _ in range(2):
        user = await accounts.ensure_owner(
            accounts_db.session, tenant.id, OWNER, "Anjali Kulkarni", PASSWORD
        )
    members = accounts_db.members(tenant.id)
    assert [(m.user_id, m.role) for m in members] == [(user.id, "owner")]
    assert user.is_superadmin is False and user.email_verified_at is not None
    assert len([u for u in accounts_db.db.all(User) if u.email == OWNER]) == 1
    ok, _ = await verify_password(PASSWORD, user.password_hash)
    assert ok


async def test_an_existing_owner_row_is_never_made_super_admin(accounts_db: Any) -> None:
    tenant = accounts_db.add_tenant("Nirmal Family Clinic")
    accounts_db.add_user(OWNER, is_superadmin=True)
    user = await accounts.ensure_owner(
        accounts_db.session, tenant.id, OWNER, "Anjali Kulkarni", PASSWORD
    )
    assert user.is_superadmin is False


async def test_the_demo_never_commits(accounts_db: Any) -> None:
    tenant = accounts_db.add_tenant("Nirmal Family Clinic")
    await accounts.ensure_owner(accounts_db.session, tenant.id, OWNER, "Anjali", PASSWORD)
    assert accounts_db.db.commits == 0
