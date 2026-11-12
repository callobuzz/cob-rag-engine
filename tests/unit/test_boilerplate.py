"""Cross-document page-furniture stripping (pipeline/boilerplate.py)."""

import uuid
from types import SimpleNamespace

import pytest

from callobuzz_rag.db.models.kb_block_hash import KbBlockHash
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement, TableData
from callobuzz_rag.pipeline import boilerplate
from callobuzz_rag.pipeline.boilerplate import (
    apply_boilerplate,
    block_hash,
    boilerplate_enabled,
)

CORPUS = uuid.uuid4()
DISCLAIMER = "Prices are inclusive of GST. Terms and conditions apply. E&OE."
CTA = "Contact us today for a free site visit in Pune."


def para(text: str) -> ParsedElement:
    return ParsedElement(type=ElementType.PARAGRAPH, text=text)


def head(text: str, level: int = 1) -> ParsedElement:
    return ParsedElement(type=ElementType.HEADING, text=text, level=level)


def doc(*elements: ParsedElement) -> ParsedDocument:
    return ParsedDocument(elements=list(elements), parser_name="t", parser_version="1")


def count_words(text: str) -> int:
    return len(text.split())


class MemoryStore:
    """The same contract as SqlBlockHashStore, kept in a set of (corpus, hash, document)."""

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

    def delete_document(self, document_id):  # what the ON DELETE CASCADE does
        self.rows = {r for r in self.rows if r[2] != document_id}


async def ingest(store, *elements, document_id=None, min_docs=3, max_chars=600):
    parsed, outcome = await apply_boilerplate(
        store,
        CORPUS,
        document_id or uuid.uuid4(),
        doc(*elements),
        min_docs=min_docs,
        max_chars=max_chars,
        count_tokens=count_words,
    )
    return [e.text for e in parsed.elements], outcome


def page(n: int) -> list[ParsedElement]:
    return [
        head(f"Service page {n}"),
        para(
            f"Page {n} explains how installation number {n} is scheduled, what the engineer "
            "checks on arrival, how the invoice is raised afterwards, and how a customer in "
            "any Indian city can reschedule the visit or ask for a different time slot."
        ),
        para(DISCLAIMER),
        para(CTA),
    ]


async def test_boilerplate_drops_block_seen_in_min_docs() -> None:
    store = MemoryStore()
    for n in range(3):
        texts, outcome = await ingest(store, *page(n))
        assert DISCLAIMER in texts and outcome.dropped == 0  # the first three keep their copy
    texts, outcome = await ingest(store, *page(3))
    assert DISCLAIMER not in texts and CTA not in texts
    assert any("Page 3 explains" in t for t in texts)
    assert outcome.dropped == 2
    assert outcome.warning == "2 blocks stripped"
    assert outcome.dropped_hashes == {block_hash(DISCLAIMER), block_hash(CTA)}


async def test_boilerplate_keeps_first_pages_copy() -> None:
    store = MemoryStore()
    first_pages = []
    for n in range(5):
        texts, _ = await ingest(store, *page(n))
        first_pages.append(texts)
    # Pages 0-2 were ingested before the count reached the limit and keep the text:
    # the knowledge base always holds at least one searchable copy.
    assert all(DISCLAIMER in t for t in first_pages[:3])
    assert all(DISCLAIMER not in t for t in first_pages[3:])


async def test_boilerplate_hash_matches_across_case_and_spacing() -> None:
    store = MemoryStore()
    for n in range(3):
        await ingest(
            store,
            head("T"),
            para(
                f"Own text {n} is long enough to stay, and it says a good deal more about gears, belts and chains."
            ),
            para(DISCLAIMER),
        )
    texts, _ = await ingest(
        store,
        head("T"),
        para(
            "Own text 9 is long enough to stay, and it says a good deal more about gears, belts and chains."
        ),
        para(DISCLAIMER.upper() + "  "),
    )
    assert not any("GST" in t.upper() and "E&OE" in t.upper() for t in texts)


