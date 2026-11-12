"""Answer-model routing by reply language (llm/language_routing.py, RAG_LLM_LANGUAGE_MODELS,
llm_config.language_models): the precedence, the provider check, and what a turn does with it
(the director answers Hindi and Hinglish on one model and English on another, the speculative
answer and the metering follow, the request carries each model's own thinking parameters)."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from callobuzz_rag.api.schemas.corpus import LLMConfigInput
from callobuzz_rag.conversation.envelope import DoneEvent, TraceEvent
from callobuzz_rag.conversation.types import LANG_KEYS
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ConfigResolver
from callobuzz_rag.llm import litellm_provider as provider_module
from callobuzz_rag.llm.cost import CallCost
from callobuzz_rag.llm.language_routing import LANGUAGE_CODES, clean_language_models
from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
from callobuzz_rag.llm.models import LLMMessage
from tests.unit.conversation_helpers import (
    FakeLLM,
    fake_orch,
    make_director,
    make_turn,
    run_events,
)

GEMMA = "google/gemma-4-31b-it"
GPT_OSS = "openai/gpt-oss-120b"
ENGINE_MAP = {"hi": GEMMA, "hi-Latn": GEMMA}
ANSWER = "Home collection costs Rs 150 in Pune [1]."


def _resolver(**settings: Any) -> ConfigResolver:
    values: dict[str, Any] = {
        "llm_provider": "openrouter",
        "llm_model": GPT_OSS,
        "llm_api_key": "engine-key",
        **settings,
    }
    return ConfigResolver(Settings(_env_file=None, **values))


# ------------------------------------------------------------------ the map itself


def test_codes_are_the_detectors_codes() -> None:
    assert tuple(LANG_KEYS) == LANGUAGE_CODES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, {}),
        ("", {}),
        ("  ", {}),
        ('{"hi": " google/gemma-4-31b-it "}', {"hi": GEMMA}),
        ({"HI-LATN": GEMMA, "en": GPT_OSS}, {"hi-Latn": GEMMA, "en": GPT_OSS}),
    ],
)
def test_clean(raw: Any, expected: dict[str, str]) -> None:
    assert clean_language_models(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["not json", "[1]", '{"klingon": "m"}', '{"hi": ""}', '{"hi": 3}', {"hi": "x" * 201}],
)
def test_clean_refuses(raw: Any) -> None:
    with pytest.raises(ValueError):
        clean_language_models(raw)


def test_the_env_var_is_read_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_LLM_LANGUAGE_MODELS", '{"hi": "' + GEMMA + '"}')
    assert Settings(_env_file=None).llm_language_models == {"hi": GEMMA}
    monkeypatch.setenv("RAG_LLM_LANGUAGE_MODELS", "")
    assert Settings(_env_file=None).llm_language_models == {}


def test_default_is_no_routing() -> None:
    assert Settings(_env_file=None).llm_language_models == {}
    assert _resolver().resolve_llm().language_models == {}


def test_the_knowledge_base_field_is_validated() -> None:
    assert LLMConfigInput(language_models={"hi-latn": GEMMA}).language_models == {"hi-Latn": GEMMA}
    assert LLMConfigInput(language_models={}).language_models == {}
    assert LLMConfigInput(language_models=None).language_models is None
    with pytest.raises(ValidationError):
        LLMConfigInput(language_models={"klingon": GEMMA})


# ------------------------------------------------------------------ precedence


def test_engine_map_applies_to_an_engine_default_request() -> None:
    resolved = _resolver(llm_language_models=ENGINE_MAP).resolve_llm()
    assert resolved.language_models == ENGINE_MAP
    assert resolved.model == GPT_OSS  # English keeps the default model


def test_knowledge_base_entry_beats_the_engine_entry() -> None:
    other = "meta-llama/llama-4-scout"
    resolved = _resolver(llm_language_models=ENGINE_MAP).resolve_llm(
        corpus_llm_config={"language_models": {"hi": other}}
    )
    assert resolved.language_models == {"hi": other, "hi-Latn": GEMMA}


def test_knowledge_base_can_route_without_an_engine_map() -> None:
    resolved = _resolver().resolve_llm(corpus_llm_config={"language_models": {"en": GPT_OSS}})
    assert resolved.language_models == {"en": GPT_OSS}


def test_an_empty_knowledge_base_map_turns_routing_off() -> None:
    resolved = _resolver(llm_language_models=ENGINE_MAP).resolve_llm(
        corpus_llm_config={"language_models": {}}
    )
    assert resolved.language_models == {}


def test_a_knowledge_base_model_beats_the_engine_map() -> None:
    resolved = _resolver(llm_language_models=ENGINE_MAP).resolve_llm(
        corpus_llm_config={"model": "openai/gpt-4.1-mini"}
    )
    assert resolved.model == "openai/gpt-4.1-mini"
    assert resolved.language_models == {}


def test_a_knowledge_base_entry_beats_its_own_model() -> None:
    resolved = _resolver().resolve_llm(
        corpus_llm_config={"model": "openai/gpt-4.1-mini", "language_models": {"hi": GEMMA}}
    )
    assert resolved.language_models == {"hi": GEMMA}


def test_a_request_model_header_turns_routing_off() -> None:
    resolved = _resolver(llm_language_models=ENGINE_MAP).resolve_llm(
        request_headers={"x-llm-model": "openai/gpt-4.1"},
        corpus_llm_config={"language_models": {"hi": GEMMA}},
    )
    assert resolved.model == "openai/gpt-4.1"
    assert resolved.language_models == {}


def test_a_request_key_alone_keeps_routing() -> None:
    resolved = _resolver(llm_language_models=ENGINE_MAP).resolve_llm(
        request_headers={"x-llm-api-key": "caller-key"}
    )
    assert resolved.payer == "byok" and resolved.language_models == ENGINE_MAP


def test_an_unreadable_stored_map_falls_through() -> None:
    resolved = _resolver(llm_language_models=ENGINE_MAP).resolve_llm(
        corpus_llm_config={"language_models": {"klingon": "m"}}
    )
    assert resolved.language_models == ENGINE_MAP


@pytest.mark.parametrize(
    "corpus",
    [
        {"provider": "anthropic", "api_key_encrypted": None},
        {"base_url": "http://localhost:11434"},
    ],
)
def test_an_engine_model_is_not_sent_to_another_provider(
    corpus: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    resolver = _resolver(llm_language_models=ENGINE_MAP)
    with caplog.at_level("INFO"):
        resolved = resolver.resolve_llm(corpus_llm_config=corpus)
    assert resolved.language_models == {}
    assert "another provider or endpoint" in caplog.text


def test_a_byok_header_provider_ignores_the_engine_map(caplog: pytest.LogCaptureFixture) -> None:
    resolved = _resolver(llm_language_models=ENGINE_MAP).resolve_llm(
        request_headers={"x-llm-provider": "anthropic", "x-llm-api-key": "sk-ant"}
    )
    assert resolved.language_models == {}


def test_a_knowledge_base_entry_is_kept_on_its_own_provider() -> None:
    resolved = _resolver(llm_language_models=ENGINE_MAP).resolve_llm(
        corpus_llm_config={"provider": "anthropic", "language_models": {"hi": "claude-haiku-4-5"}}
    )
    assert resolved.language_models == {"hi": "claude-haiku-4-5"}


# ------------------------------------------------------------------ the request each model gets


class _Capture:
    def __init__(self) -> None:
        self.kwargs: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.kwargs.append(kwargs)

        async def stream() -> AsyncIterator[Any]:
            delta = SimpleNamespace(content="ok", reasoning_content=None)
            yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)

        return stream()


async def _stream_request(model: str) -> dict[str, Any]:
    provider = LiteLLMProvider(
        Settings(_env_file=None, llm_provider="openrouter", llm_model=GPT_OSS, llm_api_key="k")
    ).for_model(model, "off")
    cap = _Capture()
    with patch("litellm.acompletion", cap):
        async for _ in provider.complete_stream([LLMMessage(role="user", content="hi")]):
            pass
    return cap.kwargs[0]


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    provider_module._REASONING_REJECTED.clear()
    yield
    provider_module._REASONING_REJECTED.clear()


async def test_each_routed_model_gets_its_own_thinking_parameters() -> None:
    gemma = await _stream_request(GEMMA)
    oss = await _stream_request(GPT_OSS)
    assert gemma["model"] == f"openrouter/{GEMMA}" or gemma["model"].endswith(GEMMA)
    assert gemma["extra_body"]["reasoning"] == {"enabled": False}  # off
    assert oss["model"].endswith(GPT_OSS)
    assert oss["extra_body"]["reasoning"] == {"effort": "low"}  # its minimum


def test_for_model_shares_the_ledger_and_leaves_the_original_alone() -> None:
    base = LiteLLMProvider(
        Settings(_env_file=None, llm_provider="openrouter", llm_model=GPT_OSS, llm_api_key="k")
    )
    clone = base.for_model(GEMMA, "off")
    assert clone.cost_ledger is base.cost_ledger
    assert clone.fast_model == base.fast_model
    assert clone.has_credentials
    assert base.thinking_effective() != "off" or base.thinking_effective(GEMMA) == "off"
    assert clone.thinking_effective() == "off"
    assert base.is_known_non_reasoning(GEMMA) == clone.is_known_non_reasoning()


def test_route_answer_llm_picks_the_model_by_the_detected_language() -> None:
    from callobuzz_rag.llm.language_routing import route_answer_llm

    base = LiteLLMProvider(
        Settings(_env_file=None, llm_provider="openrouter", llm_model=GPT_OSS, llm_api_key="k")
    )
    hindi, model = route_answer_llm(base, ENGINE_MAP, "मुझे रिटर्न पॉलिसी बताओ")
    assert model == GEMMA and hindi is not base and hindi.cost_ledger is base.cost_ledger
    hinglish, model = route_answer_llm(base, ENGINE_MAP, "mujhe return policy batao")
    assert model == GEMMA and hinglish is not base
    # English, no map, and a provider that cannot switch model all stay put.
    assert route_answer_llm(base, ENGINE_MAP, "What is the return policy?") == (base, None)
    assert route_answer_llm(base, {}, "मुझे रिटर्न पॉलिसी बताओ") == (base, None)
    plain = object()
    assert route_answer_llm(plain, ENGINE_MAP, "मुझे रिटर्न पॉलिसी बताओ") == (plain, None)


# ------------------------------------------------------------------ the director


class RoutedFakeLLM(FakeLLM):
    """A FakeLLM that can answer on another model: `for_model` hands back a sibling that
    shares the ledger and the scripted replies and records which model and thinking it was
    asked for, like `LiteLLMProvider.for_model`."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.model = "answer-model"
        self.thinking: str | None = None
        self.parent: RoutedFakeLLM = self
        self.streams: list[tuple[str, str | None]] = []

    def for_model(self, model: str, thinking: str | None = None) -> "RoutedFakeLLM":
        sibling = type(self)()
        sibling.model, sibling.thinking, sibling.parent = model, thinking, self.parent
        sibling.cost_ledger = self.cost_ledger
        sibling.stream = self.stream
        sibling.calls = self.calls
        sibling.streams = self.streams
        return sibling

    async def complete_stream(self, messages: Any, **kw: Any) -> AsyncIterator[str]:
        self.streams.append((self.model, self.thinking))
        async for piece in super().complete_stream(messages, **kw):
            yield piece


