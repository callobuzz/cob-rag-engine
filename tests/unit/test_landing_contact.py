"""LP3 T5, T6: the contact form lands in the support inbox, with no captcha and limits that fail
closed."""

import itertools
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from callobuzz_rag.accounts import cleanup as cleanup_module
from callobuzz_rag.accounts.settings_store import (
    SETTINGS_ROW_ID,
    EngineSettings,
    invalidate_engine_settings_cache,
)
from callobuzz_rag.api import errors
from callobuzz_rag.api.routes import landing as landing_routes
from callobuzz_rag.api.routes import landing_public
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.support import SupportMessage, SupportRequest
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.demo.guard import refusal
from callobuzz_rag.landing import contact
from tests.unit.accounts_fakes import FakeDB, FakeRedis
from tests.unit.landing_fakes import SECRET, make_env, make_settings, make_site

URL = "/api/v1/public/landing/contact"
GOOD: dict[str, Any] = {
    "name": "Priya Sharma",
    "email": "priya@sharmadental.in",
    "phone": None,
    "topic": "sales",
    "message": "We run two clinics in Pune. Can it answer in Marathi too?",
    "consent": True,
    "website": "",
    "lang": "en",
}
NOW = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)


class World:
    def __init__(self) -> None:
        self.db = FakeDB()
        self.operator = self.db.add(
            Tenant(
                name="Sahayak", status="active", max_corpora=10, api_key_hash="", is_operator=True
            )
        )
        self.redis = FakeRedis()
        self.mails: list[tuple[str, str, dict[str, Any]]] = []
        self.settings = make_settings(
            redis_url="redis://unused",
            demo_mode=False,
            superadmin_email="owner@sahayak.example",
            landing_contact_per_ip_per_hour=3,
            landing_contact_per_day=200,
            landing_contact_keep_days=365,
        )

    @property
    def requests(self) -> list[SupportRequest]:
        return self.db.all(SupportRequest)

    def token(self, seconds_ago: int = 20) -> str:
        return contact.make_form_token(
            self.settings, datetime.now(UTC) - timedelta(seconds=seconds_ago)
        )


@contextmanager
def app_for(
    world: World, monkeypatch: pytest.MonkeyPatch, *, redis: Any = None, site: bool = True
) -> Iterator[httpx.AsyncClient]:
    invalidate_engine_settings_cache()
    world.db.add(
        EngineSettingsRow(
            id=SETTINGS_ROW_ID,
            value=EngineSettings().model_dump(mode="json")
            | {"branding": {"product_name": "Sahayak", "support_email": "help@sahayak.example"}},
        )
    )
    counter = itertools.count(1)

    async def next_number(_session: Any) -> int:
        return next(counter)

    async def live(_request: Any) -> Any:
        return make_site() if site else None

    async def environment(_request: Any, **_kw: Any) -> Any:
        return make_env()

    monkeypatch.setattr(landing_routes, "get_live_site", live)
    monkeypatch.setattr(landing_routes, "get_environment", environment)
    queued = world.mails

    def enqueue(template: str, to: str, context: Any, **_kw: Any) -> None:
        queued.append((template, to, dict(context)))

    app = FastAPI()
    errors.register_error_handlers(app)
    app.include_router(landing_routes.router)
    app.include_router(landing_public.router)
    app.state.settings = world.settings
    app.state.session_factory = world.db.factory()
    app.state.landing_contact_limiter = contact.ContactLimiter(
        redis or world.redis, per_ip_per_hour=3, daily=200
    )
    with (
        patch("callobuzz_rag.support.service.next_number", next_number),
        patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", enqueue),
    ):
        yield httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    invalidate_engine_settings_cache()


@pytest.fixture
def world() -> World:
    w = World()
    w.settings.session_secret = type(w.settings.session_secret)(SECRET)
    return w


# ----------------------------------------------------------------- the JSON route


