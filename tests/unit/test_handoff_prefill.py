"""TV2: the WhatsApp prefill (rules only)."""

from callobuzz_rag.conversation.handoff.channels import prefill_text, whatsapp_url
from callobuzz_rag.conversation.handoff.prefill import whatsapp_prefill


def test_prefill_lists_the_last_three_questions_behind_the_reference() -> None:
    qs = ["Do you do root canals?", "Root canal ka kitna charge hai?", "Sunday open?", "Parking?"]
    assert whatsapp_prefill("SD-4F7K", qs, "en") == (
        "Ref SD-4F7K — Root canal ka kitna charge hai? · Sunday open? · Parking?"
    )


def test_at_most_300_characters_on_a_word_boundary() -> None:
    text = whatsapp_prefill("SD-4F7K", ["word " * 200], "en")
    assert len(text) <= 300
    assert not text.endswith(" ")
    assert text.startswith("Ref SD-4F7K — ")


def test_language_lead_in() -> None:
    assert whatsapp_prefill("SD-4F7K", ["दाँत दर्द"], "hi").startswith("संदर्भ SD-4F7K — ")
    assert whatsapp_prefill("SD-4F7K", ["dard"], "hi-Latn").startswith("Ref SD-4F7K — ")


def test_no_reference_falls_back_to_the_old_prefill() -> None:
    assert whatsapp_prefill(None, ["Sunday open?"], "en") == prefill_text("Sunday open?", None)


def test_reference_without_questions_is_just_the_reference() -> None:
    assert whatsapp_prefill("SD-4F7K", [], "en") == "Ref SD-4F7K"


def test_whatsapp_url_encodes_the_dash_and_devanagari() -> None:
    url = whatsapp_url("+919822012345", whatsapp_prefill("SD-4F7K", ["दाँत दर्द"], "hi"))
    assert url.startswith("https://wa.me/919822012345?text=")
    assert " " not in url


# review fixes: the newest question is the one worth keeping
def test_a_long_last_question_is_kept_in_shortened_form() -> None:
    text = whatsapp_prefill("SD-4F7K", ["Do you open Sunday?", "word " * 100], "en")
    assert len(text) <= 300
    assert text.startswith("Ref SD-4F7K — ")
    assert text.rstrip("…").rstrip().endswith("word")
    assert "word word" in text


def test_the_text_never_ends_on_a_separator() -> None:
    for qs in (["Do you open Sunday?", "y" * 400], ["x" * 400], ["a " * 200, "b " * 200]):
        text = whatsapp_prefill("SD-4F7K", qs, "en")
        assert len(text) <= 300
        assert not text.endswith(("·", "—", " "))


def test_when_three_overflow_the_oldest_goes_first() -> None:
    qs = ["oldest " + "a" * 150, "middle " + "b" * 130, "newest " + "c" * 130]
    text = whatsapp_prefill("SD-4F7K", qs, "en")
    assert len(text) <= 300
    assert "newest" in text and "middle" in text
    assert "oldest" not in text
    assert text.index("middle") < text.index("newest")