BUSINESS = {
    "label": "business_question",
    "confidence": 0.9,
    "language": "en",
    "topic": None,
    "frustrated": False,
    "needs_human": False,
}
HINDI = "होम कलेक्शन का खर्च कितना है?"
HINGLISH = "ghar pe sample lene ka kharcha kitna hai"
ENGLISH = "How much is home collection?"


@pytest.fixture(autouse=True)
def _io(director_io: Any) -> Any:
    return director_io


def _director(llm: RoutedFakeLLM, language_models: dict[str, str]) -> Any:
    director = make_director(llm, fake_orch())
    director._deps.language_models = language_models
    return director


def _turn(text: str, debug: bool = False) -> Any:
    return make_turn(
        text,
        debug=debug,
        language={"supported": ["en", "hi", "hi-Latn"]},
    )


@pytest.mark.parametrize(
    ("text", "model", "thinking"),
    [
        (HINDI, GEMMA, "off"),
        (HINGLISH, GEMMA, "off"),
        (ENGLISH, "answer-model", None),
    ],
)
async def test_the_turn_answers_on_the_model_for_its_language(
    text: str, model: str, thinking: str | None, _io: Any
) -> None:
    llm = RoutedFakeLLM(json_replies=[BUSINESS], stream=[ANSWER])
    done = await _director(llm, ENGINE_MAP).run(_turn(text))
    assert done.route.value == "grounded"
    assert llm.streams == [(model, thinking)]
    assert done.model == model
    kw = _io.meter.await_args.kwargs
    assert kw["model"] == model
    assert kw["cost"].calls >= 2  # label, query translation and answer: one ledger


