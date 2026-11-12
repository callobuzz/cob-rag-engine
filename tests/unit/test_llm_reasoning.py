"""The older thinking effort names and the fast model for helper calls
(RAG_LLM_REASONING_EFFORT, llm_config.reasoning_effort, RAG_LLM_FAST_MODEL), read through the
thinking control (llm/thinking.py; per-family payloads are in test_llm_thinking.py).

`vendor/thinker` is a model the family table does not know and LiteLLM reports as reasoning,
so it gets LiteLLM's plain `reasoning_effort`, exactly as before."""

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import pytest
from pydantic import ValidationError

from callobuzz_rag.api.schemas.corpus import LLMConfigInput
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ConfigResolver, ResolvedLLMConfig
from callobuzz_rag.core.exceptions import LLMError
from callobuzz_rag.llm import litellm_provider as module
from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
from callobuzz_rag.llm.models import LLMMessage

MSGS = [LLMMessage(role="user", content="hi")]
REASONING = {
    "vendor/thinker",
    "vendor/thinker-small",
    "anthropic/claude-haiku-4-5",
    "claude-haiku-4-5",
    "anthropic/claude-haiku-4.5",
    "gemini/gemini-2.5-flash-lite",
    "gemini/gemini-2.5-flash",
    "gpt-5-mini",
    "openai/gpt-oss-20b",
    "o4-mini",
}


def _param_error() -> Exception:
    return litellm.BadRequestError(
        message="Unsupported parameter: reasoning_effort", model="m", llm_provider="p"
    )


@pytest.fixture(autouse=True)
def _reasoning_models() -> Iterator[None]:
    """Two models LiteLLM 'knows' as reasoning; everything else is not."""
    with patch.object(module, "_supports_reasoning", lambda model, provider: model in REASONING):
        module._REASONING_REJECTED.clear()
        module._warned_fast_unset = False
        yield
    module._REASONING_REJECTED.clear()


def _settings(**over: Any) -> Settings:
    values: dict[str, Any] = {"llm_model": "vendor/thinker", "llm_provider": "", **over}
    return Settings(_env_file=None, **values)


# ----------------------------------------------------------------- settings


