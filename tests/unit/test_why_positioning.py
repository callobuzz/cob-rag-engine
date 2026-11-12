"""The "Why cob-rag-engine if LangChain..." pitch is one text in three places: README, landing
page and the COB docs page. The sentences a salesperson says out loud must match in all three."""

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs/cob/pages/why-cob-rag-engine.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _plain(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text).replace("`", "").replace("**", ""))


def _pitch_sentences() -> list[str]:
    body = _read(PAGE)
    short = body.split("## The short answer\n\n", 1)[1].split("\n\n", 1)[0]
    script = body.split("## Say it in four lines\n\n", 1)[1].split("\n\n", 1)[0]
    lines = [re.sub(r"^\d\. ", "", x) for x in script.split("\n")]
    assert len(lines) == 4
    return [short, *lines]


def test_the_pitch_is_identical_everywhere() -> None:
    surfaces = {
        "README.md": _plain(_read(ROOT / "README.md")),
        "docs/index.html": _plain(_read(ROOT / "docs/index.html")),
    }
    for name, text in surfaces.items():
        for sentence in _pitch_sentences():
            assert _plain(sentence) in text, f"{name} is missing: {sentence[:60]}"


def test_the_pitch_is_honest_about_langchain() -> None:
    for path in (PAGE, ROOT / "README.md", ROOT / "docs/index.html"):
        text = _plain(_read(path))
        assert "no LangChain inside" not in text
        assert "RAGAS" in text


def test_the_page_is_public_and_in_the_manifest() -> None:
    assert "audience: public" in _read(PAGE).split("---", 2)[1]
    manifest = _read(ROOT / "docs/cob/manifest.yaml")
    assert "docs/cob/pages/why-cob-rag-engine.md" in manifest
