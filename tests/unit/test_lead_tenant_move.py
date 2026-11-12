import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from callobuzz_rag.api.routes.corpus import _move_to_tenant


async def test_moving_a_knowledge_base_moves_its_leads() -> None:
    corpus = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    target = uuid.uuid4()
    session = AsyncMock()
    session.get = AsyncMock(return_value=SimpleNamespace(id=target))
    request = SimpleNamespace(state=SimpleNamespace(tenant=None))
    with patch("callobuzz_rag.api.routes.corpus.check_kb_room", AsyncMock()):
        await _move_to_tenant(session, request, corpus, target)  # type: ignore[arg-type]
    assert corpus.tenant_id == target
    statement = session.execute.await_args_list[0].args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert sql.startswith("UPDATE leads SET tenant_id=") and "corpus_id" in sql
    params = statement.compile(dialect=postgresql.dialect()).params
    assert target in params.values() and corpus.id in params.values()


async def test_moving_a_knowledge_base_unlinks_the_public_websites_assistant() -> None:
    corpus = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    target = uuid.uuid4()
    session = AsyncMock()
    session.get = AsyncMock(return_value=SimpleNamespace(id=target))
    request = SimpleNamespace(state=SimpleNamespace(tenant=None))
    with patch("callobuzz_rag.api.routes.corpus.check_kb_room", AsyncMock()):
        await _move_to_tenant(session, request, corpus, target)  # type: ignore[arg-type]
    compiled = session.execute.await_args_list[1].args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert sql.startswith("UPDATE landing_sites SET ask_corpus_id=")
    assert "ask_widget_key_id" in sql and corpus.id in compiled.params.values()
