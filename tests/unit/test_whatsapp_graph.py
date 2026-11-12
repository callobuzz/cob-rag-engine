import logging
from dataclasses import dataclass, replace

import httpx
import pytest

from callobuzz_rag.channels.whatsapp.graph import GraphClient, GraphError, WhatsAppIdError
from tests.unit.whatsapp_fixtures import FakeGraph


@dataclass(frozen=True)
class _Settings:
    whatsapp_graph_url: str = "https://graph.facebook.com"
    whatsapp_graph_version: str = "v23.0"
    whatsapp_app_id: str = "555"
    whatsapp_app_secret: str = "app-secret-for-tests"


SETTINGS = _Settings()


async def _nosleep(_s: float) -> None:
    return None


def client(g: FakeGraph) -> GraphClient:
    return GraphClient(transport=g.transport, settings=SETTINGS, sleep=_nosleep)


@pytest.mark.parametrize(
    "bad", ["../../me", "123/messages?x=", "abc", "", "1" * 21, "12 34", "12345%2F"]
)
def test_ids_that_are_not_digits_never_reach_a_path(bad: str) -> None:
    with pytest.raises(WhatsAppIdError):
        GraphClient(transport=FakeGraph().transport, settings=SETTINGS).message_url(bad)


def test_base_url_must_be_https() -> None:
    with pytest.raises(ValueError):
        GraphClient.from_settings(replace(SETTINGS, whatsapp_graph_url="http://graph.facebook.com"))


async def test_send_text_shape_and_bearer_token() -> None:
    g = FakeGraph(reply={"messages": [{"id": "wamid.OUT1"}]})
    out = await client(g).send_text(
        "1234567890", token="EAAG...", to="919822012345", text="Namaste!"
    )
    req = g.requests[-1]
    assert (req.method, req.path) == ("POST", "/v23.0/1234567890/messages")
    assert req.headers["authorization"] == "Bearer EAAG..."
    assert req.json == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "919822012345",
        "type": "text",
        "text": {"body": "Namaste!", "preview_url": False},
    }
    assert out == "wamid.OUT1"


async def test_meta_error_becomes_graph_error_with_the_meta_code() -> None:
    g = FakeGraph(status=400, reply={"error": {"code": 131047, "message": "Re-engagement message"}})
    with pytest.raises(GraphError) as e:
        await client(g).send_text("1234567890", token="t", to="919822012345", text="hi")
    assert e.value.meta_code == 131047 and e.value.window_closed is True
    assert len(g.requests) == 1  # a 4xx is never retried


async def test_token_never_logged_or_in_the_error(caplog: pytest.LogCaptureFixture) -> None:
    g = FakeGraph(status=400, reply={"error": {"code": 190, "message": "bad token SECRETTOKEN"}})
    with caplog.at_level(logging.DEBUG), pytest.raises(GraphError) as e:
        await client(g).send_text("1234567890", token="SECRETTOKEN", to="919822012345", text="hi")
    assert "SECRETTOKEN" not in caplog.text
    assert "SECRETTOKEN" not in str(e.value)


async def test_send_5xx_is_not_retried() -> None:
    g = FakeGraph(script=[(500, {}), (503, {})])
    with pytest.raises(GraphError) as e:
        await client(g).send_text("1234567890", token="t", to="919822012345", text="hi")
    assert len(g.requests) == 1
    assert e.value.maybe_sent is True and e.value.status == 500


async def test_idempotent_call_retries_5xx_twice_then_fails() -> None:
    g = FakeGraph(script=[(500, {}), (503, {}), (502, {})])
    with pytest.raises(GraphError) as e:
        await client(g).get_number("1234567890", token="t")
    assert len(g.requests) == 3 and e.value.maybe_sent is False


async def test_idempotent_call_5xx_then_success() -> None:
    g = FakeGraph(script=[(500, {})], reply={"display_phone_number": "+91 98220 12345"})
    out = await client(g).get_number("1234567890", token="t")
    assert out["display_phone_number"] and len(g.requests) == 2


