"""Connecting a number: Embedded Signup by the owner, by hand by the deployer (TV6)."""

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from callobuzz_rag.channels.whatsapp.graph import GraphClient
from callobuzz_rag.db.models.channel import ChannelConnection, ChannelThread
from callobuzz_rag.db.models.corpus import Corpus
from tests.unit.test_auth_routes import ADMIN, login, req
from tests.unit.whatsapp_fixtures import FakeGraph
from tests.unit.whatsapp_world import KEY, WhatsAppWorld, whatsapp_app

NUMBER = {
    "display_phone_number": "+91 98200 11111",
    "verified_name": "Shree Dental Clinic",
    "quality_rating": "GREEN",
}
NEW_TOKEN = "EAAG-business-token-9z8y"
SIGNUP = {"code": "AQBcode", "phone_number_id": "1234567890", "waba_id": "5550001"}
OK = (200, {"success": True})


def graph_for(fake: FakeGraph):
    client = GraphClient(
        settings=SimpleNamespace(
            whatsapp_graph_url="https://graph.facebook.com",
            whatsapp_graph_version="v23.0",
            whatsapp_app_id="123456789",
            whatsapp_app_secret="the-app-secret",
        ),
        transport=fake.transport,
    )
    return patch("callobuzz_rag.channels.whatsapp.service.make_graph", lambda settings: client)


def signup_script() -> list:
    """exchange, register, subscribe, read the number."""
    return [(200, {"access_token": NEW_TOKEN}), OK, OK, (200, NUMBER)]


@pytest.fixture
def world() -> WhatsAppWorld:
    w = WhatsAppWorld()
    w.db.rows[ChannelConnection].remove(w.connection)  # a knowledge base with no number yet
    w.db.default_plan.channels = {"whatsapp": True}
    return w


async def sign_in(world: WhatsAppWorld, app, role: str = "owner") -> str:
    user = world.user("asha@shree.example")
    world.member(user, world.shree, role)
    res = await login(app, user.email)
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def url(world: WhatsAppWorld, tail: str = "") -> str:
    return f"/api/v1/corpora/{world.corpus.id}/whatsapp{tail}"


def connections(world: WhatsAppWorld) -> list[ChannelConnection]:
    return world.db.all(ChannelConnection)


# ------------------------------------------------------------------ Embedded Signup


async def test_the_owner_connects_by_embedded_signup(world, caplog) -> None:
    fake = FakeGraph(script=signup_script())
    caplog.set_level(logging.DEBUG)
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        res = await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
    assert res.status_code == 201, res.text
    exchange, register, subscribe, read = fake.requests
    assert (exchange.method, exchange.path) == ("POST", "/v23.0/oauth/access_token")
    assert exchange.query == {}  # the app secret is in the form body, never the URL
    assert b"client_secret=the-app-secret" in exchange.raw and b"code=AQBcode" in exchange.raw
    assert (register.method, register.path) == ("POST", "/v23.0/1234567890/register")
    pin = register.json["pin"]
    assert len(pin) == 6 and pin.isdigit() and register.json["messaging_product"] == "whatsapp"
    assert register.headers["authorization"] == f"Bearer {NEW_TOKEN}"
    assert (subscribe.method, subscribe.path) == ("POST", "/v23.0/5550001/subscribed_apps")
    assert subscribe.headers["authorization"] == f"Bearer {NEW_TOKEN}"
    assert (read.method, read.path) == ("GET", "/v23.0/1234567890")

    (connection,) = connections(world)
    assert connection.status == "active" and connection.setup == "embedded"
    assert connection.meta_billing == "business" and connection.coexistence is False
    assert connection.display_phone == "+91 98200 11111" and connection.token_hint == "9z8y"
    encryptor = world.encryptor
    assert encryptor.decrypt(connection.access_token_encrypted) == NEW_TOKEN
    assert encryptor.decrypt(connection.pin_encrypted) == pin
    assert NEW_TOKEN not in connection.access_token_encrypted
    body = res.json()
    assert (
        body["status"] == "active" and body["token_hint"] == "9z8y" and body["setup"] == "embedded"
    )
    assert NEW_TOKEN not in res.text and "the-app-secret" not in res.text
    (audit,) = world.audits("whatsapp.connected")
    assert audit.details == {
        "corpus_id": str(world.corpus.id),
        "setup": "embedded",
        "display_phone_hint": "…1111",
    }
    assert NEW_TOKEN not in str(audit.details) and pin not in str(audit.details)
    assert (
        NEW_TOKEN not in caplog.text
        and "the-app-secret" not in caplog.text
        and pin not in caplog.text
    )


