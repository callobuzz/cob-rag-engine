"""COB's brain: who is asking and how a turn is answered.

`caller_from_request` turns the middleware's request state into a `CobCaller` (role, workspace,
the caller's own Authorization header). `CobOrchestrator` runs one turn in two steps so the
route can still answer with a plain HTTP error: `prepare` (everything that can be a 404 or a
decision: the conversation, the monthly question cap, which key pays) and `stream` (the SSE).

Answers reuse the conversation director over COB's system knowledge base. The retrieval filter
is set here from the caller's role and nothing in a request can widen it. COB re-shapes three of
the director's events (`meta`, `sources`, `done`) and adds `ui` blocks; the rest pass through
`conversation/envelope.py` unchanged.
"""

import contextlib
import dataclasses
import logging
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from callobuzz_rag.analytics.usage import SOURCE_COB, SOURCE_COB_PUBLIC  # COB6 public
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.cob import (
    CobContext,
    CobLinkBlock,  # COB4 router
    CobSearchResultsBlock,
    CobSource,
)
from callobuzz_rag.api.streaming import shielded, sse_event
from callobuzz_rag.cob import history as cob_history
from callobuzz_rag.cob.audience import admin_only_hit, admin_outranks_customer, mentions_admin_topic
from callobuzz_rag.cob.config import cob_config_for, cob_runtime_for, cob_text
from callobuzz_rag.cob.events import (
    cob_done,
    cob_error,
    cob_meta,
    cob_sources_event,
    cob_ui_event,
    deep_link,
    sources_from_director,
)
from callobuzz_rag.cob.kb import load_docs_index
from callobuzz_rag.cob.llm_choice import cob_llm_config
from callobuzz_rag.cob.prompting import role_note
from callobuzz_rag.cob.public import sign_in_hint  # COB6 public
from callobuzz_rag.cob.router import RouteDecision  # COB4 router
from callobuzz_rag.cob.search_mode import search as search_docs
from callobuzz_rag.cob.types import CobCaller, CobRole, DocPage, DocsIndex, allowed_audiences
from callobuzz_rag.conversation import surfaces
from callobuzz_rag.conversation.envelope import (
    DoneEvent,
    ErrorEvent,
    MetaEvent,
    QuickRepliesEvent,
    SourcesEvent,
    StageEvent,
    Surface,
    TokenEvent,
    to_sse,
)
from callobuzz_rag.conversation.language import detect_language
from callobuzz_rag.conversation.smalltalk import no_answer_text
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import Channel, Route

if TYPE_CHECKING:
    from fastapi import Request

    from callobuzz_rag.llm.models import LLMMessage

logger = logging.getLogger(__name__)

STATUS_ID: Final = 1
_LANGS: Final = ("en", "hi", "hi-Latn")
# Roles that may not read the administrator-only docs: they get the "managed by your
# administrator" reply when their question is about running the server.
_PROBED_ROLES: Final = frozenset({CobRole.OWNER, CobRole.MEMBER})
SYNCING: Final = (
    "COB is reading the docs for the first time. Try again in a minute.",
    "cob_docs_syncing",
)


def caller_from_request(request: "Request") -> CobCaller:
    """The caller of a COB route. Only people use COB: an API key (admin, `tk_`, `ck_`) is
    refused with 403 `session_required`, no credential at all with 401.

    Roles follow the middleware state: a super-admin session in the operator view is ADMIN; the
    super admin inside the operator workspace is OPERATOR_OWNER (reads admin docs, their tools
    act on the workspace); an owner or member session is OWNER or MEMBER."""
    state = request.state
    user = getattr(state, "user", None)
    if user is None:
        if getattr(state, "is_admin", None) is None and getattr(state, "tenant", None) is None:
            raise APIError(401, "Sign in to use COB.", "auth_required")
        raise APIError(403, "COB is for signed-in people, not for API keys.", "session_required")
    authorization = request.headers.get("authorization")
    session_id = getattr(state, "session_id", None)
    if getattr(state, "is_admin", False):
        return CobCaller(CobRole.ADMIN, user.id, None, session_id, authorization)
    tenant = getattr(state, "tenant", None)
    if tenant is None:
        raise APIError(403, "COB is for signed-in people, not for API keys.", "session_required")
    if getattr(tenant, "is_operator", False) and getattr(user, "is_superadmin", False):
        role = CobRole.OPERATOR_OWNER
    elif getattr(state, "role", None) == "owner":
        role = CobRole.OWNER
    else:
        role = CobRole.MEMBER
    return CobCaller(role, user.id, tenant.id, session_id, authorization)


@dataclass
class CobTurn:
    caller: CobCaller
    message: str
    conversation_id: uuid.UUID | None
    context: CobContext | None
    language_hint: str | None
    public: bool = False
    # Public COB only: the client's earlier turns, masked.
    history: "list[LLMMessage] | None" = None


