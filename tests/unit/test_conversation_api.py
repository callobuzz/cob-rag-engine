"""Unit tests for conversation CRUD API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.core.config import Settings

pytestmark = pytest.mark.usefixtures("unlimited_plan")


def _make_settings() -> Settings:
    return Settings(
        _env_file=None,
        jina_api_key="test",
        llm_api_key="test",
        admin_api_key="test-admin-key",
        encryption_key="VHlwZV90ZXN0X2tleV8zMl9ieXRlcw==",
    )


def _make_mock_corpus(corpus_id=None):
    corpus = MagicMock()
    corpus.id = corpus_id or uuid.uuid4()
    corpus.name = "Test Corpus"
    corpus.qdrant_collection = f"corpus_{str(corpus.id)[:8]}"
    corpus.tenant_id = uuid.uuid4()
    corpus.llm_config = None
    return corpus


def _make_conversation(corpus_id, conv_id=None, title="Test Chat"):
    now = datetime.now(UTC)
    conv = MagicMock()
    conv.id = conv_id or uuid.uuid4()
    conv.corpus_id = corpus_id
    conv.title = title
    conv.created_by = "user-1"
    conv.metadata_ = {"source": "web"}
    conv.message_count = 0
    conv.total_tokens = 0
    conv.summary = None
    conv.channel = "api"
    conv.created_at = now
    conv.updated_at = now
    return conv


def _make_message(conversation_id, msg_id=None, role="user", content="Hello"):
    now = datetime.now(UTC)
    msg = MagicMock()
    msg.id = msg_id or uuid.uuid4()
    msg.conversation_id = conversation_id
    msg.role = role
    msg.content = content
    msg.token_count = 3
    msg.retrieval_context = None
    msg.agent_log = None
    msg.created_at = now
    return msg


@pytest.fixture
def corpus():
    return _make_mock_corpus()


@pytest.fixture
def app(corpus):
    application = create_app()
    settings = _make_settings()
    application.state.settings = settings
    application.state.session_factory = MagicMock()
    application.state.vector_store = None
    application.state.embedder = None
    application.state.context_assembler = None
    application.state.collection_manager = None
    application.state.webhook_dispatcher = None

    from callobuzz_rag.api.auth import APIKeyAuthMiddleware

    original_dispatch = APIKeyAuthMiddleware.dispatch

    async def passthrough_dispatch(self, request, call_next):
        request.state.is_admin = False
        request.state.corpus = corpus
        return await call_next(request)

    APIKeyAuthMiddleware.dispatch = passthrough_dispatch
    yield application
    APIKeyAuthMiddleware.dispatch = original_dispatch


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.delete = AsyncMock()
    session.add = MagicMock()
    return session


def _override_deps(app, mock_session, corpus):
    from callobuzz_rag.api.deps import get_session

    async def override_session():
        yield mock_session

    # No override of the corpus dependency: the patched middleware authenticates
    # `corpus` as a corpus key, and path_corpus scopes the path id against it.
    app.dependency_overrides[get_session] = override_session


# ---------------------------------------------------------------------------
# POST — Create conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_conversation(app, mock_session, corpus):
    """POST should create a conversation and return 201."""
    conv = _make_conversation(corpus.id)

    async def fake_refresh(obj):
        obj.id = conv.id
        obj.corpus_id = conv.corpus_id
        obj.title = conv.title
        obj.created_by = conv.created_by
        obj.metadata_ = conv.metadata_
        obj.message_count = 0
        obj.total_tokens = 0
        obj.summary = None
        obj.created_at = conv.created_at
        obj.updated_at = conv.updated_at

    mock_session.refresh = fake_refresh
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/corpora/{corpus.id}/conversations",
            json={"title": "Test Chat", "created_by": "user-1", "metadata": {"source": "web"}},
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Test Chat"
    assert data["created_by"] == "user-1"
    assert data["metadata"] == {"source": "web"}
    mock_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_create_conversation_minimal(app, mock_session, corpus):
    """POST with empty body should create conversation with null fields."""
    now = datetime.now(UTC)

    async def fake_refresh(obj):
        obj.id = uuid.uuid4()
        obj.corpus_id = corpus.id
        obj.title = None
        obj.created_by = None
        obj.metadata_ = None
        obj.message_count = 0
        obj.total_tokens = 0
        obj.summary = None
        obj.created_at = now
        obj.updated_at = now

    mock_session.refresh = fake_refresh
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/corpora/{corpus.id}/conversations",
            json={},
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] is None


# ---------------------------------------------------------------------------
# GET — List conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_conversations(app, mock_session, corpus):
    """GET should return paginated list of conversations."""
    convs = [_make_conversation(corpus.id) for _ in range(3)]

    scalars_mock = MagicMock()
    scalars_mock.all.return_value = convs
    exec_result_list = MagicMock()
    exec_result_list.scalars.return_value = scalars_mock

    count_result = MagicMock()
    count_result.scalar.return_value = 3

    mock_session.execute = AsyncMock(side_effect=[exec_result_list, count_result])
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/corpora/{corpus.id}/conversations",
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert len(data["items"]) == 3
    assert data["offset"] == 0
    assert data["limit"] == 50


@pytest.mark.asyncio
async def test_list_conversations_with_created_by_filter(app, mock_session, corpus):
    """GET with created_by query param should filter results."""
    conv = _make_conversation(corpus.id)

    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [conv]
    exec_result_list = MagicMock()
    exec_result_list.scalars.return_value = scalars_mock

    count_result = MagicMock()
    count_result.scalar.return_value = 1

    mock_session.execute = AsyncMock(side_effect=[exec_result_list, count_result])
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/corpora/{corpus.id}/conversations?created_by=user-1",
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def _list_where(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", "conversations.channel NOT IN ('widget', 'whatsapp')"),
        ("?channel=widget", "conversations.channel = 'widget'"),
        ("?channel=whatsapp", "conversations.channel = 'whatsapp'"),
        ("?channel=api", "conversations.channel = 'api'"),
    ],
)
async def test_list_conversations_channel_filter(app, mock_session, corpus, query, expected):
    """Website visitors' and WhatsApp chats stay out of the default list; `channel=` picks one."""
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = []
    exec_result_list = MagicMock()
    exec_result_list.scalars.return_value = scalars_mock
    count_result = MagicMock()
    count_result.scalar.return_value = 0
    mock_session.execute = AsyncMock(side_effect=[exec_result_list, count_result])
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/corpora/{corpus.id}/conversations{query}",
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 200
    assert expected in _list_where(mock_session.execute.await_args_list[0].args[0])


