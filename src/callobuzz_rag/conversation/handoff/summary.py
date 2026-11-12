"""The 2-3 line summary the business receives with a handoff (design 7).

One summariser for leads and handoffs (README pinned P14): a thin wrapper over C4's
`conversation.leads.notify.summarize(llm, transcript, fallback)`, which writes English for the
business with the fast model and returns the fallback line without a credential or on any
failure, so a notification never waits on a model.
"""

from callobuzz_rag.conversation.handoff.triggers import reason_label
from callobuzz_rag.conversation.leads.notify import summarize
from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal

_FALLBACK_QUESTIONS = 3  # TV2 reference: the visitor's last few questions


def _short(text: str) -> str:
    q = " ".join(text.split())
    return q[:200].rstrip() + "…" if len(q) > 200 else q


def fallback_summary(questions: str | list[str], reason: str) -> str:
    """The plain line used with no model: the visitor's question, or (TV2) their last three."""
    items = [questions] if isinstance(questions, str) else list(questions)
    shown = " · ".join(_short(q) for q in items[-_FALLBACK_QUESTIONS:] if q.strip())
    return f"Visitor asked: {shown} Reason: {reason_label(reason)}."


async def summarize_handoff(
    llm: LLMProvider | None,
    transcript: list[tuple[str, str]],
    *,
    reason: str,
    language: str | None,
    question: str | list[str],
) -> str:
    """`language` is the visitor's (shown separately in the e-mail); the summary is English."""
    fallback = fallback_summary(question, reason)
    if llm is None:
        return fallback
    turns = [
        {"role": role, "content": sanitize_for_prompt_literal(text)[:600]}
        for role, text in transcript
    ]
    return await summarize(llm, turns, fallback)
