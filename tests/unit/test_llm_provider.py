"""Unit tests for LLM provider layer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.core.exceptions import LLMError
from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
from callobuzz_rag.llm.models import LLMMessage, LLMResponse, LLMUsage


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.llm_model = "anthropic/claude-sonnet-4-20250514"
    settings.llm_api_key.get_secret_value.return_value = "test-key"
    settings.llm_api_base = None
    settings.llm_temperature = 0.7
    settings.llm_max_tokens = 2048
    settings.llm_fast_model = ""
    settings.llm_fast_temperature = 0.0
    settings.llm_fast_max_tokens = 512
    settings.llm_timeout_seconds = 120.0
    settings.llm_fast_timeout_seconds = 30.0
    settings.llm_provider = None
    return settings


@pytest.fixture
def provider(mock_settings: MagicMock) -> LiteLLMProvider:
    return LiteLLMProvider(mock_settings)


class TestLLMModels:
    def test_llm_message(self) -> None:
        msg = LLMMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_llm_usage_defaults(self) -> None:
        usage = LLMUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_llm_response(self) -> None:
        resp = LLMResponse(content="Hi", model="test-model")
        assert resp.content == "Hi"
        assert resp.finish_reason == "stop"
        assert resp.usage.total_tokens == 0


class TestLLMProviderABC:
    def test_is_abstract(self) -> None:
        assert LLMProvider.__abstractmethods__ == {
            "complete",
            "complete_stream",
            "complete_json",
            "health_check",
        }


class TestLiteLLMProvider:
    def test_init(self, provider: LiteLLMProvider) -> None:
        assert provider._model == "anthropic/claude-sonnet-4-20250514"
        assert provider._api_key == "test-key"
        assert provider._api_base is None
        assert provider._temperature == 0.7
        assert provider._max_tokens == 2048

    def test_fast_model_fallback(self, mock_settings: MagicMock) -> None:
        mock_settings.llm_fast_model = ""
        p = LiteLLMProvider(mock_settings)
        assert p._fast_model == p._model

    def test_fast_model_explicit(self, mock_settings: MagicMock) -> None:
        mock_settings.llm_fast_model = "gpt-4o-mini"
        p = LiteLLMProvider(mock_settings)
        assert p._fast_model == "gpt-4o-mini"

    def test_build_kwargs(self, provider: LiteLLMProvider) -> None:
        msgs = [LLMMessage(role="user", content="test")]
        kwargs = provider._build_kwargs(msgs, model=None, temperature=None, max_tokens=None)
        assert kwargs["model"] == "anthropic/claude-sonnet-4-20250514"
        assert kwargs["temperature"] == 0.7
        assert kwargs["max_tokens"] == 2048
        assert kwargs["messages"] == [{"role": "user", "content": "test"}]
        assert kwargs["api_key"] == "test-key"
        assert "api_base" not in kwargs

    def test_build_kwargs_with_overrides(self, provider: LiteLLMProvider) -> None:
        msgs = [LLMMessage(role="user", content="test")]
        kwargs = provider._build_kwargs(msgs, model="gpt-4o", temperature=0.1, max_tokens=100)
        assert kwargs["model"] == "gpt-4o"
        assert kwargs["temperature"] == 0.1
        assert kwargs["max_tokens"] == 100

    def test_build_kwargs_with_api_base(self, mock_settings: MagicMock) -> None:
        mock_settings.llm_api_base = "http://localhost:11434"
        p = LiteLLMProvider(mock_settings)
        msgs = [LLMMessage(role="user", content="test")]
        kwargs = p._build_kwargs(msgs, model=None, temperature=None, max_tokens=None)
        assert kwargs["api_base"] == "http://localhost:11434"

    @pytest.mark.asyncio
    async def test_complete_success(self, provider: LiteLLMProvider) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello back"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "anthropic/claude-sonnet-4-20250514"
        mock_response.usage = MagicMock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_response.usage.total_tokens = 15

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.return_value = mock_response
            result = await provider.complete([LLMMessage(role="user", content="Hello")])

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello back"
        assert result.usage.total_tokens == 15

    @pytest.mark.asyncio
    async def test_complete_raises_llm_error(self, provider: LiteLLMProvider) -> None:
        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.side_effect = Exception("API timeout")
            with pytest.raises(LLMError, match="LLM completion failed"):
                await provider.complete([LLMMessage(role="user", content="Hello")])

    @pytest.mark.asyncio
    async def test_complete_json_sets_format(self, provider: LiteLLMProvider) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"key": "value"}'
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "test"
        mock_response.usage = None

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.return_value = mock_response
            result = await provider.complete_json([LLMMessage(role="user", content="Return JSON")])

            # Verify response_format was passed
            call_kwargs = mock_acomp.call_args[1]
            assert call_kwargs["response_format"] == {"type": "json_object"}

        assert result.content == '{"key": "value"}'

    @pytest.mark.asyncio
    async def test_health_check_success(self, provider: LiteLLMProvider) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "pong"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "test"
        mock_response.usage = None

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.return_value = mock_response
            assert await provider.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, provider: LiteLLMProvider) -> None:
        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acomp:
            mock_acomp.side_effect = Exception("unreachable")
            assert await provider.health_check() is False

    def test_parse_response_no_usage(self, provider: LiteLLMProvider) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "response"
        mock_response.choices[0].finish_reason = "stop"
        mock_response.model = "test"
        mock_response.usage = None

        result = LiteLLMProvider._parse_response(mock_response)
        assert result.usage.total_tokens == 0


class TestLiteLLMProviderStream:
    @pytest.mark.asyncio
    async def test_complete_stream_yields_chunks(self, provider: LiteLLMProvider) -> None:
        """Mocked litellm.acompletion(stream=True) yields str deltas."""

        async def _fake_stream(*_args: object, **_kwargs: object):
            for text in ["Hello", " world", "!"]:
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = text
                yield chunk

        with patch("litellm.acompletion", side_effect=_fake_stream):
            chunks = []
            async for c in provider.complete_stream([LLMMessage(role="user", content="Hi")]):
                chunks.append(c)
        assert chunks == ["Hello", " world", "!"]

    @pytest.mark.asyncio
    async def test_complete_stream_error_raises_llm_error(self, provider: LiteLLMProvider) -> None:
        """Exception during streaming -> LLMError."""

        async def _failing_stream(*_args: object, **_kwargs: object):
            raise RuntimeError("stream broke")
            yield  # make it a generator

        with (
            patch("litellm.acompletion", side_effect=_failing_stream),
            pytest.raises(LLMError, match="LLM streaming failed"),
        ):
            async for _ in provider.complete_stream([LLMMessage(role="user", content="Hi")]):
                pass

    @pytest.mark.asyncio
    async def test_complete_stream_empty_deltas_filtered(self, provider: LiteLLMProvider) -> None:
        """Chunks with empty content are not yielded."""

        async def _stream_with_empty(*_args: object, **_kwargs: object):
            for text in ["Hello", "", None, "world"]:
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = text
                yield chunk

        with patch("litellm.acompletion", side_effect=_stream_with_empty):
            chunks = []
            async for c in provider.complete_stream([LLMMessage(role="user", content="Hi")]):
                chunks.append(c)
        assert chunks == ["Hello", "world"]


class TestLLMError:
    def test_inherits_rag_kernel_error(self) -> None:
        from callobuzz_rag.core.exceptions import RAGKernelError

        err = LLMError("test")
        assert isinstance(err, RAGKernelError)


class TestFastCallOptions:
    """Query understanding is cheap, and never truncated."""

    def test_non_reasoning_model_is_capped_at_temperature_zero(
        self, mock_settings: MagicMock
    ) -> None:
        mock_settings.llm_fast_model = "openrouter/openai/gpt-4.1-nano"
        options = LiteLLMProvider(mock_settings).fast_call_options()
        assert options == {
            "model": "openrouter/openai/gpt-4.1-nano",
            "temperature": 0.0,
            "max_tokens": 512,
            "timeout": 30.0,
            "thinking": "off",
        }

    def test_reasoning_model_is_not_capped(self, mock_settings: MagicMock) -> None:
        # The engine's own .env.example names this one. A cap would cut its
        # reasoning off and return empty JSON.
        mock_settings.llm_fast_model = "openrouter/openai/gpt-oss-20b"
        options = LiteLLMProvider(mock_settings).fast_call_options()
        assert options == {
            "model": "openrouter/openai/gpt-oss-20b",
            "temperature": 0.0,
            "timeout": 30.0,
            # Not capped, but told not to deliberate over a search string (gpt-oss's floor,
            # low, is what the call then runs at).
            "thinking": "off",
        }

    def test_unknown_model_is_not_capped(self, mock_settings: MagicMock) -> None:
        mock_settings.llm_fast_model = "openrouter/nobody/unmapped-model"
        options = LiteLLMProvider(mock_settings).fast_call_options()
        assert "max_tokens" not in options
        assert options["temperature"] == 0.0

    def test_resolved_config_carries_the_fast_settings(self) -> None:
        from callobuzz_rag.core.config_resolver import ResolvedLLMConfig

        provider = LiteLLMProvider.from_resolved_config(
            ResolvedLLMConfig(
                model="openrouter/openai/gpt-oss-120b",
                api_key="k",
                base_url=None,
                temperature=0.7,
                max_tokens=2048,
                fast_model="openrouter/openai/gpt-4.1-nano",
                fast_temperature=0.0,
                fast_max_tokens=300,
            )
        )
        assert provider.fast_call_options()["max_tokens"] == 300

    @pytest.mark.asyncio
    async def test_classifier_uses_the_fast_options(self, mock_settings: MagicMock) -> None:
        from callobuzz_rag.retrieval.query_understanding import QueryClassifier

        mock_settings.llm_fast_model = "openrouter/openai/gpt-4.1-nano"
        provider = LiteLLMProvider(mock_settings)
        response = LLMResponse(
            content='{"complexity": "simple"}',
            model="m",
            usage=LLMUsage(),
        )
        with patch.object(
            provider, "complete_json", AsyncMock(return_value=response)
        ) as complete_json:
            await QueryClassifier(provider).classify("where is my box")
        assert complete_json.call_args.kwargs == {
            "model": "openrouter/openai/gpt-4.1-nano",
            "temperature": 0.0,
            "max_tokens": 512,
            "timeout": 30.0,
            "thinking": "off",
        }
