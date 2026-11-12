"""LP1 T9: the engine never learns its own address as a website (decision D5)."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.landing import LandingSite
from callobuzz_rag.db.models.widget_key import WidgetKey
from callobuzz_rag.landing import store
from callobuzz_rag.landing.schema import default_content
from callobuzz_rag.website import service
from tests.unit.accounts_fakes import FakeDB


def _world(origins: list[str]) -> tuple[FakeDB, uuid.UUID]:
    db = FakeDB()
    corpus = db.add(
        Corpus(name="Docs", tenant_id=uuid.uuid4(), qdrant_collection="c", api_key_hash="")
    )
    db.add(
        WidgetKey(
            corpus_id=corpus.id,
            name="Site",
            public_key="pk_site",
            allowed_origins=origins,
            is_active=True,
        )
    )
    return db, corpus.id


def _public_url(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    import callobuzz_rag.core.config as config

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(public_url=url))


def _site(db: FakeDB, address: str | None) -> None:
    content = default_content()
    db.add(
        LandingSite(
            id=1,
            enabled=True,
            address=address,
            draft=content.model_dump(mode="json"),
            draft_revision=1,
            draft_theme=store.theme_of(content).to_json(),
        )
    )


async def _sites(db: FakeDB, corpus_id: uuid.UUID) -> dict[str, str]:
    return await service.allowed_sites(db.session(), corpus_id)


async def test_the_engines_own_address_is_not_learnable(monkeypatch: pytest.MonkeyPatch) -> None:
    _public_url(monkeypatch, "https://app.sharmadental.in")
    db, corpus = _world(["https://app.sharmadental.in", "https://sharmadental.in"])
    # the dashboard's address is a subdomain of the clinic's own site, already covered by it
    assert list(await _sites(db, corpus)) == ["sharmadental.in"]


async def test_an_address_that_is_only_the_engine_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _public_url(monkeypatch, "https://app.sharmadental.in")
    db, corpus = _world(["https://app.sharmadental.in"])
    assert await _sites(db, corpus) == {}


async def test_the_websites_own_address_is_dropped_too(monkeypatch: pytest.MonkeyPatch) -> None:
    _public_url(monkeypatch, "")
    db, corpus = _world(["https://www.callobuzz.in", "https://sharmadental.in"])
    _site(db, "https://callobuzz.in")  # www. and the bare host are the same website
    assert list(await _sites(db, corpus)) == ["sharmadental.in"]


async def test_with_no_public_url_and_no_site_nothing_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _public_url(monkeypatch, "")
    db, corpus = _world(["https://sharmadental.in", "https://www.sharmadental.in"])
    assert list(await _sites(db, corpus)) == ["sharmadental.in"]
    _site(db, None)
    assert list(await _sites(db, corpus)) == ["sharmadental.in"]


async def test_a_public_url_that_is_not_an_address_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for junk in ("localhost", "not a url", "http://127.0.0.1:8000"):
        _public_url(monkeypatch, junk)
        db, corpus = _world(["https://sharmadental.in"])
        assert list(await _sites(db, corpus)) == ["sharmadental.in"], junk


async def test_the_scopes_helper_reads_both_places(monkeypatch: pytest.MonkeyPatch) -> None:
    _public_url(monkeypatch, "https://rag.callobuzz.in")
    db = FakeDB()
    _site(db, "https://www.callobuzz.in")
    scopes: Any = await service.own_scopes(db.session())
    assert scopes == {"rag.callobuzz.in", "callobuzz.in"}
