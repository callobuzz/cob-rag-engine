"""Ingest end to end with the boilerplate step and the near-duplicate check wired in.

The vector store and the embedder are small in-memory fakes (hashed bag-of-words vectors,
real cosine), so the similarity check sees real scores: pages of one site that share a
menu, a call to action and a disclaimer must not be mistaken for each other, and two
copies of one page must.
"""

import math
import random
import re
import uuid
import zlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.chunking.chunker import ChunkerConfig, StructureAwareChunker
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.enrichment.models import EnrichmentResult
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement
from callobuzz_rag.pipeline import orchestrator as orchestrator_module
from callobuzz_rag.pipeline.models import IngestMode, JobStatus
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
from callobuzz_rag.pipeline.text_hash import text_hash
from callobuzz_rag.vectordb.base import SearchResult

DIM = 256
BLANK_LINE = chr(10) * 2

MENU = "Home Products Pricing Warranty Stores Contact Careers Blog Login"
CTA = "Contact us today for a free site visit anywhere in Pune, Mumbai or Bengaluru."
DISCLAIMER = (
    "All prices are in Indian rupees and include GST. Offers are valid while stocks last, "
    "and the company may change terms without notice. E&OE."
)
NEWSLETTER = "Subscribe to our newsletter for service tips, new launches and festival offers."
TOPICS = [
    ("tent", "poles, fabric, zips, guy lines and the rain fly of a four person tent"),
    ("boots", "soles, stitching, waterproof membranes and resoling of a trekking boot"),
    ("bags", "down fill, loft, shell fabric and compression of a sleeping bag"),
    ("stoves", "burners, gas canisters, regulators and windshields of a camp stove"),
    ("packs", "straps, frames, hip belts and zips of a forty litre rucksack"),
    ("jackets", "seams, hoods, cuffs and taped zips of a hardshell rain jacket"),
    ("lamps", "batteries, lenses, switches and charging ports of a headlamp"),
    ("poles", "locks, tips, straps and baskets of a collapsible trekking pole"),
    ("mats", "valves, baffles, glue and patches of an inflatable sleeping mat"),
    ("filters", "cartridges, seals, hoses and backflushing of a water filter"),
]


