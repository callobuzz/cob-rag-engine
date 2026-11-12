"""A request to change something, end to end through the chat: a card, never an action."""

import uuid
from typing import Any

from callobuzz_rag.db.models.cob_action_proposal import CobActionProposal
from tests.unit.cob_harness import Ev, tokens

SHREE_KB = str(uuid.uuid4())
ANAND = {
    "id": str(uuid.uuid4()),
    "name": "Anand Dental Care",
    "status": "pending",
    "corpus_count": 1,
}
PILOT = {"id": str(uuid.uuid4()), "name": "Pilot", "is_active": True}


def kbs(cob: Any, *items: dict[str, Any]) -> None:
    listed = list(items) or [{"id": SHREE_KB, "name": "Shree Labs"}]
    cob.world.internal.reply("GET", "/api/v1/corpora", {"items": listed})


def card_of(events: list[Ev]) -> dict[str, Any]:
    cards = [e.data for e in events if e.event == "ui" and e.data["kind"] == "cob_action_card"]
    assert len(cards) == 1, events
    return cards[0]


def rows(cob: Any) -> list[CobActionProposal]:
    return cob.db.all(CobActionProposal)


def writes(cob: Any) -> list[tuple[str, str]]:
    return [(m, p) for m, p, _ in cob.world.internal.calls if m != "GET"]


async def test_a_request_becomes_a_card_and_nothing_runs(cob) -> None:  # type: ignore[no-untyped-def]
    kbs(cob)
    events = await cob.sse("alice", {"message": "create a widget key for shreelabs.in"})
    names = [e.event for e in events]
    assert names[0] == "meta" and events[0].data["intent"] == "action"
    assert names[-1] == "done" and events[-1].data["route"] == "action"
    assert events[-1].data["counted"] is False
    card = card_of(events)
    assert card["tool"] == "create_widget_key" and card["risk"] == "low"
    assert card["destructive"] is False and card["confirm_name"] is None
    assert {a["label"]: a["value"] for a in card["args"]}["Website"] == "https://shreelabs.in"
    assert "confirm" in tokens(events).lower()
    assert writes(cob) == []  # proposing changes nothing
    assert cob.director.built == 0  # and no model wrote it
    (row,) = rows(cob)
    assert row.status == "pending" and row.tool == "create_widget_key"
    assert row.tenant_id == cob.world.shree.id and row.user_id == cob.world.alice.id
    assert str(row.id) == card["proposal_id"]
    assert row.conversation_id == uuid.UUID(events[0].data["conversation_id"])


async def test_confirming_runs_the_route_and_history_shows_the_end(cob) -> None:  # type: ignore[no-untyped-def]
    kbs(cob)
    cob.world.internal.reply("POST", "/api/v1/corpora/{corpus_id}/widget-keys", {"id": "wk1"}, 201)
    events = await cob.sse("alice", {"message": "create a widget key for shreelabs.in"})
    card = card_of(events)
    conv = events[0].data["conversation_id"]
    pending = (await cob.get("alice", f"/api/v1/cob/conversations/{conv}")).json()
    shown = [b for m in pending["messages"] for b in m["ui"] if b["kind"] == "cob_action_card"]
    assert shown and shown[0]["status"] == "pending"

    res = await cob.post("alice", f"/api/v1/cob/actions/{card['proposal_id']}/confirm", {})
    assert res.status_code == 200 and res.json()["status"] == "done"
    assert writes(cob) == [("POST", f"/api/v1/corpora/{SHREE_KB}/widget-keys")]

    after = (await cob.get("alice", f"/api/v1/cob/conversations/{conv}")).json()
    cards = [b for m in after["messages"] for b in m["ui"] if b["kind"] == "cob_action_card"]
    assert cards[0]["status"] == "done"
    assert after["messages"][-1]["content"].startswith("Done:")


