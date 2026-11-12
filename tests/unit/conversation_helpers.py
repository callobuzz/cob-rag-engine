"""Fakes shared by the conversation-layer unit tests (C1 onwards)."""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.conversation.answering import AnswerDeps
from callobuzz_rag.conversation.director import Director, TurnInput
from callobuzz_rag.conversation.labeler import Labeler
from callobuzz_rag.conversation.offers import OfferRegistry
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.store import RuntimeConfig
from callobuzz_rag.conversation.types import Channel
from callobuzz_rag.core.config import Settings
from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.llm.cost import CallCost, CostLedger
from callobuzz_rag.llm.models import LLMMessage, LLMResponse, LLMUsage
from callobuzz_rag.retrieval.context_assembly import CitedChunk
from callobuzz_rag.retrieval.models import ChunkScoreBreakdown


class FakeLLM(LLMProvider):
    """Scripted replies. `json_replies` answer complete_json in order; `stream` is the
    answer streamed by complete_stream; `delay_s` makes every call slow.

    Like the real provider it records each call on `cost_ledger` (`call_cost_usd`, None =
    an unpriced call) and its `fast_call_options()` follows the reasoning-model rule: a
    known non-reasoning fast model gets a `max_tokens` cap, any other gets a
    `reasoning_effort` and no cap."""

    def __init__(
        self,
        *,
        json_replies: list[Any] | None = None,
        stream: list[str] | None = None,
        text: str = "ok",
        delay_s: float = 0.0,
        credentials: bool = True,
        non_reasoning: bool = False,
        call_cost_usd: float | None = 0.001,
    ) -> None:
        self.json_replies = list(json_replies or [])
        self.stream = list(stream or [])
        self.text = text
        self.delay_s = delay_s
        self.credentials = credentials
        self.non_reasoning = non_reasoning
        self.call_cost_usd = call_cost_usd
        self.cost_ledger = CostLedger()
        self.payer = "operator"
        self.calls: list[tuple[str, list[LLMMessage], dict[str, Any]]] = []

    @property
    def has_credentials(self) -> bool:
        return self.credentials

    @property
    def fast_model(self) -> str:
        return "fake-fast"

    def is_known_non_reasoning(self, model: str | None = None) -> bool:
        return self.non_reasoning

    def fast_call_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {"model": "fake-fast", "temperature": 0.0, "timeout": 8.0}
        if self.non_reasoning:
            options["max_tokens"] = 200
        else:
            options["reasoning_effort"] = "low"
        return options

    def _record(self, model: str, prompt: int, completion: int) -> None:
        self.cost_ledger.add(CallCost(model, prompt, completion, 0, self.call_cost_usd))

    async def complete(self, messages: list[LLMMessage], **kw: Any) -> LLMResponse:
        self.calls.append(("complete", messages, kw))
        await asyncio.sleep(self.delay_s)
        self._record("fake-model", 10, 5)
        return LLMResponse(content=self.text, model="fake-model", usage=LLMUsage(10, 5, 15))

    async def complete_stream(self, messages: list[LLMMessage], **kw: Any) -> AsyncIterator[str]:
        self.calls.append(("stream", messages, kw))
        for piece in self.stream:
            await asyncio.sleep(self.delay_s)
            yield piece
        self._record("fake-model", 10, 5)

    async def complete_json(self, messages: list[LLMMessage], **kw: Any) -> LLMResponse:
        self.calls.append(("json", messages, kw))
        await asyncio.sleep(self.delay_s)
        reply = self.json_replies.pop(0) if self.json_replies else {}
        content = reply if isinstance(reply, str) else json.dumps(reply)
        self._record("fake-fast", 20, 10)
        return LLMResponse(content=content, model="fake-fast", usage=LLMUsage(20, 10, 30))

    async def health_check(self) -> bool:
        return True


class FakeCache:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> Any | None:
        return self.data.get(key)

    async def set(self, key: str, value: Any, ttl_s: int) -> None:
        self.data[key] = value
        self.ttls[key] = ttl_s


