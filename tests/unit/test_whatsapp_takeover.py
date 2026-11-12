"""The owner steps in: an echo from the WhatsApp Business app, or the dashboard reply box (TV6)."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from callobuzz_rag.channels.whatsapp.graph import GraphClient
from callobuzz_rag.db.models.channel import ChannelThread
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message
from tests.unit.test_auth_routes import login, req
from tests.unit.whatsapp_fixtures import FakeGraph, echo_text, inbound_text
from tests.unit.whatsapp_rig import NOW, TS, rig_for
from tests.unit.whatsapp_world import CUSTOMER, WhatsAppWorld, whatsapp_app

pytestmark = pytest.mark.assistant_on


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


def thread_of(world: WhatsAppWorld) -> ChannelThread:
    return world.db.all(ChannelThread)[0]


# ------------------------------------------------------------------ an echo


async def test_an_echo_pauses_the_bot_for_that_chat_and_keeps_what_the_owner_wrote(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(
            inbound_text(wamid="wamid.IN1", text="Is Dr Rao in today?")
        )
        await rig.run(thread_id)
        rig.graph.requests.clear()
        await rig.deliver(echo_text(wamid="wamid.ECHO1", text="Yes, come at 5.", timestamp=TS + 30))
        await rig.run(thread_id)
    thread = thread_of(world)
    assert thread.paused_by == "echo"
    assert thread.bot_paused_until == rig_pause_end(TS + 30, hours=12)
    staff = rig.stored("staff")
    assert [m.content for m in staff] == ["Yes, come at 5."]
    assert rig.sends() == []  # the echo is not answered


def rig_pause_end(ts: int, hours: int):
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, tz=UTC) + timedelta(hours=hours)


async def test_messages_while_paused_are_kept_counted_and_not_answered(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(echo_text(wamid="wamid.ECHO1", timestamp=TS + 30))
        await rig.run(thread_id)
        await rig.deliver(inbound_text(wamid="wamid.IN1", text="What time?", timestamp=TS + 40))
        await rig.deliver(inbound_text(wamid="wamid.IN2", text="Hello?", timestamp=TS + 45))
        await rig.run(thread_id)
    assert rig.sends() == [] and rig.calls == [] and rig.default_llm.calls == []
    assert thread_of(world).unread_count == 2
    assert [m.content for m in rig.stored("user")] == ["What time?", "Hello?"]
    assert [e.status for e in world.events() if e.kind == "message"] == ["done", "done"]


async def test_after_the_pause_the_bot_answers_again(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(echo_text(wamid="wamid.ECHO1", timestamp=TS + 30))
        await rig.run(thread_id)
        rig.now = NOW + timedelta(hours=13)
        later = int(rig.now.timestamp()) - 5
        await rig.deliver(
            inbound_text(wamid="wamid.IN1", text="How much is home collection?", timestamp=later)
        )
        await rig.run(thread_id)
    assert len(rig.texts()) == 1 and "Rs 150" in rig.texts()[0]


async def test_the_pause_hours_are_the_numbers_own_setting(world) -> None:
    world.connection.settings = {"human_pause_hours": 3}
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(echo_text(wamid="wamid.ECHO1", timestamp=TS + 30))
        await rig.run(thread_id)
    assert thread_of(world).bot_paused_until == rig_pause_end(TS + 30, hours=3)


async def test_an_old_echo_pauses_nothing(world) -> None:
    with rig_for(world) as rig:
        rig.now = NOW + timedelta(hours=40)
        (thread_id,) = await rig.deliver(echo_text(wamid="wamid.ECHO1", timestamp=TS + 30))
        await rig.run(thread_id)
    assert thread_of(world).bot_paused_until is None
    assert [m.content for m in rig.stored("staff")] == ["Hi Asha, this is the clinic."]


async def test_the_echo_of_a_message_the_bot_sent_is_not_a_takeover(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(
            inbound_text(wamid="wamid.IN1", text="Home collection price?")
        )
        await rig.run(thread_id)
        woken = await rig.deliver(echo_text(wamid="wamid.OUT", text="Home collection costs Rs 150"))
    assert woken == [] and thread_of(world).bot_paused_until is None


async def test_what_the_owner_wrote_is_part_of_the_history_the_bot_continues(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(
            inbound_text(wamid="wamid.IN1", text="Is Dr Rao in today?")
        )
        await rig.run(thread_id)
        await rig.deliver(echo_text(wamid="wamid.ECHO1", text="Yes, come at 5.", timestamp=TS + 30))
        await rig.run(thread_id)
        rig.now = NOW + timedelta(hours=13)
        later = int(rig.now.timestamp()) - 5
        await rig.deliver(
            inbound_text(wamid="wamid.IN2", text="How much is home collection?", timestamp=later)
        )
        await rig.run(thread_id)
    seen = [m for _kind, messages, _kw in rig.default_llm.calls for m in messages]
    assert any(
        m.role == "assistant" and m.content == "[Shree Labs team] Yes, come at 5." for m in seen
    ), [m.content for m in seen]


# ------------------------------------------------------------------ the dashboard


async def _member_token(world: WhatsAppWorld, app, role: str = "member") -> str:
    user = world.user("asha@shree.example")
    world.member(user, world.shree, role)
    res = await login(app, user.email)
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def graph_for(fake: FakeGraph):
    client = GraphClient(
        settings=SimpleNamespace(
            whatsapp_graph_url="https://graph.facebook.com",
            whatsapp_graph_version="v23.0",
            whatsapp_app_id="1",
            whatsapp_app_secret="s",
        ),
        transport=fake.transport,
    )
    return patch("callobuzz_rag.channels.whatsapp.service.make_graph", lambda settings: client)


def open_thread(world: WhatsAppWorld, **fields) -> ChannelThread:
    from datetime import UTC, datetime

    recent = fields.pop("last_inbound_at", datetime.now(UTC) - timedelta(minutes=5))
    return world.thread(last_inbound_at=recent, **fields)


def reply_url(world: WhatsAppWorld, thread: ChannelThread) -> str:
    return f"/api/v1/corpora/{world.corpus.id}/whatsapp/threads/{thread.id}/reply"


async def test_the_reply_box_sends_stores_a_staff_message_and_pauses(world) -> None:
    thread = open_thread(world)
    fake = FakeGraph()
    world.db.default_plan.channels = {"whatsapp": True}
    with whatsapp_app(world) as app, graph_for(fake):
        token = await _member_token(world, app)  # a member may reply
        res = await req(
            app, "POST", reply_url(world, thread), token=token, json={"text": "We open at 9."}
        )
    assert res.status_code == 201, res.text
    assert res.json()["whatsapp_message_id"] == "wamid.OUT"
    (sent,) = [r for r in fake.requests if r.json and r.json.get("type") == "text"]
    assert sent.json["text"]["body"] == "We open at 9." and sent.json["to"] == CUSTOMER
    (staff,) = [m for m in world.db.all(Message) if m.role == "staff"]
    assert staff.content == "We open at 9." and str(staff.id) == res.json()["message_id"]
    assert thread.paused_by == "dashboard" and thread.bot_paused_until is not None
    (audit,) = world.audits("whatsapp.reply_sent")
    assert audit.details == {"corpus_id": str(world.corpus.id), "thread_id": str(thread.id)}
    assert "We open at 9." not in str(audit.details)
    assert world.db.all(Conversation)[0].channel == "whatsapp"


async def test_outside_the_window_the_reply_is_refused_and_nothing_is_sent(world) -> None:
    from datetime import UTC, datetime

    thread = open_thread(world, last_inbound_at=datetime.now(UTC) - timedelta(hours=25))
    fake = FakeGraph()
    world.db.default_plan.channels = {"whatsapp": True}
    with whatsapp_app(world) as app, graph_for(fake):
        token = await _member_token(world, app)
        res = await req(app, "POST", reply_url(world, thread), token=token, json={"text": "Hi"})
    assert res.status_code == 409 and res.json()["code"] == "whatsapp_window_closed"
    assert fake.requests == [] and thread.bot_paused_until is None


async def test_meta_refusing_a_reply_is_a_502_with_its_code(world) -> None:
    thread = open_thread(world)
    fake = FakeGraph(
        status=400, reply={"error": {"code": 131026, "message": "Message undeliverable"}}
    )
    world.db.default_plan.channels = {"whatsapp": True}
    with whatsapp_app(world) as app, graph_for(fake):
        token = await _member_token(world, app)
        res = await req(app, "POST", reply_url(world, thread), token=token, json={"text": "Hi"})
    assert res.status_code == 502 and res.json()["code"] == "whatsapp_send_failed"
    assert res.json()["meta_code"] == 131026


async def test_a_reply_over_4096_characters_is_refused(world) -> None:
    thread = open_thread(world)
    world.db.default_plan.channels = {"whatsapp": True}
    with whatsapp_app(world) as app, graph_for(FakeGraph()):
        token = await _member_token(world, app)
        res = await req(
            app, "POST", reply_url(world, thread), token=token, json={"text": "x" * 4097}
        )
    assert res.status_code == 422


async def test_the_plan_flag_gates_the_reply_box(world) -> None:
    thread = open_thread(world)
    world.db.default_plan.channels = {}
    with whatsapp_app(world) as app, graph_for(FakeGraph()):
        token = await _member_token(world, app)
        res = await req(app, "POST", reply_url(world, thread), token=token, json={"text": "Hi"})
    assert res.status_code == 403 and res.json()["code"] == "whatsapp_not_in_plan"


async def test_a_chat_of_another_knowledge_base_is_404(world) -> None:
    other = world.db.add(
        type(world.corpus)(
            name="Other", tenant_id=world.shree.id, qdrant_collection="c2", api_key_hash=""
        )
    )
    foreign = world.connect(phone_number_id="999888777", corpus=other)
    stray = world.db.add(
        ChannelThread(
            connection_id=foreign.id,
            corpus_id=other.id,
            tenant_id=world.shree.id,
            contact_hash="f" * 64,
            contact_encrypted=world.encryptor.encrypt("+1"),
        )
    )
    world.db.default_plan.channels = {"whatsapp": True}
    with whatsapp_app(world) as app, graph_for(FakeGraph()):
        token = await _member_token(world, app)
        res = await req(
            app,
            "POST",
            f"/api/v1/corpora/{world.corpus.id}/whatsapp/threads/{stray.id}/reply",
            token=token,
            json={"text": "Hi"},
        )
    assert res.status_code == 404 and res.json()["code"] == "whatsapp_thread_not_found"


async def test_resuming_lets_the_bot_answer_now_and_pausing_keeps_it_quiet(world) -> None:
    from datetime import UTC, datetime

    thread = open_thread(
        world, bot_paused_until=datetime.now(UTC) + timedelta(hours=5), paused_by="echo"
    )
    url = f"/api/v1/corpora/{world.corpus.id}/whatsapp/threads/{thread.id}"
    with whatsapp_app(world) as app:
        token = await _member_token(world, app)
        resumed = await req(app, "PATCH", url, token=token, json={"bot_paused": False})
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["bot_paused_until"] is None
        assert thread.bot_paused_until is None and thread.paused_by is None
        paused = await req(app, "PATCH", url, token=token, json={"bot_paused": True})
    assert paused.status_code == 200 and paused.json()["bot_paused_until"] is not None
    assert thread.paused_by == "dashboard"


async def test_opening_a_chat_marks_it_read_and_shows_the_conversation(world) -> None:
    from datetime import UTC, datetime

    thread = open_thread(
        world, unread_count=3, profile_name_encrypted=world.encryptor.encrypt("Asha Kulkarni")
    )
    conv = world.db.add(
        Conversation(corpus_id=world.corpus.id, channel="whatsapp", kind="assistant")
    )
    thread.conversation_id = conv.id
    base = datetime.now(UTC) - timedelta(minutes=5)
    for index, (role, text) in enumerate(
        [("user", "Is Dr Rao in?"), ("staff", "Yes, at 5."), ("assistant", "Anything else?")]
    ):
        world.db.add(
            Message(
                conversation_id=conv.id,
                role=role,
                content=text,
                created_at=base + timedelta(seconds=index),
            )
        )
    with whatsapp_app(world) as app:
        token = await _member_token(world, app)
        listing = await req(
            app, "GET", f"/api/v1/corpora/{world.corpus.id}/whatsapp/threads", token=token
        )
        detail = await req(
            app,
            "GET",
            f"/api/v1/corpora/{world.corpus.id}/whatsapp/threads/{thread.id}",
            token=token,
        )
    assert listing.json()["total"] == 1
    item = listing.json()["items"][0]
    assert item["name_masked"] == "Asha K." and item["unread_count"] == 3 and item["window_open"]
    assert CUSTOMER not in listing.text  # a list never shows the number
    body = detail.json()
    assert body["contact"] == "+" + CUSTOMER and body["unread_count"] == 0
    assert [m["role"] for m in body["messages"]] == ["user", "staff", "assistant"]
    assert thread.unread_count == 0
