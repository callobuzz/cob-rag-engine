"""The three built-in pipeline profiles (PIP1).

One source for the migration (`phase16_001`, which copies these literals so it never imports
application code) and the tests (`test_tiers_schema.py` asserts the two agree and that each
config validates as a `ProfileConfig`). Plain dicts on purpose: no import of `schema.py` here.

- Free ("Economy"): answers with the local model, no reranker, no rewrites, short answers, no
  AI follow-up chips, low priority, two answers at once per business. Its values replace each
  knowledge base's own, so a business cannot keep an expensive model on a cheap tier.
- Standard: every field follows the knowledge base and the engine. A no-op overlay, so answers
  are exactly what they were before profiles existed. It is the default profile.
- Premium: the LLM reranker and the high-priority lane; everything else follows. The deployer
  picks the best models for it in the profile editor.
"""

from typing import Any, Final

FREE_PLAN_NAME: Final = "Free"
FREE_PLAN_WEBSITE_PAGES: Final = 200

SEEDS: Final[dict[str, dict[str, Any]]] = {
    "free": {
        "name": "Free",
        "label": "Economy",
        "description": (
            "Answers with the free local model: slower, but it costs nothing to run. "
            "Used by the Free plan and as the fallback when money or fair use runs out."
        ),
        "is_default": False,
        "config": {
            "answer_route": "local",
            "reranker": "off",
            "multi_query_count": 0,
            "hyde_enabled": False,
            "query_classification_enabled": False,
            "max_answer_tokens": 600,
            "followups": False,
            "priority": "low",
            "max_concurrency": 2,
            "overrides_kb_settings": True,
        },
    },
    "standard": {
        "name": "Standard",
        "label": "Standard",
        "description": (
            "Follows each knowledge base's own settings and the engine's defaults. "
            "The default for every plan that does not pick another profile."
        ),
        "is_default": True,
        "config": {},
    },
    "premium": {
        "name": "Premium",
        "label": "Premium",
        "description": (
            "Reranks with the AI model and answers ahead of the other profiles when the "
            "engine is busy."
        ),
        "is_default": False,
        "config": {"reranker": "llm", "priority": "high"},
    },
}
