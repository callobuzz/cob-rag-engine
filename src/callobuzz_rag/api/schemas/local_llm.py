"""API schemas for the free local model (PIP2)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LocalLLMStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(description="The local model is part of this install.")
    state: Literal["not_configured", "starting", "pulling", "ready", "down"] = Field(
        description=(
            "not_configured: switched off; starting: the server is not answering yet; "
            "pulling: the server is up and still downloading the model; ready: answering; "
            "down: not reachable, or up but unable to load the model (often too little memory)."
        )
    )
    model: str = Field(description="The model free answers use.")
    models: list[str] = Field(description="Every model the local server holds.")
    checked_at: datetime


class LocalLLMTestOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    first_token_ms: int | None = Field(description="Time to the first word.")
    total_ms: int
    tokens_per_s: float | None = Field(description="Words (pieces) written per second.")
    answer: str = Field(description="What the model said (at most 32 tokens).")
