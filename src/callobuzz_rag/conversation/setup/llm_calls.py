"""How every guided-setup model call is made (propose, wording, document questions).

`fast_call_options()` caps `max_tokens` for a known non-reasoning fast model (a tight cap suits
a label, not a structured proposal with ten services and a week of opening hours). Setup replies
are longer, so the cap is raised to a floor for that case; a reasoning model gets no cap at all
(it spends its budget thinking, and a cap would truncate the JSON).
"""

from typing import Any

from callobuzz_rag.llm.base import LLMProvider

PROPOSE_TOKENS = 1500
WORDING_TOKENS = 2000
DOCUMENT_QUESTION_TOKENS = 800


def setup_call_options(llm: LLMProvider, floor: int) -> dict[str, Any]:
    """The fast model's call options, with `max_tokens` raised to `floor` when one is set."""
    options = dict(llm.fast_call_options())
    cap = options.get("max_tokens")
    if isinstance(cap, int) and cap < floor:
        options["max_tokens"] = floor
    return options
