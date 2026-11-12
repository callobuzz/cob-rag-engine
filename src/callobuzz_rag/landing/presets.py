"""Six starting examples for the home page, in English, Hindi and Hinglish (LP2).

A preset is a ready conversation (the hero's chat and its lead card) plus the industry's own
pain wall, language samples, WhatsApp chapters and FAQ. They are data (`data/presets/*.json`,
every text is `[english, hindi, hinglish]`), cost nothing to apply and are edited by the deployer
afterwards. Applying one is copying its parts into the draft; the editor does that, this module
only reads and validates them.
"""

import copy
import functools
import json
from dataclasses import dataclass
from importlib import resources
from typing import Any, Final

from callobuzz_rag.landing.schema import (
    LANGS,
    FaqSection,
    LandingContent,
    LanguagesSection,
    PainWallSection,
    ScriptedConversation,
    WhatsAppJourneySection,
    default_content,
    is_ltext,
)

PRESET_KEYS: Final = ("clinic", "store", "coaching", "real_estate", "salon", "services")
SECTION_KEYS: Final = ("pain_wall", "languages", "whatsapp_journey", "faq")
_SECTION_MODELS: Final[dict[str, Any]] = {
    "pain_wall": PainWallSection,
    "languages": LanguagesSection,
    "whatsapp_journey": WhatsAppJourneySection,
    "faq": FaqSection,
}


class PresetNotFoundError(KeyError):
    """No preset with that key."""


def _expand(value: Any, langs: tuple[str, ...]) -> Any:
    """`[en, hi, hi-Latn]` becomes `{lang: text}` for the wanted languages, recursively."""
    if isinstance(value, list) and len(value) == 3 and all(isinstance(v, str) for v in value):
        return {lang: value[LANGS.index(lang)] for lang in langs}
    if isinstance(value, list):
        return [_expand(v, langs) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, langs) for k, v in value.items()}
    return value


def _ltexts(data: Any) -> list[str]:
    if is_ltext(data):
        return list(data.values())
    if isinstance(data, dict):
        return [t for v in data.values() for t in _ltexts(v)]
    if isinstance(data, list):
        return [t for v in data for t in _ltexts(v)]
    return []


def _langs(lang: str) -> tuple[str, ...]:
    if lang == "all":
        return LANGS
    if lang not in LANGS:
        raise ValueError(f"unknown language {lang}")
    return (lang,)


@dataclass(frozen=True)
class PresetFile:
    key: str
    name: str
    raw: dict[str, Any]

    @property
    def languages(self) -> tuple[str, ...]:
        return LANGS

    def conversation(self, lang: str = "all") -> ScriptedConversation:
        data = _expand(copy.deepcopy(self.raw["conversation"]), _langs(lang))
        return ScriptedConversation.model_validate({**data, "preset": self.key})

    def sections(self, lang: str = "all") -> dict[str, dict[str, Any]]:
        """The four industry sections as validated field dictionaries (no `id` or `type`)."""
        out: dict[str, dict[str, Any]] = {}
        for key in SECTION_KEYS:
            data = _expand(copy.deepcopy(self.raw[key]), _langs(lang))
            model = _SECTION_MODELS[key].model_validate({"id": key.replace("_", "-"), **data})
            dumped = model.model_dump(mode="json", exclude={"id", "type", "enabled"})
            out[key] = {k: v for k, v in dumped.items() if v not in (None, [], {})}
        return out

    def texts(self, lang: str) -> list[str]:
        """Every text a visitor would read in that language (for the tests)."""
        parts = [self.conversation(lang).model_dump(mode="json"), self.sections(lang)]
        return [t for part in parts for t in _ltexts(part)]


@functools.cache
def load_preset(key: str) -> PresetFile:
    if key not in PRESET_KEYS:
        raise PresetNotFoundError(key)
    text = (
        resources.files("callobuzz_rag.landing")
        .joinpath(f"data/presets/{key}.json")
        .read_text(encoding="utf-8")
    )
    raw = json.loads(text)
    return PresetFile(key=raw["key"], name=raw["name"], raw=raw)


def all_presets() -> list[PresetFile]:
    return [load_preset(key) for key in PRESET_KEYS]


def preset_site(key: str) -> LandingContent:
    """The shipped default with the preset's industry parts in all three languages.

    Used by the snapshot renderer (`--content preset:<key>`) so screenshots of every industry
    can be looked at without a database.
    """
    preset = load_preset(key)
    data = default_content().model_dump(mode="json")
    data["languages"] = list(LANGS)
    sections = preset.sections("all")
    for section in data["home"]:
        kind = section["type"]
        if kind == "hero":
            section["conversation"] = preset.conversation("all").model_dump(mode="json")
        elif kind in sections:
            section.update(sections[kind])
    return LandingContent.model_validate(data)
