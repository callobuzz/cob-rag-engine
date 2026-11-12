"""Self-service config API schemas for tenants."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from callobuzz_rag.api.schemas.corpus import PromptConfigInput, RetrievalConfigInput
from callobuzz_rag.core.schemas import StrictRequest


class TenantConfigResponse(BaseModel):
    """Current configuration of one corpus."""

    corpus_id: UUID | None = Field(None, description="The corpus this configuration belongs to.")
    retrieval_config: dict[str, Any] | None = None
    prompt_config: dict[str, Any] | None = None
    llm_config: dict[str, Any] | None = Field(
        None, description="LLM config (api_key_encrypted redacted)."
    )


class UpdateTenantConfigRequest(StrictRequest):
    """Request to update tenant config. All fields optional.

    A field sent as null is removed (the engine default applies again), `{}`
    changes nothing, and a whole section sent as null resets it.
    """

    retrieval_config: RetrievalConfigInput | None = None
    prompt_config: PromptConfigInput | None = None
