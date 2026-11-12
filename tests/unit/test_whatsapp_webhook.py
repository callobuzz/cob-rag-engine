"""The WhatsApp webhook: verification, the signature on the raw bytes, storing once, fast 200 (TV6)."""

import pytest

from callobuzz_rag.channels.whatsapp import webhook
from callobuzz_rag.db.models.channel import ChannelThread
from tests.unit.test_auth_routes import req
from tests.unit.whatsapp_fixtures import (
    echo_text,
    inbound_button,
    inbound_image,
    inbound_text,
    status_update,
)
from tests.unit.whatsapp_world import (
    APP_SECRET,
    CUSTOMER,
    PHONE_ID,
    URL,
    VERIFY,
    WhatsAppWorld,
    body_of,
    post_webhook,
    sign,
    spy_queue,
    whatsapp_app,
)


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


# --------------------------------------------------------------- the pure parts


def test_signature_is_sha256_hmac_of_the_raw_body() -> None:
    body = b'{"a": 1}  '
    assert webhook.verify_signature(body, sign(body), APP_SECRET)
    assert not webhook.verify_signature(body + b" ", sign(body), APP_SECRET)
    assert not webhook.verify_signature(body, sign(body, "other"), APP_SECRET)


@pytest.mark.parametrize("header", [None, "", "sha256=00", "sha1=" + "a" * 40, "a" * 64, "sha256="])
def test_malformed_signatures_are_refused(header: str | None) -> None:
    assert not webhook.verify_signature(b"x", header, APP_SECRET)


def test_an_empty_secret_never_verifies() -> None:
    assert not webhook.verify_signature(b"x", sign(b"x", ""), "")


def test_parse_reads_messages_statuses_and_echoes() -> None:
    (msg,) = webhook.parse(inbound_text(text="Namaste", name="Asha"))
    assert (msg.kind, msg.external_id, msg.contact, msg.text) == (
        "message",
        "wamid.IN1",
        CUSTOMER,
        "Namaste",
    )
    assert msg.phone_number_id == PHONE_ID and msg.profile_name == "Asha"
    assert msg.timestamp is not None and msg.message_type == "text"

    (button,) = webhook.parse(inbound_button())
    assert (button.text, button.button_id) == ("Yes, message me", "optin_yes")

    (image,) = webhook.parse(inbound_image())
    assert image.message_type == "image" and image.text == ""

    (status,) = webhook.parse(status_update(status="failed"))
    assert status.kind == "status" and status.external_id == "status:wamid.OUT1:failed"

    (echo,) = webhook.parse(echo_text(text="Hello"))
    assert echo.kind == "echo" and echo.contact == CUSTOMER and echo.text == "Hello"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"object": "page", "entry": []},
        {"object": "whatsapp_business_account", "entry": "x"},
        {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {}}]}]},
        {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages"}]}]},
    ],
)
def test_parse_never_raises_on_what_it_does_not_understand(payload) -> None:
    assert webhook.parse(payload) == []


def test_parse_skips_a_message_without_a_sender_or_id() -> None:
    payload = inbound_text()
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message["from"] = "not-a-number"
    assert webhook.parse(payload) == []


# --------------------------------------------------------------- GET: verification


async def test_verify_challenge(world: WhatsAppWorld) -> None:
    with whatsapp_app(world) as app:
        r = await req(
            app,
            "GET",
            URL,
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY,
                "hub.challenge": "1158201444",
            },
        )
    assert r.status_code == 200 and r.text == "1158201444"
    assert r.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY, "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "<script>"},
        {"hub.mode": "subscribe", "hub.verify_token": VERIFY},
        {},
    ],
)
async def test_verify_refused(world: WhatsAppWorld, params) -> None:
    with whatsapp_app(world) as app:
        r = await req(app, "GET", URL, params=params)
    assert r.status_code == 403 and r.json()["code"] == "whatsapp_verify_failed"


# --------------------------------------------------------------- POST: events


async def test_a_signed_message_is_stored_once_and_queued_once(world: WhatsAppWorld) -> None:
    payload = inbound_text(wamid="wamid.IN1", text="Root canal ka kitna charge hai?")
    spy = spy_queue()
    with whatsapp_app(world) as app, spy:
        for _ in range(2):  # Meta retries the same event
            r = await post_webhook(app, payload)
            assert r.status_code == 200 and r.json() == {}
    assert len(world.events("wamid.IN1")) == 1
    assert len(spy.calls) == 1
    (thread,) = world.db.all(ChannelThread)
    assert spy.calls == [thread.id]
    assert thread.last_inbound_at is not None and thread.connection_id == world.connection.id
    assert world.connection.last_inbound_at == thread.last_inbound_at


async def test_two_messages_from_one_customer_share_a_chat(world: WhatsAppWorld) -> None:
    spy = spy_queue()
    with whatsapp_app(world) as app, spy:
        await post_webhook(app, inbound_text(wamid="wamid.A"))
        await post_webhook(app, inbound_text(wamid="wamid.B", text="And a filling?"))
        await post_webhook(app, inbound_text(wamid="wamid.C", from_="919811100000"))
    assert len(world.db.all(ChannelThread)) == 2
    assert len(world.events()) == 3


