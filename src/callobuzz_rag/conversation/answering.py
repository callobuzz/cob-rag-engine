"""The parts of a turn that read documents or ask the answer model to write prose.

Speculative retrieval runs on a session of its own so it can be cancelled when the turn
turns out to need no documents (small talk, declines): a cancelled query's connection is
invalidated, never returned to the pool half-used (see `api/streaming.py:shielded`).

Everything here goes through ONE `LLMProvider` (`AnswerDeps.llm`), the same instance the
retrieval orchestrator was built with, so its cost ledger holds the whole turn: the label,
the translated query, query understanding, the answer, a clarifying question.
"""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Literal

from callobuzz_rag.analytics.usage import estimate_tokens
from callobuzz_rag.api.stage_events import STAGE_WRITE, StageClock, public_timings, stop_search
from callobuzz_rag.api.streaming import shielded
from callobuzz_rag.conversation import smalltalk as st
from callobuzz_rag.conversation.business_card import card_record
from callobuzz_rag.conversation.claims import (
    EXPLAINER_KINDS,
    ClaimIndex,
    SentenceGate,
    SentenceVerdict,
)
from callobuzz_rag.conversation.compiler import CompiledPrompt, CompileTurn, compile_prompt
from callobuzz_rag.conversation.envelope import (
    DirectorEvent,
    SourcesEvent,
    StageEvent,
    TokenEvent,
    source_of,
)
from callobuzz_rag.conversation.gates import GateResult, retrieval_gate, threshold_for
from callobuzz_rag.conversation.offers import OfferKind
from callobuzz_rag.conversation.rules import normalize_message
from callobuzz_rag.conversation.safety import EchoFilter
from callobuzz_rag.conversation.schema import _NO_ANSWER as DEFAULT_NO_ANSWER
from callobuzz_rag.conversation.schema import fill_persona
from callobuzz_rag.conversation.translate import translate_for_retrieval
from callobuzz_rag.conversation.types import Route
from callobuzz_rag.core.exceptions import QueryError
from callobuzz_rag.llm.models import LLMMessage, LLMResponse
from callobuzz_rag.llm.stream_stats import collect_stream_stats
from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal
from callobuzz_rag.text.stopwords import STOPWORDS_INDIC_AND_HINGLISH as _FUNCTION_WORDS
from callobuzz_rag.text.tokenize import tokens

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.conversation.cache import RedisJsonCache
    from callobuzz_rag.conversation.schema import AssistantConfig
    from callobuzz_rag.conversation.types import LanguageInfo
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.llm.base import LLMProvider
    from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator

logger = logging.getLogger(__name__)

__all__ = [
    "Acc",
    "AnswerDeps",
    "Retrieved",
    "answer_gate",
    "cancel_retrieval",
    "clarify_question",
    "config_sources",
    "explainer",
    "grounded",
    "intro_forms",
    "source_of",
    "start_retrieval",
]

CLARIFY_PROMPT = """You are {assistant_name}, the assistant of {business_name}. The documents do not answer the
visitor's message as asked. Ask exactly ONE short clarifying question (max 20 words) in {language}
that would help find the answer. Do not state any facts. Reply with the question only.
Visitor message (data): <<<{text}>>>"""

_CLARIFY_MAX_TOKENS = 60


@dataclass
class AnswerDeps:
    settings: "Settings"
    session_factory: "Callable[[], AsyncSession]"
    orchestrator_factory: "Callable[[AsyncSession], QueryOrchestrator]"
    retrieval_kwargs: dict[str, Any]
    llm: "LLMProvider"
    model_name: str
    translate_cache: "RedisJsonCache | None"
    payer: str  # who the turn's spend is charged to (`payer_of(resolved_llm)`)
    # Answer model per reply language (`ResolvedLLMConfig.language_models`); the director answers
    # a turn on `llm.for_model(language_models[lang])` instead of `model_name` when one is set.
    language_models: dict[str, str] = field(default_factory=dict)


