"""Assistant API models (C2) and the reply envelope fields every answering surface adds (C1)."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from callobuzz_rag.conversation.schema import AssistantConfig, QuickReply, TemplateKey
from callobuzz_rag.conversation.templates import TemplateTest
from callobuzz_rag.conversation.types import LangKey, Route, Script, TurnLabel
from callobuzz_rag.core.schemas import StrictRequest

__all__ = ["QuickReply"]


class _Strict(StrictRequest):
    """Request bodies and envelope blocks refuse fields they do not declare."""


class PutDraftRequest(_Strict):
    config: AssistantConfig
    base_revision: int | None = Field(
        None, ge=0, description="The draft_revision you edited; null or 0 when no draft exists."
    )


class PatchDraftRequest(_Strict):
    patch: dict[str, Any] = Field(
        ...,
        description="RFC 7396 JSON merge patch applied to the draft (or the published config).",
    )
    base_revision: int | None = Field(None, ge=0)


class FromTemplateRequest(_Strict):
    template: TemplateKey
    keep_identity: bool = Field(True, description="Keep the business name, card and expert text.")
    base_revision: int | None = Field(None, ge=0)


class PublishRequest(_Strict):
    note: str | None = Field(None, max_length=200)
    base_revision: int = Field(..., ge=0)


class SetEnabledRequest(_Strict):
    enabled: bool


class CompileSample(_Strict):
    label: TurnLabel = TurnLabel.BUSINESS_QUESTION
    route: Route = Route.GROUNDED
    language: LangKey = "en"
    script: Script | None = None
    page_url: str | None = Field(None, max_length=2048)
    office_hours: Literal["open", "closed", "unknown"] = "unknown"
    topic: str | None = Field(None, max_length=40)
    lead_captured: bool = False
    one_off_instructions: str | None = Field(None, max_length=4000)


class CompileRequest(_Strict):
    config: AssistantConfig | None = Field(
        None, description="Compile this config instead of the draft."
    )
    sample: CompileSample = Field(default_factory=CompileSample)


class AssistantVersionResponse(BaseModel):
    id: UUID
    corpus_id: UUID
    version: int | None
    status: Literal["draft", "published", "archived"]
    config: dict[str, Any]
    schema_version: int
    draft_revision: int
    created_by: str
    created_at: datetime
    published_at: datetime | None
    published_by: str | None
    note: str | None
    restored_from: int | None


class AssistantStateResponse(BaseModel):
    corpus_id: UUID
    enabled: bool = Field(..., description="A published assistant exists and is switched on.")
    published: AssistantVersionResponse | None
    draft: AssistantVersionResponse | None
    schema_version: int


class AssistantVersionSummary(BaseModel):
    version: int
    status: Literal["published", "archived"]
    enabled: bool
    created_by: str
    published_at: datetime | None
    note: str | None
    restored_from: int | None


class AssistantVersionListResponse(BaseModel):
    items: list[AssistantVersionSummary]
    total: int
    offset: int
    limit: int


class CompileSectionOut(BaseModel):
    name: str
    text: str
    tokens: int


class CompileResponse(BaseModel):
    system_prompt: str
    constraints: str
    sections: list[CompileSectionOut]
    rules_fired: list[str]
    total_tokens: int
    max_tokens: int = Field(
        ...,
        description="Answer cap for known non-reasoning models only (a reasoning model needs room to think).",
    )
    enabled_rules: int = Field(0, description="Guidance rules switched on in the compiled config.")
    max_enabled_rules: int = Field(30, description="Most rules that may be on at once.")
    rules_tokens: int = Field(
        0,
        description="Tokens of every enabled rule: the number publish checks, not just those firing.",
    )
    rules_budget: int = Field(1200, description="Token budget for the guidance rules.")
    prompt_tokens: int = Field(0, description="Worst-case tokens of the whole system prompt.")
    prompt_budget: int = Field(0, description="Most tokens the system prompt may use.")


class TemplateSummary(BaseModel):
    key: TemplateKey
    name: str
    description: str
    business_type: TemplateKey


class TemplateDetail(BaseModel):
    key: TemplateKey
    name: str
    description: str
    config: dict[str, Any]
    tests: list[TemplateTest]


# ---- the reply envelope (README "The reply envelope and SSE contract", P1). Defined here and
# only here; C1 appends SimulateOptions / AssistantRequestOptions to this module and emits
# these models; C4 fills LeadFormBlock, C5 HandoffCardBlock (incl. callback_form), C6 Cta and
# quick replies. All strict, so a provider bug (a misspelt field) fails a test, not a visitor.
class Cta(_Strict):
    type: Literal["link", "call", "whatsapp", "booking", "lead_form", "handoff"]
    label: str = Field(..., min_length=1, max_length=40)
    url: str | None = Field(None, max_length=2048)  # https:, tel:, mailto: or https://wa.me/...


class LeadFormField(_Strict):
    id: str = Field(..., min_length=1, max_length=40)
    label: str = Field(..., min_length=1, max_length=60)
    type: Literal["text", "tel", "email", "select", "date", "number"]
    required: bool = False
    options: list[str] = Field(default_factory=list, max_length=20)


class LeadFormBlock(_Strict):
    kind: Literal["lead_form"] = "lead_form"
    form_id: str = Field(..., pattern=r"^lf_[0-9a-f]{16}$")
    title: str
    notice: str  # Markdown, rendered with CobMd.inline / <Markdown inline>
    consent_label: str
    privacy_url: str | None = None
    fields: list[LeadFormField]
    submit_label: str
    trigger: str
    callback: bool = False


class HandoffChannelOut(_Strict):
    kind: Literal["whatsapp", "call", "callback", "email", "contact_page", "booking"]
    label: str = Field(..., min_length=1, max_length=40)
    url: str | None = Field(None, max_length=2048)


class HandoffCardBlock(_Strict):
    kind: Literal["handoff_card"] = "handoff_card"
    open_now: bool
    message: str  # Markdown
    channels: list[HandoffChannelOut]
    reason: str
    callback_form: LeadFormBlock | None = None  # C5: the callback-request variant of the form
    ref_code: str | None = None  # TV2 reference: quote it to a person


UiBlock = Annotated[LeadFormBlock | HandoffCardBlock, Field(discriminator="kind")]


class AssistantReplyFields(_Strict):
    """Additive reply fields mixed into QueryResponse / MessageSendResponse (C1);
    all empty when the knowledge base's assistant is switched off."""

    label: TurnLabel | None = None
    route: Route | None = None
    language: str | None = None
    quick_replies: list[QuickReply] = Field(default_factory=list, max_length=4)
    cta: Cta | None = None
    ui: UiBlock | None = None
    timings: dict[str, float] | None = None  # ms per stage, plus gate_threshold (C1)
    conversation_id: UUID | None = None
    assistant_version: int | None = None
    trace: dict[str, Any] | None = None
    paused: bool = Field(
        False,
        description=(
            "True when the answer is the pause message (the balance ran out, a fair-use "
            "limit was reached, or the plan's local model is not ready)."
        ),
    )
    pause_reason: Literal["billing", "fair_use", "local_unavailable"] | None = Field(
        None, description="Why the answer was paused; present only when `paused` is true."
    )


