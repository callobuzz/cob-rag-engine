"""Golden pairs from a template's test questions (C2 templates/*.json, `tests`)."""

from callobuzz_rag.api.schemas.evals import GoldenPairFields
from callobuzz_rag.conversation.templates import load_template


def template_pairs(key: str) -> list[GoldenPairFields]:
    """The template's test questions as behaviour-only golden pairs. KeyError: unknown key."""
    template = load_template(key)
    return [
        GoldenPairFields(
            query=t.question,
            expected_behaviour=t.expected,
            language=t.language,
            origin="human",
        )
        for t in template.tests
    ]
