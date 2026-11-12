"""A fake answer model for the free dry run and the tests: it replaces only the network.

`FakeModel.acompletion` stands in for `litellm.acompletion` underneath the capture wrapper, so
the engine's provider, the fact gate, the capture, the records, scoring and the summary all run
for real. It streams realistic replies built from the item's own passages (sentences, Markdown
tables, Hindi and Hinglish, no-answer messages), with reasoning deltas when thinking is on, and
a final usage chunk carrying a cost like OpenRouter's.

Model ids (anything after "fake/"):
- good              correct answers; thinks only when asked to
- thinks-anyway     reasons even when thinking is off (a rail failure)
- empty             streams no content
- error500          every call fails with HTTP 500
- flaky             each item's first call fails with HTTP 429, the retry works
- no-cost           usage without a cost
- no-usage          no usage chunk at all
- rejects-reasoning HTTP 400 naming "reasoning" whenever a thinking parameter is sent
- slow              like good, 50 ms before the first token
"""

import asyncio
import hashlib
import json
import re
from collections import Counter
from typing import Any

from benchmarks.answer_bench.hosts import GenerationMeta
from benchmarks.answer_bench.items import Item
from benchmarks.answer_bench.prompt import fingerprint

PRICE_IN = 0.15 / 1_000_000
PRICE_OUT = 0.60 / 1_000_000
REASONING_TOKENS = 48
_NO_ANSWER = {
    "en": "I don't have that information in our documents.",
    "hi": "यह जानकारी हमारे दस्तावेज़ों में नहीं है।",
    "hi-Latn": "Yeh jaankari hamare documents mein nahi hai.",
}


def fake_cost(prompt: int, completion: int) -> float:
    return round(prompt * PRICE_IN + completion * PRICE_OUT, 10)


def _thinking_on(kwargs: dict[str, Any]) -> bool:
    effort = kwargs.get("reasoning_effort")
    if effort is not None:
        return effort not in ("none", "off")
    reasoning = (kwargs.get("extra_body") or {}).get("reasoning")
    if isinstance(reasoning, dict):
        return not (reasoning.get("enabled") is False or reasoning.get("effort") in ("none", "off"))
    thinking = kwargs.get("thinking")
    if isinstance(thinking, dict):
        return thinking.get("type") != "disabled"
    return False


def _sends_thinking(kwargs: dict[str, Any]) -> bool:
    extra = kwargs.get("extra_body") or {}
    return any(k in kwargs for k in ("reasoning_effort", "thinking")) or "reasoning" in extra


def _sentence_with(text: str, form: str) -> str | None:
    for sentence in re.split(r"(?<=[.!?।])\s+|\n+", text):
        if form.lower() in sentence.lower():
            return sentence.strip()
    return None


def _table_rows(text: str, forms: list[str]) -> list[str]:
    lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 3:
        return []
    body = [ln for ln in lines[2:] if any(f.lower() in ln.lower() for f in forms)]
    return [lines[0], lines[1], *body] if body else []


def answer_for(item: Item) -> str:
    """A plausible good reply to `item`, from its passages."""
    if item.type == "unanswerable":
        return _NO_ANSWER[item.language]
    first = item.gold_passages[0]
    gold = item.passages[first - 1]
    forms = [f[0] for f in item.gold_facts]
    cite = f"[{first}]"
    if item.type == "table":
        rows = _table_rows(gold.text, [f for fact in item.gold_facts for f in fact])
        if not rows:
            rows = ["| Item | Details |", "|---|---|"] + [
                f"| {item.passages[g - 1].section or item.passages[g - 1].file_name} | "
                f"{forms[i] if i < len(forms) else ''} |"
                for i, g in enumerate(item.gold_passages)
            ]
        intro = {
            "en": f"Here is the comparison {cite}:",
            "hi": f"यह रही तुलना {cite}:",
            "hi-Latn": f"Yeh rahi comparison {cite}:",
        }[item.language]
        return intro + "\n\n" + "\n".join(rows)
    if item.language == "en":
        sentence = _sentence_with(gold.text, forms[0])
        if sentence is None or sentence.startswith("|"):
            sentence = f"According to our documents, it is {', '.join(forms)}"
        return f"{sentence.rstrip('.')} {cite}."
    facts = ", ".join(forms)
    if item.language == "hi":
        return f"हमारे दस्तावेज़ों के अनुसार इसका जवाब {facts} है {cite}।"
    return f"Documents ke hisaab se iska jawab {facts} hai {cite}. Aur kuch jaanna ho to bataiye."


