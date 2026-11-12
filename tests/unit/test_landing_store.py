"""LP1 T4: the draft, publish, restore and settings rules, and the "Ask it yourself" status."""

import uuid
from typing import Any

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.landing import LandingAsset, LandingSite
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.widget_key import WidgetKey
from callobuzz_rag.landing import service, store
from callobuzz_rag.landing.schema import LandingContent, default_content
from tests.unit.accounts_fakes import FakeDB


def _content(**theme: object) -> LandingContent:
    data = default_content().model_dump(mode="json")
    data["theme"].update(theme)
    return LandingContent.model_validate(data)


async def _site(db: FakeDB) -> LandingSite:
    return await store.ensure_site(db.session())


async def test_ensure_site_creates_the_row_once_with_the_default() -> None:
    db = FakeDB()
    session = db.session()
    first = await store.ensure_site(session)
    again = await store.ensure_site(session)
    assert first is again and len(db.all(LandingSite)) == 1
    assert first.id == 1 and first.enabled is False and first.draft_revision == 1
    assert first.published_version is None
    assert LandingContent.model_validate(first.draft) == default_content()
    assert first.draft_theme["light"]["brand"]


async def test_saving_a_draft_bumps_the_revision_and_recomputes_the_theme() -> None:
    db = FakeDB()
    site = await _site(db)
    before = site.draft_theme
    await service.save_draft(
        db.session(), base_revision=1, content=_content(brand_color="#7c3aed"), updated_by="admin"
    )
    assert site.draft_revision == 2
    assert site.draft_theme != before and site.draft["theme"]["brand_color"] == "#7c3aed"


async def test_a_stale_revision_is_a_conflict() -> None:
    db = FakeDB()
    await _site(db)
    session = db.session()
    await service.save_draft(session, base_revision=1, content=_content(), updated_by="a")
    with pytest.raises(APIError) as err:
        await service.save_draft(session, base_revision=1, content=_content(), updated_by="b")
    assert err.value.status_code == 409 and err.value.code == "landing_revision_conflict"


async def test_publish_freezes_the_draft_as_version_one_then_two() -> None:
    db = FakeDB()
    await _site(db)
    session = db.session()
    one = await service.publish(session, base_revision=1, change_note=" first ", published_by="a")
    assert one.version == 1 and one.change_note == "first"
    assert one.content_hash and one.theme_css.startswith(":root{")
    site = await store.ensure_site(session)
    assert site.published_version == 1
    await service.save_draft(
        session, base_revision=1, content=_content(brand_color="#7c3aed"), updated_by="a"
    )
    two = await service.publish(session, base_revision=2, change_note=None, published_by="a")
    assert two.version == 2 and two.content["theme"]["brand_color"] == "#7c3aed"
    assert one.content["theme"]["brand_color"] == "#0f766e"  # version 1 stays as it was
    assert one.content_hash != two.content_hash


async def test_publishing_a_language_with_missing_texts_lists_the_paths() -> None:
    db = FakeDB()
    await _site(db)
    session = db.session()
    data = default_content().model_dump(mode="json")
    data["languages"] = ["en", "hi"]
    await service.save_draft(
        session, base_revision=1, content=LandingContent.model_validate(data), updated_by="a"
    )
    with pytest.raises(APIError) as err:
        await service.publish(session, base_revision=2, change_note=None, published_by="a")
    assert err.value.status_code == 409 and err.value.code == "landing_translation_missing"
    assert "home.hero.headline" in err.value.extra["paths"]
    assert "Hindi" in err.value.detail


async def test_a_section_pointing_at_a_deleted_asset_cannot_be_published() -> None:
    db = FakeDB()
    await _site(db)
    session = db.session()
    data = default_content().model_dump(mode="json")
    data["theme"]["logo_asset_id"] = str(uuid.uuid4())
    await service.save_draft(
        session, base_revision=1, content=LandingContent.model_validate(data), updated_by="a"
    )
    with pytest.raises(APIError) as err:
        await service.publish(session, base_revision=2, change_note=None, published_by="a")
    assert err.value.status_code == 409 and err.value.code == "landing_asset_missing"


