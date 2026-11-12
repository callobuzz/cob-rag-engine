"""Archiving a learned page (BIZ2): out of the answers, kept in history, back when it returns."""

from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.website import archive as archive_mod
from callobuzz_rag.website.learn import learn_source
from tests.unit.test_website_learn_runs import (  # noqa: F401  (autouse fixture)
    ORIGIN,
    World,
    _no_cache,
    long_page,
)

PRICING = f"{ORIGIN}/pricing"


async def learned() -> World:
    w = World(
        {
            "/": long_page("Home", "home", links=("/pricing",)),
            "/pricing": long_page("Pricing", "price"),
        }
    )
    assert (await learn_source(w.source.id, deps=w.deps())).status == "done"
    return w


async def archive(w: World, *urls: str) -> int:
    pages = [w.pages()[u] for u in urls]
    session = w.db.session()
    corpus = await session.get(Corpus, w.corpus.id)
    return await archive_mod.archive_pages(session, w.vector_store, corpus, pages)


def versions_of(w: World, document_id: object) -> list[DocumentVersion]:
    return [v for v in w.db.all(DocumentVersion) if v.document_id == document_id]


async def test_archiving_deactivates_the_version_and_deletes_its_vectors() -> None:
    w = await learned()
    page = w.pages()[PRICING]
    doc = next(d for d in w.db.all(Document) if d.id == page.document_id)
    active = [v for v in versions_of(w, doc.id) if v.status == "active"]
    assert len(active) == 1 and doc.content_fingerprint is not None
    assert await archive(w, PRICING) == 1
    assert w.vector_store.deleted == [{"document_version_id": str(active[0].id)}]
    assert [v.status for v in versions_of(w, doc.id)] == ["inactive"]  # the history stays
    assert doc.content_fingerprint is None and doc in w.db.all(Document)


async def test_archiving_twice_changes_nothing_more() -> None:
    w = await learned()
    assert await archive(w, PRICING) == 1
    assert await archive(w, PRICING) == 0
    assert len(w.vector_store.deleted) == 1


async def test_a_page_with_no_document_is_skipped() -> None:
    w = await learned()
    page = w.pages()[PRICING]
    page.document_id = None
    assert await archive(w, PRICING) == 0 and w.vector_store.deleted == []


async def test_the_same_text_learned_again_is_a_new_active_version() -> None:
    w = await learned()
    page = w.pages()[PRICING]
    await archive(w, PRICING)
    batches = len(w.batches)
    # the page comes back with exactly the text it had: it must not be skipped as "unchanged"
    page.status, page.content_hash = "queued", None
    from callobuzz_rag.website.learn import learn_urls

    result = await learn_urls(w.source.id, [PRICING], deps=w.deps())
    assert result.status == "done" and len(w.batches) == batches + 1
    versions = versions_of(w, page.document_id)
    assert [v.status for v in versions].count("active") == 1 and len(versions) == 2
