"""LP1 T6: robots.txt, sitemap.xml, structured data and the share image."""

import io
import json
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest
from PIL import Image

from callobuzz_rag.db.models.landing import LandingVersion
from callobuzz_rag.landing import store
from callobuzz_rag.landing.og import HEIGHT, WIDTH, is_latin, render_og
from callobuzz_rag.landing.schema import default_content
from callobuzz_rag.landing.theme import derive_theme
from tests.unit.accounts_fakes import FakeDB
from tests.unit.landing_fakes import (
    ADDRESS,
    content_with,
    make_client,
    make_env,
    make_settings,
    make_site,
)


@pytest.fixture
def client_on(monkeypatch: pytest.MonkeyPatch) -> Any:
    return make_client(monkeypatch, site=make_site())


async def test_robots_txt_points_at_the_sitemap_on_the_configured_address(client_on: Any) -> None:
    res = await client_on.get("/robots.txt", headers={"Host": "evil.example"})
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/plain")
    lines = res.text.splitlines()
    assert lines[0] == "User-agent: *"
    assert {"Disallow: /api/", "Disallow: /app", "Disallow: /_landing/preview/"} <= set(lines)
    assert f"Sitemap: {ADDRESS}/sitemap.xml" in lines
    assert "evil.example" not in res.text
    assert res.headers["cache-control"] == "public, max-age=3600"


async def test_off_means_404_for_robots_and_sitemap(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(monkeypatch, site=None)
    for path in ("/robots.txt", "/sitemap.xml"):
        res = await client.get(path)
        assert res.status_code == 404 and res.headers["x-cob-landing"] == "off"


async def test_sitemap_lists_every_page_and_language_with_alternates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["languages"] = ["en", "hi"]

    client = make_client(monkeypatch, site=make_site(content_with(mutate)))
    res = await client.get("/sitemap.xml", headers={"Host": "evil.example"})
    assert res.status_code == 200 and "evil.example" not in res.text
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "x": "http://www.w3.org/1999/xhtml"}
    root = ElementTree.fromstring(res.text)
    locs = [e.text for e in root.findall("s:url/s:loc", ns)]
    assert all(loc and loc.startswith(ADDRESS) for loc in locs)
    for page in ("", "/pricing", "/features", "/about", "/contact"):
        assert f"{ADDRESS}{page or '/'}" in locs
        assert f"{ADDRESS}/hi{page or '/'}" in locs
    assert f"{ADDRESS}/legal/terms" in locs and f"{ADDRESS}/legal/privacy" in locs
    first = root.find("s:url", ns)
    assert first is not None
    assert first.find("s:lastmod", ns).text == "2026-10-10"  # type: ignore[union-attr]
    alternates = first.findall("x:link", ns)
    assert {a.get("hreflang") for a in alternates} == {"en", "hi"}


async def test_a_disabled_page_is_absent_from_the_sitemap_and_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mutate(data: dict[str, Any]) -> None:
        data["pricing"]["enabled"] = False

    client = make_client(monkeypatch, site=make_site(content_with(mutate)))
    assert f"{ADDRESS}/pricing" not in (await client.get("/sitemap.xml")).text
    assert (await client.get("/pricing")).status_code == 404


