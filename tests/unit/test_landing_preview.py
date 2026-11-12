"""LP1 T5: signed preview links for the unpublished draft."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import SecretStr

from callobuzz_rag.db.models.landing import LandingSite
from callobuzz_rag.landing import store
from callobuzz_rag.landing.preview import PreviewTokenError, make_preview_token, read_preview_token
from callobuzz_rag.landing.schema import default_content
from tests.unit.accounts_fakes import FakeDB
from tests.unit.landing_fakes import SECRET, make_client, make_settings

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
OTHER = "o" * 40


@pytest.fixture
def settings_with_secret() -> Any:
    return make_settings()


@pytest.fixture
def other_secret_settings() -> Any:
    return make_settings(session_secret=SecretStr(OTHER))


@pytest.fixture
def rotated_settings() -> Any:
    return make_settings(session_secret=SecretStr(f"{OTHER},{SECRET}"))


def test_round_trip_carries_page_and_language(settings_with_secret: Any) -> None:
    token = make_preview_token(settings_with_secret, page="pricing", lang="hi", now=NOW)
    claims = read_preview_token(settings_with_secret, token, now=NOW + timedelta(minutes=14))
    assert (claims.page, claims.lang) == ("pricing", "hi")


def test_expired_tampered_and_foreign_tokens_are_refused(
    settings_with_secret: Any, other_secret_settings: Any
) -> None:
    token = make_preview_token(settings_with_secret, page="home", lang="en", now=NOW)
    with pytest.raises(PreviewTokenError):
        read_preview_token(settings_with_secret, token, now=NOW + timedelta(minutes=16))
    body, sig = token.rsplit(".", 1)
    with pytest.raises(PreviewTokenError):
        read_preview_token(settings_with_secret, f"{body}x.{sig}", now=NOW)
    with pytest.raises(PreviewTokenError):
        read_preview_token(other_secret_settings, token, now=NOW)
    for junk in ("", "abc", "a.b", ".", "x" * 80):
        with pytest.raises(PreviewTokenError):
            read_preview_token(settings_with_secret, junk, now=NOW)


def test_a_rotated_secret_still_reads_tokens_made_with_the_previous_one(
    rotated_settings: Any, settings_with_secret: Any
) -> None:
    token = make_preview_token(settings_with_secret, page="home", lang="en", now=NOW)
    assert read_preview_token(rotated_settings, token, now=NOW).page == "home"
    fresh = make_preview_token(rotated_settings, page="home", lang="en", now=NOW)
    assert read_preview_token(rotated_settings, fresh, now=NOW).lang == "en"


# ------------------------------------------------------------------ the route


def _db_with_draft(headline: str) -> FakeDB:
    db = FakeDB()
    data = default_content().model_dump(mode="json")
    hero = next(s for s in data["home"] if s["type"] == "hero")
    hero["headline"] = {"en": headline}
    from callobuzz_rag.landing.schema import LandingContent

    content = LandingContent.model_validate(data)
    db.add(
        LandingSite(
            id=1,
            enabled=False,
            draft=content.model_dump(mode="json"),
            draft_revision=2,
            draft_theme=store.theme_of(content).to_json(),
            published_version=None,
        )
    )
    return db


async def test_the_preview_shows_the_draft_with_locked_down_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(monkeypatch, site=None, db=_db_with_draft("A draft headline"))
    token = make_preview_token(make_settings(), page="home", lang="en")
    res = await client.get(f"/_landing/preview/home?t={token}&lang=en")  # works while off
    assert res.status_code == 200 and "A draft headline" in res.text
    assert res.headers["x-robots-tag"] == "noindex, nofollow"
    assert res.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'self' http://127.0.0.1:8090" in res.headers["content-security-policy"]
    assert 'name="robots" content="noindex, nofollow"' in res.text
    assert "Preview of your draft" in res.text
    assert "cob.rt" not in res.text  # the signed-in redirect never runs inside the editor


async def test_an_expired_or_wrong_token_is_a_403_page(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(monkeypatch, site=None, db=_db_with_draft("x"))
    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    old = make_preview_token(make_settings(), page="home", lang="en", now=long_ago)
    res = await client.get(f"/_landing/preview/home?t={old}&lang=en")
    assert res.status_code == 403 and "This preview link has expired" in res.text
    assert res.headers["cache-control"] == "no-store"
    fresh = make_preview_token(make_settings(), page="home", lang="en")
    # a token for the home page does not open the pricing page, nor another language
    assert (await client.get(f"/_landing/preview/pricing?t={fresh}&lang=en")).status_code == 403
    assert (await client.get(f"/_landing/preview/home?t={fresh}&lang=hi")).status_code == 403
    assert (await client.get("/_landing/preview/home?lang=en")).status_code == 403
    assert (await client.get(f"/_landing/preview/nowhere?t={fresh}&lang=en")).status_code == 403


async def test_a_language_the_draft_does_not_offer_is_a_noindex_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client(monkeypatch, site=None, db=_db_with_draft("x"))
    token = make_preview_token(make_settings(), page="home", lang="hi")
    res = await client.get(f"/_landing/preview/home?t={token}&lang=hi")
    assert res.status_code == 404 and res.headers["x-robots-tag"] == "noindex, nofollow"