async def test_english_routes_when_the_map_names_it() -> None:
    llm = RoutedFakeLLM(json_replies=[BUSINESS], stream=[ANSWER])
    done = await _director(llm, {"en": GPT_OSS}).run(_turn(ENGLISH))
    assert llm.streams == [(GPT_OSS, "off")]  # "off" is gpt-oss's minimum, sent as low
    assert done.model == GPT_OSS


async def test_no_map_answers_everything_on_the_default_model() -> None:
    llm = RoutedFakeLLM(json_replies=[BUSINESS], stream=[ANSWER])
    done = await _director(llm, {}).run(_turn(HINDI))
    assert llm.streams == [("answer-model", None)] and done.model == "answer-model"


async def test_the_label_call_keeps_the_fast_model() -> None:
    llm = RoutedFakeLLM(json_replies=[BUSINESS], stream=[ANSWER])
    await _director(llm, ENGINE_MAP).run(_turn(HINDI))
    label_calls = [kw for kind, _, kw in llm.calls if kind == "json"]
    assert label_calls and all(kw.get("model") == "fake-fast" for kw in label_calls)


async def test_the_trace_names_the_routed_model() -> None:
    llm = RoutedFakeLLM(json_replies=[BUSINESS], stream=[ANSWER])
    events = await run_events(_director(llm, ENGINE_MAP), _turn(HINGLISH, debug=True))
    trace = next(e for e in events if isinstance(e, TraceEvent)).trace
    assert trace["model"]["name"] == GEMMA
    assert trace["model"]["language_routing"] == (
        f"model chosen for hi-Latn by language routing: {GEMMA}"
    )
    assert isinstance(events[-1], DoneEvent)


