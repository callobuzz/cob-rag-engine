"""Named API keys of a knowledge base or a customer: issue, rotate, change, revoke.

A key is `{ck|tk}_{id_short}_{random}`. `id_short` is the first 8 characters of its owner's
id, so a request finds the owner's key rows by index, and the first 8 characters of `random`
are stored as `lookup`, so it usually finds exactly one row to run bcrypt on (the table's
docstring in `db/models/api_key.py` has the rest). Only the bcrypt hash is stored; the plaintext
is returned once, by the call that issued it.

These functions add to the caller's session and never commit; each route commits. Each one
writes its audit row (`api_key.created`, `.rotated`, `.updated`, `.revoked`), which never
carries a key. The permission rules are `api/permissions.py`.
"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.permissions import PERMISSIONS
from callobuzz_rag.core.secrets import generate_api_key, hash_api_key
from callobuzz_rag.db.models.api_key import ApiKey

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant

DEFAULT_KEY_NAME = "Default key"
MAX_KEYS_PER_OWNER = 50
Kind = Literal["ck", "tk"]


@dataclass(frozen=True)
class Owner:
    """Whose key it is: a knowledge base (`ck`) or a customer (`tk`)."""

    kind: Kind
    id: uuid.UUID
    tenant_id: uuid.UUID
    label: str

    @property
    def id_short(self) -> str:
        return str(self.id)[:8]


def corpus_owner(corpus: "Corpus") -> Owner:
    return Owner("ck", corpus.id, corpus.tenant_id, corpus.name)


def tenant_owner(tenant: "Tenant") -> Owner:
    return Owner("tk", tenant.id, tenant.id, tenant.name)


def key_actor_label(request: "Request | None") -> str:
    """Who is making the key, for `created_by`: an e-mail, `admin key`, or a key's name."""
    if request is None:
        return "system"
    state = request.state
    user = getattr(state, "user", None)
    if user is not None:
        return str(user.email)[:320]
    if getattr(state, "is_admin", False):
        return "admin key"
    info = getattr(state, "api_key", None)
    if info is not None:
        return f"key: {info.name}"[:320]
    return "system"


def not_found() -> APIError:
    return APIError(404, "API key not found", "key_not_found")


def check_permission(permission: str) -> str:
    if permission not in PERMISSIONS:
        raise APIError(
            422, "Permission must be read, write or full", "invalid_permission", allowed=PERMISSIONS
        )
    return permission


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise APIError(422, "Give the key a name", "key_name_required")
    return cleaned[:120]


def _owner_filter(owner: Owner):  # type: ignore[no-untyped-def]
    return ApiKey.corpus_id == owner.id if owner.kind == "ck" else ApiKey.tenant_id == owner.id


def _audit_details(row: ApiKey, **more: object) -> dict[str, object]:
    return {"name": row.name, "permission": row.permission, "prefix": row.key_prefix, **more}


async def _material(owner: Owner, id_short: str) -> tuple[str, str, str, str]:
    """(plaintext, lookup, shown prefix, bcrypt hash) of a new key for `owner`, carrying the
    id segment `id_short` (the one its row is found by)."""
    plaintext = generate_api_key(prefix=f"{owner.kind}_{id_short}")
    random_part = plaintext.split("_", 2)[2]
    lookup = random_part[:8]
    shown = f"{owner.kind}_{id_short}_{random_part[:4]}..."
    hashed = await asyncio.to_thread(hash_api_key, plaintext)
    return plaintext, lookup, shown, hashed


async def list_keys(session: "AsyncSession", owner: Owner) -> list[ApiKey]:
    """The owner's keys, oldest first."""
    rows = await session.execute(select(ApiKey).where(_owner_filter(owner)))
    return sorted(rows.scalars().all(), key=lambda k: (k.created_at, k.name))


async def get_key(session: "AsyncSession", owner: Owner, key_id: uuid.UUID) -> ApiKey:
    row = await session.get(ApiKey, key_id)
    if row is None:
        raise not_found()
    belongs = row.corpus_id == owner.id if owner.kind == "ck" else row.tenant_id == owner.id
    if not belongs:
        raise not_found()
    return row


