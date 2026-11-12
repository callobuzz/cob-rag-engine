"""The COB eval set: one JSON object per line in `docs/cob/eval/cob-eval.jsonl`.

An item is a question a real person would type, the role that types it and what a right
answer looks like (which help page, which link, which tool, what must never appear). The set is
data, reviewed like code; the runners in this package only read it.
"""

from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from callobuzz_rag.cob.types import CobRole

EVAL_PATH: Final = Path("docs") / "cob" / "eval" / "cob-eval.jsonl"

Behaviour = Literal[
    "answer",
    "decline",
    "no_answer",
    "admin_managed",
    "sign_in_hint",
    "smalltalk",
    "card",
    "proposal",
]
Intent = Literal["docs", "account", "action", "mixed", "smalltalk"]
Routing = Literal["rules", "model", "none"]
Language = Literal["en", "hi", "hi-Latn"]
NEGATIVE_BEHAVIOURS: Final = frozenset({"admin_managed", "sign_in_hint"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Expect(_Strict):
    behaviour: Behaviour
    intent: Intent = "docs"
    tool: str | None = None
    # The help page (and section) the answer comes from. For a negative item: the page that
    # must NOT be reachable for the role.
    slug: str | None = None
    anchor: str | None = None
    link_route: str | None = None
    must_not_include: tuple[str, ...] = ()


class EvalItem(_Strict):
    id: str = Field(min_length=3, max_length=64)
    role: Literal["admin", "operator_owner", "owner", "member", "public"]
    language: Language = "en"
    question: str = Field(min_length=2, max_length=2000)
    # English rendering of a Hindi or Hinglish question. The live path translates the query
    # with the fast model; the offline gate has no model, so it uses this.
    translation_en: str | None = None
    expect: Expect
    routing: Routing = "model"
    tags: tuple[str, ...] = ()

    @property
    def cob_role(self) -> CobRole:
        return CobRole(self.role)

    @property
    def is_negative(self) -> bool:
        return self.expect.behaviour in NEGATIVE_BEHAVIOURS

    @property
    def is_indic(self) -> bool:
        return self.language != "en"

    @property
    def search_text(self) -> str:
        """What the offline retrieval searches for."""
        return self.translation_en or self.question


def load_eval_items(root: Path | None = None) -> list[EvalItem]:
    """The eval set shipped with the docs (`docs/cob/eval/cob-eval.jsonl` under `root`)."""
    from callobuzz_rag.cob.manifest import docs_root

    return load_items((root or docs_root()) / EVAL_PATH)


def load_items(path: Path) -> list[EvalItem]:
    """Every item of the file, validated; blank lines are skipped."""
    items: list[EvalItem] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            items.append(EvalItem.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"{path.name} line {number}: {exc}") from exc
    return items
