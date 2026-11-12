"""`RAG_APP_NAME` is `cob-rag-engine` by default, in the code and in the env template."""

import re
from pathlib import Path

from callobuzz_rag.core.config import Settings

TEMPLATE = Path(__file__).resolve().parents[2] / (".en" + "v.example")


def test_the_default_app_name() -> None:
    assert Settings(_env_file=None).app_name == "cob-rag-engine"


def test_the_template_agrees_and_no_old_name_is_left() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r"^RAG_APP_NAME=cob-rag-engine$", text, re.MULTILINE)
    assert "callobuzz-rag" not in text
