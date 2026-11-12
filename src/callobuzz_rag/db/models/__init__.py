"""Database models — import all models here so Alembic can discover them."""

from callobuzz_rag.db.models.add_on import AddOn, AddOnGrant  # BIL5
from callobuzz_rag.db.models.api_key import ApiKey
from callobuzz_rag.db.models.assistant_config import AssistantConfigVersion
from callobuzz_rag.db.models.assistant_review_action import AssistantReviewAction
from callobuzz_rag.db.models.assistant_setup import AssistantSetupSession
from callobuzz_rag.db.models.assistant_turn import AssistantTurn
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.billing_account import BillingAccount  # BIL2
from callobuzz_rag.db.models.billing_notice import BillingNotice  # TV3
from callobuzz_rag.db.models.billing_reconciliation import BillingReconciliation  # BIL3
from callobuzz_rag.db.models.billing_statement import BillingStatement  # BIL6
from callobuzz_rag.db.models.channel import (  # TV6
    ChannelConnection,
    ChannelEvent,
    ChannelThread,
)
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.chunk_metadata import ChunkMetadata
from callobuzz_rag.db.models.cob_action_proposal import CobActionProposal  # COB5
from callobuzz_rag.db.models.cob_status import CobStatus
from callobuzz_rag.db.models.config import Config
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.corpus_migration import CorpusMigration
from callobuzz_rag.db.models.credit_lot import CreditLot  # BIL3
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.email_code import EmailCode
from callobuzz_rag.db.models.embedding import Embedding
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.eval_criterion import EvalCriterion, EvalSettings
from callobuzz_rag.db.models.eval_run import EvalRun
from callobuzz_rag.db.models.eval_run_sample import EvalRunSample
from callobuzz_rag.db.models.fair_use import FairUseAlert, FairUseCounter  # PIP3
from callobuzz_rag.db.models.feedback import Feedback
from callobuzz_rag.db.models.gap_report import GapReport
from callobuzz_rag.db.models.golden_pair import GoldenPair
from callobuzz_rag.db.models.golden_set import GoldenSet
from callobuzz_rag.db.models.handoff import Handoff
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.db.models.invitation import Invitation
from callobuzz_rag.db.models.kb_block_hash import KbBlockHash
from callobuzz_rag.db.models.knowledge_article import KnowledgeArticle  # BIZ1
from callobuzz_rag.db.models.landing import LandingAsset, LandingSite, LandingVersion  # LP1 landing
from callobuzz_rag.db.models.lead import Lead
from callobuzz_rag.db.models.lead_consent import LeadConsent
from callobuzz_rag.db.models.legal import LegalAcceptance, LegalDocument  # P3 legal
from callobuzz_rag.db.models.memory import Memory
from callobuzz_rag.db.models.message import Message
from callobuzz_rag.db.models.onboarding import OnboardingState, WidgetOriginSeen  # BIZ4
from callobuzz_rag.db.models.optimization_run import OptimizationRun
from callobuzz_rag.db.models.payment import (  # P1 payments
    BillingProfile,
    Payment,
    PaymentEvent,
    PaymentOrder,
    PaymentProviderSetting,
    PaymentRefund,
)
from callobuzz_rag.db.models.pipeline_profile import (  # PIP1
    PipelineProfile,
    TenantPipelineSettings,
)
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing  # BIL2
from callobuzz_rag.db.models.provider_balance_setting import ProviderBalanceSetting  # BIL1
from callobuzz_rag.db.models.public_asset import PublicAsset  # BIZ4
from callobuzz_rag.db.models.query_log import QueryLog
from callobuzz_rag.db.models.service_order import ServiceOrder  # P5 plans
from callobuzz_rag.db.models.support import SupportMessage, SupportRequest  # TV3
from callobuzz_rag.db.models.tax_document import TaxDocument, TaxDocumentCounter  # P2 invoices
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.tenant_question_count import TenantQuestionCount
from callobuzz_rag.db.models.usage import UsageDaily
from callobuzz_rag.db.models.usage_charge import UsageCharge  # BIL3
from callobuzz_rag.db.models.user import User
from callobuzz_rag.db.models.user_session import UserSession
from callobuzz_rag.db.models.value import ValueReport, ValueSettings  # TV4
from callobuzz_rag.db.models.wallet_ledger import WalletLedger  # BIL3
from callobuzz_rag.db.models.webhook_config import WebhookConfig
from callobuzz_rag.db.models.website import (  # BIZ1
    WebsitePage,
    WebsiteRun,
    WebsiteSettings,
    WebsiteSource,
)
from callobuzz_rag.db.models.widget_key import WidgetKey

