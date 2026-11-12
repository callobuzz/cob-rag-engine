"""Unit tests for Corpus CRUD v2 endpoints.

Tests cover: API key generation, Qdrant collection creation, LLM config
encryption, prompt config, detail redaction, update, delete, key rotation,
and admin-only access enforcement.
"""

import datetime
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

pytestmark = pytest.mark.usefixtures("unlimited_plan")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_corpus(
    corpus_id=None,
    name="Test Corpus",
    description=None,
    api_key_hash="hashed-key",
    qdrant_collection="corpus_abcd1234",
    llm_config=None,
    prompt_config=None,
):
    """Create a mock Corpus object with standard attributes."""
    corpus = MagicMock()
    corpus.id = corpus_id or uuid4()
    corpus.name = name
    corpus.description = description
    corpus.api_key_hash = api_key_hash
    corpus.qdrant_collection = qdrant_collection
    corpus.llm_config = llm_config
    corpus.prompt_config = prompt_config
    corpus.created_at = datetime.datetime.now(datetime.UTC)
    return corpus


def _mock_admin_request() -> MagicMock:
    """A fake Request authenticated as admin, with no tenant.

    A bare `MagicMock()` is not safe here. The routes read
    `getattr(request.state, "tenant", None)`, and on a MagicMock that auto-creates a
    truthy child mock — so production code took the tenant branch and raised
    `403: Corpus does not belong to this tenant` (or compared a coroutine to a
    MagicMock) in tests that were never about tenants. `state` must be pinned.
    """
    request = MagicMock()
    request.state.tenant = None
    request.state.corpus = None
    request.state.is_admin = True
    return request


# ---------------------------------------------------------------------------
# Schema Tests
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_create_corpus_request_with_llm_config(self) -> None:
        """CreateCorpusRequest should accept optional LLM config."""
        from callobuzz_rag.api.schemas.corpus import CreateCorpusRequest, LLMConfigInput

        req = CreateCorpusRequest(
            name="My Corpus",
            llm_config=LLMConfigInput(
                model="anthropic/claude-sonnet-4-20250514",
                api_key="sk-test-123",
                temperature=0.5,
            ),
        )
        assert req.llm_config is not None
        assert req.llm_config.model == "anthropic/claude-sonnet-4-20250514"
        assert req.llm_config.api_key == "sk-test-123"

    def test_create_corpus_request_with_prompt_config(self) -> None:
        """CreateCorpusRequest should accept optional prompt config."""
        from callobuzz_rag.api.schemas.corpus import CreateCorpusRequest, PromptConfigInput

        req = CreateCorpusRequest(
            name="My Corpus",
            prompt_config=PromptConfigInput(
                system_prompt="You are a helpful assistant.",
                response_language="he",
            ),
        )
        assert req.prompt_config is not None
        assert req.prompt_config.system_prompt == "You are a helpful assistant."
        assert req.prompt_config.response_language == "he"

    def test_create_corpus_request_minimal(self) -> None:
        """CreateCorpusRequest with only required fields."""
        from callobuzz_rag.api.schemas.corpus import CreateCorpusRequest

        req = CreateCorpusRequest(name="Simple Corpus")
        assert req.name == "Simple Corpus"
        assert req.llm_config is None
        assert req.prompt_config is None

    def test_create_corpus_response_has_api_key(self) -> None:
        """CreateCorpusResponse must include the api_key field."""
        from callobuzz_rag.api.schemas.corpus import CreateCorpusResponse

        resp = CreateCorpusResponse(
            id=uuid4(),
            name="Test",
            api_key="ck_abcd1234_test123",
            qdrant_collection="corpus_abcd1234",
            created_at="2026-03-07T00:00:00Z",
        )
        assert resp.api_key == "ck_abcd1234_test123"

    def test_update_corpus_request_all_optional(self) -> None:
        """UpdateCorpusRequest should accept all fields as optional."""
        from callobuzz_rag.api.schemas.corpus import UpdateCorpusRequest

        req = UpdateCorpusRequest()
        assert req.name is None
        assert req.description is None
        assert req.llm_config is None
        assert req.prompt_config is None

    def test_corpus_detail_response_has_config_fields(self) -> None:
        """CorpusDetailResponse should include llm_config and prompt_config."""
        from callobuzz_rag.api.schemas.corpus import CorpusDetailResponse

        resp = CorpusDetailResponse(
            id=uuid4(),
            name="Test",
            description=None,
            qdrant_collection="corpus_abcd1234",
            llm_config={"model": "gpt-4", "api_key_encrypted": "***"},
            prompt_config={"system_prompt": "Be helpful."},
            created_at="2026-03-07T00:00:00Z",
            tenant_id=uuid4(),
            tenant_name="Acme",
        )
        assert resp.llm_config["api_key_encrypted"] == "***"
        assert resp.prompt_config["system_prompt"] == "Be helpful."

    def test_rotate_key_response_has_api_key(self) -> None:
        """RotateKeyResponse must include the new api_key."""
        from callobuzz_rag.api.schemas.corpus import RotateKeyResponse

        resp = RotateKeyResponse(api_key="ck_abcd1234_newkey456")
        assert resp.api_key == "ck_abcd1234_newkey456"

    def test_llm_config_temperature_validation(self) -> None:
        """Temperature must be between 0.0 and 2.0."""
        from pydantic import ValidationError

        from callobuzz_rag.api.schemas.corpus import LLMConfigInput

        with pytest.raises(ValidationError):
            LLMConfigInput(temperature=3.0)

        with pytest.raises(ValidationError):
            LLMConfigInput(temperature=-0.1)

        # Valid
        config = LLMConfigInput(temperature=1.5)
        assert config.temperature == 1.5

    def test_llm_config_max_tokens_validation(self) -> None:
        """max_tokens must be >= 1."""
        from pydantic import ValidationError

        from callobuzz_rag.api.schemas.corpus import LLMConfigInput

        with pytest.raises(ValidationError):
            LLMConfigInput(max_tokens=0)

        config = LLMConfigInput(max_tokens=4096)
        assert config.max_tokens == 4096