@dataclass
class PreparedTurn:
    """Everything `stream` needs, decided while the request's own database session is open."""

    turn: CobTurn
    corpus: Any
    conversation_id: uuid.UUID
    state: ConversationState
    history: "list[LLMMessage]"
    index: DocsIndex
    page: DocPage | None
    metering_tenant_id: uuid.UUID
    llm_override: dict[str, Any] | None
    mode: str = "answer"  # "answer" | "search" | "demo_limit" (L5a demo: a fixed polite reply)
    search_reason: str | None = (
        None  # question_cap_reached | llm_unavailable | balance_paused | fair_use
    )
    resets_on: datetime | None = None
    error: tuple[str, str] | None = None  # (detail, code): the docs are not synced yet

    @property
    def visible_conversation_id(self) -> uuid.UUID | None:
        """The id a client is told. A public turn has no conversation, so none."""
        return None if self.turn.public else self.conversation_id


def build_retriever(request: "Request", session: Any, corpus: Any) -> Any:
    """A retrieval-only `QueryOrchestrator` over COB's knowledge base. It never calls a model
    unless a caller turns query understanding on, which search mode and the probe never do."""
    from callobuzz_rag.api.routes.query import _build_orchestrator, _get_config_resolver
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

    resolver = _get_config_resolver(request)
    resolved_llm = resolver.resolve_llm(corpus_llm_config=corpus.llm_config)
    resolved_prompt = resolver.resolve_prompt(
        request_body=None, corpus_prompt_config=corpus.prompt_config
    )
    resolved_retrieval = resolver.resolve_retrieval(corpus_retrieval_config=corpus.retrieval_config)
    llm = LiteLLMProvider.from_resolved_config(resolved_llm)
    return _build_orchestrator(
        request, session, corpus, resolved_llm, resolved_prompt,
        resolved_retrieval=resolved_retrieval, llm=llm,
    )  # fmt: skip


def llm_available(request: "Request", corpus: Any, override: dict[str, Any] | None) -> bool:
    """Whether some language-model key (the workspace's own, else the engine's) is set."""
    from callobuzz_rag.api.routes.query import _get_config_resolver

    try:
        resolved = _get_config_resolver(request).resolve_llm(
            corpus_llm_config=override if override is not None else corpus.llm_config
        )
    except Exception:
        logger.warning("COB could not resolve its language model", exc_info=True)
        return False
    return bool(resolved.api_key) or bool(resolved.base_url)


def _reply_language(turn: CobTurn) -> str:
    """The language COB's own sentences use: the message's (detected), the interface hint when
    the message has no letters, else English."""
    info = detect_language(turn.message)
    if info.confidence > 0 and info.code in _LANGS:
        return str(info.code)
    return turn.language_hint if turn.language_hint in _LANGS else "en"


def _reset_date(moment: datetime) -> str:
    return f"{moment.day} {moment:%b %Y}"


# COB4 router: the pieces the router and the read tools need from the request. Module-level so a
# test can replace them (no model call, no real internal client).
def build_router_llm(request: "Request", corpus: Any, override: dict[str, Any] | None) -> Any:
    """The fast-model provider the router asks, resolved like the turn's own (the workspace's
    key when it has one), so the router's cost lands on the same ledger. None if there is none."""
    from callobuzz_rag.api.routes.query import _get_config_resolver
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

    try:
        resolved = _get_config_resolver(request).resolve_llm(
            corpus_llm_config=override if override is not None else corpus.llm_config
        )
        return LiteLLMProvider.from_resolved_config(resolved)
    except Exception:
        logger.warning("COB's router could not resolve a language model", exc_info=True)
        return None


def make_internal_client(request: "Request", caller: CobCaller) -> Any:
    """The in-process client a tool reads through: the caller's own Authorization header."""
    from callobuzz_rag.cob.tools.http import InternalClient
    from callobuzz_rag.widget.security import request_client_ip

    return InternalClient(request.app, caller.authorization, client_ip=request_client_ip(request))


# end COB4 router


