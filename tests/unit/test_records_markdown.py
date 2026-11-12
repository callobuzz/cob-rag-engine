import dataclasses
import hashlib
import json

from callobuzz_rag.parsing.models import ElementType
from callobuzz_rag.pipeline.records import TextRecord


def test_markdown_record_turns_hash_lines_into_headings() -> None:
    rec = TextRecord(
        id="web:https://shreedental.in/pricing",
        title="Pricing",
        markdown=True,
        text="# Root canal\nFrom ₹4,500 per tooth.\n\n## Cleaning\n₹800 per sitting",
    )
    got = [(e.type, e.level, e.text) for e in rec.to_parsed().elements]
    assert got == [
        (ElementType.HEADING, 1, "Pricing"),
        (ElementType.HEADING, 1, "Root canal"),
        (ElementType.PARAGRAPH, None, "From ₹4,500 per tooth."),
        (ElementType.HEADING, 2, "Cleaning"),
        (ElementType.PARAGRAPH, None, "₹800 per sitting"),
    ]


def test_list_lines_stay_one_paragraph_and_a_hash_without_space_is_text() -> None:
    rec = TextRecord(
        id="web:x", markdown=True, text="- Mon-Sat 10:00-19:00\n- Sunday closed\n\n#hashtag"
    )
    got = [(e.type, e.text) for e in rec.to_parsed().elements]
    assert got == [
        (ElementType.PARAGRAPH, "- Mon-Sat 10:00-19:00\n- Sunday closed"),
        (ElementType.PARAGRAPH, "#hashtag"),
    ]


def test_plain_records_keep_their_old_fingerprint_and_paragraphs() -> None:
    rec = TextRecord(id="faq:1", text="Hello", title="Hi")
    old = hashlib.sha256(
        json.dumps(
            {"title": "Hi", "text": "Hello", "url": None, "metadata": {}},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    assert rec.fingerprint() == old
    assert TextRecord(id="faq:1", text="Hello", title="Hi", markdown=True).fingerprint() != old
    plain = TextRecord(id="faq:2", text="# not a heading\n\nsecond")
    assert [e.type for e in plain.to_parsed().elements] == [ElementType.PARAGRAPH] * 2


def test_record_round_trips_through_asdict() -> None:  # the worker passes dicts
    rec = TextRecord(id="web:x", text="# A", markdown=True)
    assert TextRecord(**dataclasses.asdict(rec)) == rec
