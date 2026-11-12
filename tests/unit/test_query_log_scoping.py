"""A query log is readable, and rateable, only inside the caller's own scope.

GET /api/v1/query/{id} used to return any query to any valid key, which let one
client read another client's questions and answers by id. POST /feedback only
scope-checked corpus keys, so a tenant key could rate another tenant's queries.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from callobuzz_rag.api.routes.feedback import submit_feedback
from callobuzz_rag.api.routes.query import get_query
from callobuzz_rag.api.schemas.feedback import FeedbackRequest
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.query_log import QueryLog

TENANT_A, TENANT_B = uuid4(), uuid4()
CORPUS_A, CORPUS_B = uuid4(), uuid4()


def _request(*, admin: bool = False, tenant: object = None, corpus: object = None) -> MagicMock:
    request = MagicMock()
    request.state = SimpleNamespace(is_admin=admin, tenant=tenant, corpus=corpus)
    return request


def _session(log: QueryLog | None) -> AsyncMock:
    corpora = {
        CORPUS_A: SimpleNamespace(id=CORPUS_A, tenant_id=TENANT_A),
        CORPUS_B: SimpleNamespace(id=CORPUS_B, tenant_id=TENANT_B),
    }

    async def _get(model: object, key: object) -> object | None:
        if model is QueryLog:
            return log
        if model is Corpus:
            return corpora.get(key)  # type: ignore[call-overload]
        return None

    session = AsyncMock()
    session.get = AsyncMock(side_effect=_get)
    result = MagicMock()
    result.scalars.return_value.all.return_value = [
        c.id for c in corpora.values() if c.tenant_id == TENANT_A
    ]
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    return session


def _log(corpus_id: object) -> QueryLog:
    log = QueryLog(query_text="Do I need to fast?", response="Yes.")
    log.id = uuid4()
    log.corpus_id = corpus_id  # type: ignore[assignment]
    return log


class TestGetQuery:
    async def test_admin_reads_any_query(self) -> None:
        log = _log(CORPUS_B)
        assert await get_query(log.id, _request(admin=True), _session(log)) is log

    async def test_corpus_key_reads_its_own_query(self) -> None:
        log = _log(CORPUS_A)
        caller = _request(corpus=SimpleNamespace(id=CORPUS_A, tenant_id=TENANT_A))
        assert await get_query(log.id, caller, _session(log)) is log

    async def test_corpus_key_cannot_read_another_clients_query(self) -> None:
        log = _log(CORPUS_B)
        caller = _request(corpus=SimpleNamespace(id=CORPUS_A, tenant_id=TENANT_A))
        with pytest.raises(HTTPException) as exc:
            await get_query(log.id, caller, _session(log))
        assert exc.value.status_code == 404  # not 403: existence is not revealed

    async def test_tenant_key_cannot_read_another_tenants_query(self) -> None:
        log = _log(CORPUS_B)
        caller = _request(tenant=SimpleNamespace(id=TENANT_A))
        with pytest.raises(HTTPException) as exc:
            await get_query(log.id, caller, _session(log))
        assert exc.value.status_code == 404

    async def test_tenant_key_reads_its_own_corpora_queries(self) -> None:
        log = _log(CORPUS_A)
        caller = _request(tenant=SimpleNamespace(id=TENANT_A))
        assert await get_query(log.id, caller, _session(log)) is log

    async def test_query_with_no_corpus_is_admin_only(self) -> None:
        log = _log(None)
        caller = _request(tenant=SimpleNamespace(id=TENANT_A))
        with pytest.raises(HTTPException) as exc:
            await get_query(log.id, caller, _session(log))
        assert exc.value.status_code == 404


class TestFeedbackScope:
    async def test_tenant_key_cannot_rate_another_tenants_query(self) -> None:
        log = _log(CORPUS_B)
        body = FeedbackRequest(query_id=log.id, rating=1)
        caller = _request(tenant=SimpleNamespace(id=TENANT_A))
        with pytest.raises(HTTPException) as exc:
            await submit_feedback(body, caller, _session(log))
        assert exc.value.status_code == 403

    async def test_corpus_key_cannot_rate_another_clients_query(self) -> None:
        log = _log(CORPUS_B)
        body = FeedbackRequest(query_id=log.id, rating=1)
        caller = _request(corpus=SimpleNamespace(id=CORPUS_A, tenant_id=TENANT_A))
        with pytest.raises(HTTPException) as exc:
            await submit_feedback(body, caller, _session(log))
        assert exc.value.status_code == 403
