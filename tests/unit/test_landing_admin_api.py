"""LP1 T4/T5: the editor API end to end (HTTP, with the operator check replaced)."""

from typing import Any

import pytest

from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.landing.preview import read_preview_token
from callobuzz_rag.landing.schema import default_content
from tests.unit.accounts_fakes import FakeDB
from tests.unit.landing_fakes import make_admin_client, make_settings

BASE = "/api/v1/admin/landing"


def _client(**settings: Any) -> tuple[Any, FakeDB, Any]:
    db = FakeDB()
    values = {
        "landing_video_max_mb": 25,
        "landing_image_max_mb": 5,
        "landing_cache_dir": "",
        "s3_bucket": "b",
        "storage_provider": "local",
        "storage_local_root": "x",
        "api_port": 8000,
        **settings,
    }
    config = make_settings(**values)
    return make_admin_client(db, config), db, config


async def test_the_first_read_creates_the_site_with_the_default_and_the_limits() -> None:
    client, _, _ = _client()
    res = await client.get(BASE)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled"] is False and body["published"] is None
    assert body["draft_revision"] == 1
    assert body["limits"]["hero.headline"] == 60
    assert (
        body["limits"]["upload.video_mb"] == 25 and body["limits"]["upload.image_mb"] == 5
    )  # LP4 landing
    assert body["draft"]["home"][0]["type"] == "hero"
    report = body["draft_theme"]["report"]
    assert report and all(r["ratio"] >= 4.5 for r in report)
    assert body["draft_theme"]["css"].startswith(":root{")
    assert {w["code"] for w in body["warnings"]} == {"address_missing", "nothing_published"}
    assert body["copy_warnings"] == []
    assert body["ask"]["status"] == "not_connected" and body["assets"] == []


async def test_save_publish_switch_on_and_the_audit_trail() -> None:
    client, db, _ = _client()
    state = (await client.get(BASE)).json()
    draft = state["draft"]
    draft["pricing"]["headline"] = {"en": "Plans for clinics"}
    saved = await client.put(
        f"{BASE}/draft", json={"base_revision": state["draft_revision"], "content": draft}
    )
    assert saved.status_code == 200 and saved.json()["draft_revision"] == 2
    stale = await client.put(f"{BASE}/draft", json={"base_revision": 1, "content": draft})
    assert stale.status_code == 409 and stale.json()["code"] == "landing_revision_conflict"

    off = await client.put(f"{BASE}/settings", json={"enabled": True})
    assert off.status_code == 409 and off.json()["code"] == "landing_nothing_published"
    published = await client.post(
        f"{BASE}/publish", json={"base_revision": 2, "change_note": "First version"}
    )
    assert published.status_code == 200
    assert published.json()["published"]["version"] == 1
    needs = await client.put(f"{BASE}/settings", json={"enabled": True})
    assert needs.status_code == 409 and needs.json()["code"] == "landing_needs_address"
    on = await client.put(
        f"{BASE}/settings", json={"enabled": True, "address": "https://www.sharmadental.in/"}
    )
    assert on.status_code == 200, on.text
    assert on.json()["enabled"] is True and on.json()["address"] == "https://www.sharmadental.in"
    assert on.json()["address_effective"] == "https://www.sharmadental.in"

    versions = (await client.get(f"{BASE}/versions")).json()["items"]
    assert [v["version"] for v in versions] == [1] and versions[0]["change_note"] == "First version"
    restored = await client.post(
        f"{BASE}/versions/1/restore", json={"base_revision": on.json()["draft_revision"]}
    )
    assert restored.status_code == 200
    assert restored.json()["draft"]["pricing"]["headline"] == {"en": "Plans for clinics"}
    missing = await client.post(f"{BASE}/versions/7/restore", json={"base_revision": 3})
    assert missing.status_code == 404 and missing.json()["code"] == "landing_version_not_found"

    actions = [a.action for a in db.all(AuditLog)]
    assert actions == ["landing.published", "landing.settings_updated", "landing.version_restored"]


async def test_invalid_content_and_unknown_fields_are_422() -> None:
    client, _, _ = _client()
    draft = default_content().model_dump(mode="json")
    draft["home"][0]["onclick"] = "alert(1)"
    res = await client.put(f"{BASE}/draft", json={"base_revision": 1, "content": draft})
    assert res.status_code == 422
    res = await client.put(f"{BASE}/settings", json={"enabled": True, "extra": 1})
    assert res.status_code == 422
    bad = await client.put(f"{BASE}/settings", json={"address": "http://insecure.example"})
    assert bad.status_code == 422 and bad.json()["code"] == "landing_address_invalid"


async def test_the_theme_preview_is_pure_and_measured() -> None:
    client, db, _ = _client()
    res = await client.post(
        f"{BASE}/theme/preview", json={"brand_color": "#facc15", "base": "dark"}
    )
    assert res.status_code == 200
    body = res.json()
    assert any("darker shade" in line for line in body["adjusted"])
    assert all(row["ratio"] >= 4.5 for row in body["report"])
    assert body["css"].startswith(":root{color-scheme:dark;")
    assert db.all(AuditLog) == []
    bad = await client.post(f"{BASE}/theme/preview", json={"brand_color": "teal"})
    assert bad.status_code == 422


