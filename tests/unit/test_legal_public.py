"""P3 T4: the public pages (HTML and JSON) and the branding footer links."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.legal import LegalDocument
from callobuzz_rag.demo import guard
from tests.unit.test_auth_routes import World, auth_app, req

CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


@pytest.fixture
def world() -> World:
    return World()


def publish(world: World, slug: str, version: int, html: str, title: str = "Terms") -> None:
    when = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    world.db.add(
        LegalDocument(
            slug=slug,
            version=version,
            title=title,
            body_md="x",
            body_filled=f"# {title}\n\nbody v{version}",
            body_html=html,
            revision=1,
            published_at=when,
            created_at=when,
            updated_at=when,
        )
    )


def settings(world: World, **sections: Any) -> None:
    world.db.add(EngineSettingsRow(id=1, value=dict(sections)))
    invalidate_engine_settings_cache()


async def get(app: Any, path: str) -> Any:
    return await req(app, "GET", path)  # no credential at all


async def test_json_list_is_empty_until_something_is_published(world: World) -> None:
    with auth_app(world) as app:
        res = await get(app, "/api/v1/public/legal")
    assert res.status_code == 200, res.text
    assert res.json() == {"items": []}


async def test_json_list_and_page_show_the_newest_version(world: World) -> None:
    publish(world, "terms", 1, "<h1>Terms</h1><p>old</p>")
    publish(world, "terms", 2, "<h1>Terms</h1><p>new</p>")
    with auth_app(world) as app:
        listed = await get(app, "/api/v1/public/legal")
        page = await get(app, "/api/v1/public/legal/terms")
        unpublished = await get(app, "/api/v1/public/legal/privacy")
        unknown = await get(app, "/api/v1/public/legal/nonsense")
    (item,) = listed.json()["items"]
    assert item["slug"] == "terms" and item["version"] == 2
    assert item["url"] == "http://t/legal/terms"
    body = page.json()
    assert body["version"] == 2 and "new" in body["html"] and body["markdown"].endswith("v2")
    for res in (unpublished, unknown):
        assert res.status_code == 404
        assert res.json()["code"] == "legal_page_not_found"


async def test_html_page(world: World) -> None:
    publish(world, "terms", 2, "<h1>Terms of Service</h1><p>new text</p>")
    publish(world, "privacy", 1, "<h1>Privacy</h1>", title="Privacy Notice")
    settings(world, branding={"product_name": "Sahayak"})
    with auth_app(world) as app:
        res = await get(app, "/legal/terms")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "text/html; charset=utf-8"
    html = res.text
    assert "Sahayak" in html and "Version 2" in html and "Last updated 8 October 2026" in html
    assert "new text" in html
    assert 'href="/legal/privacy"' in html and "Privacy Notice" in html
    assert "<script" not in html.lower()
    assert res.headers["content-security-policy"] == CSP
    assert res.headers["x-content-type-options"] == "nosniff"


async def test_unpublished_page_is_an_html_404(world: World) -> None:
    with auth_app(world) as app:
        missing = await get(app, "/legal/terms")
        unknown = await get(app, "/legal/nonsense")
    for res in (missing, unknown):
        assert res.status_code == 404
        assert res.headers["content-type"].startswith("text/html")
        assert "Page not found" in res.text
        assert res.headers["content-security-policy"] == CSP


async def test_page_title_is_escaped(world: World) -> None:
    publish(world, "terms", 1, "<h1>Terms</h1>", title="<img src=x onerror=alert(1)>")
    with auth_app(world) as app:
        res = await get(app, "/legal/terms")
    assert "<img src=x" not in res.text


def test_these_routes_need_no_credential_and_the_demo_leaves_them_alone() -> None:
    from callobuzz_rag.api.permissions import PUBLIC_ROUTES

    assert ("GET", "/api/v1/public/legal") in PUBLIC_ROUTES
    assert ("GET", "/api/v1/public/legal/{slug}") in PUBLIC_ROUTES
    for path in ("/legal/terms", "/api/v1/public/legal", "/api/v1/public/legal/terms"):
        assert guard.refusal("GET", path, admin_key=False) is None
    # the sign-up e-mails are not shown to demo visitors
    assert guard.refusal("GET", "/api/v1/admin/legal/acceptances", admin_key=False) == "privacy"
    assert guard.refusal("PUT", "/api/v1/admin/legal/settings", admin_key=False) == "manage"
    assert guard.refusal("POST", "/api/v1/admin/legal/terms/publish", admin_key=False) == "manage"


async def test_branding_lists_published_pages_for_the_footer(world: World) -> None:
    publish(world, "terms", 1, "<h1>T</h1>")
    publish(world, "refunds", 1, "<h1>R</h1>", title="Refund Policy")
    with auth_app(world) as app:
        res = await get(app, "/api/v1/public/branding")
    assert res.status_code == 200, res.text
    links = res.json()["legal"]
    assert [link["slug"] for link in links] == ["terms", "refunds"]
    assert links[0]["url"] == "http://t/legal/terms" and links[1]["title"] == "Refund Policy"


async def test_branding_has_no_links_when_the_footer_is_off(world: World) -> None:
    publish(world, "terms", 1, "<h1>T</h1>")
    settings(world, legal={"show_in_footer": False})
    with auth_app(world) as app:
        res = await get(app, "/api/v1/public/branding")
    assert res.json()["legal"] == []


async def test_branding_has_no_links_when_nothing_is_published(world: World) -> None:
    with auth_app(world) as app:
        res = await get(app, "/api/v1/public/branding")
    assert res.json()["legal"] == []
