"""LP1 T8: size budgets for the public website, measured on the committed build and on pages
rendered by the snapshot renderer. Skipped (with the reason) while the build directory is absent;
CI runs it after `pnpm build:landing`."""

import gzip
import re
from pathlib import Path
from typing import Any

import pytest

from callobuzz_rag.landing import build, snapshot
from callobuzz_rag.landing.schema import default_content

KB = 1024

pytestmark = pytest.mark.skipif(
    not (build.BUILD_DIR / "manifest.json").is_file(),
    reason="the landing build is absent: run `pnpm build:landing` in services/dashboard",
)


def _gz(data: bytes) -> int:
    return len(gzip.compress(data, compresslevel=9))


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    build.reload()
    return build.manifest()


@pytest.fixture(scope="module")
def build_dir() -> Path:
    return build.BUILD_DIR


@pytest.fixture(scope="module")
def snapshot_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("landing-snapshot")
    snapshot.render_site(default_content(), out, languages=["en"])
    return out


def test_js_on_load_for_the_home_page_is_at_most_15_kb(
    manifest: dict[str, Any], build_dir: Path
) -> None:
    files = build.entry_files(manifest, "core") + build.entry_files(manifest, "home")
    scripts = [f for f in dict.fromkeys(files) if f.endswith(".js")]
    assert scripts
    assert sum(_gz((build_dir / f).read_bytes()) for f in scripts) <= 15 * KB


def test_css_per_page_is_at_most_25_kb(manifest: dict[str, Any], build_dir: Path) -> None:
    for entry in ("home", "pages"):
        names = build.entry_files(manifest, "core") + build.entry_files(manifest, entry)
        css = [f for f in dict.fromkeys(names) if f.endswith(".css")]
        assert css
        assert sum(_gz((build_dir / f).read_bytes()) for f in css) <= 25 * KB, entry


def test_html_of_every_page_is_at_most_30_kb(snapshot_dir: Path) -> None:
    pages = list(snapshot_dir.glob("**/index.html"))
    pages = [p for p in pages if "_landing" not in p.parts]
    assert len(pages) == 5
    for page in pages:
        assert _gz(page.read_bytes()) <= 30 * KB, page


def test_fonts_preloaded_are_at_most_two_files_under_120_kb(
    snapshot_dir: Path, build_dir: Path
) -> None:
    html = (snapshot_dir / "index.html").read_text("utf-8")
    preloads = re.findall(r'<link rel="preload" href="/_landing/build/([^"]+\.woff2)"', html)
    assert 1 <= len(preloads) <= 2
    assert sum((build_dir / p).stat().st_size for p in preloads) <= 120 * KB


def test_the_pages_load_the_built_files(snapshot_dir: Path) -> None:
    html = (snapshot_dir / "index.html").read_text("utf-8")
    assert re.search(r'<link rel="stylesheet" href="/_landing/build/assets/core-[^"]+\.css">', html)
    assert re.search(r'<script type="module" src="/_landing/build/assets/core-[^"]+\.js">', html)
    assert (snapshot_dir / "_landing" / "build" / "manifest.json").is_file()


def test_no_image_is_required_above_the_fold(snapshot_dir: Path) -> None:
    html = (snapshot_dir / "index.html").read_text("utf-8")
    hero = html.split('data-section="hero"')[1].split("</section>")[0]
    assert "<img" not in hero


def test_every_built_file_the_pages_name_exists(snapshot_dir: Path) -> None:
    for page in snapshot_dir.glob("**/index.html"):
        if "_landing" in page.parts:
            continue
        for url in re.findall(r'(?:href|src)="(/_landing/build/[^"]+)"', page.read_text("utf-8")):
            assert (snapshot_dir / url.lstrip("/")).is_file(), (page, url)


def test_the_build_route_serves_only_files_the_manifest_names(build_dir: Path) -> None:
    some = next(iter(sorted((build_dir / "assets").glob("*.js"))))
    assert build.build_file(f"assets/{some.name}") == some.resolve()
    for name in (
        "../manifest.json",
        "assets/../manifest.json",
        "nope.js",
        "assets/",
        "",
        "/etc/passwd",
    ):
        assert build.build_file(name) is None, name


async def test_the_build_files_are_served_with_long_cache_headers_even_when_the_site_is_off(
    monkeypatch: pytest.MonkeyPatch, build_dir: Path
) -> None:
    from tests.unit.landing_fakes import make_client

    client = make_client(monkeypatch, site=None)
    script = next(iter(sorted((build_dir / "assets").glob("core-*.js"))))
    res = await client.get(f"/_landing/build/assets/{script.name}")
    assert res.status_code == 200 and res.content == script.read_bytes()
    assert res.headers["content-type"].startswith("text/javascript")
    assert res.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert res.headers["x-content-type-options"] == "nosniff"
    style = next(iter(sorted((build_dir / "assets").glob("core-*.css"))))
    assert (
        (await client.get(f"/_landing/build/assets/{style.name}"))
        .headers["content-type"]
        .startswith("text/css")
    )
    font = next(iter(sorted((build_dir / "fonts").glob("*.woff2"))))
    got = await client.get(f"/_landing/build/fonts/{font.name}")
    assert got.status_code == 200 and got.headers["content-type"] == "font/woff2"
    for bad in (
        "../manifest.json",
        "assets/../manifest.json",
        "assets/nope.js",
        "%2e%2e/manifest.json",
    ):
        assert (await client.get(f"/_landing/build/{bad}")).status_code == 404, bad


def test_every_pairing_names_existing_preload_files_and_a_devanagari_family() -> None:
    from callobuzz_rag.landing import fonts

    build.reload()
    manifest = build.fonts()
    for pairing in fonts.PAIRINGS.values():
        files = fonts.preloads(pairing, manifest)
        assert 1 <= len(files) <= 2, pairing.key
        assert all((build.BUILD_DIR / f).is_file() for f in files), pairing.key
        assert sum((build.BUILD_DIR / f).stat().st_size for f in files) <= 120 * KB, pairing.key
        assert manifest.get(pairing.devanagari), pairing.key