@pytest.mark.asyncio
async def test_list_conversations_rejects_an_unknown_channel(app, mock_session, corpus):
    _override_deps(app, mock_session, corpus)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/corpora/{corpus.id}/conversations?channel=sms",
            headers={"X-API-Key": "ck_fake"},
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET — Get single conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_conversation(app, mock_session, corpus):
    """GET /{id} should return a single conversation."""
    conv = _make_conversation(corpus.id)

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = conv
    mock_session.execute = AsyncMock(return_value=result_mock)
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/corpora/{corpus.id}/conversations/{conv.id}",
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 200
    assert resp.json()["id"] == str(conv.id)


@pytest.mark.asyncio
async def test_get_conversation_not_found(app, mock_session, corpus):
    """GET /{id} should return 404 for missing conversation."""
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=result_mock)
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/corpora/{corpus.id}/conversations/{uuid.uuid4()}",
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH — Update conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_conversation(app, mock_session, corpus):
    """PATCH should update title and metadata."""
    conv = _make_conversation(corpus.id)

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = conv
    mock_session.execute = AsyncMock(return_value=result_mock)

    async def fake_refresh(obj):
        obj.title = "Updated"
        obj.metadata_ = {"new": True}

    mock_session.refresh = fake_refresh
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/api/v1/corpora/{corpus.id}/conversations/{conv.id}",
            json={"title": "Updated", "metadata": {"new": True}},
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Updated"
    assert data["metadata"] == {"new": True}


