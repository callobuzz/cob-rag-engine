"""Heading ids: COB's `slugify` and `Anchors` against the fixture the dashboard's renderer is
also tested with, so a Docs table-of-contents anchor always finds its heading."""

import json
from pathlib import Path

import pytest

from callobuzz_rag.cob.sources.common import Anchors, slugify

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "markdown" / "heading-ids.json").read_text(
        encoding="utf-8"
    )
)


def test_fixture_is_big_enough() -> None:
    assert len(FIXTURE["cases"]) >= 15 and FIXTURE["sequences"]


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["text"][:30])
def test_slugify_matches_the_fixture(case: dict[str, str]) -> None:
    assert slugify(case["text"]) == case["id"]


@pytest.mark.parametrize("seq", FIXTURE["sequences"])
def test_duplicates_get_a_counter(seq: dict[str, list[str]]) -> None:
    anchors = Anchors()
    assert [anchors.take(slugify(h)) for h in seq["headings"]] == seq["ids"]
