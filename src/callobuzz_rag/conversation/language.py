"""Language and script of a visitor message, and the language to reply in (design section 4.3).

Rules only, no model: Unicode blocks decide the script; a Roman-Hindi lexicon separates
Hinglish from English; Marathi is told from Hindi by function words. Deterministic and
free; C1's label benchmark measures it on 240+ labelled turns.
"""

from functools import cache
from importlib import resources

from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.types import LangKey, LanguageInfo, Script, script_of
from callobuzz_rag.text.tokenize import tokens as word_tokens

_BLOCKS: tuple[tuple[LangKey, int, int], ...] = (
    ("bn", 0x0980, 0x09FF),
    ("pa", 0x0A00, 0x0A7F),
    ("gu", 0x0A80, 0x0AFF),
    ("ta", 0x0B80, 0x0BFF),
    ("te", 0x0C00, 0x0C7F),
    ("kn", 0x0C80, 0x0CFF),
    ("ml", 0x0D00, 0x0D7F),
)


@cache
def _words(name: str) -> frozenset[str]:
    text = (
        resources.files("callobuzz_rag.conversation")
        .joinpath(f"data/lang/{name}")
        .read_text("utf-8")
    )
    return frozenset(w.strip().lower() for w in text.split() if w.strip())


def _is_devanagari(ch: str) -> bool:
    return 0x0900 <= ord(ch) <= 0x097F


def detect_language(text: str) -> LanguageInfo:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return LanguageInfo("en", Script.LATIN, 0.0)
    total = len(letters)
    deva = sum(1 for ch in letters if _is_devanagari(ch))
    if deva / total >= 0.3:
        tokens = [t for t in word_tokens(text) if any(_is_devanagari(c) for c in t)]
        marathi = sum(1 for t in tokens if t in _words("marathi_markers.txt"))
        code: LangKey = "mr" if marathi >= 1 and marathi / max(len(tokens), 1) >= 0.2 else "hi"
        return LanguageInfo(code, Script.DEVANAGARI, round(deva / total, 2))
    for lang, lo, hi in _BLOCKS:
        share = sum(1 for ch in letters if lo <= ord(ch) <= hi) / total
        if share >= 0.3:
            return LanguageInfo(lang, Script.OTHER, round(share, 2))
    tokens = word_tokens(text.lower())
    hinglish = sum(1 for t in tokens if t in _words("hinglish_lexicon.txt"))
    english = sum(1 for t in tokens if t in _words("english_common.txt"))
    size = max(len(tokens), 1)
    if hinglish >= 2 or (hinglish >= 1 and hinglish >= english):
        return LanguageInfo("hi-Latn", Script.LATIN, round(min(1.0, 0.5 + hinglish / size), 2))
    return LanguageInfo("en", Script.LATIN, round(min(1.0, 0.5 + english / size), 2))


def reply_language(config: AssistantConfig, info: LanguageInfo) -> tuple[LangKey, Script]:
    """The language and script the assistant answers in (`language.reply_policy/script_policy`)."""
    policy = config.language
    primary = policy.primary
    if policy.reply_policy == "fixed" or not policy.auto_detect or info.confidence == 0.0:
        return primary, script_of(primary)
    code: LangKey = info.code if info.code in policy.supported else primary
    if code in ("hi", "hi-Latn"):
        if policy.script_policy == "native":
            code = "hi"
        elif policy.script_policy == "roman":
            code = "hi-Latn"
    return code, script_of(code)
