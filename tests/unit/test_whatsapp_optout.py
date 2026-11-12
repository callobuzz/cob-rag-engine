import pytest

from callobuzz_rag.channels.whatsapp.optout import opt_out_intent


@pytest.mark.parametrize(
    "text",
    [
        "STOP",
        "stop",
        "Stop!",
        "Unsubscribe",
        "band karo",
        "message mat karo",
        "मैसेज मत करो",
        "रोकें",
        "बंद करो",
        "please stop messaging me",
        "cancel all messages",
        "Stop 🙏",
        "STOP\ufe0f",
        "band karo please",
        "don\u2018t message me",
        "roko",
        "unsubscribe me",
        "Unsubscribe me please",
        "opt me out",
        "No more messages",
        "no more messages please",
        "mat bhejo",
        "Mujhe mat bhejo",
        "stop it",
        "Please stop this",
    ],
)
def test_opt_out_phrases(text: str) -> None:
    assert opt_out_intent(text) == "stop"


@pytest.mark.parametrize("text", ["START", "start", "shuru karo"])
def test_opt_in_again(text: str) -> None:
    assert opt_out_intent(text) == "start"


@pytest.mark.parametrize(
    "text",
    [
        "Does the bus stop near your clinic?",
        "stop bleeding after extraction?",
        "How do I stop teeth grinding at night",
        "",
        "   ",
        "Root canal ka kitna charge hai?",
        "Cancel",
        "cancel my appointment",
        "no more messages from the clinic about my report?",
        "stop it from hurting when I chew",
        "mat bhejo report kal",
    ],
)
def test_questions_with_the_word_stop_are_questions(text: str) -> None:
    assert opt_out_intent(text) is None


# ------------------------------------------------------------------ the chat (TV6 Task 5)


@pytest.mark.assistant_on
async def test_stop_records_confirms_once_and_a_later_question_is_still_answered() -> None:
    from callobuzz_rag.db.models.channel import ChannelThread
    from tests.unit.whatsapp_fixtures import inbound_text
    from tests.unit.whatsapp_rig import rig_for
    from tests.unit.whatsapp_world import WhatsAppWorld

    world = WhatsAppWorld()
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.S1", text="STOP"))
        await rig.run(thread_id)
        thread = world.db.all(ChannelThread)[0]
        assert thread.opted_out_at is not None
        assert len(rig.texts()) == 1 and "will not send you any more messages" in rig.texts()[0]
        assert rig.calls == [] and rig.default_llm.calls == [] and rig.io.meter.await_count == 0

        await rig.deliver(inbound_text(wamid="wamid.Q1", text="How much is home collection?"))
        await rig.run(thread_id)
    assert len(rig.texts()) == 2 and "Rs 150" in rig.texts()[1]  # they wrote to us: answered
    assert thread.opted_out_at is not None  # still opted out of business-initiated messages


@pytest.mark.assistant_on
async def test_start_clears_the_opt_out_and_thanks() -> None:
    from callobuzz_rag.db.models.channel import ChannelThread
    from tests.unit.whatsapp_fixtures import inbound_text
    from tests.unit.whatsapp_rig import rig_for
    from tests.unit.whatsapp_world import WhatsAppWorld

    world = WhatsAppWorld()
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.S1", text="stop"))
        await rig.run(thread_id)
        await rig.deliver(inbound_text(wamid="wamid.S2", text="START"))
        await rig.run(thread_id)
    assert world.db.all(ChannelThread)[0].opted_out_at is None
    assert "may hear from us again" in rig.texts()[1]


@pytest.mark.assistant_on
async def test_a_hindi_stop_is_confirmed_in_hindi() -> None:
    from tests.unit.whatsapp_fixtures import inbound_text
    from tests.unit.whatsapp_rig import rig_for
    from tests.unit.whatsapp_world import WhatsAppWorld

    world = WhatsAppWorld()
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.S1", text="रोकें"))
        await rig.run(thread_id)
    assert "संदेश नहीं भेजेंगे" in rig.texts()[0]


@pytest.mark.assistant_on
async def test_a_question_with_the_word_stop_is_answered_not_treated_as_stop() -> None:
    from callobuzz_rag.db.models.channel import ChannelThread
    from tests.unit.whatsapp_fixtures import inbound_text
    from tests.unit.whatsapp_rig import rig_for
    from tests.unit.whatsapp_world import WhatsAppWorld

    world = WhatsAppWorld()
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(
            inbound_text(wamid="wamid.Q1", text="Does the bus stop near home collection?")
        )
        await rig.run(thread_id)
    assert world.db.all(ChannelThread)[0].opted_out_at is None
    assert rig.calls == ["cap", "billing", "fair_use"]
