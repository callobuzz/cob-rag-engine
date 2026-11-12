"""Admin can create a corpus for a tenant — the core "onboard a customer" action."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from callobuzz_rag.accounts import caps
from callobuzz_rag.api.routes.corpus import create_corpus
from callobuzz_rag.api.schemas.corpus import CreateCorpusRequest


def _request(tenant=None):
    request = MagicMock()
    request.state.tenant = tenant
    request.state.is_admin = tenant is None
    request.app.state.vector_store = None
    request.app.state.settings = SimpleNamespace(
        embedding_dimensions=768,
        embedding_provider="tei",
        embedding_model="m",
        assistant_default_for_new_kbs=True,
        encryption_key="",
    )
    return request


def _session(tenant_in_db=None, existing_count: int = 0):
    session = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock(return_value=tenant_in_db)
    count = MagicMock()
    count.scalar.return_value = existing_count
    session.execute = AsyncMock(return_value=count)

    async def flush():
        session.add.call_args.args[0].id = uuid.uuid4()

    async def refresh(obj):
        obj.created_at = datetime.now(UTC)

    session.flush = AsyncMock(side_effect=flush)
    session.refresh = AsyncMock(side_effect=refresh)
    return session


def _kb_limit(limit: int | None):  # type: ignore[no-untyped-def]
    """The tenant's effective knowledge-base limit (accounts A6: override, else plan)."""
    return patch.object(
        caps, "effective_limits", AsyncMock(return_value=caps.Limits(max_knowledge_bases=limit))
    )


async def test_admin_creates_corpus_for_tenant() -> None:
    tenant = SimpleNamespace(id=uuid.uuid4(), max_corpora=5)
    session = _session(tenant_in_db=tenant)
    with _kb_limit(5):
        await create_corpus(
            CreateCorpusRequest(name="Help", tenant_id=tenant.id), _request(), session, None
        )
    # the corpus is the first row added; its default key follows
    assert session.add.call_args_list[0].args[0].tenant_id == tenant.id


async def test_admin_create_respects_tenant_corpus_limit() -> None:
    tenant = SimpleNamespace(id=uuid.uuid4(), max_corpora=2)
    with pytest.raises(HTTPException) as err, _kb_limit(2):
        await create_corpus(
            CreateCorpusRequest(name="Help", tenant_id=tenant.id),
            _request(),
            _session(tenant_in_db=tenant, existing_count=2),
            None,
        )
    assert err.value.status_code == 409
    assert getattr(err.value, "code", None) == "kb_limit_reached"
    assert err.value.extra == {"limit": 2}  # type: ignore[attr-defined]


async def test_unlimited_plan_never_refuses_a_knowledge_base() -> None:
    tenant = SimpleNamespace(id=uuid.uuid4(), max_corpora=None)
    session = _session(tenant_in_db=tenant, existing_count=5000)
    with _kb_limit(None):
        await create_corpus(
            CreateCorpusRequest(name="Help", tenant_id=tenant.id), _request(), session, None
        )
    # the knowledge base, its default key, its assistant v1 and that publish's audit row
    assert session.add.call_count == 4


async def test_default_key_never_exceeds_the_callers_permission() -> None:
    """A write-permission tenant key must not mint a full key for the new knowledge base."""
    from callobuzz_rag.api.permissions import KeyInfo
    from callobuzz_rag.db.models.api_key import ApiKey

    tenant = SimpleNamespace(id=uuid.uuid4(), max_corpora=5)
    request = _request(tenant=tenant)
    request.state.api_key = KeyInfo(id=uuid.uuid4(), name="Agent", permission="write", kind="tk")
    session = _session()
    with _kb_limit(5):
        await create_corpus(
            CreateCorpusRequest(name="Help", tenant_id=tenant.id), request, session, None
        )
    keys = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], ApiKey)]
    assert [k.permission for k in keys] == ["write"]


async def test_default_key_is_full_for_admin_and_sessions() -> None:
    from callobuzz_rag.db.models.api_key import ApiKey

    tenant = SimpleNamespace(id=uuid.uuid4(), max_corpora=5)
    request = _request(tenant=tenant)
    request.state.api_key = None
    session = _session()
    with _kb_limit(5):
        await create_corpus(
            CreateCorpusRequest(name="Help", tenant_id=tenant.id), request, session, None
        )
    keys = [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], ApiKey)]
    assert [k.permission for k in keys] == ["full"]


async def test_unknown_tenant_is_404() -> None:
    with pytest.raises(HTTPException) as err:
        await create_corpus(
            CreateCorpusRequest(name="Help", tenant_id=uuid.uuid4()),
            _request(),
            _session(tenant_in_db=None),
            None,
        )
    assert err.value.status_code == 404


async def test_tenant_key_cannot_create_for_another_tenant() -> None:
    me = SimpleNamespace(id=uuid.uuid4(), max_corpora=5)
    with pytest.raises(HTTPException) as err:
        await create_corpus(
            CreateCorpusRequest(name="Help", tenant_id=uuid.uuid4()),
            _request(tenant=me),
            _session(),
            None,
        )
    assert err.value.status_code == 403


async def test_admin_without_tenant_id_is_refused() -> None:
    """Every corpus belongs to a tenant (accounts A1): no unowned corpus is created."""
    session = _session()
    with pytest.raises(HTTPException) as err:
        await create_corpus(CreateCorpusRequest(name="Help"), _request(), session, None)
    assert err.value.status_code == 422
    assert getattr(err.value, "code", None) == "tenant_required"
    session.add.assert_not_called()