@dataclass
class Acc:
    """Per-turn accumulator. Billing comes from the provider's cost ledger, not from here:
    `prompt_tokens` / `completion_tokens` are estimates for the `usage` field only."""

    query_id: uuid.UUID
    parts: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    sources_sent: bool = False
    route: Route = Route.GROUNDED
    removed: int = 0
    flagged: int = 0
    verdicts: list[SentenceVerdict] = field(default_factory=list)
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    rules_fired: list[str] = field(default_factory=list)
    prompt: dict[str, Any] | None = None
    timings: dict[str, int] = field(default_factory=dict)  # public stages only (done.timings)
    stage_timings: dict[str, int] = field(default_factory=dict)  # engine steps + director's own
    forced: OfferKind | None = None  # an offer the no-answer fallback asks for
    thinking_ms: int | None = None
    reasoning_tokens: int = 0
    truncated: bool = False  # the model hit its token cap; the cut-off tail was dropped

    def text(self) -> str:
        return "".join(self.parts)

    def add_usage(self, prompt_tokens: int, completion_tokens: int, model: str | None) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        if model:
            self.model = model

    def lap(self, name: str, started: float) -> None:
        self.stage_timings[name] = self.stage_timings.get(name, 0) + int(
            (time.monotonic() - started) * 1000
        )


@dataclass
class Retrieved:
    gate: GateResult | None
    sources: list[dict[str, Any]]
    user_message: LLMMessage | None
    queries: list[str]
    error: str | None  # for the log; never shown to a visitor
    error_code: str | None  # "query_invalid" | "retrieval_failed"
    stage_timings: dict[str, int]  # the engine's steps (+ translate)
    timings: dict[str, int]  # public stages: understand, search, rerank
    processed_query: str | None = None


def _same(a: str, b: str) -> bool:
    return re.sub(r"\W+", " ", a).strip().lower() == re.sub(r"\W+", " ", b).strip().lower()


def start_retrieval(
    deps: AnswerDeps,
    *,
    corpus_id: uuid.UUID,
    text: str,
    info: "LanguageInfo",
    config: "AssistantConfig",
    compile_turn: CompileTurn,
    applied_embedding: dict[str, Any] | None,
    on_stage: Callable[[str], None] | None = None,
    retrieval_overrides: dict[str, Any] | None = None,
    translate: bool = True,
) -> "asyncio.Task[Retrieved]":
    """Search the knowledge base for `text` (plus a translated variant for Hindi/Hinglish).

    `retrieval_overrides` replaces entries of the knowledge base's retrieval settings and
    `translate=False` skips the translation call; a probe uses both to stay LLM-free.

    `on_stage` is the pipeline's stage callback, so the caller's clock keeps streaming
    `understand -> search -> rerank` while the turn runs.
    """

    async def run() -> Retrieved:
        stage: dict[str, int] = {}
        extra: list[str] = []
        translate_ms = 0
        if (
            translate
            and config.language.translate_queries
            and info.code != config.language.documents_language
            and deps.llm.has_credentials
        ):
            started = time.monotonic()
            translated = await translate_for_retrieval(
                deps.llm,
                text,
                info,
                config.language.documents_language,
                cache=deps.translate_cache,
                timeout_s=deps.settings.assistant_translate_timeout_ms / 1000,
            )
            translate_ms = int((time.monotonic() - started) * 1000)
            stage["translate"] = translate_ms
            if translated and not _same(translated, text):
                extra.append(translated)
        prelim = compile_prompt(config, compile_turn)
        session = deps.session_factory()
        try:
            orchestrator = deps.orchestrator_factory(session)
            pipeline = await orchestrator.run_retrieval_pipeline(
                text=text,
                corpus_id=corpus_id,
                system_prompt=prelim.system_prompt,
                constraints=prelim.constraints,
                response_language=None,
                extra_queries=extra or None,
                on_stage=on_stage,
                updated_tz=config.identity.card.timezone,  # TV5 freshness
                **{**deps.retrieval_kwargs, **(retrieval_overrides or {})},
            )
        except asyncio.CancelledError:
            await shielded(session.invalidate())
            raise
        except Exception as exc:
            code = "query_invalid" if isinstance(exc, QueryError) else "retrieval_failed"
            logger.warning("Assistant retrieval failed for corpus %s: %s", corpus_id, exc)
            await shielded(session.close())
            return Retrieved(None, [], None, [text, *extra], str(exc), code, stage, {})
        await shielded(session.close())
        stage.update(pipeline.stage_timings_ms)
        public = public_timings(pipeline.stage_timings_ms)
        if "translate" in stage:
            public["understand"] = public.get("understand", 0) + translate_ms
        cited = pipeline.assembled.cited_chunks
        sources = [source_of(cc, pipeline.score_breakdown.get(str(cc.chunk_id))) for cc in cited]
        cited_ids = {s["chunk_id"] for s in sources}
        threshold, source = threshold_for(config, applied_embedding, deps.settings)
        gate = retrieval_gate(
            score_breakdown={k: v for k, v in pipeline.score_breakdown.items() if k in cited_ids},
            chunks=len(cited),
            threshold=threshold,
            source=source,
        )
        messages = pipeline.assembled.messages
        return Retrieved(
            gate,
            sources,
            messages[-1] if messages else None,
            [text, *extra],
            None,
            None,
            stage,
            public,
            pipeline.processed_query,
        )

    return asyncio.create_task(run())