async def test_boilerplate_never_drops_tables_code_headings() -> None:
    store = MemoryStore()
    table = ParsedElement(
        type=ElementType.TABLE,
        text="| Plan | Price |\n| Basic | Rs 4,999 |",
        table_data=TableData(
            markdown="| Plan | Price |\n| Basic | Rs 4,999 |", json_rows=[], num_rows=2, num_cols=2
        ),
    )
    code = ParsedElement(type=ElementType.CODE, text="curl https://example.in/api")
    caption = ParsedElement(type=ElementType.CAPTION, text="Figure 1: the unit")

    def shared(n: int) -> list[ParsedElement]:
        return [head("Pricing"), table, code, caption, para(f"Unique body text number {n} here.")]

    for n in range(6):
        texts, outcome = await ingest(store, *shared(n))
        assert table.text in texts and code.text in texts and caption.text in texts
        assert "Pricing" in texts
        assert outcome.dropped == 0


async def test_boilerplate_respects_max_chars_and_half_document_guard() -> None:
    store = MemoryStore()
    long_block = "Warranty clause. " * 60  # well over 600 characters
    for n in range(4):
        texts, outcome = await ingest(
            store, head("T"), para(f"Distinct {n} paragraph with words."), para(long_block)
        )
        assert long_block.strip() in " ".join(
            texts
        )  # too long to be judged: left to the search collapse
        assert outcome.dropped == 0

    # Half-document guard: a page that is mostly repeated text is kept whole, with a warning.
    store = MemoryStore()
    repeated = [
        para(f"Repeated line {i} that every page of this site carries around.") for i in range(6)
    ]
    for n in range(3):
        await ingest(store, head("T"), *repeated, para(f"Own {n}."))
    texts, outcome = await ingest(store, head("T"), *repeated, para("Own 9."))
    assert outcome.dropped == 0
    assert all(r.text in texts for r in repeated)
    assert outcome.warning is not None and "kept" in outcome.warning

    # ... and a page that would lose everything keeps everything.
    texts, outcome = await ingest(store, *repeated)
    assert outcome.dropped == 0 and len(texts) == len(repeated)


async def test_boilerplate_min_docs_is_configurable() -> None:
    store = MemoryStore()
    await ingest(
        store,
        head("A"),
        para("Shared notice for all customers of the shop."),
        para("Alpha is about gears."),
        min_docs=2,
    )
    await ingest(
        store,
        head("B"),
        para("Shared notice for all customers of the shop."),
        para("Beta is about belts."),
        min_docs=2,
    )
    texts, outcome = await ingest(
        store,
        head("C"),
        para("Shared notice for all customers of the shop."),
        para(
            "Gamma is about chains, and this page explains at length how a drive chain is measured, fitted and lubricated."
        ),
        min_docs=2,
    )
    assert "Shared notice for all customers of the shop." not in texts
    assert outcome.dropped == 1


async def test_reingest_excludes_own_hashes() -> None:
    store = MemoryStore()
    own = uuid.uuid4()
    for _ in range(5):  # the same document, ingested again and again, never counts itself
        texts, outcome = await ingest(store, *page(0), document_id=own)
        assert DISCLAIMER in texts and outcome.dropped == 0
    assert len({r for r in store.rows if r[2] == own}) == len(
        {block_hash(DISCLAIMER), block_hash(CTA), block_hash(page(0)[1].text)}
    )


async def test_block_counts_follow_document_delete() -> None:
    store = MemoryStore()
    ids = [uuid.uuid4() for _ in range(3)]
    for n, document_id in enumerate(ids):
        await ingest(store, *page(n), document_id=document_id)
    texts, _ = await ingest(store, *page(7))
    assert DISCLAIMER not in texts  # three other documents hold it
    # Four documents hold it now (a dropped block's hash is stored too). Two deletes (the
    # FK cascade) bring the count under the limit again.
    store.delete_document(ids[0])
    store.delete_document(ids[1])
    texts, _ = await ingest(store, *page(8))
    assert DISCLAIMER in texts


async def test_every_candidate_hash_is_stored_dropped_ones_included() -> None:
    store = MemoryStore()
    for n in range(3):
        await ingest(store, *page(n))
    document_id = uuid.uuid4()
    await ingest(store, *page(5), document_id=document_id)
    stored = {h for _, h, d in store.rows if d == document_id}
    assert {block_hash(DISCLAIMER), block_hash(CTA)} <= stored