@pytest.mark.asyncio
async def test_update_conversation_not_found(app, mock_session, corpus):
    """PATCH on missing conversation should return 404."""
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=result_mock)
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            f"/api/v1/corpora/{corpus.id}/conversations/{uuid.uuid4()}",
            json={"title": "Nope"},
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE — Delete conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_conversation(app, mock_session, corpus):
    """DELETE should return 204 on success."""
    conv = _make_conversation(corpus.id)

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = conv

    # execute is called twice: once for SELECT, once for DELETE
    mock_session.execute = AsyncMock(side_effect=[result_mock, MagicMock()])
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            f"/api/v1/corpora/{corpus.id}/conversations/{conv.id}",
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_conversation_not_found(app, mock_session, corpus):
    """DELETE on missing conversation should return 404."""
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=result_mock)
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            f"/api/v1/corpora/{corpus.id}/conversations/{uuid.uuid4()}",
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET — List messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_messages(app, mock_session, corpus):
    """GET /messages should return paginated messages oldest-first."""
    conv = _make_conversation(corpus.id)
    msgs = [
        _make_message(conv.id, role="user", content="Hello"),
        _make_message(conv.id, role="assistant", content="Hi!"),
    ]

    # First execute: conversation existence check
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conv.id

    # Second execute: message list
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = msgs
    msg_result = MagicMock()
    msg_result.scalars.return_value = scalars_mock

    # Third execute: count
    count_result = MagicMock()
    count_result.scalar.return_value = 2

    mock_session.execute = AsyncMock(side_effect=[conv_result, msg_result, count_result])
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/corpora/{corpus.id}/conversations/{conv.id}/messages",
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["role"] == "user"
    assert data["items"][1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_list_messages_conversation_not_found(app, mock_session, corpus):
    """GET /messages on missing conversation should return 404."""
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=conv_result)
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/corpora/{corpus.id}/conversations/{uuid.uuid4()}/messages",
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Corpus mismatch — 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corpus_mismatch_returns_403(app, mock_session, corpus):
    """Using a different corpus_id in the path should return 403."""
    wrong_corpus_id = uuid.uuid4()
    _override_deps(app, mock_session, corpus)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/corpora/{wrong_corpus_id}/conversations",
            headers={"X-API-Key": "ck_fake"},
        )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Retrieval-searcher construction (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_builds_searcher_when_retrieval_configured(
    app, mock_session, corpus, monkeypatch
):
    """POST /messages must build the HybridSearcher without raising.

    Regression test. This branch runs only when BOTH `embedder` and
    `vector_store` are present on app.state — and every other test in this file
    leaves both as None, so nothing ever entered it. Inside, the code called
    `QueryEmbeddingCache()` while its __init__ requires `settings`, so any
    deployment with retrieval configured raised
    `TypeError: __init__() missing 1 required positional argument: 'settings'`
    on every conversation message.

    Uses the SSE branch because it returns before touching the database: the
    StreamingResponse wraps a generator that is not consumed here, so this test
    isolates exactly the construction path that was broken.
    """
    _override_deps(app, mock_session, corpus)
    app.state.llm_provider = MagicMock()
    app.state.embedder = MagicMock()
    app.state.vector_store = MagicMock()

    async def fake_stream(**kwargs):
        yield "data: {}\n\n"

    monkeypatch.setattr(
        "callobuzz_rag.api.services.streaming_service.stream_message_response",
        fake_stream,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/corpora/{corpus.id}/conversations/{uuid.uuid4()}/messages",
            headers={"X-API-Key": "ck_fake", "accept": "text/event-stream"},
            json={"content": "What is RAG?"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")


# ---------------------------------------------------------------------------
# Per-corpus LLM resolution and usage metering (G-22)
# ---------------------------------------------------------------------------


def _use_encrypting_resolver(app):
    """A resolver whose encryptor 'decrypts' the corpus key to a known value."""
    from callobuzz_rag.core.config_resolver import ConfigResolver

    encryptor = MagicMock()
    encryptor.decrypt.return_value = "sk-corpus-key"
    app.state.config_resolver = ConfigResolver(app.state.settings, encryptor=encryptor)


def _patch_service(monkeypatch, llm_responses):
    """Replace ConversationService; capture the provider the route built."""
    captured: dict = {}

    class FakeService:
        def __init__(self, session, llm_provider, searcher=None):
            captured["llm"] = llm_provider
            self.llm_responses = list(llm_responses)

        async def send_message(self, conversation_id, corpus_id, content, metadata=None):
            return (
                _make_message(conversation_id, role="user", content=content),
                _make_message(conversation_id, role="assistant", content="answer"),
                None,
            )

    monkeypatch.setattr(
        "callobuzz_rag.api.services.conversation_service.ConversationService", FakeService
    )
    return captured


def _patch_record_usage(monkeypatch):
    calls: list[dict] = []

    async def fake_record_usage(session, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("callobuzz_rag.api.routes.conversations.record_usage", fake_record_usage)
    return calls


@pytest.mark.asyncio
async def test_send_message_uses_corpus_llm_config_and_meters(
    app, mock_session, corpus, monkeypatch
):
    """JSON messages use the corpus's model, fast model and stored key, and are metered."""
    from callobuzz_rag.llm.models import LLMResponse, LLMUsage

    corpus.tenant_id = uuid.uuid4()
    corpus.llm_config = {
        "model": "openai/corpus-model",
        "fast_model": "openai/corpus-fast",
        "api_key_encrypted": "gAAAA-stored",
    }
    _override_deps(app, mock_session, corpus)
    _use_encrypting_resolver(app)
    captured = _patch_service(
        monkeypatch,
        [
            LLMResponse(
                content="answer",
                model="openai/corpus-model",
                usage=LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
            ),
            LLMResponse(
                content="A title",
                model="openai/corpus-fast",
                usage=LLMUsage(prompt_tokens=30, completion_tokens=4, total_tokens=34),
            ),
        ],
    )
    calls = _patch_record_usage(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/corpora/{corpus.id}/conversations/{uuid.uuid4()}/messages",
            headers={"X-API-Key": "ck_fake"},
            json={"content": "What is RAG?"},
        )

    assert resp.status_code == 201, resp.text
    llm = captured["llm"]
    assert llm._model == "openai/corpus-model"
    assert llm.fast_model == "openai/corpus-fast"
    assert llm._api_key == "sk-corpus-key"  # corpus key beats the engine default

    assert [c["source"] for c in calls] == ["conversation", "conversation"]
    answer, title = calls
    assert answer["requests"] == 1
    assert title["requests"] == 0
    assert (answer["prompt_tokens"], answer["completion_tokens"]) == (100, 20)
    assert answer["model"] == "openai/corpus-model"
    assert title["model"] == "openai/corpus-fast"
    assert answer["corpus_id"] == corpus.id
    assert answer["tenant_id"] == corpus.tenant_id
    # BIL3: the answer carries the turn's id, the title call its own derived one
    assert isinstance(answer["request_id"], uuid.UUID)
    assert title["request_id"] == uuid.uuid5(answer["request_id"], "aux1")
    mock_session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_send_message_x_llm_headers_beat_corpus_config(
    app, mock_session, corpus, monkeypatch
):
    """X-LLM-* headers win over the corpus's stored key and model, as on /query."""
    corpus.llm_config = {"model": "openai/corpus-model", "api_key_encrypted": "gAAAA-stored"}
    _override_deps(app, mock_session, corpus)
    _use_encrypting_resolver(app)
    captured = _patch_service(monkeypatch, [])
    _patch_record_usage(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/corpora/{corpus.id}/conversations/{uuid.uuid4()}/messages",
            headers={
                "X-API-Key": "ck_fake",
                "X-LLM-Model": "anthropic/header-model",
                "X-LLM-API-Key": "sk-from-header",
            },
            json={"content": "Hi"},
        )

    assert resp.status_code == 201, resp.text
    assert captured["llm"]._model == "anthropic/header-model"
    assert captured["llm"]._api_key == "sk-from-header"


@pytest.mark.asyncio
async def test_send_message_engine_default_when_corpus_has_no_llm_config(
    app, mock_session, corpus, monkeypatch
):
    """No headers, no corpus config: the engine default model and key."""
    _override_deps(app, mock_session, corpus)
    captured = _patch_service(monkeypatch, [])
    _patch_record_usage(monkeypatch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/corpora/{corpus.id}/conversations/{uuid.uuid4()}/messages",
            headers={"X-API-Key": "ck_fake"},
            json={"content": "Hi"},
        )

    assert resp.status_code == 201, resp.text
    settings = app.state.settings
    assert captured["llm"]._model == settings.llm_model
    assert captured["llm"]._api_key == settings.llm_api_key.get_secret_value()


@pytest.mark.asyncio
async def test_streamed_message_uses_corpus_llm_and_meters(app, mock_session, corpus, monkeypatch):
    """The SSE branch gets the same provider and records usage after `done`."""
    from callobuzz_rag.llm.models import LLMMessage

    corpus.tenant_id = uuid.uuid4()
    corpus.llm_config = {"model": "openai/corpus-model", "api_key_encrypted": "gAAAA-stored"}
    _override_deps(app, mock_session, corpus)
    _use_encrypting_resolver(app)
    captured: dict = {}

    async def fake_stream(*, llm_provider, meter, **kwargs):
        captured["llm"] = llm_provider
        yield "event: done\ndata: {}\n\n"
        await meter([LLMMessage(role="user", content="What is RAG?")], "It is retrieval.")

    monkeypatch.setattr(
        "callobuzz_rag.api.services.streaming_service.stream_message_response", fake_stream
    )
    recorded: list[dict] = []

    async def fake_new_session(session_factory, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(
        "callobuzz_rag.api.routes.conversations.record_usage_new_session", fake_new_session
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/corpora/{corpus.id}/conversations/{uuid.uuid4()}/messages",
            headers={"X-API-Key": "ck_fake", "accept": "text/event-stream"},
            json={"content": "What is RAG?"},
        )

    assert resp.status_code == 200, resp.text
    assert captured["llm"]._model == "openai/corpus-model"
    assert captured["llm"]._api_key == "sk-corpus-key"
    assert len(recorded) == 1
    row = recorded[0]
    assert row["source"] == "conversation"
    assert row["model"] == "openai/corpus-model"
    assert row["tenant_id"] == corpus.tenant_id
    assert row["prompt_tokens"] > 0
    assert row["completion_tokens"] > 0
    assert isinstance(row["request_id"], uuid.UUID)  # BIL3 request id


_GEMMA = "google/gemma-4-31b-it"
_HINDI = "मुझे रिटर्न पॉलिसी बताओ"


def _routed_llm_config() -> dict:
    return {
        "model": "openai/corpus-model",
        "api_key_encrypted": "gAAAA-stored",
        "language_models": {"hi": _GEMMA},
    }


@pytest.mark.asyncio
async def test_send_message_answers_hindi_on_the_routed_model(
    app, mock_session, corpus, monkeypatch
):
    """The older JSON message endpoint routes by the visitor's language and meters the model."""
    from callobuzz_rag.llm.models import LLMResponse, LLMUsage

    corpus.tenant_id = uuid.uuid4()
    corpus.llm_config = _routed_llm_config()
    _override_deps(app, mock_session, corpus)
    _use_encrypting_resolver(app)
    captured = _patch_service(
        monkeypatch,
        [
            LLMResponse(
                content="answer",
                model=_GEMMA,
                usage=LLMUsage(prompt_tokens=10, completion_tokens=2, total_tokens=12),
            )
        ],
    )
    calls = _patch_record_usage(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for content in (_HINDI, "What is RAG?"):
            captured.clear()
            calls.clear()
            resp = await client.post(
                f"/api/v1/corpora/{corpus.id}/conversations/{uuid.uuid4()}/messages",
                headers={"X-API-Key": "ck_fake"},
                json={"content": content},
            )
            assert resp.status_code == 201, resp.text
            expected = _GEMMA if content == _HINDI else "openai/corpus-model"
            assert captured["llm"]._model == expected
            assert calls[0]["model"] == _GEMMA  # the fake reply names the routed model


@pytest.mark.asyncio
async def test_streamed_message_answers_hindi_on_the_routed_model(
    app, mock_session, corpus, monkeypatch
):
    from callobuzz_rag.llm.models import LLMMessage

    corpus.tenant_id = uuid.uuid4()
    corpus.llm_config = _routed_llm_config()
    _override_deps(app, mock_session, corpus)
    _use_encrypting_resolver(app)
    captured: dict = {}

    async def fake_stream(*, llm_provider, meter, **kwargs):
        captured["llm"] = llm_provider
        yield "event: done\ndata: {}\n\n"
        await meter([LLMMessage(role="user", content="q")], "reply")

    monkeypatch.setattr(
        "callobuzz_rag.api.services.streaming_service.stream_message_response", fake_stream
    )
    recorded: list[dict] = []

    async def fake_new_session(session_factory, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(
        "callobuzz_rag.api.routes.conversations.record_usage_new_session", fake_new_session
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/corpora/{corpus.id}/conversations/{uuid.uuid4()}/messages",
            headers={"X-API-Key": "ck_fake", "accept": "text/event-stream"},
            json={"content": _HINDI},
        )

    assert resp.status_code == 200, resp.text
    assert captured["llm"]._model == _GEMMA
    assert captured["llm"]._api_key == "sk-corpus-key"  # the corpus key travels with the copy
    assert [r["model"] for r in recorded] == [_GEMMA]