async def test_a_coexistence_number_is_not_registered(world) -> None:
    fake = FakeGraph(script=[(200, {"access_token": NEW_TOKEN}), OK, (200, NUMBER)])
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        res = await req(
            app,
            "POST",
            url(world, "/embedded-signup"),
            token=token,
            json={**SIGNUP, "coexistence": True},
        )
    assert res.status_code == 201, res.text
    assert [r.path.rsplit("/", 1)[-1] for r in fake.requests] == [
        "access_token",
        "subscribed_apps",
        "1234567890",
    ]
    (connection,) = connections(world)
    assert connection.coexistence is True and connection.pin_encrypted is None


@pytest.mark.parametrize("step", ["exchange_code", "register", "subscribe", "read_number"])
async def test_a_failing_step_is_a_502_naming_it_and_nothing_is_saved(world, step) -> None:
    script = signup_script()
    index = ["exchange_code", "register", "subscribe", "read_number"].index(step)
    script[index] = (400, {"error": {"code": 100, "message": "no"}})
    fake = FakeGraph(script=script)
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        res = await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
    assert res.status_code == 502, res.text
    assert res.json()["code"] == "whatsapp_signup_failed" and res.json()["step"] == step
    assert connections(world) == [] and world.audits("whatsapp.connected") == []


async def test_a_number_on_another_knowledge_base_is_409_and_nothing_is_called(world) -> None:
    other = world.db.add(
        Corpus(name="Other", tenant_id=world.shree.id, qdrant_collection="c2", api_key_hash="")
    )
    world.connect(phone_number_id="1234567890", corpus=other)
    fake = FakeGraph(script=signup_script())
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        res = await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
    assert res.status_code == 409 and res.json()["code"] == "whatsapp_number_in_use"
    assert fake.requests == []


@pytest.mark.parametrize(
    "bad", ["abc", "../../me", "123/messages?x=", "12 34", "1" * 21, "1234", "12345%2F"]
)
async def test_ids_that_are_not_digits_are_422_before_any_call(world, bad) -> None:
    fake = FakeGraph(script=signup_script())
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        a = await req(
            app,
            "POST",
            url(world, "/embedded-signup"),
            token=token,
            json={**SIGNUP, "phone_number_id": bad},
        )
        b = await req(
            app,
            "POST",
            url(world, "/embedded-signup"),
            token=token,
            json={**SIGNUP, "waba_id": bad},
        )
    for res in (a, b):
        assert res.status_code == 422 and res.json()["code"] == "whatsapp_id_invalid"
    assert fake.requests == []


async def test_a_plan_without_whatsapp_is_403(world) -> None:
    world.db.default_plan.channels = {}
    with whatsapp_app(world) as app, graph_for(FakeGraph(script=signup_script())):
        token = await sign_in(world, app)
        res = await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
    assert res.status_code == 403 and res.json()["code"] == "whatsapp_not_in_plan"


async def test_an_engine_without_a_meta_app_is_409(world) -> None:
    with whatsapp_app(world, whatsapp_enabled=False) as app, graph_for(FakeGraph()):
        token = await sign_in(world, app)
        res = await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
    assert res.status_code == 409 and res.json()["code"] == "whatsapp_not_enabled"


async def test_a_member_cannot_connect(world) -> None:
    with whatsapp_app(world) as app, graph_for(FakeGraph(script=signup_script())):
        token = await sign_in(world, app, role="member")
        res = await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
    assert res.status_code == 403 and res.json()["code"] == "owner_required"


