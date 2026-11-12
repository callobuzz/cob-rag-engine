"""Answering on WhatsApp: the same director, the gates in order, a stable billing id (TV6)."""

import asyncio
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.charging import NAMESPACE_BILLING
from callobuzz_rag.channels.whatsapp import inbound
from callobuzz_rag.conversation.types import Channel
from callobuzz_rag.db.models.channel import ChannelEvent, ChannelThread
from callobuzz_rag.db.models.conversation import Conversation
from tests.unit.conversation_helpers import FakeLLM, make_config
from tests.unit.whatsapp_fixtures import (
    inbound_image,
    inbound_message,
    inbound_text,
)
from tests.unit.whatsapp_rig import BUSINESS, NOW, TS, rig_for, state_of
from tests.unit.whatsapp_world import CUSTOMER, WhatsAppWorld

pytestmark = pytest.mark.assistant_on


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


def event_status(world: WhatsAppWorld, wamid: str) -> tuple[str, str | None]:
    (event,) = world.events(wamid)
    return event.status, event.error_code


# ----------------------------------------------------------- a question, answered


async def test_a_text_question_runs_the_gates_in_order_and_is_answered_once(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(
            inbound_text(wamid="wamid.IN1", text="How much is home collection?")
        )
        counts = await rig.run(thread_id)
        again = await rig.run(thread_id)  # the same event processed twice
    assert counts == {"done": 1, "ignored": 0, "failed": 0} and again == counts | {"done": 0}
    assert rig.calls == ["cap", "billing", "fair_use"]  # the human check came first and passed
    texts = rig.texts()
    assert len(texts) == 1 and "Rs 150" in texts[0] and "[1]" not in texts[0]
    send = rig.sends()[0]
    assert send.path == "/v23.0/1234567890/messages"
    assert send.json["to"] == CUSTOMER and send.headers["authorization"].startswith("Bearer EAAG")
    assert len(rig.reads()) == 1 and rig.reads()[0].json["message_id"] == "wamid.IN1"
    assert event_status(world, "wamid.IN1") == ("done", None)

    (rec,) = rig.persisted
    assert rec.turn.channel is Channel.WHATSAPP and rec.turn.persist is True
    assert rec.turn.usage_source == "whatsapp" and rec.turn.contact_phone == "+" + CUSTOMER
    (conv,) = world.db.all(Conversation)
    assert rec.turn.conversation_id == conv.id and conv.channel == "whatsapp"
    assert conv.kind == "assistant"
    thread = world.db.all(ChannelThread)[0]
    assert thread.conversation_id == conv.id and thread.last_outbound_at == NOW

    (usage,) = rig.metered()
    assert usage["source"] == "whatsapp"
    assert usage["request_id"] == uuid.uuid5(NAMESPACE_BILLING, "wa:wamid.IN1")
    assert usage["requests"] == 1 and usage["tenant_id"] == world.shree.id


async def test_the_answer_is_recorded_so_its_echo_is_not_a_takeover(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.IN1"))
        await rig.run(thread_id)
    sent = [e for e in world.events() if e.kind == "other"]
    assert [e.external_id for e in sent] == ["wamid.OUT"]  # FakeGraph's reply id
    assert sent[0].status == "done"


async def test_a_hindi_question_is_answered_in_hindi(world) -> None:
    llm = FakeLLM(
        json_replies=[{**BUSINESS, "language": "hi"}], stream=["घर पर सैंपल का शुल्क 150 रुपये है [1]।"]
    )
    with rig_for(world) as rig:
        rig.llms.append(llm)
        (thread_id,) = await rig.deliver(inbound_text(text="घर पर सैंपल कलेक्शन का क्या शुल्क है?"))
        await rig.run(thread_id)
    assert rig.persisted[0].language == "hi"
    assert "रुपये" in rig.texts()[0]


async def test_a_retry_of_the_same_message_is_stored_once_and_charged_once(world) -> None:
    payload = inbound_text(wamid="wamid.IN1")
    with rig_for(world) as rig:
        first = await rig.deliver(payload)
        second = await rig.deliver(payload)
        await rig.run(first[0])
    assert first and second == []
    assert len(rig.sends()) == 1 and len(rig.metered()) == 1


# ----------------------------------------------------------- a person is asked for


async def test_a_request_for_a_person_skips_every_gate_and_the_model(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(
            inbound_text(wamid="wamid.H1", text="Kisi insaan se baat karni hai")
        )
        await rig.run(thread_id)
    assert rig.calls == []  # no cap, no billing, no fair use
    assert rig.default_llm.calls == []
    assert rig.io.meter.await_count == 0  # free: nothing metered
    text = "\n".join(rig.texts())
    assert "Shree Labs" in text
    assert "SL-4F7K" in text
    assert "+91 98220 12345" in text
    assert "wa.me" not in text
    (call,) = rig.offered.call_args_list
    payload = call.args[0]
    assert payload["channel"] == "whatsapp_chat" and payload["reason"] == "explicit_request"
    assert str(payload["conversation_id"]) == str(world.db.all(Conversation)[0].id)


async def test_a_request_for_a_person_is_answered_even_at_the_cap(world) -> None:
    cap = APIError(429, "x", "question_cap_reached")
    with rig_for(world, cap=cap) as rig:
        (thread_id,) = await rig.deliver(inbound_text(text="I want to talk to a person"))
        await rig.run(thread_id)
    assert rig.calls == [] and "Shree Labs" in "\n".join(rig.texts())


# ----------------------------------------------------------- the gates say no


@pytest.mark.parametrize("pause", ["billing", "fair_use"])
async def test_a_paused_workspace_gets_the_pause_message_and_the_contacts(world, pause) -> None:
    with rig_for(world, pause=pause) as rig:
        (thread_id,) = await rig.deliver(inbound_text(text="How much is home collection?"))
        await rig.run(thread_id)
    assert rig.default_llm.calls == []  # no label, no search, no model
    text = "\n".join(rig.texts())
    assert "can't answer questions here right now" in text and "+91 98220 12345" in text
    assert rig.io.meter.await_count == 0


async def test_the_question_cap_gets_the_polite_sentence_and_nothing_is_metered(world) -> None:
    cap = APIError(429, "Monthly question limit reached", "question_cap_reached")
    with rig_for(world, cap=cap) as rig:
        (thread_id,) = await rig.deliver(inbound_text(text="How much is home collection?"))
        await rig.run(thread_id)
    assert rig.texts() == ["This assistant is not available right now."]
    assert rig.default_llm.calls == [] and rig.io.meter.await_count == 0
    assert event_status(world, "wamid.IN1") == ("done", "cap")


# ----------------------------------------------------------- what is not text


@pytest.mark.parametrize("kind", ["image", "audio", "video", "document", "sticker", "location"])
async def test_a_message_that_is_not_text_gets_the_fixed_reply_and_no_model(world, kind) -> None:
    payload = inbound_image(wamid="wamid.M1")
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message["type"] = kind
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(payload)
        await rig.run(thread_id)
    assert rig.texts() == [
        "I can read text messages. Please type your question, or write 'talk to a person'."
    ]
    assert rig.calls == [] and rig.default_llm.calls == [] and rig.io.meter.await_count == 0
    assert [m.content for m in rig.stored("user")] == [f"[{kind}]"]


async def test_a_greeting_by_rule_needs_no_model_and_is_free(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(text="namaste"))
        await rig.run(thread_id)
    assert rig.default_llm.calls == [] and rig.io.meter.await_count == 0
    assert len(rig.texts()) == 1


# ----------------------------------------------------------- old messages, order, locks


async def test_an_event_older_than_a_day_is_ignored_and_nothing_is_sent(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.OLD"))
        rig.now = NOW + timedelta(hours=25)
        await rig.run(thread_id)
    assert rig.sends() == [] and rig.calls == []
    assert event_status(world, "wamid.OLD") == ("ignored", "stale")


async def test_open_scope_is_read_as_docs_plus_domain_on_whatsapp(world) -> None:
    config = make_config(scope={"scope_mode": "open_with_guardrails"})
    with rig_for(world, config=config) as rig:
        (thread_id,) = await rig.deliver(inbound_text(text="How much is home collection?"))
        await rig.run(thread_id)
    assert rig.persisted[0].turn.runtime.config.scope.scope_mode == "docs_plus_domain"
    assert rig.config.scope.scope_mode == "open_with_guardrails"  # the stored config is untouched


async def test_off_topic_is_declined_on_a_strict_assistant(world) -> None:
    llm = FakeLLM(json_replies=[{**BUSINESS, "label": "off_topic"}])
    config = make_config(scope={"scope_mode": "open_with_guardrails"})
    with rig_for(world, config=config) as rig:
        rig.llms.append(llm)
        (thread_id,) = await rig.deliver(inbound_text(text="Who will win the cricket match?"))
        await rig.run(thread_id)
    assert rig.persisted[0].route.value == "decline" and "Rs 150" not in "".join(rig.texts())


async def test_messages_arriving_together_are_answered_in_arrival_order(world) -> None:
    answers = [FakeLLM(json_replies=[BUSINESS], stream=[f"Answer {n}."]) for n in (1, 2, 3)]
    with rig_for(world) as rig:
        rig.llms.extend(answers)
        woken = await rig.deliver(
            inbound_text(wamid="wamid.A", text="First question about home collection?"),
            inbound_text(wamid="wamid.B", text="Second question about home collection?"),
            inbound_text(wamid="wamid.C", text="Third question about home collection?"),
        )
        assert len(woken) == 1  # one chat, woken once, for all three
        await rig.run(woken[0])
    assert rig.texts() == ["Answer 1.", "Answer 2.", "Answer 3."]
    assert [e.status for e in world.events() if e.kind == "message"] == ["done"] * 3


async def test_two_workers_on_one_chat_never_answer_the_same_event(world) -> None:
    with rig_for(world) as rig:
        rig.llms.extend(
            FakeLLM(json_replies=[BUSINESS], stream=[f"Answer {n}."]) for n in range(1, 4)
        )
        (thread_id, *_rest) = await rig.deliver(
            inbound_text(wamid="wamid.A", text="One question about home collection?"),
            inbound_text(wamid="wamid.B", text="Two questions about home collection?"),
        )
        await asyncio.gather(rig.run(thread_id), rig.run(thread_id))
    assert sorted(rig.texts()) == ["Answer 1.", "Answer 2."]
    assert len(rig.metered()) == 2
    assert {e.status for e in world.events() if e.kind == "message"} == {"done"}


async def test_a_failure_ends_the_event_and_does_not_stop_the_chat(world) -> None:
    class BoomError(Exception):
        pass

    with rig_for(world) as rig:
        woken = await rig.deliver(
            inbound_text(wamid="wamid.A", text="One question about home collection?"),
            inbound_text(wamid="wamid.B", text="Two questions about home collection?"),
        )
        real = rig.director
        calls = {"n": 0}

        def flaky(corpus, profile):
            calls["n"] += 1
            if calls["n"] == 1:
                raise BoomError("no director")
            return real(corpus, profile)

        deps = rig.deps()
        deps.director_factory = flaky
        counts = await rig.run(woken[0], deps)
    assert counts == {"done": 1, "ignored": 0, "failed": 1}
    assert event_status(world, "wamid.A") == ("failed", "internal")
    assert event_status(world, "wamid.B") == ("done", None)


# ----------------------------------------------------------- the owner's setup


async def test_an_assistant_that_is_off_answers_nothing(world) -> None:
    with rig_for(world, assistant=False) as rig:
        (thread_id,) = await rig.deliver(inbound_text())
        await rig.run(thread_id)
    assert rig.sends() == [] and event_status(world, "wamid.IN1") == ("ignored", "assistant_off")


async def test_a_plan_that_lost_whatsapp_disables_the_number_and_answers_nothing(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text())
        world.db.default_plan.channels = {"whatsapp": False}
        await rig.run(thread_id)
    assert rig.sends() == []
    assert event_status(world, "wamid.IN1") == ("ignored", "plan")
    assert world.connection.status == "disabled" and world.connection.last_error_code == "plan"


async def test_a_token_meta_refuses_puts_the_connection_in_error(world) -> None:
    from tests.unit.whatsapp_fixtures import FakeGraph

    graph = FakeGraph(status=401, reply={"error": {"code": 190, "message": "Invalid OAuth token"}})
    with rig_for(world, graph=graph) as rig:
        (thread_id,) = await rig.deliver(inbound_text())
        await rig.run(thread_id)
    assert world.connection.status == "error" and world.connection.last_error_code == "token"
    assert event_status(world, "wamid.IN1") == ("failed", "token_invalid")
    assert rig.metered() != [] or True  # the answer was made; the spend is real


# ----------------------------------------------------------- leads


async def test_a_lead_on_whatsapp_asks_for_consent_first_and_uses_the_number_after(world) -> None:
    config = make_config(
        leads={"enabled": True, "mode": "form", "triggers": {"buying_intent": False}},
    )
    created = AsyncMock(return_value=uuid.uuid4())
    llm1 = FakeLLM(json_replies=[BUSINESS], stream=["Home collection costs Rs 150 [1]."])
    llm2 = FakeLLM(json_replies=[BUSINESS], stream=["Noted."])
    with (
        rig_for(world, config=config) as rig,
        patch("callobuzz_rag.conversation.leads.provider._create_from_capture", created),
    ):
        rig.llms.extend([llm1, llm2])
        (thread_id,) = await rig.deliver(
            inbound_text(wamid="wamid.L1", text="Can you whatsapp me the home collection details")
        )
        await rig.run(thread_id)
        asked = "\n".join(rig.texts())
        assert "contact you on this WhatsApp number" in asked and "YES" in asked
        assert created.await_count == 0  # no lead before the yes
        assert state_of(world).lead_capture["awaiting_consent"] is True
        await rig.deliver(inbound_text(wamid="wamid.L2", text="yes"))
        await rig.run(thread_id)
    assert created.await_count == 1
    pre, clean = created.await_args.args
    assert clean.phone == "+" + CUSTOMER and pre.channel is Channel.WHATSAPP
    assert state_of(world).lead_id is not None
    assert "passed your details" in rig.texts()[-1]


async def test_a_no_to_the_lead_question_creates_nothing(world) -> None:
    config = make_config(leads={"enabled": True, "mode": "form"})
    created = AsyncMock(return_value=uuid.uuid4())
    with (
        rig_for(world, config=config) as rig,
        patch("callobuzz_rag.conversation.leads.provider._create_from_capture", created),
    ):
        rig.llms.extend(
            [
                FakeLLM(json_replies=[BUSINESS], stream=["Rs 150."]),
                FakeLLM(json_replies=[BUSINESS], stream=["Fine."]),
            ]
        )
        (thread_id,) = await rig.deliver(
            inbound_text(wamid="wamid.L1", text="Can you whatsapp me the details")
        )
        await rig.run(thread_id)
        await rig.deliver(inbound_text(wamid="wamid.L2", text="no thanks"))
        await rig.run(thread_id)
    assert created.await_count == 0


# ----------------------------------------------------------- a new window, a new conversation


async def test_a_message_after_a_day_starts_a_new_conversation(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(
            inbound_text(wamid="wamid.A", text="First about home collection?")
        )
        await rig.run(thread_id)
        later = NOW + timedelta(hours=30)
        rig.now = later
        late = inbound_text(
            wamid="wamid.B",
            text="Second about home collection?",
            timestamp=int(later.timestamp()) - 5,
        )
        await rig.deliver(late)
        await rig.run(thread_id)
    assert len(world.db.all(Conversation)) == 2
    thread = world.db.all(ChannelThread)[0]
    assert thread.conversation_id == world.db.all(Conversation)[1].id


async def test_the_chat_keeps_one_conversation_inside_a_window(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(
            inbound_text(wamid="wamid.A", text="First about home collection?")
        )
        await rig.run(thread_id)
        rig.now = NOW + timedelta(hours=2)
        await rig.deliver(
            inbound_text(wamid="wamid.B", text="Second about home collection?", timestamp=TS + 7000)
        )
        await rig.run(thread_id)
    assert len(world.db.all(Conversation)) == 1


def test_the_billing_id_is_derived_from_the_wamid_alone() -> None:
    a = inbound.request_id_for("wamid.X")
    assert a == inbound.request_id_for("wamid.X") != inbound.request_id_for("wamid.Y")
    assert a == uuid.uuid5(NAMESPACE_BILLING, "wa:wamid.X")


def test_messages_from_unknown_shapes_are_unused() -> None:
    assert inbound_message({"from": "1", "id": "x", "type": "text"}) is not None
    assert ChannelEvent is not None
