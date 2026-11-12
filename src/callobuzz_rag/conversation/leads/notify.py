"""What the business receives about a new lead (design section 7).

Pure builders: the worker task (`worker/conversation_tasks.py`) decrypts the lead, calls these
and sends. Nothing here logs contact data or the transcript.
"""

import logging
from typing import Any

from callobuzz_rag.llm.models import LLMMessage

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = (
    "Summarise this website chat for the business's team in 2 or 3 short sentences: what the "
    "visitor asked, what they want next, and any time or place they mentioned. Use only what "
    "the visitor wrote. No greeting, no bullet points. Write in English."
)


def lead_dashboard_url(dashboard_url: str, lead: Any) -> str:
    return f"{dashboard_url.rstrip('/')}/leads?kb={lead.corpus_id}&lead={lead.id}"


async def summarize(llm: Any, transcript: list[dict[str, str]], fallback: str) -> str:
    """Two or three sentences for the team. Falls back to the visitor's first question when
    there is no key, no transcript or the model fails; the lead is never held back."""
    if not transcript or not getattr(llm, "has_credentials", False):
        return fallback[:500]
    lines = "\n".join(f"{t['role']}: {t['content']}" for t in transcript[-12:])
    try:
        reply = await llm.complete(
            [
                LLMMessage(role="system", content=SUMMARY_PROMPT),
                LLMMessage(role="user", content=lines),
            ],
            **llm.fast_call_options(),
        )
        text = " ".join(reply.content.split())
        return text[:600] or fallback[:500]
    except Exception:
        logger.warning("Lead summary failed; using the visitor's first question", exc_info=True)
        return fallback[:500]


def webhook_payload(
    lead: Any,
    fields: dict[str, Any],
    corpus_name: str,
    summary: str,
    transcript_url: str,
    sources: list[str],
    ref_code: str | None = None,  # TV2 reference
) -> dict[str, Any]:
    return {
        "lead_id": str(lead.id),
        "corpus_id": str(lead.corpus_id),
        "knowledge_base": corpus_name,
        "tenant_id": str(lead.tenant_id),
        "created_at": lead.created_at.isoformat(),
        "fields": fields,
        "language": lead.language,
        "page_url": lead.page_url,
        "utm": lead.utm,
        "trigger": lead.trigger,
        "channel": lead.channel,  # TV6 channel
        "tags": list(lead.tags or []),
        "callback_window": lead.callback_window,
        "qualification": lead.qualification,
        "summary": summary,
        "transcript_url": transcript_url,
        "sources": sources,
        "ref_code": ref_code,  # TV2 reference
    }


def email_context(
    lead: Any,
    fields: dict[str, Any],
    corpus_name: str,
    business_name: str,
    summary: str,
    lead_url: str,
    labels: dict[str, str],
    ref_code: str | None = None,  # TV2 reference
) -> dict[str, Any]:
    return {
        "kb_name": corpus_name,
        "business_name": business_name,
        "name": fields.get("name") or "",
        "phone": fields.get("phone") or "",
        "email": fields.get("email") or "",
        "custom": [
            {"label": labels.get(k, k), "value": str(v)}
            for k, v in (fields.get("custom") or {}).items()
        ],
        "summary": summary,
        "language": lead.language or "",
        "page_url": lead.page_url or "",
        "trigger": lead.trigger,
        "sources": list(lead.sources_consulted or []),
        "lead_url": lead_url,
        "ref_code": ref_code or "",  # TV2 reference
    }