# ---------------------------------------------------------------------------
# Route Logic Tests (mocked DB + providers)
# ---------------------------------------------------------------------------


class TestCreateCorpus:
    def test_create_corpus_returns_api_key(self) -> None:
        """POST /corpora should return a generated API key in the response."""
        from callobuzz_rag.api.schemas.corpus import CreateCorpusResponse

        # Verify schema accepts api_key
        resp = CreateCorpusResponse(
            id=uuid4(),
            name="Test",
            api_key="ck_abcd1234_generated123",
            qdrant_collection="corpus_abcd1234",
            created_at="2026-03-07T00:00:00Z",
        )
        assert resp.api_key.startswith("ck_")

    def test_create_corpus_generates_qdrant_collection(self) -> None:
        """Collection name should follow pattern corpus_{id[:8]}."""
        corpus_id = uuid4()
        expected_collection = f"corpus_{str(corpus_id)[:8]}"
        assert expected_collection.startswith("corpus_")
        assert len(expected_collection) == len("corpus_") + 8


class TestCreateCorpusWithConfig:
    def test_create_corpus_with_llm_config(self) -> None:
        """LLM config should be serializable as dict for JSONB storage."""
        from callobuzz_rag.api.schemas.corpus import LLMConfigInput

        config = LLMConfigInput(
            model="anthropic/claude-sonnet-4-20250514",
            api_key="sk-test-key",
            temperature=0.7,
            max_tokens=2048,
        )
        dumped = config.model_dump(exclude_none=True)
        assert dumped["model"] == "anthropic/claude-sonnet-4-20250514"
        assert dumped["api_key"] == "sk-test-key"
        assert dumped["temperature"] == 0.7
        assert dumped["max_tokens"] == 2048

    def test_create_corpus_llm_key_encrypted(self) -> None:
        """LLM API key should be encrypted before storage via FernetEncryptor."""
        from callobuzz_rag.api.routes.corpus import _encrypt_llm_key

        mock_request = MagicMock()
        mock_request.app.state.settings.encryption_key = "test-key-32-bytes-long-enough!!"

        with patch("callobuzz_rag.core.secrets.FernetEncryptor") as mock_fernet_cls:
            mock_encryptor = MagicMock()
            mock_encryptor.encrypt.return_value = "encrypted_value"
            mock_fernet_cls.return_value = mock_encryptor

            config_dict = {"model": "gpt-4", "api_key": "sk-plain-key"}
            result = _encrypt_llm_key(config_dict, mock_request)

            assert "api_key" not in result
            assert result["api_key_encrypted"] == "encrypted_value"
            mock_encryptor.encrypt.assert_called_once_with("sk-plain-key")

    def test_create_corpus_with_prompt_config(self) -> None:
        """Prompt config should be serializable as dict for JSONB storage."""
        from callobuzz_rag.api.schemas.corpus import PromptConfigInput

        config = PromptConfigInput(
            system_prompt="You are a customer service bot.",
            constraints="Always respond in Hebrew.",
            response_language="he",
        )
        dumped = config.model_dump(exclude_none=True)
        assert dumped["system_prompt"] == "You are a customer service bot."
        assert dumped["constraints"] == "Always respond in Hebrew."
        assert dumped["response_language"] == "he"

    def test_encrypt_llm_key_no_key_noop(self) -> None:
        """If no api_key in config, encryption is a no-op."""
        from callobuzz_rag.api.routes.corpus import _encrypt_llm_key

        mock_request = MagicMock()
        config_dict = {"model": "gpt-4", "temperature": 0.5}
        result = _encrypt_llm_key(config_dict, mock_request)
        assert result == config_dict

    def test_encrypt_llm_key_empty_dict(self) -> None:
        """Empty dict should be returned as-is."""
        from callobuzz_rag.api.routes.corpus import _encrypt_llm_key

        mock_request = MagicMock()
        result = _encrypt_llm_key({}, mock_request)
        assert result == {}

    def test_encrypt_llm_key_none_value(self) -> None:
        """None api_key value should not trigger encryption."""
        from callobuzz_rag.api.routes.corpus import _encrypt_llm_key

        mock_request = MagicMock()
        config_dict = {"model": "gpt-4", "api_key": None}
        result = _encrypt_llm_key(config_dict, mock_request)
        assert result["api_key"] is None