def _embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for token in re.findall(r"\w+", text.lower()):
        vec[zlib.crc32(token.encode()) % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeEmbedder:
    async def embed_texts(self, texts: list[str], task: str | None = None) -> list[Any]:
        return [
            EmbeddingResult(vector=_embed(t), model="fake", model_version="1", dimensions=DIM)
            for t in texts
        ]


class FakeVectors:
    def __init__(self) -> None:
        self.points: dict[str, Any] = {}

    async def upsert(self, collection: str, points: list[Any]) -> None:
        for p in points:
            self.points[p.id] = p

    async def search(
        self, collection: str, query_vector: list[float], limit: int = 10, filters: Any = None
    ) -> list[SearchResult]:
        scored = [
            SearchResult(
                id=p.id,
                score=sum(a * b for a, b in zip(query_vector, p.vector, strict=True)),
                payload=p.payload,
            )
            for p in self.points.values()
            if p.payload.get("corpus_id") == (filters or {}).get("corpus_id")
        ]
        return sorted(scored, key=lambda r: -r.score)[:limit]

    async def delete_by_filter(self, collection: str, filters: dict[str, Any]) -> None:
        def matches(payload: dict[str, Any]) -> bool:
            return all(
                payload.get(key) in value if isinstance(value, list) else payload.get(key) == value
                for key, value in filters.items()
            )

        self.points = {k: p for k, p in self.points.items() if not matches(p.payload)}


class MemoryStore:
    """`SqlBlockHashStore`'s contract, in memory and shared between ingests."""

    def __init__(self) -> None:
        self.rows: set[tuple[Any, str, Any]] = set()

    async def counts(self, corpus_id, document_id, hashes):
        out: dict[str, int] = {}
        for c, h, d in self.rows:
            if c == corpus_id and h in hashes and d != document_id:
                out[h] = out.get(h, 0) + 1
        return out

    async def replace(self, corpus_id, document_id, hashes):
        self.rows = {r for r in self.rows if r[2] != document_id}
        self.rows |= {(corpus_id, h, document_id) for h in hashes}


def para(text: str) -> ParsedElement:
    return ParsedElement(type=ElementType.PARAGRAPH, text=text)


def _prose(rng: random.Random, words: int) -> str:
    """Distinct body text: pages of one site differ in what they say, not only in a number."""
    pool = [a + b + c for a in "bdfgklmnprstv" for b in "aeiou" for c in ("n", "r", "l", "k", "m")]
    return " ".join(rng.choice(pool) for _ in range(words)).capitalize() + "."


def site_page(n: int) -> list[ParsedElement]:
    """A page of one shop's site: its own content between the site's repeated lines."""
    rng = random.Random(n)
    word, about = TOPICS[n % len(TOPICS)]
    return [
        ParsedElement(type=ElementType.HEADING, text=f"Warranty for {word} (page {n})", level=1),
        para(MENU),
        para(f"This page explains the warranty on {about}. " + _prose(rng, 70)),
        para(_prose(rng, 80)),
        para(_prose(rng, 80)),
        para(CTA),
        para(DISCLAIMER),
        para(NEWSLETTER),
    ]


def build(
    *,
    settings: Settings | None = None,
    retrieval_config: dict[str, Any] | None = None,
    chunker_config: ChunkerConfig | None = None,
) -> tuple[IngestionOrchestrator, Any, FakeVectors, AsyncMock, list[Any]]:
    session = AsyncMock()
    nothing = MagicMock()
    nothing.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=nothing)
    session.add = MagicMock()
    added: list[Any] = []
    session.add_all = MagicMock(side_effect=lambda objs: added.extend(objs))
    corpus = SimpleNamespace(retrieval_config=retrieval_config)

    async def get(model: Any, key: Any) -> Any:
        if model is Corpus:
            return corpus
        if model is IngestJob:
            return IngestJob(id=key, status="queued")
        return None

    session.get = AsyncMock(side_effect=get)
    parser = AsyncMock()
    vectors = FakeVectors()
    enricher = AsyncMock()
    enricher.enrich_batch = AsyncMock(side_effect=lambda cs: [EnrichmentResult() for _ in cs])
    orch = IngestionOrchestrator(
        session=session,
        storage=AsyncMock(),
        parser=parser,
        chunker=StructureAwareChunker(chunker_config or ChunkerConfig()),
        enricher=enricher,
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=vectors,  # type: ignore[arg-type]
        settings=settings or Settings(_env_file=None),
    )
    return orch, session, vectors, parser, added


