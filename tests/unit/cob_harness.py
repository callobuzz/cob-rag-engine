"""A world for COB chat tests: the real app and middleware state, an in-memory database, and a
scripted director and retriever in place of the model and the vector store.

`cob` is the fixture; tests `await cob.sse("alice", {"message": "..."})` and read the events.
The director never runs a model: `FakeDirector` records how it was built and what turn it got,
then yields a script. `FakeRetriever` serves records of the real docs index, filtered by the
`audience` filter it is given, so a test can prove an owner never sees an administrator page.
"""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, NamedTuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.api.deps import get_session
from callobuzz_rag.billing.enforcement import BillingState
from callobuzz_rag.cob.kb import load_docs_index
from callobuzz_rag.cob.types import Audience
from callobuzz_rag.conversation import surfaces
from callobuzz_rag.conversation.envelope import (
    DoneEvent,
    ErrorEvent,
    MetaEvent,
    QuickRepliesEvent,
    SourcesEvent,
    StageEvent,
    TokenEvent,
)
from callobuzz_rag.conversation.schema import QuickReply
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import Route, Script, TurnLabel
from callobuzz_rag.db.models.cob_status import CobStatus
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.message import Message
from tests.unit.accounts_fakes import FakeDB
from tests.unit.cob_tool_helpers import FakeInternalClient  # COB4 router
from tests.unit.scope_harness import NOW, World, _tenant, _user, call, scoped_app

QID = uuid.uuid4()
ANSWER = "Open the Website widget screen and copy the embed snippet."


class Ev(NamedTuple):
    event: str
    data: dict[str, Any]


def parse_sse(text: str) -> list[Ev]:
    out: list[Ev] = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append(Ev(lines["event"], json.loads(lines["data"])))
    return out


def tokens(events: list[Ev]) -> str:
    return "".join(e.data["content"] for e in events if e.event == "token")


def _chunk(slug: str, anchor: str, score: float, text: str = "How it works.") -> Any:
    return SimpleNamespace(
        citation_index=1,
        chunk_id=uuid.uuid4(),
        document_id=None,
        external_id=f"system:cob/{slug}#{anchor}",
        file_name=None,
        section=None,
        page=None,
        text=text,
        score=score,
    )