async def test_an_existing_asset_passes_the_check() -> None:
    db = FakeDB()
    await _site(db)
    session = db.session()
    asset = db.add(
        LandingAsset(
            kind="logo",
            content_type="image/png",
            bytes=10,
            sha256="0" * 64,
            storage_key="k",
            created_by="a",
        )
    )
    data = default_content().model_dump(mode="json")
    data["theme"]["logo_asset_id"] = str(asset.id)
    await service.save_draft(
        session, base_revision=1, content=LandingContent.model_validate(data), updated_by="a"
    )
    row = await service.publish(session, base_revision=2, change_note=None, published_by="a")
    assert row.version == 1


async def test_restore_copies_a_version_into_the_draft() -> None:
    db = FakeDB()
    await _site(db)
    session = db.session()
    await service.publish(session, base_revision=1, change_note=None, published_by="a")
    await service.save_draft(
        session, base_revision=1, content=_content(brand_color="#7c3aed"), updated_by="a"
    )
    site = await service.restore(session, version=1, base_revision=2, updated_by="a")
    assert site.draft["theme"]["brand_color"] == "#0f766e" and site.draft_revision == 3
    with pytest.raises(APIError) as err:
        await service.restore(session, version=9, base_revision=3, updated_by="a")
    assert err.value.status_code == 404 and err.value.code == "landing_version_not_found"


async def _settings(session: Any, **kw: Any) -> Any:
    args: dict[str, Any] = {
        "enabled": None,
        "address": None,
        "address_given": False,
        "public_url": "",
        "prod": True,
        "updated_by": "a",
    }
    return await service.update_settings(session, **{**args, **kw})


async def test_switching_on_needs_a_published_version_and_an_address() -> None:
    db = FakeDB()
    await _site(db)
    session = db.session()
    with pytest.raises(APIError) as err:
        await _settings(session, enabled=True, address="https://www.sharmadental.in")
    assert err.value.code == "landing_nothing_published"
    await service.publish(session, base_revision=1, change_note=None, published_by="a")
    with pytest.raises(APIError) as err:
        await _settings(session, enabled=True)
    assert err.value.code == "landing_needs_address"
    done = await _settings(session, enabled=True, public_url="https://app.sharmadental.in/x")
    assert done.changed == ["enabled"] and done.site.enabled is True
    done = await _settings(session, address="https://www.sharmadental.in/", address_given=True)
    assert done.changed == ["address"] and done.site.address == "https://www.sharmadental.in"


@pytest.mark.parametrize(
    "address",
    [
        "http://www.sharmadental.in",
        "https://www.sharmadental.in/pricing",
        "https://sharmadental.in?x=1",
        "https://localhost",
        "https://-bad.in",
        "javascript:alert(1)",
        "www.sharmadental.in",
    ],
)
async def test_the_address_is_a_bare_https_origin_in_production(address: str) -> None:
    db = FakeDB()
    await _site(db)
    with pytest.raises(APIError) as err:
        await _settings(db.session(), address=address, address_given=True)
    assert err.value.status_code == 422 and err.value.code == "landing_address_invalid"


async def test_a_local_address_is_allowed_only_outside_production() -> None:
    db = FakeDB()
    await _site(db)
    done = await _settings(
        db.session(), address="http://127.0.0.1:8000", address_given=True, prod=False
    )
    assert done.site.address == "http://127.0.0.1:8000"
    with pytest.raises(APIError):
        await _settings(db.session(), address="http://127.0.0.1:8000", address_given=True)


async def test_clearing_the_address_falls_back_to_the_public_url() -> None:
    db = FakeDB()
    await _site(db)
    session = db.session()
    await _settings(session, address="https://www.sharmadental.in", address_given=True)
    done = await _settings(session, address=None, address_given=True)
    assert done.site.address is None and done.changed == ["address"]
    assert service.effective_address(None, "https://app.sharmadental.in/dashboard/") == (
        "https://app.sharmadental.in"
    )
    assert service.effective_address(None, "") is None
    assert service.effective_address("https://a.in", "https://b.in") == "https://a.in"


