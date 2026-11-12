"""TV6 review fixes on the routes: blank replies and the connection check need a plan."""

import pytest

from tests.unit.test_auth_routes import req
from tests.unit.test_whatsapp_takeover import _member_token, graph_for, open_thread, reply_url
from tests.unit.whatsapp_fixtures import FakeGraph
from tests.unit.whatsapp_world import WhatsAppWorld, whatsapp_app


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


@pytest.mark.parametrize("text", ["   ", "\n\t "])
async def test_a_blank_reply_is_refused_with_422(world, text) -> None:
    thread = open_thread(world)
    world.db.default_plan.channels = {"whatsapp": True}
    fake = FakeGraph()
    with whatsapp_app(world) as app, graph_for(fake):
        token = await _member_token(world, app)
        res = await req(app, "POST", reply_url(world, thread), token=token, json={"text": text})
    assert res.status_code == 422 and fake.requests == []


async def test_a_reply_is_sent_trimmed(world) -> None:
    thread = open_thread(world)
    world.db.default_plan.channels = {"whatsapp": True}
    fake = FakeGraph()
    with whatsapp_app(world) as app, graph_for(fake):
        token = await _member_token(world, app)
        res = await req(app, "POST", reply_url(world, thread), token=token, json={"text": " Hi \n"})
    assert res.status_code == 201
    assert fake.requests[0].json["text"]["body"] == "Hi"


async def test_the_connection_check_needs_the_plan(world) -> None:
    world.db.default_plan.channels = {}
    fake = FakeGraph()
    with whatsapp_app(world) as app, graph_for(fake):
        token = await _member_token(world, app)
        res = await req(
            app, "POST", f"/api/v1/corpora/{world.corpus.id}/whatsapp/check", token=token
        )
    assert res.status_code == 403 and res.json()["code"] == "whatsapp_not_in_plan"
    assert fake.requests == []