class FakeRetriever:
    """Records of the real docs index; the `audience` filter it is given decides what it finds."""

    def __init__(self, records: list[tuple[str, float]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.records = records or [
            ("screens/widget", 0.91),
            ("screens/team", 0.7),
            ("screens/engine", 0.8),  # administrator-only
        ]

    async def run_retrieval_pipeline(self, text: str, corpus_id: Any, **kwargs: Any) -> Any:
        self.calls.append({"text": text, "corpus_id": corpus_id, **kwargs})
        allowed = (kwargs.get("metadata_filters") or {}).get("audience")
        index = load_docs_index()
        chunks = []
        for slug, score in self.records:
            page = index.pages[slug]
            if allowed is None or page.audience.value in allowed:
                chunks.append(_chunk(slug, "", score, page.summary))
        return SimpleNamespace(assembled=SimpleNamespace(cited_chunks=chunks))


class FakeDirector:
    """Stands in for the conversation director: records its build and its turn."""

    def __init__(self, world: "CobWorld") -> None:
        self.world = world
        self.kwargs: dict[str, Any] = {}
        self.turn: Any = None
        self.built = 0
        self.closed = 0
        self.script = "answer"
        self.model_calls = 1
        self.answer_calls: int | None = None  # None: as many as model_calls
        self.cited: list[str] = ["system:cob/screens/widget#"]  # external_ids the answer cites
        self.language = "en"

    def build(self, request: Any, corpus: Any, **kwargs: Any) -> "FakeDirector":
        self.built += 1
        self.kwargs = {"corpus": corpus, **kwargs}
        return self

    async def aclose(self) -> None:
        self.closed += 1

    def _persist(self, turn: Any, text: str) -> uuid.UUID:
        db = self.world.db
        db.add(Message(conversation_id=turn.conversation_id, role="user", content=turn.text))
        reply = db.add(
            Message(conversation_id=turn.conversation_id, role="assistant", content=text)
        )
        conv = next(c for c in db.all(Conversation) if c.id == turn.conversation_id)
        conv.message_count = (conv.message_count or 0) + 2  # what persist_turn does
        conv.title = conv.title or turn.text[:60]
        conv.updated_at = datetime.now(UTC)
        return reply.id  # type: ignore[no-any-return]

    async def run_stream(self, turn: Any) -> Any:
        self.turn = turn
        script = self.script
        if script == "error":
            yield ErrorEvent(
                detail="The assistant could not answer right now.", code="assistant_failed"
            )
            return
        lang = self.language
        route = {
            "answer": Route.GROUNDED,
            "smalltalk": Route.SMALLTALK,
            "no_answer": Route.NO_ANSWER,
        }[script]
        label = TurnLabel.SMALLTALK if script == "smalltalk" else TurnLabel.BUSINESS_QUESTION
        yield MetaEvent(
            query_id=QID,
            conversation_id=turn.conversation_id,
            session=None,
            label=label,
            route=route,
            language=lang,
            script=Script.LATIN,
            assistant_version=1,
        )
        yield StageEvent(stage="search", elapsed_ms=5)
        sources: list[dict[str, Any]] = []
        if script == "answer":
            sources = [
                {
                    "index": n,
                    "chunk_id": "c-secret",
                    "document_id": "d-secret",
                    "external_id": external_id,
                    "file_name": None,
                    "section": None,
                    "page": None,
                    "text": "Website widget\n\nKeys, allowed sites and the embed snippet.",
                    "score": 0.93,
                    "score_breakdown": {"dense_score": 0.9},
                }
                for n, external_id in enumerate(self.cited, 1)
            ]
            yield SourcesEvent(sources=sources, query_id=QID)
            text = ANSWER
            yield TokenEvent(content=text)
        elif script == "smalltalk":
            text = "You're welcome!"
            yield TokenEvent(content=text)
        else:
            from callobuzz_rag.cob.config import cob_config
            from callobuzz_rag.conversation.smalltalk import no_answer_text

            text = no_answer_text(cob_config(), lang)
            yield TokenEvent(content=text)
            yield TokenEvent(content="\n\nWhich screen are you on?")
            text += "\n\nWhich screen are you on?"
        yield QuickRepliesEvent(items=[QuickReply(label="More", message="Tell me more")])
        message_id = self._persist(turn, text) if turn.persist else None  # COB6
        yield DoneEvent(
            query_id=QID,
            label=label,
            route=route,
            language=lang,
            cta=None,
            timings={"search": 5},
            claims_removed=0,
            model="m" if self.model_calls else None,
            usage={
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "model_calls": self.model_calls,
                "answer_calls": self.model_calls
                if self.answer_calls is None
                else self.answer_calls,
            },
            latency_ms=12,
            text=text,
            message_id=message_id,
            conversation_id=turn.conversation_id,
            sources=sources,
            gate_threshold=0.55,
        )


class CobWorld(World):
    """Two workspaces (Shree, Anand), the operator workspace, a super admin and their users."""

    def __init__(self) -> None:
        super().__init__()
        self.db = FakeDB()
        self.shree = self.tenant_a
        self.anand = self.tenant_b
        self.operator = _tenant("Operator")
        self.operator.is_operator = True
        self.system_tenant = uuid.uuid4()
        self.alice = _user("alice@shree.example")
        self.ravi = _user("ravi@shree.example")
        self.corpus = self.db.add(
            Corpus(
                id=uuid.uuid4(),
                name="COB docs",
                qdrant_collection="cob_docs",
                tenant_id=self.system_tenant,
                is_system=True,
                llm_config=None,
                retrieval_config=None,
                prompt_config=None,
                applied_embedding=None,
            )
        )
        self.status = self.db.add(CobStatus(id=1, sync_state="idle", document_count=321))
        self.director = FakeDirector(self)
        self.retriever = FakeRetriever()
        self.llm_ok = True
        self.quota: tuple[int, int] | None = None  # (limit, used)
        self.paused = False  # BIL4: the caller's workspace has run out of balance
        self.billing_error = False  # BIL4: the billing read itself fails
        # COB4 router: the router's model (None: rules only) and the tools' route answers
        self.router_llm: Any = None
        self.internal = FakeInternalClient()

        def principal(user: Any, tenant: Any, role: str, *, admin: bool = False) -> dict[str, Any]:
            return {
                "is_admin": admin,
                "tenant": tenant,
                "corpus": None,
                "role": role,
                "user": user,
                "session_id": uuid.uuid4(),
                "auth_kind": "session",
            }

        self.principals: dict[str, dict[str, Any]] = {
            "alice": principal(self.alice, self.shree, "owner"),
            "alice_anand": principal(self.alice, self.anand, "owner"),
            "ravi": principal(self.ravi, self.shree, "member"),
            "super_op": principal(self.superadmin, self.operator, "owner"),
        }

    def state_for(self, key: str) -> dict[str, Any] | None:
        return self.principals.get(key) or super().state_for(key)

    def add_byok_kb(self, tenant: Any, config: dict[str, Any]) -> Any:
        return self.db.add(
            Corpus(
                id=uuid.uuid4(),
                name="Own key",
                qdrant_collection=f"c_{uuid.uuid4().hex[:8]}",
                tenant_id=tenant.id,
                is_system=False,
                llm_config=config,
                created_at=NOW,
            )
        )


class CobClient:
    def __init__(self, app: Any, world: CobWorld) -> None:
        self.app = app
        self.world = world
        self.db = world.db
        self.director = world.director
        self.retriever = world.retriever

    async def post(self, key: str | None, path: str, body: Any = None) -> Any:
        return await call(self.app, key, "POST", path, json=body)

    async def get(self, key: str | None, path: str) -> Any:
        return await call(self.app, key, "GET", path)

    async def delete(self, key: str | None, path: str) -> Any:
        return await call(self.app, key, "DELETE", path)

    async def chat(self, key: str | None, body: dict[str, Any]) -> Any:
        return await self.post(key, "/api/v1/cob/chat", body)

    async def sse(self, key: str | None, body: dict[str, Any]) -> list[Ev]:
        res = await self.chat(key, body)
        assert res.status_code == 200, res.text
        return parse_sse(res.text)


@pytest.fixture
def cob() -> Iterator[CobClient]:
    world = CobWorld()
    with scoped_app(world) as (app, _world):
        app.state.session_factory = world.db.factory()

        async def override() -> Any:
            yield world.db.session()

        app.dependency_overrides[get_session] = override

        async def context(
            session: Any,
            corpus_id: Any,
            conversation_id: Any,
            settings: Any,
            kind: str = "assistant",
        ) -> Any:
            return await session.get(Conversation, conversation_id), ConversationState(), []

        with (
            patch.object(surfaces, "build_director", MagicMock(side_effect=world.director.build)),
            patch.object(surfaces, "conversation_context", AsyncMock(side_effect=context)),
            patch(
                "callobuzz_rag.cob.orchestrator.build_retriever",
                MagicMock(side_effect=lambda *a, **k: world.retriever),
            ),
            patch(
                "callobuzz_rag.cob.orchestrator.llm_available",
                MagicMock(side_effect=lambda *a, **k: world.llm_ok),
            ),
            patch(  # COB4 router
                "callobuzz_rag.cob.orchestrator.build_router_llm",
                MagicMock(side_effect=lambda *a, **k: world.router_llm),
            ),
            patch(  # COB4 router
                "callobuzz_rag.cob.orchestrator.make_internal_client",
                MagicMock(side_effect=lambda *a, **k: world.internal),
            ),
            patch(
                "callobuzz_rag.accounts.caps.question_quota",
                AsyncMock(side_effect=lambda *a, **k: world.quota),
            ),
            patch(  # BIL4: no pricing at the cap, and the balance is the world's
                "callobuzz_rag.accounts.caps.overage_lets_through",
                AsyncMock(return_value=False),
            ),
            patch(
                "callobuzz_rag.billing.enforcement.billing_state",
                AsyncMock(side_effect=_billing_state_of(world)),
            ),
        ):
            yield CobClient(app, world)


def _billing_state_of(world: Any) -> Any:
    def state(*_a: Any, **_k: Any) -> BillingState:
        if world.billing_error:
            raise RuntimeError("billing read failed")
        return BillingState(True, world.paused, "prepaid_empty" if world.paused else "ok", 0, "INR")

    return state


def admin_audience_pages() -> list[str]:
    index = load_docs_index()
    return [s for s, p in index.pages.items() if p.audience == Audience.ADMIN]
