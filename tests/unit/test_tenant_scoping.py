"""Scope resolution and corpus authorization (deps.resolve_scope / authorized_corpus_ids).

test_admin_endpoint_scoping.py proves the endpoints DECLARE the dependency. This
proves the dependency actually refuses the right callers — the part that would be
a cross-tenant leak if it were wrong.

The rule these encode: `authorized_corpus_ids` returns None ONLY for admin. Every
other caller gets a concrete list, so a route that filters on the result cannot
accidentally fall through to unrestricted.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from callobuzz_rag.api.deps import Scope, authorized_corpus_ids, resolve_scope

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
CORPUS_A1 = uuid.uuid4()
CORPUS_A2 = uuid.uuid4()
CORPUS_B1 = uuid.uuid4()


def _request(*, is_admin=None, tenant=None, corpus=None):
    return SimpleNamespace(state=SimpleNamespace(is_admin=is_admin, tenant=tenant, corpus=corpus))


def _session(*, get_result=None, scalars=()):
    """Session stub: `get` returns a corpus, `execute` returns tenant corpus ids."""
    session = MagicMock()
    session.get = AsyncMock(return_value=get_result)
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(scalars)
    session.execute = AsyncMock(return_value=result)
    return session


class TestResolveScope:
    def test_admin_key_is_unrestricted(self) -> None:
        scope = resolve_scope(_request(is_admin=True))
        assert scope.is_admin
        assert scope.tenant_id is None and scope.corpus_id is None

    def test_tenant_key_carries_tenant_id(self) -> None:
        scope = resolve_scope(_request(is_admin=False, tenant=SimpleNamespace(id=TENANT_A)))
        assert not scope.is_admin
        assert scope.tenant_id == TENANT_A
        assert scope.corpus_id is None

    def test_corpus_key_resolves_its_owning_tenant(self) -> None:
        # The middleware sets tenant=None for corpus keys, so the owning tenant
        # has to come off the corpus itself or tenant scoping silently breaks.
        scope = resolve_scope(
            _request(
                is_admin=False,
                corpus=SimpleNamespace(id=CORPUS_A1, tenant_id=TENANT_A),
            )
        )
        assert scope.corpus_id == CORPUS_A1
        assert scope.tenant_id == TENANT_A

    def test_unauthenticated_is_401(self) -> None:
        with pytest.raises(HTTPException) as exc:
            resolve_scope(_request())
        assert exc.value.status_code == 401


class TestAdminScope:
    @pytest.mark.asyncio
    async def test_no_corpus_means_no_restriction(self) -> None:
        assert await authorized_corpus_ids(Scope(is_admin=True), None, _session()) is None

    @pytest.mark.asyncio
    async def test_named_corpus_is_honoured(self) -> None:
        assert await authorized_corpus_ids(Scope(is_admin=True), CORPUS_B1, _session()) == [
            CORPUS_B1
        ]


class TestCorpusKeyScope:
    @pytest.mark.asyncio
    async def test_defaults_to_its_own_corpus(self) -> None:
        scope = Scope(is_admin=False, tenant_id=TENANT_A, corpus_id=CORPUS_A1)
        assert await authorized_corpus_ids(scope, None, _session()) == [CORPUS_A1]

    @pytest.mark.asyncio
    async def test_asking_for_another_corpus_is_403(self) -> None:
        scope = Scope(is_admin=False, tenant_id=TENANT_A, corpus_id=CORPUS_A1)
        with pytest.raises(HTTPException) as exc:
            await authorized_corpus_ids(scope, CORPUS_B1, _session())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_cannot_widen_to_a_sibling_corpus_of_the_same_tenant(self) -> None:
        # A corpus key is narrower than its tenant. Owning the tenant is not
        # enough — the key names one corpus and that is the whole scope.
        scope = Scope(is_admin=False, tenant_id=TENANT_A, corpus_id=CORPUS_A1)
        with pytest.raises(HTTPException) as exc:
            await authorized_corpus_ids(scope, CORPUS_A2, _session())
        assert exc.value.status_code == 403


class TestTenantKeyScope:
    @pytest.mark.asyncio
    async def test_own_corpus_is_allowed(self) -> None:
        session = _session(get_result=SimpleNamespace(id=CORPUS_A1, tenant_id=TENANT_A))
        scope = Scope(is_admin=False, tenant_id=TENANT_A)
        assert await authorized_corpus_ids(scope, CORPUS_A1, session) == [CORPUS_A1]

    @pytest.mark.asyncio
    async def test_another_tenants_corpus_is_403(self) -> None:
        """The leak this whole change exists to prevent."""
        session = _session(get_result=SimpleNamespace(id=CORPUS_B1, tenant_id=TENANT_B))
        scope = Scope(is_admin=False, tenant_id=TENANT_A)
        with pytest.raises(HTTPException) as exc:
            await authorized_corpus_ids(scope, CORPUS_B1, session)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_corpus_is_404(self) -> None:
        scope = Scope(is_admin=False, tenant_id=TENANT_A)
        with pytest.raises(HTTPException) as exc:
            await authorized_corpus_ids(scope, uuid.uuid4(), _session(get_result=None))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_no_corpus_falls_back_to_everything_owned(self) -> None:
        session = _session(scalars=[CORPUS_A1, CORPUS_A2])
        scope = Scope(is_admin=False, tenant_id=TENANT_A)
        assert await authorized_corpus_ids(scope, None, session) == [CORPUS_A1, CORPUS_A2]

    @pytest.mark.asyncio
    async def test_tenant_owning_nothing_gets_empty_not_unrestricted(self) -> None:
        # The dangerous failure mode: an empty result must filter everything out,
        # never degrade to None (which routes read as "no restriction" = admin).
        session = _session(scalars=[])
        result = await authorized_corpus_ids(
            Scope(is_admin=False, tenant_id=TENANT_A), None, session
        )
        assert result == []
        assert result is not None