def _raising(exc: Exception) -> tuple[GraphClient, list[int]]:
    calls: list[int] = []

    def boom(_r: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise exc

    return GraphClient(
        transport=httpx.MockTransport(boom), settings=SETTINGS, sleep=_nosleep
    ), calls


async def test_send_connect_timeout_retries_then_fails() -> None:
    c, calls = _raising(httpx.ConnectTimeout("slow"))
    with pytest.raises(GraphError) as e:
        await c.send_text("1234567890", token="t", to="919822012345", text="hi")
    assert len(calls) == 3 and e.value.maybe_sent is False


@pytest.mark.parametrize(
    "exc", [httpx.ReadTimeout("slow"), httpx.WriteTimeout("slow"), httpx.RemoteProtocolError("x")]
)
async def test_send_read_timeout_is_not_retried(exc: Exception) -> None:
    c, calls = _raising(exc)
    with pytest.raises(GraphError) as e:
        await c.send_text("1234567890", token="t", to="919822012345", text="hi")
    assert len(calls) == 1 and e.value.maybe_sent is True


async def test_read_timeout_retries_for_a_get() -> None:
    c, calls = _raising(httpx.ReadTimeout("slow"))
    with pytest.raises(GraphError):
        await c.get_number("1234567890", token="t")
    assert len(calls) == 3


async def test_upload_media_is_multipart_and_returns_the_id() -> None:
    g = FakeGraph(reply={"id": "MEDIA1"})
    out = await client(g).upload_media(
        "1234567890", token="t", filename="rates.pdf", content=b"%PDF-1.4", mime="application/pdf"
    )
    req = g.requests[-1]
    assert out == "MEDIA1" and req.path == "/v23.0/1234567890/media"
    assert req.headers["content-type"].startswith("multipart/form-data")
    assert b'name="messaging_product"' in req.raw and b"%PDF-1.4" in req.raw


async def test_send_document_by_media_id() -> None:
    g = FakeGraph()
    await client(g).send_document(
        "1234567890",
        token="t",
        to="919822012345",
        media_id="M1",
        filename="rates.pdf",
        caption="Rates",
    )
    sent = g.requests[-1].json
    assert sent["type"] == "document"
    assert sent["document"] == {"id": "M1", "filename": "rates.pdf", "caption": "Rates"}


async def test_send_buttons_max_three_and_title_length() -> None:
    g = FakeGraph()
    c = client(g)
    await c.send_buttons(
        "1234567890",
        token="t",
        to="919822012345",
        body="Message you later?",
        buttons=[("yes", "Yes, message me"), ("no", "No thanks")],
    )
    sent = g.requests[-1].json["interactive"]
    assert sent["type"] == "button"
    assert sent["action"]["buttons"][0] == {
        "type": "reply",
        "reply": {"id": "yes", "title": "Yes, message me"},
    }
    with pytest.raises(ValueError):
        await c.send_buttons(
            "1234567890", token="t", to="1", body="x", buttons=[(str(i), "t") for i in range(4)]
        )
    with pytest.raises(ValueError):
        await c.send_buttons("1234567890", token="t", to="1", body="x", buttons=[("a", "x" * 21)])


async def test_send_template_shape_and_name_check() -> None:
    g = FakeGraph()
    c = client(g)
    await c.send_template(
        "1234567890",
        token="t",
        to="919822012345",
        name="follow_up_v1",
        language="en",
        params=["Asha"],
    )
    t = g.requests[-1].json["template"]
    assert t["name"] == "follow_up_v1" and t["language"] == {"code": "en"}
    assert t["components"] == [{"type": "body", "parameters": [{"type": "text", "text": "Asha"}]}]
    with pytest.raises(ValueError):
        await c.send_template(
            "1234567890", token="t", to="1", name="Bad Name!", language="en", params=[]
        )


async def test_mark_read_and_get_number() -> None:
    g = FakeGraph(reply={"display_phone_number": "+91 98220 12345", "verified_name": "Shree"})
    c = client(g)
    await c.mark_read("1234567890", token="t", message_id="wamid.IN1")
    assert g.requests[-1].json == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.IN1",
    }
    info = await c.get_number("1234567890", token="t")
    assert g.requests[-1].method == "GET" and info["verified_name"] == "Shree"
    assert "display_phone_number" in g.requests[-1].query["fields"]