class TestSettings:
    def test_default_is_unset(self) -> None:
        assert Settings(_env_file=None).llm_reasoning_effort is None

    @pytest.mark.parametrize("value", ["none", "low", "medium", "high"])
    def test_accepts_the_four_levels(self, value: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_LLM_REASONING_EFFORT", value.upper())
        assert Settings(_env_file=None).llm_reasoning_effort == value

    def test_empty_means_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_LLM_REASONING_EFFORT", "")
        assert Settings(_env_file=None).llm_reasoning_effort is None

    def test_rejects_anything_else(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_LLM_REASONING_EFFORT", "extreme")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


class TestCorpusSchema:
    def test_accepts_the_levels_and_null(self) -> None:
        assert LLMConfigInput(reasoning_effort="low").reasoning_effort == "low"
        assert LLMConfigInput(reasoning_effort=None).reasoning_effort is None

    def test_rejects_other_values(self) -> None:
        with pytest.raises(ValidationError):
            LLMConfigInput(reasoning_effort="extreme")


class TestResolver:
    """The old effort names keep working as thinking levels ("none" = off)."""

    def test_engine_default(self) -> None:
        resolved = ConfigResolver(_settings(llm_reasoning_effort="low")).resolve_llm()
        assert resolved.thinking == "low"

    def test_corpus_beats_engine(self) -> None:
        resolver = ConfigResolver(_settings(llm_reasoning_effort="low"))
        assert resolver.resolve_llm(None, {"reasoning_effort": "high"}).thinking == "high"

    def test_corpus_none_level_is_off(self) -> None:
        resolver = ConfigResolver(_settings(llm_reasoning_effort="high"))
        assert resolver.resolve_llm(None, {"reasoning_effort": "none"}).thinking == "off"

    def test_unset_everywhere_is_none(self) -> None:
        resolved = ConfigResolver(_settings()).resolve_llm()
        assert resolved.thinking is None
        assert resolved.fast_thinking == "off"

    def test_not_overridable_by_header(self) -> None:
        resolver = ConfigResolver(_settings())
        headers = {"X-LLM-Reasoning-Effort": "high", "X-LLM-Thinking": "high"}
        assert resolver.resolve_llm(headers, None).thinking is None

    def test_an_unreadable_stored_value_is_ignored(self) -> None:
        resolver = ConfigResolver(_settings(llm_reasoning_effort="low"))
        assert resolver.resolve_llm(None, {"reasoning_effort": "turbo"}).thinking == "low"

    def test_the_new_name_beats_the_old_on_each_tier(self) -> None:
        resolver = ConfigResolver(_settings(llm_thinking="minimal", llm_reasoning_effort="high"))
        assert resolver.resolve_llm().thinking == "minimal"
        kb = {"thinking": "off", "reasoning_effort": "high"}
        assert resolver.resolve_llm(None, kb).thinking == "off"

    def test_an_old_corpus_value_beats_the_new_engine_setting(self) -> None:
        resolver = ConfigResolver(_settings(llm_thinking="high"))
        assert resolver.resolve_llm(None, {"reasoning_effort": "low"}).thinking == "low"

    def test_fast_thinking_corpus_beats_engine(self) -> None:
        resolver = ConfigResolver(_settings(llm_fast_thinking="minimal"))
        assert resolver.resolve_llm().fast_thinking == "minimal"
        assert resolver.resolve_llm(None, {"fast_thinking": "low"}).fast_thinking == "low"


# ----------------------------------------------------------------- the call


class TestAnswerCalls:
    def test_sent_when_set_and_the_model_reasons(self) -> None:
        provider = LiteLLMProvider(_settings(llm_reasoning_effort="medium"))
        kwargs = provider._build_kwargs(MSGS, model=None, temperature=None, max_tokens=None)
        assert kwargs["reasoning_effort"] == "medium"

    def test_not_sent_when_unset(self) -> None:
        kwargs = LiteLLMProvider(_settings())._build_kwargs(
            MSGS, model=None, temperature=None, max_tokens=None
        )
        assert "reasoning_effort" not in kwargs

    def test_not_sent_to_a_model_that_does_not_reason(self) -> None:
        provider = LiteLLMProvider(_settings(llm_model="vendor/plain", llm_reasoning_effort="high"))
        kwargs = provider._build_kwargs(MSGS, model=None, temperature=None, max_tokens=None)
        assert "reasoning_effort" not in kwargs

    def test_an_explicit_per_call_value_beats_the_default(self) -> None:
        provider = LiteLLMProvider(_settings(llm_reasoning_effort="high"))
        kwargs = provider._build_kwargs(
            MSGS, model=None, temperature=None, max_tokens=None, reasoning_effort="low"
        )
        assert kwargs["reasoning_effort"] == "low"

    def test_from_resolved_config(self) -> None:
        config = ResolvedLLMConfig(
            model="vendor/thinker",
            api_key="k",
            base_url=None,
            temperature=0.7,
            max_tokens=100,
            thinking="high",
        )
        provider = LiteLLMProvider.from_resolved_config(config)
        kwargs = provider._build_kwargs(MSGS, model=None, temperature=None, max_tokens=None)
        assert kwargs["reasoning_effort"] == "high"

    def test_a_mock_settings_object_sends_nothing(self) -> None:
        # Settings stand-ins in other tests are MagicMocks: never forward one.
        mock = MagicMock()
        mock.llm_model = "vendor/thinker"
        mock.llm_api_key.get_secret_value.return_value = "k"
        mock.llm_api_base = None
        mock.llm_fast_model = ""
        mock.llm_provider = None
        kwargs = LiteLLMProvider(mock)._build_kwargs(
            MSGS, model=None, temperature=None, max_tokens=None
        )
        assert "reasoning_effort" not in kwargs

    async def test_retries_without_it_when_the_provider_refuses(self) -> None:
        provider = LiteLLMProvider(_settings(llm_reasoning_effort="high"))
        ok = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="done"), finish_reason="stop")
            ],
            usage=None,
            model="vendor/thinker",
        )
        fake = AsyncMock(
            side_effect=[
                litellm.UnsupportedParamsError(
                    message="reasoning_effort", model="m", llm_provider="p"
                ),
                ok,
            ]
        )
        with patch("litellm.acompletion", fake):
            response = await provider.complete(MSGS)
        assert response.content == "done"
        assert "reasoning_effort" in fake.await_args_list[0].kwargs
        assert "reasoning_effort" not in fake.await_args_list[1].kwargs
        # and it is not tried again for that model
        kwargs = provider._build_kwargs(MSGS, model=None, temperature=None, max_tokens=None)
        assert "reasoning_effort" not in kwargs

    @pytest.mark.parametrize(
        "error",
        [
            litellm.RateLimitError(message="slow down", model="m", llm_provider="p"),
            litellm.Timeout(message="timed out", model="m", llm_provider="p"),
            litellm.AuthenticationError(message="bad key", model="m", llm_provider="p"),
            RuntimeError("network blip"),
            litellm.BadRequestError(message="max_tokens too large", model="m", llm_provider="p"),
        ],
    )
    async def test_an_unrelated_error_is_not_retried_and_does_not_poison(
        self, error: Exception
    ) -> None:
        provider = LiteLLMProvider(_settings(llm_reasoning_effort="high"))
        fake = AsyncMock(side_effect=error)
        with patch("litellm.acompletion", fake), pytest.raises(LLMError):
            await provider.complete(MSGS)
        assert fake.await_count == 1
        assert not module._REASONING_REJECTED
        kwargs = provider._build_kwargs(MSGS, model=None, temperature=None, max_tokens=None)
        assert kwargs["reasoning_effort"] == "high"

    async def test_a_bad_request_about_thinking_is_retried_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider = LiteLLMProvider(_settings(llm_reasoning_effort="high"))
        error = litellm.BadRequestError(
            message="thinking.budget_tokens must be less than max_tokens",
            model="m",
            llm_provider="p",
        )
        ok = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="x"), finish_reason="stop")],
            usage=None,
            model="m",
        )
        with (
            patch("litellm.acompletion", AsyncMock(side_effect=[error, ok])),
            caplog.at_level(logging.WARNING, logger=module.logger.name),
        ):
            assert (await provider.complete(MSGS)).content == "x"
        assert "budget_tokens" in caplog.text

    async def test_streaming_does_not_retry_an_unrelated_error(self) -> None:
        provider = LiteLLMProvider(_settings(llm_reasoning_effort="low"))
        fake = AsyncMock(
            side_effect=litellm.RateLimitError(message="x", model="m", llm_provider="p")
        )
        with patch("litellm.acompletion", fake), pytest.raises(LLMError):
            _ = [c async for c in provider.complete_stream(MSGS)]
        assert fake.await_count == 1
        assert not module._REASONING_REJECTED

    async def test_a_real_failure_without_the_parameter_still_raises(self) -> None:
        provider = LiteLLMProvider(_settings())
        with (
            patch("litellm.acompletion", AsyncMock(side_effect=RuntimeError("boom"))),
            pytest.raises(LLMError, match="boom"),
        ):
            await provider.complete(MSGS)

    async def test_a_failure_with_and_without_the_parameter_raises_the_second_error(self) -> None:
        provider = LiteLLMProvider(_settings(llm_reasoning_effort="low"))
        fake = AsyncMock(side_effect=[_param_error(), RuntimeError("second")])
        with patch("litellm.acompletion", fake), pytest.raises(LLMError, match="second"):
            await provider.complete(MSGS)
        assert fake.await_count == 2

    async def test_streaming_retries_too(self) -> None:
        provider = LiteLLMProvider(_settings(llm_reasoning_effort="low"))

        async def stream() -> Any:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = "ok"
            yield chunk

        fake = AsyncMock(side_effect=[_param_error(), stream()])
        with patch("litellm.acompletion", fake):
            chunks = [c async for c in provider.complete_stream(MSGS)]
        assert chunks == ["ok"]
        assert "reasoning_effort" not in fake.await_args_list[1].kwargs


