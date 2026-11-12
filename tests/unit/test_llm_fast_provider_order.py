"""Pinning the fast model's OpenRouter host (RAG_LLM_FAST_PROVIDER_ORDER,
llm_config.fast_provider_order): sent as OpenRouter's `provider.order`, only for OpenRouter,
only on fast-model calls, with fallbacks allowed."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from callobuzz_rag.api.schemas.corpus import LLMConfigInput
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ConfigResolver
from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
from callobuzz_rag.llm.models import LLMMessage

MSGS = [LLMMessage(role="user", content="hi")]
PIN = {"provider": {"order": ["groq", "cerebras"], "allow_fallbacks": True}}


def _settings(**over: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_provider": "openrouter",
        "llm_model": "openai/gpt-oss-20b",
        "llm_fast_model": "meta-llama/llama-3.3-70b-instruct",
        "llm_api_key": "sk-test",
        **over,
    }
    return Settings(_env_file=None, **values)


def _reply() -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")],
        usage=None,
        model="m",
    )


class TestSetting:
    def test_a_comma_list_is_split_trimmed_and_lowered(self) -> None:
        s = _settings(llm_fast_provider_order=" Groq, cerebras ,,deepinfra/turbo ")
        assert s.llm_fast_provider_order == ["groq", "cerebras", "deepinfra/turbo"]

    def test_empty_means_no_pin(self) -> None:
        assert _settings().llm_fast_provider_order == []

    def test_a_bad_slug_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _settings(llm_fast_provider_order="groq; rm -rf")


class TestKnowledgeBaseField:
    def test_accepts_a_list_of_slugs(self) -> None:
        cfg = LLMConfigInput(fast_provider_order=["Groq", "deepinfra/turbo"])
        assert cfg.fast_provider_order == ["groq", "deepinfra/turbo"]

    @pytest.mark.parametrize("bad", [["groq x"], [""], ["a"] * 9])
    def test_refuses_bad_lists(self, bad: list[str]) -> None:
        with pytest.raises(ValidationError):
            LLMConfigInput(fast_provider_order=bad)

    def test_the_knowledge_base_wins_and_an_empty_list_clears_the_pin(self) -> None:
        resolver = ConfigResolver(_settings(llm_fast_provider_order="groq"))
        assert resolver.resolve_llm(corpus_llm_config={}).fast_provider_order == ["groq"]
        kb = resolver.resolve_llm(corpus_llm_config={"fast_provider_order": ["cerebras"]})
        assert kb.fast_provider_order == ["cerebras"]
        cleared = resolver.resolve_llm(corpus_llm_config={"fast_provider_order": []})
        assert cleared.fast_provider_order == []


class TestSentToOpenRouter:
    async def test_fast_calls_carry_the_provider_order(self) -> None:
        provider = LiteLLMProvider(_settings(llm_fast_provider_order="groq,cerebras"))
        fake = AsyncMock(return_value=_reply())
        with patch("litellm.acompletion", fake):
            await provider.complete_json(MSGS, **provider.fast_call_options())
        assert fake.await_args.kwargs["extra_body"] == PIN

    async def test_answer_calls_on_the_main_model_are_not_pinned(self) -> None:
        provider = LiteLLMProvider(_settings(llm_fast_provider_order="groq"))
        fake = AsyncMock(return_value=_reply())
        with patch("litellm.acompletion", fake):
            await provider.complete(MSGS)
        assert "extra_body" not in fake.await_args.kwargs

    async def test_other_providers_never_get_it(self) -> None:
        provider = LiteLLMProvider(
            _settings(
                llm_provider="openai", llm_fast_model="gpt-4.1-mini", llm_fast_provider_order="groq"
            )
        )
        fake = AsyncMock(return_value=_reply())
        with patch("litellm.acompletion", fake):
            await provider.complete_json(MSGS, **provider.fast_call_options())
        assert "extra_body" not in fake.await_args.kwargs

    async def test_no_fast_model_set_means_no_pin(self) -> None:
        """The main model doing helper work is the answer model's host's business."""
        provider = LiteLLMProvider(_settings(llm_fast_model="", llm_fast_provider_order="groq"))
        fake = AsyncMock(return_value=_reply())
        with patch("litellm.acompletion", fake):
            await provider.complete_json(MSGS, **provider.fast_call_options())
        assert "provider" not in fake.await_args.kwargs.get("extra_body", {})

    async def test_the_pin_and_the_thinking_switch_share_the_body(self) -> None:
        provider = LiteLLMProvider(
            _settings(llm_fast_model="openai/gpt-oss-20b", llm_fast_provider_order="groq")
        )
        fake = AsyncMock(return_value=_reply())
        with patch("litellm.acompletion", fake):
            await provider.complete_json(MSGS, **provider.fast_call_options())
        assert fake.await_args.kwargs["extra_body"] == {
            "provider": {"order": ["groq"], "allow_fallbacks": True},
            "reasoning": {"effort": "low"},
        }

    async def test_resolved_config_carries_it(self) -> None:
        resolved = ConfigResolver(_settings()).resolve_llm(
            corpus_llm_config={"fast_provider_order": ["groq", "cerebras"]}
        )
        provider = LiteLLMProvider.from_resolved_config(resolved)
        fake = AsyncMock(return_value=_reply())
        with patch("litellm.acompletion", fake):
            await provider.complete_json(MSGS, **provider.fast_call_options())
        assert fake.await_args.kwargs["extra_body"] == PIN