async def test_signup_steps() -> None:
    g = FakeGraph(reply={"access_token": "BIZTOKEN", "success": True})
    c = client(g)
    assert await c.exchange_code("the-code") == "BIZTOKEN"
    q = g.requests[-1]
    assert q.path == "/v23.0/oauth/access_token"
    assert q.method == "POST"
    assert q.query == {}  # nothing secret in the URL
    assert b"code=the-code" in q.raw and b"client_id=555" in q.raw
    await c.register_number("1234567890", token="t", pin="123456")
    assert g.requests[-1].json == {"messaging_product": "whatsapp", "pin": "123456"}
    await c.subscribe_app("987654321", token="t")
    last = g.requests[-1]
    assert (last.method, last.path) == ("POST", "/v23.0/987654321/subscribed_apps")
    await c.unsubscribe_app("987654321", token="t")
    assert g.requests[-1].method == "DELETE"


async def test_client_secret_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    g = FakeGraph(status=400, reply={"error": {"code": 100, "message": "bad code"}})
    with caplog.at_level(logging.DEBUG), pytest.raises(GraphError):
        await client(g).exchange_code("c")
    assert "app-secret-for-tests" not in caplog.text


@pytest.mark.parametrize(
    ("status", "code"), [(429, None), (429, 4), (400, 130429), (400, 131056), (400, 80007)]
)
async def test_throttle_is_retryable_and_never_maybe_sent(status: int, code: int | None) -> None:
    reply = {"error": {"code": code}} if code else {}
    g = FakeGraph(status=status, reply=reply)
    with pytest.raises(GraphError) as e:
        await client(g).send_text("1234567890", token="t", to="919822012345", text="hi")
    assert e.value.retryable is True and e.value.maybe_sent is False
    assert e.value.retry_after == 60.0
    assert len(g.requests) == 1  # the client does not hammer; the caller re-queues


@pytest.mark.parametrize(
    ("header", "expected"),
    [("7", 7.0), ("99999", 3600.0), ("soon", 60.0), ("-3", 60.0), ("nan", 60.0)],
)
async def test_retry_after_header_is_read_and_capped(header: str, expected: float) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={}, headers={"Retry-After": header})

    c = GraphClient(settings=SETTINGS, transport=httpx.MockTransport(handler), sleep=_nosleep)
    with pytest.raises(GraphError) as e:
        await c.send_text("1234567890", token="t", to="919822012345", text="hi")
    assert e.value.retry_after == expected


async def test_ordinary_refusals_are_not_retryable() -> None:
    g = FakeGraph(status=400, reply={"error": {"code": 190}})
    with pytest.raises(GraphError) as e:
        await client(g).send_text("1234567890", token="t", to="919822012345", text="hi")
    assert e.value.retryable is False and e.value.retry_after is None


async def test_client_secret_not_in_the_url_nor_the_httpx_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    g = FakeGraph(reply={"access_token": "BIZTOKEN"})
    httpx_log = logging.getLogger("httpx")
    level = httpx_log.level
    with caplog.at_level(logging.INFO, logger="httpx"):
        await client(g).exchange_code("c")
    assert "app-secret-for-tests" not in caplog.text
    assert "app-secret-for-tests" not in str(g.requests[-1].query)
    assert httpx_log.level == level  # the global logger is never touched
