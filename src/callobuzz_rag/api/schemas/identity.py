"""Response schemas for GET /api/v1/whoami and GET /api/v1/info."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from callobuzz_rag.api.features import DashboardFeaturesInfo  # BIZ3 features


class WhoAmIUser(BaseModel):
    id: UUID
    email: str
    name: str


class WhoAmIResponse(BaseModel):
    """What the presented credential is, and what it reaches."""

    kind: Literal["admin", "tenant", "corpus"] = Field(
        description=(
            "admin = RAG_ADMIN_API_KEY or the super admin's sign-in, tenant = a tk_ key or a "
            "workspace sign-in, corpus = a ck_ key."
        )
    )
    auth: Literal["api_key", "session"] = Field(
        "api_key", description="api_key = X-API-Key; session = Authorization: Bearer."
    )
    role: Literal["owner", "member"] | None = Field(
        None, description="In the tenant: a tk_ key acts as owner; a sign-in has its role."
    )
    user: WhoAmIUser | None = Field(None, description="The signed-in person (sessions only).")
    tenant_id: UUID | None = Field(
        None, description="The tenant this key belongs to (a corpus key's owning tenant)."
    )
    tenant_name: str | None = None
    corpus_id: UUID | None = Field(None, description="Set only for a corpus (ck_) key.")
    corpus_name: str | None = None
    permission: Literal["read", "write", "full"] | None = Field(
        None,
        description=(
            "A tk_ or ck_ key's permission: read (search, ask, list, get), write (plus add and "
            "change content) or full (plus delete and key management). Null for the admin key "
            "and sign-ins, which carry none."
        ),
    )
    key_name: str | None = Field(None, description="The name of the tk_ or ck_ key in use.")
    key_id: UUID | None = Field(
        None, description="The id of the tk_ or ck_ key in use (the id in the key lists)."
    )


class TranscriptionInfo(BaseModel):
    enabled: bool = Field(description="Audio uploads are accepted.")
    provider: str = Field(description="whisper_api | whisper_local | off")


class RerankerInfo(BaseModel):
    local: bool = Field(description="The self-hosted cross-encoder is loaded in this engine.")
    llm: bool = Field(
        description="Reranking with an LLM is possible (with the caller's or the engine's key)."
    )
    default_provider: str = Field(description="local | llm, for corpora that do not choose.")
    enabled_by_default: bool = Field(description="Corpora rerank unless they turn it off.")


class WebSearchInfo(BaseModel):
    enabled: bool
    provider: str


class CobInfo(BaseModel):
    """COB, the built-in assistant."""

    enabled: bool = Field(description="The dashboard COB (chat, history, actions) is on.")
    public: bool = Field(description="The docs-only COB on the sign-in pages is on.")
    corpus_id: UUID | None = Field(
        None, description="COB's own knowledge base; usage screens label it COB."
    )
    docs_version: str | None = Field(
        None,
        description="The docs version COB's knowledge base holds (null before the first sync).",
    )


class BillingInfo(BaseModel):  # BIL2
    enabled: bool = Field(
        description="Customer billing is on (RAG_BILLING_ENABLED); the billing routes exist."
    )


class WebsiteLearningInfo(BaseModel):  # BIZ1 learning
    enabled: bool = Field(
        description=(
            "The engine reads businesses' websites (RAG_WEBSITE_LEARNING_ENABLED). A plan can "
            "still switch it off for a workspace."
        )
    )
    render: bool = Field(
        description="Pages that need a browser can be read (the scraper's render route is set up)."
    )


# PIP2 local
class LocalLLMInfo(BaseModel):
    enabled: bool = Field(description="The free local model is part of this install.")
    ready: bool = Field(description="It is downloaded and answering right now.")


# /PIP2 local


# TV6 whatsapp
class WhatsAppInfo(BaseModel):
    """Whether the dashboard shows the WhatsApp page."""

    enabled: bool = Field(
        default=False, description="The engine has a Meta app configured for WhatsApp."
    )
    available: bool = Field(
        default=False,
        description="The engine is ready and the caller's plan includes WhatsApp (an operator "
        "sees true whenever the engine is ready).",
    )
    embedded_signup: bool = Field(
        default=False, description="A business owner can connect a number by Embedded Signup."
    )
    public_address_missing: bool = Field(
        default=False,
        description="The operator only: WhatsApp needs a public https address (RAG_PUBLIC_URL) "
        "and this server has none.",
    )


# /TV6 whatsapp


# TV1 human
class TrustInfo(BaseModel):
    """The deployer's trust switches a dashboard needs to draw its screens."""

    allow_hide_human_button: bool = Field(
        default=False,
        description='A business owner may hide the "Talk to a person" button of its widget.',
    )
    # TV5 disclosure
    require_ai_disclosure: bool = Field(
        default=True,
        description="Every widget shows the AI badge and first line; no business can switch "
        "them off.",
    )
    disclosure_wording_editable: bool = Field(
        default=True,
        description="A business may word the AI disclosure line itself (it must still say AI).",
    )
    # /TV5 disclosure


# /TV1 human


# P1 payments
class PaymentsInfo(BaseModel):
    enabled: bool = Field(
        default=False,
        description="Online payments are switched on and usable (billing on, a provider set up).",
    )
    mode: Literal["test", "live"] | None = Field(
        default=None, description="Whether the saved keys are Razorpay test or live keys."
    )


# /P1 payments


