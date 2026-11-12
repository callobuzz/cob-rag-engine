"""Unit tests for context assembly and prompt templates."""

from callobuzz_rag.retrieval.prompt_templates import (
    CONSTRAINTS_BLOCK,
    CONTEXT_HEADER,
    QUESTION_PREFIX,
    SYSTEM_PROMPT,
    format_citation,
)


class TestFormatCitation:
    def test_with_file_and_page(self) -> None:
        result = format_citation(1, "Some text", "report.pdf", 3)
        assert result == "[1] (source: report.pdf, page: 3)\nSome text"

    def test_with_file_only(self) -> None:
        result = format_citation(2, "Text", "doc.docx", None)
        assert result == "[2] (source: doc.docx)\nText"

    def test_with_page_only(self) -> None:
        result = format_citation(3, "Text", None, 5)
        assert result == "[3] (page: 5)\nText"

    def test_no_metadata(self) -> None:
        result = format_citation(4, "Text", None, None)
        assert result == "[4]\nText"

    def test_citation_index_preserved(self) -> None:
        result = format_citation(42, "Content", "f.pdf", 1)
        assert result.startswith("[42]")


class TestPromptConstants:
    def test_system_prompt_exists(self) -> None:
        assert "ONLY" in SYSTEM_PROMPT
        assert "[N]" in SYSTEM_PROMPT

    def test_constraints_block_exists(self) -> None:
        assert "CONSTRAINTS:" in CONSTRAINTS_BLOCK
        assert "[N]" in CONSTRAINTS_BLOCK

    def test_context_header(self) -> None:
        assert CONTEXT_HEADER == "CONTEXT:"

    def test_question_prefix(self) -> None:
        assert QUESTION_PREFIX == "QUESTION: "


def test_citation_carries_the_section_heading() -> None:
    """The heading is often the only place a chunk says what it is about."""
    result = format_citation(1, "Adds offline checkout.", "release-notes.md", None, "Version 4.2.0")
    assert result.startswith("[1] (source: release-notes.md, section: Version 4.2.0)")


def test_section_equal_to_the_source_is_not_repeated() -> None:
    """An imported record's only heading is its title, which is also its name."""
    result = format_citation(1, "Text", "Returns", None, "Returns")
    assert result.startswith("[1] (source: Returns)")