class TestReasoningTokens:
    def _raw(self, details: Any) -> Any:
        usage = SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=300,
            total_tokens=310,
            completion_tokens_details=details,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="a"), finish_reason="stop")],
            usage=usage,
            model="m",
        )

    def test_reported_by_litellm(self) -> None:
        parsed = LiteLLMProvider._parse_response(self._raw(SimpleNamespace(reasoning_tokens=220)))
        assert parsed.usage.reasoning_tokens == 220

    @pytest.mark.parametrize(
        "details", [None, SimpleNamespace(), SimpleNamespace(reasoning_tokens=None)]
    )
    def test_zero_when_absent(self, details: Any) -> None:
        assert LiteLLMProvider._parse_response(self._raw(details)).usage.reasoning_tokens == 0

    def test_a_mock_usage_object_does_not_leak_through(self) -> None:
        raw = self._raw(MagicMock())
        assert LiteLLMProvider._parse_response(raw).usage.reasoning_tokens == 0


# ------------------------------------------------------------- fast model


class TestFastModel:
    def test_the_fast_model_gets_the_fast_level(self) -> None:
        provider = LiteLLMProvider(_settings(llm_fast_model="vendor/thinker-small"))
        options = provider.fast_call_options()
        assert options["model"] == "vendor/thinker-small"
        assert options["thinking"] == "off"
        assert "max_tokens" not in options  # still uncapped: a reasoning model needs room

    def test_off_is_sent_as_the_cheapest_effort_the_model_accepts(self) -> None:
        provider = LiteLLMProvider(_settings(llm_fast_model="vendor/thinker-small"))
        kwargs = provider._build_kwargs(
            MSGS, model="vendor/thinker-small", temperature=0.0, max_tokens=None, thinking="off"
        )
        assert kwargs["reasoning_effort"] == "none"

    def test_a_plain_fast_model_is_sent_nothing(self) -> None:
        provider = LiteLLMProvider(_settings(llm_fast_model="vendor/plain"))
        kwargs = provider._build_kwargs(
            MSGS, model="vendor/plain", temperature=0.0, max_tokens=None, thinking="off"
        )
        assert "reasoning_effort" not in kwargs and "extra_body" not in kwargs

    def test_unset_fast_model_falls_back_to_main_at_the_fast_level(self) -> None:
        provider = LiteLLMProvider(_settings(llm_model="vendor/thinker"))
        options = provider.fast_call_options()
        assert options["model"] == "vendor/thinker"
        assert options["thinking"] == "off"

    def test_fast_level_is_its_own_whatever_the_answer_level(self) -> None:
        provider = LiteLLMProvider(_settings(llm_reasoning_effort="high"))
        assert provider.fast_call_options()["thinking"] == "off"
        provider = LiteLLMProvider(_settings(llm_thinking="high", llm_fast_thinking="low"))
        assert provider.fast_call_options()["thinking"] == "low"

    def test_unset_fast_model_with_a_reasoning_main_warns_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider = LiteLLMProvider(_settings())
        with caplog.at_level(logging.WARNING, logger=module.logger.name):
            provider.fast_call_options()
            provider.fast_call_options()
            LiteLLMProvider(_settings()).fast_call_options()
        warnings = [r for r in caplog.records if "RAG_LLM_FAST_MODEL" in r.getMessage()]
        assert len(warnings) == 1
        assert "'off'" in warnings[0].getMessage()

    def test_no_warning_when_a_fast_model_is_set(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger=module.logger.name):
            LiteLLMProvider(_settings(llm_fast_model="vendor/plain")).fast_call_options()
        assert not [r for r in caplog.records if "RAG_LLM_FAST_MODEL" in r.getMessage()]

    def test_no_warning_when_the_main_model_does_not_reason(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=module.logger.name):
            LiteLLMProvider(_settings(llm_model="vendor/plain")).fast_call_options()
        assert not [r for r in caplog.records if "RAG_LLM_FAST_MODEL" in r.getMessage()]

    def test_resolved_config_remembers_whether_a_fast_model_was_set(self) -> None:
        resolved = ConfigResolver(_settings(llm_fast_model="vendor/plain")).resolve_llm()
        provider = LiteLLMProvider.from_resolved_config(resolved)
        assert provider.fast_call_options()["model"] == "vendor/plain"

    async def test_complete_json_sends_the_fast_level(self) -> None:
        provider = LiteLLMProvider(_settings(llm_fast_model="vendor/thinker-small"))
        ok = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")],
            usage=None,
            model="m",
        )
        fake = AsyncMock(return_value=ok)
        with patch("litellm.acompletion", fake):
            await provider.complete_json(MSGS, **provider.fast_call_options())
        sent = fake.await_args.kwargs
        assert sent["model"] == "vendor/thinker-small"
        assert sent["reasoning_effort"] == "none"
        assert sent["response_format"] == {"type": "json_object"}