class FeaturesInfo(BaseModel):
    content_portal: bool = Field(description="The /api/v1/portal routes exist.")
    accounts: bool = Field(
        description="Dashboard sign-in (/api/v1/auth) is on: RAG_SESSION_SECRET is set."
    )
    # LP1 landing
    landing: bool = Field(
        default=False, description="The deployer's public website is live at the root address."
    )
    # /LP1 landing
    streaming: bool = Field(description="POST /api/v1/query/stream is enabled.")
    transcription: TranscriptionInfo
    reranker: RerankerInfo
    web_search: WebSearchInfo
    cob: CobInfo
    billing: BillingInfo  # BIL2
    eval_ragas: bool = Field(
        description=(
            "The answer-quality evaluation add-on (RAGAS + LangChain) is installed: "
            "faithfulness, answer relevancy and context metrics can be scored. "
            "pip install 'cob-rag-engine[eval]'; Docker: INSTALL_EVAL=true."
        )
    )
    # BIZ3 features
    dashboard: DashboardFeaturesInfo | None = Field(
        default=None,
        description=(
            "What the caller's plan shows in the dashboard (a business's session only). "
            "null: everything is shown (admin, API keys, the operator's own workspace)."
        ),
    )
    # /BIZ3 features
    # BIZ1 learning
    website_learning: WebsiteLearningInfo
    # /BIZ1 learning
    # PIP2 local
    local_llm: LocalLLMInfo
    # /PIP2 local
    # TV1 human
    trust: TrustInfo = Field(default_factory=TrustInfo)
    # /TV1 human
    whatsapp: WhatsAppInfo = Field(default_factory=WhatsAppInfo)  # TV6 whatsapp
    payments: PaymentsInfo = Field(default_factory=PaymentsInfo)  # P1 payments


class EmbeddingInfo(BaseModel):
    provider: str
    model: str
    dimensions: int


class LLMInfo(BaseModel):
    provider: str | None
    model: str
    fast_model: str | None
    fast_provider_order: list[str] = Field(
        default_factory=list,
        description="OpenRouter hosts tried first for the fast model (RAG_LLM_FAST_PROVIDER_ORDER).",
    )
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = Field(
        None, description="Engine default thinking effort (RAG_LLM_REASONING_EFFORT); null = unset."
    )
    thinking: str | None = Field(
        None,
        description=(
            "Engine default thinking of the answer model (RAG_LLM_THINKING, else "
            "RAG_LLM_REASONING_EFFORT read as a level): off, minimal, low, medium, high or "
            "provider-default; null = unset (provider default)."
        ),
    )
    fast_thinking: str = Field(
        "off", description="Engine default thinking of the fast model (RAG_LLM_FAST_THINKING)."
    )
    language_models: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Engine default answer model per reply language (RAG_LLM_LANGUAGE_MODELS): a "
            "language code to a model id. A language not listed uses `model`. Empty = no routing."
        ),
    )
    key_configured: bool = Field(
        description="The engine has its own LLM key; without one callers send X-LLM-* headers."
    )


class LimitsInfo(BaseModel):
    max_upload_bytes: int = Field(
        description="Largest file one upload may be: the engine's cap, lowered by the plan."
    )
    max_document_tokens: int = Field(
        description="Most text one document may hold, in tokens, counted after parsing "
        "(the engine's cap, lowered by the plan). Not the same as the file size."
    )
    allowed_mime_types: list[str] = Field(description="Accepted by POST /api/v1/ingest.")
    allowed_extensions: list[str] = Field(description="File extensions those types come as.")
    max_records_per_request: int
    max_record_chars: int
    max_query_chars: int


class RetrievalDefaultsInfo(BaseModel):
    """The value each retrieval setting takes for a corpus that has not stored one.

    Field names match `RetrievalConfigInput` (without `preset`). The values come from
    the same resolver the query pipeline uses, applied to an empty corpus config, so a
    UI can show what an unset field actually does. A preset stores its own values, which
    take priority over these.
    """

    top_k: int
    final_k: int
    multi_query_count: int
    hyde_enabled: bool
    query_classification_enabled: bool
    reranking_enabled: bool
    mmr_enabled: bool
    mmr_lambda: float
    temporal_decay_enabled: bool
    validation_enabled: bool
    cache_enabled: bool
    hybrid_search_enabled: bool
    fusion_strategy: str = Field(description="convex | rrf")
    hybrid_dense_weight: float
    rrf_k: int
    rerank_provider: Literal["local", "llm"]
    rerank_llm_model: str | None = Field(
        description="None = the caller's fast model is used for LLM reranking."
    )
    pipeline_order_enabled: bool
    boilerplate_strip: bool = Field(
        description="Page furniture is stripped from documents before they are indexed."
    )
    collapse_duplicates: bool = Field(
        description="A passage stored in several documents is returned once, with `also_in`."
    )


class InfoResponse(BaseModel):
    """What this engine can do, for a UI deciding which screens and limits to show."""

    version: str
    features: FeaturesInfo
    embedding: EmbeddingInfo
    llm: LLMInfo
    public_url: str = Field(description="Where browsers reach the engine (RAG_PUBLIC_URL).")
    public_url_configured: bool = Field(
        description="False when public_url is only this request's own address."
    )
    limits: LimitsInfo
    retrieval_defaults: RetrievalDefaultsInfo = Field(
        description="Effective retrieval settings for fields a corpus leaves unset."
    )
