"""Suggested first questions from the pages the assistant learned (BIZ4): no model, instant."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from callobuzz_rag.db.models.assistant_config import AssistantConfigVersion
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.website import WebsitePage, WebsiteSource
from callobuzz_rag.website.suggest import GENERIC, suggest
from tests.unit.test_auth_routes import ADMIN, PASSWORD, World, login, req
from tests.unit.test_website_api import with_app

pytestmark = pytest.mark.usefixtures("unlimited_plan")

PAGES = [  # (url, title, rank_score) of learned pages
    ("https://shreedental.in/pricing", "Fees", 410),
    ("https://shreedental.in/treatments/root-canal", "Root Canal Treatment", 150),
    ("https://shreedental.in/contact-us", "Contact us", 310),
    ("https://shreedental.in/blog/2019/myths", "5 myths", -190),
]


def test_english_suggestions_follow_page_rank() -> None:
    assert suggest(PAGES, card_hours=True, lang="en") == [
        "What are your prices?",
        "Where are you located?",
        "Tell me about Root Canal Treatment",
        "What are your opening hours?",
    ]


def test_hinglish_suggestions() -> None:
    assert suggest(PAGES, card_hours=False, lang="hi-Latn") == [
        "Aapki fees kitni hai?",
        "Aapka address kya hai?",
        "Root Canal Treatment ke baare mein batayein",
    ]


def test_at_most_six_and_never_from_low_ranked_pages() -> None:
    pages = [
        (f"https://shreedental.in/treatments/t{i}", f"Treatment {i}", 100 - i) for i in range(12)
    ]
    pages += [("https://shreedental.in/old-offers", "Old offers", -50)]
    out = suggest(pages, card_hours=True, lang="en")
    assert len(out) == 6 and len(set(out)) == 6
    assert out[0] == "Tell me about Treatment 0"
    assert "What are your opening hours?" not in out[:5]  # hours come last, if there is room
    assert not any("Old offers" in q for q in out)


def test_no_pages_gives_generic_starters() -> None:
    assert suggest([], card_hours=False, lang="en") == [
        "What services do you offer?",
        "What are your prices?",
        "Where are you located?",
    ]
    assert suggest([], card_hours=True, lang="en")[-1] == "What are your opening hours?"
    assert suggest([], card_hours=False, lang="hi-Latn")[0] == "Aap kaun si services dete hain?"
    assert set(GENERIC) == {"en", "hi-Latn"}


def test_pages_that_say_nothing_useful_fall_back_to_the_starters() -> None:
    pages = [
        ("https://shreedental.in/", "Home", 1000),
        ("https://shreedental.in/about", "About", 200),
    ]
    out = suggest(pages, card_hours=False, lang="en")
    assert "What services do you offer?" in out


def test_an_hours_page_and_the_card_do_not_ask_twice() -> None:
    pages = [("https://shreedental.in/timings", "Clinic timings", 300)]
    out = suggest(pages, card_hours=True, lang="en")
    assert out.count("What are your opening hours?") == 1


def test_titles_lose_the_site_name_and_stay_short() -> None:
    pages = [
        ("https://shreedental.in/services/implants", "Dental Implants | Shree Dental Clinic", 120),
        ("https://shreedental.in/services/x", "A" * 120, 110),
        ("https://shreedental.in/services/teeth-whitening", "", 100),
    ]
    out = suggest(pages, card_hours=False, lang="en")
    assert "Tell me about Dental Implants" in out
    assert "Tell me about Teeth Whitening" in out
    assert all(len(q) <= 80 for q in out)


def test_booking_and_refund_pages_have_their_own_questions() -> None:
    pages = [
        ("https://shreedental.in/book-appointment", "Book", 300),
        ("https://shop.in/refund-policy", "Refunds", 200),
        ("https://shop.in/shipping", "Shipping", 150),
    ]
    out = suggest(pages, card_hours=False, lang="en")
    assert out == [
        "How can I book an appointment?",
        "What is your refund policy?",
        "Do you deliver, and how long does it take?",
    ]


# ---------------------------------------------------------------- the route


def seed(world: World) -> Corpus:
    corpus = world.db.add(
        Corpus(
            name="Shree Dental",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_suggest",
            api_key_hash="",
        )
    )
    source = world.db.add(
        WebsiteSource(
            corpus_id=corpus.id,
            tenant_id=world.shree.id,
            origin="https://shreedental.in",
            scope="shreedental.in",
        )
    )
    for url, title, score in PAGES:
        world.db.add(
            WebsitePage(
                source_id=source.id,
                corpus_id=corpus.id,
                url=url,
                url_hash=uuid.uuid4().hex,
                title=title,
                rank_score=score,
                status="learned",
            )
        )
    world.db.add(
        WebsitePage(
            source_id=source.id,
            corpus_id=corpus.id,
            url="https://shreedental.in/login",
            url_hash=uuid.uuid4().hex,
            title="Contact us",
            rank_score=900,
            status="skipped",  # never learned: no question from it
        )
    )
    return corpus


async def ask(app: Any, corpus: Corpus, query: str = "", **kw: Any) -> Any:
    return await req(
        app, "GET", f"/api/v1/corpora/{corpus.id}/website/suggestions{query}", key=ADMIN, **kw
    )


async def test_the_route_suggests_from_learned_pages_and_the_card() -> None:
    world = World()
    corpus = seed(world)
    world.db.add(
        AssistantConfigVersion(
            corpus_id=corpus.id,
            version=1,
            status="published",
            config={
                "identity": {"card": {"hours": [{"day": "mon", "open": "10:00", "close": "19:00"}]}}
            },
            schema_version=1,
            created_by="owner",
            created_at=datetime(2026, 10, 5, tzinfo=UTC),
        )
    )
    with with_app(world) as app:
        res = await ask(app, corpus)
        hinglish = await ask(app, corpus, "?lang=hi-Latn")
    assert res.status_code == 200, res.text
    assert [i["text"] for i in res.json()["items"]] == [
        "What are your prices?",
        "Where are you located?",
        "Tell me about Root Canal Treatment",
        "What are your opening hours?",
    ]
    assert hinglish.json()["items"][0] == {"text": "Aapki fees kitni hai?"}
    assert "Aapke khulne ka time kya hai?" in [i["text"] for i in hinglish.json()["items"]]


async def test_a_knowledge_base_with_nothing_learned_gets_the_starters() -> None:
    world = World()
    corpus = world.db.add(
        Corpus(
            name="Empty",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_empty",
            api_key_hash="",
        )
    )
    with with_app(world) as app:
        res = await ask(app, corpus)
    assert res.json()["items"][0]["text"] == "What services do you offer?"


async def test_an_unknown_language_is_refused() -> None:
    world = World()
    corpus = seed(world)
    with with_app(world) as app:
        res = await ask(app, corpus, "?lang=de")
    assert res.status_code == 422


async def test_another_workspaces_knowledge_base_is_not_visible() -> None:
    world = World()
    corpus = seed(world)
    other = world.user("meera@pune.example")
    world.member(other, world.pune, "owner")
    with with_app(world) as app:
        token = (await login(app, "meera@pune.example", PASSWORD)).json()["access_token"]
        res = await req(
            app,
            "GET",
            f"/api/v1/corpora/{corpus.id}/website/suggestions",
            token=token,
        )
    assert res.status_code in (403, 404)
