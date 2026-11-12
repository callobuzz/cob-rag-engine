"""Pipeline profile API schemas (PIP1): the deployer's profiles, which plan or business uses
which, and the live state of the answer lanes."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from callobuzz_rag.core.schemas import StrictRequest
from callobuzz_rag.tiers.schema import ProfileConfig

_CONFIG = (
    "The profile's settings (see ProfileConfig). A field left out or null follows the "
    "knowledge base's own setting, then the engine's. Replaces the whole config on update."
)


class ResolvedOut(BaseModel):
    """What the profile's null fields mean right now (the engine's own values), so a deployer
    sees the real model and numbers behind 'follow the engine'."""

    answer_model: str
    thinking: str | None
    fast_model: str | None
    language_models: dict[str, str]
    reranker: str = Field(description="off | local | llm | api: what 'inherit' amounts to")
    rerank_model: str | None
    top_k: int
    final_k: int
    multi_query_count: int
    hyde_enabled: bool
    query_classification_enabled: bool
    max_answer_tokens: int
    followups: bool
    priority: str
    max_concurrency: int | None


class ProfileOut(BaseModel):
    id: uuid.UUID
    name: str
    label: str = Field(description="What business owners see (Economy, Standard, Premium...)")
    description: str | None
    builtin_key: str | None = Field(description="free | standard | premium for the seeded ones")
    is_default: bool = Field(description="Plans that pick no profile answer with this one")
    config: ProfileConfig
    resolved: ResolvedOut
    plan_count: int = Field(
        description="Plans that use this profile (the default counts every plan without one)"
    )
    tenant_count: int = Field(description="Businesses given this profile directly")
    available: bool = Field(
        description="false: the profile answers with the local model and it is not ready"
    )
    created_at: datetime
    updated_at: datetime
    updated_by: str | None


class ProfileListResponse(BaseModel):
    items: list[ProfileOut]
    default_id: uuid.UUID | None


class CreateProfileRequest(StrictRequest):
    name: str = Field(..., min_length=1, max_length=60)
    label: str = Field(..., min_length=1, max_length=40)
    description: str | None = Field(None, max_length=2000)
    config: dict[str, Any] = Field(default_factory=dict, description=_CONFIG)


class UpdateProfileRequest(StrictRequest):
    """Only the fields sent change; `config`, when sent, replaces the whole config."""

    name: str | None = Field(None, min_length=1, max_length=60)
    label: str | None = Field(None, min_length=1, max_length=40)
    description: str | None = Field(None, max_length=2000)
    config: dict[str, Any] | None = Field(None, description=_CONFIG)


class CloneProfileRequest(StrictRequest):
    name: str = Field(..., min_length=1, max_length=60)
    label: str | None = Field(
        None, min_length=1, max_length=40, description="Default: the source's"
    )


class PoolOut(BaseModel):
    slots: int = Field(description="Answers this pool runs at once (0 = no queue)")
    busy: int = Field(description="Slots taken right now")
    by_priority: dict[str, int] = Field(description="Busy slots by priority: high, normal, low")


class LanesOut(BaseModel):
    enabled: bool = Field(description="false: no engine-wide pool is set (RAG_ANSWER_SLOTS=0)")
    pools: dict[str, PoolOut] = Field(description="engine and local")


class TenantPipelineOut(BaseModel):
    plan_profile_id: uuid.UUID | None = Field(
        description="The profile the business's plan picks (null: the default profile)"
    )
    override_profile_id: uuid.UUID | None = Field(
        description="A profile given to this business directly, or null"
    )
    effective: ProfileOut = Field(description="The profile the business answers with")


class SetTenantPipelineRequest(StrictRequest):
    profile_id: uuid.UUID | None = Field(
        None, description="A profile for this business; null goes back to its plan's"
    )