def make_config(**updates: Any) -> AssistantConfig:
    """`AssistantConfig()` with Shree Labs identity, two topics and a business card;
    `updates` are deep-merged dicts per section (e.g. scope={"scope_mode": "strict_docs"})."""
    base: dict[str, Any] = {
        "identity": {
            "assistant_name": "Asha",
            "business_name": "Shree Labs",
            "business_type": "clinic",
            "business_profile": "Pathology lab in Pune with home sample collection.",
            "card": {
                "phone": "+919822012345",
                "whatsapp": "+919822012345",
                "email": "help@shree-labs.in",
                "website": "https://shree-labs.in",
                "address": "12 Karve Road, Kothrud, Pune 411038",
                "hours": [
                    {"day": d, "open": "07:00", "close": "20:00"}
                    for d in ("mon", "tue", "wed", "thu", "fri", "sat")
                ],
            },
        },
        "scope": {
            "topics": [
                {
                    "id": "tests",
                    "name": "Tests and preparation",
                    "description": "Lab tests",
                    "examples": ["Do I need to fast?"],
                },
                {
                    "id": "home_collection",
                    "name": "Home collection",
                    "description": "Sample pickup at home",
                    "examples": ["Do you come home?"],
                },
            ],
        },
    }
    for section, values in updates.items():
        base[section] = {**base.get(section, {}), **values} if isinstance(values, dict) else values
    return AssistantConfig.model_validate(base)


CHUNK = uuid.uuid4()
NOW = datetime(2026, 10, 5, 5, 0, tzinfo=UTC)  # Monday 10:30 IST, open


def fake_orch(dense: float = 0.72) -> MagicMock:
    cc = CitedChunk(
        citation_index=1,
        chunk_id=CHUNK,
        text="Home sample collection costs Rs 150 within Pune city.",
        file_name="home-collection.pdf",
        page=1,
        score=0.9,
        token_count=12,
        section="Charges",
    )
    pipeline = SimpleNamespace(
        processed_query="q",
        assembled=SimpleNamespace(
            messages=[LLMMessage("system", "s"), LLMMessage("user", "CONTEXT q")],
            cited_chunks=[cc],
        ),
        score_breakdown={
            str(CHUNK): ChunkScoreBreakdown(fused_score=1.0, pipeline_rank=1, dense_score=dense)
        },
        stage_timings_ms={"embed": 4},
    )
    orch = MagicMock()
    orch.run_retrieval_pipeline = AsyncMock(return_value=pipeline)
    return orch


def make_director(
    llm: FakeLLM, orch: MagicMock | None = None, registry: OfferRegistry | None = None
) -> Director:
    session = MagicMock()
    session.close = AsyncMock()
    session.commit = AsyncMock()
    session.invalidate = AsyncMock()
    deps = AnswerDeps(
        settings=Settings(_env_file=None),
        session_factory=lambda: session,
        orchestrator_factory=lambda s: orch or fake_orch(),
        retrieval_kwargs={},
        llm=llm,
        model_name="answer-model",
        translate_cache=None,
        payer="byok",
    )
    return Director(
        deps,
        labeler=Labeler(llm=llm, cache=None, timeout_s=1.0, ttl_s=60),
        registry=registry or OfferRegistry(),
    )


def make_turn(
    text: str, *, state: ConversationState | None = None, debug: bool = False, **cfg: Any
) -> TurnInput:
    return TurnInput(
        corpus_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        applied_embedding=None,
        runtime=RuntimeConfig(
            config=make_config(**cfg), version=3, draft_revision=None, enabled=True
        ),
        text=text,
        channel=Channel.WIDGET,
        usage_source="widget:k",
        conversation_id=None,
        state=state or ConversationState(),
        history=[],
        page_url=None,
        session_token="ws_new",
        debug=debug,
        is_preview=False,
        simulate=None,
        one_off_instructions=None,
        now=NOW,
    )


async def run_events(d: Director, turn: TurnInput) -> list[Any]:
    return [e async for e in d.run_stream(turn)]
