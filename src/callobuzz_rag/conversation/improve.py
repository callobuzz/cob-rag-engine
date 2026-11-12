"""'Improve this rule' (design 4.5; conversation C9).

One fast-model call rewrites the owner's rough text into one clean, single-objective rule and
names enabled rules it may conflict with. It returns a suggestion only; nothing is written.
`complete_json` errors (`LLMError`) propagate: the route turns them into a 502 and still
meters the spend.
"""

import json
import logging
from typing import Any

from callobuzz_rag.api.schemas.assistant import ImproveRuleResponse, RuleConflict
from callobuzz_rag.conversation.schema import GuidanceRule
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal

logger = logging.getLogger(__name__)

MAX_CONTEXT_RULES = 40
MAX_RULE_CHARS = 200

SYSTEM = (
    "You edit guidance rules for a business's website assistant. A good rule has ONE objective, "
    "is plain language, starts with an action (Always / Never / If ... then ...), is under 60 "
    "words, and never states business facts (prices, timings, phone numbers): those come from "
    "documents. Keep the owner's intent; do not add new obligations. Write the rule in the same "
    "language and script as the owner's text (English, Hindi or Hinglish). Reply with JSON only: "
    '{"improved": "<rule>", "conflicts": [{"rule_id": "<id>", "reason": "<why>"}], '
    '"notes": "<one line>"}'
)


def _context(others: list[GuidanceRule]) -> tuple[str, set[str]]:
    shown = [r for r in others if r.enabled][:MAX_CONTEXT_RULES]
    lines = [
        f"- {r.id} ({r.type}): {sanitize_for_prompt_literal(r.instruction[:MAX_RULE_CHARS])}"
        for r in shown
    ]
    return "\n".join(lines) or "(none)", {r.id for r in shown}


async def improve_rule(
    llm: Any, *, instruction: str, rule_type: str, others: list[GuidanceRule]
) -> ImproveRuleResponse:
    existing, known = _context(others)
    user = (
        f"Rule type: {rule_type}\n"
        f"Owner's text: {sanitize_for_prompt_literal(instruction)}\n"
        f"Other enabled rules:\n{existing}"
    )
    messages = [LLMMessage(role="system", content=SYSTEM), LLMMessage(role="user", content=user)]
    reply = await llm.complete_json(messages, **llm.fast_call_options())
    try:
        data = json.loads(reply.content)
        if not isinstance(data, dict):
            raise TypeError("not an object")
        improved = str(data.get("improved") or "").strip()[:500] or instruction
        conflicts = [
            RuleConflict(rule_id=str(c["rule_id"]), reason=str(c.get("reason", ""))[:300])
            for c in data.get("conflicts") or []
            if isinstance(c, dict) and str(c.get("rule_id")) in known
        ]
        return ImproveRuleResponse(
            improved=improved, conflicts=conflicts, notes=str(data.get("notes") or "")[:300]
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Improve-rule reply was not usable JSON: %s", exc)
        return ImproveRuleResponse(
            improved=instruction,
            conflicts=[],
            notes="No suggestion this time; your rule is unchanged.",
        )