__all__ = [
    "AddOn",  # BIL5
    "AddOnGrant",  # BIL5
    "ApiKey",
    "AssistantConfigVersion",
    "AssistantReviewAction",
    "AssistantSetupSession",
    "AssistantTurn",
    "AuditLog",
    "BillingAccount",  # BIL2
    "BillingNotice",  # TV3
    "BillingProfile",  # P1 payments
    "BillingReconciliation",  # BIL3
    "BillingStatement",  # BIL6
    "ChannelConnection",  # TV6
    "ChannelEvent",  # TV6
    "ChannelThread",  # TV6
    "Chunk",
    "ChunkMetadata",
    "CobActionProposal",  # COB5
    "CobStatus",
    "Config",
    "Conversation",
    "Corpus",
    "CorpusMigration",
    "CreditLot",  # BIL3
    "Document",
    "DocumentVersion",
    "EmailCode",
    "Embedding",
    "EngineSettingsRow",
    "EvalCriterion",
    "EvalRun",
    "EvalRunSample",
    "EvalSettings",
    "FairUseAlert",  # PIP3
    "FairUseCounter",  # PIP3
    "Feedback",
    "GapReport",
    "GoldenPair",
    "GoldenSet",
    "Handoff",
    "IngestJob",
    "Invitation",
    "KbBlockHash",
    "KnowledgeArticle",  # BIZ1
    "LandingAsset",  # LP1 landing
    "LandingSite",  # LP1 landing
    "LandingVersion",  # LP1 landing
    "Lead",
    "LeadConsent",
    "LegalAcceptance",  # P3 legal
    "LegalDocument",  # P3 legal
    "Memory",
    "Message",
    "OnboardingState",  # BIZ4
    "OptimizationRun",
    "Payment",  # P1 payments
    "PaymentEvent",  # P1 payments
    "PaymentOrder",  # P1 payments
    "PaymentProviderSetting",  # P1 payments
    "PaymentRefund",  # P1 payments
    "PipelineProfile",  # PIP1
    "Plan",
    "PlanPricing",  # BIL2
    "ProviderBalanceSetting",  # BIL1
    "PublicAsset",  # BIZ4
    "QueryLog",
    "ServiceOrder",  # P5 plans
    "SupportMessage",  # TV3
    "SupportRequest",  # TV3
    "TaxDocument",  # P2 invoices
    "TaxDocumentCounter",  # P2 invoices
    "Tenant",
    "TenantMember",
    "TenantPipelineSettings",  # PIP1
    "TenantQuestionCount",
    "UsageCharge",  # BIL3
    "UsageDaily",
    "User",
    "UserSession",
    "ValueReport",  # TV4
    "ValueSettings",  # TV4
    "WalletLedger",  # BIL3
    "WebhookConfig",
    "WebsitePage",  # BIZ1
    "WebsiteRun",  # BIZ1
    "WebsiteSettings",  # BIZ1
    "WebsiteSource",  # BIZ1
    "WidgetKey",
    "WidgetOriginSeen",  # BIZ4
]

# --- Content Portal models (conditional on feature flag) ---
try:
    from callobuzz_rag.core.config import get_settings

    if get_settings().enable_content_portal:
        from callobuzz_rag.content_portal.models import (
            ContentRule,
            StagedChunk,
            StagedContent,
            StagedDocument,
            Workspace,
            WorkspaceConversation,
            WorkspaceMessage,
        )

        __all__ += [
            "ContentRule",
            "StagedChunk",
            "StagedContent",
            "StagedDocument",
            "Workspace",
            "WorkspaceConversation",
            "WorkspaceMessage",
        ]
except Exception:
    # Feature flag check may fail during migrations or tests
    pass
