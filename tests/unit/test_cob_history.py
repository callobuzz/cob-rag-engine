"""COB's conversations: private to their author and workspace, deletable, rated, retained for a
limited time, and invisible to a knowledge base's own conversation routes."""

import uuid
from datetime import UTC, datetime, timedelta

from callobuzz_rag.accounts.cleanup import cleanup
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.message import Message

LIST = "/api/v1/cob/conversations"


async def chat(cob, key: str, message: str = "How do I embed the widget?", **extra) -> dict:  # type: ignore[no-untyped-def,type-arg]
    events = await cob.sse(key, {"message": message, **extra})
    return {"conversation_id": events[0].data["conversation_id"], "done": events[-1].data}


async def ids(cob, key: str) -> set[str]:  # type: ignore[no-untyped-def]
    res = await cob.get(key, LIST)
    assert res.status_code == 200, res.text
    return {i["id"] for i in res.json()["items"]}


async def test_list_shows_only_my_cob_chats_in_this_workspace(cob) -> None:  # type: ignore[no-untyped-def]
    c1 = (await chat(cob, "alice"))["conversation_id"]
    c2 = (await chat(cob, "alice", "And the colours?"))["conversation_id"]
    c3 = (await chat(cob, "alice_anand"))["conversation_id"]  # the same person, another workspace
    c4 = (await chat(cob, "ravi"))["conversation_id"]  # a colleague
    assert await ids(cob, "alice") == {c1, c2}
    assert await ids(cob, "alice_anand") == {c3}
    assert await ids(cob, "ravi") == {c4}
    assert await ids(cob, "sa") == set()  # the operator view has chats of its own


async def test_the_list_carries_title_count_and_pages(cob) -> None:  # type: ignore[no-untyped-def]
    for n in range(3):
        await chat(cob, "alice", f"Question number {n}")
    body = (await cob.get("alice", LIST + "?limit=2")).json()
    assert body["total"] == 3 and len(body["items"]) == 2
    item = body["items"][0]
    assert set(item) == {"id", "title", "created_at", "updated_at", "message_count"}
    assert item["title"].startswith("Question number") and item["message_count"] == 2
    rest = (await cob.get("alice", LIST + "?limit=2&offset=2")).json()
    assert len(rest["items"]) == 1 and rest["total"] == 3
    assert (await cob.get("alice", LIST + "?limit=51")).status_code == 422


async def test_old_chats_are_not_listed(cob) -> None:  # type: ignore[no-untyped-def]
    old = (await chat(cob, "alice"))["conversation_id"]
    fresh = (await chat(cob, "alice"))["conversation_id"]
    row = next(c for c in cob.db.all(Conversation) if str(c.id) == old)
    row.updated_at = datetime.now(UTC) - timedelta(days=31)
    assert await ids(cob, "alice") == {fresh}


async def test_a_chat_reads_back_with_its_sources_and_buttons(cob) -> None:  # type: ignore[no-untyped-def]
    c = (await chat(cob, "alice"))["conversation_id"]
    body = (await cob.get("alice", f"{LIST}/{c}")).json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    answer = body["messages"][1]
    assert answer["sources"][0]["slug"] == "screens/widget"
    assert answer["ui"][0]["kind"] == "cob_link" and answer["rating"] is None
    assert body["title"] == "How do I embed the widget?"


async def test_another_users_chat_is_404_everywhere(cob) -> None:  # type: ignore[no-untyped-def]
    c1 = (await chat(cob, "alice"))["conversation_id"]
    res = await cob.get("ravi", f"{LIST}/{c1}")
    assert res.status_code == 404 and res.json()["code"] == "conversation_not_found"
    assert (await cob.delete("ravi", f"{LIST}/{c1}")).status_code == 404
    res = await cob.chat("ravi", {"message": "hi", "conversation_id": c1})
    assert res.status_code == 404 and res.json()["code"] == "conversation_not_found"
    # the same person in another workspace cannot open it either
    assert (await cob.get("alice_anand", f"{LIST}/{c1}")).status_code == 404
    assert (await cob.get("alice", f"{LIST}/{uuid.uuid4()}")).status_code == 404


async def test_delete_removes_the_chat_and_its_messages(cob) -> None:  # type: ignore[no-untyped-def]
    c = (await chat(cob, "alice"))["conversation_id"]
    assert (await cob.delete("alice", f"{LIST}/{c}")).status_code == 204
    assert (await cob.get("alice", f"{LIST}/{c}")).status_code == 404
    assert [m for m in cob.db.all(Message) if str(m.conversation_id) == c] == []


async def test_cob_chats_never_show_in_a_knowledge_bases_conversation_routes(cob) -> None:  # type: ignore[no-untyped-def]
    c = (await chat(cob, "alice"))["conversation_id"]
    corpus = cob.world.corpus.id
    res = await cob.get("sa", f"/api/v1/corpora/{corpus}/conversations/{c}")
    assert res.status_code == 404
    res = await cob.get("sa", f"/api/v1/corpora/{corpus}/conversations/{c}/messages")
    assert res.status_code == 404
    res = await cob.delete("sa", f"/api/v1/corpora/{corpus}/conversations/{c}")
    assert res.status_code in (404, 409)  # COB's knowledge base is protected anyway
    assert any(str(x.id) == c for x in cob.db.all(Conversation))  # untouched


