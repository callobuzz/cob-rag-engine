"""The thinking control (llm/thinking.py, RAG_LLM_THINKING, RAG_LLM_FAST_THINKING,
llm_config.thinking / fast_thinking): the exact request each model family gets, the fallback
when a provider refuses it, reasoning kept out of the answer and counted in the cost."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import pytest
from pydantic import ValidationError

from callobuzz_rag.api.schemas.corpus import LLMConfigInput
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import LLMError
from callobuzz_rag.llm import litellm_provider as module
from callobuzz_rag.llm.cost import request_cost_of
from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.llm.stream_stats import collect_stream_stats
from callobuzz_rag.llm.thinking import (
    ReasoningTextFilter,
    check_thinking_off,
    describe,
    family_of,
    parse_thinking,
    reasoning_of,
    strip_reasoning_text,
    thinking_attempts,
)

MSGS = [LLMMessage(role="user", content="hi")]
THINKING_KEYS = ("reasoning_effort", "allowed_openai_params", "thinking")


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    module._REASONING_REJECTED.clear()
    yield
    module._REASONING_REJECTED.clear()


def _reply(content: str = "ok", usage: Any = None) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=usage,
        model="m",
    )


def _provider(model: str, provider: str = "openrouter", **over: Any) -> LiteLLMProvider:
    values: dict[str, Any] = {
        "llm_provider": provider,
        "llm_model": model,
        "llm_api_key": "k",
        **over,
    }
    return LiteLLMProvider(Settings(_env_file=None, **values))


async def _sent(model: str, level: str, provider: str = "openrouter") -> dict[str, Any]:
    """The thinking part of the one request a call at `level` sends."""
    fake = AsyncMock(return_value=_reply())
    with patch("litellm.acompletion", fake):
        await _provider(model, provider).complete(MSGS, thinking=level)
    kwargs = fake.await_args.kwargs
    out = {k: kwargs[k] for k in THINKING_KEYS if k in kwargs}
    if "extra_body" in kwargs:
        out["extra_body"] = kwargs["extra_body"]
    return out


def _or(reasoning: dict[str, Any]) -> dict[str, Any]:
    return {"extra_body": {"reasoning": reasoning}}


def _effort(value: str) -> dict[str, Any]:
    return {"reasoning_effort": value, "allowed_openai_params": ["reasoning_effort"]}


# ------------------------------------------------------------ levels and settings


class TestLevels:
    @pytest.mark.parametrize(
        ("raw", "level"),
        [
            ("off", "off"),
            ("OFF", "off"),
            (" minimal ", "minimal"),
            ("provider_default", "provider-default"),
            ("none", "off"),  # the older effort name
            ("high", "high"),
            ("", None),
            (None, None),
        ],
    )
    def test_parse(self, raw: Any, level: str | None) -> None:
        assert parse_thinking(raw) == level

    def test_rejects_anything_else(self) -> None:
        with pytest.raises(ValueError):
            parse_thinking("extreme")

    def test_settings_defaults(self) -> None:
        s = Settings(_env_file=None)
        assert s.llm_thinking is None
        assert s.llm_fast_thinking == "off"

    def test_settings_read_the_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_LLM_THINKING", "Minimal")
        monkeypatch.setenv("RAG_LLM_FAST_THINKING", "")
        s = Settings(_env_file=None)
        assert s.llm_thinking == "minimal"
        assert s.llm_fast_thinking == "off"

    def test_settings_refuse_a_bad_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_LLM_FAST_THINKING", "turbo")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_knowledge_base_fields(self) -> None:
        cfg = LLMConfigInput(thinking="off", fast_thinking="provider-default")
        assert (cfg.thinking, cfg.fast_thinking) == ("off", "provider-default")
        with pytest.raises(ValidationError):
            LLMConfigInput(thinking="none")  # the old name lives in reasoning_effort only

    def test_old_engine_effort_still_drives_the_answer(self) -> None:
        provider = _provider("openai/gpt-6-luna", llm_reasoning_effort="none")
        assert provider.thinking_effective() == "off"

    def test_unset_sends_nothing(self) -> None:
        provider = _provider("openai/gpt-6-luna")
        kwargs = provider._build_kwargs(MSGS, model=None, temperature=None, max_tokens=None)
        assert "extra_body" not in kwargs and "reasoning_effort" not in kwargs
        assert provider.thinking_effective() == "provider-default"


# ------------------------------------------------------------ OpenRouter payloads


OPENROUTER_OFF = [
    # The answer-model candidates (docs/internal/research/2026-10-03-answer-model-candidates.md)
    ("google/gemini-2.5-flash-lite", _or({"effort": "none"}), "off"),
    ("google/gemini-2.5-flash", _or({"effort": "none"}), "off"),
    ("google/gemma-4-26b-a4b-it", _or({"enabled": False}), "off"),
    ("google/gemma-4-31b-it", _or({"enabled": False}), "off"),
    ("anthropic/claude-haiku-4.5", {}, "off"),  # opt-in: never sent `thinking`
    ("qwen/qwen3.5-35b-a3b", _or({"enabled": False}), "off"),
    ("qwen/qwen3.5-9b", _or({"enabled": False}), "off"),
    ("qwen/qwen3.8-27b", _or({"enabled": False}), "off"),
    ("openai/gpt-6-luna", _or({"effort": "none"}), "off"),
    ("openai/gpt-5.4-nano", _or({"effort": "none"}), "off"),
    ("mistralai/mistral-small-2603", _or({"effort": "none"}), "off"),
    ("deepseek/deepseek-v4.1-flash", _or({"enabled": False}), "off"),
    # Models that never think: nothing is sent.
    ("qwen/qwen3-next-80b-a3b-instruct", {}, "off"),
    ("openai/gpt-4.1-mini", {}, "off"),
    ("meta-llama/llama-4-scout", {}, "off"),
    ("meta-llama/llama-3.3-70b-instruct", {}, "off"),
    ("mistralai/mistral-small-3.2-24b-instruct", {}, "off"),
    ("qwen/qwen3-30b-a3b-instruct-2507", {}, "off"),
    # Cannot be switched off: their minimum.
    ("openai/gpt-oss-120b", _or({"effort": "low"}), "low"),
    ("openai/gpt-oss-20b", _or({"effort": "low"}), "low"),
    ("openai/gpt-5-mini", _or({"effort": "minimal"}), "minimal"),
    ("google/gemini-2.5-pro", _or({"max_tokens": 128}), "minimal"),
    ("google/gemini-3.1-flash-lite", _or({"effort": "minimal"}), "minimal"),
    ("google/gemini-3.5-flash-lite", _or({"effort": "minimal"}), "minimal"),
    ("google/gemini-3.8-flash", _or({"effort": "low"}), "low"),
    ("x-ai/grok-4.7", _or({"effort": "low"}), "low"),
    ("z-ai/glm-5.3-flash", _or({"effort": "low"}), "low"),
    # Other hybrids.
    ("qwen/qwen3-30b-a3b", _or({"enabled": False}), "off"),
    ("z-ai/glm-4.6", _or({"enabled": False}), "off"),
    ("nvidia/nemotron-3.5-lightning", _or({"enabled": False}), "off"),
    ("x-ai/grok-4.3", _or({"effort": "none"}), "off"),
    ("anthropic/claude-sonnet-5", _or({"enabled": False}), "off"),
    # Thinking-only variants: nothing to send, the provider decides.
    ("deepseek/deepseek-r1", {}, "provider-default"),
    ("qwen/qwen3-30b-a3b-thinking-2507", {}, "provider-default"),
    ("moonshotai/kimi-k2-thinking", {}, "provider-default"),
]


class TestOpenRouterOff:
    @pytest.mark.parametrize(("model", "payload", "effective"), OPENROUTER_OFF)
    async def test_exact_request(self, model: str, payload: dict[str, Any], effective: str) -> None:
        assert await _sent(model, "off") == payload
        assert _provider(model).thinking_effective(model, "off") == effective

    async def test_openrouter_prefix_without_a_provider(self) -> None:
        fake = AsyncMock(return_value=_reply())
        provider = _provider("openrouter/openai/gpt-6-luna", provider="")
        with patch("litellm.acompletion", fake):
            await provider.complete(MSGS, thinking="off")
        assert fake.await_args.kwargs["extra_body"] == {"reasoning": {"effort": "none"}}

    @pytest.mark.parametrize(
        ("model", "level", "payload"),
        [
            ("openai/gpt-6-luna", "low", _or({"effort": "low"})),
            ("openai/gpt-6-luna", "minimal", _or({"effort": "low"})),  # no minimal: next up
            ("openai/gpt-5-mini", "minimal", _or({"effort": "minimal"})),
            ("google/gemini-2.5-flash", "medium", _or({"effort": "medium"})),
            ("google/gemini-2.5-pro", "high", _or({"effort": "high"})),
            ("anthropic/claude-haiku-4.5", "minimal", _or({"effort": "low"})),
            ("anthropic/claude-haiku-4.5", "high", _or({"effort": "high"})),
            ("deepseek/deepseek-v4.1-flash", "medium", _or({"effort": "high"})),
            ("qwen/qwen3.5-35b-a3b", "high", _or({"enabled": True})),
            ("mistralai/mistral-small-2603", "low", _or({"effort": "high"})),
            ("openai/gpt-4.1-mini", "high", {}),
            ("openai/gpt-6-luna", "provider-default", {}),
        ],
    )
    async def test_levels_above_off(self, model: str, level: str, payload: dict[str, Any]) -> None:
        assert await _sent(model, level) == payload


# ------------------------------------------------------------ native payloads


NATIVE = [
    ("openai", "gpt-6-luna", "off", _effort("none")),
    ("openai", "gpt-5.4-nano", "off", _effort("none")),
    ("openai", "gpt-5-mini", "off", _effort("minimal")),
    ("openai", "o4-mini", "off", _effort("low")),
    ("groq", "openai/gpt-oss-120b", "off", _effort("low")),
    ("openai", "gpt-4.1-mini", "off", {}),
    # LiteLLM maps these to Gemini thinkingBudget 0 / thinkingLevel.
    ("gemini", "gemini/gemini-2.5-flash", "off", _effort("none")),
    ("gemini", "gemini/gemini-2.5-flash-lite", "off", _effort("none")),
    ("gemini", "gemini/gemini-2.5-pro", "off", _effort("minimal")),
    ("gemini", "gemini/gemini-3.5-flash-lite", "off", _effort("minimal")),
    ("gemini", "gemini/gemini-3.8-flash", "off", _effort("low")),
    ("anthropic", "claude-haiku-4-5", "off", {}),
    ("anthropic", "claude-haiku-4-5", "low", _effort("low")),
    ("anthropic", "claude-sonnet-5", "off", {"thinking": {"type": "disabled"}}),
    ("mistral", "mistral-small-2603", "off", _effort("none")),
    ("xai", "grok-4.7", "off", _effort("low")),
    (
        "hosted_vllm",
        "Qwen/Qwen3.5-35B-A3B",
        "off",
        {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    ),
    (
        "hosted_vllm",
        "Qwen/Qwen3-8B",
        "high",
        {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}},
    ),
    (
        "hosted_vllm",
        "nvidia/nemotron-3.5-lightning",
        "off",
        {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    ),
    ("deepseek", "deepseek-v4.1-flash", "off", {"extra_body": {"thinking": {"type": "disabled"}}}),
    ("zai", "glm-4.7", "off", {"extra_body": {"thinking": {"type": "disabled"}}}),
    ("ollama", "gemma-4-26b-a4b-it", "off", {}),  # no <|think|>: off by default
    ("deepseek", "deepseek-reasoner", "off", {}),
]


class TestNative:
    @pytest.mark.parametrize(("provider", "model", "level", "payload"), NATIVE)
    async def test_exact_request(
        self, provider: str, model: str, level: str, payload: dict[str, Any]
    ) -> None:
        assert await _sent(model, level, provider) == payload

    async def test_the_host_pin_and_the_switch_share_extra_body(self) -> None:
        provider = _provider(
            "openai/gpt-oss-120b",
            llm_fast_model="qwen/qwen3.5-35b-a3b",
            llm_fast_provider_order="deepinfra",
        )
        fake = AsyncMock(return_value=_reply())
        with patch("litellm.acompletion", fake):
            await provider.complete_json(MSGS, **provider.fast_call_options())
        assert fake.await_args.kwargs["extra_body"] == {
            "provider": {"order": ["deepinfra"], "allow_fallbacks": True},
            "reasoning": {"enabled": False},
        }


# ------------------------------------------------------------ fallback on refusal


def _refusal(text: str = "Reasoning is mandatory for this endpoint and cannot be disabled") -> Any:
    return litellm.BadRequestError(message=text, model="m", llm_provider="openrouter")


class TestFallback:
    async def test_a_refused_switch_steps_down_and_is_remembered(self) -> None:
        # An unknown OpenRouter model: none, then enabled:false, then the floors.
        provider = _provider("vendor/new-thinker")
        fake = AsyncMock(side_effect=[_refusal(), _refusal(), _reply("fine")])
        with patch("litellm.acompletion", fake):
            response = await provider.complete(MSGS, thinking="off")
        assert response.content == "fine"
        sent = [c.kwargs["extra_body"]["reasoning"] for c in fake.await_args_list]
        assert sent == [{"effort": "none"}, {"enabled": False}, {"effort": "minimal"}]
        # The next call starts where the last one succeeded.
        assert provider.thinking_effective("vendor/new-thinker", "off") == "minimal"
        fake = AsyncMock(return_value=_reply())
        with patch("litellm.acompletion", fake):
            await provider.complete(MSGS, thinking="off")
        assert fake.await_args.kwargs["extra_body"] == {"reasoning": {"effort": "minimal"}}

    async def test_ends_by_sending_nothing(self) -> None:
        provider = _provider("openai/gpt-oss-120b")
        fake = AsyncMock(side_effect=[_refusal("reasoning effort not supported"), _reply()])
        with patch("litellm.acompletion", fake):
            await provider.complete(MSGS, thinking="off")
        assert "extra_body" not in fake.await_args_list[1].kwargs
        assert provider.thinking_effective("openai/gpt-oss-120b", "off") == "provider-default"

    async def test_gemini_budget_zero_is_the_second_try(self) -> None:
        provider = _provider("google/gemini-2.5-flash")
        fake = AsyncMock(side_effect=[_refusal("reasoning.effort none not allowed"), _reply()])
        with patch("litellm.acompletion", fake):
            await provider.complete(MSGS, thinking="off")
        assert fake.await_args.kwargs["extra_body"] == {"reasoning": {"max_tokens": 0}}

    @pytest.mark.parametrize(
        "error",
        [
            litellm.RateLimitError(message="slow down", model="m", llm_provider="p"),
            litellm.BadRequestError(message="context too long", model="m", llm_provider="p"),
        ],
    )
    async def test_an_unrelated_error_is_not_a_refusal(self, error: Exception) -> None:
        provider = _provider("openai/gpt-6-luna")
        fake = AsyncMock(side_effect=error)
        with patch("litellm.acompletion", fake), pytest.raises(LLMError):
            await provider.complete(MSGS, thinking="off")
        assert fake.await_count == 1
        assert not module._REASONING_REJECTED

    async def test_a_plain_model_error_is_raised_at_once(self) -> None:
        provider = _provider("openai/gpt-4.1-mini")
        fake = AsyncMock(side_effect=_refusal("thinking is not a thing here"))
        with patch("litellm.acompletion", fake), pytest.raises(LLMError):
            await provider.complete(MSGS, thinking="off")
        assert fake.await_count == 1

    async def test_streams_fall_back_too(self) -> None:
        provider = _provider("openai/gpt-oss-120b", llm_thinking="off")

        async def stream() -> Any:
            chunk = MagicMock()
            chunk.usage = None
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = "ok"
            yield chunk

        fake = AsyncMock(side_effect=[_refusal(), stream()])
        with patch("litellm.acompletion", fake):
            chunks = [c async for c in provider.complete_stream(MSGS)]
        assert chunks == ["ok"]
        assert fake.await_args_list[0].kwargs["extra_body"] == {"reasoning": {"effort": "low"}}
        assert "extra_body" not in fake.await_args_list[1].kwargs


# ------------------------------------------------------------ the answer and its cost


def _usage(reasoning: int, cost: float = 0.002) -> Any:
    """A usage block as LiteLLM returns it (attributes, not a dict)."""
    return SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=50 + reasoning,
        total_tokens=150 + reasoning,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
        cost=cost,
    )


class TestAnswer:
    async def test_inline_reasoning_never_reaches_the_answer(self) -> None:
        provider = _provider("qwen/qwen3.5-9b", llm_thinking="off")
        fake = AsyncMock(return_value=_reply("<think>\n\n</think>\n\nNamaste! Open at 9."))
        with patch("litellm.acompletion", fake):
            response = await provider.complete(MSGS)
        assert response.content == "Namaste! Open at 9."

    async def test_gemma_empty_thought_channel_is_stripped_from_a_stream(self) -> None:
        provider = _provider("google/gemma-4-31b-it", llm_thinking="off")

        async def stream() -> Any:
            for piece in ["<|chan", "nel>thought\n", "<channel|>", "Hello", " there"]:
                chunk = MagicMock()
                chunk.usage = None
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = piece
                chunk.choices[0].delta.reasoning_content = None
                chunk.choices[0].delta.reasoning = None
                yield chunk

        with patch("litellm.acompletion", AsyncMock(return_value=stream())):
            chunks = [c async for c in provider.complete_stream(MSGS)]
        assert "".join(chunks) == "Hello there"

    async def test_reasoning_tokens_are_counted_and_the_level_recorded(self) -> None:
        provider = _provider("openai/gpt-oss-120b", llm_thinking="off")
        with patch("litellm.acompletion", AsyncMock(return_value=_reply("a", _usage(40)))):
            response = await provider.complete(MSGS)
        assert response.usage.reasoning_tokens == 40
        spent = request_cost_of(provider)
        assert spent is not None
        assert spent.reasoning_tokens == 40
        assert spent.completion_tokens == 90  # reasoning is inside completion: it is paid for
        assert spent.as_dict()["thinking_effective"] == "low"
        assert provider.cost_ledger.calls[0].thinking == "low"

    async def test_helper_calls_do_not_set_the_answer_level(self) -> None:
        provider = _provider(
            "openai/gpt-6-luna", llm_thinking="high", llm_fast_model="openai/gpt-4.1-mini"
        )
        with patch("litellm.acompletion", AsyncMock(return_value=_reply("{}", _usage(0)))):
            await provider.complete_json(MSGS, **provider.fast_call_options())
        spent = request_cost_of(provider)
        assert spent is not None and "thinking_effective" not in spent.as_dict()
        with patch("litellm.acompletion", AsyncMock(return_value=_reply("a", _usage(10)))):
            await provider.complete(MSGS)
        spent = request_cost_of(provider)
        assert spent is not None and spent.thinking_effective == "high"

    async def test_streamed_answer_records_its_level(self) -> None:
        provider = _provider("openai/gpt-6-luna", llm_thinking="off")

        async def stream() -> Any:
            chunk = MagicMock()
            chunk.usage = None
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = "hi"
            yield chunk
            last = MagicMock()
            last.choices = []
            last.usage = _usage(0)
            yield last

        with collect_stream_stats(), patch("litellm.acompletion", AsyncMock(return_value=stream())):
            _ = [c async for c in provider.complete_stream(MSGS)]
        spent = request_cost_of(provider)
        assert spent is not None and spent.thinking_effective == "off"


# ------------------------------------------------------------ helpers


class TestFilterAndCheck:
    @pytest.mark.parametrize(
        ("text", "visible"),
        [
            ("plain answer", "plain answer"),
            ("<think>a\nb</think>\n\nAnswer", "Answer"),
            ("<THINK></THINK>Answer", "Answer"),
            ("<|channel>thought\n<channel|>Answer", "Answer"),
            ("<think>never closed", ""),
            ("a < b and <b>bold</b>", "a < b and <b>bold</b>"),
        ],
    )
    def test_strip(self, text: str, visible: str) -> None:
        assert strip_reasoning_text(text) == visible

    def test_a_stream_split_anywhere_gives_the_same_text(self) -> None:
        text = "<think>x</think> Hello <|channel>thought\n<channel|>world <"
        for size in range(1, 8):
            f = ReasoningTextFilter()
            out = "".join(f.feed(text[i : i + size]) for i in range(0, len(text), size))
            assert out + f.flush() == "Hello world <"

    def test_check_passes_only_a_clean_zero(self) -> None:
        assert check_thinking_off(content="Hi", reasoning_tokens=0).ok
        assert not check_thinking_off(content="Hi", reasoning_tokens=12).ok
        assert not check_thinking_off(content="Hi", reasoning_tokens=None).ok
        leak = check_thinking_off(content="<think></think>Hi", reasoning_tokens=0)
        assert not leak.ok and "<think>" in leak.leaked
        field = check_thinking_off(content="Hi", reasoning_tokens=0, reasoning_text="hmm")
        assert field.leaked == ("reasoning field",)
        assert check_thinking_off(
            content="Hi", reasoning_tokens=0, thinking_effective="off"
        ).as_dict() == {
            "thinking_ok": True,
            "reasoning_tokens": 0,
            "leaked": [],
            "thinking_effective": "off",
        }

    def test_reasoning_of_reads_every_field(self) -> None:
        assert reasoning_of({"reasoning_content": "a", "reasoning": "b"}) == "a\nb"
        msg = SimpleNamespace(
            reasoning_content=None, reasoning=None, thinking_blocks=[{"thinking": "c"}]
        )
        assert reasoning_of(msg) == "c"
        assert reasoning_of({"content": "x"}) == ""

    def test_describe_marks_minimum_only_families(self) -> None:
        assert describe("google/gemini-3.5-flash-lite", openrouter=True, level="off")[
            "minimum_only"
        ]
        assert not describe("google/gemini-2.5-flash", openrouter=True, level="off")["minimum_only"]
        assert not describe("openai/gpt-4.1-mini", openrouter=True, level="off")["minimum_only"]

    @pytest.mark.parametrize(
        ("model", "family"),
        [
            ("openrouter/openai/gpt-6-luna", "openai-none"),
            ("openai/gpt-5.4-nano:batch", "openai-none"),
            ("~google/gemini-flash-latest", "gemini-3-low"),
            ("google/gemma-3-27b-it", "plain"),
            ("openai/gpt-6-astra", "openai-mandatory"),
            ("openai/gpt-5.2-chat", "plain"),
            ("vendor/unknown", None),
        ],
    )
    def test_families(self, model: str, family: str | None) -> None:
        found = family_of(model)
        assert (found.name if found else None) == family

    def test_every_list_ends_by_sending_nothing(self) -> None:
        for model, _, _ in OPENROUTER_OFF:
            for level in ("off", "minimal", "low", "medium", "high"):
                attempts = thinking_attempts(model, openrouter=True, level=level)
                assert not attempts[-1].sends


# ------------------------------------------------------------ the catalogue snapshot


class TestCatalogue:
    """Through OpenRouter, a model in the catalogue snapshot (llm/openrouter_reasoning.json)
    gets the efforts the catalogue lists; the table keeps a vendor's stricter floor."""

    @pytest.mark.parametrize(
        ("model", "payload", "effective"),
        [
            ("openai/o4-mini-high", _or({"effort": "high"}), "high"),  # high is all it takes
            ("qwen/qwen3.8-2.4t-a95b", _or({"effort": "low"}), "low"),
            ("stepfun/step-3.7-flash", _or({"effort": "low"}), "low"),  # mandatory, not in table
            ("upstage/solar-pro4", _or({"effort": "none"}), "off"),
            ("x-ai/grok-4.20", _or({"enabled": False}), "off"),
            ("openai/gpt-6-luna-pro", _or({"effort": "none"}), "off"),
            ("google/gemini-2.5-flash-image", {}, "off"),  # listed without reasoning
            ("openrouter/auto", {}, "off"),
        ],
    )
    async def test_exact_request(self, model: str, payload: dict[str, Any], effective: str) -> None:
        assert await _sent(model, "off") == payload
        assert _provider(model).thinking_effective(model, "off") == effective

    def test_the_snapshot_lists_every_benchmark_candidate(self) -> None:
        from benchmarks.thinking_check import DEFAULT_MODELS

        from callobuzz_rag.llm.thinking import catalogue_meta

        assert all(catalogue_meta(m)[0] for m in DEFAULT_MODELS)

    def test_without_the_snapshot_the_table_decides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from callobuzz_rag.llm import thinking

        monkeypatch.setattr(thinking, "_snapshot", lambda: {})
        attempts = thinking_attempts("openai/o4-mini-high", openrouter=True, level="off")
        assert attempts[0].body == {"reasoning": {"effort": "low"}}
