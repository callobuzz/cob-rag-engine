"""Real request cost: provider-reported first, price lists next, unknown never zero."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from callobuzz_rag.llm.cost import (
    CallCost,
    CostLedger,
    call_cost_from_response,
    call_cost_from_stream,
    priced_cost,
    provider_reported_cost,
    usage_counts,
)


def _raw(usage, hidden=None):
    return SimpleNamespace(usage=usage, _hidden_params=hidden or {})


class TestProviderReportedCost:
    def test_openrouter_usage_cost(self) -> None:
        assert provider_reported_cost({"cost": 0.00042}) == pytest.approx(0.00042)

    def test_attribute_style_usage(self) -> None:
        assert provider_reported_cost(SimpleNamespace(cost=0.5)) == 0.5

    def test_zero_is_a_real_price(self) -> None:
        assert provider_reported_cost({"cost": 0}) == 0.0

    def test_missing_or_garbage_is_none(self) -> None:
        assert provider_reported_cost(None) is None
        assert provider_reported_cost({"prompt_tokens": 3}) is None
        assert provider_reported_cost({"cost": "1.0"}) is None
        assert provider_reported_cost({"cost": -1}) is None

    def test_byok_adds_the_upstream_price(self) -> None:
        usage = {
            "cost": 0.0001,
            "is_byok": True,
            "cost_details": {"upstream_inference_cost": 0.002},
        }
        assert provider_reported_cost(usage) == pytest.approx(0.0021)


class TestCallCost:
    def test_usage_counts_with_reasoning(self) -> None:
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=40,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=25),
        )
        assert usage_counts(usage) == (100, 40, 25)

    def test_response_cost_comes_from_the_usage_object(self) -> None:
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, cost=0.0007)
        call = call_cost_from_response(_raw(usage), "openai/gpt-oss-120b", "openrouter")
        assert call.cost_usd == pytest.approx(0.0007)
        assert (call.prompt_tokens, call.completion_tokens) == (10, 5)

    def test_response_cost_falls_back_to_litellm_header_cost(self) -> None:
        hidden = {"additional_headers": {"llm_provider-x-litellm-response-cost": 0.003}}
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        call = call_cost_from_response(_raw(usage, hidden), "m", None)
        assert call.cost_usd == pytest.approx(0.003)

    def test_unpriced_model_is_unknown_not_zero(self) -> None:
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        with patch("litellm.cost_per_token", side_effect=Exception("not mapped")):
            call = call_cost_from_response(_raw(usage), "acme/unheard-of-1", "openrouter")
        assert call.cost_usd is None

    def test_price_list_fallback(self) -> None:
        usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=500)
        with patch("litellm.cost_per_token", side_effect=Exception("not mapped")):
            call = call_cost_from_response(_raw(usage), "openrouter/openai/gpt-4o-mini", None)
        assert call.cost_usd == pytest.approx(0.00015 + 0.0003)

    def test_no_tokens_costs_nothing_known(self) -> None:
        assert priced_cost("whatever", None, 0, 0) == 0.0

    def test_stream_uses_the_final_usage_chunk(self) -> None:
        usage = {"prompt_tokens": 50, "completion_tokens": 20, "cost": 0.0002}
        call = call_cost_from_stream(usage, "m", None, prompt_estimate=1, completion_estimate=1)
        assert (call.prompt_tokens, call.completion_tokens, call.cost_usd) == (50, 20, 0.0002)

    def test_stream_without_usage_estimates_tokens_and_prices_from_lists_only(self) -> None:
        with patch("litellm.cost_per_token", side_effect=Exception("nope")):
            call = call_cost_from_stream(
                None, "acme/unheard-of-1", None, prompt_estimate=30, completion_estimate=10
            )
        assert (call.prompt_tokens, call.completion_tokens) == (30, 10)
        assert call.cost_usd is None


class TestLedger:
    def test_sums_every_call_of_the_request(self) -> None:
        ledger = CostLedger()
        ledger.add(CallCost("fast", 10, 5, 0, 0.0001))
        ledger.add(CallCost("answer", 100, 50, 30, 0.001))
        s = ledger.summary()
        assert (s.prompt_tokens, s.completion_tokens, s.reasoning_tokens) == (110, 55, 30)
        assert s.cost_micro == 1100
        assert s.cost_known is True and s.calls == 2
        assert s.cost_usd == pytest.approx(0.0011)

    def test_one_unpriced_call_makes_the_total_unknown(self) -> None:
        ledger = CostLedger()
        ledger.add(CallCost("fast", 10, 5, 0, None))
        ledger.add(CallCost("answer", 100, 50, 0, 0.001))
        s = ledger.summary()
        assert s.cost_known is False
        assert s.cost_usd is None
        assert s.cost_micro == 1000  # the known part is kept, as a floor

    def test_an_empty_ledger_is_a_known_zero(self) -> None:
        s = CostLedger().summary()
        assert s.cost_known is True and s.cost_micro == 0 and s.calls == 0


class TestProviderLedger:
    """Every call a per-request provider makes lands in its ledger, helpers included."""

    @staticmethod
    def _provider():
        from unittest.mock import MagicMock

        from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

        s = MagicMock()
        s.llm_model = "openai/gpt-oss-120b"
        s.llm_api_key.get_secret_value.return_value = "k"
        s.llm_api_base = None
        s.llm_temperature = 0.1
        s.llm_max_tokens = 100
        s.llm_fast_model = "openai/gpt-oss-20b"
        s.llm_fast_temperature = 0.0
        s.llm_fast_max_tokens = 50
        s.llm_timeout_seconds = 10.0
        s.llm_fast_timeout_seconds = 5.0
        s.llm_provider = "openrouter"
        return LiteLLMProvider(s)

    @staticmethod
    def _completion(cost, content="hi"):
        usage = SimpleNamespace(
            prompt_tokens=20,
            completion_tokens=10,
            total_tokens=30,
            cost=cost,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=4),
        )
        choice = SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=usage, model="m", _hidden_params={})

    async def test_complete_and_complete_json_are_both_recorded(self) -> None:
        from unittest.mock import AsyncMock

        from callobuzz_rag.llm.models import LLMMessage

        p = self._provider()
        msgs = [LLMMessage(role="user", content="q")]
        with patch(
            "litellm.acompletion",
            new=AsyncMock(side_effect=[self._completion(0.0002), self._completion(0.0003)]),
        ):
            await p.complete_json(msgs, model="openai/gpt-oss-20b")
            await p.complete(msgs)
        s = p.cost_ledger.summary()
        assert s.calls == 2 and s.cost_micro == 500 and s.cost_known
        assert s.reasoning_tokens == 8

    async def test_stream_records_the_usage_chunk_and_survives_its_empty_choices(self) -> None:
        from callobuzz_rag.llm.models import LLMMessage

        p = self._provider()

        async def fake_stream(*_a, **kw):
            assert kw["stream_options"] == {"include_usage": True}
            for text in ["a", "b"]:
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None
                )
            yield SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(prompt_tokens=40, completion_tokens=12, cost=0.0009),
            )

        with patch("litellm.acompletion", side_effect=fake_stream):
            out = [c async for c in p.complete_stream([LLMMessage(role="user", content="q")])]
        assert out == ["a", "b"]
        s = p.cost_ledger.summary()
        assert (s.prompt_tokens, s.completion_tokens, s.cost_micro) == (40, 12, 900)

    async def test_stream_without_usage_still_records_an_estimate(self) -> None:
        from callobuzz_rag.llm.models import LLMMessage

        p = self._provider()

        async def fake_stream(*_a, **_kw):
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="hello"))], usage=None
            )

        with (
            patch("litellm.acompletion", side_effect=fake_stream),
            patch("litellm.cost_per_token", side_effect=Exception("unmapped")),
        ):
            _ = [c async for c in p.complete_stream([LLMMessage(role="user", content="q")])]
        s = p.cost_ledger.summary()
        assert s.calls == 1 and s.completion_tokens > 0
        assert s.cost_known is False  # openai/gpt-oss-120b is not in the engine table


class TestOpenRouterWireFormat:
    """The real LiteLLM client against an OpenRouter-shaped reply: the provider's own cost
    reaches the ledger for a model no price list knows, streamed or not."""

    @staticmethod
    def _run(body: bytes, content_type: str):
        import json

        import httpx
        import litellm
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, content=body, headers={"content-type": content_type})

        client = AsyncHTTPHandler()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        real = litellm.acompletion

        async def with_client(**kwargs):
            return await real(client=client, **kwargs)

        return seen, with_client

    async def test_non_streamed_cost_comes_from_usage_cost(self) -> None:
        import json

        from callobuzz_rag.llm.models import LLMMessage

        reply = {
            "id": "g1",
            "object": "chat.completion",
            "created": 1,
            "model": "acme/unheard-of-1",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hi"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 6,
                "total_tokens": 18,
                "cost": 0.00042,
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        }
        seen, with_client = self._run(json.dumps(reply).encode(), "application/json")
        p = TestProviderLedger._provider()
        p._model = "acme/unheard-of-1"
        with patch("litellm.acompletion", with_client):
            await p.complete([LLMMessage(role="user", content="q")])
        assert seen["body"]["usage"] == {"include": True}  # OpenRouter is asked for its cost
        s = p.cost_ledger.summary()
        assert s.cost_known is True and s.cost_micro == 420
        assert (s.prompt_tokens, s.completion_tokens, s.reasoning_tokens) == (12, 6, 3)

    async def test_streamed_cost_comes_from_the_final_usage_chunk(self) -> None:
        import json

        from callobuzz_rag.llm.models import LLMMessage

        def chunk(**kw):
            return {
                "id": "g1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "acme/unheard-of-1",
                **kw,
            }

        chunks = [
            chunk(
                choices=[
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "Hel"},
                        "finish_reason": None,
                    }
                ]
            ),
            chunk(choices=[{"index": 0, "delta": {"content": "lo"}, "finish_reason": "stop"}]),
            chunk(
                choices=[],
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "cost": 0.0003,
                },
            ),
        ]
        body = ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()
        seen, with_client = self._run(body, "text/event-stream")
        p = TestProviderLedger._provider()
        p._model = "acme/unheard-of-1"
        with patch("litellm.acompletion", with_client):
            out = [c async for c in p.complete_stream([LLMMessage(role="user", content="q")])]
        assert "".join(out) == "Hello"
        assert seen["body"]["stream_options"] == {"include_usage": True}
        s = p.cost_ledger.summary()
        assert s.cost_known is True and s.cost_micro == 300
        assert (s.prompt_tokens, s.completion_tokens) == (10, 5)