async def test_a_preview_link_is_signed_and_built_from_the_configured_address() -> None:
    client, _, settings = _client()
    await client.put(f"{BASE}/settings", json={"address": "https://www.sharmadental.in"})
    res = await client.post(f"{BASE}/preview-link", json={"page": "pricing", "lang": "hi"})
    assert res.status_code == 200, res.text
    url = res.json()["url"]
    assert url.startswith("https://www.sharmadental.in/_landing/preview/pricing?t=")
    token = url.split("t=")[1].split("&")[0]
    claims = read_preview_token(settings, token)
    assert (claims.page, claims.lang) == ("pricing", "hi") and "lang=hi" in url


async def test_without_an_address_a_preview_link_never_uses_the_request_host() -> None:
    dev, _, _ = _client(is_prod=False)
    url = (await dev.post(f"{BASE}/preview-link", json={"page": "home"})).json()["url"]
    assert url.startswith("http://127.0.0.1:8000/_landing/preview/home?t=")
    prod, _, _ = _client(is_prod=True)
    res = await prod.post(
        f"{BASE}/preview-link", json={"page": "home"}, headers={"Host": "evil.example"}
    )
    url = res.json()["url"]
    assert url.startswith("/_landing/preview/home?t=") and "evil.example" not in url


@pytest.mark.parametrize("page", ["blog", "", "Home"])
async def test_a_preview_link_for_an_unknown_page_is_refused(page: str) -> None:
    client, _, _ = _client()
    res = await client.post(f"{BASE}/preview-link", json={"page": page})
    assert res.status_code == 422


# LP2 landing
async def test_presets_list_and_one_preset_in_a_language() -> None:
    from tests.unit.accounts_fakes import FakeDB
    from tests.unit.landing_fakes import make_admin_client

    client = make_admin_client(FakeDB())
    listing = (await client.get("/api/v1/admin/landing/presets")).json()["items"]
    assert [i["key"] for i in listing] == [
        "clinic",
        "store",
        "coaching",
        "real_estate",
        "salon",
        "services",
    ]
    res = await client.get("/api/v1/admin/landing/presets/clinic?lang=hi-Latn")
    body = res.json()
    assert res.status_code == 200 and body["lang"] == "hi-Latn"
    assert set(body["conversation"]["turns"][0]["text"]) == {"hi-Latn"}
    assert set(body["sections"]) == {"pain_wall", "languages", "whatsapp_journey", "faq"}


async def test_an_unknown_preset_is_404_with_its_code() -> None:
    from tests.unit.accounts_fakes import FakeDB
    from tests.unit.landing_fakes import make_admin_client

    res = await make_admin_client(FakeDB()).get("/api/v1/admin/landing/presets/nope")
    assert res.status_code == 404 and res.json()["code"] == "landing_preset_not_found"


# /LP2 landing


async def test_connecting_a_new_key_goes_through_the_widget_key_route_with_the_callers_sign_in() -> (
    None
):
    """LP4: `make_client` is real here; the inner widget-key route is a stub that records what
    it was sent and makes the key row."""
    import uuid

    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant
    from callobuzz_rag.db.models.widget_key import WidgetKey

    client, db, _ = _client(public_url="https://www.sharmadental.in")
    operator = db.add(Tenant(name="Op", is_operator=True))
    kb = db.add(Corpus(name="Ours", tenant_id=operator.id, qdrant_collection="a", api_key_hash=""))
    seen: dict[str, Any] = {}

    from fastapi import Request

    app = client._transport.app

    @app.post("/api/v1/corpora/{corpus_id}/widget-keys")
    async def _make_key(corpus_id: uuid.UUID, request: Request) -> dict[str, str]:
        body = await request.json()
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = body
        key = db.add(
            WidgetKey(
                corpus_id=corpus_id,
                name=body["name"],
                public_key="pk_new",
                allowed_origins=body["allowed_origins"],
                is_active=True,
            )
        )
        return {"id": str(key.id)}

    res = await client.post(
        f"{BASE}/assistant",
        json={"corpus_id": str(kb.id)},
        headers={"Authorization": "Bearer sa-session"},
    )
    assert res.status_code == 200, res.text
    assert seen["authorization"] == "Bearer sa-session"
    assert seen["body"] == {
        "name": "Public website",
        "allowed_origins": ["https://www.sharmadental.in"],
    }
    keys = db.all(WidgetKey)
    assert len(keys) == 1 and keys[0].name == "Public website"
    assert res.json()["ask"]["status"] == "ready"
    audit = [a for a in db.all(AuditLog) if a.action == "landing.assistant_connected"]
    assert len(audit) == 1 and audit[0].details["key_id"] == str(keys[0].id)