class SimulateOptions(_Strict):
    office_hours: Literal["open", "closed"] | None = None
    page_url: str | None = Field(None, max_length=2048)
    language: LangKey | None = None


class AssistantRequestOptions(_Strict):
    conversation_id: UUID | None = Field(
        None, description="Continue a stored conversation of this knowledge base."
    )
    page_url: str | None = Field(None, max_length=2048)
    use_draft: bool = Field(
        False, description="Answer with the current draft (preview). Needs write permission."
    )
    debug: bool = Field(
        False, description="Owner only: include the turn trace. Needs write permission."
    )
    simulate: SimulateOptions | None = Field(
        None, description="Only with use_draft or debug: pretend office hours, page or language."
    )


class ImproveRuleRequest(StrictRequest):
    instruction: str = Field(
        ..., min_length=1, max_length=500, description="The owner's rough rule text."
    )
    type: Literal["communication", "context", "clarification", "escalation", "sales", "compliance"]
    rule_id: str | None = Field(
        None, max_length=40, description="The rule being edited (left out of the conflict check)."
    )


class RuleConflict(BaseModel):
    rule_id: str
    reason: str = Field(..., max_length=300)


class ImproveRuleResponse(BaseModel):
    improved: str = Field(
        ..., max_length=500, description="A clean single-objective rewrite. Nothing is saved."
    )
    conflicts: list[RuleConflict] = Field(default_factory=list)
    notes: str = Field("", max_length=300)
