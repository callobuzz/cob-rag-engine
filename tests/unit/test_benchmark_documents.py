"""benchmarks/documents.py: it imports, scores `also_in`, and its furniture fixtures are whole."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks"


@pytest.fixture(scope="module")
def documents():
    sys.path.insert(0, str(ROOT / "clients" / "python"))
    sys.path.insert(0, str(BENCH))
    spec = importlib.util.spec_from_file_location("bench_documents", BENCH / "documents.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_default_questions_are_unchanged() -> None:
    lines = (BENCH / "documents" / "questions.jsonl").read_text("utf-8").splitlines()
    assert len([line for line in lines if line]) == 55
    assert len(list((BENCH / "documents" / "files").iterdir())) == 7


def test_answered_at_accepts_the_right_file_through_also_in(documents) -> None:
    item = {"q": "?", "file": "a.html", "facts": ["Rs 450"], "kind": "x"}
    chunk = {
        "file_name": "b.html",
        "text": "Zip replacement | Rs 450",
        "also_in": [{"file_name": "a.html"}],
    }
    assert documents.answered_at([chunk], item) == 1
    # Without the listing the chunk is another file's: a miss, as before.
    assert documents.answered_at([{**chunk, "also_in": []}], item) is None
    # A question can name the other files that hold the passage.
    shared = {**item, "also_in": ["b.html"]}
    assert documents.answered_at([{**chunk, "also_in": []}], shared) == 1
    # Every fact must be present.
    assert documents.answered_at([chunk], {**item, "facts": ["Rs 450", "Rs 999"]}) is None


def test_furniture_questions_point_at_pages_that_hold_their_facts() -> None:
    folder = BENCH / "documents" / "furniture"
    pages = {p.name: p.read_text("utf-8") for p in folder.glob("*.html")}
    assert len(pages) == 3
    questions = [
        json.loads(line)
        for line in (folder / "questions.jsonl").read_text("utf-8").splitlines()
        if line
    ]
    assert len(questions) >= 10
    for q in questions:
        for fact in q["facts"]:
            html = pages[q["file"]].replace("&amp;", "&")
            assert fact in html, (q["q"], fact)
        for other in q.get("also_in", []):
            assert all(f in pages[other] for f in q["facts"]), (q["q"], other)
    assert {q["kind"] for q in questions} == {"pricing table", "furniture page"}


def test_the_scrape_fixture_is_ten_markdown_pages_sharing_the_site_lines(documents) -> None:
    pages = documents.scrape_pages()
    assert len(pages) == 10
    assert len({name for name, _ in pages}) == 10
    shared = json.loads(
        (BENCH / "documents" / "furniture" / "scrape-site.json").read_text("utf-8")
    )["shared"]
    for _, body in pages:
        text = body.decode("utf-8")
        assert text.startswith("# ")
        assert all(line in text for line in shared.values())
    assert len({body for _, body in pages}) == 10  # no two pages are the same


class _Rag:
    """A client whose list endpoint refuses what the API refuses: limit above 100."""

    def __init__(self, total: int) -> None:
        self.docs = [{"id": f"d{n}", "file_name": f"f{n}.html"} for n in range(total)]

    def list_documents(self, limit: int = 50, offset: int = 0) -> dict:
        if not 1 <= limit <= 100:
            raise AssertionError("422: limit must be 1..100")
        return {"items": self.docs[offset : offset + limit], "count": len(self.docs)}

    def get_document(self, document_id: str, chunk_limit: int = 1) -> dict:
        return {"chunk_total": int(document_id[1:]) + 1}


def test_chunk_counts_pages_through_the_list_within_the_api_limit(documents) -> None:
    counts = documents.chunk_counts(_Rag(250))
    assert len(counts) == 250
    assert counts["f249.html"] == 250