def _json_reply(content: str = '{"ranking": [{"index": 0, "score": 1}]}') -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=None,
        model="m",
    )


class TestHelperThinking:
    """Helper calls get the fast model's level, never the answer level."""

    def test_helper_level_is_the_fast_level(self) -> None:
        provider = LiteLLMProvider(_settings(llm_thinking="high", llm_fast_thinking="minimal"))
        assert provider.helper_thinking("vendor/plain") == "minimal"

    def test_the_answer_level_never_reaches_another_model(self) -> None:
        provider = LiteLLMProvider(_settings(llm_reasoning_effort="high"))
        kwargs = provider._build_kwargs(
            MSGS, model="vendor/thinker-small", temperature=0.0, max_tokens=None
        )
        assert "reasoning_effort" not in kwargs

    async def test_the_reranker_does_not_inherit_the_answer_level(self) -> None:
        from callobuzz_rag.retrieval.reranking.llm import LLMRerankProvider

        provider = LiteLLMProvider(
            _settings(
                llm_api_key="k", llm_reasoning_effort="high", llm_fast_model="vendor/thinker-small"
            )
        )
        fake = AsyncMock(return_value=_json_reply())
        with patch("litellm.acompletion", fake):
            await LLMRerankProvider(provider).rerank("q", ["doc"])
        assert fake.await_args.kwargs["model"] == "vendor/thinker-small"
        assert fake.await_args.kwargs["reasoning_effort"] == "none"

    async def test_the_reranker_on_the_main_model_gets_the_helper_level(self) -> None:
        from callobuzz_rag.retrieval.reranking.llm import LLMRerankProvider

        provider = LiteLLMProvider(_settings(llm_api_key="k", llm_reasoning_effort="high"))
        fake = AsyncMock(return_value=_json_reply())
        with patch("litellm.acompletion", fake):
            await LLMRerankProvider(provider).rerank("q", ["doc"])
        assert fake.await_args.kwargs["reasoning_effort"] == "none"


class TestIsKnownNonReasoning:
    """The public answer-cap question: True only for a model LiteLLM vouches for as plain."""

    def test_base_provider_never_vouches(self) -> None:
        from callobuzz_rag.llm.base import LLMProvider

        assert LLMProvider.is_known_non_reasoning(object(), "any/model") is False  # type: ignore[arg-type]

    def test_litellm_provider_asks_the_model_map(self) -> None:
        provider = LiteLLMProvider(_settings(llm_model="vendor/plain"))
        with patch.object(
            module, "_is_known_non_reasoning", lambda model, p: model == "vendor/plain"
        ):
            assert provider.is_known_non_reasoning("vendor/plain") is True
            assert provider.is_known_non_reasoning("vendor/thinker") is False
            assert provider.is_known_non_reasoning() is True  # defaults to the answer model