async def test_without_an_address_there_is_no_sitemap(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(monkeypatch, site=make_site(), env=make_env(address=None))
    assert (await client.get("/sitemap.xml")).status_code == 404
    assert "Sitemap:" not in (await client.get("/robots.txt")).text


def _blocks(html: str) -> list[dict[str, Any]]:
    found = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    return [json.loads(block) for block in found]


async def test_home_has_an_organization_and_a_software_application_and_no_offers(
    client_on: Any,
) -> None:
    html = (await client_on.get("/")).text
    blocks = _blocks(html)
    types = [b["@type"] for b in blocks]
    assert types == ["Organization", "SoftwareApplication"]
    organization, application = blocks
    assert organization["name"] == "Sahayak" and organization["url"] == f"{ADDRESS}/"
    assert organization["contactPoint"]["email"] == "hello@sharmadental.in"
    assert application["applicationCategory"] == "BusinessApplication"
    assert application["operatingSystem"] == "Web"
    text = json.dumps(blocks)
    assert "offers" not in text and "aggregateRating" not in text
    assert "</script" not in "".join(re.findall(r"ld\+json\">(.*?)</script>", html, re.S))
    assert _blocks((await client_on.get("/pricing")).text) == []


async def test_faq_structured_data_only_when_switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    def on(data: dict[str, Any]) -> None:
        next(s for s in data["home"] if s["type"] == "faq")["structured_data"] = True

    plain = make_client(monkeypatch, site=make_site())
    assert "FAQPage" not in (await plain.get("/")).text
    enabled = make_client(monkeypatch, site=make_site(content_with(on)))
    blocks = _blocks((await enabled.get("/")).text)
    faq = next(b for b in blocks if b["@type"] == "FAQPage")
    assert faq["mainEntity"][0]["@type"] == "Question"


async def test_a_hostile_text_cannot_close_the_script_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.accounts.settings_store import Branding

    env = make_env(branding=Branding(product_name="</script><script>alert(1)</script>"))
    client = make_client(monkeypatch, site=make_site(), env=env)
    html = (await client.get("/")).text
    assert "<script>alert(1)" not in html
    assert _blocks(html)[0]["name"] == "</script><script>alert(1)</script>"


async def test_the_page_names_its_share_image(client_on: Any) -> None:
    html = (await client_on.get("/")).text
    assert f'<meta property="og:image" content="{ADDRESS}/_landing/og/3-en.png">' in html
    assert 'name="twitter:card" content="summary_large_image"' in html


# ------------------------------------------------------------------ the share image


def test_the_share_image_is_a_1200_by_630_png_and_deterministic() -> None:
    tokens = derive_theme("#0f766e", None)
    first = render_og(tokens, "Sahayak", "An assistant that answers from your own content", "en")
    again = render_og(tokens, "Sahayak", "An assistant that answers from your own content", "en")
    assert first.startswith(b"\x89PNG") and first == again
    with Image.open(io.BytesIO(first)) as picture:
        assert picture.size == (WIDTH, HEIGHT) == (1200, 630)


def test_a_non_latin_headline_falls_back_to_the_product_name() -> None:
    tokens = derive_theme("#0f766e", None)
    hindi = render_og(tokens, "Sahayak", "आपके ग्राहकों के सवाल", "hi")
    name_only = render_og(tokens, "Sahayak", "", "hi")
    assert hindi == name_only
    assert is_latin("Pay ₹500 now") and not is_latin("आपके")


def test_a_long_headline_does_not_overflow() -> None:
    tokens = derive_theme("#0f766e", None)
    long = " ".join(["word"] * 80)
    data = render_og(tokens, "Sahayak", long, "en")
    with Image.open(io.BytesIO(data)) as picture:
        assert picture.size == (1200, 630)


async def test_the_share_image_is_drawn_on_the_first_request_and_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from callobuzz_rag.db.models.landing import LandingAsset
    from callobuzz_rag.landing import assets
    from callobuzz_rag.storage.local import LocalFileStorageProvider

    storage = LocalFileStorageProvider(tmp_path / "files")
    monkeypatch.setattr(assets, "object_storage", lambda _settings: storage)
    monkeypatch.setattr(assets, "cache_dir", lambda _settings: tmp_path / "cache")
    db = FakeDB()
    content = default_content()
    dump = content.model_dump(mode="json")
    theme = store.theme_of(content).to_json()
    db.add(
        LandingVersion(
            version=3,
            content=dump,
            theme=theme,
            theme_css=":root{}",
            content_hash=store.content_hash(dump, theme),
            published_by="admin key",
        )
    )
    settings = make_settings(
        landing_cache_dir="", s3_bucket="b", storage_provider="local", storage_local_root="x"
    )
    client = make_client(monkeypatch, site=make_site(), db=db, settings=settings)
    res = await client.get("/_landing/og/3-en.png")
    assert res.status_code == 200 and res.content.startswith(b"\x89PNG")
    assert res.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert res.headers["content-type"] == "image/png"
    (row,) = db.all(LandingAsset)
    assert row.kind == "og" and row.storage_key == "landing/og/3-en.png"
    again = await client.get("/_landing/og/3-en.png")
    assert again.content == res.content and len(db.all(LandingAsset)) == 1
    assert (await client.get("/_landing/og/3-hi.png")).status_code == 404  # not in the version
    assert (await client.get("/_landing/og/9-en.png")).status_code == 404
    assert (await client.get("/_landing/og/../../etc.png")).status_code in (404, 400)
