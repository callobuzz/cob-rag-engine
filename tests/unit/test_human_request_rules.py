"""One rule for "a person, please" (TV1 task 1)."""

import json
import re
from pathlib import Path

import pytest

from callobuzz_rag.conversation.human import HUMAN_ASK, is_human_request

STATIC = Path(__file__).parents[2] / "src" / "callobuzz_rag" / "widget" / "static"

YES = {
    "en": [
        "talk to a person",
        "Can I speak to someone?",
        "agent",
        "human please",
        "call me",
        "call me back pls",
        "I want to talk to the doctor",
        "connect me with an executive",
        "Is there someone I can talk to?",
        "real person",
    ],
    "hi": ["किसी इंसान से बात करनी है", "मैनेजर से बात कराइए", "मुझे कॉल कीजिए"],
    "hi-Latn": [
        "insaan se baat",
        "kisi se baat karni hai",
        "kisi insaan se baat karao",
        "manager se baat karni hai",
        "call back kariye",
        "mujhe call karo",
        "agent chahiye",
    ],
}
NO = [
    "what does a travel agent do?",
    "who is the doctor on Sunday?",
    "call timings?",
    "baat karte hain kal",
    "Is the person in the photo your dentist?",
    "hi",
    "ok",
    # negations and ordinary business questions (review TV1-2)
    "please don't call me",
    "do not call me",
    "mat call karo",
    "don't connect me to an agent, just answer",
    "i don't want to talk to a person",
    "manager se baat nahi karni",
    "they call me Ravi",
    "will you call me when the report is ready?",
    "I need an agent to sell my flat",
    "agent chahiye for my flat",
    "property manager chahiye",
    "I need a person to clean my house",
    "I need a human resources consultant",
    "Do you offer chat with a doctor online?",
]

STILL_YES = [
    "please call me on 9822012345",
    "can you call me",
    "hi, call me back",
    "I need an agent",
    "i need a person to talk to",
    "mujhe agent chahiye",
    "manager chahiye",
    "don't worry, talk to a person",
]


@pytest.mark.parametrize("lang,text", [(lang, t) for lang, ts in YES.items() for t in ts])
def test_requests_for_a_person(lang: str, text: str) -> None:
    assert is_human_request(text) is True


@pytest.mark.parametrize("text", NO)
def test_not_requests(text: str) -> None:
    assert is_human_request(text) is False


def test_abuse_wins() -> None:
    assert is_human_request("talk to a person you idiot") is False


def test_human_ask_strings_match_the_widget() -> None:
    js = (STATIC / "widget.js").read_text("utf-8")
    block = re.search(r"var HANDOFF_ASKS = \{(.*?)\};", js, re.S)
    assert block is not None
    in_js = re.findall(r':\s*"([^"]+)"', block.group(1))
    assert sorted(in_js) == sorted(HUMAN_ASK.values())
    for lang, text in HUMAN_ASK.items():
        assert json.dumps(text, ensure_ascii=False) in js, lang
        assert is_human_request(text), lang


@pytest.mark.parametrize("text", STILL_YES)
def test_imperative_and_anchored_forms_still_match(text: str) -> None:
    assert is_human_request(text) is True
