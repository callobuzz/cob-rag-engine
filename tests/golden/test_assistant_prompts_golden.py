"""Golden files for compile_prompt. Regenerate after an intended wording change with:

    UPDATE_GOLDEN=1 .venv/Scripts/python -m pytest tests/golden/test_assistant_prompts_golden.py

then read every changed file in the diff before committing.
"""

import os
from pathlib import Path

import pytest

from callobuzz_rag.conversation.compiler import CompileTurn, compile_prompt
from callobuzz_rag.conversation.templates import TEMPLATE_KEYS, load_template
from callobuzz_rag.conversation.types import OfficeState, Route, Script, TurnLabel

GOLDEN = Path(__file__).parent / "expected" / "assistant_prompts"

CASES: dict[str, tuple[str, CompileTurn]] = {
    **{key: (key, CompileTurn()) for key in TEMPLATE_KEYS},
    "clinic_hi": ("clinic", CompileTurn(language="hi", script=Script.DEVANAGARI)),
    "clinic_hinglish_buying": (
        "clinic",
        CompileTurn(language="hi-Latn", script=Script.LATIN, label=TurnLabel.BUYING_INTENT),
    ),
    "clinic_closed_prices": ("clinic", CompileTurn(office=OfficeState.CLOSED, topic_id="prices")),
    "store_lead_captured": (
        "store",
        CompileTurn(lead_captured=True, label=TurnLabel.BUYING_INTENT),
    ),
    "clinic_explainer": (
        "clinic",
        CompileTurn(label=TurnLabel.DOMAIN_EXPLAINER, route=Route.EXPLAINER),
    ),
}

_NAMES = {"clinic": "Shree Labs", "store": "Rangoli Sarees"}


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden(case: str) -> None:
    key, turn = CASES[case]
    config = load_template(key).config.model_copy(deep=True)
    config.identity.business_name = _NAMES.get(key, "Vidya Classes")
    out = compile_prompt(config, turn)
    text = (
        f"=== SYSTEM ===\n{out.system_prompt}\n=== CONSTRAINTS ===\n{out.constraints}\n"
        f"=== RULES FIRED ===\n{', '.join(out.rules_fired)}\n"
    )
    path = GOLDEN / f"{case}.txt"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    assert path.read_text(encoding="utf-8") == text
