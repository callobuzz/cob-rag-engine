"""Prompt templates and constants for context assembly."""

SYSTEM_PROMPT = (
    "You are a knowledgeable assistant. Answer the user's question using ONLY "
    "the provided context. If the context doesn't contain enough information, "
    "say so. Always cite your sources using [N] notation."
)

CONSTRAINTS_BLOCK = (
    "CONSTRAINTS:\n"
    "- Only use information from the provided context\n"
    "- Cite sources using [N] notation\n"
    '- If unsure, say "I don\'t have enough information to answer this"'
)

CONTEXT_HEADER = "CONTEXT:"

QUESTION_PREFIX = "QUESTION: "


def format_citation(
    index: int,
    chunk_text: str,
    file_name: str | None,
    page: int | None,
    section: str | None = None,
    updated: str | None = None,  # TV5 freshness: "YYYY-MM-DD"; None keeps the output unchanged
) -> str:
    """Format a single chunk with its citation anchor and source metadata.

    The section is the chunk's heading path. A chunk cut from mid-section often
    never names what it is about ("Released on 14 March. Adds offline checkout")
    — the heading ("Version 4.2.0") is what lets the model answer "which release".

    Returns:
        Formatted string like:
        [1] (source: report.pdf, section: Results > Revenue, page: 3)
        The quarterly revenue increased by 15%...
    """
    parts: list[str] = []
    if file_name:
        parts.append(f"source: {file_name}")
    if section and section != file_name:
        parts.append(f"section: {section}")
    if page is not None:
        parts.append(f"page: {page}")
    if updated:  # TV5 freshness
        parts.append(f"updated: {updated}")

    meta = f" ({', '.join(parts)})" if parts else ""
    return f"[{index}]{meta}\n{chunk_text}"
