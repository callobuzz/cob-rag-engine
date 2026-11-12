"""May this knowledge base learn from its website, and how many pages (BIZ1).

Five reasons say no: the engine switch (`RAG_WEBSITE_LEARNING_ENABLED`), the COB system
knowledge base (it never learns a website), a workspace that is not active (suspended or still
waiting for approval: nothing is read, nothing is parked, it resumes with the workspace), the
workspace's plan (`website_learning_enabled`) and
the knowledge base's own switch. The page cap is the deployer's decision per workspace (D2):
`min(plan.website_pages_max or unlimited, RAG_WEBSITE_MAX_PAGES)` minus what the workspace's other
sites already hold; the engine ceiling always applies.
"""

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select

from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.pipeline_profile import TenantPipelineSettings  # PIP3 fair use
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.website import WebsiteSettings, WebsiteSource

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

Reason = Literal["engine", "system", "plan", "kb", "tenant"]


@dataclass(frozen=True)
class WebsitePolicy:
    enabled: bool
    reason: Reason | None
    pages_cap: int  # pages this site may hold now (never negative)
    base_cap: int = 0  # the workspace's whole allowance: min(plan, engine ceiling)


async def website_policy(
    session: "AsyncSession",
    corpus: Corpus,
    settings: Settings,
    *,
    exclude_source_id: uuid.UUID | None = None,
) -> WebsitePolicy:
    """The policy for `corpus`. `exclude_source_id` is the site being (re-)learned: its own
    pages do not count against its cap."""
    ceiling = settings.website_max_pages
    if not settings.website_learning_enabled:
        return WebsitePolicy(False, "engine", 0, ceiling)
    if getattr(corpus, "is_system", False):
        return WebsitePolicy(False, "system", 0)

    tenant = await session.get(Tenant, corpus.tenant_id)
    if tenant is not None and tenant.status != "active":  # suspended or not yet approved
        return WebsitePolicy(False, "tenant", 0)
    plan = await session.get(Plan, tenant.plan_id) if tenant is not None else None
    base = ceiling
    if plan is not None:
        if not plan.website_learning_enabled:
            return WebsitePolicy(False, "plan", 0)
        if plan.website_pages_max is not None:
            base = min(plan.website_pages_max, ceiling)
        # PIP3 fair use: the deployer may give this business its own page total (D9: the plan's
        # `website_pages_max` is the default; an override of null means no limit but the engine's)
        overrides: dict[str, Any] = (
            await session.execute(
                select(TenantPipelineSettings.fair_use_overrides).where(
                    TenantPipelineSettings.tenant_id == corpus.tenant_id
                )
            )
        ).scalar_one_or_none() or {}
        if "website_pages_total" in overrides:
            own = overrides["website_pages_total"]
            base = ceiling if own is None else min(int(own), ceiling)
        # /PIP3 fair use

    row = (
        await session.execute(select(WebsiteSettings).where(WebsiteSettings.corpus_id == corpus.id))
    ).scalar_one_or_none()
    if row is not None and not row.enabled:
        return WebsitePolicy(False, "kb", 0, base)

    others = (
        (
            await session.execute(
                select(WebsiteSource).where(WebsiteSource.tenant_id == corpus.tenant_id)
            )
        )
        .scalars()
        .all()
    )
    held = sum(s.pages_learned for s in others if s.id != exclude_source_id)
    return WebsitePolicy(True, None, max(base - held, 0), base)