class TestGetCorpus:
    def test_get_corpus_redacts_llm_key(self) -> None:
        """GET /corpora/{id} should redact the LLM API key to '***'."""
        from callobuzz_rag.api.routes.corpus import _redact_llm_config

        config = {
            "model": "gpt-4",
            "api_key_encrypted": "encrypted_secret_value",
            "temperature": 0.7,
        }
        redacted = _redact_llm_config(config)

        assert redacted is not None
        assert redacted["api_key_encrypted"] == "***"
        assert redacted["model"] == "gpt-4"
        assert redacted["temperature"] == 0.7

    def test_redact_llm_config_none(self) -> None:
        """None config should return None."""
        from callobuzz_rag.api.routes.corpus import _redact_llm_config

        assert _redact_llm_config(None) is None

    def test_redact_llm_config_no_key(self) -> None:
        """Config without api_key should be returned as-is."""
        from callobuzz_rag.api.routes.corpus import _redact_llm_config

        config = {"model": "gpt-4"}
        redacted = _redact_llm_config(config)
        assert redacted == {"model": "gpt-4"}

    def test_redact_does_not_mutate_original(self) -> None:
        """Redaction should not modify the original dict."""
        from callobuzz_rag.api.routes.corpus import _redact_llm_config

        original = {"model": "gpt-4", "api_key_encrypted": "encrypted_secret"}
        _redact_llm_config(original)
        assert original["api_key_encrypted"] == "encrypted_secret"