async def test_an_assistant_chat_is_not_reachable_through_cob(cob) -> None:  # type: ignore[no-untyped-def]
    other = cob.db.add(
        Conversation(corpus_id=cob.world.corpus.id, kind="assistant", user_id=cob.world.alice.id)
    )
    assert (await cob.get("alice", f"{LIST}/{other.id}")).status_code == 404
    res = await cob.chat("alice", {"message": "hi", "conversation_id": str(other.id)})
    assert res.status_code == 404


async def test_feedback_is_only_on_my_own_assistant_message(cob) -> None:  # type: ignore[no-untyped-def]
    done = (await chat(cob, "alice"))["done"]
    mid = done["message_id"]
    url = f"/api/v1/cob/messages/{mid}/feedback"
    res = await cob.post("alice", url, {"rating": -1, "comment": "my password is hunter2xyz"})
    assert res.status_code == 204
    stored = next(m for m in cob.db.all(Message) if str(m.id) == mid)
    assert stored.agent_log["cob"]["rating"] == -1
    assert "hunter2xyz" not in (stored.agent_log["cob"]["comment"] or "")  # masked like any message
    assert stored.agent_log["cob"]["sources"]  # the rest of COB's details survive
    body = (await cob.get("alice", f"{LIST}/{done['conversation_id']}")).json()
    assert body["messages"][1]["rating"] == -1
    assert (await cob.post("ravi", url, {"rating": 1})).status_code == 404
    user_msg = next(
        m
        for m in cob.db.all(Message)
        if m.role == "user" and str(m.conversation_id) == done["conversation_id"]
    )
    res = await cob.post("alice", f"/api/v1/cob/messages/{user_msg.id}/feedback", {"rating": 1})
    assert res.status_code == 404
    assert (await cob.post("alice", url, {"rating": 2})).status_code == 422
    assert (await cob.post("alice", url, {"rating": 1, "extra": 1})).status_code == 422
    assert (await cob.post("alice", url, {"rating": 1, "comment": "x" * 501})).status_code == 422
    assert (await cob.post("alice", url, {"rating": 1})).status_code == 204  # can change its mind


async def test_cleanup_deletes_old_cob_chats_only(cob) -> None:  # type: ignore[no-untyped-def]
    old_cob = (await chat(cob, "alice"))["conversation_id"]
    recent_cob = (await chat(cob, "alice"))["conversation_id"]
    old_assistant = cob.db.add(
        Conversation(
            corpus_id=cob.world.corpus.id,
            kind="assistant",
            updated_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    )
    old_row = next(c for c in cob.db.all(Conversation) if str(c.id) == old_cob)
    old_row.updated_at = datetime.now(UTC) - timedelta(days=31)
    counts = await cleanup(cob.db.session(), cob_history_days=30)
    assert counts["cob_conversations"] == 1
    left = {str(c.id) for c in cob.db.all(Conversation)}
    assert recent_cob in left and old_cob not in left and str(old_assistant.id) in left
    assert [m for m in cob.db.all(Message) if str(m.conversation_id) == old_cob] == []


async def test_the_kind_filter_holds_on_an_ordinary_knowledge_base_too(cob) -> None:  # type: ignore[no-untyped-def]
    kb = cob.db.add(
        Corpus(
            id=uuid.uuid4(),
            name="KB",
            qdrant_collection="kb",
            tenant_id=cob.world.shree.id,
            is_system=False,
        )
    )
    chat_row = cob.db.add(
        Conversation(corpus_id=kb.id, kind="cob", user_id=cob.world.alice.id, title="private")
    )
    base = f"/api/v1/corpora/{kb.id}/conversations/{chat_row.id}"
    assert (await cob.get("sa", base)).status_code == 404
    assert (await cob.get("sa", base + "/messages")).status_code == 404
    assert (await cob.delete("sa", base)).status_code == 404
    assert chat_row in cob.db.all(Conversation)


async def test_conversation_context_keeps_the_two_kinds_apart() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import pytest

    from callobuzz_rag.api.errors import APIError
    from callobuzz_rag.conversation import surfaces

    corpus_id = uuid.uuid4()
    conv = SimpleNamespace(id=uuid.uuid4(), corpus_id=corpus_id, kind="cob")
    session = AsyncMock()
    session.get = AsyncMock(return_value=conv)
    with pytest.raises(APIError) as refused:  # the assistant routes may not continue a COB chat
        await surfaces.conversation_context(session, corpus_id, conv.id, SimpleNamespace())
    assert refused.value.status_code == 404
    conv.kind = "assistant"
    with pytest.raises(APIError):  # and COB may not continue an assistant chat
        await surfaces.conversation_context(
            session, corpus_id, conv.id, SimpleNamespace(), kind="cob"
        )


async def test_a_chat_that_ended_without_a_turn_is_not_listed(cob) -> None:  # type: ignore[no-untyped-def]
    kept = (await chat(cob, "alice"))["conversation_id"]
    cob.director.script = "error"
    events = await cob.sse("alice", {"message": "How do I embed the widget?"})
    assert any(e.event == "error" for e in events)
    assert any(not c.message_count for c in cob.db.all(Conversation))  # the empty row exists
    assert await ids(cob, "alice") == {kept}
    assert (await cob.get("alice", LIST)).json()["total"] == 1