@pytest.mark.parametrize(
    "header", [None, "sha256=00", "sha1=abc", sign(b"other body"), sign(b"x", "wrong-secret")]
)
async def test_a_bad_signature_is_401_and_stores_nothing(world: WhatsAppWorld, header) -> None:
    body = body_of(inbound_text(wamid="wamid.X"))
    headers = {"Content-Type": "application/json"} | (
        {"X-Hub-Signature-256": header} if header else {}
    )
    spy = spy_queue()
    with whatsapp_app(world) as app, spy:
        r = await req(app, "POST", URL, content=body, headers=headers)
    assert r.status_code == 401 and r.json()["code"] == "whatsapp_signature_invalid"
    assert world.events("wamid.X") == [] and spy.calls == []
    assert world.db.all(ChannelThread) == []


async def test_the_signature_is_checked_on_the_raw_bytes(world: WhatsAppWorld) -> None:
    body = b'{"object":"whatsapp_business_account","entry":[]}  '  # trailing spaces matter
    with whatsapp_app(world) as app:
        ok = await req(app, "POST", URL, content=body, headers={"X-Hub-Signature-256": sign(body)})
        stripped = await req(
            app, "POST", URL, content=body, headers={"X-Hub-Signature-256": sign(body.strip())}
        )
    assert ok.status_code == 200 and stripped.status_code == 401


async def test_signed_but_not_json_is_200_and_keeps_nothing(world: WhatsAppWorld) -> None:
    with whatsapp_app(world) as app:
        r = await post_webhook(app, b"not json at all")
    assert r.status_code == 200 and world.events() == []


async def test_an_unknown_number_is_ignored_with_200(world: WhatsAppWorld) -> None:
    spy = spy_queue()
    with whatsapp_app(world) as app, spy:
        r = await post_webhook(app, inbound_text(phone_number_id="999999999"))
    assert r.status_code == 200 and world.events() == [] and spy.calls == []


@pytest.mark.parametrize("status", ["pending", "disabled"])
async def test_a_number_that_is_not_live_is_not_served(world: WhatsAppWorld, status: str) -> None:
    world.connection.status = status
    spy = spy_queue()
    with whatsapp_app(world) as app, spy:
        r = await post_webhook(app, inbound_text())
    assert r.status_code == 200 and world.events() == [] and spy.calls == []


async def test_a_body_over_one_mebibyte_is_413(world: WhatsAppWorld) -> None:
    big = b"x" * (1024 * 1024 + 1)
    with whatsapp_app(world) as app:
        r = await post_webhook(app, big)
    assert r.status_code == 413 and r.json()["code"] == "payload_too_large"


async def test_engine_off_answers_404(world: WhatsAppWorld) -> None:
    body = body_of(inbound_text())
    with whatsapp_app(world, whatsapp_enabled=False) as app:
        post = await req(
            app, "POST", URL, content=body, headers={"X-Hub-Signature-256": sign(body)}
        )
        get = await req(
            app,
            "GET",
            URL,
            params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1"},
        )
    assert post.status_code == 404 and get.status_code == 404
    assert post.json()["code"] == "whatsapp_not_enabled"


async def test_statuses_and_echoes_are_stored_with_their_kind(world: WhatsAppWorld) -> None:
    spy = spy_queue()
    with whatsapp_app(world) as app, spy:
        await post_webhook(app, status_update(wamid="wamid.OUT1", status="read"))
        await post_webhook(app, echo_text(wamid="wamid.ECHO1"))
    (status,) = world.events("status:wamid.OUT1:read")
    assert status.kind == "status" and status.status == "done" and status.thread_id is None
    (echo,) = world.events("wamid.ECHO1")
    assert echo.kind == "echo" and echo.status == "received" and echo.thread_id is not None
    assert spy.calls == [echo.thread_id]  # a status wakes nobody, an echo pauses the chat


async def test_payload_is_stored_encrypted(world: WhatsAppWorld) -> None:
    with whatsapp_app(world) as app, spy_queue():
        await post_webhook(app, inbound_text(wamid="wamid.IN1", text="my secret question"))
    (event,) = world.events("wamid.IN1")
    assert CUSTOMER not in event.payload_encrypted
    assert "secret" not in event.payload_encrypted
    assert world.stored_payload(event)["text"] == "my secret question"
    (thread,) = world.db.all(ChannelThread)
    assert CUSTOMER not in thread.contact_encrypted
    assert world.encryptor.decrypt(thread.contact_encrypted) == "+" + CUSTOMER


async def test_an_image_message_is_stored_as_a_message(world: WhatsAppWorld) -> None:
    spy = spy_queue()
    with whatsapp_app(world) as app, spy:
        await post_webhook(app, inbound_image(wamid="wamid.IMG1"))
    (event,) = world.events("wamid.IMG1")
    assert event.kind == "message" and world.stored_payload(event)["type"] == "image"
    assert len(spy.calls) == 1


async def test_without_an_encryption_key_the_webhook_says_503(world: WhatsAppWorld) -> None:
    with whatsapp_app(world, encryption_key="") as app:
        r = await post_webhook(app, inbound_text())
    assert r.status_code == 503 and r.json()["code"] == "encryption_unavailable"