async def create_key(
    session: "AsyncSession",
    owner: Owner,
    *,
    name: str,
    permission: str,
    request: "Request | None",
    is_default: bool = False,
    audit: bool = True,
) -> tuple[ApiKey, str]:
    """Issue a key. Returns the row and the plaintext, which exists nowhere else."""
    check_permission(permission)
    cleaned = _clean_name(name)
    if not is_default and len(await list_keys(session, owner)) >= MAX_KEYS_PER_OWNER:
        raise APIError(
            409,
            f"At most {MAX_KEYS_PER_OWNER} keys each; revoke one you no longer use",
            "key_limit_reached",
            limit=MAX_KEYS_PER_OWNER,
        )
    plaintext, lookup, shown, hashed = await _material(owner, owner.id_short)
    row = ApiKey(
        corpus_id=owner.id if owner.kind == "ck" else None,
        tenant_id=owner.id if owner.kind == "tk" else None,
        id_short=owner.id_short,
        lookup=lookup,
        name=cleaned,
        key_prefix=shown,
        key_hash=hashed,
        permission=permission,
        is_default=is_default,
        created_by=key_actor_label(request),
    )
    session.add(row)
    await session.flush()
    if audit:
        await record_audit(
            session,
            request,
            "api_key.created",
            tenant_id=owner.tenant_id,
            tenant_label=None,
            target_type="corpus" if owner.kind == "ck" else "tenant",
            target_id=str(owner.id),
            details=_audit_details(row, owner=owner.label, key_id=str(row.id)),
        )
    return row, plaintext


async def rotate_key(
    session: "AsyncSession", owner: Owner, row: ApiKey, *, request: "Request | None"
) -> str:
    """Replace the key's secret. The old one stops on its next request. Returns the new one."""
    plaintext, lookup, shown, hashed = await _material(owner, row.id_short)
    row.lookup = lookup
    row.key_prefix = shown
    row.key_hash = hashed
    row.last_used_at = None
    await session.flush()
    await record_audit(
        session,
        request,
        "api_key.rotated",
        tenant_id=owner.tenant_id,
        target_type="corpus" if owner.kind == "ck" else "tenant",
        target_id=str(owner.id),
        details=_audit_details(row, owner=owner.label, key_id=str(row.id)),
    )
    return plaintext


async def update_key(
    session: "AsyncSession",
    owner: Owner,
    row: ApiKey,
    *,
    name: str | None,
    permission: str | None,
    request: "Request | None",
) -> None:
    """Rename a key and/or change its permission (audited when anything changed)."""
    changes: dict[str, dict[str, str]] = {}
    if name is not None:
        cleaned = _clean_name(name)
        if cleaned != row.name:
            changes["name"] = {"from": row.name, "to": cleaned}
            row.name = cleaned
    if permission is not None:
        check_permission(permission)
        if permission != row.permission:
            changes["permission"] = {"from": row.permission, "to": permission}
            row.permission = permission
    if not changes:
        return
    await session.flush()
    await record_audit(
        session,
        request,
        "api_key.updated",
        tenant_id=owner.tenant_id,
        target_type="corpus" if owner.kind == "ck" else "tenant",
        target_id=str(owner.id),
        details={"key_id": str(row.id), "owner": owner.label, "changes": changes},
    )


async def revoke_key(
    session: "AsyncSession", owner: Owner, row: ApiKey, *, request: "Request | None"
) -> None:
    """Delete the key: it stops on its next request."""
    details = _audit_details(row, owner=owner.label, key_id=str(row.id))
    await session.delete(row)
    await session.flush()
    await record_audit(
        session,
        request,
        "api_key.revoked",
        tenant_id=owner.tenant_id,
        target_type="corpus" if owner.kind == "ck" else "tenant",
        target_id=str(owner.id),
        details=details,
    )


async def rotate_default_key(
    session: "AsyncSession", owner: Owner, *, request: "Request | None"
) -> str:
    """What the single-key `rotate-key` routes do: replace the owner's default key, or issue
    one (full permission, as the single key always was) when it has none or it was revoked."""
    existing = next((k for k in await list_keys(session, owner) if k.is_default), None)
    if existing is not None:
        return await rotate_key(session, owner, existing, request=request)
    _, plaintext = await create_key(
        session,
        owner,
        name=DEFAULT_KEY_NAME,
        permission="full",
        request=request,
        is_default=True,
    )
    return plaintext