class TestUpdateCorpus:
    def test_update_corpus_llm_config(self) -> None:
        """UpdateCorpusRequest should accept LLM config changes."""
        from callobuzz_rag.api.schemas.corpus import LLMConfigInput, UpdateCorpusRequest

        req = UpdateCorpusRequest(
            llm_config=LLMConfigInput(temperature=0.3),
        )
        assert req.llm_config is not None
        assert req.llm_config.temperature == 0.3

    def test_update_corpus_prompt_config(self) -> None:
        """UpdateCorpusRequest should accept prompt config changes."""
        from callobuzz_rag.api.schemas.corpus import PromptConfigInput, UpdateCorpusRequest

        req = UpdateCorpusRequest(
            prompt_config=PromptConfigInput(response_language="es"),
        )
        assert req.prompt_config is not None
        assert req.prompt_config.response_language == "es"


class TestDeleteCorpus:
    def test_delete_corpus_returns_204(self) -> None:
        """DELETE /corpora/{id} route should be configured for 204 status."""
        from callobuzz_rag.api.routes.corpus import router

        delete_routes = [
            r for r in router.routes if hasattr(r, "methods") and "DELETE" in r.methods
        ]
        assert len(delete_routes) >= 1
        # Verify the route path pattern
        delete_route = delete_routes[0]
        assert "corpus_id" in delete_route.path

    @pytest.mark.asyncio
    async def test_delete_corpus_calls_qdrant_delete(self) -> None:
        """DELETE should call vector_store.delete_collection."""
        from callobuzz_rag.api.routes.corpus import delete_corpus

        corpus = _mock_corpus(qdrant_collection="corpus_abcd1234")
        mock_session = AsyncMock()
        mock_session.get.return_value = corpus
        # DELETE ... RETURNING (id, collection)
        mock_session.execute.return_value = MagicMock(
            all=MagicMock(return_value=[(corpus.id, "corpus_abcd1234")])
        )
        mock_request = _mock_admin_request()
        mock_vector_store = AsyncMock()
        mock_object_store = AsyncMock()
        mock_request.app.state.vector_store = mock_vector_store
        mock_request.app.state.object_store = mock_object_store

        await delete_corpus(
            corpus_id=corpus.id,
            request=mock_request,
            session=mock_session,
        )

        mock_vector_store.delete_collection.assert_called_once_with("corpus_abcd1234")

    @pytest.mark.asyncio
    async def test_delete_corpus_queues_its_stored_files_for_deletion(self) -> None:
        """Its documents' files and its Content Portal staged uploads, after the commit,
        through the bounded cleanup task (never inline in the request)."""
        from callobuzz_rag.api.routes.corpus import delete_corpus

        corpus = _mock_corpus(qdrant_collection="corpus_abcd1234")
        mock_session = AsyncMock()
        mock_session.get.return_value = corpus
        # DELETE ... RETURNING (id, collection)
        mock_session.execute.return_value = MagicMock(
            all=MagicMock(return_value=[(corpus.id, "corpus_abcd1234")])
        )
        mock_request = _mock_admin_request()
        mock_request.app.state.vector_store = AsyncMock()
        mock_request.app.state.object_store = AsyncMock()

        with patch(
            "callobuzz_rag.storage.cleanup.schedule_prefix_cleanup", new=AsyncMock()
        ) as schedule:
            await delete_corpus(corpus_id=corpus.id, request=mock_request, session=mock_session)

        bucket = mock_request.app.state.settings.s3_bucket
        schedule.assert_awaited_once()
        assert schedule.call_args.args == (
            bucket,
            [f"corpus/{corpus.id}/", f"portal/{corpus.id}/"],
        )
        mock_request.app.state.object_store.delete_prefix.assert_not_called()
        mock_session.commit.assert_awaited()