async def test_a_good_enquiry_lands_in_the_support_inbox(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        res = await client.post(URL, json={**GOOD, "form_token": world.token()})
    assert res.status_code == 202 and res.json() == {"ok": True}
    [row] = world.requests
    assert row.tenant_id == world.operator.id and row.subject.startswith("Website enquiry: ")
    assert row.context["landing_contact"]["email"] == "priya@sharmadental.in"
    assert row.created_by_user_id is None and row.page == "/contact"
    [message] = world.db.all(SupportMessage)
    assert (
        "Can it answer in Marathi too?" in message.body and "priya@sharmadental.in" in message.body
    )
    assert [m[0] for m in world.mails] == ["support_request_new"]  # the provider is told
    assert world.mails[0][1] == "help@sahayak.example"


async def test_the_honeypot_and_a_too_fast_form_get_the_same_answer(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        bot = await client.post(
            URL, json={**GOOD, "website": "http://spam.example", "form_token": world.token()}
        )
        fast = await client.post(URL, json={**GOOD, "form_token": world.token(seconds_ago=1)})
    for res in (bot, fast):
        assert res.status_code == 422
        assert res.json() == {
            "detail": "Please check the form and try again.",
            "code": "landing_contact_invalid",
        }
    assert not world.requests


async def test_tampered_and_expired_tokens_are_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = world.token()
    bad = good[:-1] + ("A" if good[-1] != "A" else "B")
    with app_for(world, monkeypatch) as client:
        for token in (bad, world.token(seconds_ago=25 * 3600), "nope"):
            res = await client.post(URL, json={**GOOD, "form_token": token})
            assert res.json()["code"] == "landing_contact_invalid", token
    assert not world.requests


async def test_three_an_hour_per_visitor_then_429_with_retry_after(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        for _ in range(3):
            assert (
                await client.post(URL, json={**GOOD, "form_token": world.token()})
            ).status_code == 202
        res = await client.post(URL, json={**GOOD, "form_token": world.token()})
    assert res.status_code == 429 and res.json()["code"] == "landing_contact_rate_limited"
    assert int(res.headers["retry-after"]) > 0
    assert len(world.requests) == 3


async def test_every_attempt_counts_against_the_visitor_even_a_bot(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        for _ in range(3):
            await client.post(URL, json={**GOOD, "website": "x", "form_token": world.token()})
        res = await client.post(URL, json={**GOOD, "form_token": world.token()})
    assert res.status_code == 429


async def test_a_malformed_body_is_refused_in_the_same_words_and_counts(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        for body in (b"not json", b"[1, 2]", b'{"name": "x"}'):
            res = await client.post(URL, content=body, headers={"content-type": "application/json"})
            assert res.status_code == 422
            assert res.json() == {
                "detail": contact.INVALID_DETAIL,
                "code": "landing_contact_invalid",
            }
        res = await client.post(URL, json={**GOOD, "form_token": world.token()})
    assert res.status_code == 429  # the three bad bodies used up the visitor's three an hour
    assert not world.requests


async def test_bots_cannot_use_up_the_daily_allowance(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from callobuzz_rag.widget import security

    counter = itertools.count(1)
    monkeypatch.setattr(security, "request_client_ip", lambda _request: f"10.0.0.{next(counter)}")
    with app_for(world, monkeypatch) as client:
        for _ in range(250):
            res = await client.post(
                URL, json={**GOOD, "website": "http://spam.example", "form_token": world.token()}
            )
            assert res.status_code == 422
        good = await client.post(URL, json={**GOOD, "form_token": world.token()})
    assert good.status_code == 202 and len(world.requests) == 1


async def test_the_daily_allowance_counts_real_messages(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from callobuzz_rag.widget import security

    counter = itertools.count(1)
    monkeypatch.setattr(security, "request_client_ip", lambda _request: f"10.1.0.{next(counter)}")
    with app_for(world, monkeypatch) as client:
        app = client._transport.app  # type: ignore[attr-defined]
        app.state.landing_contact_limiter = contact.ContactLimiter(
            world.redis, per_ip_per_hour=3, daily=2
        )
        codes = [
            (await client.post(URL, json={**GOOD, "form_token": world.token()})).status_code
            for _ in range(3)
        ]
    assert codes == [202, 202, 429]


class BrokenRedis:
    def pipeline(self) -> Any:
        raise ConnectionError("down")


async def test_redis_down_fails_closed(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    with app_for(world, monkeypatch, redis=BrokenRedis()) as client:
        res = await client.post(URL, json={**GOOD, "form_token": world.token()})
    assert res.status_code == 503 and res.json()["code"] == "landing_contact_unavailable"
    assert not world.requests


async def test_email_or_phone_is_required_and_extra_fields_are_refused(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        res = await client.post(URL, json={**GOOD, "email": None, "form_token": world.token()})
        assert res.status_code == 422
        assert res.json() == {"detail": contact.INVALID_DETAIL, "code": "landing_contact_invalid"}
        res = await client.post(URL, json={**GOOD, "form_token": world.token(), "tenant_id": "x"})
        assert res.status_code == 422
        assert res.json() == {"detail": contact.INVALID_DETAIL, "code": "landing_contact_invalid"}
        phone_only = await client.post(
            URL,
            json={**GOOD, "email": None, "phone": "+91 98220 12345", "form_token": world.token()},
        )
    assert phone_only.status_code == 202
    assert world.requests[0].created_by_email == "+91 98220 12345"


async def test_no_operator_workspace_means_unavailable(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.db.rows[Tenant].clear()
    with app_for(world, monkeypatch) as client:
        res = await client.post(URL, json={**GOOD, "form_token": world.token()})
    assert res.status_code == 503 and res.json()["code"] == "landing_contact_unavailable"


async def test_a_demo_server_refuses_the_form(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.settings.demo_mode = True
    with app_for(world, monkeypatch) as client:
        res = await client.post(URL, json={**GOOD, "form_token": world.token()})
    assert res.status_code == 403 and not world.requests
    assert refusal("POST", "/api/v1/public/landing/contact", admin_key=False) == "privacy"
    assert refusal("POST", "/contact", admin_key=False) == "manage"
    assert refusal("POST", "/hi/contact", admin_key=False) == "manage"


async def test_the_operator_reply_sends_no_mail_to_owners() -> None:
    from callobuzz_rag.support import notify

    row = SupportRequest(
        subject="Website enquiry",
        context={"landing_contact": {"name": "P"}},
        created_by_email="p@x.in",
    )
    sent: list[Any] = []
    count = await notify.notify_reply(
        None,  # type: ignore[arg-type]
        row,
        SupportMessage(body="Thanks"),
        Tenant(name="Sahayak"),
        enqueue=lambda *a, **k: sent.append(a),
        product_name="Sahayak",
        dashboard_url="http://x",
    )
    assert count == 0 and not sent


async def test_old_enquiries_are_deleted(world: World) -> None:
    def enquiry(
        days: int, *, page: str = "/contact", user: Any = None, website: bool = True
    ) -> SupportRequest:
        return world.db.add(
            SupportRequest(
                tenant_id=world.operator.id,
                subject=f"{days} days",
                page=page,
                context={"landing_contact": {"name": "Priya"}} if website else {"plan": "Pro"},
                created_by_user_id=user,
                created_by_email="p@x.in",
                last_message_at=NOW - timedelta(days=days),
            )
        )

    enquiry(400)
    # a workspace's own request may name any page, and has no author when filed with a key
    keep = [enquiry(10), enquiry(400, page="/billing", website=False), enquiry(400, website=False)]
    counts = await cleanup_module.cleanup(
        world.db.session(), now=NOW, landing_contact_keep_days=365
    )
    assert counts["landing_contacts"] == 1
    assert world.requests == keep


# ----------------------------------------------------------------- the page and the no-JS form


def form_data(world: World, **over: Any) -> dict[str, str]:
    data = {
        "name": "Priya Sharma",
        "email": "priya@sharmadental.in",
        "phone": "",
        "topic": "sales",
        "message": "We run two clinics in Pune. Can it answer in Marathi too?",
        "website": "",
        "form_token": world.token(),
        "consent": "1",
    }
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


async def test_the_page_has_a_form_that_works_without_javascript(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        res = await client.get("/contact?topic=support&service=Website%20design")
    html = res.text
    assert res.headers["cache-control"] == "no-store"
    assert '<form class="contact-form" method="post" action="/contact"' in html
    for field in ("name", "email", "phone", "topic", "message"):
        assert f'name="{field}"' in html
    for label in ("Your name", "E-mail", "Your message"):
        assert f">{label}</label>" in html
    assert 'autocomplete="name"' in html and 'autocomplete="email"' in html
    assert 'autocomplete="tel"' in html
    assert re.search(r'name="form_token" value="\d+\.[\w-]+"', html)
    assert '<option value="support" selected>' in html
    assert 'value="Website design" readonly' in html
    assert 'href="/legal/privacy"' in html  # the consent line links the Privacy page


async def test_the_honeypot_is_hidden_from_people_and_present_in_the_html(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        html = (await client.get("/contact")).text
    match = re.search(r'<div class="hp" aria-hidden="true">.*?</div>', html, re.S)
    assert match and 'name="website"' in match.group(0) and 'tabindex="-1"' in match.group(0)
    assert 'autocomplete="off"' in match.group(0)


async def test_channels_follow_the_toggles(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    with app_for(world, monkeypatch) as client:
        html = (await client.get("/contact")).text
    assert 'href="https://wa.me/919822012345"' in html
    assert 'href="tel:+919822012345"' in html and 'href="mailto:hello@sharmadental.in"' in html
    assert "12 MG Road" in html


async def test_a_posted_form_redirects_to_the_thank_you(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        res = await client.post("/contact", data=form_data(world), follow_redirects=False)
        assert res.status_code == 303 and res.headers["location"] == "/contact?sent=1"
        done = await client.get("/contact?sent=1")
    assert "Thank you. We have your message" in done.text and len(world.requests) == 1


async def test_a_bad_form_shows_errors_and_keeps_the_text(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        res = await client.post(
            "/contact",
            data=form_data(world, email="", message="Hello there, hello."),
            follow_redirects=False,
        )
    assert res.status_code == 422 and not world.requests
    assert "Please give an e-mail or a phone number" in res.text
    assert "Hello there, hello." in res.text and 'value="Priya Sharma"' in res.text
    assert 'name="form_token" value="' in res.text


async def test_an_oversized_form_is_refused_before_it_is_buffered(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        declared = await client.post(
            "/contact", content=b"x=1", headers={"content-length": "999999"}
        )
        streamed = await client.post("/contact", content=b"m=" + b"a" * 30_000)
    assert declared.status_code == 413 and streamed.status_code == 413
    assert not world.requests


async def test_a_rate_limited_form_keeps_retry_after(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        for _ in range(3):
            await client.post("/contact", data=form_data(world), follow_redirects=False)
        res = await client.post("/contact", data=form_data(world), follow_redirects=False)
    assert res.status_code == 429 and int(res.headers["retry-after"]) > 0


async def test_the_consent_error_is_tied_to_its_checkbox_and_the_phone_bound_matches(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        bad = await client.post("/contact", data=form_data(world, consent=None))
        page = (await client.get("/contact")).text
    assert bad.status_code == 422
    assert 'id="cf-consent"' in bad.text and 'aria-describedby="cf-consent-err"' in bad.text
    assert 'id="cf-consent-err"' in bad.text
    assert 'name="phone" type="tel" autocomplete="tel" maxlength="21"' in page


async def test_a_form_failure_never_says_which_check_caught_it(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    with app_for(world, monkeypatch) as client:
        bot = await client.post("/contact", data=form_data(world, website="http://spam"))
        fast = await client.post("/contact", data=form_data(world, form_token=world.token(1)))
    for res in (bot, fast):
        assert res.status_code == 422 and "Please check the form and try again." in res.text
        assert "honeypot" not in res.text.lower() and "too fast" not in res.text.lower()


async def test_the_form_is_replaced_by_a_sentence_on_a_demo_or_without_an_operator(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.settings.demo_mode = True
    with app_for(world, monkeypatch) as client:
        demo = (await client.get("/contact")).text
    assert "<form" not in demo.split("<main")[1] and "switched off on this demo" in demo
    other = World()
    other.settings.session_secret = world.settings.session_secret
    other.db.rows[Tenant].clear()
    with app_for(other, monkeypatch) as client:
        off = (await client.get("/contact")).text
    assert "<form" not in off.split("<main")[1] and "not working right now" in off


async def test_a_language_prefix_posts_to_its_own_form(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def hinglish(d: dict[str, Any]) -> None:
        d["languages"] = ["en", "hi-Latn"]
        d["contact"]["headline"]["hi-Latn"] = "Humse baat karein."

    from tests.unit.landing_fakes import content_with

    async def live(_request: Any) -> Any:
        return make_site(content_with(hinglish))

    with app_for(world, monkeypatch) as client:
        monkeypatch.setattr(landing_routes, "get_live_site", live)
        html = (await client.get("/hi-latn/contact")).text
        assert 'action="/hi-latn/contact"' in html and "Aapka naam" in html
        res = await client.post("/hi-latn/contact", data=form_data(world), follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"] == "/hi-latn/contact?sent=1"
