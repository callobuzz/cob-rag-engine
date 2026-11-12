"""Real pages of one site, through the real HTML parser: chrome out, content in.

The three warranty pages in benchmarks/documents/furniture share a menu, a cookie notice,
a related-articles box, a footer, a disclaimer and a repair price table. Parsing uses the
installed Docling, so this also catches a Docling upgrade that changes its furniture rules.
"""

import uuid
from pathlib import Path

import pytest

from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.parsing.docling_parser import DoclingParserProvider
from callobuzz_rag.parsing.models import ElementType
from callobuzz_rag.pipeline.boilerplate import apply_boilerplate
from callobuzz_rag.pipeline.text_hash import text_hash

FURNITURE = Path(__file__).resolve().parents[2] / "benchmarks" / "documents" / "furniture"
PAGES = ["warranty-tents.html", "warranty-boots.html", "warranty-bags.html"]

CHROME = [
    "My account",  # menu
    "Stores in Pune, Mumbai and Bengaluru",  # menu
    "Accept all cookies",  # cookie notice
    "Related articles",  # <aside>
    "Pitching a tent in the monsoon",  # <aside>
    "Contact us today for a free site visit",  # footer
    "Subscribe to our newsletter",  # footer
    "Privacy | Terms",  # footer
]


@pytest.fixture(scope="module")
def parser() -> DoclingParserProvider:
    return DoclingParserProvider()


@pytest.fixture(scope="module")
async def parsed(parser: DoclingParserProvider):
    return {name: await parser.parse(FURNITURE / name, "text/html") for name in PAGES}


def _text(doc) -> str:
    return "\n".join(e.text for e in doc.elements)


async def test_page_chrome_is_stripped_and_content_kept(parsed) -> None:
    text = _text(parsed["warranty-tents.html"])
    for chrome in CHROME:
        assert chrome not in text, chrome
    # What the page is about stays, including the lead paragraph before the first heading
    # (Docling alone drops it) and the table.
    assert "built in our Pune workshop" in text
    assert "7-year warranty" in text
    assert "T-12" in text
    kinds = [e.type for e in parsed["warranty-tents.html"].elements]
    assert ElementType.TABLE in kinds
    assert "Rs 450" in text


async def test_without_the_pre_pass_the_page_is_parsed_as_docling_leaves_it(parser) -> None:
    doc = await parser.parse(FURNITURE / "warranty-tents.html", "text/html", strip_chrome=False)
    text = _text(doc)
    assert "Pitching a tent in the monsoon" in text  # <aside> is not furniture to Docling
    assert "built in our Pune workshop" not in text  # and text before the first heading is


async def test_a_page_that_is_all_chrome_is_parsed_unchanged(parser, tmp_path) -> None:
    page = tmp_path / "menu.html"
    page.write_text(
        "<html><body><nav><a href='/'>Home</a> Shop Stores Help Account</nav>"
        "<h1>Menu</h1></body></html>",
        encoding="utf-8",
    )
    doc = await parser.parse(page, "text/html")
    assert doc.parser_name == "docling"  # parsed, no error, nothing invented


class _Store:
    def __init__(self) -> None:
        self.rows: set[tuple[uuid.UUID, str, uuid.UUID]] = set()

    async def counts(self, corpus_id, document_id, hashes):
        out: dict[str, int] = {}
        for c, h, d in self.rows:
            if c == corpus_id and h in hashes and d != document_id:
                out[h] = out.get(h, 0) + 1
        return out

    async def replace(self, corpus_id, document_id, hashes):
        self.rows = {r for r in self.rows if r[2] != document_id}
        self.rows |= {(corpus_id, h, document_id) for h in hashes}


async def test_shared_text_goes_once_and_the_price_table_is_never_lost(parsed) -> None:
    store, corpus = _Store(), uuid.uuid4()
    chunker = StructureAwareChunker()
    disclaimer = "All prices are in Indian rupees and include GST."
    kept, table_hashes = [], []
    for name in PAGES:
        doc, _ = await apply_boilerplate(
            store,
            corpus,
            uuid.uuid4(),
            parsed[name],
            min_docs=2,  # two pages already hold it: the third drops it
            max_chars=600,
            count_tokens=chunker.counter.count,
        )
        kept.append(disclaimer in _text(doc))
        chunks = chunker.chunk(doc)
        tables = [c for c in chunks if c.is_table]
        assert tables, f"{name}: the repair price table must survive"
        assert "Rs 450" in tables[0].text
        table_hashes.append(text_hash(tables[0].core_text or tables[0].text))
        assert any(
            "7-year" in c.text or "12-month" in c.text or "3 years" in c.text for c in chunks
        )
    assert kept == [True, True, False]
    # The same table on every page has one hash, so search shows it once ("also in" the others).
    assert len(set(table_hashes)) == 1
