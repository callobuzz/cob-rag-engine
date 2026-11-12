"""Deep links, screen starters and the administrator-only reply."""

import uuid

import pytest

from callobuzz_rag.api.schemas.cob import CobContext
from callobuzz_rag.cob.config import generic_starters
from callobuzz_rag.cob.events import deep_link, fill_route
from callobuzz_rag.cob.kb import load_docs_index
from callobuzz_rag.cob.types import CobRole
from tests.unit.cob_harness import tokens

KB = uuid.uuid4()


@pytest.fixture(scope="module")
def index():  # type: ignore[no-untyped-def]
    return load_docs_index()


def test_link_fills_kb_param_from_context(index) -> None:  # type: ignore[no-untyped-def]
    link = deep_link(
        index,
        cited=["screens/kb-detail"],
        role=CobRole.OWNER,
        context=CobContext(route="/playground", kb_id=KB),
        language="en",
    )
    assert link is not None
    assert link.route == f"/kbs/{KB}"
    assert link.label == "Open Knowledge base settings →"
    assert link.docs_slug is None


def test_link_takes_the_param_from_the_current_screen(index) -> None:  # type: ignore[no-untyped-def]
    link = deep_link(
        index,
        cited=["screens/kb-setup"],
        role=CobRole.OWNER,
        context=CobContext(route=f"/kbs/{KB}"),
        language="en",
    )
    assert link is not None and link.route == f"/kbs/{KB}/setup"


def test_link_to_an_admin_screen_is_never_for_an_owner(index) -> None:  # type: ignore[no-untyped-def]
    assert (
        deep_link(index, cited=["screens/costs"], role=CobRole.OWNER, context=None, language="en")
        is None
    )
    admin = deep_link(
        index, cited=["screens/costs"], role=CobRole.ADMIN, context=None, language="en"
    )
    assert admin is not None and admin.route == "/admin/costs"


def test_a_param_without_context_goes_to_the_list(index) -> None:  # type: ignore[no-untyped-def]
    link = deep_link(
        index, cited=["screens/customer-detail"], role=CobRole.ADMIN, context=None, language="en"
    )
    assert link is not None and link.route == "/admin/customers"
    assert fill_route("/crawler/$jobId", index, None) == "/crawler"


def test_the_first_visible_screen_wins_over_an_earlier_hidden_one(index) -> None:  # type: ignore[no-untyped-def]
    link = deep_link(
        index,
        cited=["screens/costs", "screens/widget"],
        role=CobRole.OWNER,
        context=None,
        language="en",
    )
    assert link is not None and link.route == "/widget"


def test_a_page_without_a_screen_links_to_the_docs(index) -> None:  # type: ignore[no-untyped-def]
    slug = next(
        s for s, p in index.pages.items() if p.route is None and p.audience.value == "public"
    )
    link = deep_link(index, cited=[slug], role=CobRole.MEMBER, context=None, language="en")
    assert link is not None and link.docs_slug == slug and link.route == f"/docs/{slug}"
    assert link.label.startswith("Read ")


def test_nothing_cited_is_no_link(index) -> None:  # type: ignore[no-untyped-def]
    assert deep_link(index, cited=[], role=CobRole.OWNER, context=None, language="en") is None
    assert (
        deep_link(index, cited=["no/such/page"], role=CobRole.OWNER, context=None, language="en")
        is None
    )


def test_the_label_follows_the_language(index) -> None:  # type: ignore[no-untyped-def]
    link = deep_link(
        index, cited=["screens/widget"], role=CobRole.OWNER, context=None, language="hi-Latn"
    )
    assert link is not None and link.label == "Website widget kholein →"


SERVER_RECORDS = [("screens/engine", 0.8), ("screens/widget", 0.4)]  # the server docs win


async def test_customer_asking_about_env_gets_the_administrator_reply(cob) -> None:  # type: ignore[no-untyped-def]
    cob.retriever.records = SERVER_RECORDS
    events = await cob.sse("alice", {"message": "Where do I set RAG_SESSION_SECRET in .env?"})
    text = tokens(events)
    assert "platform administrator" in text and "RAG_SESSION_SECRET=" not in text
    probe = cob.retriever.calls[0]
    assert probe["metadata_filters"] == {"audience": ["admin"]}  # a probe; its text never streams
    # the pages this person may read were probed too, and lost
    assert cob.retriever.calls[1]["metadata_filters"] == {"audience": ["customer", "public"]}
    assert probe["enable_hybrid"] is False and probe["top_k"] == 3
    assert cob.director.built == 0  # no model was called
    assert events[-1].data["counted"] is False and events[-1].data["route"] == "admin_managed"
    assert events[0].data["mode"] == "answer"