class FakeModel:
    """`acompletion(**kwargs)` for fake/* models. Knows the items to answer from."""

    def __init__(self, items: list[Item], *, first_token_s: float = 0.02) -> None:
        self._by_question = {i.question: i for i in items}
        self._first_token_s = first_token_s
        self.calls: Counter[str] = Counter()

    def _item(self, messages: list[dict[str, Any]]) -> Item | None:
        text = str(messages[-1].get("content", ""))
        match = re.search(r"QUESTION: (.*?)\n\nCONSTRAINTS:", text, re.S)
        return self._by_question.get(match.group(1)) if match else None

    async def acompletion(self, **kwargs: Any) -> Any:
        import litellm

        model = str(kwargs["model"])
        kind = model.split("/", 1)[1] if "/" in model else model
        messages = kwargs["messages"]
        text = fingerprint(json.dumps(messages, sort_keys=True))
        digest = hashlib.sha256(text.encode()).hexdigest()[:12]
        self.calls[f"{model}:{digest}"] += 1
        await asyncio.sleep(0)
        if kind == "error500":
            raise litellm.InternalServerError("fake upstream error", "openrouter", model)
        if kind == "flaky" and self.calls[f"{model}:{digest}"] == 1:
            raise litellm.RateLimitError("fake rate limit", "openrouter", model)
        if kind == "rejects-reasoning" and _sends_thinking(kwargs):
            raise litellm.BadRequestError(
                "Reasoning is not supported for this model", model, "openrouter"
            )
        prompt = sum(len(str(m.get("content", ""))) for m in messages) // 4
        if not kwargs.get("stream"):
            return self._judge_response(model, digest, prompt)
        item = self._item(messages)
        text = "" if kind == "empty" or item is None else answer_for(item)
        thinks = kind == "thinks-anyway" or (kind != "empty" and _thinking_on(kwargs))
        return self._stream(model, digest, kind, text, thinks, prompt)

    async def _stream(
        self, model: str, digest: str, kind: str, text: str, thinks: bool, prompt: int
    ) -> Any:
        from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices, Usage

        gen_id = f"gen-fake-{digest}"

        def chunk(
            content: str | None = None,
            reasoning: str | None = None,
            finish: str | None = None,
            usage: Any = None,
        ) -> Any:
            delta = Delta(content=content, reasoning_content=reasoning)
            extra = {"usage": usage} if usage is not None else {}
            return ModelResponseStream(
                id=gen_id,
                model=model,
                choices=[StreamingChoices(delta=delta, finish_reason=finish)],
                **extra,
            )

        if thinks:
            for piece in ("Let me check ", "the passages."):
                await asyncio.sleep(0.001)
                yield chunk(reasoning=piece)
        await asyncio.sleep(self._first_token_s + (0.05 if kind == "slow" else 0.0))
        for i in range(0, len(text), 12):
            yield chunk(content=text[i : i + 12])
            await asyncio.sleep(0)
        completion = max(1, len(text) // 4) + (REASONING_TOKENS if thinks else 0)
        if kind == "no-usage":
            yield chunk(finish="stop")
            return
        usage_kwargs: dict[str, Any] = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "completion_tokens_details": {"reasoning_tokens": REASONING_TOKENS if thinks else 0},
        }
        if kind != "no-cost":
            usage_kwargs["cost"] = fake_cost(prompt, completion)
        yield chunk(finish="stop", usage=Usage(**usage_kwargs))

    def _judge_response(self, model: str, digest: str, prompt: int) -> Any:
        from litellm.types.utils import Choices, Message, ModelResponse, Usage

        content = json.dumps(
            {
                "fluency": 4,
                "naturalness": 4,
                "script_ok": True,
                "register_ok": True,
                "notes": "fake judge",
            }
        )
        completion = len(content) // 4
        return ModelResponse(
            id=f"gen-fake-judge-{digest}",
            model=model,
            choices=[
                Choices(
                    message=Message(content=content, role="assistant"),
                    finish_reason="stop",
                    index=0,
                )
            ],
            usage=Usage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
                cost=fake_cost(prompt, completion),
            ),
        )


class FakeLookup:
    source = "fake"

    async def lookup(self, generation_id: str) -> GenerationMeta:
        return GenerationMeta("FakeHost", None, None)