async def test_connecting_again_replaces_the_knowledge_bases_number(world) -> None:
    fake = FakeGraph(script=signup_script() + signup_script())
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        first = await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
        again = await req(
            app,
            "POST",
            url(world, "/embedded-signup"),
            token=token,
            json={**SIGNUP, "phone_number_id": "9988776655"},
        )
    assert first.status_code == 201 and again.status_code == 201, again.text
    (connection,) = connections(world)  # one number per knowledge base
    assert connection.phone_number_id == "9988776655"


# ------------------------------------------------------------------ by hand


MANUAL = {
    "phone_number_id": "1234567890",
    "waba_id": "5550001",
    "access_token": "EAAG-permanent-token-ab12",
    "meta_billing": "deployer",
}


async def test_the_deployer_connects_by_hand_and_the_token_is_checked(world, caplog) -> None:
    fake = FakeGraph(script=[(200, NUMBER), OK])
    caplog.set_level(logging.DEBUG)
    with whatsapp_app(world) as app, graph_for(fake):
        res = await req(app, "PUT", url(world, "/manual"), key=ADMIN, json=MANUAL)
    assert res.status_code == 200, res.text
    read, subscribe = fake.requests
    assert (
        read.path == "/v23.0/1234567890"
        and read.headers["authorization"] == f"Bearer {MANUAL['access_token']}"
    )
    assert subscribe.path == "/v23.0/5550001/subscribed_apps"
    (connection,) = connections(world)
    assert connection.setup == "manual" and connection.meta_billing == "deployer"
    assert connection.token_hint == "ab12" and connection.status == "active"
    assert world.encryptor.decrypt(connection.access_token_encrypted) == MANUAL["access_token"]
    assert MANUAL["access_token"] not in res.text and res.json()["token_hint"] == "ab12"
    (audit,) = world.audits("whatsapp.connected")
    assert MANUAL["access_token"] not in str(audit.details)
    assert MANUAL["access_token"] not in caplog.text


async def test_a_token_meta_refuses_is_422_and_nothing_is_saved(world) -> None:
    fake = FakeGraph(
        script=[(400, {"error": {"code": 190, "message": "Invalid OAuth access token"}})]
    )
    with whatsapp_app(world) as app, graph_for(fake):
        res = await req(app, "PUT", url(world, "/manual"), key=ADMIN, json=MANUAL)
    assert res.status_code == 422 and res.json()["code"] == "whatsapp_token_invalid"
    assert connections(world) == []


async def test_a_failed_subscription_does_not_block_a_manual_connection(world) -> None:
    fake = FakeGraph(
        script=[(200, NUMBER), (400, {"error": {"code": 200, "message": "no permission"}})]
    )
    with whatsapp_app(world) as app, graph_for(fake):
        res = await req(app, "PUT", url(world, "/manual"), key=ADMIN, json=MANUAL)
    assert res.status_code == 200 and len(connections(world)) == 1


async def test_manual_entry_is_the_deployers_alone(world) -> None:
    with whatsapp_app(world) as app, graph_for(FakeGraph(script=[(200, NUMBER), OK])):
        owner = await sign_in(world, app)
        res = await req(app, "PUT", url(world, "/manual"), token=owner, json=MANUAL)
    assert res.status_code == 403 and connections(world) == []


@pytest.mark.parametrize("missing", ["meta_billing", "access_token", "waba_id", "phone_number_id"])
async def test_every_manual_field_is_required(world, missing) -> None:
    body = {k: v for k, v in MANUAL.items() if k != missing}
    with whatsapp_app(world) as app, graph_for(FakeGraph()):
        res = await req(app, "PUT", url(world, "/manual"), key=ADMIN, json=body)
    assert res.status_code == 422


async def test_a_new_token_on_the_same_number_is_a_token_update(world) -> None:
    fake = FakeGraph(script=[(200, NUMBER), OK, (200, NUMBER), OK])
    with whatsapp_app(world) as app, graph_for(fake):
        await req(app, "PUT", url(world, "/manual"), key=ADMIN, json=MANUAL)
        res = await req(
            app,
            "PUT",
            url(world, "/manual"),
            key=ADMIN,
            json={**MANUAL, "access_token": "EAAG-rotated-token-cd34"},
        )
    assert res.status_code == 200 and res.json()["token_hint"] == "cd34"
    assert (
        len(world.audits("whatsapp.connected")) == 1
        and len(world.audits("whatsapp.token_updated")) == 1
    )
    assert "EAAG-rotated-token-cd34" not in str(world.audits("whatsapp.token_updated")[0].details)


