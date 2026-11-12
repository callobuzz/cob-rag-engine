"""A rig for the WhatsApp answer loop: the real director with fakes, a recording Graph, the
in-memory database, and the gates as recorders. Nothing here touches Meta, a model or a network.
"""

import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from callobuzz_rag.billing.enforcement import NOT_BILLED
from callobuzz_rag.channels.whatsapp import inbound
from callobuzz_rag.channels.whatsapp.graph import GraphClient
from callobuzz_rag.channels.whatsapp.store import store_events
from callobuzz_rag.channels.whatsapp.webhook import parse
from callobuzz_rag.conversation import director as director_mod
from callobuzz_rag.conversation.director import Director
from callobuzz_rag.conversation.labeler import Labeler
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.store import RuntimeConfig
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message
from tests.unit.conversation_helpers import FakeLLM, fake_orch, make_config
from tests.unit.whatsapp_fixtures import FakeGraph
from tests.unit.whatsapp_world import KEY, WhatsAppWorld, whatsapp_settings

TS = 1_790_000_000  # the clock of the fixtures' messages
NOW = datetime.fromtimestamp(TS + 60, tz=UTC)

BUSINESS = {
    "label": "business_question",
    "confidence": 0.9,
    "language": "en",
    "topic": None,
    "frustrated": False,
    "needs_human": False,
}