async def test_the_trace_has_no_routing_note_for_an_unrouted_turn() -> None:
    llm = RoutedFakeLLM(json_replies=[BUSINESS], stream=[ANSWER])
    events = await run_events(_director(llm, ENGINE_MAP), _turn(ENGLISH, debug=True))
    trace = next(e for e in events if isinstance(e, TraceEvent)).trace
    assert trace["model"] == {"name": "answer-model", "language_routing": None}


async def test_a_provider_that_cannot_switch_models_keeps_the_default() -> None:
    llm = FakeLLM(json_replies=[BUSINESS], stream=[ANSWER])  # no for_model
    director = make_director(llm, fake_orch())
    director._deps.language_models = ENGINE_MAP
    done = await director.run(_turn(HINDI))
    assert done.model == "answer-model"


async def test_the_speculative_answer_uses_the_routed_model() -> None:
    import asyncio

    class SlowLabel(RoutedFakeLLM):
        async def complete_json(self, messages: Any, **kw: Any) -> Any:
            await asyncio.sleep(0.15)
            return await super().complete_json(messages, **kw)

    llm = SlowLabel(json_replies=[BUSINESS], stream=[ANSWER])
    director = _director(llm, ENGINE_MAP)
    assert director._deps.settings.assistant_speculative_answer
    done = await director.run(_turn(HINDI))
    # One answer call only: the speculation was adopted, and it was on Gemma.
    assert llm.streams == [(GEMMA, "off")]
    assert done.model == GEMMA


async def test_a_failed_turn_is_metered_under_the_routed_model(_io: Any) -> None:
    class Broken(RoutedFakeLLM):
        async def complete_stream(self, messages: Any, **kw: Any) -> AsyncIterator[str]:
            self.streams.append((self.model, self.thinking))
            self.cost_ledger.add(CallCost(self.model, 5, 0, 0, 0.001))
            raise RuntimeError("provider down")
            yield ""  # pragma: no cover

    llm = Broken(json_replies=[BUSINESS], stream=[ANSWER])
    events = await run_events(_director(llm, ENGINE_MAP), _turn(HINGLISH))
    assert events[-1].__class__.__name__ == "ErrorEvent"
    assert _io.meter.await_args.kwargs["model"] == GEMMA
