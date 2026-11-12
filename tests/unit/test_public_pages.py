"""The public pages describe an AGPL-3.0 open-source project with a commercial licence on request."""

import re
from pathlib import Path

import pytest

from callobuzz_rag.core.product import COMMERCIAL_LICENSE_URL

ROOT = Path(__file__).resolve().parents[2]
PAGES = [
    "index.html",
    "pitch.html",
    "guide.html",
    "deploy.html",
    "benchmarks.html",
    "requirements.html",
    "getting-started.html",
    "support.html",
]
STORE = re.compile(
    r"codecanyon|envato|purchase[ -]code|evaluation copy|extended licen[cs]e|regular licen[cs]e",
    re.IGNORECASE,
)


def _read(name: str) -> str:
    return (ROOT / "docs" / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", PAGES)
def test_no_page_sells_through_a_store(name: str) -> None:
    hits = sorted({m.group(0) for m in STORE.finditer(_read(name))})
    assert not hits, f"docs/{name} still says {hits}"


@pytest.mark.parametrize("name", ["index.html", "getting-started.html", "support.html"])
def test_the_licence_pages_say_agpl_and_link_the_commercial_licence(name: str) -> None:
    page = _read(name)
    assert "AGPL-3.0" in page
    assert f'href="{COMMERCIAL_LICENSE_URL}"' in page


@pytest.mark.parametrize("name", PAGES)
def test_every_page_keeps_the_attribution_as_text(name: str) -> None:
    assert "cob-rag-engine by Callobuzz" in _read(name), name


def test_the_readme_has_the_licence_section() -> None:
    readme = (ROOT / "README.md").read_text("utf-8")
    assert "\n## License\n" in readme
    assert "AGPL-3.0" in readme and "MIT" in readme and COMMERCIAL_LICENSE_URL in readme
    assert not STORE.search(readme)
    assert "proprietary software" not in readme.lower()


def test_the_index_has_one_get_the_code_link() -> None:
    html = _read("index.html")
    links = re.findall(r'<a[^>]*data-cta="source"[^>]*href="([^"]+)"', html)
    assert links == ["https://github.com/callobuzz/cob-rag-engine"]
    assert 'class="btn btn-primary" href="#start">Install it on a server</a>' in html
