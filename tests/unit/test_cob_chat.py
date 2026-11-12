"""POST /api/v1/cob/chat: the role filter, the paying workspace, the event shapes."""

import json

from callobuzz_rag.conversation.types import Channel

CHAT = "/api/v1/cob/chat"


async def test_owner_turn_filters_to_customer_and_public(cob) -> None:  # type: ignore[no-untyped-def]
    await cob.sse("alice", {"message": "How do I add my website?"})
    assert cob.director.kwargs["retrieval_filters"] == {"audience": ["customer", "public"]}
    turn = cob.director.turn
    assert turn.corpus_id == cob.world.corpus.id
    assert turn.tenant_id == cob.world.shree.id
    assert turn.usage_source == "cob" and turn.channel == Channel.COB
    assert cob.director.kwargs["use_headers"] is False


async def test_member_filters_like_an_owner(cob) -> None:  # type: ignore[no-untyped-def]
    await cob.sse("ravi", {"message": "How do I add my website?"})
    assert cob.director.kwargs["retrieval_filters"] == {"audience": ["customer", "public"]}
    assert "member" in cob.director.turn.one_off_instructions
    assert "an owner of the workspace must do it" in cob.director.turn.one_off_instructions


async def test_admin_turn_sees_admin_docs_and_meters_on_the_system_tenant(cob) -> None:  # type: ignore[no-untyped-def]
    await cob.sse("sa", {"message": "Which RAG_ setting sets the fast model?"})
    assert cob.director.kwargs["retrieval_filters"] == {"audience": ["admin", "customer", "public"]}
    assert cob.director.turn.tenant_id == cob.world.system_tenant


async def test_operator_workspace_owner_reads_admin_docs_but_meters_on_the_workspace(cob) -> None:  # type: ignore[no-untyped-def]
    await cob.sse("super_op", {"message": "hello"})
    assert cob.director.kwargs["retrieval_filters"] == {"audience": ["admin", "customer", "public"]}
    assert cob.director.turn.tenant_id == cob.world.operator.id


async def test_request_cannot_widen_the_filter(cob) -> None:  # type: ignore[no-untyped-def]
    res = await cob.chat("alice", {"message": "hi", "audience": ["admin"]})
    assert res.status_code == 422
    res = await cob.chat(
        "alice", {"message": "hi", "context": {"route": "/", "audience": ["admin"]}}
    )
    assert res.status_code == 422
    assert cob.director.built == 0


async def test_byok_workspace_uses_its_own_kb_key(cob) -> None:  # type: ignore[no-untyped-def]
    config = {"model": "gpt-x", "api_key_encrypted": "enc:abc"}
    cob.world.add_byok_kb(cob.world.shree, config)
    await cob.sse("alice", {"message": "hello"})
    assert cob.director.kwargs["llm_config_override"] == config


async def test_workspace_without_its_own_key_uses_the_engine_default(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.add_byok_kb(cob.world.anand, {"model": "m", "api_key_encrypted": "enc:abc"})
    await cob.sse("alice", {"message": "hello"})
    assert cob.director.kwargs["llm_config_override"] is None
    await cob.sse("sa", {"message": "hello"})
    assert cob.director.kwargs["llm_config_override"] is None


async def test_event_order_and_shapes(cob) -> None:  # type: ignore[no-untyped-def]
    events = await cob.sse(
        "alice",
        {"message": "How do I embed the widget?", "context": {"route": "/playground"}},
    )
    names = [e.event for e in events]
    assert names[0] == "meta" and names[-1] == "done" and "sources" in names
    assert names.index("sources") < names.index("token") < names.index("done")
    meta = events[0].data
    assert meta["intent"] == "docs" and meta["mode"] == "answer" and meta["role"] == "owner"
    assert meta["language"] == "en" and meta["conversation_id"]
    src = next(e for e in events if e.event == "sources").data["sources"][0]
    assert set(src) == {"index", "slug", "anchor", "title", "snippet", "route", "public_url"}
    assert src["slug"] == "screens/widget" and src["route"] == "/widget"
    blob = json.dumps([e.data for e in events])
    assert "chunk_id" not in blob and "c-secret" not in blob and "score" not in blob
    link = [e.data for e in events if e.event == "ui" and e.data["kind"] == "cob_link"]
    assert link and link[0]["route"] == "/widget" and link[0]["label"] == "Open Website widget →"
    done = events[-1].data
    assert done["counted"] is True and done["mode"] == "answer" and done["intent"] == "docs"
    assert done["message_id"] and done["conversation_id"] == meta["conversation_id"]
    assert "stage" in names and "quick_replies" in names


async def test_the_answer_details_are_stored_for_the_history(cob) -> None:  # type: ignore[no-untyped-def]
    events = await cob.sse("alice", {"message": "How do I embed the widget?"})
    done = events[-1].data
    from callobuzz_rag.db.models.message import Message

    stored = next(m for m in cob.db.all(Message) if str(m.id) == done["message_id"])
    extra = stored.agent_log["cob"]
    assert extra["role"] == "owner" and extra["counted"] is True
    assert extra["sources"][0]["slug"] == "screens/widget"
    assert extra["ui"][0]["kind"] == "cob_link"


async def test_small_talk_is_labelled_and_free(cob) -> None:  # type: ignore[no-untyped-def]
    cob.director.script = "smalltalk"
    cob.director.model_calls = 0
    events = await cob.sse("alice", {"message": "thanks!"})
    assert events[0].data["intent"] == "smalltalk"
    assert events[-1].data["counted"] is False
    assert not [e for e in events if e.event == "ui"]


async def test_the_workspace_and_the_chat_continue(cob) -> None:  # type: ignore[no-untyped-def]
    first = await cob.sse("alice", {"message": "How do I embed the widget?"})
    conversation_id = first[0].data["conversation_id"]
    second = await cob.sse("alice", {"message": "And colours?", "conversation_id": conversation_id})
    assert second[0].data["conversation_id"] == conversation_id
    assert str(cob.director.turn.conversation_id) == conversation_id


async def test_a_director_error_is_passed_on(cob) -> None:  # type: ignore[no-untyped-def]
    cob.director.script = "error"
    events = await cob.sse("alice", {"message": "hello"})
    assert [e.event for e in events] == ["error"]
    assert events[0].data["code"] == "assistant_failed"
    assert cob.director.closed == 1


async def test_docs_not_synced_yet_is_an_error_event(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.status.document_count = 0
    events = await cob.sse("alice", {"message": "hello"})
    assert [e.event for e in events] == ["error"]
    assert events[0].data["code"] == "cob_docs_syncing"
    assert cob.director.built == 0


async def test_switched_off_is_404(cob) -> None:  # type: ignore[no-untyped-def]
    cob.app.state.settings = cob.app.state.settings.model_copy(update={"cob_enabled": False})
    res = await cob.chat("alice", {"message": "hello"})
    assert res.status_code == 404 and res.json()["code"] == "cob_disabled"
    res = await cob.get("alice", "/api/v1/cob/starters?route=/")
    assert res.status_code == 404 and res.json()["code"] == "cob_disabled"


async def test_the_answer_mirrors_the_language_the_director_found(cob) -> None:  # type: ignore[no-untyped-def]
    cob.director.language = "hi"
    events = await cob.sse("alice", {"message": "मैं अपनी वेबसाइट पर चैट कैसे लगाऊँ?"})
    assert events[0].data["language"] == "hi" and events[-1].data["language"] == "hi"
    link = next(e.data for e in events if e.event == "ui")
    assert link["label"] == "Website widget खोलें →"
