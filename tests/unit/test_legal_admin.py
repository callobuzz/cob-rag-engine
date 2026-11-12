"""P3 T3: drafts, preview, publish and the legal settings, through the real app on the in-memory
database of `accounts_fakes.py`."""

from collections.abc import Iterator
from typing import Any

import pytest

from callobuzz_rag.accounts.audit import AUDIT_ACTIONS
from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.legal import LegalDocument
from callobuzz_rag.legal.store import SLUGS
from tests.unit.test_auth_routes import ADMIN, World, auth_app, login, req

COMPANY = {
    "legal_name": "Sharma Furniture",
    "address_line1": "12 MG Road",
    "city": "Pune",
    "state_code": "27",
    "pincode": "411001",
    "email": "accounts@sharmafurniture.in",
    "phone": "+91 98765 43210",
}
LEGAL = {
    "grievance_name": "Asha Sharma",
    "grievance_email": "grievance@sharmafurniture.in",
    "grievance_address": "12 MG Road, Pune 411001",
    "jurisdiction_city": "Pune",
    "website": "https://sharmafurniture.in",
}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


@pytest.fixture
def world() -> World:
    return World()


def set_settings(world: World, **sections: Any) -> None:
    for row in world.db.all(EngineSettingsRow):
        world.db.rows[EngineSettingsRow].remove(row)
    world.db.add(EngineSettingsRow(id=1, value=dict(sections)))
    invalidate_engine_settings_cache()


def fully_configured(world: World) -> None:
    set_settings(
        world,
        company=COMPANY,
        legal=LEGAL,
        branding={"product_name": "Sahayak", "support_email": "help@sharmafurniture.in"},
    )


async def api(app: Any, method: str, path: str = "", *, key: str | None = ADMIN, **kw: Any) -> Any:
    return await req(app, method, f"/api/v1/admin/legal{path}", key=key, **kw)


async def test_list_shows_the_eight_pages_unpublished(world: World) -> None:
    with auth_app(world) as app:
        res = await api(app, "GET")
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert [i["slug"] for i in items] == list(SLUGS)
    for item in items:
        assert item["published_version"] is None
        assert item["has_draft"] is False
        assert item["draft_changed"] is False
        assert item["default_available"] is True
        assert item["company_changed"] is False


async def test_get_without_a_draft_returns_the_default_template(world: World) -> None:
    with auth_app(world) as app:
        res = await api(app, "GET", "/terms")
    body = res.json()
    assert res.status_code == 200, res.text
    assert body["revision"] == 0 and body["is_default_template"] is True
    assert body["markdown"].startswith("[//]: # (TEMPLATE")
    assert "legal_name" in body["missing"]  # nothing is configured yet
    assert body["review_note"].startswith("This is a starting template")


async def test_unknown_slug_is_404(world: World) -> None:
    with auth_app(world) as app:
        res = await api(app, "GET", "/nonsense")
    assert res.status_code == 404
    assert res.json()["code"] == "legal_page_not_found"


async def test_draft_create_and_conflict(world: World) -> None:
    with auth_app(world) as app:
        first = await api(
            app,
            "PUT",
            "/terms/draft",
            json={"title": "Terms", "markdown": "# Terms\n\nv1", "base_revision": 0},
        )
        again = await api(
            app,
            "PUT",
            "/terms/draft",
            json={"title": "Terms", "markdown": "# Terms\n\nv1b", "base_revision": 0},
        )
        second = await api(
            app,
            "PUT",
            "/terms/draft",
            json={"title": "Terms", "markdown": "# Terms\n\nv2", "base_revision": 1},
        )
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1 and first.json()["is_default_template"] is False
    assert again.status_code == 409 and again.json()["code"] == "legal_draft_conflict"
    assert second.status_code == 200 and second.json()["revision"] == 2
    assert second.json()["markdown"].endswith("v2")


async def test_a_draft_body_is_strict(world: World) -> None:
    with auth_app(world) as app:
        res = await api(
            app,
            "PUT",
            "/terms/draft",
            json={"title": "T", "markdown": "x", "base_revision": 0, "surprise": 1},
        )
    assert res.status_code == 422


async def test_preview_marks_missing_values(world: World) -> None:
    with auth_app(world) as app:
        res = await api(
            app,
            "POST",
            "/terms/preview",
            json={"markdown": "# T\n\nWrite to {{grievance_email}} at {{product_name}}"},
        )
    body = res.json()
    assert res.status_code == 200, res.text
    assert "[[grievance_email]]" in body["html"]
    assert body["missing"] == ["grievance_email"]
    assert "cob-rag-engine" in body["html"]


async def test_publish_needs_review_confirmation_and_every_value(world: World) -> None:
    with auth_app(world) as app:
        unconfirmed = await api(app, "POST", "/terms/publish", json={})
        missing = await api(app, "POST", "/terms/publish", json={"confirm_reviewed": True})
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["code"] == "legal_review_required"
    assert missing.status_code == 422
    body = missing.json()
    assert body["code"] == "legal_placeholders_missing"
    assert "legal_name" in body["missing"] and "grievance_email" in body["missing"]
    assert world.db.all(LegalDocument) == []  # nothing was published, no draft left behind