async def test_the_administrator_reply_is_stored_as_a_turn(cob) -> None:  # type: ignore[no-untyped-def]
    cob.retriever.records = SERVER_RECORDS
    events = await cob.sse("ravi", {"message": "Show me the docker compose file for the server"})
    conv = events[0].data["conversation_id"]
    res = await cob.get("ravi", f"/api/v1/cob/conversations/{conv}")
    messages = res.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert "platform administrator" in messages[1]["content"]


async def test_a_weak_admin_match_does_not_refuse(cob) -> None:  # type: ignore[no-untyped-def]
    cob.retriever.records = [("screens/engine", 0.2)]
    events = await cob.sse("alice", {"message": "Can I run this in docker?"})
    assert "platform administrator" not in tokens(events)
    assert cob.director.built == 1


async def test_an_ordinary_question_with_a_server_word_is_answered(cob) -> None:  # type: ignore[no-untyped-def]
    # "compose" and "Postgres" are not server questions on their own; the customer docs win
    # (or the pattern does not even match), and the director answers.
    cob.retriever.records = [("screens/widget", 0.9), ("screens/engine", 0.6)]
    for message in (
        "How do I compose a welcome message?",
        "Can I import my Postgres data?",
        "How do I deploy the engine to my website?",  # names a server thing, docs rank customer
    ):
        before = cob.director.built
        events = await cob.sse("alice", {"message": message})
        assert "platform administrator" not in tokens(events), message
        assert cob.director.built == before + 1, message
        assert events[-1].data["route"] != "admin_managed"


def test_the_server_patterns_are_narrow() -> None:
    from callobuzz_rag.cob.audience import mentions_admin_topic

    assert not mentions_admin_topic("How do I compose a welcome message?")
    assert not mentions_admin_topic("Can I import my Postgres data?")
    assert mentions_admin_topic("Show me the docker compose file")
    assert mentions_admin_topic("Edit compose.yml for me")
    assert mentions_admin_topic("What port does the Postgres container use?")


async def test_a_question_that_ends_in_no_answer_is_checked_against_the_admin_docs(cob) -> None:  # type: ignore[no-untyped-def]
    cob.director.script = "no_answer"
    events = await cob.sse("alice", {"message": "How do I tune the vector index?"})
    text = tokens(events)
    assert "platform administrator" in text
    assert "Which screen are you on?" not in text and "couldn't find that" not in text
    assert [c["metadata_filters"] for c in cob.retriever.calls] == [{"audience": ["admin"]}]
    assert events[-1].data["route"] == "admin_managed"
    # the stored reply is the text the person saw
    from callobuzz_rag.db.models.message import Message

    stored = next(m for m in cob.db.all(Message) if str(m.id) == events[-1].data["message_id"])
    assert "platform administrator" in stored.content


async def test_no_answer_without_an_admin_match_keeps_the_no_answer_reply(cob) -> None:  # type: ignore[no-untyped-def]
    cob.director.script = "no_answer"
    cob.retriever.records = [("screens/widget", 0.9)]  # nothing administrator-only
    events = await cob.sse("alice", {"message": "What is the weather?"})
    text = tokens(events)
    assert "couldn't find that" in text and "Which screen are you on?" in text
    assert "platform administrator" not in text


async def test_an_administrator_is_never_probed(cob) -> None:  # type: ignore[no-untyped-def]
    cob.director.script = "no_answer"
    events = await cob.sse("sa", {"message": "Where do I set RAG_LLM_MODEL in .env?"})
    assert cob.retriever.calls == []
    assert "couldn't find that" in tokens(events)


async def test_starters_follow_screen_and_role(cob) -> None:  # type: ignore[no-untyped-def]
    res = await cob.get("sa", "/api/v1/cob/starters?route=/admin/engine")
    admin = res.json()
    assert admin["page_slug"] == "screens/engine" and admin["starters"]
    assert len(admin["starters"]) <= 4
    owner = (await cob.get("alice", "/api/v1/cob/starters?route=/admin/engine")).json()
    assert owner["page_slug"] is None and owner["starters"] == generic_starters("en")


async def test_starters_of_a_customer_screen_and_a_param_route(cob) -> None:  # type: ignore[no-untyped-def]
    widget = (await cob.get("alice", "/api/v1/cob/starters?route=/widget")).json()
    assert widget["page_slug"] == "screens/widget" and widget["route"] == "/widget"
    kb = (await cob.get("ravi", f"/api/v1/cob/starters?route=/kbs/{KB}")).json()
    assert kb["page_slug"] == "screens/kb-detail"


async def test_starters_speak_the_language_when_the_screen_has_none(cob) -> None:  # type: ignore[no-untyped-def]
    res = await cob.get("alice", "/api/v1/cob/starters?route=/admin/engine&language=hi")
    assert res.json()["starters"] == generic_starters("hi")
    res = await cob.get("alice", "/api/v1/cob/starters?route=/x&language=fr")
    assert res.status_code == 422
