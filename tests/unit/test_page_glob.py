"""One glob for every "which pages" field; the widget reads the same fixture (C9)."""

import json
from pathlib import Path

import pytest

from callobuzz_rag.conversation.page_glob import matches

CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/conversation/page_globs.json").read_text("utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=[f"{c['glob']}|{c['url']}" for c in CASES])
def test_shared_cases(case: dict[str, object]) -> None:
    assert matches(str(case["glob"]), str(case["url"])) is case["match"]


def test_the_compiler_and_the_lead_triggers_use_it() -> None:
    from callobuzz_rag.conversation import compiler
    from callobuzz_rag.conversation.leads import triggers

    assert compiler.matches is matches and triggers.matches is matches
