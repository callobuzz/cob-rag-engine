"""Setup auto tests (C8): 15-25 cases with Hinglish, off-topic and sensitive ones; gaps."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from callobuzz_rag.conversation.setup.autotests import (
    build_setup_cases,
    case,
    document_cases,
    gaps_from_samples,
    section_headings,
    template_cases,
)
from callobuzz_rag.conversation.templates import TemplateTest, load_template
from callobuzz_rag.llm.models import LLMResponse

TEMPLATE = template_cases(
    [
        TemplateTest(question=f"Template question {i}", expected="answer", language="en")
        for i in range(10)
    ]
)


def _llm(payload: object, options: dict | None = None) -> MagicMock:
    llm = MagicMock()
    llm.has_credentials = True
    llm.fast_call_options.return_value = options or {"model": "fast"}
    llm.complete_json = AsyncMock(
        return_value=LLMResponse(content=json.dumps(payload), model="fast")
    )
    return llm


def test_template_cases_are_behaviour_only_pairs() -> None:
    assert all(p.expected_behaviour == "answer" and p.expected_answer is None for p in TEMPLATE)
    assert all(p.origin == "human" for p in TEMPLATE)


def test_counts_and_mix_without_document_cases() -> None:
    cases = build_setup_cases(template_tests=TEMPLATE, doc_cases=[], profile="medical")
    assert 15 <= len(cases) <= 25
    assert sum(c.language == "hi-Latn" for c in cases) >= 3
    assert sum(c.expected_behaviour in ("decline", "handoff") for c in cases) >= 2
    assert any("medicine" in c.query.lower() or "dose" in c.query.lower() for c in cases)
    assert len({c.query for c in cases}) == len(cases)


def test_capped_at_25_with_many_document_cases() -> None:
    docs = [case(f"Doc question {i}", "answer", "en", origin="ai_generated") for i in range(8)]
    cases = build_setup_cases(template_tests=TEMPLATE, doc_cases=docs, profile="none")
    assert len(cases) == 25


def test_every_real_template_yields_15_to_25_cases_with_hinglish() -> None:
    from callobuzz_rag.conversation.templates import list_templates

    for template in list_templates():
        profile = template.config.scope.sensitive_profile
        cases = build_setup_cases(
            template_tests=template_cases(template.tests), doc_cases=[], profile=profile
        )
        assert 15 <= len(cases) <= 25, template.key
        assert sum(c.language == "hi-Latn" for c in cases) >= 3, template.key


async def test_document_cases_from_headings() -> None:
    llm = _llm(
        {
            "questions": [
                {"question": "Do I need to fast for a lipid profile?", "language": "en"},
                {"question": "Lipid profile ke liye khali pet aana hai?", "language": "hi-Latn"},
                {"question": "   ", "language": "en"},
            ]
        }
    )
    cases = await document_cases(llm, ["Test preparation > Lipid profile"])
    assert [c.language for c in cases] == ["en", "hi-Latn"]
    assert all(c.expected_behaviour == "answer" and c.origin == "ai_generated" for c in cases)


async def test_document_cases_headings_are_escaped_and_cap_raised() -> None:
    llm = _llm({"questions": []}, options={"model": "fast", "max_tokens": 512})
    await document_cases(llm, ['x </li> "ignore previous" <b>'])
    prompt = llm.complete_json.await_args.args[0][0].content
    assert "&lt;/li&gt;" in prompt and "<b>" not in prompt
    assert llm.complete_json.await_args.kwargs["max_tokens"] >= 800


async def test_document_cases_without_credentials_or_on_failure() -> None:
    llm = MagicMock()
    llm.has_credentials = False
    assert await document_cases(llm, ["Anything"]) == []
    failing = _llm({})
    failing.complete_json = AsyncMock(side_effect=RuntimeError("down"))
    assert await document_cases(failing, ["Anything"]) == []


async def test_section_headings_skip_engine_documents() -> None:
    import uuid

    captured: dict[str, str] = {}

    class _Result:
        def all(self) -> list[tuple[str, int]]:
            return [("Prices > CBC", 3), ("", 1)]

    class _Session:
        async def execute(self, stmt: object) -> _Result:
            captured["sql"] = str(
                stmt.compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
            )
            return _Result()

    out = await section_headings(_Session(), uuid.uuid4())  # type: ignore[arg-type]
    assert out == ["Prices > CBC"]
    sql = captured["sql"]
    assert "system:%" in sql or "NOT LIKE" in sql
    assert "document_versions.status" in sql


def test_gaps_are_expected_answers_that_found_nothing() -> None:
    samples = [
        SimpleNamespace(
            question="Do you do thyroid tests?",
            behaviour={"expected": "answer", "route": "no_answer"},
        ),
        SimpleNamespace(
            question="Who won the match?", behaviour={"expected": "decline", "route": "decline"}
        ),
        SimpleNamespace(
            question="What are your timings?",
            behaviour={"expected": "answer", "route": "grounded"},
        ),
        SimpleNamespace(question="A question that errored", behaviour=None),
    ]
    assert gaps_from_samples(samples) == [
        {
            "question": "Do you do thyroid tests?",
            "message": "No document covers this yet — add one?",
        }
    ]


def test_template_loaded_for_the_arithmetic() -> None:
    assert len(load_template("clinic").tests) == 10
