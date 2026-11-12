"""First-run onboarding: three screens from a website address to a working assistant (BIZ4).

`POST /onboarding/website` sets up the knowledge base, the assistant, the widget key and website
learning by calling the engine's own routes with the caller's credential, so every rule that
applies on the dashboard applies here. It is idempotent and resumable.
"""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, require_owner, resolve_scope
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.onboarding import (
    OnboardingOut,
    StartOnboardingRequest,
    UpdateOnboardingRequest,
)
from callobuzz_rag.onboarding import service

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


def _workspace(request: Request) -> uuid.UUID:
    """The caller's workspace: a workspace sign-in or a `tk_` key. Never the operator, never a
    single knowledge base's key."""
    scope = resolve_scope(request)
    if scope.corpus_id is not None:
        raise APIError(
            403, "Setting up belongs to a workspace, not to one knowledge base.", "scope_forbidden"
        )
    if scope.is_admin or scope.tenant_id is None:
        raise APIError(403, "There is no workspace here.", "no_workspace")
    return scope.tenant_id


@router.get("", response_model=OnboardingOut)
async def get_onboarding(
    request: Request, session: AsyncSession = Depends(get_session)
) -> OnboardingOut:
    """Where the workspace is in the first-run steps, the script to install, whether the
    assistant has loaded on the website yet and how many pages it has read."""
    return await service.get_state(session, request, _workspace(request))


@router.post("/website", response_model=OnboardingOut, dependencies=[Depends(require_owner)])
async def start_onboarding(
    body: StartOnboardingRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> OnboardingOut:
    """Set the business up from its website address: its knowledge base, assistant (name,
    picture, contact card), widget key for the site and website learning. Safe to repeat: a
    failed step keeps what succeeded and the next call carries on. Owner only."""
    return await service.start_website(session, request, _workspace(request), body)


@router.patch("", response_model=OnboardingOut, dependencies=[Depends(require_owner)])
async def update_onboarding(
    body: UpdateOnboardingRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> OnboardingOut:
    """Move to a step, skip the first-run steps (`dismissed`) or finish them (`done`)."""
    return await service.update_state(session, request, _workspace(request), body)
