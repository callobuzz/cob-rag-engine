"""Brochures on WhatsApp: only what the owner marked, uploaded once, never a private file (TV6)."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.channels.whatsapp import documents
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from tests.unit.whatsapp_fixtures import inbound_text
from tests.unit.whatsapp_rig import NOW, rig_for
from tests.unit.whatsapp_world import WhatsAppWorld

pytestmark = pytest.mark.assistant_on


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


def add_document(
    world: WhatsAppWorld, rig, name: str, *, mime: str = "application/pdf", marked: bool = True
):
    document = world.db.add(
        Document(
            corpus_id=world.corpus.id,
            file_name=name,
            mime_type=mime,
            raw_storage_key=f"corpus/{world.corpus.id}/{name}",
        )
    )
    world.db.add(DocumentVersion(document_id=document.id, status="active"))
    rig.storage.files[document.raw_storage_key] = b"%PDF-1.7 " + name.encode()
    if marked:
        ids = list(world.connection.settings.get("shareable_document_ids") or [])
        world.connection.settings = {
            **world.connection.settings,
            "shareable_document_ids": [*ids, str(document.id)],
        }
    return document


# ------------------------------------------------------------------ the request


@pytest.mark.parametrize(
    "text",
    [
        "brochure",
        "Brochure bhejo",
        "menu bhejiye",
        "price list please",
        "can I get the rate card",
        "please send me your menu",
        "प्राइस लिस्ट भेजो",
        "ब्रोशर चाहिए",
        "Can you send the catalogue?",
        "I need the fee structure",
    ],
)
def test_requests_for_a_document(text: str) -> None:
    assert documents.is_document_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "What is on the menu for lunch and what are the timings on sunday?",
        "How much is a root canal?",
        "namaste",
        "I lost my brochure holder somewhere in the clinic yesterday evening",
        "",
    ],
)
def test_other_messages_are_not_document_requests(text: str) -> None:
    assert not documents.is_document_request(text)


def _doc(name: str, **kw):
    return SimpleNamespace(
        id=uuid.uuid4(),
        file_name=name,
        doc_metadata=kw.get("meta"),
        mime_type=kw.get("mime", "application/pdf"),
    )


def test_the_best_matching_documents_are_chosen_at_most_three() -> None:
    docs = [
        _doc("price-list-2026.pdf"),
        _doc("dinner-menu.pdf"),
        _doc("about-us.pdf"),
        _doc("careers.pdf"),
        _doc("faq.pdf"),
    ]
    assert [d.file_name for d in documents.choose(docs, "price list bhejo")] == [
        "price-list-2026.pdf"
    ]
    assert [d.file_name for d in documents.choose(docs, "menu please")] == ["dinner-menu.pdf"]


def test_with_no_match_and_three_or_fewer_all_are_sent_else_none() -> None:
    few = [_doc("a.pdf"), _doc("b.pdf")]
    assert len(documents.choose(few, "brochure bhejo")) == 2
    many = [_doc(f"d{i}.pdf") for i in range(4)]
    assert documents.choose(many, "brochure bhejo") == []


def test_a_title_in_the_metadata_counts() -> None:
    docs = [_doc("doc1.pdf", meta={"title": "Dental price list"}), _doc("doc2.pdf")]
    assert documents.choose(docs, "price list")[0].file_name == "doc1.pdf"


def test_only_pdf_jpg_png_can_be_sent() -> None:
    assert documents.sendable(_doc("a.pdf"))
    assert documents.sendable(_doc("a.JPG", mime="image/jpeg"))
    assert documents.sendable(_doc("scan", mime="image/png"))
    assert documents.sendable(SimpleNamespace(file_name="menu.jpeg", mime_type=None))
    assert not documents.sendable(
        _doc(
            "a.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    )
    assert not documents.sendable(SimpleNamespace(file_name=None, mime_type=None))


# ------------------------------------------------------------------ sending


async def test_a_brochure_request_sends_the_marked_documents_and_uploads_each_once(world) -> None:
    with rig_for(world) as rig:
        add_document(world, rig, "price-list.pdf")
        add_document(world, rig, "dinner-menu.pdf")
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.B1", text="brochure bhejo"))
        await rig.run(thread_id)
        uploads_after_first = rig.graph.uploads
        await rig.deliver(
            inbound_text(wamid="wamid.B2", text="brochure bhejo", timestamp=1_790_000_090)
        )
        await rig.run(thread_id)
    docs = [t for t in rig.texts() if t.startswith("document:")]
    assert docs == ["document:price-list.pdf", "document:dinner-menu.pdf"] * 2
    assert uploads_after_first == 2 and rig.graph.uploads == 2  # the second request reuses the ids
    assert rig.default_llm.calls == [] and rig.io.meter.await_count == 0 and rig.calls == []
    sent = [r for r in rig.sends() if r.json["type"] == "document"]
    assert (
        sent[0].json["document"]["id"] == "MEDIA1"
        and sent[0].json["document"]["filename"] == "price-list.pdf"
    )
    upload = next(r for r in rig.graph.requests if r.path.endswith("/media"))
    assert upload.path == "/v23.0/1234567890/media" and b"price-list.pdf" in upload.raw
    assert "Sent: price-list.pdf, dinner-menu.pdf" in next(
        m.content for m in rig.stored("assistant")
    )


async def test_a_document_that_is_not_marked_is_never_sent_even_if_it_matches(world) -> None:
    with rig_for(world) as rig:
        add_document(world, rig, "price-list.pdf", marked=False)
        add_document(world, rig, "dinner-menu.pdf")
        (thread_id,) = await rig.deliver(inbound_text(text="price list bhejo"))
        await rig.run(thread_id)
    assert [t for t in rig.texts() if t.startswith("document:")] == ["document:dinner-menu.pdf"]


async def test_with_nothing_marked_the_normal_answer_takes_the_question(world) -> None:
    with rig_for(world) as rig:
        add_document(world, rig, "price-list.pdf", marked=False)
        (thread_id,) = await rig.deliver(inbound_text(text="price list bhejo"))
        await rig.run(thread_id)
    assert not any(t.startswith("document:") for t in rig.texts())
    assert rig.default_llm.calls != [] and rig.graph.uploads == 0


async def test_a_deleted_document_drops_out_of_the_list(world) -> None:
    with rig_for(world) as rig:
        doc = add_document(world, rig, "price-list.pdf")
        add_document(world, rig, "dinner-menu.pdf")
        world.db.rows[Document].remove(doc)
        (thread_id,) = await rig.deliver(inbound_text(text="brochure bhejo"))
        await rig.run(thread_id)
    assert [t for t in rig.texts() if t.startswith("document:")] == ["document:dinner-menu.pdf"]


async def test_a_replaced_file_is_uploaded_again_and_an_old_id_expires(world) -> None:
    with rig_for(world) as rig:
        doc = add_document(world, rig, "price-list.pdf")
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.B1", text="price list please"))
        await rig.run(thread_id)
        assert rig.graph.uploads == 1
        # a new active version: the cached id belongs to the old one
        for version in world.db.all(DocumentVersion):
            version.status = "superseded"
        world.db.add(DocumentVersion(document_id=doc.id, status="active"))
        await rig.deliver(
            inbound_text(wamid="wamid.B2", text="price list please", timestamp=1_790_000_090)
        )
        await rig.run(thread_id)
        assert rig.graph.uploads == 2
        # 26 days on, every cached id has expired
        rig.now = NOW + timedelta(days=26)
        late = int(rig.now.timestamp()) - 5
        await rig.deliver(inbound_text(wamid="wamid.B3", text="price list please", timestamp=late))
        await rig.run(thread_id)
    assert rig.graph.uploads == 3


async def test_an_upload_that_fails_falls_back_to_the_normal_answer(world) -> None:
    from tests.unit.whatsapp_fixtures import FakeGraph

    graph = FakeGraph()
    graph.script = [(400, {"error": {"code": 100, "message": "bad file"}})]
    with rig_for(world, graph=graph) as rig:
        add_document(world, rig, "price-list.pdf")
        (thread_id,) = await rig.deliver(inbound_text(text="price list please"))
        await rig.run(thread_id)
    assert not any(t.startswith("document:") for t in rig.texts())
    assert rig.default_llm.calls != []


async def test_a_request_for_a_person_beats_a_brochure_request(world) -> None:
    with rig_for(world) as rig:
        add_document(world, rig, "price-list.pdf")
        (thread_id,) = await rig.deliver(
            inbound_text(text="send me the brochure or let me talk to a person")
        )
        await rig.run(thread_id)
    assert not any(t.startswith("document:") for t in rig.texts())
    assert "Shree Labs" in "\n".join(rig.texts())


# ------------------------------------------------------------------ marking documents


async def test_marking_a_document_of_another_knowledge_base_or_a_docx_is_refused(world) -> None:
    session = world.db.session()
    mine = world.db.add(
        Document(corpus_id=world.corpus.id, file_name="a.pdf", mime_type="application/pdf")
    )
    word = world.db.add(
        Document(corpus_id=world.corpus.id, file_name="a.docx", mime_type="application/msword")
    )
    other = world.db.add(
        Document(corpus_id=uuid.uuid4(), file_name="b.pdf", mime_type="application/pdf")
    )
    assert await documents.validate_marked(session, world.corpus.id, [mine.id, mine.id]) == [
        mine.id
    ]
    for bad in ([word.id], [other.id], [uuid.uuid4()], [mine.id, word.id]):
        with pytest.raises(APIError) as caught:
            await documents.validate_marked(session, world.corpus.id, bad)
        assert caught.value.status_code == 422 and caught.value.code == "whatsapp_document_invalid"
    assert await documents.validate_marked(session, world.corpus.id, []) == []


async def test_at_most_twenty_documents_can_be_marked(world) -> None:
    with pytest.raises(APIError) as caught:
        await documents.validate_marked(
            world.db.session(), world.corpus.id, [uuid.uuid4() for _ in range(21)]
        )
    assert caught.value.code == "whatsapp_document_invalid"
