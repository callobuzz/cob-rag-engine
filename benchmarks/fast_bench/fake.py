"""A fake label model for the free dry run and the tests: it replaces only the network.

`FakeLabelModel.acompletion` stands in for `litellm.acompletion` (non-streamed, as the label call
is), so the engine's provider, the labeler, the capture, the records, scoring and the merge all
run for real. It answers each turn from the fixtures (the expected label; a few turns wrong on
purpose) in the JSON the label prompt asks for, with a usage block carrying a cost like
OpenRouter's.

Model ids (anything after "fake/"):
- good           the expected label, correct language
- thinks-anyway  reasoning tokens and a reasoning field even when thinking is off
- error500       every call fails with HTTP 500
- flaky          the first call of every sixth turn fails with HTTP 429, the retry works
- garbage        a reply that is not JSON (the labeler falls back)
- no-cost        usage without a cost
- no-usage       no usage block at all
- key-limit      HTTP 403 "Key limit exceeded" (aborts the run)
- slow           like good, 60 ms before the reply (the others 20 ms: Windows timers are coarse)
"""

import asyncio
import hashlib
import json
import re
from collections import Counter
from typing import Any

from benchmarks.answer_bench.hosts import GenerationMeta

PRICE_IN = 0.15 / 1_000_000
PRICE_OUT = 0.60 / 1_000_000
REASONING_TOKENS = 24
_TEXT = re.compile(r"<<<(.*)>>>\s*\n\s*Classify the visitor", re.S)


def fake_cost(prompt: int, completion: int) -> float:
    return round(prompt * PRICE_IN + completion * PRICE_OUT, 10)


class FakeLabelModel:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal

        self._by_text = {}
        for r in rows:
            self._by_text.setdefault(sanitize_for_prompt_literal(r["text"])[:2000], r)
        self.calls: Counter[str] = Counter()

    def _row(self, prompt: str) -> dict[str, Any] | None:
        match = _TEXT.search(prompt)
        return self._by_text.get(match.group(1)) if match else None

    async def acompletion(self, **kwargs: Any) -> Any:
        import litellm
        from litellm.types.utils import Choices, Message, ModelResponse, Usage

        model = str(kwargs["model"])
        kind = model.split("/", 1)[1] if "/" in model else model
        prompt_text = str(kwargs["messages"][-1].get("content", ""))
        digest = hashlib.sha256(prompt_text.encode()).hexdigest()[:12]
        self.calls[f"{model}:{digest}"] += 1
        await asyncio.sleep(0.06 if kind == "slow" else 0.02)
        if kind == "error500":
            raise litellm.InternalServerError("fake upstream error", "openrouter", model)
        if kind == "flaky" and int(digest, 16) % 6 == 0 and self.calls[f"{model}:{digest}"] == 1:
            raise litellm.RateLimitError("fake rate limit", "openrouter", model)
        if kind == "key-limit":
            raise litellm.PermissionDeniedError(
                "Key limit exceeded (daily limit)", "openrouter", model, response=_response(403)
            )
        row = self._row(prompt_text)
        label = row["label"] if row else "business_question"
        language = row["language"] if row else "en"
        content = (
            "I think this one is a business question"
            if kind == "garbage"
            else json.dumps(
                {
                    "label": label,
                    "confidence": 0.9,
                    "language": language,
                    "topic": None,
                    "frustrated": False,
                    "needs_human": False,
                }
            )
        )
        thinks = kind == "thinks-anyway"
        prompt = len(prompt_text) // 4
        completion = max(1, len(content) // 4) + (REASONING_TOKENS if thinks else 0)
        message = Message(content=content, role="assistant")
        if thinks:
            message.reasoning_content = "Let me think about the visitor's message."
        response = ModelResponse(
            id=f"gen-fake-{digest}-{self.calls[f'{model}:{digest}']}",
            model=model,
            choices=[Choices(message=message, finish_reason="stop", index=0)],
        )
        if kind != "no-usage":
            usage_kwargs: dict[str, Any] = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "completion_tokens_details": {
                    "reasoning_tokens": REASONING_TOKENS if thinks else 0
                },
                "prompt_tokens_details": {"cached_tokens": prompt // 2},
            }
            if kind != "no-cost":
                usage_kwargs["cost"] = fake_cost(prompt, completion)
            response.usage = Usage(**usage_kwargs)
        return response


def _response(status: int) -> Any:
    import httpx

    return httpx.Response(status, request=httpx.Request("POST", "https://openrouter.ai"))


class FakeLookup:
    source = "fake"

    async def lookup(self, generation_id: str) -> GenerationMeta:
        return GenerationMeta("FakeHost", None, None)