# ------------------------------------------------------------------ reading, settings, disconnecting


async def test_a_knowledge_base_without_a_number_says_so_and_offers_the_sign_up(world) -> None:
    with whatsapp_app(world) as app:
        token = await sign_in(world, app, role="member")
        res = await req(app, "GET", url(world), token=token)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["available"] is True and body["status"] is None
    assert body["embedded_signup"] == {
        "app_id": "123456789",
        "config_id": "99999",
        "graph_version": "v23.0",
    }


async def test_an_unavailable_plan_hides_the_sign_up(world) -> None:
    world.db.default_plan.channels = {}
    with whatsapp_app(world) as app:
        token = await sign_in(world, app)
        res = await req(app, "GET", url(world), token=token)
    assert res.json()["available"] is False and res.json()["embedded_signup"] is None


async def test_the_owner_changes_settings_and_the_audit_names_what_changed(world) -> None:
    world.connect()
    with whatsapp_app(world) as app:
        token = await sign_in(world, app)
        res = await req(
            app,
            "PATCH",
            url(world),
            token=token,
            json={
                "human_pause_hours": 6,
                "follow_up": {
                    "enabled": True,
                    "template_name": "callback_followup",
                    "language": "en",
                },
            },
        )
    assert res.status_code == 200, res.text
    settings = res.json()["settings"]
    assert settings["human_pause_hours"] == 6 and settings["follow_up"]["enabled"] is True
    assert settings["follow_up"]["after_hours"] == 24
    (audit,) = world.audits("whatsapp.settings_updated")
    assert set(audit.details["changed"]) == {
        "human_pause_hours",
        "follow_up.enabled",
        "follow_up.template_name",
        "follow_up.language",
    }
    assert "callback_followup" not in str(audit.details)


@pytest.mark.parametrize(
    "body",
    [
        {"follow_up": {"enabled": True}},  # no template
        {"human_pause_hours": 0},
        {"human_pause_hours": 73},
        {"follow_up": {"template_name": "Bad Name"}},
        {"follow_up": {"after_hours": 100}},
        {"surprise": 1},
    ],
)
async def test_bad_settings_are_refused(world, body) -> None:
    world.connect()
    with whatsapp_app(world) as app:
        token = await sign_in(world, app)
        res = await req(app, "PATCH", url(world), token=token, json=body)
    assert res.status_code == 422


async def test_switching_the_number_off_and_on(world) -> None:
    connection = world.connect()
    with whatsapp_app(world) as app:
        token = await sign_in(world, app)
        off = await req(app, "PATCH", url(world), token=token, json={"enabled": False})
        assert off.json()["status"] == "disabled" and connection.status == "disabled"
        on = await req(app, "PATCH", url(world), token=token, json={"enabled": True})
    assert on.json()["status"] == "active"


async def test_disconnecting_deletes_the_token_and_keeps_the_chats_readable(world) -> None:
    connection = world.connect()
    world.connection = connection
    thread = world.thread()
    fake = FakeGraph(reply={"success": True})
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        gone = await req(app, "DELETE", url(world), token=token)
        chats = await req(app, "GET", url(world, "/threads"), token=token)
        after = await req(app, "GET", url(world), token=token)
    assert gone.status_code == 204
    (unsubscribe,) = fake.requests
    assert unsubscribe.method == "DELETE" and unsubscribe.path == "/v23.0/5550001/subscribed_apps"
    assert connection.access_token_encrypted == "" and connection.token_hint == ""
    assert connection.pin_encrypted is None and connection.status == "disabled"
    assert not connection.phone_number_id.isdigit()  # no webhook can reach it any more
    assert world.db.all(ChannelThread) == [thread]
    assert chats.status_code == 200 and chats.json()["total"] == 1
    assert after.json()["status"] is None and after.json()["token_hint"] is None
    assert len(world.audits("whatsapp.disconnected")) == 1