async def test_colour_and_logo_changes_are_saved() -> None:
    db = FakeDB()
    await _site(db)
    session = db.session()
    site = await service.save_draft(
        session, base_revision=1, content=_content(brand_color="#7c3aed"), updated_by="a"
    )
    assert site.draft["theme"]["brand_color"] == "#7c3aed"


# ------------------------------------------------------------------ ask_status


def _kb(db: FakeDB, *, origins: list[str], active: bool = True) -> tuple[Corpus, WidgetKey]:
    operator = db.add(Tenant(name="Op", is_operator=True))
    corpus = db.add(
        Corpus(name="Clinic", tenant_id=operator.id, qdrant_collection="c", api_key_hash="")
    )
    key = db.add(
        WidgetKey(
            corpus_id=corpus.id,
            name="Site",
            public_key="pk_demo",
            allowed_origins=origins,
            is_active=active,
        )
    )
    return corpus, key


@pytest.mark.parametrize(
    ("origins", "active", "origin", "expected"),
    [
        (["https://www.sharmadental.in"], True, "https://www.sharmadental.in", "ready"),
        (["https://WWW.sharmadental.in/"], True, "https://www.sharmadental.in", "ready"),
        (["https://other.in"], True, "https://www.sharmadental.in", "origin_missing"),
        (["https://www.sharmadental.in"], True, None, "origin_missing"),
        (["https://www.sharmadental.in"], False, "https://www.sharmadental.in", "key_inactive"),
    ],
)
async def test_ask_status(
    origins: list[str], active: bool, origin: str | None, expected: str
) -> None:
    db = FakeDB()
    site = await _site(db)
    corpus, key = _kb(db, origins=origins, active=active)
    site.ask_corpus_id, site.ask_widget_key_id = corpus.id, key.id
    assert await service.ask_status(db.session(), site, origin) == expected


async def test_ask_status_not_connected_and_kb_missing() -> None:
    db = FakeDB()
    site = await _site(db)
    assert await service.ask_status(db.session(), site, "https://a.in") == "not_connected"
    corpus, key = _kb(db, origins=["https://a.in"])
    site.ask_widget_key_id = key.id
    db.rows[Corpus].remove(corpus)
    assert await service.ask_status(db.session(), site, "https://a.in") == "kb_missing"
    site.ask_widget_key_id = uuid.uuid4()  # the key itself is gone
    assert await service.ask_status(db.session(), site, "https://a.in") == "not_connected"


async def test_warnings_in_plain_words() -> None:
    db = FakeDB()
    site = await _site(db)
    session = db.session()
    content = LandingContent.model_validate(site.draft)
    found = await service.warnings(session, site, content, address=None, ask="not_connected")
    assert {w.code for w in found} == {"address_missing", "nothing_published"}
    data = content.model_dump(mode="json")
    data["ask"]["enabled"] = True
    found = await service.warnings(
        session,
        site,
        LandingContent.model_validate(data),
        address="https://a.in",
        ask="origin_missing",
    )
    assert "ask_not_connected" in {w.code for w in found}


async def test_ask_status_drops_when_the_kb_leaves_the_operator() -> None:
    from callobuzz_rag.db.models.tenant import Tenant

    db = FakeDB()
    site = await _site(db)
    corpus, key = _kb(db, origins=["https://a.in"])
    site.ask_corpus_id, site.ask_widget_key_id = corpus.id, key.id
    assert await service.ask_status(db.session(), site, "https://a.in") == "ready"
    customer = db.add(Tenant(name="Customer", is_operator=False))
    corpus.tenant_id = customer.id  # moved to a customer's workspace
    assert await service.ask_status(db.session(), site, "https://a.in") == "kb_missing"
