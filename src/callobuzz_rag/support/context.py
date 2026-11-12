"""What the provider sees next to a support request (TV3, D12).

Built on the server from the workspace itself, except `page` and `client_errors`, which the
dashboard sends and which are validated by the request body (a path, and at most three error
codes). Nothing here is a secret, a key, a balance or something a visitor typed.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import callobuzz_rag
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant


async def build_context(
    session: AsyncSession,
    tenant: Tenant,
    *,
    page: str | None,
    client_errors: list[str],
) -> dict[str, Any]:
    """`{plan, workspace_status, paused, pause_reason, kb_count, page, product_version,
    client_errors}` for `tenant`."""
    plan = await session.get(Plan, tenant.plan_id) if tenant.plan_id is not None else None
    account = (
        await session.execute(select(BillingAccount).where(BillingAccount.tenant_id == tenant.id))
    ).scalar_one_or_none()
    paused = bool(account is not None and (account.paused_at is not None))
    reason: str | None = None
    if account is not None and account.pause_override == "force":
        paused, reason = True, "forced"
    elif paused:
        reason = "balance"
    kb_ids = (
        (await session.execute(select(Corpus.id).where(Corpus.tenant_id == tenant.id)))
        .scalars()
        .all()
    )
    return {
        "plan": plan.name if plan is not None else None,
        "workspace_status": tenant.status,
        "paused": paused,
        "pause_reason": reason,
        "kb_count": len(kb_ids),
        "page": page,
        "product_version": callobuzz_rag.__version__,
        "client_errors": list(client_errors)[:3],
    }
