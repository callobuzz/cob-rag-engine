"""The conversation director (design §3): masks, labels, routes, answers, offers, records.

It never decides facts: business facts come only from `answering.grounded` (documents
behind gate 1 and gate 2) or from owner-written verbatim answers. Every surface consumes
`run_stream` (SSE) or `run` (JSON); writes happen before the final `done` event, in their
own session, shielded from a client hanging up (`api/streaming.py:shielded`).

One `LLMProvider` carries the whole turn (`AnswerDeps.llm`): its cost ledger is what the turn
cost, and what is metered, whether the turn succeeded or failed part-way.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

from callobuzz_rag.analytics.usage import record_usage_new_session
from callobuzz_rag.api.stage_events import (
    STAGE_CHECK,
    STAGE_HANDOFF,
    STAGE_QUEUED,  # PIP1 lane
    STAGE_UNDERSTAND,
    STAGE_WRITE,
    StageClock,
    events_until_done,
    public_stage,
)
from callobuzz_rag.api.streaming import shielded
from callobuzz_rag.conversation import smalltalk as st
from callobuzz_rag.conversation.answering import (
    Acc,
    AnswerDeps,
    Retrieved,
    cancel_retrieval,
    clarify_question,
    explainer,
    grounded,
    start_retrieval,
)
from callobuzz_rag.conversation.compiler import CompileTurn, compile_prompt, is_comparison
from callobuzz_rag.conversation.envelope import (
    DirectorEvent,
    DoneEvent,
    ErrorEvent,
    MetaEvent,
    QuickRepliesEvent,
    SourcesEvent,
    StageEvent,
    TokenEvent,
    TraceEvent,
    UiEvent,
)
from callobuzz_rag.conversation.human import is_human_request  # TV2 reference
from callobuzz_rag.conversation.labeler import LabelResult
from callobuzz_rag.conversation.language import detect_language, reply_language
from callobuzz_rag.conversation.loop_guard import loop_detected, note_reply
from callobuzz_rag.conversation.loop_guard import reset as reset_loop
from callobuzz_rag.conversation.offers import (
    OfferContext,
    OfferKind,
    OfferRegistry,
    PreTurnContext,
    PreTurnResult,
)
from callobuzz_rag.conversation.offers import registry as default_registry
from callobuzz_rag.conversation.pii import MaskResult, mask_pii, pii_notice
from callobuzz_rag.conversation.routing import RouteDecision, decide_route, needs_retrieval
from callobuzz_rag.conversation.rules import rule_labeler
from callobuzz_rag.conversation.safety import abuse_level, injection_markers
from callobuzz_rag.conversation.schema import DEFAULT_BUSY, DEFAULT_PAUSED  # PIP1 lane
from callobuzz_rag.conversation.signals import TurnSignals, office_state
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.turns import record_turn
from callobuzz_rag.conversation.types import (
    Channel,
    LanguageInfo,
    OfficeState,
    Route,
    Script,
    TurnLabel,
    script_of,
)
from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.core.pause import PausedBy
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message
from callobuzz_rag.db.models.query_log import QueryLog
from callobuzz_rag.fairuse.alerts import schedule_busy  # PIP3 gate
from callobuzz_rag.llm import local as local_llm
from callobuzz_rag.llm.cost import CostLedger
from callobuzz_rag.llm.language_routing import ROUTED_THINKING, routing_note
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.tiers.slot import SlotHold  # PIP1 lane

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.api.schemas.assistant import SimulateOptions
    from callobuzz_rag.conversation.gates import GateResult
    from callobuzz_rag.conversation.labeler import Labeler
    from callobuzz_rag.conversation.store import RuntimeConfig
    from callobuzz_rag.tiers.slot import SlotSpec  # PIP1 lane

logger = logging.getLogger(__name__)

_UI_ROUTES = {Route.GROUNDED, Route.VERBATIM, Route.SMALLTALK, Route.EXPLAINER, Route.NO_ANSWER}


# TV2 reference
def _card_can_be_offered(
    registry: "OfferRegistry", ctx: "OfferContext", forced: "OfferKind | None", *, ui_ok: bool
) -> bool:
    """True when this turn may show a handoff card on a real conversation (not a preview, not a
    stateless API turn): forced, or a trigger fired and a card fits the route."""
    if ctx.conversation_id is None or ctx.signals.is_preview:
        return False
    cards = registry.providers(OfferKind.HANDOFF_CARD)
    if not cards:
        return False
    if forced is OfferKind.HANDOFF_CARD:
        return True
    return ui_ok and forced is None and any(p.wanted(ctx) for p in cards)


# /TV2 reference
_SEARCH_FAILED = "The assistant could not search its documents."
_ANSWER_FAILED = "The assistant could not answer right now. Please try again."


class DirectorError(RAGKernelError):
    """The turn ended in an `error` event: `detail` is safe to show, `code` is the event's."""

    def __init__(self, detail: str, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code


@dataclass
class TurnInput:
    corpus_id: uuid.UUID
    tenant_id: uuid.UUID
    applied_embedding: dict[str, Any] | None
    runtime: "RuntimeConfig"
    text: str
    channel: Channel
    usage_source: str
    conversation_id: uuid.UUID | None
    state: ConversationState
    history: list[LLMMessage]
    page_url: str | None
    session_token: str | None
    debug: bool
    is_preview: bool
    simulate: "SimulateOptions | None"
    one_off_instructions: str | None
    now: datetime
    widget_key_id: uuid.UUID | None = None  # widget turns (C4's pre-turn capture reads it)
    utm: dict[str, str] | None = None
    # False for synthetic turns (test runs): metered, but never written as traffic.
    persist: bool = True
    # BIL4 paused: the workspace's balance ran out. The turn costs nothing: rules-only label, no
    # search, no model; a question gets the pause message and the handoff card.
    billing_paused: bool = False
    # BIZ4 learning: the business's website is still being read and the knowledge base has no
    # document of its own yet. Like a pause it costs nothing (rules-only label, no search, no
    # model); a question gets the "still learning" reply and the handoff card.
    learning: bool = False
    # PIP1 lane: why the turn is a free pause reply instead of an answer. `billing`: the
    # BIL4 flag above; `fair_use`: set by the fair-use gate (PIP3); `busy`: set by the director
    # itself when the answer slot did not come free in time.
    paused_by: PausedBy | Literal["busy"] | None = None
    # The answer slot this turn must hold (None: no lane applies).
    slot: "SlotSpec | None" = None
    # /PIP1 lane
    # TV1 human: the visitor asked for a person and the route skipped the cap, billing, fair-use
    # and lane gates. The turn is free: rules-only label, no search, no model, not metered.
    human_only: bool = False
    # /TV1 human
    # TV6 channel: the id a retry of the same message repeats (WhatsApp: a uuid5 of the wamid),
    # used as the turn's query id and so as its billing request id; and the customer's own
    # number on a channel that knows it (E.164), offered to the lead hook after consent.
    request_id: uuid.UUID | None = None
    contact_phone: str | None = None
    # /TV6 channel

    @property
    def pause_kind(
        self,
    ) -> PausedBy | Literal["busy"] | None:  # PIP1 lane
        """Why this turn is a pause reply, or None when it is a normal turn."""
        return self.paused_by or ("billing" if self.billing_paused else None)

    @property
    def model_free(self) -> bool:
        """No model labelling, no search and no model call: paused, busy, learning, or a
        request for a person (TV1)."""
        return (  # TV1 human
            self.pause_kind is not None
            or self.learning
            or self.human_only
            or loop_detected(self.state)  # a repeated reply: the next turn is the handoff card
        )

    # /BIZ4 learning


# A follow-up probe is embedding + search only: no HyDE, no classification, no translation.
_PROBE_OVERRIDES: Final = {
    "enable_hybrid": False,  # dense scores only: a keyword-only hit must not pass the gate
    "enable_hyde": False,
    "enable_classification": False,
    "enable_reranking": False,
    "enable_mmr": False,
}


def _ledger_mark(llm: Any) -> int:
    ledger = getattr(llm, "cost_ledger", None)
    return len(ledger.calls) if isinstance(ledger, CostLedger) else 0


def _spent_since(llm: Any, mark: int) -> Any:
    """What this turn spent: the ledger's calls after `mark`, so a provider shared by several
    turns (a test run) never bills one turn for another's calls. None when there were none."""
    ledger = getattr(llm, "cost_ledger", None)
    if not isinstance(ledger, CostLedger) or len(ledger.calls) <= mark:
        return None
    return CostLedger(calls=ledger.calls[mark:]).summary()


@dataclass(frozen=True)
class TurnPlan:
    """What `Director.plan` decides: the turn's label and final route, no answer written."""

    label: TurnLabel
    label_confidence: float
    route: Route
    gate: "GateResult | None"


@dataclass
class _Prepared:
    masked: MaskResult
    question: str
    injection: list[str]
    abuse: int
    info: LanguageInfo
    lang: str
    script: Script
    office: OfficeState
    page_url: str | None
    label: LabelResult | None
    task: "asyncio.Task[Retrieved] | None"
    pre_turn: PreTurnResult | None
    compile_turn: Callable[[TurnLabel, Route, str | None], CompileTurn]
    start: Callable[..., "asyncio.Task[Retrieved]"]
    speculation: "_Speculation | None" = None


@dataclass
class _Speculation:
    """A grounded answer started before the label arrived (`Director._speculate`). Its events
    wait in `events` and its bookkeeping in `acc` until the director adopts or discards it."""

    retrieved: Retrieved
    system_prompt: str
    acc: Acc
    events: "asyncio.Queue[DirectorEvent]"
    task: "asyncio.Task[None]"

    async def discard(self) -> None:
        """Cancel the answer and wait for it to unwind. What it already cost stays on the
        provider's ledger, so the turn is billed for it."""
        self.task.cancel()
        with contextlib.suppress(BaseException):
            await self.task


# The `Acc` fields a grounded answer writes, copied from an adopted speculation.
_ANSWER_FIELDS: Final = (
    "sources",
    "sources_sent",
    "route",
    "removed",
    "flagged",
    "verdicts",
    "model",
    "rules_fired",
    "prompt",
    "thinking_ms",
    "reasoning_tokens",
)


# TV1 human: handoff reasons that must always leave the visitor a way to reach someone
_ALWAYS_A_PATH: Final = frozenset({"explicit_request", "loop", "misses"})

# BIL4 paused
# What `done.pause_reason` may say (a "busy" turn is not a pause the caller is told about)
_PAUSE_REASONS: Final = ("billing", "fair_use", "local_unavailable", "demo_limit")
_PAUSED_ROUTES: Final = {Route.GROUNDED, Route.EXPLAINER, Route.NO_ANSWER}


def _paused_decision(
    decision: RouteDecision,
    turn: TurnInput,
    label: LabelResult,
    *,
    has_handoff: bool,
) -> RouteDecision:
    """While answers are paused, every route that would search or call the model becomes the
    pause reply (a handoff with the pause message). Rule-answered routes (greetings, verbatim
    topics, decline, abuse warning, handoff requests) are free and stay."""
    kind = turn.pause_kind  # PIP1 lane
    if kind is None:
        return decision
    model_smalltalk = decision.route == Route.SMALLTALK and not label.intent
    if decision.route not in _PAUSED_ROUTES and not model_smalltalk:
        return decision
    if kind == "demo_limit":  # L5a demo: a fixed polite text, no card, no contact line
        return RouteDecision(Route.HANDOFF, "demo_limit", decision.topic_id, None)
    handoff_ok = has_handoff and turn.runtime.config.handoff.enabled
    return RouteDecision(
        Route.HANDOFF,
        "busy" if kind == "busy" else "billing_paused",  # PIP1 lane
        decision.topic_id,
        OfferKind.HANDOFF_CARD if handoff_ok else None,
    )


# /BIL4 paused


# BIZ4 learning
def _learning_decision(
    decision: RouteDecision,
    turn: TurnInput,
    label: LabelResult,
    *,
    has_handoff: bool,
) -> RouteDecision:
    """While the website is first being learned, every route that would search or call the model
    becomes the "still learning" reply (a handoff). Free routes stay, and the pause message of
    billing (already applied) wins: it is a handoff route, so it is left alone here."""
    if not turn.learning or decision.reason in ("billing_paused", "busy", "demo_limit"):
        return decision
    model_smalltalk = decision.route == Route.SMALLTALK and not label.intent
    if decision.route not in _PAUSED_ROUTES and not model_smalltalk:
        return decision
    handoff_ok = has_handoff and turn.runtime.config.handoff.enabled
    return RouteDecision(
        Route.HANDOFF,
        "still_learning",
        decision.topic_id,
        OfferKind.HANDOFF_CARD if handoff_ok else None,
    )


# /BIZ4 learning


# TV1 human
_LOOP_KEEPS: Final = (Route.HANDOFF, Route.ABUSE_WARNING, Route.SMALLTALK)


def _loop_decision(decision: RouteDecision, turn: TurnInput, *, has_handoff: bool) -> RouteDecision:
    """The assistant said the same thing twice in a row: this turn is the handoff card (the
    contact line when there is no card), whatever was asked. A turn that is already a handoff,
    an abuse warning or small talk stays as it is."""
    if not loop_detected(turn.state) or decision.route in _LOOP_KEEPS:
        return decision
    handoff_ok = has_handoff and turn.runtime.config.handoff.enabled
    if handoff_ok:
        return RouteDecision(Route.HANDOFF, "loop", decision.topic_id, OfferKind.HANDOFF_CARD)
    return RouteDecision(Route.VERBATIM, "loop", decision.topic_id)


# /TV1 human


def _notice(prep: _Prepared) -> str:
    """The masked-PII notice that opens the reply, or ""."""
    return pii_notice(prep.lang) + "\n\n" if prep.masked.kinds else ""


@dataclass
class TurnRecord:
    turn: TurnInput
    query_id: uuid.UUID
    masked: str
    label: "LabelResult"
    language: str
    script: str
    route: Route
    topic_id: str | None
    gate: "GateResult | None"
    answer: str
    sources: list[dict[str, Any]]
    claims_removed: int
    claims_flagged: int
    rules_fired: list[str]
    offers: list[str]
    latency_ms: int
    timings: dict[str, Any]  # what `assistant_turns.timings` keeps
    model: str | None
    usage: dict[str, Any]  # estimates + the real cost fields, as `QueryLog.token_usage`
    state: ConversationState
    extra: dict[str, Any] = field(default_factory=dict)
    # TV4 value
    office: OfficeState = OfficeState.UNKNOWN
    # /TV4 value


async def persist_turn(
    session_factory: "Callable[[], AsyncSession] | None", rec: TurnRecord
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Messages (masked), conversation state, query_log, assistant_turns — one commit.
    Best-effort like metering: a failed write is logged, never shown to the visitor."""
    if session_factory is None:
        return None, None
    user_id: uuid.UUID | None = None
    reply_id: uuid.UUID | None = None
    completion = int(rec.usage.get("completion_tokens", 0))
    total = int(rec.usage.get("total_tokens", 0))
    try:
        async with session_factory() as s:
            if rec.turn.conversation_id is not None:
                # Locked: the widget event route writes `cta_declined` and the click counters
                # between two turns; merge them in so this write never erases them.
                conv = await s.get(Conversation, rec.turn.conversation_id, with_for_update=True)
                if conv is not None:
                    # Both rows are written in one transaction, so `now()` would tie them;
                    # an explicit pair of timestamps keeps the question before its answer.
                    asked_at = datetime.now(UTC)
                    user = Message(
                        conversation_id=conv.id,
                        role="user",
                        content=rec.masked,
                        token_count=0,
                        created_at=asked_at,
                    )
                    reply = Message(
                        conversation_id=conv.id,
                        role="assistant",
                        content=rec.answer,
                        token_count=completion,
                        created_at=asked_at + timedelta(microseconds=1),
                        retrieval_context=(
                            {
                                "chunks": [
                                    {
                                        "chunk_id": x["chunk_id"],
                                        "text": x["text"][:500],
                                        "score": x["score"],
                                        "document_id": x["document_id"],
                                    }
                                    for x in rec.sources
                                ],
                                "total_chunks": len(rec.sources),
                            }
                            if rec.sources
                            else None
                        ),
                    )
                    s.add_all([user, reply])
                    await s.flush()
                    user_id, reply_id = user.id, reply.id
                    conv.message_count = (conv.message_count or 0) + 2
                    conv.total_tokens = (conv.total_tokens or 0) + total
                    rec.state.merge_event_keys(conv.assistant_state)
                    conv.assistant_state = rec.state.to_dict()
                    conv.language = rec.language
                    conv.assistant_version = rec.turn.runtime.version
                    if conv.title is None:
                        # No model call for a title on assistant turns: the visitor's first
                        # (masked) message, cut at 60 characters.
                        conv.title = rec.masked[:60]
            s.add(
                QueryLog(
                    id=rec.query_id,
                    corpus_id=rec.turn.corpus_id,
                    query_text=rec.masked,
                    retrieved_chunk_ids=[uuid.UUID(x["chunk_id"]) for x in rec.sources],
                    response=rec.answer,
                    confidence_score=None,
                    latency_ms=rec.latency_ms,
                    model_used=(rec.model or "none")[:100],
                    token_usage=rec.usage,
                )
            )
            await record_turn(
                s,
                corpus_id=rec.turn.corpus_id,
                tenant_id=rec.turn.tenant_id,
                conversation_id=rec.turn.conversation_id,
                message_id=reply_id,
                query_id=rec.query_id,
                channel=rec.turn.channel,
                assistant_version=rec.turn.runtime.version,
                label=rec.label.label.value,
                label_source=rec.label.source,
                label_confidence=rec.label.confidence,
                language=rec.language,
                script=rec.script,
                route=rec.route.value,
                topic_id=rec.topic_id,
                top_score=rec.gate.top_score if rec.gate else None,
                gate_passed=rec.gate.passed if rec.gate else None,
                claims_removed=rec.claims_removed,
                claims_flagged=rec.claims_flagged,
                rules_fired=rec.rules_fired,
                offers=rec.offers,
                no_answer=rec.route == Route.NO_ANSWER,
                question_masked=rec.masked,
                is_preview=rec.turn.is_preview,
                latency_ms=rec.latency_ms,
                timings=rec.timings,
                office_open=None
                if rec.office is OfficeState.UNKNOWN
                else rec.office is OfficeState.OPEN,  # TV4 value
            )
            await s.commit()
    except Exception:
        logger.warning("Could not record assistant turn %s", rec.query_id, exc_info=True)
    return user_id, reply_id


class Director:
    def __init__(
        self,
        deps: AnswerDeps,
        *,
        labeler: "Labeler",
        registry: OfferRegistry = default_registry,
        closers: list[Callable[[], Awaitable[None]]] | None = None,
    ) -> None:
        self._deps = deps
        self._labeler = labeler
        self._registry = registry
        self._closers = list(closers or [])
        # PIP1 lane: the slot a turn without its own `slot` holds (golden runs in the worker)
        self.default_slot: SlotSpec | None = None

    def _answer_deps(self, lang: str) -> AnswerDeps:
        """The deps a turn answers with in `lang`: the engine's, or, when language routing names
        a model for `lang`, the same deps on that model (`LiteLLMProvider.for_model`: one shared
        cost ledger, thinking `ROUTED_THINKING`). The label, search and offer calls keep the fast
        model either way."""
        deps = self._deps
        model = deps.language_models.get(lang)
        for_model = getattr(deps.llm, "for_model", None)
        if not model or not callable(for_model):
            return deps
        return replace(deps, llm=for_model(model, ROUTED_THINKING), model_name=model)

    async def aclose(self) -> None:
        """Release what the director owns (a worker director's embedder and vector client).
        Safe to call more than once."""
        closers, self._closers = self._closers, []
        for close in closers:
            try:
                await close()
            except Exception:
                logger.warning("Director cleanup failed", exc_info=True)

    async def run(self, turn: TurnInput) -> DoneEvent:
        async with contextlib.aclosing(self.run_stream(turn)) as stream:
            async for event in stream:
                if isinstance(event, ErrorEvent):
                    raise DirectorError(event.detail, event.code)
                if isinstance(event, DoneEvent):
                    return event
        raise DirectorError("The assistant produced no answer.")

    async def plan(self, turn: TurnInput) -> TurnPlan:
        """Understand and route a turn without answering it: no answer-model call, nothing
        persisted or metered (C7's fast golden runs; README P15). Uses the same `_prepare`
        step as `run_stream`, then waits for retrieval (when the route needs documents) so
        the gate decides the final route exactly as a real turn would."""
        acc = Acc(query_id=uuid.uuid4())
        prep = await self._prepare(turn, acc, StageClock(), asyncio.Queue(), speculate=False)
        label = prep.label
        assert label is not None
        try:
            if prep.pre_turn is not None and prep.pre_turn.reply is not None:
                return TurnPlan(label.label, label.confidence, Route.LEAD, None)
            gate = None
            if prep.task is not None:
                retrieved = await prep.task
                prep.task = None
                gate = retrieved.gate
            decision = decide_route(
                label=label,
                gate=gate,
                config=turn.runtime.config,
                state=turn.state,
                has_handoff=self._registry.has(OfferKind.HANDOFF_CARD),
            )
            return TurnPlan(label.label, label.confidence, decision.route, gate)
        finally:
            if prep.task is not None:
                await cancel_retrieval(prep.task)

    async def _run_pre_turn(
        self, turn: TurnInput, question: str, lang: str, page_url: str | None
    ) -> PreTurnResult | None:
        """README P9: hooks run right after PII masking; each opens its own sessions from
        `session_factory`. The first hook that returns a result wins."""
        hooks = self._registry.pre_turn_hooks()
        if not hooks:
            return None
        ctx = PreTurnContext(
            config=turn.runtime.config,
            state=turn.state,
            text=question,
            language=lang,
            corpus_id=turn.corpus_id,
            tenant_id=turn.tenant_id,
            conversation_id=turn.conversation_id,
            widget_key_id=turn.widget_key_id,
            channel=turn.channel,
            page_url=page_url,
            utm=turn.utm,
            session_factory=self._deps.session_factory,
            settings=self._deps.settings,
            now=turn.now,
            is_preview=turn.is_preview,
            contact_phone=turn.contact_phone,  # TV6 channel
        )
        for hook in hooks:
            result = await hook(ctx)
            if result is not None:
                return result
        return None

    def _answered_by_rules(self, turn: TurnInput, prep: _Prepared) -> bool:
        """True when this turn needs neither search nor model: a pre-turn hook answered it, or
        the rules label routes it somewhere free (a greeting, a verbatim topic, a handoff
        request, a decline). Such a turn never queues for an answer slot."""
        if prep.pre_turn is not None and prep.pre_turn.reply is not None:
            return True
        label = self._labeler.by_rules(prep.question)
        if label is None:
            return False
        has_handoff = self._registry.has(OfferKind.HANDOFF_CARD)
        route = decide_route(
            label=label,
            gate=None,
            config=turn.runtime.config,
            state=turn.state,
            has_handoff=has_handoff,
        ).route
        model_smalltalk = route == Route.SMALLTALK and not label.intent
        return route not in _PAUSED_ROUTES and not model_smalltalk

    async def _prepare(
        self,
        turn: TurnInput,
        acc: Acc,
        clock: StageClock,
        events: "asyncio.Queue[StageEvent]",
        *,
        speculate: bool = True,
    ) -> _Prepared:
        """`_prepare_head` then `_prepare_tail` (`plan` has no lane to wait for in between)."""
        prep = await self._prepare_head(turn, acc, clock, events)
        return await self._prepare_tail(turn, acc, prep, speculate=speculate)

    async def _prepare_head(
        self,
        turn: TurnInput,
        acc: Acc,
        clock: StageClock,
        events: "asyncio.Queue[StageEvent]",
    ) -> _Prepared:
        """Steps 1-3 of design §3 (mask, pre-turn hooks, language, label) and the start of
        retrieval when the turn needs documents. Shared by `run_stream` and `plan`. The
        retrieval's own progress goes to `events` through `clock`.

        A message the rules cannot label is searched while the fast model labels it. With
        `speculate` (and RAG_ASSISTANT_SPECULATIVE_ANSWER), a search that finishes first and
        passes its gate also starts the grounded answer (`_speculate`)."""
        config = turn.runtime.config
        sim = turn.simulate

        started = time.monotonic()
        masked = mask_pii(turn.text)
        question = masked.text
        injection = injection_markers(question)
        abuse = abuse_level(turn.text)
        acc.lap("safety", started)

        started = time.monotonic()
        info = detect_language(question)
        if sim is not None and sim.language:
            info = LanguageInfo(code=sim.language, script=script_of(sim.language), confidence=1.0)
        lang, script = reply_language(config, info)
        office = (
            OfficeState(sim.office_hours)
            if sim is not None and sim.office_hours
            else office_state(config.identity.card, turn.now)
        )
        page_url = sim.page_url if sim is not None and sim.page_url else turn.page_url
        acc.lap("language", started)

        pre = await self._run_pre_turn(turn, question, lang, page_url)
        if pre is not None and pre.stored_text is not None:
            question = pre.stored_text  # what the transcript keeps (e.g. "[phone shared]")

        # TV5 disclosure: the deployer's lock outranks an owner who switched their own line off
        required_line: str | None = None
        if not config.identity.ai_disclosure:
            from callobuzz_rag.conversation.disclosure import forced_line, read_trust

            required, editable = await read_trust(self._deps.session_factory)
            required_line = forced_line(config, lang, required=required, editable=editable)

        def compile_turn(label: TurnLabel, route: Route, topic_id: str | None) -> CompileTurn:
            return CompileTurn(
                label=label,
                route=route,
                language=lang,
                script=script,
                office=office,
                page_url=page_url,
                topic_id=topic_id,
                lead_captured=turn.state.lead_id is not None,
                one_off_instructions=turn.one_off_instructions,
                comparison=is_comparison(question),
                disclosure=required_line,  # TV5 disclosure
            )

        def on_stage(raw: str) -> None:
            moved = clock.advance(public_stage(raw))
            if moved is not None:
                events.put_nowait(StageEvent(*moved))

        def start(
            text: str, *, staged: bool = True, probe: bool = False
        ) -> "asyncio.Task[Retrieved]":
            return start_retrieval(
                self._deps,
                corpus_id=turn.corpus_id,
                text=text,
                info=info,
                config=config,
                compile_turn=compile_turn(TurnLabel.BUSINESS_QUESTION, Route.GROUNDED, None),
                applied_embedding=turn.applied_embedding,
                on_stage=on_stage if staged else None,
                retrieval_overrides=_PROBE_OVERRIDES if probe else None,
                translate=not probe,
            )

        prep = _Prepared(
            masked=masked,
            question=question,
            injection=injection,
            abuse=abuse,
            info=info,
            lang=lang,
            script=script,
            office=office,
            page_url=page_url,
            label=None,
            task=None,
            pre_turn=pre,
            compile_turn=compile_turn,
            start=start,
        )
        if pre is not None and pre.reply is not None:
            prep.label = LabelResult(TurnLabel.BUYING_INTENT, "rules", 1.0, intent="lead_capture")
        return prep

    async def _prepare_tail(
        self, turn: TurnInput, acc: Acc, prep: _Prepared, *, speculate: bool = True
    ) -> _Prepared:
        """The label and the start of retrieval (what an answer slot is held around)."""
        if prep.label is not None:  # a pre-turn hook already answered
            return prep
        config = turn.runtime.config
        question = prep.question
        start = prep.start
        started = time.monotonic()
        scope_key = (
            str(turn.runtime.version)
            if turn.runtime.version is not None
            else f"d{turn.runtime.draft_revision or 0}"
        )
        label = self._labeler.by_rules(question)
        if label is None and turn.model_free:  # BIL4 paused / BIZ4 learning: no model labelling
            label = LabelResult(TurnLabel.BUSINESS_QUESTION, "fallback", 0.0)
        if label is None:
            prep.task = start(question)
            labelling = asyncio.ensure_future(
                self._labeler.by_model(
                    question,
                    config=config,
                    corpus_id=turn.corpus_id,
                    scope=scope_key,
                    history=turn.history,
                )
            )
            try:
                if speculate and self._deps.settings.assistant_speculative_answer:
                    await self._speculate(turn, prep, labelling)
                label = await labelling
            finally:
                if not labelling.done():
                    labelling.cancel()
        acc.lap("label", started)
        prep.label = label
        wants_docs = needs_retrieval(label, config) and not turn.model_free  # BIL4/BIZ4: no search
        if wants_docs and prep.task is None:
            prep.task = start(question)
        elif not wants_docs and prep.task is not None:
            await cancel_retrieval(prep.task)
            prep.task = None
        if not wants_docs and prep.speculation is not None:
            await prep.speculation.discard()
            prep.speculation = None
        return prep

    async def _speculate(
        self, turn: TurnInput, prep: _Prepared, labelling: "asyncio.Future[LabelResult]"
    ) -> None:
        """While the label is pending: if the search finishes first and its gate passes, start
        the grounded answer as a business question, the label most such turns get. Its events
        are held; `run_stream` adopts them only when the final route is grounded on this same
        search with the same compiled prompt, and discards them otherwise."""
        task = prep.task
        assert task is not None
        pending: set[asyncio.Future[Any]] = {labelling, task}
        await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        if labelling.done() or not task.done() or task.cancelled():
            return
        retrieved = task.result()
        gate = retrieved.gate
        if retrieved.error is not None or retrieved.user_message is None:
            return
        if gate is None or not gate.passed:
            return
        config = turn.runtime.config
        compile_turn = prep.compile_turn(TurnLabel.BUSINESS_QUESTION, Route.GROUNDED, None)
        spec_acc = Acc(query_id=uuid.uuid4())
        queue: asyncio.Queue[DirectorEvent] = asyncio.Queue()

        async def answer() -> None:
            async for event in grounded(
                self._answer_deps(prep.lang),
                config=config,
                compile_turn=compile_turn,
                retrieved=retrieved,
                question=prep.question,
                history=turn.history,
                acc=spec_acc,
                clock=StageClock(),  # its stage events are dropped: the turn's clock speaks
                prefix=_notice(prep),
            ):
                if not isinstance(event, StageEvent):
                    queue.put_nowait(event)

        prep.speculation = _Speculation(
            retrieved=retrieved,
            system_prompt=compile_prompt(config, compile_turn).system_prompt,
            acc=spec_acc,
            events=queue,
            task=asyncio.create_task(answer()),
        )

    def _adoptable(
        self,
        spec: _Speculation,
        prep: _Prepared,
        decision: RouteDecision,
        turn: TurnInput,
        label: LabelResult,
        retrieved: Retrieved | None,
    ) -> bool:
        """The speculative answer is the one this turn would write: grounded, on the same
        search, from the same compiled prompt."""
        if decision.route != Route.GROUNDED or retrieved is not spec.retrieved:
            return False
        final = prep.compile_turn(label.label, Route.GROUNDED, decision.topic_id)
        return compile_prompt(turn.runtime.config, final).system_prompt == spec.system_prompt

    async def _adopt(
        self, spec: _Speculation, acc: Acc, clock: StageClock
    ) -> AsyncIterator[DirectorEvent]:
        """Release a speculative answer: what it has written so far, then the rest."""
        moved = clock.advance(STAGE_WRITE)
        if moved is not None:
            yield StageEvent(*moved)
        async for event in events_until_done(spec.task, spec.events):
            yield event
        spec.task.result()  # its failure (LLMError) is the turn's
        done = spec.acc
        acc.parts.extend(done.parts)
        for name in _ANSWER_FIELDS:
            setattr(acc, name, getattr(done, name))
        acc.timings.update(done.timings)
        acc.prompt_tokens += done.prompt_tokens
        acc.completion_tokens += done.completion_tokens

    def _probe(self, prep: _Prepared) -> Callable[[str], Awaitable[bool]]:
        """A retrieve-only gate check for a suggested follow-up (C6). No answer-model call;
        its cost is one embedding + search: no translation, HyDE, classification, re-rank or MMR
        (_PROBE_OVERRIDES)."""

        async def probe(text: str) -> bool:
            retrieved = await prep.start(text, staged=False, probe=True)
            return bool(retrieved.gate is not None and retrieved.gate.passed)

        return probe

    async def _meter_failed(
        self,
        turn: TurnInput,
        mark: int = 0,
        model: str | None = None,
        query_id: uuid.UUID | None = None,
    ) -> None:
        """A turn that failed after paying for model calls: record that spend (tokens and
        cost), but not another answered question."""
        spent = _spent_since(self._deps.llm, mark)
        if spent is None:
            return
        await shielded(
            record_usage_new_session(
                self._deps.session_factory,
                corpus_id=turn.corpus_id,
                tenant_id=turn.tenant_id,
                source=turn.usage_source,
                prompt_tokens=0,
                completion_tokens=0,
                model=model or self._deps.model_name,
                cost=spent,
                payer=self._deps.payer,
                requests=0,
                request_id=uuid.uuid5(query_id, "failed") if query_id else None,  # BIL3 request id
            )
        )

    async def run_stream(self, turn: TurnInput) -> AsyncGenerator[DirectorEvent]:
        """The turn's events. A turn on the free local model that fails before it said anything
        (the model will not load, the server just stopped) is never an error for the visitor: the
        engine stops calling the model ready, and the turn is answered again as the polite busy
        reply. Only the meta and sources frames are held back to make that possible (they
        precede the first word), and only for a turn that runs on the local model."""
        if not local_llm.serves_locally(self._deps.llm):
            async with contextlib.aclosing(self._run_stream_once(turn)) as plain:
                async for event in plain:
                    yield event
            return
        held: list[DirectorEvent] = []
        spoke = False
        failed = False
        async with contextlib.aclosing(self._run_stream_once(turn)) as stream:
            async for event in stream:
                if not spoke:
                    if isinstance(event, MetaEvent | SourcesEvent):
                        held.append(event)
                        continue
                    if isinstance(event, ErrorEvent) and event.detail == _ANSWER_FAILED:
                        failed = True
                        held.clear()
                        continue
                    if not isinstance(event, StageEvent):
                        spoke = True
                        for frame in held:
                            yield frame
                        held.clear()
                yield event
        for frame in held:  # a turn that ended before saying anything else
            yield frame
        if not failed:
            return
        logger.warning("The local model failed an answer; the turn becomes the busy reply")
        local_llm.mark_unavailable(self._deps.settings)
        retry = replace(turn, paused_by="busy")
        async with contextlib.aclosing(self._run_stream_once(retry)) as again:
            async for event in again:
                yield event

    async def _run_stream_once(self, turn: TurnInput) -> AsyncGenerator[DirectorEvent]:
        clock = StageClock()
        mark = _ledger_mark(self._deps.llm)
        config = turn.runtime.config
        state = turn.state
        acc = Acc(query_id=turn.request_id or uuid.uuid4())  # TV6 channel: a stable id on a retry
        stopped_before = state.stopped  # TV1 human: a stop reply is not another miss
        queue: asyncio.Queue[StageEvent] = asyncio.Queue()
        prep: _Prepared | None = None
        answer_model = self._deps.model_name  # what the turn is metered under (language routing)
        metered = False
        hold = SlotHold(None, turn.tenant_id)  # PIP1 lane
        try:
            # The first frame leaves at once; the label call that follows can take a while.
            first = clock.advance(STAGE_UNDERSTAND)
            if first is not None:
                yield StageEvent(*first)
            # PIP1 lane: wait for an answer slot, never for a free turn (paused, learning, or
            # one the rules answer alone: a greeting, a verbatim topic, a handoff request, the
            # lead-capture pre-turn). The slot is held around the label, retrieval and
            # generation only. While queued the stream says so once; a wait that ends in a
            # timeout turns the turn into the polite busy reply (no model call, nothing
            # metered, never an error).
            prep = await self._prepare_head(turn, acc, clock, queue)
            lane = None if turn.model_free else (turn.slot or self.default_slot)
            if lane is not None and self._answered_by_rules(turn, prep):
                lane = None
            hold = SlotHold(lane, turn.tenant_id)
            queued = False
            while True:
                async for _waited_ms in hold.wait():
                    if not queued:
                        queued = True
                        yield StageEvent(STAGE_QUEUED, clock.elapsed_ms())
                if hold.busy and lane is not None and lane.patient:
                    continue  # a golden run is never answered "busy": it waits again
                break
            if hold.busy:
                turn.paused_by = "busy"
                schedule_busy(  # PIP3 gate: counted, and given back to the question windows
                    self._deps.session_factory,
                    turn.tenant_id,
                    redis=lane.lanes.redis if lane is not None else None,
                )
            # /PIP1 lane
            prep = await self._prepare_tail(turn, acc, prep)
            label = prep.label
            assert label is not None
            question, lang, script, office = prep.question, prep.lang, prep.script, prep.office
            answer_model = self._answer_deps(lang).model_name
            has_handoff = self._registry.has(OfferKind.HANDOFF_CARD)
            retrieved: Retrieved | None = None
            gate: GateResult | None = None
            retrieval_ms: int | None = None

            if prep.pre_turn is not None and prep.pre_turn.reply is not None:
                # A pre-turn hook (C4's conversational lead capture) answered the turn.
                yield MetaEvent(
                    query_id=acc.query_id,
                    conversation_id=turn.conversation_id,
                    session=turn.session_token,
                    label=label.label,
                    route=Route.LEAD,
                    language=lang,
                    script=script,
                    assistant_version=turn.runtime.version,
                    label_source=label.source if turn.debug else None,
                )
                acc.route = Route.LEAD
                acc.parts.append(prep.pre_turn.reply)
                yield TokenEvent(content=prep.pre_turn.reply)
                decision = RouteDecision(route=Route.LEAD, reason="pre_turn")
                pre_replies = list(prep.pre_turn.quick_replies)
            else:
                planned = _loop_decision(  # TV1 human
                    _learning_decision(  # BIZ4 learning
                        _paused_decision(  # BIL4 paused
                            decide_route(
                                label=label,
                                gate=None,
                                config=config,
                                state=state,
                                has_handoff=has_handoff,
                            ),
                            turn,
                            label,
                            has_handoff=has_handoff,
                        ),
                        turn,
                        label,
                        has_handoff=has_handoff,
                    ),
                    turn,
                    has_handoff=has_handoff,
                )
                yield MetaEvent(
                    query_id=acc.query_id,
                    conversation_id=turn.conversation_id,
                    session=turn.session_token,
                    label=label.label,
                    route=planned.route,
                    language=lang,
                    script=script,
                    assistant_version=turn.runtime.version,
                    label_source=label.source if turn.debug else None,
                )
                pre_replies = []
                if prep.task is not None:
                    async for progress in events_until_done(prep.task, queue):
                        yield progress
                    retrieved = prep.task.result()
                    prep.task = None
                    retrieval_ms = clock.elapsed_ms()
                    acc.stage_timings.update(retrieved.stage_timings)
                    acc.timings.update(retrieved.timings)
                    if retrieved.error is not None:
                        await self._meter_failed(turn, mark, answer_model, acc.query_id)
                        metered = True
                        yield ErrorEvent(detail=_SEARCH_FAILED, code=retrieved.error_code)
                        return
                    moved = clock.advance(STAGE_CHECK)
                    if moved is not None:
                        yield StageEvent(*moved)
                gate = retrieved.gate if retrieved is not None else None
                decision = _loop_decision(  # TV1 human
                    _learning_decision(  # BIZ4 learning
                        _paused_decision(  # BIL4 paused
                            decide_route(
                                label=label,
                                gate=gate,
                                config=config,
                                state=state,
                                has_handoff=has_handoff,
                            ),
                            turn,
                            label,
                            has_handoff=has_handoff,
                        ),
                        turn,
                        label,
                        has_handoff=has_handoff,
                    ),
                    turn,
                    has_handoff=has_handoff,
                )
                acc.route = decision.route
                if decision.strike == "off_topic":
                    state.off_topic_strikes += 1
                elif decision.strike == "abuse":
                    state.abuse_strikes += 1
                if decision.stop:
                    state.stopped = True
                notice = _notice(prep)
                # On a grounded turn the notice rides inside the first token, after the
                # sources frame; every other route speaks it up front.
                in_answer = decision.route == Route.GROUNDED and retrieved is not None
                if notice and not in_answer:
                    acc.parts.append(notice)
                    yield TokenEvent(content=notice)
                spoke = time.monotonic()
                spec, prep.speculation = prep.speculation, None
                if spec is not None and not (
                    in_answer and self._adoptable(spec, prep, decision, turn, label, retrieved)
                ):
                    await spec.discard()
                    spec = None
                speech = (
                    self._adopt(spec, acc, clock)
                    if spec is not None
                    else self._speak(
                        decision,
                        turn,
                        acc,
                        label,
                        question,
                        lang,
                        office,
                        retrieved,
                        prep,
                        clock,
                        notice if in_answer else "",
                    )
                )
                async for event in speech:
                    yield event
                if in_answer and notice and not acc.sources_sent:
                    # Nothing was released (refused): the notice still goes out, first.
                    acc.parts.insert(0, notice)
                    yield TokenEvent(content=notice)
                if "write" not in acc.timings:
                    acc.timings["write"] = int((time.monotonic() - spoke) * 1000)

            forced: OfferKind | None = decision.forced_offer
            if acc.route == Route.NO_ANSWER:
                async for event in self._no_answer(turn, acc, question, lang):
                    yield event
                forced = acc.forced

            blocked_action = None
            if decision.reason == "blocked_topic":
                blocked_action = next(
                    (b.action for b in config.scope.blocked_topics if b.id == decision.topic_id),
                    None,
                )
            signals = TurnSignals(
                label=label.label,
                label_confidence=label.confidence,
                topic_id=decision.topic_id or label.topic_id,
                frustrated=label.frustrated,
                needs_human=label.needs_human,
                route=acc.route,
                gate_passed=gate.passed if gate else None,
                top_score=gate.top_score if gate else None,
                no_answer=acc.route == Route.NO_ANSWER,
                office=office,
                page_url=prep.page_url,
                language=lang,
                message_count=state.message_count + 1,
                keyword_hits=[
                    k for k in config.handoff.triggers.keywords if k.lower() in question.lower()
                ],
                text=question,
                abuse_level=prep.abuse,
                blocked_topic_id=decision.topic_id if blocked_action else None,
                blocked_action=blocked_action,
                asked_for_contact=rule_labeler().asked_for_contact(question),
                is_preview=turn.is_preview,
                stopped=stopped_before,  # TV1 human
                now=turn.now,
            )
            ctx = OfferContext(
                config=config,
                signals=signals,
                state=state,
                language=lang,
                corpus_id=turn.corpus_id,
                conversation_id=turn.conversation_id,
                channel=turn.channel,
                answer_text=acc.text(),
                cited_sources=acc.sources,
                llm=None if turn.model_free else self._deps.llm,  # BIL4/BIZ4: no model
                probe=None if turn.model_free else self._probe(prep),
                question=question,
            )
            answer_calls = _ledger_mark(self._deps.llm) - mark  # before the follow-up call
            # TV2 reference: a conversation that can be offered a person gets its reference now
            # (once; its own short transaction), so the card can carry it and the WhatsApp text.
            if _card_can_be_offered(self._registry, ctx, forced, ui_ok=acc.route in _UI_ROUTES):
                from callobuzz_rag.conversation.handoff.reference import ref_prefix, reserve_ref

                assert turn.conversation_id is not None
                ref = await reserve_ref(
                    self._deps.session_factory,
                    turn.conversation_id,
                    ref_prefix(config.identity.business_name or ""),
                )
                if ref:
                    ctx = replace(ctx, ref_code=ref)
            # /TV2 reference
            offers = await self._registry.collect(
                ctx, ui_allowed=acc.route in _UI_ROUTES and acc.route != Route.LEAD, forced=forced
            )
            for paragraph in offers.paragraphs:
                piece = "\n\n" + paragraph
                acc.parts.append(piece)
                yield TokenEvent(content=piece)
            if forced is not None and offers.ui is None and not offers.paragraphs:
                contact = st.contact_line(config, lang)
                if not contact and decision.reason in _ALWAYS_A_PATH and acc.route == Route.HANDOFF:
                    # TV1 human: never leave a request for a person with nothing to act on
                    from callobuzz_rag.conversation.handoff.provider import no_channel_text

                    said = no_channel_text(config, lang)
                    if said not in acc.text():  # said once, not after the opener too
                        contact = said
                line = "\n\n" + contact
                if line.strip():
                    acc.parts.append(line)
                    yield TokenEvent(content=line)
            quick_replies = pre_replies + list(offers.quick_replies)
            if quick_replies:
                yield QuickRepliesEvent(items=quick_replies[:4])
            if offers.ui is not None:
                yield UiEvent(block=offers.ui)
            state.after_turn(
                label=label.label,
                route=acc.route,
                gate_passed=gate.passed if gate else None,
                was_stopped=stopped_before,  # TV1 human
                # TV2 reference: a bare "talk to a person" tells the person nothing
                question=None if is_human_request(question) else question,
            )
            if acc.route is Route.HANDOFF or decision.reason == "loop":  # TV1 human
                reset_loop(state)  # a card was shown: the repeat count starts again
            else:
                note_reply(state, acc.route, acc.text())

            trace = (
                self._trace(
                    label,
                    prep.info,
                    prep.masked.kinds,
                    prep.injection,
                    decision,
                    office,
                    state,
                    retrieved,
                    acc,
                    offers.reasons,
                    lang,
                )
                if turn.debug
                else None
            )
            if trace is not None:
                yield TraceEvent(trace=trace)
            latency = clock.elapsed_ms()
            sources = acc.sources if acc.sources_sent else []
            spent = _spent_since(self._deps.llm, mark)
            usage: dict[str, Any] = {
                "prompt_tokens": acc.prompt_tokens,
                "completion_tokens": acc.completion_tokens,
                "total_tokens": acc.prompt_tokens + acc.completion_tokens,
            }
            if spent is not None:
                usage.update(spent.as_dict())
            usage["model_calls"] = spent.calls if spent is not None else 0
            usage["answer_calls"] = answer_calls  # the calls that answered, not the follow-up
            threshold = gate.threshold if gate is not None else None
            stored_timings: dict[str, Any] = {**acc.timings, "total": latency}
            if threshold is not None:
                stored_timings["gate_threshold"] = float(threshold)
            rec = TurnRecord(
                turn=turn,
                query_id=acc.query_id,
                masked=question,
                label=label,
                language=lang,
                script=script.value,
                route=acc.route,
                topic_id=signals.topic_id,
                gate=gate,
                answer=acc.text(),
                sources=sources,
                claims_removed=acc.removed,
                claims_flagged=acc.flagged,
                rules_fired=acc.rules_fired,
                offers=[
                    f"cta:{offers.cta.type}"
                    if k == OfferKind.CTA.value and offers.cta is not None
                    else k
                    for k, _ in offers.reasons
                ],
                latency_ms=latency,
                timings=stored_timings,
                model=acc.model,
                usage=usage,
                state=state,
                office=office,  # TV4 value
            )
            user_id, reply_id = (
                await shielded(persist_turn(self._deps.session_factory, rec))
                if turn.persist
                else (None, None)
            )
            if spent is not None:
                # A turn that made no model call (small talk by rule, a verbatim answer, a
                # decline) is free; one that did counts as a question and as its real cost.
                await shielded(
                    record_usage_new_session(
                        self._deps.session_factory,
                        corpus_id=turn.corpus_id,
                        tenant_id=turn.tenant_id,
                        source=turn.usage_source,
                        prompt_tokens=acc.prompt_tokens,
                        completion_tokens=acc.completion_tokens,
                        model=answer_model,
                        cost=spent,
                        payer=self._deps.payer,
                        requests=1 if answer_calls else 0,
                        request_id=acc.query_id,  # BIL3 request id
                    )
                )
            metered = True
            yield DoneEvent(
                query_id=acc.query_id,
                label=label.label,
                route=acc.route,
                language=lang,
                cta=offers.cta,
                timings=dict(acc.timings),
                claims_removed=acc.removed,
                model=acc.model,
                usage=usage,
                latency_ms=latency,
                text=acc.text(),
                message_id=reply_id,
                user_message_id=user_id,
                conversation_id=turn.conversation_id,
                sources=sources,
                quick_replies=quick_replies[:4],
                ui=offers.ui,
                assistant_version=turn.runtime.version,
                trace=trace,
                stage_timings_ms=dict(acc.stage_timings),
                retrieval_ms=retrieval_ms,
                processed_query=retrieved.processed_query if retrieved is not None else None,
                thinking_ms=acc.thinking_ms,
                reasoning_tokens=acc.reasoning_tokens or None,
                gate_threshold=float(threshold) if threshold is not None else None,
                truncated=acc.truncated,
                context_texts=[s["text"] for s in sources],
                paused=turn.pause_kind in _PAUSE_REASONS,  # BIL4 paused
                pause_reason=(  # PIP3 gate; a demo limit is told to callers as `fair_use`
                    ("fair_use" if turn.pause_kind == "demo_limit" else turn.pause_kind)
                    if turn.pause_kind in _PAUSE_REASONS
                    else None
                ),
            )
        except Exception as exc:
            if not metered:
                await self._meter_failed(turn, mark, answer_model, acc.query_id)
            # L5a demo: a demo limit that surfaces mid-turn (a race at the last answer, the
            # model-call backstop, the counters down) is the polite sentence, never "failed"
            from callobuzz_rag.demo.limits import error_code, polite_text

            polite = polite_text(exc)
            if polite is not None:
                logger.info("demo: turn stopped by a demo limit (%s)", type(exc).__name__)
                yield ErrorEvent(detail=polite, code=error_code(exc))
            else:
                logger.exception("Assistant turn failed for corpus %s", turn.corpus_id)
                yield ErrorEvent(detail=_ANSWER_FAILED)
        finally:
            await hold.release()  # PIP1 lane
            if prep is not None and prep.speculation is not None:
                await prep.speculation.discard()
            if prep is not None and prep.task is not None:
                await cancel_retrieval(prep.task)

    async def _speak(
        self,
        decision: RouteDecision,
        turn: TurnInput,
        acc: Acc,
        label: "LabelResult",
        question: str,
        lang: str,
        office: OfficeState,
        retrieved: Retrieved | None,
        prep: _Prepared,
        clock: StageClock,
        prefix: str = "",
    ) -> AsyncIterator[DirectorEvent]:
        config = turn.runtime.config

        def say(text: str) -> TokenEvent:
            acc.parts.append(text)
            return TokenEvent(content=text)

        route = decision.route
        if route == Route.SMALLTALK:
            if label.intent:
                yield say(st.instant_reply(label.intent, config, lang))
            else:
                text, _ = await st.model_smalltalk(
                    self._deps.llm, config, question, lang, turn.history
                )
                yield say(text)
        elif route == Route.VERBATIM:
            if decision.reason == "verbatim":
                topic = next(t for t in config.scope.topics if t.id == decision.topic_id)
                yield say(st.localized(topic.verbatim_answer, lang))
            else:
                messages = config.handoff.messages
                opener = st.localized(
                    messages.closed if office == OfficeState.CLOSED else messages.open, lang, ""
                )
                contact = st.contact_line(config, lang)
                if not contact:  # TV1 human: nothing on the card, so say so
                    from callobuzz_rag.conversation.handoff.provider import no_channel_text

                    yield say(no_channel_text(config, lang))
                else:
                    yield say(f"{opener} {contact}".strip())
        elif route == Route.DECLINE:
            if decision.reason in ("stopped", "strikes"):
                yield say(st.stopped_reply(config, lang))
            elif decision.reason == "blocked_topic":
                blocked = next(
                    (b for b in config.scope.blocked_topics if b.id == decision.topic_id), None
                )
                custom = st.localized(blocked.message, lang, "") if blocked else ""
                yield say(custom or st.decline_reply(config, lang))
            else:
                yield say(st.decline_reply(config, lang))
        elif route == Route.ABUSE_WARNING:
            text = st.abuse_warning(lang)
            if turn.state.abuse_strikes >= 2:
                text += " " + st.contact_line(config, lang)
            yield say(text.strip())
        elif route == Route.HANDOFF:
            moved = clock.advance(STAGE_HANDOFF)
            if moved is not None:
                yield StageEvent(*moved)
            messages = config.handoff.messages
            if decision.reason == "busy":  # PIP1 lane
                busy_text = st.localized(messages.busy, lang, DEFAULT_BUSY["en"])
                if decision.forced_offer is None:  # no card to show: the contact line instead
                    busy_text = f"{busy_text} {st.contact_line(config, lang)}".strip()
                yield say(busy_text)
                # /PIP1 lane
            elif decision.reason == "demo_limit":  # L5a demo
                from callobuzz_rag.demo.limits import current_scope, notice_for_turn

                yield say(notice_for_turn(current_scope()))
            elif decision.reason == "billing_paused":  # BIL4 paused
                paused_text = st.localized(messages.paused, lang, DEFAULT_PAUSED["en"])
                if decision.forced_offer is None:  # no card to show: the contact line instead
                    paused_text = f"{paused_text} {st.contact_line(config, lang)}".strip()
                yield say(paused_text)
            elif decision.reason == "still_learning":  # BIZ4 learning
                from callobuzz_rag.conversation.learning import LEARNING_TEXT, learning_text

                learning_reply = learning_text(LEARNING_TEXT, lang, config.identity.business_name)
                if decision.forced_offer is None:  # no card to show: the contact line instead
                    learning_reply = f"{learning_reply} {st.contact_line(config, lang)}".strip()
                yield say(learning_reply)
                # /BIZ4 learning
            else:
                opener = st.localized(
                    messages.closed if office == OfficeState.CLOSED else messages.open,
                    lang,
                    "You can reach our team here.",
                )
                if decision.reason in _ALWAYS_A_PATH:  # TV1 human
                    from callobuzz_rag.conversation.handoff.provider import (
                        closed_opener,
                        has_any_path,
                        no_channel_text,
                    )

                    if not has_any_path(config, office, lang, question):
                        opener = no_channel_text(config, lang)
                    else:
                        opener = closed_opener(config, office, lang, question) or opener
                yield say(opener)
        elif route == Route.GROUNDED and retrieved is not None:
            async for event in grounded(
                self._answer_deps(lang),
                config=config,
                compile_turn=prep.compile_turn(label.label, Route.GROUNDED, decision.topic_id),
                retrieved=retrieved,
                question=question,
                history=turn.history,
                acc=acc,
                clock=clock,
                prefix=prefix,
            ):
                yield event
        elif route == Route.EXPLAINER:
            async for event in explainer(
                self._answer_deps(lang),
                config=config,
                compile_turn=prep.compile_turn(label.label, Route.EXPLAINER, decision.topic_id),
                question=question,
                history=turn.history,
                acc=acc,
                clock=clock,
            ):
                yield event
        # Route.NO_ANSWER is spoken by _no_answer (also reached when gate 2 removed everything).

    async def _no_answer(
        self, turn: TurnInput, acc: Acc, question: str, lang: str
    ) -> AsyncIterator[DirectorEvent]:
        config = turn.runtime.config
        state = turn.state
        acc.route = Route.NO_ANSWER
        acc.sources_sent = False
        text = st.no_answer_text(config, lang)
        acc.parts.append(text)
        yield TokenEvent(content=text)
        acc.forced = None
        for action in config.scope.fallback.actions:
            if action == "clarify" and not state.clarify_used:
                q, response = await clarify_question(self._deps.llm, config, question, lang)
                state.clarify_used = True
                if q is not None and response is not None:
                    acc.parts.append("\n\n" + q)
                    yield TokenEvent(content="\n\n" + q)
                    return
            elif (
                action == "handoff"
                and config.handoff.enabled
                and self._registry.has(OfferKind.HANDOFF_CARD)
            ):
                acc.forced = OfferKind.HANDOFF_CARD
                return
            elif (
                action == "lead"
                and config.leads.enabled
                and self._registry.has(OfferKind.LEAD_FORM)
            ):
                acc.forced = OfferKind.LEAD_FORM
                return
        line = st.contact_line(config, lang)
        if line:
            acc.parts.append("\n\n" + line)
            yield TokenEvent(content="\n\n" + line)

    def _trace(
        self,
        label: "LabelResult",
        info: LanguageInfo,
        pii_kinds: list[str],
        injection: list[str],
        decision: RouteDecision,
        office: OfficeState,
        state: ConversationState,
        retrieved: Retrieved | None,
        acc: Acc,
        offer_reasons: list[tuple[str, str]],
        lang: str,
    ) -> dict[str, Any]:
        gate = retrieved.gate if retrieved is not None else None
        return {
            "label": {
                "value": label.label.value,
                "source": label.source,
                "confidence": label.confidence,
                "ms": label.ms,
            },
            "language": {
                "code": info.code,
                "script": info.script.value,
                "confidence": info.confidence,
            },
            "pii": pii_kinds,
            "safety": {
                "injection": injection,
                "abuse": 1 if label.label == TurnLabel.ABUSE else 0,
            },
            "policy": {
                "route": acc.route.value,
                "reason": decision.reason,
                "office": office.value,
                "offers": [{"kind": k, "reason": r} for k, r in offer_reasons],
                "state": state.to_dict(),
            },
            "retrieval": None
            if retrieved is None
            else {
                "queries": retrieved.queries,
                "chunks": [
                    {
                        k: s[k]
                        for k in ("chunk_id", "file_name", "section", "score", "score_breakdown")
                    }
                    for s in retrieved.sources
                ],
                "top_score": gate.top_score if gate else None,
                "threshold": gate.threshold if gate else None,
                "threshold_source": gate.source if gate else "none",
                "gate_passed": gate.passed if gate else None,
            },
            "model": {
                "name": acc.model,
                "language_routing": (
                    routing_note(lang, acc.model)
                    if acc.model is not None and acc.model == self._deps.language_models.get(lang)
                    else None
                ),
            },
            "rules_fired": acc.rules_fired,
            "prompt": acc.prompt,
            "claims": [
                {
                    "sentence": v.sentence,
                    "action": v.action,
                    "claims": [
                        {"kind": c.kind.value, "value": c.value, "supported": ok}
                        for c, ok in v.claims
                    ],
                }
                for v in acc.verdicts
            ],
            "timings": dict(acc.timings),
            "stage_timings_ms": dict(acc.stage_timings),
        }
