# tests/unit/test_conv_rules.py
"""C1: the deterministic first pass — small talk, human request, abuse, buying intent."""

import time

import pytest

from callobuzz_rag.conversation.rules import normalize_message, rule_labeler
from callobuzz_rag.conversation.types import TurnLabel


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Hi!", "greeting"),
        ("hello there", "greeting"),
        ("Good morning sir", "greeting"),
        ("नमस्ते", "greeting"),
        ("namaste ji", "greeting"),
        ("Thank you so much!!", "thanks"),
        ("धन्यवाद", "thanks"),
        ("shukriya", "thanks"),
        ("Thanks, that helps", "thanks"),
        ("Thanks, that helps!", "thanks"),
        ("that was really helpful, thank you", "thanks"),
        ("Perfect, thanks", "thanks"),
        ("got it, thanks ji", "thanks"),
        ("ok, that answers my question", "thanks"),
        ("bahut madad mili, shukriya", "thanks"),
        ("samajh gaya, thanks", "thanks"),
        ("badhiya, dhanyavaad", "thanks"),
        ("यह मददगार था, धन्यवाद", "thanks"),
        ("समझ गया, बहुत बढ़िया", "thanks"),
        ("bye", "bye"),
        ("ok bye take care", "bye"),
        ("how are you?", "how_are_you"),
        ("kaise ho", "how_are_you"),
        ("आप कैसे हैं", "how_are_you"),
        ("who are you", "who_are_you"),
        ("aap kaun ho", "who_are_you"),
        ("are you a bot?", "are_you_bot"),
        ("kya tum robot ho", "are_you_bot"),
    ],
)
def test_smalltalk_full_message(text: str, intent: str) -> None:
    out = rule_labeler().classify(text)
    assert out.label == TurnLabel.SMALLTALK
    assert out.intent == intent


@pytest.mark.parametrize(
    "text",
    [
        "I want to talk to a human",
        "can I speak with someone from the lab?",
        "This is not helping, let me talk to a person",
        "kisi insaan se baat karni hai",
        "किसी व्यक्ति से बात करनी है",
        "agent se baat karao",
    ],
)
def test_human_request_searched(text: str) -> None:
    assert rule_labeler().classify(text).label == TurnLabel.HUMAN_REQUEST


@pytest.mark.parametrize(
    "text",
    [
        "I want to book an appointment",
        "how can I buy the full body checkup?",
        "admission lena hai",
        "मुझे बुक करना है",
        "order karna hai",
    ],
)
def test_buying_intent(text: str) -> None:
    assert rule_labeler().classify(text).label == TurnLabel.BUYING_INTENT


@pytest.mark.parametrize(
    "text",
    [
        "can I talk to the owner?",
        "I want to speak with your sales manager",
        "is there someone I can talk to?",
        "get me a real person",
        "manager sahab se baat karwao",
        "koi staff hai jisse baat ho sake?",
        "kisi real insaan se milao",
        "मालिक से बात करवाइए",
        "डॉक्टर जी से बात करनी है",
        "किसी असली इंसान से मिलवाइए",
    ],
)
def test_more_ways_to_ask_for_a_person(text: str) -> None:
    assert rule_labeler().classify(text).label == TurnLabel.HUMAN_REQUEST


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("is this an automated reply?", "are_you_bot"),
        ("ye automated chat hai kya", "are_you_bot"),
        ("क्या यह ऑटोमैटिक जवाब है", "are_you_bot"),
        ("good night", "bye"),
        ("shubh ratri ji", "bye"),
        ("शुभ रात्रि", "bye"),
    ],
)
def test_automated_chat_and_good_night_are_small_talk(text: str, intent: str) -> None:
    out = rule_labeler().classify(text)
    assert out.label == TurnLabel.SMALLTALK and out.intent == intent