async def cancel_retrieval(task: "asyncio.Task[Retrieved]") -> None:
    """Stop a retrieval nobody needs and wait until it has fully unwound (it may be mid-query
    on its session, which must not go back to the pool until it has)."""
    await stop_search(task)


def _names(config: "AssistantConfig") -> frozenset[str]:
    return frozenset(
        n.lower() for n in (config.identity.business_name, config.identity.assistant_name) if n
    )


def config_sources(config: "AssistantConfig", lang: str) -> list[str]:
    """What the assistant's configuration states, which the fact check counts as a source:
    the business card, the assistant and business names, the disclosure line it is told to
    say and the topics it helps with."""
    ident = config.identity
    texts = [ident.business_name or "", ident.assistant_name]
    if ident.ai_disclosure:
        texts.append(fill_persona(st.localized(ident.ai_disclosure_text, lang, ""), config))
    texts += [t.name for t in config.scope.topics]
    texts += [t.description for t in config.scope.topics if t.description]
    card = card_record(config)
    if card is not None:
        texts.append(card.text)
    return [t for t in texts if t]


def source_passages(sources: list[dict[str, Any]]) -> list[str]:
    """What the fact gate may treat as supported anywhere in the answer: each source's text."""
    return [s["text"] for s in sources]


def source_updates(sources: list[dict[str, Any]]) -> list[str]:  # TV5 freshness
    """Each source's update date written the ways an answer says it (the prompt carries
    `updated: YYYY-MM-DD`, which the claim reader does not parse). These support a date only in
    a sentence about updating (`SentenceGate.update_index`), so "last updated on 3 October 2026"
    is supported and a date the model made up from the metadata is not."""
    from datetime import date

    passages: list[str] = []
    for source in sources:
        raw = source.get("updated_at")
        if not raw:
            continue
        try:
            d = date.fromisoformat(str(raw))
        except ValueError:
            continue
        passages.append(f"updated {d.day} {d:%B} {d.year} ({d:%d/%m/%Y})")
    return passages


def answer_gate(
    config: "AssistantConfig",
    compiled: CompiledPrompt,
    passages: list[str],
    question: str,
    lang: str,
    updates: list[str] | tuple[str, ...] = (),  # TV5 freshness
) -> SentenceGate:
    """The grounded answer's fact gate (design §5.2). A value is supported only by the
    passages retrieved for this answer or by the assistant's configuration
    (`config_sources`); the visitor's question supports names only. The echo filter lets
    through what the prompt tells the assistant to say (`CompiledPrompt.speakable`)."""
    return SentenceGate(
        ClaimIndex([*passages, *config_sources(config, lang)], names_only=[question]),
        update_index=ClaimIndex(updates) if updates else None,  # TV5 freshness
        mode=config.scope.claim_check,
        echo=EchoFilter(compiled.system_prompt, allowed=compiled.speakable),
        stoplist=_names(config),
    )


def _answer_options(deps: AnswerDeps, compiled: CompiledPrompt) -> dict[str, Any]:
    """The reply length cap, only on a model known not to reason: a reasoning model spends
    the budget thinking and a cap leaves it with an empty answer. The persona's voice section
    carries the length guidance for every model."""
    if deps.llm.is_known_non_reasoning(deps.model_name):
        return {"max_tokens": compiled.max_tokens}
    return {}


