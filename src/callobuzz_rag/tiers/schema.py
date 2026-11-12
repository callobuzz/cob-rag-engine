"""What a pipeline profile holds (PIP1).

A profile bundles how a business's questions are answered. Every `None` means "follow" (the
knowledge base's own setting, then the engine's), so the seeded Standard profile is a no-op
overlay and no answer changes until the deployer edits a profile or assigns one (D2).

Validation messages are written for a deployer, not a developer: they are shown as the
`detail` of a 422 `invalid_profile`.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import Field, field_validator, model_validator

from callobuzz_rag.core.schemas import StrictRequest
from callobuzz_rag.llm.language_routing import clean_language_models
from callobuzz_rag.llm.thinking import ThinkingLevel

Priority = Literal["high", "normal", "low"]
# engine = RAG_LLM_PROVIDER / RAG_LLM_API_BASE; local = the free local model (PIP2).
AnswerRoute = Literal["engine", "local"]
RerankChoice = Literal["inherit", "off", "local", "llm", "api"]
Thinking = ThinkingLevel
ProfileSource = Literal["exempt", "override", "plan", "default", "downgrade", "builtin_fallback"]

PRIORITIES: Final[tuple[str, ...]] = ("high", "normal", "low")


class ProfileConfig(StrictRequest):
    answer_route: AnswerRoute = "engine"
    # None: RAG_LLM_MODEL (engine route) / RAG_LOCAL_LLM_MODEL (local route).
    answer_model: str | None = Field(None, min_length=1, max_length=200)
    thinking: Thinking | None = None  # None: follow (knowledge base, then engine)
    # None: follow; {}: no routing; keys are language codes (llm/language_routing).
    language_models: dict[str, str] | None = None
    fast_model: str | None = Field(None, max_length=200)
    fast_thinking: Thinking | None = None
    reranker: RerankChoice = "inherit"
    # llm: the ranking model (None: the knowledge base's, else the engine's `rerank_llm_model`);
    # api: the LiteLLM rerank id ("cohere/rerank-v3.5"), required.
    rerank_model: str | None = Field(None, max_length=200)
    top_k: int | None = Field(None, ge=1, le=100)
    final_k: int | None = Field(None, ge=1, le=30)
    multi_query_count: int | None = Field(None, ge=0, le=5)  # 0 = rewrites off
    hyde_enabled: bool | None = None
    query_classification_enabled: bool | None = None
    max_answer_tokens: int | None = Field(None, ge=64, le=8192)  # a cap, never a raise
    followups: bool = True  # False turns AI follow-up chips off (never on)
    priority: Priority = "normal"
    max_concurrency: int | None = Field(None, ge=1, le=200)  # answers of ONE business at once
    overrides_kb_settings: bool = False  # True: profile values beat the knowledge base's own

    @field_validator("language_models", mode="before")
    @classmethod
    def _language_models(cls, value: Any) -> Any:
        if value is None:
            return None
        try:
            return clean_language_models(value)
        except ValueError as exc:
            raise ValueError(f"The language map is not valid: {exc}") from exc

    @model_validator(mode="after")
    def _fits_together(self) -> "ProfileConfig":
        if self.answer_route == "local" and not self.overrides_kb_settings:
            raise ValueError(
                "A profile that uses the local model must also set "
                "'Use these settings instead of each knowledge base's own'."
            )
        if self.reranker == "api" and not self.rerank_model:
            raise ValueError(
                "A profile that reranks through a reranking service needs a rerank_model "
                "such as 'cohere/rerank-v3.5'."
            )
        if self.top_k is not None and self.final_k is not None and self.final_k > self.top_k:
            raise ValueError(
                "final_k (how many passages reach the answer) cannot be larger than top_k "
                "(how many the search pulls in)."
            )
        return self


def rule_text(exc: Exception) -> str:
    """The first human sentence of a pydantic ValidationError, for `invalid_profile`."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        for err in errors():
            msg = str(err.get("msg", ""))
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, ") :]
            loc = ".".join(str(p) for p in err.get("loc", ()))
            if err.get("type") == "extra_forbidden":
                return f"'{loc}' is not a setting a profile has (extra field)."
            return f"{loc}: {msg}" if loc else msg
    return str(exc)


@dataclass(frozen=True, slots=True)
class EffectiveProfile:
    id: uuid.UUID | None
    name: str  # "Free", "Standard", "Premium", or the deployer's
    label: str  # what owners see ("Economy", "Standard", "Premium")
    config: ProfileConfig
    source: ProfileSource
    available: bool  # False: answer_route=local and the local model is not ready (PIP2)
    # The business answers with its own AI key: its turn never touches the local model, so it
    # takes no slot of the local pool (set by the fair-use gate, which knows the payer).
    own_key: bool = False
    # P5 plans: whether this business may use its own AI key (`billing/byok.py` decides; filled
    # by the same query that finds the profile). Carried through every `replace(...)`, so a
    # downgrade keeps it. Default True: code that has no business to look up changes nothing.
    byok_allowed: bool = True
    # /P5 plans
