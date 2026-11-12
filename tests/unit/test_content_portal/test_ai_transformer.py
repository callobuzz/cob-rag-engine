"""Tests for AI Transformer Service (CP-06)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.content_portal.services.ai_transformer import (
    AITransformer,
    TokenTracker,
    TransformError,
    TransformResult,
    _call_llm,
    _fetch_url_content,
    _parse_json_response,
    _resolve_llm_config,
)
from callobuzz_rag.core.config import Settings

# ---------------------------------------------------------------------------
# TestTokenTracker
# ---------------------------------------------------------------------------


class TestTokenTracker:
    def test_record_step(self) -> None:
        tracker = TokenTracker()
        tracker.record("step1", prompt_tokens=100, completion_tokens=50)
        assert tracker.steps["step1"]["prompt_tokens"] == 100
        assert tracker.steps["step1"]["completion_tokens"] == 50
        assert tracker.steps["step1"]["total_tokens"] == 150

    def test_total_across_steps(self) -> None:
        tracker = TokenTracker()
        tracker.record("step1", 100, 50)
        tracker.record("step2", 200, 100)
        assert tracker.total() == 450

    def test_to_dict(self) -> None:
        tracker = TokenTracker()
        tracker.record("step1", 10, 20)
        d = tracker.to_dict()
        assert d["total_tokens"] == 30
        assert "step1" in d["steps"]
        assert d["steps"]["step1"]["total_tokens"] == 30

    def test_empty_tracker(self) -> None:
        tracker = TokenTracker()
        assert tracker.total() == 0
        d = tracker.to_dict()
        assert d["total_tokens"] == 0
        assert d["steps"] == {}


# ---------------------------------------------------------------------------
# TestParseJsonResponse
# ---------------------------------------------------------------------------


class TestParseJsonResponse:
    def test_clean_json(self) -> None:
        result = _parse_json_response('{"key": "value"}', "test")
        assert result == {"key": "value"}

    def test_markdown_json_fences(self) -> None:
        response = '```json\n{"key": "value"}\n```'
        result = _parse_json_response(response, "test")
        assert result == {"key": "value"}

    def test_plain_fences(self) -> None:
        response = '```\n{"key": "value"}\n```'
        result = _parse_json_response(response, "test")
        assert result == {"key": "value"}

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(TransformError, match="invalid JSON"):
            _parse_json_response("not json at all", "test")

    def test_whitespace_handling(self) -> None:
        response = '  \n  {"key": "value"}  \n  '
        result = _parse_json_response(response, "test")
        assert result == {"key": "value"}


# ---------------------------------------------------------------------------
# TestCallLLM
# ---------------------------------------------------------------------------


class TestCallLLM:
    @pytest.mark.asyncio
    async def test_successful_call_with_tracking(self) -> None:
        usage = SimpleNamespace(prompt_tokens=50, completion_tokens=30)
        choice = SimpleNamespace(message=SimpleNamespace(content="hello"))
        mock_response = SimpleNamespace(choices=[choice], usage=usage)

        tracker = TokenTracker()

        with patch(
            "callobuzz_rag.content_portal.services.ai_transformer.litellm",
            create=True,
        ) as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_response)

            with patch.dict("sys.modules", {"litellm": mock_litellm}):
                result = await _call_llm("test-model", "test-key", "prompt", "step1", tracker)

        assert result == "hello"
        assert tracker.steps["step1"]["prompt_tokens"] == 50
        assert tracker.steps["step1"]["completion_tokens"] == 30

    @pytest.mark.asyncio
    async def test_failed_call_raises(self) -> None:
        mock_litellm = MagicMock()
        mock_litellm.acompletion = AsyncMock(side_effect=RuntimeError("API down"))
        tracker = TokenTracker()

        with (
            patch.dict("sys.modules", {"litellm": mock_litellm}),
            pytest.raises(TransformError, match="LLM call failed"),
        ):
            await _call_llm("test-model", "test-key", "prompt", "step1", tracker)

    @pytest.mark.asyncio
    async def test_no_usage_records_zeros(self) -> None:
        choice = SimpleNamespace(message=SimpleNamespace(content="ok"))
        mock_response = SimpleNamespace(choices=[choice], usage=None)
        mock_litellm = MagicMock()
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        tracker = TokenTracker()

        with patch.dict("sys.modules", {"litellm": mock_litellm}):
            await _call_llm("test-model", "test-key", "prompt", "step1", tracker)

        assert tracker.steps["step1"]["prompt_tokens"] == 0
        assert tracker.steps["step1"]["completion_tokens"] == 0


# ---------------------------------------------------------------------------
# TestResolveLLMConfig
# ---------------------------------------------------------------------------


class TestResolveLLMConfig:
    def _make_corpus(self, llm_config: dict | None = None) -> SimpleNamespace:
        return SimpleNamespace(llm_config=llm_config)

    def _make_settings(self, **overrides: object) -> Settings:
        defaults = {
            "llm_model": "default/model",
            "llm_api_key": "global-key",
            "portal_ai_default_model": "",
            "encryption_key": "",
        }
        defaults.update(overrides)
        return Settings(**defaults)  # type: ignore[arg-type]

    def test_default_settings(self) -> None:
        corpus = self._make_corpus()
        settings = self._make_settings()
        model, key = _resolve_llm_config(corpus, settings, None, byok_allowed=True)
        assert model == "default/model"
        assert key == "global-key"

    def test_portal_default_model(self) -> None:
        corpus = self._make_corpus()
        settings = self._make_settings(portal_ai_default_model="portal/model")
        model, _ = _resolve_llm_config(corpus, settings, None, byok_allowed=True)
        assert model == "portal/model"

    def test_corpus_model_overrides(self) -> None:
        # get_portal_ai_model receives corpus.llm_config as corpus_config,
        # then checks corpus_config.get("llm_config", {}).get("model").
        # But _resolve_llm_config passes corpus.llm_config directly,
        # so "model" at top level only matches if portal_ai_model is set
        # OR if we nest it under llm_config. The actual fallback path in
        # get_portal_ai_model checks corpus_config.get("llm_config", {}).get("model"),
        # which means we need {"llm_config": {"model": "corpus/model"}} as the
        # corpus.llm_config value. This is how BYOK corpora store config.
        corpus = self._make_corpus(llm_config={"llm_config": {"model": "corpus/model"}})
        settings = self._make_settings()
        model, _ = _resolve_llm_config(corpus, settings, None, byok_allowed=True)
        assert model == "corpus/model"

    def test_model_override_wins(self) -> None:
        corpus = self._make_corpus(llm_config={"model": "corpus/model"})
        settings = self._make_settings(portal_ai_default_model="portal/model")
        model, _ = _resolve_llm_config(corpus, settings, "override/model", byok_allowed=True)
        assert model == "override/model"

    def test_corpus_byok_key(self) -> None:
        # Encrypt a test key
        from callobuzz_rag.core.secrets import FernetEncryptor, generate_fernet_key

        fernet_key = generate_fernet_key()
        encryptor = FernetEncryptor(fernet_key)
        encrypted = encryptor.encrypt("byok-secret")

        corpus = self._make_corpus(llm_config={"api_key_encrypted": encrypted})
        settings = self._make_settings(encryption_key=fernet_key)
        _, key = _resolve_llm_config(corpus, settings, None, byok_allowed=True)
        assert key == "byok-secret"

    def test_no_api_key_raises(self) -> None:
        corpus = self._make_corpus()
        settings = self._make_settings(llm_api_key="")
        with pytest.raises(TransformError, match="No LLM API key"):
            _resolve_llm_config(corpus, settings, None, byok_allowed=True)


# ---------------------------------------------------------------------------
# TestFetchUrlContent
# ---------------------------------------------------------------------------


class TestFetchUrlContent:
    @pytest.mark.asyncio
    async def test_successful_fetch(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "fetched content"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            result = await _fetch_url_content("https://example.com")
        assert result == "fetched content"

    @pytest.mark.asyncio
    async def test_fetch_failure_raises(self) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("connection error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        mock_httpx = MagicMock()
        mock_httpx.AsyncClient = MagicMock(return_value=mock_client)

        with (
            patch.dict("sys.modules", {"httpx": mock_httpx}),
            pytest.raises(TransformError, match="Failed to fetch URL"),
        ):
            await _fetch_url_content("https://bad.example.com")


# ---------------------------------------------------------------------------
# TestAITransformerPipeline
# ---------------------------------------------------------------------------


class TestAITransformerPipeline:
    def _make_settings(self) -> Settings:
        return Settings(
            llm_model="test/model",
            llm_api_key="test-key",
            portal_ai_max_tokens_per_transform=60000,
        )  # type: ignore[arg-type]

    def _mock_call_llm_responses(self, responses: dict[str, str]) -> AsyncMock:
        """Create a mock _call_llm that returns different responses per step."""

        async def side_effect(
            model: str,
            api_key: str,
            prompt: str,
            step_name: str,
            tracker: TokenTracker,
            **kwargs: object,
        ) -> str:
            tracker.record(step_name, 10, 5)
            return responses.get(step_name, "{}")

        return AsyncMock(side_effect=side_effect)

    @pytest.mark.asyncio
    async def test_full_pipeline(self) -> None:
        responses = {
            "detect_content_type": json.dumps(
                {
                    "content_type": "faq",
                    "language": "en",
                    "complexity": "simple",
                    "recommended_output": "Q&A format",
                }
            ),
            "extract_structure": "## FAQ\n\n**Q: What?**\nA: Something.",
            "enrich_content": json.dumps(
                {
                    "enriched_text": "## FAQ\n\n**Q: What?**\nA: Something.",
                    "flags": [
                        {
                            "type": "vague",
                            "location": "Q1",
                            "description": "vague answer",
                            "severity": "low",
                        }
                    ],
                }
            ),
            "prepare_chunks": json.dumps(
                {
                    "chunks": [
                        {
                            "text": "Q: What? A: Something.",
                            "chunk_index": 0,
                            "metadata": {"section": "FAQ"},
                            "confidence_score": 0.9,
                            "warnings": [],
                        }
                    ]
                }
            ),
            "quality_report": json.dumps(
                {
                    "overall_score": 0.85,
                    "completeness": 0.8,
                    "clarity": 0.9,
                    "structure_quality": 0.85,
                    "issues": [],
                    "recommendations": ["Add more detail"],
                }
            ),
        }

        with patch(
            "callobuzz_rag.content_portal.services.ai_transformer._call_llm",
            self._mock_call_llm_responses(responses),
        ):
            transformer = AITransformer(
                model="test/model",
                api_key="test-key",
                settings=self._make_settings(),
            )
            result = await transformer.transform("Some raw content here")

        assert result.output_type == "faq"
        assert len(result.chunks) == 1
        assert result.quality_report["overall_score"] == 0.85
        assert len(result.enrichment_flags) == 1
        assert result.token_usage["total_tokens"] > 0

    @pytest.mark.asyncio
    async def test_step_failure_raises(self) -> None:
        async def fail_llm(*args: object, **kwargs: object) -> str:
            raise TransformError("LLM exploded")

        with patch(
            "callobuzz_rag.content_portal.services.ai_transformer._call_llm",
            AsyncMock(side_effect=fail_llm),
        ):
            transformer = AITransformer(
                model="test/model",
                api_key="test-key",
                settings=self._make_settings(),
            )
            with pytest.raises(TransformError):
                await transformer.transform("content")

    @pytest.mark.asyncio
    async def test_cross_doc_stub_returns_empty(self) -> None:
        transformer = AITransformer(
            model="test/model",
            api_key="test-key",
            settings=self._make_settings(),
        )
        result = await transformer._step_cross_document_intelligence("text")
        assert result == []

    @pytest.mark.asyncio
    async def test_content_type_truncation(self) -> None:
        """Verify detection step truncates content to 3000 chars."""
        long_content = "x" * 5000
        captured_prompts: list[str] = []

        async def capture_llm(
            model: str,
            api_key: str,
            prompt: str,
            step_name: str,
            tracker: TokenTracker,
            **kwargs: object,
        ) -> str:
            captured_prompts.append(prompt)
            tracker.record(step_name, 10, 5)
            return json.dumps(
                {
                    "content_type": "general",
                    "language": "en",
                    "complexity": "simple",
                    "recommended_output": "",
                }
            )

        with patch(
            "callobuzz_rag.content_portal.services.ai_transformer._call_llm",
            AsyncMock(side_effect=capture_llm),
        ):
            transformer = AITransformer(
                model="test/model",
                api_key="test-key",
                settings=self._make_settings(),
            )
            tracker = TokenTracker()
            await transformer._step_detect_content_type(long_content, tracker)

        # The prompt should contain truncated content (3000 x's, not 5000)
        assert "x" * 3000 in captured_prompts[0]
        assert "x" * 3001 not in captured_prompts[0]


# ---------------------------------------------------------------------------
# TestTransformResult
# ---------------------------------------------------------------------------


class TestTransformResult:
    def test_default_values(self) -> None:
        result = TransformResult()
        assert result.output_type == ""
        assert result.structured_text == ""
        assert result.metadata == {}
        assert result.chunks == []
        assert result.quality_report == {}
        assert result.token_usage == {}
        assert result.enrichment_flags == []