async def test_after_disconnecting_the_number_can_go_to_another_knowledge_base(world) -> None:
    world.connect()
    other = world.db.add(
        Corpus(name="Other", tenant_id=world.shree.id, qdrant_collection="c2", api_key_hash="")
    )
    fake = FakeGraph(script=[OK, (200, NUMBER), OK])
    with whatsapp_app(world) as app, graph_for(fake):
        await req(app, "DELETE", url(world), key=ADMIN)
        res = await req(
            app,
            "PUT",
            f"/api/v1/corpora/{other.id}/whatsapp/manual",
            key=ADMIN,
            json=MANUAL,
        )
    assert res.status_code == 200, res.text


async def test_disconnecting_nothing_is_404(world) -> None:
    with whatsapp_app(world) as app, graph_for(FakeGraph()):
        token = await sign_in(world, app)
        res = await req(app, "DELETE", url(world), token=token)
    assert res.status_code == 404 and res.json()["code"] == "whatsapp_not_connected"


async def test_check_reads_the_number_and_clears_an_error(world) -> None:
    connection = world.connect(status="error")
    connection.last_error_code = "token"
    fake = FakeGraph(reply=NUMBER)
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app, role="member")
        res = await req(app, "POST", url(world, "/check"), token=token)
    assert res.status_code == 200 and res.json() == {
        "ok": True,
        "display_phone": "+91 98200 11111",
        "quality_rating": "GREEN",
        "error_code": None,
    }
    assert connection.status == "active" and connection.last_error_code is None
    assert fake.requests[0].query == {"fields": "display_phone_number,verified_name,quality_rating"}


async def test_check_with_a_refused_token_sets_the_error(world) -> None:
    connection = world.connect()
    fake = FakeGraph(status=401, reply={"error": {"code": 190, "message": "Expired"}})
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        res = await req(app, "POST", url(world, "/check"), token=token)
    assert res.json()["ok"] is False and res.json()["error_code"] == "token_invalid"
    assert connection.status == "error" and connection.last_error_code == "token_invalid"


async def test_the_deployer_sees_every_number(world) -> None:
    world.connect()
    with whatsapp_app(world) as app:
        res = await req(app, "GET", "/api/v1/admin/whatsapp/connections", key=ADMIN)
        owner = await sign_in(world, app)
        denied = await req(app, "GET", "/api/v1/admin/whatsapp/connections", token=owner)
    assert denied.status_code == 403
    assert res.status_code == 200, res.text
    assert KEY not in res.text and "EAAG" not in res.text


def test_pin_is_six_digits_and_the_hint_is_the_end_of_the_number() -> None:
    from callobuzz_rag.channels.whatsapp import signup

    crypto = WhatsAppWorld().crypto
    for n in range(50):
        pin = signup.derive_pin(crypto, f"12345{n:05d}")
        assert len(pin) == 6 and pin.isdigit()
    # the same number always asks for the same PIN, another number for another
    assert signup.derive_pin(crypto, "1234567890") == signup.derive_pin(crypto, "1234567890")
    assert signup.derive_pin(crypto, "1234567890") != signup.derive_pin(crypto, "1234567891")
    assert signup.display_hint("+91 98200 11111") == "…1111" and signup.display_hint("") == ""
    assert ChannelThread is not None


# ------------------------------------------------------------------ review fixes (TV6)


def other_kb(world: WhatsAppWorld) -> Corpus:
    return world.db.add(
        Corpus(name="Other", tenant_id=world.shree.id, qdrant_collection="c2", api_key_hash="")
    )


async def test_disconnect_keeps_the_waba_subscription_while_another_number_uses_it(world) -> None:
    mine = world.connect()
    world.connect(phone_number_id="2222222222", corpus=other_kb(world))  # same WABA
    fake = FakeGraph(reply={"success": True})
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        gone = await req(app, "DELETE", url(world), token=token)
    assert gone.status_code == 204
    assert fake.requests == []  # the other number still needs the subscription
    assert mine.access_token_encrypted == "" and not mine.phone_number_id.isdigit()


