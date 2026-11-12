"""The widget localises a title, greeting or placeholder only while it still equals the engine's
English default, so the copy in widget-core.js must equal `_DEFAULTS` (C9)."""

import json
import re
from pathlib import Path

from callobuzz_rag.api.routes.widget import _DEFAULTS

CORE = Path(__file__).parents[2] / "src/callobuzz_rag/widget/static/widget-core.js"
WIDGET = CORE.with_name("widget.js")
_STRING = r'"(?:[^"\\]|\\.)*"'


def _defaults_en() -> dict[str, str]:
    block = re.search(r"var DEFAULTS_EN = \{(.*?)\n  \};", CORE.read_text("utf-8"), re.DOTALL)
    assert block is not None
    pairs = re.findall(rf"(\w+):\s*({_STRING})", block.group(1))
    return {key: json.loads(value) for key, value in pairs}


def test_core_defaults_equal_the_engine_defaults() -> None:
    found = _defaults_en()
    assert set(found) == {"title", "greeting", "placeholder"}
    for key, value in found.items():
        assert value == _DEFAULTS[key], key


def test_the_widgets_own_fallbacks_equal_them_too() -> None:
    cfg = re.search(r"var cfg = \{(.*?)\n  \};", WIDGET.read_text("utf-8"), re.DOTALL)
    assert cfg is not None
    for key in ("title", "greeting", "placeholder"):
        value = re.search(rf"\n    {key}: ({_STRING}),", cfg.group(1))
        assert value is not None and json.loads(value.group(1)) == _DEFAULTS[key], key
