"""Named API key schemas (knowledge-base `ck_` keys and customer `tk_` keys)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from callobuzz_rag.core.schemas import StrictRequest

KeyPermission = Literal["read", "write", "full"]

_PERMISSION_HELP = (
    "read: search, ask, list and get only. write: read plus add and change content and "
    "non-destructive settings. full: write plus delete and key management."
)


class CreateApiKeyRequest(StrictRequest):
    name: str = Field(
        ..., min_length=1, max_length=120, description="What it is for, e.g. 'Agent - Claude Code'"
    )
    permission: KeyPermission = Field("read", description=_PERMISSION_HELP)


class UpdateApiKeyRequest(StrictRequest):
    name: str | None = Field(None, min_length=1, max_length=120)
    permission: KeyPermission | None = Field(None, description=_PERMISSION_HELP)


class ApiKeyResponse(BaseModel):
    """A key as listed: never the key itself or its hash, only the prefix it starts with."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    key_prefix: str = Field(description="The start of the key, enough to tell keys apart")
    permission: KeyPermission = Field(description=_PERMISSION_HELP)
    is_default: bool = Field(
        description="The key the single-key rotate routes replace (migrated keys are 'Default key')"
    )
    created_by: str | None = Field(description="An e-mail, 'admin key', a key's name or 'migrated'")
    created_at: datetime
    last_used_at: datetime | None = Field(description="Updated at most once a minute")


class ApiKeyListResponse(BaseModel):
    keys: list[ApiKeyResponse]


class CreateApiKeyResponse(ApiKeyResponse):
    api_key: str = Field(description="The key. Shown only now: store it securely.")


class RotateApiKeyResponse(BaseModel):
    id: UUID
    key_prefix: str
    api_key: str = Field(description="The new key, shown only now. The old one has stopped.")