async def test_disconnect_ignores_numbers_that_are_already_gone(world) -> None:
    world.connect()
    gone = world.connect(phone_number_id="gone-abc", corpus=other_kb(world))
    gone.access_token_encrypted = ""
    fake = FakeGraph(reply={"success": True})
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        res = await req(app, "DELETE", url(world), token=token)
    assert res.status_code == 204
    assert [r.method for r in fake.requests] == ["DELETE"]


async def test_a_retry_after_a_failed_sign_up_registers_with_the_same_pin(world) -> None:
    failing = [*signup_script()[:2], (400, {"error": {"code": 100, "message": "no"}})]
    fake = FakeGraph(script=[*failing, *signup_script()])
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        first = await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
        again = await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
    assert first.status_code == 502 and again.status_code == 201, again.text
    pins = [r.json["pin"] for r in fake.requests if r.path.endswith("/register")]
    assert len(pins) == 2 and pins[0] == pins[1]
    (connection,) = connections(world)
    assert world.encryptor.decrypt(connection.pin_encrypted) == pins[0]


async def test_connecting_the_same_number_again_after_disconnecting_reuses_its_pin(world) -> None:
    fake = FakeGraph(script=[*signup_script(), OK, *signup_script()])
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
        await req(app, "DELETE", url(world), token=token)
        again = await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
    assert again.status_code == 201, again.text
    pins = [r.json["pin"] for r in fake.requests if r.path.endswith("/register")]
    assert len(pins) == 2 and pins[0] == pins[1]


async def test_a_stored_pin_for_the_same_number_wins_over_the_derived_one(world) -> None:
    connection = world.connect()
    connection.pin_encrypted = world.encryptor.encrypt("424242")
    fake = FakeGraph(script=signup_script())
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        res = await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
    assert res.status_code == 201, res.text
    (register,) = [r for r in fake.requests if r.path.endswith("/register")]
    assert register.json["pin"] == "424242"


def thread_with_history(world: WhatsAppWorld) -> ChannelThread:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return world.thread(
        opted_in_at=now,
        opted_out_at=now,
        opt_in_asked_at=now,
        follow_up_sent_at=now,
        bot_paused_until=now,
        paused_by="dashboard",
    )


async def test_a_new_number_does_not_inherit_the_old_numbers_consents(world) -> None:
    connection = world.connect(settings={"media_cache": {"x": {"id": "m"}}, "human_pause_hours": 6})
    world.connection = connection
    thread = thread_with_history(world)
    fake = FakeGraph(script=[(200, NUMBER), OK])
    with whatsapp_app(world) as app, graph_for(fake):
        res = await req(
            app,
            "PUT",
            url(world, "/manual"),
            key=ADMIN,
            json={**MANUAL, "phone_number_id": "9988776655"},
        )
    assert res.status_code == 200, res.text
    assert connection.phone_number_id == "9988776655"
    assert world.db.all(ChannelThread) == [thread]  # the chat stays readable
    assert thread.opted_in_at is None and thread.opted_out_at is None
    assert thread.opt_in_asked_at is None and thread.follow_up_sent_at is None
    assert thread.bot_paused_until is None and thread.paused_by is None
    assert "media_cache" not in connection.settings  # media ids belong to the old number
    assert connection.settings["human_pause_hours"] == 6  # the owner's choices stay


async def test_reconnecting_after_a_disconnect_starts_fresh_too(world) -> None:
    world.connection = world.connect()
    thread = thread_with_history(world)
    fake = FakeGraph(script=[OK, (200, NUMBER), OK])
    with whatsapp_app(world) as app, graph_for(fake):
        await req(app, "DELETE", url(world), key=ADMIN)
        res = await req(app, "PUT", url(world, "/manual"), key=ADMIN, json=MANUAL)
    assert res.status_code == 200, res.text
    assert thread.opted_out_at is None and thread.follow_up_sent_at is None


