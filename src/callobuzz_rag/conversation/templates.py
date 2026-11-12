"""Business templates (design section 4 "Templates are JSON files"): data, not code.

Each file holds a full AssistantConfig plus ten test questions with the behaviour expected
(C7 imports them as a test set). Owners start from one and everything stays editable.
"""

import json
from functools import cache
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict

from callobuzz_rag.conversation.schema import AssistantConfig, TemplateKey
from callobuzz_rag.conversation.types import LangKey

TEMPLATE_KEYS: tuple[str, ...] = (
    "generic_support",
    "clinic",
    "store",
    "coaching",
    "saas",
    "real_estate",
    "government",
    "restaurant",
)


class TemplateTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    expected: Literal["answer", "decline", "handoff", "lead", "smalltalk", "no_answer"]
    language: LangKey = "en"


class Template(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: TemplateKey
    name: str
    description: str
    config: AssistantConfig
    tests: list[TemplateTest]


@cache
def load_template(key: str) -> Template:
    if key not in TEMPLATE_KEYS:
        raise KeyError(key)
    raw = (
        resources.files("callobuzz_rag.conversation")
        .joinpath(f"templates/{key}.json")
        .read_text("utf-8")
    )
    return Template.model_validate(json.loads(raw))


def list_templates() -> list[Template]:
    return [load_template(key) for key in TEMPLATE_KEYS]