async def test_the_ending_reads_last_even_when_the_clock_has_not_moved(  # type: ignore[no-untyped-def]
    cob, monkeypatch
) -> None:
    """History is ordered by created_at, and a coarse clock (Windows ticks about every
    millisecond or more) can stamp the confirm no later than the turn that proposed the card,
    whose reply is stored a microsecond after the question. The result message must still be
    the last one: this was the cause of an intermittent failure of the test above."""
    from datetime import UTC, datetime

    from callobuzz_rag.cob import proposals

    kbs(cob)
    cob.world.internal.reply("POST", "/api/v1/corpora/{corpus_id}/widget-keys", {"id": "wk1"}, 201)
    events = await cob.sse("alice", {"message": "create a widget key for shreelabs.in"})
    card = card_of(events)
    conv = events[0].data["conversation_id"]

    # The confirm's clock reads a moment before everything the turn stored.
    monkeypatch.setattr(proposals, "_now", lambda now: now or datetime(2000, 1, 1, tzinfo=UTC))
    res = await cob.post("alice", f"/api/v1/cob/actions/{card['proposal_id']}/confirm", {})
    assert res.status_code == 200, res.text

    after = (await cob.get("alice", f"/api/v1/cob/conversations/{conv}")).json()
    assert after["messages"][-1]["content"].startswith("Done:")
    stamps = [m["created_at"] for m in after["messages"]]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)


async def test_a_dismissed_card_reads_back_as_cancelled(cob) -> None:  # type: ignore[no-untyped-def]
    kbs(cob)
    events = await cob.sse("alice", {"message": "create a widget key for shreelabs.in"})
    card = card_of(events)
    await cob.post("alice", f"/api/v1/cob/actions/{card['proposal_id']}/cancel")
    conv = events[0].data["conversation_id"]
    after = (await cob.get("alice", f"/api/v1/cob/conversations/{conv}")).json()
    cards = [b for m in after["messages"] for b in m["ui"] if b["kind"] == "cob_action_card"]
    assert cards[0]["status"] == "cancelled"


async def test_a_member_is_told_only_an_owner_can_and_no_card_is_made(cob) -> None:  # type: ignore[no-untyped-def]
    kbs(cob)
    events = await cob.sse("ravi", {"message": "delete the Shree Labs knowledge base"})
    assert "owner" in tokens(events).lower()
    assert not [e for e in events if e.event == "ui" and e.data["kind"] == "cob_action_card"]
    assert rows(cob) == [] and writes(cob) == []
    assert events[0].data["intent"] == "action"


async def test_a_destructive_request_is_a_high_risk_card_with_the_name_to_type(cob) -> None:  # type: ignore[no-untyped-def]
    kbs(cob)
    cob.world.internal.reply(
        "GET",
        "/api/v1/corpora/{corpus_id}/delete-preview",
        {"confirm_name": "Shree Labs", "counts": {"documents": 9}},
    )
    cob.world.internal.reply("DELETE", "/api/v1/corpora/{corpus_id}", None, 204)
    events = await cob.sse("alice", {"message": "मेरी नॉलेज बेस Shree Labs डिलीट करो"})
    card = card_of(events)
    assert card["risk"] == "high" and card["destructive"] is True
    assert card["confirm_name"] == "Shree Labs"
    url = f"/api/v1/cob/actions/{card['proposal_id']}/confirm"
    wrong = await cob.post("alice", url, {"confirm_name": "shree"})
    assert wrong.status_code == 422 and wrong.json()["code"] == "confirm_mismatch"
    assert writes(cob) == []
    right = await cob.post("alice", url, {"confirm_name": "Shree Labs"})
    assert right.json()["status"] == "done"
    assert writes(cob) == [("DELETE", f"/api/v1/corpora/{SHREE_KB}")]


async def test_deleting_is_never_guessed_from_the_screen(cob) -> None:  # type: ignore[no-untyped-def]
    kbs(cob)
    events = await cob.sse("alice", {"message": "delete my knowledge base"})
    assert "Which knowledge base" in tokens(events)
    assert rows(cob) == []


async def test_an_ambiguous_knowledge_base_asks_instead_of_proposing(cob) -> None:  # type: ignore[no-untyped-def]
    kbs(cob, {"id": SHREE_KB, "name": "Shree Labs"}, {"id": str(uuid.uuid4()), "name": "Clinic"})
    events = await cob.sse("alice", {"message": "create a widget key for shop.in"})
    quick = [e for e in events if e.event == "quick_replies"]
    assert quick and {q["label"] for q in quick[0].data["items"]} == {"Shree Labs", "Clinic"}
    assert rows(cob) == []