@pytest.mark.parametrize(
    "text",
    [
        "I'll take the blue one",
        "could you book a table for four tonight?",
        "please reserve two seats for Saturday",
        "sign me up for the morning yoga class",
        "we would like to place an order for 30 kits",
        "keen to place a bulk order",
        "please share a quotation for 40 chairs",
        "I want to subscribe to the annual plan",
        "दो किलो ले लूँगा",
        "शनिवार की टेबल बुक कर दीजिए",
        "हम सौ टी-शर्ट खरीदना चाहते हैं",
        "पचास कुर्सियों का कोटेशन चाहिए",
        "agle mahine ke liye hall book karwa do",
        "ye wala le lunga",
        "40 chairs ka quotation chahiye",
        "hum yearly plan kharidna chahte hain",
        "ek cake order dena hai",
    ],
)
def test_explicit_purchase_phrases_are_buying_intent(text: str) -> None:
    assert rule_labeler().classify(text).label == TurnLabel.BUYING_INTENT


@pytest.mark.parametrize(
    "text",
    [
        "how much does a table for four cost?",
        "is the blue one available in medium?",
        "do I need to book in advance?",
        "how long does a quotation take?",
        "kya pehle se booking zaroori hai?",
        "क्या पहले से बुकिंग करनी पड़ती है?",
        "what is your refund policy for orders?",
    ],
)
def test_informational_price_and_availability_questions_are_left_to_the_model(text: str) -> None:
    assert rule_labeler().classify(text).label is None


@pytest.mark.parametrize(
    "text", ["ok", "Okay.", "hmmm", "yes", "nope", "ठीक है", "हाँ जी", "नहीं", "thik hai ji", "haan"]
)
def test_a_bare_acknowledgement_is_other_not_small_talk(text: str) -> None:
    out = rule_labeler().classify(text)
    assert out.label == TurnLabel.OTHER and out.intent == "acknowledgement"


@pytest.mark.parametrize(
    "text", ["ok thanks", "theek hai, dhanyavaad", "hmm, what are your timings?", "yes book it"]
)
def test_an_acknowledgement_with_more_is_not_the_bare_rule(text: str) -> None:
    assert rule_labeler().classify(text).label != TurnLabel.OTHER


def test_abuse_beats_everything() -> None:
    assert rule_labeler().classify("hi idiot").label == TurnLabel.ABUSE


@pytest.mark.parametrize(
    "text",
    [
        "please call me tomorrow",
        "can you call me back?",
        "contact me on whatsapp",
        "mujhe call karo",
        "call back kar do",
        "मुझे कॉल करें",
        "मुझसे संपर्क करें",
        "here is my number, call me",
    ],
)
def test_asked_for_contact(text: str) -> None:
    assert rule_labeler().asked_for_contact(text)


@pytest.mark.parametrize(
    "text", ["what are your timings", "how much is a CBC test", "call centre timings?"]
)
def test_not_asked_for_contact(text: str) -> None:
    assert not rule_labeler().asked_for_contact(text)


@pytest.mark.parametrize(
    "text",
    [
        "hi, what are your timings on Sunday?",
        "Do I need to fast for a lipid profile?",
        "thanks, and what is the price of HbA1c?",
        "Home collection kitne ka hai?",
        "What is a thyroid test?",
    ],
)
def test_questions_are_left_to_the_model(text: str) -> None:
    out = rule_labeler().classify(text)
    assert out.label is None


def test_greeting_prefix_is_stripped() -> None:
    out = rule_labeler().classify("Hi, what are your timings on Sunday?")
    assert out.label is None and out.greeted
    assert out.remainder == "what are your timings on sunday"


def test_normalisation() -> None:
    assert normalize_message("  Hello!!  Sir ") == "hello sir"
    assert normalize_message("नमस्ते।") == "नमस्ते"


def test_rules_are_fast() -> None:
    labeler = rule_labeler()
    msgs = ["hi", "what is the price of a CBC test at your Kothrud branch?"] * 500
    start = time.perf_counter()
    for m in msgs:
        labeler.classify(m)
    per_message_ms = (time.perf_counter() - start) * 1000 / len(msgs)
    assert per_message_ms < 5


@pytest.mark.parametrize(
    "text",
    [
        "thanks, that helps. what is the price of HbA1c?",
        "that helps but do you open on sunday",
        "perfect, how much is home collection?",
        "madad mili par report kab milegi",
        "i see that the price is high, can you reduce it?",
    ],
)
def test_an_acknowledgement_with_a_real_question_is_not_small_talk(text: str) -> None:
    assert rule_labeler().classify(text).label != TurnLabel.SMALLTALK
