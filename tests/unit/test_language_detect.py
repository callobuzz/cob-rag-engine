"""Language and script detection (English, Hindi, Hinglish first-class) and reply policy."""

import pytest

from callobuzz_rag.conversation.language import detect_language, reply_language
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.types import LanguageInfo, Script


@pytest.mark.parametrize(
    ("text", "code", "script"),
    [
        ("Do I need to fast before a lipid profile test?", "en", Script.LATIN),
        ("What are your timings on Sunday?", "en", Script.LATIN),
        ("क्या मुझे टेस्ट से पहले उपवास करना है?", "hi", Script.DEVANAGARI),
        ("मुझे किसी व्यक्ति से बात करनी है", "hi", Script.DEVANAGARI),
        ("Kya mujhe test se pehle fasting karni hai?", "hi-Latn", Script.LATIN),
        ("Home collection Kothrud mein hota hai kya", "hi-Latn", Script.LATIN),
        ("bhai price kitna hai", "hi-Latn", Script.LATIN),
        ("मला उद्या रिपोर्ट मिळेल का?", "mr", Script.DEVANAGARI),
        ("உங்கள் நேரம் என்ன?", "ta", Script.OTHER),
        ("మీ సమయాలు ఏమిటి?", "te", Script.OTHER),
        ("আপনাদের সময় কী?", "bn", Script.OTHER),
    ],
)
def test_detect(text: str, code: str, script: Script) -> None:
    info = detect_language(text)
    assert (info.code, info.script) == (code, script)
    assert 0.0 < info.confidence <= 1.0


@pytest.mark.parametrize(
    "text",
    [
        "What is the price of this test?",
        "Is the clinic open on Sunday?",
        "Can you tell us the fees for the course?",
        "Is there a discount for us if we book three tests?",
        "What is the refund policy of the store?",
        "Do you deliver to us in the evening?",
        "Is the report ready by the evening?",
        "How can the team reach us on WhatsApp?",
        "Tell us what the demo includes.",
        "Is the batch for class 10 the same as class 9?",
        "The price is too high, is there a cheaper plan?",
        "Where is the nearest branch to us?",
    ],
)
def test_ordinary_english_with_is_the_us_stays_english(text: str) -> None:
    assert detect_language(text).code == "en"


def test_no_letters_is_unknown_english_with_zero_confidence() -> None:
    assert detect_language("12345 ?!") == LanguageInfo("en", Script.LATIN, 0.0)


def test_mixed_script_devanagari_with_english_words() -> None:
    assert detect_language("मेरा CBC test कब तक ready होगा?").code == "hi"


def test_hindi_with_ka_is_not_marathi() -> None:
    assert detect_language("रिपोर्ट का समय क्या है?").code == "hi"


def test_reply_mirror() -> None:
    c = AssistantConfig()
    assert reply_language(c, detect_language("kitne baje khulta hai?")) == ("hi-Latn", Script.LATIN)
    assert reply_language(c, detect_language("कितने बजे खुलता है?")) == ("hi", Script.DEVANAGARI)
    # Tamil is not in `supported` -> primary
    assert reply_language(c, detect_language("உங்கள் நேரம் என்ன?")) == ("en", Script.LATIN)


def test_reply_fixed_and_script_policies() -> None:
    fixed = AssistantConfig.model_validate({"language": {"reply_policy": "fixed", "primary": "hi"}})
    assert reply_language(fixed, detect_language("What time?")) == ("hi", Script.DEVANAGARI)
    native = AssistantConfig.model_validate({"language": {"script_policy": "native"}})
    assert reply_language(native, detect_language("kitne baje khulta hai?")) == (
        "hi",
        Script.DEVANAGARI,
    )
    roman = AssistantConfig.model_validate({"language": {"script_policy": "roman"}})
    assert reply_language(roman, detect_language("कितने बजे खुलता है?")) == ("hi-Latn", Script.LATIN)


def test_low_confidence_uses_primary() -> None:
    c = AssistantConfig.model_validate(
        {"language": {"primary": "hi-Latn", "supported": ["hi-Latn", "en"]}}
    )
    assert reply_language(c, LanguageInfo("en", Script.LATIN, 0.0)) == ("hi-Latn", Script.LATIN)