async def test_the_same_number_keeps_its_consents_on_a_token_update(world) -> None:
    world.connection = world.connect()
    thread = thread_with_history(world)
    fake = FakeGraph(script=[(200, NUMBER), OK])
    with whatsapp_app(world) as app, graph_for(fake):
        res = await req(app, "PUT", url(world, "/manual"), key=ADMIN, json=MANUAL)
    assert res.status_code == 200
    assert thread.opted_out_at is not None and thread.follow_up_sent_at is not None


async def test_a_token_update_does_not_switch_a_disabled_number_back_on(world) -> None:
    connection = world.connect(status="disabled")
    fake = FakeGraph(script=[(200, NUMBER), OK])
    with whatsapp_app(world) as app, graph_for(fake):
        res = await req(
            app,
            "PUT",
            url(world, "/manual"),
            key=ADMIN,
            json={**MANUAL, "access_token": "EAAG-rotated-token-cd34"},
        )
    assert res.status_code == 200 and res.json()["status"] == "disabled"
    assert connection.status == "disabled" and connection.token_hint == "cd34"


async def test_a_token_update_clears_an_error(world) -> None:
    connection = world.connect(status="error")
    connection.last_error_code = "token"
    fake = FakeGraph(script=[(200, NUMBER), OK])
    with whatsapp_app(world) as app, graph_for(fake):
        res = await req(app, "PUT", url(world, "/manual"), key=ADMIN, json=MANUAL)
    assert res.json()["status"] == "active" and connection.last_error_code is None


async def test_the_owners_sign_up_switches_a_disabled_number_on(world) -> None:
    connection = world.connect(status="disabled")
    fake = FakeGraph(script=signup_script())
    with whatsapp_app(world) as app, graph_for(fake):
        token = await sign_in(world, app)
        res = await req(app, "POST", url(world, "/embedded-signup"), token=token, json=SIGNUP)
    assert res.status_code == 201 and connection.status == "active"


async def test_manual_connect_with_a_pin_registers_the_number(world) -> None:
    fake = FakeGraph(script=[(200, NUMBER), OK, OK])
    with whatsapp_app(world) as app, graph_for(fake):
        res = await req(
            app, "PUT", url(world, "/manual"), key=ADMIN, json={**MANUAL, "pin": "908172"}
        )
    assert res.status_code == 200, res.text
    read, register, subscribe = fake.requests
    assert register.path == "/v23.0/1234567890/register"
    assert register.json == {"messaging_product": "whatsapp", "pin": "908172"}
    assert register.headers["authorization"] == f"Bearer {MANUAL['access_token']}"
    assert subscribe.path.endswith("/subscribed_apps") and read.method == "GET"
    (connection,) = connections(world)
    assert world.encryptor.decrypt(connection.pin_encrypted) == "908172"
    assert "908172" not in res.text and "908172" not in str(world.audits("whatsapp.connected"))


async def test_manual_connect_without_a_pin_does_not_register(world) -> None:
    fake = FakeGraph(script=[(200, NUMBER), OK])
    with whatsapp_app(world) as app, graph_for(fake):
        res = await req(app, "PUT", url(world, "/manual"), key=ADMIN, json=MANUAL)
    assert res.status_code == 200
    assert not any(r.path.endswith("/register") for r in fake.requests)
    assert connections(world)[0].pin_encrypted is None


async def test_manual_register_failure_is_a_502_and_nothing_is_saved(world) -> None:
    fake = FakeGraph(script=[(200, NUMBER), (400, {"error": {"code": 133005, "message": "pin"}})])
    with whatsapp_app(world) as app, graph_for(fake):
        res = await req(
            app, "PUT", url(world, "/manual"), key=ADMIN, json={**MANUAL, "pin": "908172"}
        )
    assert res.status_code == 502 and res.json()["step"] == "register"
    assert connections(world) == []


@pytest.mark.parametrize("bad", ["12345", "1234567", "abcdef", "12 456"])
async def test_a_manual_pin_is_six_digits(world, bad) -> None:
    with whatsapp_app(world) as app, graph_for(FakeGraph()):
        res = await req(app, "PUT", url(world, "/manual"), key=ADMIN, json={**MANUAL, "pin": bad})
    assert res.status_code == 422
