"""A visitor asking for a person (TV1). Pure: the rules data only, never a model.

Every answering surface calls `is_human_request` before the question cap, the billing pause,
the fair-use gates and the lane slot, so that a request to reach a person is answered in every
state. The abuse rule still wins: an abusive message is not a free pass.
"""

from typing import Final

from callobuzz_rag.conversation.rules import rule_labeler
from callobuzz_rag.conversation.types import TurnLabel

# What the widget's "Talk to a person" button sends (kept equal to `HANDOFF_ASKS` in widget.js,
# pinned by tests/unit/test_human_request_rules.py).
HUMAN_ASK: Final[dict[str, str]] = {
    "en": "I'd like to talk to a person",
    "hi": "मुझे किसी व्यक्ति से बात करनी है",
    "hi-Latn": "Mujhe kisi insaan se baat karni hai",
}


def is_human_request(text: str) -> bool:
    return rule_labeler().classify(text).label is TurnLabel.HUMAN_REQUEST
