"""The engine's real grounded-answer prompt for an item, built from its frozen passages.

- The assistant is the C2 "Customer support" template (`generic_support`) with its defaults, the
  item's business name filled in; nothing else is changed.
- The system prompt and the constraints come from the engine's prompt compiler for a business
  question in the item's language (`compile_prompt`), with `today` pinned so the prompt (and its
  hash) is the same on every run.
- The user message is laid out exactly as the engine's `ContextAssembler` lays out retrieved
  chunks (content sanitizer, `format_citation`, CONTEXT / QUESTION / CONSTRAINTS);
  `tests/unit/test_answer_bench.py` checks it against the assembler itself.
"""

import re
import uuid
from datetime import date
from functools import cache
from typing import TYPE_CHECKING, Any

from benchmarks.answer_bench.items import Item

if TYPE_CHECKING:
    from callobuzz_rag.conversation.compiler import CompiledPrompt, CompileTurn
    from callobuzz_rag.conversation.schema import AssistantConfig
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.llm.models import LLMMessage

TEMPLATE = "generic_support"
PINNED_TODAY = date(2026, 10, 1)
_NS = uuid.UUID("6f1d3c52-61a8-4c1e-9d3b-0a5d2f1e7c11")


@cache
def assistant_config(business: str) -> "AssistantConfig":
    from callobuzz_rag.conversation.templates import load_template

    config = load_template(TEMPLATE).config
    identity = config.identity.model_copy(update={"business_name": business})
    return config.model_copy(update={"identity": identity}, deep=True)


def compile_turn(item: Item) -> "CompileTurn":
    from callobuzz_rag.conversation.compiler import CompileTurn, is_comparison
    from callobuzz_rag.conversation.types import Route, Script, TurnLabel

    return CompileTurn(
        label=TurnLabel.BUSINESS_QUESTION,
        route=Route.GROUNDED,
        language=item.language,
        script=Script.DEVANAGARI if item.language == "hi" else Script.LATIN,
        today=PINNED_TODAY,
        comparison=is_comparison(item.question),
    )


def compiled(item: Item) -> "CompiledPrompt":
    from callobuzz_rag.conversation.compiler import compile_prompt

    return compile_prompt(assistant_config(item.business), compile_turn(item))


_NONCE = re.compile(r'id="[0-9a-f]{16}"')


def fingerprint(text: str) -> str:
    """`text` with the content sanitizer's random boundary ids masked: the sanitizer draws a new
    id for every passage of every call (against marker spoofing), so the prompt a model sees
    is identical across models except for those ids, and a hash must ignore them."""
    return _NONCE.sub('id="*"', text)


def chunk_id(passage_id: str) -> uuid.UUID:
    return uuid.uuid5(_NS, passage_id)


def user_message(item: Item, settings: "Settings") -> "LLMMessage":
    """CONTEXT + QUESTION + CONSTRAINTS, as `ContextAssembler.assemble` builds it."""
    from callobuzz_rag.llm.models import LLMMessage
    from callobuzz_rag.retrieval.prompt_templates import (
        CONTEXT_HEADER,
        QUESTION_PREFIX,
        format_citation,
    )
    from callobuzz_rag.retrieval.safety import ContentSanitizer

    sanitizer = ContentSanitizer(settings)
    block = CONTEXT_HEADER + "\n"
    for n, p in enumerate(item.passages, start=1):
        text = sanitizer.sanitize(p.text, source_label=p.file_name)
        block += format_citation(n, text, p.file_name, p.page, p.section) + "\n\n"
    constraints = compiled(item).constraints
    return LLMMessage(
        role="user", content=f"{block}{QUESTION_PREFIX}{item.question}\n\n{constraints}"
    )


def sources(item: Item) -> list[dict[str, Any]]:
    """The cited chunks as the director's retrieval hands them to `grounded`."""
    return [
        {
            "index": n,
            "chunk_id": str(chunk_id(p.id)),
            "document_id": None,
            "external_id": None,
            "file_name": p.file_name,
            "section": p.section,
            "page": p.page,
            "text": p.text,
            "score": 1.0,
            "score_breakdown": None,
        }
        for n, p in enumerate(item.passages, start=1)
    ]
