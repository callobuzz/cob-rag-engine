"""LP4: the editor's font cards name the same six pairings the engine serves."""

import re
from pathlib import Path

from callobuzz_rag.landing.fonts import FAMILIES, PAIRINGS

SOURCE = Path("services/dashboard/src/features/landing/fontPairings.ts")


def test_the_editor_lists_exactly_the_engines_pairings() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    keys = re.findall(r'key: "([a-z]+)"', text)
    assert keys == list(PAIRINGS)


def test_each_card_names_the_families_of_its_pairing() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    for key, pairing in PAIRINGS.items():
        card = re.search(rf'key: "{key}".*?families: "([^"]+)"', text)
        assert card is not None, key
        named = {FAMILIES[pairing.display].css_name, FAMILIES[pairing.body].css_name}
        if pairing.mono:
            named = {FAMILIES[pairing.display].css_name, FAMILIES[pairing.mono].css_name}
        assert all(name in card.group(1) for name in named), (key, card.group(1))
