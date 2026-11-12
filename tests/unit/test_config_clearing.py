"""Corpus config writes: explicit null clears a field, `{}` is a no-op merge.

Before: every merge used `exclude_none`, so a stored value (the encrypted LLM
key, base_url, a prompt) could never be removed, and `retrieval_config: {}`
silently reset the corpus to engine defaults.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.core.config import Settings

ADMIN = {"X-API-Key": "test-admin-key"}


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        jina_api_key="test",
        llm_api_key="test",
        admin_api_key="test-admin-key",
        # A valid Fernet key: the new-key test really encrypts.
        encryption_key="HokEyGOdfmWPKTqeM8zo_tkXkn4QFMu4Ryn9cXAoHOg=",
    )


def _corpus(**config: Any) -> MagicMock:
    corpus = MagicMock()
    corpus.id = uuid.uuid4()
    corpus.name = "Test"
    corpus.description = "Test corpus"
    corpus.qdrant_collection = f"corpus_{str(corpus.id)[:8]}"
    corpus.tenant_id = uuid.uuid4()
    corpus.llm_config = config.get("llm_config")
    corpus.prompt_config = config.get("prompt_config")
    corpus.retrieval_config = config.get("retrieval_config")
    corpus.embedding_config = None
    corpus.applied_embedding = None
    corpus.created_at = "2026-03-08T00:00:00Z"
    return corpus


@pytest.fixture(autouse=True)
def _no_corpus_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests mock the session per route; the stats query is tested on its own."""
    from unittest.mock import AsyncMock as _AsyncMock

    monkeypatch.setattr("callobuzz_rag.api.routes.corpus.corpus_stats", _AsyncMock(return_value={}))


@pytest.fixture
def app():
    from callobuzz_rag.api.app import create_app

    application = create_app()
    application.state.settings = _settings()
    application.state.session_factory = MagicMock()
    application.state.vector_store = None
    application.state.embedder = None
    application.state.context_assembler = None
    application.state.collection_manager = None
    application.state.webhook_dispatcher = None
    return application


def _use_session(app, corpus: MagicMock) -> AsyncMock:
    from callobuzz_rag.api.deps import get_session

    session = AsyncMock()
    session.get = AsyncMock(return_value=corpus)

    async def override():
        yield session

    app.dependency_overrides[get_session] = override
    return session


async def _put(app, corpus: MagicMock, body: dict[str, Any]):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.put(f"/api/v1/corpora/{corpus.id}", headers=ADMIN, json=body)


STORED_LLM = {
    "provider": "openai",
    "model": "openai/gpt-4o-mini",
    "base_url": "https://proxy.example",
    "temperature": 0.2,
    "api_key_encrypted": "gAAAA-stored-key",
}


class TestAdminPutLLMConfig:
    async def test_null_api_key_removes_the_stored_key(self, app) -> None:
        corpus = _corpus(llm_config=dict(STORED_LLM))
        _use_session(app, corpus)

        resp = await _put(app, corpus, {"llm_config": {"api_key": None}})

        assert resp.status_code == 200, resp.text
        assert "api_key_encrypted" not in corpus.llm_config
        assert corpus.llm_config["model"] == "openai/gpt-4o-mini"  # the rest is untouched
        assert "api_key_encrypted" not in resp.json()["llm_config"]

    async def test_null_fields_are_removed(self, app) -> None:
        corpus = _corpus(llm_config=dict(STORED_LLM))
        _use_session(app, corpus)

        resp = await _put(app, corpus, {"llm_config": {"base_url": None, "temperature": None}})

        assert resp.status_code == 200, resp.text
        assert "base_url" not in corpus.llm_config
        assert "temperature" not in corpus.llm_config
        assert corpus.llm_config["api_key_encrypted"] == "gAAAA-stored-key"

    async def test_new_key_is_encrypted_and_redacted(self, app, monkeypatch) -> None:
        corpus = _corpus(llm_config=dict(STORED_LLM))
        _use_session(app, corpus)
        # P5: saving a key asks whether the workspace's plan allows its own key (one query the
        # mocked session cannot answer); test_byok_enforcement.py covers the refusal itself
        monkeypatch.setattr(
            "callobuzz_rag.api.routes.corpus.refuse_key_save", AsyncMock(return_value=None)
        )

        resp = await _put(app, corpus, {"llm_config": {"api_key": "sk-new"}})

        assert resp.status_code == 200, resp.text
        stored = corpus.llm_config
        assert "api_key" not in stored
        assert stored["api_key_encrypted"] not in ("gAAAA-stored-key", "sk-new")
        assert resp.json()["llm_config"]["api_key_encrypted"] == "***"
        assert "sk-new" not in resp.text

    async def test_empty_string_key_is_refused(self, app) -> None:
        corpus = _corpus(llm_config=dict(STORED_LLM))
        _use_session(app, corpus)

        resp = await _put(app, corpus, {"llm_config": {"api_key": ""}})

        assert resp.status_code == 422
        assert corpus.llm_config == STORED_LLM

    async def test_empty_object_changes_nothing(self, app) -> None:
        corpus = _corpus(llm_config=dict(STORED_LLM))
        _use_session(app, corpus)

        resp = await _put(app, corpus, {"llm_config": {}})

        assert resp.status_code == 200, resp.text
        assert corpus.llm_config == STORED_LLM

    async def test_whole_section_null_resets_to_engine_defaults(self, app) -> None:
        corpus = _corpus(llm_config=dict(STORED_LLM))
        _use_session(app, corpus)

        resp = await _put(app, corpus, {"llm_config": None})

        assert resp.status_code == 200, resp.text
        assert corpus.llm_config is None

    async def test_omitted_section_is_untouched(self, app) -> None:
        corpus = _corpus(llm_config=dict(STORED_LLM))
        _use_session(app, corpus)

        resp = await _put(app, corpus, {"name": "Renamed"})

        assert resp.status_code == 200, resp.text
        assert corpus.llm_config == STORED_LLM
        assert corpus.description == "Test corpus"


