"""GET /api/v1/admin/cob/review: COB's weak answers for the operator, masked and grouped."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from callobuzz_rag.api.deps import get_session
from callobuzz_rag.cob.eval.review import Signal, group_signals
from callobuzz_rag.cob.system import ensure_cob_system, get_system_corpus
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.assistant_turn import AssistantTurn
from callobuzz_rag.db.models.cob_status import CobStatus
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message
from tests.unit.accounts_fakes import FakeDB
from tests.unit.scope_harness import call, scoped_app

REVIEW = "/api/v1/admin/cob/review"
NOW = datetime.now(UTC)


class Setup:
    def __init__(self, app: Any) -> None:
        self.db = FakeDB()
        self.db.add(CobStatus(id=1, sync_state="idle", document_count=10))

        async def override() -> Any:
            yield self.db.session()

        app.dependency_overrides[get_session] = override

    async def ready(self) -> uuid.UUID:
        session = self.db.session()
        await ensure_cob_system(session, settings=Settings(_env_file=None), lock=False)  # type: ignore[arg-type,call-arg]
        corpus = await get_system_corpus(session)  # type: ignore[arg-type]
        assert corpus is not None
        return corpus.id

    def conversation(self, corpus_id: uuid.UUID, *, when: datetime) -> Conversation:
        return self.db.add(  # type: ignore[no-any-return]
            Conversation(
                corpus_id=corpus_id, kind="cob", channel="cob", updated_at=when, created_at=when
            )
        )

    def exchange(
        self,
        conv: Conversation,
        question: str,
        *,
        when: datetime,
        log: dict[str, Any] | None = None,
    ) -> Message:
        self.db.add(
            Message(conversation_id=conv.id, role="user", content=question, created_at=when)
        )
        return self.db.add(  # type: ignore[no-any-return]
            Message(
                conversation_id=conv.id,
                role="assistant",
                content="An answer.",
                created_at=when + timedelta(seconds=1),
                agent_log={"cob": log} if log is not None else None,
            )
        )

    def turn(
        self,
        corpus_id: uuid.UUID,
        question: str,
        *,
        when: datetime,
        reply: Message | None = None,
        no_answer: bool = False,
        claims_removed: int = 0,
    ) -> AssistantTurn:
        return self.db.add(  # type: ignore[no-any-return]
            AssistantTurn(
                corpus_id=corpus_id,
                tenant_id=uuid.uuid4(),
                message_id=reply.id if reply else None,
                channel="cob",
                label="how_to",
                label_source="rules",
                language="en",
                script="latin",
                route="no_answer" if no_answer else "grounded",
                question_masked=question,
                no_answer=no_answer,
                claims_removed=claims_removed,
                is_preview=False,
                created_at=when,
            )
        )


async def world(app: Any) -> tuple[Setup, uuid.UUID]:
    setup = Setup(app)
    return setup, await setup.ready()


async def test_groups_the_three_signals_newest_first_with_masked_text() -> None:
    with scoped_app() as (app, _w):
        setup, cid = await world(app)
        conv = setup.conversation(cid, when=NOW)
        # a thumbs down with a comment, citing the widget page
        down = setup.exchange(
            conv,
            "How do I put chat on my site?",
            when=NOW - timedelta(hours=3),
            log={
                "rating": -1,
                "comment": "It sent me to the wrong screen",
                "role": "owner",
                "language": "en",
                "sources": [{"slug": "screens/widget"}],
            },
        )
        # the same question reworded, answered with nothing found
        setup.turn(
            cid, "how can I put the chat on my site", when=NOW - timedelta(hours=2), no_answer=True
        )
        # a different question where the fact check removed a statement
        reply = setup.exchange(
            conv,
            "What does the Pro plan cost?",
            when=NOW - timedelta(hours=1),
            log={"role": "member", "language": "en", "sources": [{"slug": "screens/plan"}]},
        )
        setup.turn(
            cid,
            "What does the Pro plan cost?",
            when=NOW - timedelta(hours=1),
            reply=reply,
            claims_removed=2,
        )
        # a fine answer is not listed
        fine = setup.exchange(
            conv, "How do I log out?", when=NOW - timedelta(hours=4), log={"rating": 1}
        )
        setup.turn(cid, "How do I log out?", when=NOW - timedelta(hours=4), reply=fine)
        res = await call(app, "sa", "GET", REVIEW)
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert [i["question"] for i in items] == [
        "What does the Pro plan cost?",
        "how can I put the chat on my site",
    ]
    plan, widget = items
    assert plan["reasons"] == ["claims_removed"] and plan["role"] == "member"
    assert plan["top_slug"] == "screens/plan" and plan["count"] == 1 and plan["comments"] == []
    assert widget["count"] == 2 and widget["reasons"] == ["no_answer", "thumbs_down"]
    assert widget["comments"] == ["It sent me to the wrong screen"]
    assert widget["top_slug"] == "screens/widget" and widget["language"] == "en"
    assert down.id is not None


async def test_the_same_answer_with_two_signals_counts_once() -> None:
    with scoped_app() as (app, _w):
        setup, cid = await world(app)
        conv = setup.conversation(cid, when=NOW)
        reply = setup.exchange(
            conv, "Why is my upload slow?", when=NOW - timedelta(hours=1), log={"rating": -1}
        )
        setup.turn(
            cid,
            "Why is my upload slow?",
            when=NOW - timedelta(hours=1),
            reply=reply,
            no_answer=True,
        )
        res = await call(app, "sa", "GET", REVIEW)
    [item] = res.json()["items"]
    assert item["count"] == 1 and item["reasons"] == ["no_answer", "thumbs_down"]
    assert item["top_slug"] is None and item["role"] == "unknown"


async def test_rating_down_keeps_only_thumbs_down() -> None:
    with scoped_app() as (app, _w):
        setup, cid = await world(app)
        conv = setup.conversation(cid, when=NOW)
        setup.exchange(
            conv, "Where are my leads?", when=NOW - timedelta(hours=2), log={"rating": -1}
        )
        setup.turn(cid, "What is the moon made of?", when=NOW - timedelta(hours=1), no_answer=True)
        every = await call(app, "sa", "GET", REVIEW)
        down = await call(app, "sa", "GET", f"{REVIEW}?rating=down")
    assert len(every.json()["items"]) == 2
    assert [i["question"] for i in down.json()["items"]] == ["Where are my leads?"]


async def test_old_signals_and_other_knowledge_bases_are_left_out() -> None:
    with scoped_app() as (app, _w):
        setup, cid = await world(app)
        old = NOW - timedelta(days=45)
        conv = setup.conversation(cid, when=old)
        setup.exchange(conv, "An old question", when=old, log={"rating": -1})
        setup.turn(cid, "An old unanswered one", when=old, no_answer=True)
        setup.turn(uuid.uuid4(), "Another corpus's question", when=NOW, no_answer=True)
        thirty = await call(app, "sa", "GET", REVIEW)
        ninety = await call(app, "sa", "GET", f"{REVIEW}?days=90")
    assert thirty.json()["items"] == []
    assert {i["question"] for i in ninety.json()["items"]} == {
        "An old question",
        "An old unanswered one",
    }


async def test_personal_identifiers_are_masked_again_on_the_way_out() -> None:
    with scoped_app() as (app, _w):
        setup, cid = await world(app)
        setup.turn(cid, "My Aadhaar is 2345 6789 0124, why no answer?", when=NOW, no_answer=True)
        res = await call(app, "sa", "GET", REVIEW)
    [item] = res.json()["items"]
    assert "2345" not in item["question"] and "6789" not in item["question"]


async def test_limit_and_days_are_validated() -> None:
    with scoped_app() as (app, _w):
        await world(app)
        too_far = await call(app, "sa", "GET", f"{REVIEW}?days=91")
        zero = await call(app, "sa", "GET", f"{REVIEW}?days=0")
        big = await call(app, "sa", "GET", f"{REVIEW}?limit=101")
        bad = await call(app, "sa", "GET", f"{REVIEW}?rating=sideways")
    assert [r.status_code for r in (too_far, zero, big, bad)] == [422, 422, 422, 422]


async def test_no_system_knowledge_base_yet_is_an_empty_list() -> None:
    with scoped_app() as (app, _w):
        Setup(app)
        res = await call(app, "sa", "GET", REVIEW)
    assert res.status_code == 200 and res.json() == {"items": []}


@pytest.mark.parametrize("key", ["admin", "owner_a", "member_a", "tk_a", "ck_a"])
async def test_only_the_operator_session_may_read_it(key: str) -> None:
    with scoped_app() as (app, _w):
        await world(app)
        res = await call(app, key, "GET", REVIEW)
    assert res.status_code == 403
    if key == "admin":  # even the admin key: sessions only
        assert res.json()["code"] == "session_required"


async def test_unauthenticated_is_401() -> None:
    with scoped_app() as (app, _w):
        await world(app)
        res = await call(app, None, "GET", REVIEW)
    assert res.status_code == 401


def test_grouping_is_by_normalised_question_and_limited() -> None:
    topics = ["widgets", "uploads", "billing", "leads", "invitations"]
    signals = [
        Signal(
            f"k{n}", f"how to fix {topic}", NOW - timedelta(minutes=n + 1), reasons={"no_answer"}
        )
        for n, topic in enumerate(topics)
    ]
    signals.append(Signal("again", "fix widgets, how to", NOW, reasons={"thumbs_down"}))
    groups = group_signals(signals, only_down=False, limit=3)
    assert len(groups) == 3
    assert groups[0].last_at >= groups[1].last_at >= groups[2].last_at
    assert groups[0].count == 2 and groups[0].reasons == ["no_answer", "thumbs_down"]
    assert [g.count for g in groups[1:]] == [1, 1]
    assert len(group_signals(signals, only_down=True, limit=10)) == 1
