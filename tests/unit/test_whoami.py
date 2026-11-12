"""GET /api/v1/whoami tells each kind of key what it is and what it reaches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from callobuzz_rag.api.routes.identity import whoami

TENANT_ID, CORPUS_ID = uuid4(), uuid4()


def _request(**state: object) -> MagicMock:
    request = MagicMock()
    request.state = SimpleNamespace(
        is_admin=state.get("admin", False),
        tenant=state.get("tenant"),
        corpus=state.get("corpus"),
        user=state.get("user"),
        role=state.get("role"),
        auth_kind=state.get("auth_kind", "api_key"),
    )
    return request


def _session(tenant: object | None = None) -> AsyncMock:
    session = AsyncMock()
    session.get = AsyncMock(return_value=tenant)
    return session


async def test_admin_key() -> None:
    result = await whoami(_request(admin=True), _session())
    assert result.kind == "admin"
    assert result.tenant_id is None and result.corpus_id is None
    assert result.auth == "api_key" and result.user is None and result.role is None


async def test_tenant_key_acts_as_owner() -> None:
    tenant = SimpleNamespace(id=TENANT_ID, name="Pune Agency")
    result = await whoami(_request(tenant=tenant, role="owner"), _session())
    assert result.role == "owner" and result.auth == "api_key"


async def test_super_admin_session() -> None:
    user = SimpleNamespace(id=uuid4(), email="ops@operator.example", name="Ops")
    result = await whoami(_request(admin=True, user=user, auth_kind="session"), _session())
    assert result.kind == "admin" and result.auth == "session" and result.role is None
    assert result.user is not None and result.user.email == "ops@operator.example"


async def test_tenant_session() -> None:
    tenant = SimpleNamespace(id=TENANT_ID, name="Shree Labs")
    user = SimpleNamespace(id=uuid4(), email="asha@shree.example", name="Asha")
    result = await whoami(
        _request(tenant=tenant, user=user, role="member", auth_kind="session"), _session()
    )
    assert result.kind == "tenant" and result.auth == "session" and result.role == "member"
    assert result.tenant_id == TENANT_ID and result.tenant_name == "Shree Labs"
    assert result.user is not None and result.user.name == "Asha"


async def test_tenant_key() -> None:
    tenant = SimpleNamespace(id=TENANT_ID, name="Pune Agency")
    result = await whoami(_request(tenant=tenant), _session())
    assert result.kind == "tenant"
    assert result.tenant_id == TENANT_ID and result.tenant_name == "Pune Agency"
    assert result.corpus_id is None


async def test_corpus_key_learns_its_own_corpus_and_tenant() -> None:
    corpus = SimpleNamespace(id=CORPUS_ID, name="Shree Labs", tenant_id=TENANT_ID)
    tenant = SimpleNamespace(id=TENANT_ID, name="Pune Agency")
    result = await whoami(_request(corpus=corpus), _session(tenant))
    assert result.kind == "corpus"
    assert result.corpus_id == CORPUS_ID and result.corpus_name == "Shree Labs"
    assert result.tenant_id == TENANT_ID and result.tenant_name == "Pune Agency"


async def test_no_auth_state_is_401() -> None:
    with pytest.raises(HTTPException) as exc:
        await whoami(_request(), _session())
    assert exc.value.status_code == 401