async def test_publish_fills_freezes_and_versions(world: World) -> None:
    fully_configured(world)
    with auth_app(world) as app:
        first = await api(
            app,
            "POST",
            "/terms/publish",
            json={"confirm_reviewed": True, "change_note": "First version"},
        )
        edited = await api(
            app,
            "PUT",
            "/terms/draft",
            json={
                "title": "Terms",
                "markdown": "# Terms\n\nSecond {{legal_name}}",
                "base_revision": 1,
            },
        )
        second = await api(app, "POST", "/terms/publish", json={"confirm_reviewed": True})
    assert first.status_code == 200, first.text
    assert first.json()["published"]["version"] == 1
    assert first.json()["published"]["change_note"] == "First version"
    assert edited.status_code == 200, edited.text
    assert second.json()["published"]["version"] == 2
    assert [h["version"] for h in second.json()["history"]] == [2, 1]
    docs = {d.version: d for d in world.db.all(LegalDocument) if d.slug == "terms"}
    assert set(docs) == {None, 1, 2}
    assert "Sharma Furniture" in (docs[1].body_filled or "")
    assert "{{" not in (docs[1].body_filled or "") and "[[" not in (docs[1].body_filled or "")
    assert docs[1].body_html and "<script" not in docs[1].body_html
    assert "Second Sharma Furniture" in (docs[2].body_filled or "")
    assert "Sharma Furniture" in docs[2].body_filled and "Second" not in (docs[1].body_filled or "")
    audits = [a for a in world.db.all(AuditLog) if a.action == "legal.page_published"]
    assert [a.details["version"] for a in audits] == [1, 2]
    assert audits[0].details == {"slug": "terms", "version": 1, "change_note": "First version"}


async def test_company_changed_is_flagged_until_published_again(world: World) -> None:
    fully_configured(world)
    with auth_app(world) as app:
        await api(app, "POST", "/privacy/publish", json={"confirm_reviewed": True})
        before = (await api(app, "GET", "/privacy")).json()["company_changed"]
        set_settings(
            world,
            company={**COMPANY, "city": "Mumbai"},
            legal=LEGAL,
            branding={"product_name": "Sahayak", "support_email": "help@sharmafurniture.in"},
        )
        after = (await api(app, "GET", "/privacy")).json()["company_changed"]
        listed = (await api(app, "GET")).json()["items"]
        await api(app, "POST", "/privacy/publish", json={"confirm_reviewed": True})
        republished = (await api(app, "GET", "/privacy")).json()["company_changed"]
    assert before is False and after is True and republished is False
    assert next(i for i in listed if i["slug"] == "privacy")["company_changed"] is True


async def test_only_the_operator_may_use_it(world: World) -> None:
    owner = world.user("asha@shree.example")
    world.member(owner, world.shree, "owner")
    with auth_app(world) as app:
        token = (await login(app, "asha@shree.example")).json()["access_token"]
        as_owner = await req(app, "GET", "/api/v1/admin/legal", token=token)
        no_key = await req(app, "GET", "/api/v1/admin/legal")
    assert as_owner.status_code == 403
    assert no_key.status_code == 401


async def test_legal_settings_validate_and_audit(world: World) -> None:
    with auth_app(world) as app:
        bad = await api(app, "PUT", "/settings", json={"grievance_email": "not-an-email"})
        bad_site = await api(app, "PUT", "/settings", json={"website": "javascript:alert(1)"})
        good = await api(app, "PUT", "/settings", json=LEGAL)
        read = await api(app, "GET", "/settings")
    assert bad.status_code == 422 and bad_site.status_code == 422
    assert good.status_code == 200, good.text
    assert read.json()["grievance_name"] == "Asha Sharma"
    assert read.json()["show_in_footer"] is True
    audits = [a for a in world.db.all(AuditLog) if a.action == "legal.settings_updated"]
    assert len(audits) == 1 and "grievance_name" in audits[0].details["changed"]


def test_actions_are_in_the_catalog() -> None:
    assert {"legal.page_published", "legal.settings_updated"} <= AUDIT_ACTIONS


async def test_draft_changed_follows_the_published_version(world: World) -> None:
    fully_configured(world)

    async def flag(app: Any) -> bool:
        items = (await api(app, "GET")).json()["items"]
        return next(i for i in items if i["slug"] == "terms")["draft_changed"]

    with auth_app(world) as app:
        assert await flag(app) is False  # nothing yet
        await api(app, "POST", "/terms/publish", json={"confirm_reviewed": True})
        assert await flag(app) is False  # the draft is exactly what was published
        await api(
            app,
            "PUT",
            "/terms/draft",
            json={"title": "Terms", "markdown": "# Terms\n\nedited", "base_revision": 1},
        )
        assert await flag(app) is True
        await api(app, "POST", "/terms/publish", json={"confirm_reviewed": True})
        assert await flag(app) is False


async def test_a_raced_first_publish_is_a_409_not_a_500(world: World) -> None:
    """Two first publishes of one slug both add a draft; the loser hits the one-draft index."""
    from unittest.mock import patch

    from sqlalchemy.exc import IntegrityError

    from tests.unit.accounts_fakes import FakeSession

    fully_configured(world)

    async def flush_raises(self: Any) -> None:
        raise IntegrityError("INSERT", {}, Exception("uq_legal_documents_one_draft"))

    with auth_app(world) as app, patch.object(FakeSession, "flush", flush_raises):
        res = await api(app, "POST", "/terms/publish", json={"confirm_reviewed": True})
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "legal_draft_conflict"