class CobOrchestrator:
    def __init__(self, request: "Request") -> None:
        self._request = request
        # COB4 router: the account card of a mixed turn, merged into the docs answer's message
        self._mixed: dict[str, Any] | None = None

    # ------------------------------------------------------------------ prepare

    async def prepare(self, session: Any, turn: CobTurn) -> PreparedTurn:
        """Resolve the conversation, the cap and the paying key. Raises `APIError` for what is
        a plain HTTP answer (404 `cob_disabled`, 404 `conversation_not_found`)."""
        from callobuzz_rag.accounts.caps import cap_blocks_answers, next_month_start
        from callobuzz_rag.cob.system import get_system_corpus
        from callobuzz_rag.db.models.cob_status import CobStatus
        from callobuzz_rag.fairuse import alerts  # PIP3 gate
        from callobuzz_rag.fairuse.gate import cob_blocked  # PIP3 gate

        settings = self._request.app.state.settings
        if not settings.cob_enabled:
            raise APIError(404, "COB is switched off on this engine.", "cob_disabled")
        caller = turn.caller
        index = load_docs_index()
        context = turn.context
        page = None
        if context is not None:
            page = (index.pages.get(context.docs_slug) if context.docs_slug else None) or (
                index.page_for_route(context.route)
            )
        corpus = await get_system_corpus(session)
        status = await session.get(CobStatus, STATUS_ID)
        blank = PreparedTurn(
            turn=turn,
            corpus=corpus,
            conversation_id=turn.conversation_id or uuid.uuid4(),
            state=ConversationState(),
            history=[],
            index=index,
            page=page,
            metering_tenant_id=caller.tenant_id or uuid.uuid4(),
            llm_override=None,
        )
        if corpus is None or status is None or not status.document_count:
            blank.error = SYNCING
            return blank
        if turn.public:  # COB6 public: no conversation, no cap, no workspace key
            public = self._prepare_public(blank, corpus)
            await self._demo_gate(public)  # L5a demo: a signed-out visitor is a demo visitor too
            return public
        conv = await cob_history.find_or_create(session, caller, corpus.id, turn.conversation_id)
        state: ConversationState = ConversationState()
        history: list[LLMMessage] = []
        if turn.conversation_id is not None:
            _conv, state, history = await surfaces.conversation_context(
                session, corpus.id, conv.id, settings, kind=cob_history.KIND
            )
        await session.commit()  # the director writes through sessions of its own
        override = await cob_llm_config(session, caller)
        prepared = dataclasses.replace(
            blank,
            conversation_id=conv.id,
            state=state,
            history=history,
            metering_tenant_id=caller.tenant_id or corpus.tenant_id,
            llm_override=override,
        )
        now = datetime.now(UTC)
        if caller.role != CobRole.ADMIN and caller.tenant_id is not None:
            cob_payer = "byok" if override else "operator"  # BIL4 billing
            if await cap_blocks_answers(session, caller.tenant_id, now, cob_payer):
                prepared.mode = "search"
                prepared.search_reason = "question_cap_reached"
                prepared.resets_on = next_month_start(now)
            else:  # BIL4 billing: a paused workspace gets search, like a capped one
                from callobuzz_rag.billing.enforcement import billing_state

                try:
                    cob_paused = (
                        await billing_state(session, caller.tenant_id, payer=cob_payer)
                    ).paused
                except Exception:  # a billing read never takes COB down: answer, like every surface
                    logger.warning("Billing state unavailable for COB", exc_info=True)
                    cob_paused = False
                if cob_paused:
                    prepared.mode = "search"
                    prepared.search_reason = "balance_paused"
                # PIP3 gate: the business's COB questions for today (D11: COB keeps its own model,
                # so this is the only fair-use limit that applies to it)
                elif await cob_blocked(
                    session,
                    caller.tenant_id,
                    now=now,
                    enabled=bool(
                        getattr(self._request.app.state.settings, "fair_use_enabled", True)
                    ),
                ):
                    prepared.mode = "search"
                    prepared.search_reason = "fair_use"
                # the 80 % and 100 % notices of the day's COB questions (a business without that
                # limit costs one cheap background read)
                if getattr(settings, "fair_use_enabled", True):
                    alerts.schedule_usage(
                        getattr(self._request.app.state, "session_factory", None),
                        caller.tenant_id,
                        ("cob_questions_per_day",),
                        settings,
                    )
                # /PIP3 gate
        if prepared.mode == "answer" and not llm_available(self._request, corpus, override):
            prepared.mode = "search"
            prepared.search_reason = "llm_unavailable"
        await self._demo_gate(prepared)  # L5a demo
        return prepared

    async def _demo_gate(self, prepared: PreparedTurn) -> None:
        """L5a demo: the demo's daily answer limits (every demo visitor, the shared operator
        sign-in included; only the admin key is exempt). A turn that would answer with the model
        and is over a limit becomes the fixed polite reply; the scope it opens makes the turn's
        first model call count one answer. Search mode is free, so it is left alone."""
        settings = self._request.app.state.settings
        if getattr(settings, "demo_mode", False) is not True:
            return
        from callobuzz_rag.demo import limits, runtime

        scope = await limits.check(self._request, runtime.get_redis(self._request.app), settings)
        if scope is not None and scope.standing.kind != "ok" and prepared.mode == "answer":
            prepared.mode = "demo_limit"

    # COB6 public -----------------------------------------------------------------------------

    def _prepare_public(self, blank: PreparedTurn, corpus: Any) -> PreparedTurn:
        """A visitor's turn: nothing is looked up or stored, the operator's key answers, and
        the usage is metered on the system workspace. No key at all means search mode."""
        prepared = dataclasses.replace(
            blank,
            history=list(blank.turn.history or []),
            metering_tenant_id=corpus.tenant_id,
        )
        if not llm_available(self._request, corpus, None):
            prepared.mode = "search"
            prepared.search_reason = "llm_unavailable"
        return prepared

    async def _restricted_probe(self, p: PreparedTurn, *, threshold: float | None) -> bool:
        from callobuzz_rag.cob.public import restricted_hit

        async with self._retriever(p) as retriever:
            return await restricted_hit(retriever, p.corpus.id, p.turn.message, threshold=threshold)

    # end COB6 public

    # ------------------------------------------------------------------- stream

    async def run_stream(self, turn: CobTurn) -> AsyncGenerator[str]:
        """Prepare and stream in one go, with a session of its own (for callers with no route)."""
        factory = self._request.app.state.session_factory
        async with factory() as session:
            prepared = await self.prepare(session, turn)
        async for frame in self.stream(prepared):
            yield frame

    async def stream(self, p: PreparedTurn) -> AsyncGenerator[str]:
        started = time.monotonic()
        if p.error is not None:
            yield cob_error(*p.error)
            return
        lang = _reply_language(p.turn)
        # COB4 router: a question about the person's own account is answered from the engine's
        # own routes (free); a mixed one gets the card first and then the docs answer.
        decision = await self._route(p)
        # COB5 proposals: a request to change something becomes a card the person confirms
        if decision.tool is not None and decision.intent == "action":
            async for frame in self._action_turn(p, decision, lang, started):
                yield frame
            return
        mixed = False
        if decision.tool is not None and decision.intent in ("account", "mixed"):
            async for frame in self._account_turn(p, decision, lang, started):
                yield frame
            if decision.intent == "account" or p.mode != "answer":
                return
            mixed = True
        # end COB4 router
        if p.mode == "demo_limit":  # L5a demo
            async for frame in self._demo_limit_turn(p, lang, started):
                yield frame
            return
        if p.mode == "search":
            async for frame in self._search_turn(p, lang, started):
                yield frame
            return
        role = p.turn.caller.role
        if role in _PROBED_ROLES and mentions_admin_topic(p.turn.message):
            try:
                refuse = await self._admin_outranks(p)
            except Exception:
                logger.warning("COB's administrator probe failed", exc_info=True)
                refuse = False
            if refuse:
                async for frame in self._admin_managed_turn(p, lang, started):
                    yield frame
                return
        async for frame in self._answer_turn(p, lang, mixed=mixed):
            yield frame

    # ----------------------------------------------------------------- helpers

    @contextlib.asynccontextmanager
    async def _retriever(self, p: PreparedTurn) -> AsyncIterator[Any]:
        factory = self._request.app.state.session_factory
        async with factory() as session:
            yield build_retriever(self._request, session, p.corpus)

    async def _admin_probe(self, p: PreparedTurn, *, threshold: float | None) -> bool:
        async with self._retriever(p) as retriever:
            return await admin_only_hit(retriever, p.corpus.id, p.turn.message, threshold=threshold)

    async def _admin_outranks(self, p: PreparedTurn) -> bool:
        """A question that names a server thing: refuse only when the administrator-only docs
        match it better than the pages this person may read; otherwise answer as usual (a
        question the docs cannot answer still meets the no-answer probe)."""
        async with self._retriever(p) as retriever:
            return await admin_outranks_customer(
                retriever,
                p.corpus.id,
                p.turn.message,
                customer_audiences=allowed_audiences(p.turn.caller.role),
                threshold=None,
            )

    def _data(self, p: PreparedTurn, lang: str, **extra: Any) -> dict[str, Any]:
        return {
            "intent": "docs",
            "mode": "answer",
            "role": p.turn.caller.role.value,
            "language": lang,
            "sources": [],
            "ui": [],
            "counted": False,
            **extra,
        }

    async def _own_turn(self, p: PreparedTurn, text: str, data: dict[str, Any]) -> uuid.UUID | None:
        """Store a turn COB answered itself; the id of its reply (None if it could not be
        written, and always None for a visitor: nothing of a public turn is stored)."""
        if p.turn.public:  # COB6 public
            return None
        factory = self._request.app.state.session_factory
        _user_id, reply_id = await shielded(
            cob_history.append_turn(
                factory, p.conversation_id, question=p.turn.message, answer=text, data=data
            )
        )
        return reply_id

    async def _admin_managed_turn(
        self, p: PreparedTurn, lang: str, started: float
    ) -> AsyncGenerator[str]:
        role = p.turn.caller.role
        text = cob_text("admin_managed", lang)
        query_id = uuid.uuid4()
        card = self._mixed  # COB4 router: a mixed turn already sent `meta` and its card
        if card is None:
            yield cob_meta(
                conversation_id=p.visible_conversation_id,
                query_id=query_id,
                intent="docs",
                mode="answer",
                language=lang,
                role=role,
            )
        yield sse_event("token", {"content": text})
        if card is None:
            reply_id = await self._own_turn(p, text, self._data(p, lang))
        else:
            data = self._data(p, lang, intent="mixed", **card["data"], ui=card["ui"])
            reply_id = await self._own_turn(p, f"{card['text']}\n\n{text}", data)
        yield cob_done(
            message_id=reply_id,
            conversation_id=p.visible_conversation_id,
            intent="docs" if card is None else "mixed",
            mode="answer",
            counted=False,
            label=None,
            route="admin_managed",
            language=lang,
            claims_removed=0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def _demo_limit_turn(
        self, p: PreparedTurn, lang: str, started: float
    ) -> AsyncGenerator[str]:
        """L5a demo: this visitor, or the whole demo, has used today's answers (or they cannot be
        counted). A fixed sentence: no search, no model call, nothing metered."""
        from callobuzz_rag.demo.limits import current_scope, notice_for_turn

        text = notice_for_turn(current_scope())
        if self._mixed is None:  # a mixed turn already sent `meta` with its card
            yield cob_meta(
                conversation_id=p.visible_conversation_id,
                query_id=uuid.uuid4(),
                intent="docs",
                mode="answer",
                language=lang,
                role=p.turn.caller.role,
            )
        yield sse_event("token", {"content": text})
        reply_id = await self._own_turn(p, text, self._data(p, lang))
        yield cob_done(
            message_id=reply_id,
            conversation_id=p.visible_conversation_id,
            intent="docs",
            mode="answer",
            counted=False,
            label=None,
            route="demo_limit",
            language=lang,
            claims_removed=0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def _search_turn(self, p: PreparedTurn, lang: str, started: float) -> AsyncGenerator[str]:
        role = p.turn.caller.role
        reason = p.search_reason or "question_cap_reached"
        yield cob_meta(
            conversation_id=p.visible_conversation_id,
            query_id=uuid.uuid4(),
            intent="docs",
            mode="search",
            language=lang,
            role=role,
        )
        try:
            async with self._retriever(p) as retriever:
                items = await search_docs(
                    retriever,
                    p.corpus.id,
                    p.turn.message,
                    index=p.index,
                    role=role,
                    context=p.turn.context,
                )
        except Exception:
            logger.warning("COB search mode failed", exc_info=True)
            yield cob_error(
                "COB could not search the docs right now. Please try again.", "retrieval_failed"
            )
            return
        if not items:
            text = cob_text("search_none", lang)
        elif reason == "question_cap_reached" and p.resets_on is not None:
            text = cob_text("search_cap", lang, date=_reset_date(p.resets_on))
        else:
            text = cob_text("search_unavailable", lang)
        block = CobSearchResultsBlock(reason=reason, items=items)
        yield sse_event("token", {"content": text})
        yield cob_ui_event(block)
        data = self._data(p, lang, mode="search", ui=[block.model_dump(mode="json")], counted=False)
        reply_id = await self._own_turn(p, text, data)
        yield cob_done(
            message_id=reply_id,
            conversation_id=p.visible_conversation_id,
            intent="docs",
            mode="search",
            counted=False,
            label=None,
            route="search",
            language=lang,
            claims_removed=0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def _answer_turn(
        self, p: PreparedTurn, lang: str, *, mixed: bool = False
    ) -> AsyncGenerator[str]:
        turn = p.turn
        caller = turn.caller
        role = caller.role
        director = surfaces.build_director(
            self._request,
            p.corpus,
            use_headers=False,
            options=None,
            llm_config_override=p.llm_override,
            retrieval_filters={"audience": allowed_audiences(role)},
        )
        config = cob_config_for(role)
        director_turn = dataclasses.replace(
            surfaces.make_turn(
                corpus=p.corpus,
                runtime=cob_runtime_for(role),
                text=turn.message,
                channel=Channel.COB,
                usage_source=SOURCE_COB_PUBLIC if turn.public else SOURCE_COB,  # COB6 public
                conversation_id=None if turn.public else p.conversation_id,  # COB6 public
                state=p.state,
                history=p.history,
                page_url=None,
                session_token=None,
                opts=None,
                one_off=role_note(caller, p.page),
            ),
            tenant_id=p.metering_tenant_id,
            persist=not turn.public,  # COB6 public
        )
        probe_refusals = role in _PROBED_ROLES or turn.public  # COB6 public
        expected_no_answer: str | None = None
        holding = False
        first_token = True
        held: list[str] = []
        held_quick: list[str] = []
        cited: list[CobSource] = []
        intent = "docs"
        try:
            async with contextlib.aclosing(director.run_stream(director_turn)) as events:
                async for event in events:
                    if isinstance(event, MetaEvent):
                        lang = event.language if event.language in _LANGS else lang
                        intent = "smalltalk" if event.route == Route.SMALLTALK else "docs"
                        if mixed:  # COB4 router: the card already sent `meta` (intent mixed)
                            intent = "mixed"
                        if probe_refusals:
                            expected_no_answer = no_answer_text(config, event.language)
                        if not mixed:
                            yield cob_meta(
                                conversation_id=p.visible_conversation_id,
                                query_id=event.query_id,
                                intent=intent,
                                mode="answer",
                                language=lang,
                                role=role,
                            )
                    elif isinstance(event, SourcesEvent):
                        cited = sources_from_director(
                            p.index, event.sources, role=role, context=turn.context
                        )
                        if cited:
                            yield cob_sources_event(cited)
                    elif isinstance(event, TokenEvent):
                        if first_token and expected_no_answer is not None:
                            holding = event.content == expected_no_answer
                        first_token = False
                        if holding:
                            held.append(event.content)
                        else:
                            yield sse_event("token", {"content": event.content})
                    elif isinstance(event, QuickRepliesEvent):
                        frame = to_sse(event, Surface.CONVERSATION)
                        if frame is not None and holding:
                            held_quick.append(frame)
                        elif frame is not None:
                            yield frame
                    elif isinstance(event, StageEvent):
                        stage = to_sse(event, Surface.CONVERSATION)
                        if stage is not None:
                            yield stage
                    elif isinstance(event, ErrorEvent):
                        error = to_sse(event, Surface.CONVERSATION)
                        if error is not None:
                            yield error
                        return
                    elif isinstance(event, DoneEvent):
                        async for frame in self._finish(
                            p, event, lang, intent, cited, held, held_quick, holding
                        ):
                            yield frame
        finally:
            await director.aclose()

    async def _finish(
        self,
        p: PreparedTurn,
        done: DoneEvent,
        lang: str,
        intent: str,
        cited: list[CobSource],
        held: list[str],
        held_quick: list[str],
        holding: bool,
    ) -> AsyncGenerator[str]:
        role = p.turn.caller.role
        replaced: str | None = None
        if holding:
            refuse = False
            if done.route == Route.NO_ANSWER:
                try:
                    if p.turn.public:  # COB6 public: a signed-in topic gets the sign-in hint
                        refuse = await self._restricted_probe(p, threshold=done.gate_threshold)
                    else:
                        refuse = await self._admin_probe(p, threshold=done.gate_threshold)
                except Exception:
                    logger.warning("COB's restricted-topic probe failed", exc_info=True)
            if refuse:
                replaced = sign_in_hint(lang) if p.turn.public else cob_text("admin_managed", lang)
                yield sse_event("token", {"content": replaced})
            else:
                for text in held:
                    yield sse_event("token", {"content": text})
                for frame in held_quick:
                    yield frame
        link = None
        if cited and replaced is None:
            link = deep_link(
                p.index,
                # COB6 public: a visitor is only ever sent to a public screen, never to Docs
                cited=[s.slug for s in cited if not p.turn.public or p.index.pages[s.slug].route],
                role=role,
                context=p.turn.context,
                language=lang,
            )
        counted = int(done.usage.get("answer_calls", 0) or 0) > 0
        data = self._data(
            p,
            lang,
            intent=intent,
            sources=[s.model_dump() for s in cited] if replaced is None else [],
            ui=[link.model_dump(mode="json")] if link else [],
            counted=counted,
        )
        content = replaced
        if self._mixed is not None:  # COB4 router: the card and its link come first
            card = self._mixed
            data = {**data, **card["data"], "ui": [*card["ui"], *data["ui"]]}
            content = f"{card['text']}\n\n{replaced if replaced is not None else done.text}"
        await shielded(
            cob_history.write_agent_log(
                self._request.app.state.session_factory,
                done.message_id,
                data,
                content=content,
            )
        )
        if link is not None:
            yield cob_ui_event(link)
        yield cob_done(
            message_id=done.message_id,
            conversation_id=p.visible_conversation_id,
            intent=intent,
            mode="answer",
            counted=counted,
            label=done.label.value,
            route=(
                ("restricted" if p.turn.public else "admin_managed")
                if replaced is not None
                else done.route.value
            ),
            language=lang,
            claims_removed=done.claims_removed,
            latency_ms=done.latency_ms,
        )

    # COB4 router -----------------------------------------------------------------------------

    async def _route(self, p: PreparedTurn) -> RouteDecision:
        """Docs, account, or both. Rules decide the common questions; the fast model is asked
        only for the rest, and only while answers are on (at the cap or with no key the rules
        alone decide). The router sees the person's own words and nothing else."""
        from callobuzz_rag.analytics.usage import (
            PAYER_BYOK,
            PAYER_OPERATOR,
            record_usage_new_session,
        )
        from callobuzz_rag.cob.router import CobRouter, load_rules

        caller = p.turn.caller
        if p.turn.public or caller.role == CobRole.PUBLIC:
            return RouteDecision("docs", None)
        llm = None
        if p.mode == "answer":
            llm = build_router_llm(self._request, p.corpus, p.llm_override)
        earlier = [m.content for m in p.history if m.role == "user"][-2:]
        try:
            decision = await CobRouter(llm, rules=load_rules()).decide(
                p.turn.message, earlier, caller.role, p.turn.context
            )
        except Exception:
            logger.warning("COB's router failed; answering from the docs", exc_info=True)
            return RouteDecision("docs", None)
        if decision.usage is not None:
            prompt, completion, model = decision.usage
            # The router's own tokens are spend, never a question (requests=0).
            await record_usage_new_session(
                self._request.app.state.session_factory,
                corpus_id=p.corpus.id,
                tenant_id=p.metering_tenant_id,
                source=SOURCE_COB,
                prompt_tokens=prompt,
                completion_tokens=completion,
                model=model,
                requests=0,
                payer=PAYER_BYOK if p.llm_override is not None else PAYER_OPERATOR,
            )
        return decision

    async def _account_turn(
        self, p: PreparedTurn, decision: RouteDecision, lang: str, started: float
    ) -> AsyncGenerator[str]:
        """Run one read tool and answer with its card. Free: no model writes the answer."""
        from callobuzz_rag.cob.tools import REGISTRY
        from callobuzz_rag.cob.tools.base import ToolContext, ToolResult
        from callobuzz_rag.cob.tools.cards import ts
        from callobuzz_rag.cob.tools.http import tool_client

        caller = p.turn.caller
        mixed = decision.intent == "mixed" and p.mode == "answer"
        intent = "mixed" if mixed else "account"
        tool = REGISTRY[decision.tool] if decision.tool else None
        yield cob_meta(
            conversation_id=p.visible_conversation_id,
            query_id=uuid.uuid4(),
            intent=intent,
            mode=p.mode,
            language=lang,
            role=caller.role,
        )
        yield sse_event(
            "stage", {"stage": "tool", "elapsed_ms": int((time.monotonic() - started) * 1000)}
        )
        result: ToolResult
        if decision.refusal == "owner_only":
            result = ToolResult(markdown=ts("owner_only", lang), ok=False)
        elif decision.refusal == "operator_view":
            result = ToolResult(
                markdown=ts("operator_view", lang),
                link=CobLinkBlock(label=ts("link_operator_view", lang), route="/"),
                ok=False,
            )
        elif tool is None or tool.kind != "read" or caller.role not in tool.roles:
            result = ToolResult(markdown=ts("unavailable", lang), ok=False)
        else:
            result = await self._run_tool(p, tool, decision, lang, tool_client, ToolContext)
        text = result.markdown
        yield sse_event("token", {"content": text + ("\n\n" if mixed else "")})
        ui: list[dict[str, Any]] = []
        if result.link is not None:
            ui.append(result.link.model_dump(mode="json"))
            yield cob_ui_event(result.link)
        if result.quick_replies:
            frame = to_sse(QuickRepliesEvent(items=result.quick_replies), Surface.CONVERSATION)
            if frame is not None:
                yield frame
        info = {
            "tool": decision.tool,
            "source": decision.source,
            "ok": result.ok,
        }
        if mixed:
            self._mixed = {"text": text, "ui": ui, "data": info}
            return
        data = self._data(p, lang, intent="account", mode=p.mode, ui=ui, **info)
        reply_id = await self._own_turn(p, text, data)
        yield cob_done(
            message_id=reply_id,
            conversation_id=p.visible_conversation_id,
            intent="account",
            mode=p.mode,
            counted=False,
            label=None,
            route="account",
            language=lang,
            claims_removed=0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def _run_tool(
        self,
        p: PreparedTurn,
        tool: Any,
        decision: RouteDecision,
        lang: str,
        tool_client: Any,
        tool_context: Any,
    ) -> Any:
        from callobuzz_rag.cob.tools.base import ToolResult
        from callobuzz_rag.cob.tools.cards import link_to, ts

        caller = p.turn.caller
        ctx = tool_context(
            caller=caller,
            client=tool_client(tool, make_internal_client(self._request, caller)),
            language=lang,
            page=p.turn.context,
            now=datetime.now(UTC),
            question=p.turn.message,
        )
        try:
            args = tool.args_model.model_validate(decision.args)
        except ValueError:
            args = tool.args_model()
        try:
            return await tool.run(ctx, args)
        except Exception:
            logger.warning("COB tool %s failed", tool.name, exc_info=True)
            return ToolResult(
                markdown=ts("unavailable", lang), link=link_to(tool, ctx, tool.screen), ok=False
            )

    # end COB4 router

    # COB5 proposals --------------------------------------------------------------------------

    async def _action_turn(
        self, p: PreparedTurn, decision: RouteDecision, lang: str, started: float
    ) -> AsyncGenerator[str]:
        """A request to change something: COB stores a proposal and answers with a card. Nothing
        runs until the person confirms it (`cob/proposals.py`); the model is never involved."""
        from callobuzz_rag.cob import proposals
        from callobuzz_rag.cob.router import CobRouter, load_rules
        from callobuzz_rag.cob.tools import REGISTRY
        from callobuzz_rag.cob.tools.actions.common import ActionBase, ProposalDraft
        from callobuzz_rag.cob.tools.base import ToolContext, ToolResult
        from callobuzz_rag.cob.tools.cards import ts
        from callobuzz_rag.cob.tools.http import ToolFailure, tool_client

        caller = p.turn.caller
        tool = REGISTRY.get(decision.tool or "")
        yield cob_meta(
            conversation_id=p.visible_conversation_id,
            query_id=uuid.uuid4(),
            intent="action",
            mode=p.mode,
            language=lang,
            role=caller.role,
        )
        yield sse_event(
            "stage", {"stage": "propose", "elapsed_ms": int((time.monotonic() - started) * 1000)}
        )
        result: ToolResult | None = None
        card = None
        if decision.refusal == "owner_only":
            result = ToolResult(markdown=ts("action_owner_only", lang), ok=False)
        elif decision.refusal == "operator_view":
            result = ToolResult(
                markdown=ts("operator_view", lang),
                link=CobLinkBlock(label=ts("link_operator_view", lang), route="/"),
                ok=False,
            )
        elif not isinstance(tool, ActionBase) or caller.role not in tool.roles:
            result = ToolResult(markdown=ts("unavailable", lang), ok=False)
        else:
            ctx = ToolContext(
                caller=caller,
                client=tool_client(tool, make_internal_client(self._request, caller)),
                language=lang,
                page=p.turn.context,
                now=datetime.now(UTC),
                question=p.turn.message,
            )
            try:
                args = tool.args_model.model_validate(decision.args)
            except ValueError:
                args = tool.args_model()
            try:
                proposed = await tool.propose(ctx, args)
            except ToolFailure as failure:
                key = {"owner": "action_owner_only", "forbidden": "forbidden"}.get(
                    failure.kind, "unavailable"
                )
                proposed = ToolResult(markdown=ts(key, lang), ok=False)
            except Exception:
                logger.warning("COB action %s could not be proposed", tool.name, exc_info=True)
                proposed = ToolResult(markdown=ts("unavailable", lang), ok=False)
            if isinstance(proposed, ProposalDraft):
                factory = self._request.app.state.session_factory
                async with factory() as session:
                    row = await proposals.create_proposal(
                        session, caller, p.conversation_id, tool.name, proposed, language=lang
                    )
                    await session.commit()
                    card = proposals.card_for(row, proposed, tool)
                if tool.name == "suspend_customer":
                    intro = ts("propose_intro_suspend", lang)
                else:
                    intro = ts("propose_intro_high" if tool.destructive else "propose_intro", lang)
                others = CobRouter(None, rules=load_rules()).other_actions(
                    p.turn.message, caller.role, tool.name
                )
                if others:
                    intro += " " + ts("one_action_at_a_time", lang)
                result = ToolResult(markdown=intro)
            else:
                result = proposed
        text = result.markdown
        yield sse_event("token", {"content": text})
        ui: list[dict[str, Any]] = []
        if card is not None:
            ui.append(card.model_dump(mode="json"))
            yield cob_ui_event(card)
        if result.link is not None:
            ui.append(result.link.model_dump(mode="json"))
            yield cob_ui_event(result.link)
        if result.quick_replies:
            frame = to_sse(QuickRepliesEvent(items=result.quick_replies), Surface.CONVERSATION)
            if frame is not None:
                yield frame
        data = self._data(
            p,
            lang,
            intent="action",
            mode=p.mode,
            ui=ui,
            tool=decision.tool,
            source=decision.source,
            ok=result.ok,
        )
        reply_id = await self._own_turn(p, text, data)
        yield cob_done(
            message_id=reply_id,
            conversation_id=p.visible_conversation_id,
            intent="action",
            mode=p.mode,
            counted=False,
            label=None,
            route="action",
            language=lang,
            claims_removed=0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # end COB5 proposals