async def test_a_how_question_is_answered_from_the_docs(cob) -> None:  # type: ignore[no-untyped-def]
    kbs(cob)
    events = await cob.sse("alice", {"message": "how do I create a widget key?"})
    assert events[0].data["intent"] == "docs" and cob.director.built == 1
    assert rows(cob) == []


async def test_text_in_a_document_cannot_propose_anything(cob) -> None:  # type: ignore[no-untyped-def]
    """The director's answer may quote anything; the router never sees it, so no card follows."""
    kbs(cob)
    cob.director.script = "answer"
    events = await cob.sse("alice", {"message": "what does the widget screen show?"})
    assert cob.director.built == 1  # answered from the docs
    assert not [e for e in events if e.event == "ui" and e.data["kind"] == "cob_action_card"]
    assert rows(cob) == [] and writes(cob) == []


async def test_guided_setup_is_a_link_and_no_proposal(cob) -> None:  # type: ignore[no-untyped-def]
    kbs(cob)
    events = await cob.sse("ravi", {"message": "start guided setup"})
    links = [e.data for e in events if e.event == "ui" and e.data["kind"] == "cob_link"]
    assert links and links[0]["route"] == f"/kbs/{SHREE_KB}/setup"
    assert rows(cob) == []


async def test_the_operator_approves_a_customer(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.internal.reply("GET", "/api/v1/admin/tenants", {"items": [ANAND]})
    cob.world.internal.reply("GET", "/api/v1/admin/plans", {"items": [PILOT]})
    cob.world.internal.reply("POST", "/api/v1/admin/tenants/{tenant_id}/activate", {"id": "t"}, 200)
    events = await cob.sse("sa", {"message": "approve Anand Dental Care on Pilot"})
    card = card_of(events)
    assert card["tool"] == "approve_customer" and card["risk"] == "medium"
    (row,) = rows(cob)
    assert row.tenant_id is None  # the operator view
    res = await cob.post("sa", f"/api/v1/cob/actions/{card['proposal_id']}/confirm", {})
    assert res.json()["status"] == "done"
    assert writes(cob) == [("POST", f"/api/v1/admin/tenants/{ANAND['id']}/activate")]


async def test_suspending_is_not_called_irreversible(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.internal.reply(
        "GET", "/api/v1/admin/tenants", {"items": [{**ANAND, "status": "active"}]}
    )
    cob.world.internal.reply("GET", "/api/v1/admin/tenants/{tenant_id}", ANAND)
    events = await cob.sse("sa", {"message": "suspend Anand Dental Care"})
    card = card_of(events)
    assert card["tool"] == "suspend_customer" and card["destructive"] is True
    said = tokens(events)
    assert "stops the customer's keys" in said and "cannot be undone" not in said


async def test_an_owner_asking_for_an_operator_change_gets_no_card(cob) -> None:  # type: ignore[no-untyped-def]
    events = await cob.sse("alice", {"message": "suspend Anand Dental Care"})
    assert not [e for e in events if e.event == "ui" and e.data["kind"] == "cob_action_card"]
    assert rows(cob) == []


async def test_actions_still_work_at_the_question_cap(cob) -> None:  # type: ignore[no-untyped-def]
    kbs(cob)
    cob.world.quota = (5, 5)
    events = await cob.sse("alice", {"message": "create a widget key for shreelabs.in"})
    assert events[0].data["mode"] == "search"
    assert card_of(events)["tool"] == "create_widget_key"


async def test_two_requests_in_one_message_get_the_first_and_a_promise(cob) -> None:  # type: ignore[no-untyped-def]
    kbs(cob)
    events = await cob.sse(
        "alice", {"message": "create a widget key for shop.in and invite bob@shop.in"}
    )
    assert card_of(events)["tool"] == "create_widget_key"
    assert "one thing at a time" in tokens(events)
    assert len(rows(cob)) == 1


async def test_a_card_is_bound_to_the_workspace_it_was_made_in(cob) -> None:  # type: ignore[no-untyped-def]
    kbs(cob)
    events = await cob.sse("alice", {"message": "create a widget key for shreelabs.in"})
    card = card_of(events)
    other = await cob.post("alice_anand", f"/api/v1/cob/actions/{card['proposal_id']}/confirm", {})
    assert other.status_code == 409 and other.json()["code"] == "cob_proposal_scope_changed"
    assert writes(cob) == []