_CURLY_APOSTROPHE = chr(0x2019)


def _plain(text: str) -> str:
    return normalize_message(text.replace("'", "").replace(_CURLY_APOSTROPHE, ""))


_FIRST_SENTENCE = re.compile(r"(?<=[.!?।])\s+|\n+")


def refusal_forms(config: "AssistantConfig") -> list[str]:
    """The no-answer message in every language the assistant has it in, plus the built-in
    ones, normalised for comparison with what the model wrote."""
    texts = [*config.scope.fallback.no_answer_message.values(), *DEFAULT_NO_ANSWER.values()]
    return [f for t in texts if (f := _plain(fill_persona(t, config)))]


def intro_forms(config: "AssistantConfig") -> list[str]:
    """The assistant's disclosure line ("I'm Asha, the AI assistant of ...") in every
    language it has it in, normalised like `refusal_forms`. The prompt tells the model to
    say it in its first reply, so a refusal may come after it."""
    if not config.identity.ai_disclosure:
        return []
    texts = config.identity.ai_disclosure_text.values()
    return [f for t in texts if (f := _plain(fill_persona(t, config)))]


def _is_intro(sentence: str, intros: list[str]) -> bool:
    plain = _plain(sentence)
    return bool(plain) and any(
        form in plain or SequenceMatcher(None, plain, form).ratio() >= 0.85 for form in intros
    )


def strip_intro(text: str, intros: list[str]) -> str:
    """`text` without its leading disclosure line (and a short greeting before it, such as
    "Namaste!"), so what the model goes on to say is judged on its own."""
    if not intros:
        return text
    parts = [p for p in _FIRST_SENTENCE.split(text.strip()) if p.strip()]
    i = 0
    while i < len(parts):
        if _is_intro(parts[i], intros):
            i += 1
        elif len(parts[i].split()) <= 3 and i + 1 < len(parts) and _is_intro(parts[i + 1], intros):
            i += 2
        else:
            break
    return "\n".join(parts[i:])


_FILLER = frozenset(
    "the and for you your our are was were what whats how much many does did can could will "  # noqa: SIM905
    "would should have has had with this that these those there here about any some from into "
    "tell please also aur hai hain kya kitna kitne kitni mein".split()
)


def _terms(text: str) -> set[str]:
    """The content words of a text, lower-cased and crudely stemmed."""
    found = (w for w in tokens(_plain(text), 3) if "_" not in w)
    return {w.removesuffix("s") for w in found if w not in _FILLER and w not in _FUNCTION_WORDS}


def _answers_question(continuation: str, question: str) -> bool:
    """Whether what follows a refusal sentence addresses the question: it shares at least one
    content word with it and at least half of the question's content words. Related facts
    that merely sit near the topic share too few."""
    wanted = _terms(question)
    if not wanted:
        return False
    shared = len(wanted & _terms(continuation))
    return shared >= 1 and shared * 2 >= len(wanted)


def refusal_state(
    text: str, forms: list[str], question: str = "", intros: list[str] | None = None
) -> Literal["refusal", "maybe", "no"]:
    """Whether the model's opening is its own no-answer message: "refusal" (the whole text
    reads as one), "maybe" (it could still grow into one), or "no". Small wording drift
    ("dont", a missing full stop, one changed word) still counts as the message.

    One rule for a reply that opens with the message and goes on: it is a refusal when what
    follows does not address the question (nearby facts answer a question nobody asked), and
    a real answer, keeping its sources, when the continuation does (`_answers_question`).
    Without a `question` the continuation never counts as an answer.

    The assistant's own disclosure line (`intros`, from `intro_forms`) at the start is not
    the answer: it is skipped, and a reply that so far is only that line is "maybe"."""
    if intros:
        if any(form.startswith(_plain(text)) for form in intros):
            return "maybe"
        text = strip_intro(text, intros)
    plain = _plain(text)
    if not plain:
        return "maybe"
    parts = _FIRST_SENTENCE.split(text.strip(), maxsplit=1)
    opening = _plain(parts[0])
    rest = parts[1] if len(parts) > 1 else ""
    for form in forms:
        if plain == form or SequenceMatcher(None, plain, form).ratio() >= 0.9:
            return "refusal"
        # Only a distinctive form may match on its opening alone: a one-word message such as
        # "Sorry!" also opens real answers.
        if (
            opening != plain
            and len(form.split()) >= 4
            and (opening == form or SequenceMatcher(None, opening, form).ratio() >= 0.9)
            and not _answers_question(rest, question)
        ):
            return "refusal"
    if any(form.startswith(plain) for form in forms):
        return "maybe"
    return "no"