class FakeStorage:
    """Object storage with `download(bucket, key)`: the files of the documents a test uploads."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.downloads: list[str] = []

    async def download(self, bucket: str, key: str) -> bytes:
        self.downloads.append(key)
        return self.files[key]


class Rig:
    """One business, one chat, everything injected."""

    def __init__(self, world: WhatsAppWorld, *, config: Any = None, graph: FakeGraph | None = None):
        from callobuzz_rag.conversation.offers import registry as default_registry

        self.world = world
        self.graph = graph or FakeGraph()
        self.config = config or make_config()
        self.runtime = RuntimeConfig(
            config=self.config, version=3, draft_revision=None, enabled=True
        )
        self.llms: list[FakeLLM] = []
        self.default_llm = FakeLLM(
            json_replies=[BUSINESS], stream=["Home collection costs Rs 150 [1]."]
        )
        self.registry = default_registry
        self.calls: list[str] = []  # the gates, in the order they ran
        self.settings = Settings(_env_file=None, **{**whatsapp_settings(), "encryption_key": KEY})
        self.now = NOW
        self.persisted: list[Any] = []
        self._deliveries = 0
        self.storage = FakeStorage()
        self.after_send: Any = None

    # ---------------------------------------------------------------- the pieces

    def director(self, corpus: Any = None, profile: Any = None) -> Director:
        from callobuzz_rag.conversation.answering import AnswerDeps

        llm = self.llms.pop(0) if self.llms else self.default_llm
        session = MagicMock()
        session.close = AsyncMock()
        session.commit = AsyncMock()
        session.invalidate = AsyncMock()
        deps = AnswerDeps(
            settings=Settings(_env_file=None, encryption_key=KEY),
            session_factory=lambda: session,
            orchestrator_factory=lambda s: fake_orch(),
            retrieval_kwargs={},
            llm=llm,
            model_name="answer-model",
            translate_cache=None,
            payer="byok",
        )
        return Director(
            deps,
            labeler=Labeler(llm=llm, cache=None, timeout_s=1.0, ttl_s=60),
            registry=self.registry,
        )

    def deps(self) -> inbound.InboundDeps:
        return inbound.InboundDeps(
            session_factory=self.world.db.factory(),
            settings=self.settings,
            crypto=self.world.crypto,
            graph=GraphClient(
                settings=SimpleNamespace(
                    whatsapp_graph_url="https://graph.facebook.com",
                    whatsapp_graph_version="v23.0",
                    whatsapp_app_id="123456789",
                    whatsapp_app_secret="s",
                ),
                transport=self.graph.transport,
            ),
            director_factory=self.director,
            clock=lambda: self.now,
            after_send=self.after_send,
            storage=self.storage,
        )

    async def deliver(self, *payloads: dict[str, Any]) -> list[uuid.UUID]:
        """What the webhook does: parse and store; the chats to wake."""
        events = [e for p in payloads for e in parse(p)]
        session = self.world.db.session()
        self._deliveries += 1  # separate webhook calls arrive at separate moments
        stamp = self.now + timedelta(milliseconds=self._deliveries)
        woken = await store_events(session, self.world.crypto, events, now=stamp)
        await session.commit()
        return woken

    async def run(
        self, thread_id: uuid.UUID, deps: inbound.InboundDeps | None = None
    ) -> dict[str, int]:
        return await inbound.process_thread(deps or self.deps(), thread_id)

    # ---------------------------------------------------------------- what went out

    def sends(self) -> list[Any]:
        """The Graph requests that sent something (not the read receipts)."""
        return [r for r in self.graph.requests if isinstance(r.json, dict) and r.json.get("type")]

    def texts(self) -> list[str]:
        out = []
        for r in self.sends():
            kind = r.json["type"]
            body = r.json[kind]
            if kind == "text":
                out.append(body["body"])
            elif kind == "interactive":
                out.append(body["body"]["text"])
            elif kind == "template":
                out.append("template:" + body["name"])
            elif kind == "document":
                out.append("document:" + body["filename"])
        return out

    def reads(self) -> list[Any]:
        return [
            r
            for r in self.graph.requests
            if isinstance(r.json, dict) and r.json.get("status") == "read"
        ]

    def metered(self) -> list[dict[str, Any]]:
        return [c.kwargs for c in self.io.meter.await_args_list]

    def stored(self, role: str | None = None) -> list[Message]:
        rows = self.world.db.all(Message)
        return [m for m in rows if role is None or m.role == role]


@contextmanager
def rig_for(
    world: WhatsAppWorld,
    *,
    config: Any = None,
    graph: FakeGraph | None = None,
    assistant: bool = True,
    cap: Exception | None = None,
    pause: str | None = None,
    reference: str | None = "SL-4F7K",
    disclosure: bool = False,
) -> Iterator[Rig]:
    """The patches a chat needs: the assistant's config, the gates (recording their order), the
    director's two writes, the reference code and the handoff notification queue."""
    from callobuzz_rag.conversation.handoff.reference import reserve_ref  # noqa: F401
    from callobuzz_rag.fairuse.decision import GateDecision
    from callobuzz_rag.tiers.profiles import noop_profile

    world.db.default_plan.channels = {"whatsapp": True, "whatsapp_fees": "pass_through"}
    rig = Rig(world, config=config, graph=graph)
    with ExitStack() as stack:
        calls = rig.calls

        async def cap_gate(*a: Any, **k: Any) -> None:
            calls.append("cap")
            if cap is not None:
                raise cap

        async def billing_gate(*a: Any, **k: Any) -> Any:
            calls.append("billing")
            return NOT_BILLED

        async def fair_use(session: Any, tenant_id: Any) -> Any:
            calls.append("fair_use")
            from callobuzz_rag.fairuse.limits import EffectiveFairUse, FairUseLimits, FairUsePolicy

            return EffectiveFairUse(
                limits=FairUseLimits(), policy=FairUsePolicy(), exempt=False, overridden=frozenset()
            )

        stack.enter_context(patch("callobuzz_rag.fairuse.gate.enforce_question_cap", cap_gate))
        stack.enter_context(patch("callobuzz_rag.fairuse.gate.enforce_billing", billing_gate))
        stack.enter_context(patch("callobuzz_rag.fairuse.gate.effective_fair_use", fair_use))
        if pause:
            stack.enter_context(
                patch(
                    "callobuzz_rag.fairuse.gate.answer_gate",
                    AsyncMock(
                        return_value=GateDecision(
                            "pause", noop_profile(), (pause,), (), NOT_BILLED, pause, False, None
                        )
                    ),
                )
            )
        if not disclosure:
            # TV5: the fixed AI line is on by default; the chats of other tests do not count it
            stack.enter_context(
                patch(
                    "callobuzz_rag.channels.whatsapp.inbound._disclosure_line",
                    AsyncMock(return_value=None),
                )
            )
        stack.enter_context(
            patch(
                "callobuzz_rag.conversation.surfaces.assistant_for",
                AsyncMock(return_value=rig.runtime if assistant else None),
            )
        )

        async def persist(session_factory: Any, rec: Any) -> tuple[Any, Any]:
            """The director's write, kept in the in-memory database (messages and state)."""
            rig.persisted.append(rec)
            conv = await world.db.session().get(Conversation, rec.turn.conversation_id)
            if conv is None:
                return None, None
            conv.assistant_state = rec.state.to_dict()
            conv.language = rec.language
            conv.message_count = (conv.message_count or 0) + 2
            user = world.db.add(
                Message(conversation_id=conv.id, role="user", content=rec.masked, token_count=0)
            )
            reply = world.db.add(
                Message(
                    conversation_id=conv.id, role="assistant", content=rec.answer, token_count=0
                )
            )
            return user.id, reply.id

        meter = AsyncMock()
        stack.enter_context(patch.object(director_mod, "persist_turn", persist))
        stack.enter_context(patch.object(director_mod, "record_usage_new_session", meter))
        rig.io = SimpleNamespace(meter=meter)  # type: ignore[attr-defined]
        if reference:
            stack.enter_context(
                patch(
                    "callobuzz_rag.conversation.handoff.reference.reserve_ref",
                    AsyncMock(return_value=reference),
                )
            )
        rig.offered = stack.enter_context(  # type: ignore[attr-defined]
            patch("callobuzz_rag.conversation.handoff.provider._enqueue_offered")
        )
        yield rig


def state_of(world: WhatsAppWorld) -> ConversationState:
    (conv,) = world.db.all(Conversation)
    return ConversationState.from_dict(conv.assistant_state)