class TestCreateCorpusProviderCalls:
    @pytest.mark.asyncio
    async def test_create_corpus_calls_qdrant_create_collection(self) -> None:
        """POST /corpora should call vector_store.create_collection."""
        from callobuzz_rag.api.routes.corpus import create_corpus
        from callobuzz_rag.api.schemas.corpus import CreateCorpusRequest

        tenant_id = uuid4()
        body = CreateCorpusRequest(name="Test Corpus", tenant_id=tenant_id)
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=MagicMock(id=tenant_id, max_corpora=10))
        count = MagicMock()
        count.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=count)
        mock_corpus = _mock_corpus()
        mock_session.flush = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        mock_request = _mock_admin_request()
        mock_vector_store = AsyncMock()
        mock_request.app.state.vector_store = mock_vector_store
        mock_request.app.state.settings.embedding_dimensions = 1024

        with (
            patch("callobuzz_rag.api.routes.corpus.Corpus", return_value=mock_corpus),
            patch(
                "callobuzz_rag.core.secrets.generate_api_key",
                return_value="ck_abcd1234_test",
            ),
            patch(
                "callobuzz_rag.core.secrets.hash_api_key",
                return_value="hashed",
            ),
        ):
            await create_corpus(body=body, request=mock_request, session=mock_session)

        mock_vector_store.create_collection.assert_called_once()


class TestRotateKey:
    def test_rotate_key_returns_new_key(self) -> None:
        """RotateKeyResponse should contain the new API key."""
        from callobuzz_rag.api.schemas.corpus import RotateKeyResponse

        resp = RotateKeyResponse(api_key="ck_abcd1234_rotated789")
        assert resp.api_key == "ck_abcd1234_rotated789"

    def test_old_key_invalid_after_rotation(self) -> None:
        """After rotation, the old hash should not match.

        This is a logical test — rotation replaces the hash entirely,
        so verify_api_key with the old key against the new hash fails.
        """
        from callobuzz_rag.core.secrets import generate_api_key, hash_api_key, verify_api_key

        old_key = generate_api_key(prefix="ck_abcd1234")
        old_hash = hash_api_key(old_key)

        new_key = generate_api_key(prefix="ck_abcd1234")
        new_hash = hash_api_key(new_key)

        # Old key should NOT verify against new hash
        assert not verify_api_key(old_key, new_hash)
        # New key should verify against new hash
        assert verify_api_key(new_key, new_hash)
        # Old key still verifies against old hash
        assert verify_api_key(old_key, old_hash)


class TestAdminRequired:
    """Corpus CRUD is privileged: no route may be reachable without a gate.

    These two asserted `require_admin` by name, which the Phase 8 tenant layer
    replaced with `require_admin_or_tenant` — a tenant has to be able to create and
    manage its own corpora. They were left red rather than updated, which is worse
    than deleting them: a red fitness test proves nothing, and this is the only thing
    standing between a new corpus route and no authentication at all. They now assert
    the rule that actually holds — *some* gate, never none — so adding an ungated
    route still fails, while the deliberate widening no longer does.
    """

    GATES: ClassVar[set[str]] = {"require_admin", "require_admin_or_tenant"}

    @staticmethod
    def _dep_names(route: object) -> list[str]:
        return [
            dep.call.__name__
            for dep in route.dependant.dependencies
            if hasattr(dep, "call") and hasattr(dep.call, "__name__")
        ]

    def test_create_corpus_is_gated(self) -> None:
        """POST /corpora must depend on an authentication gate."""
        from callobuzz_rag.api.routes.corpus import router

        post_routes = [
            r
            for r in router.routes
            if hasattr(r, "methods") and "POST" in r.methods and r.path == "/api/v1/corpora"
        ]
        assert len(post_routes) >= 1

        dep_names = self._dep_names(post_routes[0])
        assert self.GATES & set(dep_names), (
            f"create_corpus must depend on one of {sorted(self.GATES)}. Found: {dep_names}"
        )

    def test_all_corpus_routes_are_gated(self) -> None:
        """No corpus route may be reachable unauthenticated."""
        from callobuzz_rag.api.routes.corpus import router

        ungated = []
        for route in router.routes:
            if not hasattr(route, "dependant"):
                continue
            dep_names = self._dep_names(route)
            if not self.GATES & set(dep_names):
                ungated.append(f"{route.path} {sorted(route.methods)} -> {dep_names}")

        assert ungated == [], (
            "corpus routes reachable without an authentication gate: " + "; ".join(ungated)
        )