async def grounded(
    deps: AnswerDeps,
    *,
    config: "AssistantConfig",
    compile_turn: CompileTurn,
    retrieved: Retrieved,
    question: str,
    history: list[LLMMessage],
    acc: Acc,
    clock: StageClock,
    prefix: str = "",
) -> AsyncIterator[DirectorEvent]:
    """Write the answer from the retrieved passages, releasing it sentence by sentence once
    each sentence's checkable facts are found in the sources. A generation failure raises
    (`LLMError`); the director reports it and meters the spend."""
    compiled = compile_prompt(config, compile_turn)
    acc.rules_fired = list(compiled.rules_fired)
    assert retrieved.user_message is not None
    messages = [
        LLMMessage(role="system", content=compiled.system_prompt),
        *history,
        retrieved.user_message,
    ]
    acc.prompt = {
        "system": compiled.system_prompt,
        "user": retrieved.user_message.content,
        "tokens": compiled.total_tokens,
    }
    gate = answer_gate(
        config,
        compiled,
        source_passages(retrieved.sources),
        question,
        compile_turn.language,
        updates=source_updates(retrieved.sources),  # TV5 freshness
    )
    moved = clock.advance(STAGE_WRITE)
    if moved is not None:
        yield StageEvent(*moved)
    started = time.monotonic()
    raw: list[str] = []

    refusals = refusal_forms(config)
    intros = intro_forms(config)
    # The opening of the answer waits here while it is blank or could still turn out to be
    # the model refusing in its own words ("I don't have that information..."). A refusal is
    # not an answer: it must send neither sources nor the notice, and the director speaks
    # the no-answer route instead. Anything else opens the stream, sources first.
    held: list[str] = []

    def open_stream() -> list[DirectorEvent]:
        acc.sources_sent = True
        acc.sources = retrieved.sources
        piece = prefix + "".join(held)
        held.clear()
        acc.parts.append(piece)
        return [
            SourcesEvent(sources=retrieved.sources, query_id=acc.query_id),
            TokenEvent(content=piece),
        ]

    def release(piece: str) -> list[DirectorEvent]:
        if acc.sources_sent:
            acc.parts.append(piece)
            return [TokenEvent(content=piece)]
        held.append(piece)
        text = "".join(held)
        if not text.strip() or refusal_state(text, refusals, question, intros) != "no":
            return []
        return open_stream()

    with collect_stream_stats() as stream_stats:
        async for delta in deps.llm.complete_stream(messages, **_answer_options(deps, compiled)):
            raw.append(delta)
            for piece in gate.feed(delta):
                for event in release(piece):
                    yield event
    acc.truncated = stream_stats.finish_reason == "length"
    if acc.truncated:
        logger.info("Answer hit its token cap (%s); the cut-off tail is dropped", deps.model_name)
    for piece in gate.flush(truncated=acc.truncated):
        for event in release(piece):
            yield event
    refused = False
    if held and not acc.sources_sent:
        said = "".join(held)
        # A reply that is only the disclosure line answers nothing: the no-answer route.
        if (
            refusal_state(said, refusals, question, intros) == "refusal"
            or not strip_intro(said, intros).strip()
        ):
            refused = True
        elif said.strip():
            for event in open_stream():
                yield event
    acc.timings[STAGE_WRITE] = int((time.monotonic() - started) * 1000)
    acc.thinking_ms = stream_stats.thinking_ms
    acc.reasoning_tokens = stream_stats.reasoning_tokens
    acc.add_usage(
        sum(estimate_tokens(m.content) for m in messages),
        estimate_tokens("".join(raw)),
        deps.model_name,
    )
    acc.removed, acc.flagged, acc.verdicts = gate.removed, gate.flagged, gate.verdicts
    if refused or not gate.released.strip():
        acc.route = Route.NO_ANSWER
        return
    acc.route = Route.GROUNDED
    if gate.removed:
        tail = "\n\n" + st.softener(config, compile_turn.language)
        acc.parts.append(tail)
        yield TokenEvent(content=tail)