class TestAdminPutRetrievalAndPrompt:
    async def test_empty_retrieval_config_is_a_no_op(self, app) -> None:
        """It used to reset the corpus to engine defaults."""
        stored = {"top_k": 30, "final_k": 6, "hyde_enabled": True}
        corpus = _corpus(retrieval_config=dict(stored))
        _use_session(app, corpus)

        resp = await _put(app, corpus, {"retrieval_config": {}})

        assert resp.status_code == 200, resp.text
        assert corpus.retrieval_config == stored

    async def test_null_retrieval_field_is_removed(self, app) -> None:
        corpus = _corpus(retrieval_config={"top_k": 30, "final_k": 6})
        _use_session(app, corpus)

        resp = await _put(app, corpus, {"retrieval_config": {"final_k": None, "top_k": 40}})

        assert resp.status_code == 200, resp.text
        assert corpus.retrieval_config == {"top_k": 40}

    async def test_removing_the_last_field_stores_null(self, app) -> None:
        corpus = _corpus(retrieval_config={"top_k": 30})
        _use_session(app, corpus)

        resp = await _put(app, corpus, {"retrieval_config": {"top_k": None}})

        assert resp.status_code == 200, resp.text
        assert corpus.retrieval_config is None

    async def test_retrieval_bounds_still_enforced(self, app) -> None:
        corpus = _corpus(retrieval_config={"top_k": 30})
        _use_session(app, corpus)

        resp = await _put(app, corpus, {"retrieval_config": {"top_k": 500}})

        assert resp.status_code == 422
        assert corpus.retrieval_config == {"top_k": 30}

    async def test_null_prompt_field_is_removed(self, app) -> None:
        corpus = _corpus(prompt_config={"system_prompt": "Be brief.", "constraints": "No PII."})
        _use_session(app, corpus)

        resp = await _put(app, corpus, {"prompt_config": {"system_prompt": None}})

        assert resp.status_code == 200, resp.text
        assert corpus.prompt_config == {"constraints": "No PII."}

    async def test_null_description_clears_it(self, app) -> None:
        corpus = _corpus()
        _use_session(app, corpus)

        resp = await _put(app, corpus, {"description": None})

        assert resp.status_code == 200, resp.text
        assert corpus.description is None


class TestSelfServicePatch:
    @pytest.fixture
    def corpus_app(self, app):
        from callobuzz_rag.api.auth import APIKeyAuthMiddleware

        original = APIKeyAuthMiddleware.dispatch

        async def passthrough(self, request, call_next):
            request.state.is_admin = False
            request.state.corpus = None
            return await call_next(request)

        APIKeyAuthMiddleware.dispatch = passthrough
        yield app
        APIKeyAuthMiddleware.dispatch = original

    async def _patch(self, app, corpus, body):
        from callobuzz_rag.api.deps import scoped_corpus

        app.dependency_overrides[scoped_corpus] = lambda: corpus
        _use_session(app, corpus)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.patch("/api/v1/config", headers={"X-API-Key": "ck_x"}, json=body)

    async def test_null_clears_and_empty_is_a_no_op(self, corpus_app) -> None:
        corpus = _corpus(
            retrieval_config={"top_k": 30, "final_k": 6},
            prompt_config={"system_prompt": "Be brief."},
            llm_config=dict(STORED_LLM),
        )

        resp = await self._patch(
            corpus_app,
            corpus,
            {"retrieval_config": {"final_k": None}, "prompt_config": {}},
        )

        assert resp.status_code == 200, resp.text
        assert corpus.retrieval_config == {"top_k": 30}
        assert corpus.prompt_config == {"system_prompt": "Be brief."}
        assert resp.json()["llm_config"]["api_key_encrypted"] == "***"

    async def test_bounds_hold_for_corpus_keys(self, corpus_app) -> None:
        corpus = _corpus(retrieval_config={"top_k": 30})

        resp = await self._patch(corpus_app, corpus, {"retrieval_config": {"mmr_lambda": 3.0}})

        assert resp.status_code == 422
        assert corpus.retrieval_config == {"top_k": 30}

    async def test_llm_config_is_not_writable_with_a_corpus_key(self, corpus_app) -> None:
        corpus = _corpus(llm_config=dict(STORED_LLM))

        resp = await self._patch(corpus_app, corpus, {"llm_config": {"api_key": None}})

        # An unknown field is refused, naming it (it used to be ignored silently).
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"][0]["loc"][-1] == "llm_config"
        assert corpus.llm_config == STORED_LLM
