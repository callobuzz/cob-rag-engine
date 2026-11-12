"""The deployer's own company details (P1): who the legal pages, invoices and payments speak for.

`GET/PUT /api/v1/admin/company`. Admin only. Not under billing: the legal pages need it while
billing is off. Stored in the `company` engine setting; audited as `settings.updated`.
"""

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.accounts.settings_store import (
    invalidate_engine_settings_cache,
    read_engine_settings_row,
    update_engine_settings,
)
from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.core.company import CompanyDetails

# P1 payments
router = APIRouter(
    prefix="/api/v1/admin/company", tags=["settings"], dependencies=[Depends(require_admin)]
)


@router.get("", response_model=CompanyDetails)
async def get_company(session: AsyncSession = Depends(get_session)) -> CompanyDetails:
    """Your company's name, address and contact details."""
    return (await read_engine_settings_row(session))[0].company


@router.put("", response_model=CompanyDetails)
async def put_company(
    body: CompanyDetails, request: Request, session: AsyncSession = Depends(get_session)
) -> CompanyDetails:
    """Replace the company details. A GST state is checked against the list of state codes."""
    _, _, actor_label = actor_from_request(request)
    try:
        settings, changed = await update_engine_settings(
            session, {"company": body.model_dump()}, actor_label=actor_label
        )
    except ValidationError as exc:  # a stored GSTIN that no longer matches the new state, etc.
        from fastapi.exceptions import RequestValidationError

        raise RequestValidationError(
            [{**e, "loc": ("body", *e["loc"])} for e in exc.errors(include_url=False)]
        ) from exc
    if changed:
        await record_audit(
            session,
            request,
            "settings.updated",
            target_type="settings",
            target_id="engine",
            details={"changed": changed},
        )
        await session.commit()
        invalidate_engine_settings_cache()
    return settings.company


# /P1 payments