async def explainer(
    deps: AnswerDeps,
    *,
    config: "AssistantConfig",
    compile_turn: CompileTurn,
    question: str,
    history: list[LLMMessage],
    acc: Acc,
    clock: StageClock,
) -> AsyncIterator[DirectorEvent]:
    """General information about the business's field: no documents, so no business
    specifics may appear (the fact check runs against an empty source set)."""
    compiled = compile_prompt(config, compile_turn)
    acc.rules_fired = list(compiled.rules_fired)
    messages = [
        LLMMessage(role="system", content=compiled.system_prompt),
        *history,
        LLMMessage(role="user", content=question),
    ]
    acc.prompt = {
        "system": compiled.system_prompt,
        "user": question,
        "tokens": compiled.total_tokens,
    }
    gate = SentenceGate(
        ClaimIndex([]),
        mode="enforce",
        echo=EchoFilter(compiled.system_prompt, allowed=compiled.speakable),
        stoplist=_names(config),
        kinds=EXPLAINER_KINDS,
    )
    lang = compile_turn.language
    moved = clock.advance(STAGE_WRITE)
    if moved is not None:
        yield StageEvent(*moved)
    prefix = st.explainer_prefix(lang)
    acc.parts.append(prefix)
    yield TokenEvent(content=prefix)
    started = time.monotonic()
    raw: list[str] = []
    with collect_stream_stats() as stream_stats:
        async for delta in deps.llm.complete_stream(messages, **_answer_options(deps, compiled)):
            raw.append(delta)
            for piece in gate.feed(delta):
                acc.parts.append(piece)
                yield TokenEvent(content=piece)
    acc.truncated = stream_stats.finish_reason == "length"
    for piece in gate.flush(truncated=acc.truncated):
        acc.parts.append(piece)
        yield TokenEvent(content=piece)
    acc.timings[STAGE_WRITE] = int((time.monotonic() - started) * 1000)
    acc.thinking_ms = stream_stats.thinking_ms
    acc.reasoning_tokens = stream_stats.reasoning_tokens
    acc.add_usage(
        sum(estimate_tokens(m.content) for m in messages),
        estimate_tokens("".join(raw)),
        deps.model_name,
    )
    acc.removed, acc.verdicts = gate.removed, gate.verdicts
    acc.route = Route.EXPLAINER
    if config.scope.sensitive_profile != "none":
        disclaimer = st.localized(config.scope.disclaimers, lang, "")
        if disclaimer:
            tail = "\n\n" + disclaimer
            acc.parts.append(tail)
            yield TokenEvent(content=tail)


async def clarify_question(
    llm: "LLMProvider", config: "AssistantConfig", question: str, lang: str
) -> tuple[str | None, LLMResponse | None]:
    """One short clarifying question when the documents do not answer, or None."""
    if not llm.has_credentials:
        return None, None
    prompt = CLARIFY_PROMPT.format(
        assistant_name=config.identity.assistant_name,
        business_name=config.identity.business_name or "this business",
        language={"hi": "Hindi (Devanagari)", "hi-Latn": "Hinglish (Roman Hindi)"}.get(
            lang, "English"
        ),
        text=sanitize_for_prompt_literal(question)[:500],
    )
    # The fast model's own options: a reasoning model keeps its `reasoning_effort` and is
    # not capped (the cap would be spent thinking); only a model known not to reason is.
    options = dict(llm.fast_call_options())
    if "max_tokens" in options:
        options["max_tokens"] = min(int(options["max_tokens"]), _CLARIFY_MAX_TOKENS)
    try:
        response = await llm.complete([LLMMessage(role="user", content=prompt)], **options)
    except Exception:
        logger.info("Clarifying question failed; skipping it", exc_info=True)
        return None, None
    lines = response.content.strip().splitlines()
    text = lines[0].strip() if lines else ""
    return (text or None), response
