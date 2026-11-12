"""TV6 review: a question about the menu is not a request for it, and caching a media id never
undoes the owner's settings."""

import pytest

from callobuzz_rag.channels.whatsapp import documents
from tests.unit.test_whatsapp_documents import add_document
from tests.unit.whatsapp_fixtures import inbound_text
from tests.unit.whatsapp_rig import rig_for
from tests.unit.whatsapp_world import WhatsAppWorld

pytestmark = pytest.mark.assistant_on


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


@pytest.mark.parametrize(
    "text",
    [
        "Is the menu veg?",
        "menu me paneer hai?",
        "Does the menu have vegan options",
        "what is on the menu",
        "क्या मेनू में पनीर है",
    ],
)
def test_a_question_about_a_document_is_not_a_request_for_it(text: str) -> None:
    assert not documents.is_document_request(text)


@pytest.mark.parametrize(
    "text", ["menu?", "price list?", "menu", "price list please", "Can you send the menu?"]
)
def test_a_short_ask_still_is(text: str) -> None:
    assert documents.is_document_request(text)


async def test_caching_a_media_id_keeps_the_owners_settings_and_locks_the_row(world) -> None:
    locked: list[bool] = []
    with rig_for(world) as rig:
        add_document(world, rig, "price-list.pdf")
        world.connection.settings = {**world.connection.settings, "human_pause_hours": 6}
        real = rig.deps().session_factory

        def spying():
            ctx = real()
            inner = ctx

            class Wrap:
                async def __aenter__(self):
                    session = await inner.__aenter__()
                    original = session.execute

                    async def execute(stmt, *a, **k):
                        if getattr(stmt, "_for_update_arg", None) is not None:
                            locked.append(True)
                        return await original(stmt, *a, **k)

                    session.execute = execute
                    return session

                async def __aexit__(self, *exc):
                    return await inner.__aexit__(*exc)

            return Wrap()

        deps = rig.deps()
        deps.session_factory = spying
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.B1", text="price list please"))
        await rig.run(thread_id, deps)
    settings = world.connection.settings
    assert settings["human_pause_hours"] == 6 and settings["shareable_document_ids"]
    assert settings["media_cache"] and locked
