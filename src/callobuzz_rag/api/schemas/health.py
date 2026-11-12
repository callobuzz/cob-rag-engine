"""Health check schemas."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    # ok/degraded from /health, ready/not_ready from /health/ready.
    status: Literal["ok", "degraded", "ready", "not_ready"]
    services: dict[str, bool]