@pytest.fixture
def shared_hashes(monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    store = MemoryStore()
    monkeypatch.setattr(orchestrator_module, "SqlBlockHashStore", lambda session: store)
    return store


async def ingest(orch, parser, corpus_id, name, elements, mode=IngestMode.AUTO):
    parser.parse = AsyncMock(
        return_value=ParsedDocument(elements=elements, parser_name="docling", parser_version="x")
    )
    return await orch.ingest(
        file_bytes=name.encode() + b"-" + uuid.uuid4().bytes,
        file_name=name,
        mime_type="text/markdown",
        corpus_id=corpus_id,
        storage_key=f"k/{name}",
        mode=mode,
        auto_confirm=False,
        job_id=uuid.uuid4(),
    )


async def test_scraper_pages_same_site_not_pending_review(shared_hashes: MemoryStore) -> None:
    """A 10-page scrape of one site, sent the way the scraper sends it (auto mode, no
    auto-confirm): every page completes, none waits for a review."""
    orch, _, _, parser, _ = build()
    corpus_id = uuid.uuid4()
    results = [
        await ingest(orch, parser, corpus_id, f"page-{n}.md", site_page(n)) for n in range(10)
    ]
    assert [r.status for r in results] == [JobStatus.COMPLETED] * 10
    assert not [r for r in results if r.status == JobStatus.PENDING_REVIEW]
    assert all(not r.similar_documents for r in results)

    # Pages 0-2 keep the shared lines; from page 3 on they are stripped, so the index holds
    # far fewer copies of them.
    assert results[0].chunks_created and results[9].chunks_created
    assert not any("blocks stripped" in w for w in results[0].warnings)
    stripped = [w for r in results[3:] for w in r.warnings if "stripped" in w]
    assert len(stripped) == 7  # every page after the third lost its shared lines
    assert all(
        w == "4 blocks stripped" for w in stripped
    )  # menu, call to action, disclaimer, newsletter


async def test_the_shared_lines_are_kept_by_the_first_three_pages_only(
    shared_hashes: MemoryStore,
) -> None:
    orch, _, _, parser, added = build()
    corpus_id = uuid.uuid4()
    for n in range(6):
        await ingest(orch, parser, corpus_id, f"page-{n}.md", site_page(n))
    chunk_texts = [c.text for c in added if isinstance(c, Chunk)]
    assert len(chunk_texts) == 6  # one chunk per page
    # The disclaimer is in the first three pages' chunks and no later one: the knowledge
    # base keeps a searchable copy, not a copy per page.
    assert [("E&OE" in t) for t in chunk_texts] == [True, True, True, False, False, False]


async def test_two_copies_of_one_page_are_still_flagged_as_near_duplicates(
    shared_hashes: MemoryStore,
) -> None:
    orch, _, _, parser, _ = build()
    corpus_id = uuid.uuid4()
    first = await ingest(orch, parser, corpus_id, "warranty.md", site_page(0))
    assert first.status == JobStatus.COMPLETED

    # The same page again, saved under another name, with one sentence changed.
    again = site_page(0)
    again[2] = para(again[2].text.replace("This page explains", "This page describes"))
    second = await ingest(orch, parser, corpus_id, "warranty-copy.md", again)

    assert second.status == JobStatus.PENDING_REVIEW
    assert second.similar_documents and second.similar_documents[0]["match_count"] >= 1
    assert second.similar_documents[0]["similarity"] >= 0.9


async def test_chunks_and_payloads_carry_the_text_hash(shared_hashes: MemoryStore) -> None:
    orch, _, vectors, parser, added = build()
    corpus_id = uuid.uuid4()
    await ingest(orch, parser, corpus_id, "page-0.md", site_page(0))
    chunks = [c for c in added if isinstance(c, Chunk)]
    assert chunks and all(c.text_hash and len(c.text_hash) == 16 for c in chunks)
    by_id = {str(c.id): c for c in chunks}
    for point in vectors.points.values():
        assert point.payload["text_hash"] == by_id[point.id].text_hash
    # The hash is of the core text: it equals the hash of the stored text when no overlap
    # was prepended (the first chunk of a section never has any).
    first = min(chunks, key=lambda c: c.chunk_index or 0)
    assert first.text_hash == text_hash(first.text)


async def test_boilerplate_corpus_opt_out_keeps_every_page_whole(
    shared_hashes: MemoryStore,
) -> None:
    orch, _, _, parser, _ = build(retrieval_config={"boilerplate_strip": False})
    corpus_id = uuid.uuid4()
    for n in range(5):
        result = await ingest(orch, parser, corpus_id, f"page-{n}.md", site_page(n))
        assert not any("stripped" in w for w in result.warnings)
    assert shared_hashes.rows == set()  # the step never ran: nothing recorded either
    # ... and the parser was told to leave the page's HTML chrome alone
    assert parser.parse.await_args.kwargs == {"strip_chrome": False}


async def test_engine_setting_off_disables_the_step_everywhere(shared_hashes: MemoryStore) -> None:
    orch, _, _, parser, _ = build(
        settings=Settings(_env_file=None, boilerplate_strip_enabled=False)
    )
    for n in range(5):
        result = await ingest(orch, parser, uuid.uuid4(), f"page-{n}.md", site_page(n))
        assert not any("stripped" in w for w in result.warnings)
    assert shared_hashes.rows == set()


async def test_enabled_step_leaves_the_parser_call_unchanged(shared_hashes: MemoryStore) -> None:
    orch, _, _, parser, _ = build()
    await ingest(orch, parser, uuid.uuid4(), "page-0.md", site_page(0))
    assert parser.parse.await_args.kwargs == {}  # chrome stripping stays on (its default)


async def test_a_page_held_for_review_is_rolled_back(
    shared_hashes: MemoryStore,
) -> None:
    """Hashes are written in the job's transaction; a page held for review rolls back with
    it, and is processed again (and counted) only when confirmed."""
    orch, session, _, parser, _ = build()
    corpus_id = uuid.uuid4()
    await ingest(orch, parser, corpus_id, "warranty.md", site_page(0))
    again = site_page(0)
    again[2] = para(again[2].text.replace("This page explains", "This page describes"))
    held = await ingest(orch, parser, corpus_id, "warranty-copy.md", again)
    assert held.status == JobStatus.PENDING_REVIEW
    session.rollback.assert_awaited()


async def test_text_records_get_the_same_treatment(shared_hashes: MemoryStore) -> None:
    from callobuzz_rag.pipeline.records import TextRecord

    orch, session, vectors, _, added = build()
    rows = MagicMock()
    rows.scalars.return_value = []
    session.execute = AsyncMock(return_value=rows)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    records = [
        TextRecord(
            id=f"faq-{n}",
            title=f"FAQ {n}",
            text=BLANK_LINE.join(
                [
                    _prose(random.Random(n), 60),
                    _prose(random.Random(n + 100), 60),
                    CTA,
                    DISCLAIMER,
                ]
            ),
        )
        for n in range(6)
    ]
    outcome = await orch.ingest_records(records, uuid.uuid4(), uuid.uuid4())

    assert outcome.status == "completed", outcome.error
    texts = [c.text for c in added if isinstance(c, Chunk)]
    assert [("E&OE" in t) for t in texts] == [True, True, True, False, False, False]
    assert all(p.payload["text_hash"] for p in vectors.points.values())


def short_page(n: int) -> list[ParsedElement]:
    """A short page of the same site: one or two body paragraphs, the rest is furniture."""
    rng = random.Random(100 + n)
    word, about = TOPICS[n % len(TOPICS)]
    body = [para(f"The warranty on {about} lasts a year. " + _prose(rng, 18))]
    if n % 2:
        body.append(para(_prose(rng, 14)))
    return [
        ParsedElement(type=ElementType.HEADING, text=f"Warranty for {word}", level=1),
        para(MENU),
        *body,
        para(CTA),
        para(DISCLAIMER),
        para(NEWSLETTER),
    ]


async def test_short_pages_of_one_site_do_not_wait_for_review(shared_hashes: MemoryStore) -> None:
    """Pages 2 and 3 keep their furniture (fewer than three other pages hold it yet), but the
    near-duplicate check must compare their own text, not the menu and footer they share
    with page 1."""
    orch, _, _, parser, _ = build()
    corpus_id = uuid.uuid4()
    results = [
        await ingest(orch, parser, corpus_id, f"short-{n}.md", short_page(n)) for n in range(1, 5)
    ]
    assert [r.status for r in results] == [JobStatus.COMPLETED] * 4
    assert all(not r.similar_documents for r in results)


def sectioned_page(n: int) -> list[ParsedElement]:
    """A short page whose furniture sits under its own headings, as a footer does."""
    rng = random.Random(200 + n)
    word, about = TOPICS[n % len(TOPICS)]

    def section(title: str, *blocks: str) -> list[ParsedElement]:
        head = ParsedElement(type=ElementType.HEADING, text=title, level=2)
        return [head, *[para(b) for b in blocks]]

    return [
        ParsedElement(type=ElementType.HEADING, text=f"Warranty for {word}", level=1),
        para(MENU),
        para(f"The warranty on {about} lasts a year. " + _prose(rng, 18)),
        *section("Visit us", CTA),
        *section("Legal", DISCLAIMER),
        *section("Newsletter", NEWSLETTER),
    ]


async def test_furniture_only_chunks_do_not_make_pages_near_duplicates(
    shared_hashes: MemoryStore,
) -> None:
    """When furniture sits in chunks of its own, three of a page's sampled chunks used to be
    the menu, call to action and disclaimer: page 2 matched page 1 on all three and waited
    for review. Pages 2 and 3 still hold their furniture (the count has not reached three),
    so the sample leaves out chunks made only of paragraphs other documents hold."""
    orch, _, _, parser, _ = build(chunker_config=ChunkerConfig(min_tokens=1, overlap_tokens=0))
    corpus_id = uuid.uuid4()
    results = [
        await ingest(orch, parser, corpus_id, f"sec-{n}.md", sectioned_page(n)) for n in range(1, 5)
    ]
    assert [r.status for r in results] == [JobStatus.COMPLETED] * 4, [
        r.similar_documents for r in results
    ]
    assert all(not r.similar_documents for r in results)