async def test_long_blocks_are_not_stored() -> None:
    store = MemoryStore()
    document_id = uuid.uuid4()
    await ingest(
        store,
        head("T"),
        para("x " * 400),
        para("short paragraph here"),
        document_id=document_id,
        max_chars=600,
    )
    assert len({r for r in store.rows if r[2] == document_id}) == 1


def test_boilerplate_corpus_opt_out() -> None:
    on = SimpleNamespace(boilerplate_strip_enabled=True)
    off = SimpleNamespace(boilerplate_strip_enabled=False)
    assert boilerplate_enabled(on, SimpleNamespace(retrieval_config=None))
    assert boilerplate_enabled(on, SimpleNamespace(retrieval_config={"top_k": 5}))
    assert not boilerplate_enabled(
        on, SimpleNamespace(retrieval_config={"boilerplate_strip": False})
    )
    assert boilerplate_enabled(on, SimpleNamespace(retrieval_config={"boilerplate_strip": None}))
    assert not boilerplate_enabled(off, SimpleNamespace(retrieval_config=None))
    assert not boilerplate_enabled(
        off, SimpleNamespace(retrieval_config={"boilerplate_strip": True})
    )
    assert not boilerplate_enabled(None, None)  # no settings: the step is off
    assert boilerplate_enabled(on, None)


def test_the_hash_rows_cascade_with_their_document_and_knowledge_base() -> None:
    fks = {fk.parent.name: fk.ondelete for fk in KbBlockHash.__table__.foreign_keys}
    assert fks == {"corpus_id": "CASCADE", "document_id": "CASCADE"}
    assert {c.name for c in KbBlockHash.__table__.primary_key} == {
        "corpus_id",
        "block_hash",
        "document_id",
    }


def test_module_exposes_the_documented_candidates() -> None:
    assert block_hash("  Hello   WORLD ") == block_hash("hello world")
    assert {ElementType.PARAGRAPH, ElementType.LIST_ITEM} == boilerplate.CANDIDATE_TYPES


@pytest.mark.parametrize("kind", [ElementType.LIST_ITEM, ElementType.PARAGRAPH])
async def test_list_items_and_paragraphs_are_both_judged(kind: ElementType) -> None:
    store = MemoryStore()
    furniture = ParsedElement(type=kind, text="Follow us on social media for offers.")
    for n in range(3):
        await ingest(
            store,
            head("T"),
            furniture,
            para(
                f"Real content number {n} about boilers, covering installation, servicing, pricing and warranty in detail."
            ),
        )
    texts, _ = await ingest(
        store,
        head("T"),
        furniture,
        para(
            "Real content number 9 about boilers, covering installation, servicing, pricing and warranty in detail."
        ),
    )
    assert furniture.text not in texts


async def test_shared_hashes_cover_blocks_held_by_any_other_document_kept_or_dropped() -> None:
    store = MemoryStore()
    await ingest(store, *page(0))
    _, outcome = await ingest(store, *page(1))
    assert outcome.dropped == 0  # one other page holds them: below the limit, kept
    assert outcome.shared_hashes == {block_hash(DISCLAIMER), block_hash(CTA)}


def test_a_chunk_of_only_shared_paragraphs_is_shared() -> None:
    from callobuzz_rag.pipeline.boilerplate import is_shared_chunk

    shared = {block_hash(DISCLAIMER), block_hash(CTA)}
    blank = chr(10) * 2
    assert is_shared_chunk(f"{CTA}{blank}{DISCLAIMER}", shared, 600)
    assert not is_shared_chunk(f"{CTA}{blank}Our own warranty text, not repeated.", shared, 600)
    assert not is_shared_chunk("", shared, 600)
    assert not is_shared_chunk(CTA, set(), 600)
    # A list whose every line is shared counts; one other line does not.
    lines = {block_hash("Free delivery"), block_hash("Easy returns")}
    assert is_shared_chunk("Free delivery" + chr(10) + "Easy returns", lines, 600)
    assert not is_shared_chunk("Free delivery" + chr(10) + "A new line", lines, 600)
