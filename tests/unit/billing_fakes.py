"""Stand-ins for the billing tests (BIL2): the in-memory database cannot run
`INSERT ... ON CONFLICT`, so `ensure_account` is replaced by a version that adds the row.
The real statement is exercised on PostgreSQL by `tests/integration/test_billing_accounts_db.py`.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from callobuzz_rag.billing import accounts, ledger
from callobuzz_rag.billing import summary as billing_summary  # BIL6
from callobuzz_rag.billing.pricing import PricingFields
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from tests.unit.accounts_fakes import FakeDB


def stored_pricing(plan: Plan, *, version: int = 1, **fields: Any) -> PlanPricing:
    """A stored pricing version with every column set (the stand-in has no server defaults)."""
    values = PricingFields(**fields).model_dump()
    return PlanPricing(plan_id=plan.id, version=version, is_current=True, **values)


@contextmanager
def fake_accounts(db: FakeDB) -> Iterator[None]:
    """`ensure_account` that creates the row in `db` (currency from the plan's pricing)."""

    async def ensure(session: Any, tenant_id: uuid.UUID) -> BillingAccount:
        for account in db.all(BillingAccount):
            if account.tenant_id == tenant_id:
                return account
        tenant = next((t for t in db.all(Tenant) if t.id == tenant_id), None)
        if tenant is None:
            raise accounts.TenantNotFoundError(str(tenant_id))
        pricing = next(
            (p for p in db.all(PlanPricing) if p.plan_id == tenant.plan_id and p.is_current), None
        )
        return db.add(
            BillingAccount(
                tenant_id=tenant_id, currency=pricing.currency if pricing else "INR", ledger_seq=0
            )
        )

    async def owed(session: Any, account: BillingAccount) -> int:  # BIL3: the stand-in has no SUM()
        return int(account.usage_residue_micro or 0)

    async def usage(session: Any, account: BillingAccount, now: Any = None) -> Any:  # BIL6
        return billing_summary.UsagePeriod(questions=0, charged_minor=0, unpriced=0)

    async def refs(session: Any, account: BillingAccount, now: Any = None) -> list[Any]:  # BIL6
        return []

    async def own_key(session: Any, tenant_id: Any, *, now: Any = None) -> tuple[bool, bool, None]:
        return True, False, None  # P5 plans: the lookup is one EXISTS statement the stand-in lacks

    with (
        patch("callobuzz_rag.api.routes.billing.own_key_status", own_key),  # P5 plans
        patch.object(accounts, "ensure_account", ensure),
        patch.object(ledger, "owed_micro", owed),
        patch.object(billing_summary, "usage_this_period", usage),  # BIL6: needs SUM()
        patch.object(billing_summary, "recent_statements", refs),  # BIL6: needs SUM()
    ):
        yield
