"""Named API keys: many per knowledge base (`ck_`) and per customer (`tk_`).

Three mounts of the same five routes (list, create, rename or change permission, rotate,
revoke):

- `/api/v1/corpora/{corpus_id}/keys`: a knowledge base's keys. Any credential that reaches
  the knowledge base (admin, its customer, a dashboard owner, or a `ck_` key with `full`
  permission).
- `/api/v1/tenants/me/keys`: the calling customer's keys (a `tk_` key with `full` permission
  or a workspace owner's sign-in).
- `/api/v1/admin/tenants/{tenant_id}/keys`: any customer's keys, for the operator.

The key is returned once, by create and rotate; a list shows only each key's prefix. Every
change is audited (`api_key.*`). Members of a workspace cannot manage keys (`require_owner`),
and a key needs `full` permission (`api/permissions.py`: these routes are `manage`).
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts import api_keys
from callobuzz_rag.accounts.api_keys import Owner
from callobuzz_rag.api.deps import (
    get_authenticated_tenant,
    get_session,
    path_corpus,
    require_admin,
    require_owner,
)
from callobuzz_rag.api.schemas.api_key import (
    ApiKeyListResponse,
    ApiKeyResponse,
    CreateApiKeyRequest,
    CreateApiKeyResponse,
    RotateApiKeyResponse,
    UpdateApiKeyRequest,
)
from callobuzz_rag.cob.system import refuse_system_tenant_write
from callobuzz_rag.db.models.api_key import ApiKey
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant

OwnerDep = Callable[..., Awaitable[Owner]]

corpus_router = APIRouter(prefix="/api/v1/corpora/{corpus_id}/keys", tags=["api-keys"])
tenant_router = APIRouter(prefix="/api/v1/tenants/me/keys", tags=["api-keys"])
admin_router = APIRouter(prefix="/api/v1/admin/tenants/{tenant_id}/keys", tags=["api-keys"])


async def corpus_key_owner(
    corpus: Corpus = Depends(path_corpus), _owner: None = Depends(require_owner)
) -> Owner:
    return api_keys.corpus_owner(corpus)


async def own_tenant_key_owner(
    tenant: Tenant = Depends(get_authenticated_tenant), _owner: None = Depends(require_owner)
) -> Owner:
    return api_keys.tenant_owner(tenant)


async def admin_tenant_key_owner(
    tenant_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> Owner:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    refuse_system_tenant_write(request, tenant)
    return api_keys.tenant_owner(tenant)


def _response(row: ApiKey) -> ApiKeyResponse:
    return ApiKeyResponse.model_validate(row)


def _mount(router: APIRouter, owner_dep: OwnerDep, what: str) -> None:
    """Register the five key routes on `router`, the owner coming from `owner_dep`."""

    @router.get("", response_model=ApiKeyListResponse, summary=f"List {what} keys")
    async def list_keys(
        owner: Owner = Depends(owner_dep), session: AsyncSession = Depends(get_session)
    ) -> ApiKeyListResponse:
        """The keys, oldest first: name, prefix, permission, who made it, when it was last
        used (to the minute). Never a key."""
        rows = await api_keys.list_keys(session, owner)
        return ApiKeyListResponse(keys=[_response(r) for r in rows])

    @router.post(
        "", response_model=CreateApiKeyResponse, status_code=201, summary=f"Create {what} key"
    )
    async def create_key(
        body: CreateApiKeyRequest,
        request: Request,
        owner: Owner = Depends(owner_dep),
        session: AsyncSession = Depends(get_session),
    ) -> CreateApiKeyResponse:
        """Make a named key. The key is in the response and nowhere else: store it now.
        `permission` defaults to `read` (search, ask, list, get only)."""
        row, plaintext = await api_keys.create_key(
            session, owner, name=body.name, permission=body.permission, request=request
        )
        await session.commit()
        return CreateApiKeyResponse(**_response(row).model_dump(), api_key=plaintext)

    @router.patch("/{key_id}", response_model=ApiKeyResponse, summary=f"Update {what} key")
    async def update_key(
        key_id: uuid.UUID,
        body: UpdateApiKeyRequest,
        request: Request,
        owner: Owner = Depends(owner_dep),
        session: AsyncSession = Depends(get_session),
    ) -> ApiKeyResponse:
        """Rename a key or change its permission. A change takes effect on its next request."""
        row = await api_keys.get_key(session, owner, key_id)
        await api_keys.update_key(
            session, owner, row, name=body.name, permission=body.permission, request=request
        )
        await session.commit()
        return _response(row)

    @router.post(
        "/{key_id}/rotate", response_model=RotateApiKeyResponse, summary=f"Rotate {what} key"
    )
    async def rotate_key(
        key_id: uuid.UUID,
        request: Request,
        owner: Owner = Depends(owner_dep),
        session: AsyncSession = Depends(get_session),
    ) -> RotateApiKeyResponse:
        """Replace this key's secret. The old one stops on its next request; the new one is in
        the response only. Name and permission stay."""
        row = await api_keys.get_key(session, owner, key_id)
        plaintext = await api_keys.rotate_key(session, owner, row, request=request)
        await session.commit()
        return RotateApiKeyResponse(id=row.id, key_prefix=row.key_prefix, api_key=plaintext)

    @router.delete("/{key_id}", status_code=204, summary=f"Revoke {what} key")
    async def revoke_key(
        key_id: uuid.UUID,
        request: Request,
        owner: Owner = Depends(owner_dep),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        """Revoke a key: it stops on its next request and cannot be restored."""
        row = await api_keys.get_key(session, owner, key_id)
        await api_keys.revoke_key(session, owner, row, request=request)
        await session.commit()
        return Response(status_code=204)

    _ = (list_keys, create_key, update_key, rotate_key, revoke_key)  # registered above


_mount(corpus_router, corpus_key_owner, "a knowledge base's")
_mount(tenant_router, own_tenant_key_owner, "your")
_mount(admin_router, admin_tenant_key_owner, "a customer's")

ROUTERS: tuple[Any, ...] = (corpus_router, tenant_router, admin_router)
