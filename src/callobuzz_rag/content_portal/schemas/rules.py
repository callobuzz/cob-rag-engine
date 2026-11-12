"""Pydantic schemas for content routing rules endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from callobuzz_rag.core.schemas import StrictRequest

# Condition keys each rule_type requires: the condition must carry at least one
# of them. Shared by CreateRuleRequest and UpdateRuleRequest so the two cannot
# drift apart. "always" needs no keys and is deliberately absent, as is any
# unrecognised rule_type — rule_type itself is validated separately.
_CONDITION_REQUIREMENTS: dict[str, tuple[tuple[str, ...], str]] = {
    "mime_type": (
        ("mime_type", "mime_types"),
        "mime_type rule requires 'mime_type' or 'mime_types' in condition",
    ),
    "size": (
        ("size_gt_mb", "size_lt_mb"),
        "size rule requires 'size_gt_mb' and/or 'size_lt_mb' in condition",
    ),
    "confidence": (
        ("confidence_below",),
        "confidence rule requires 'confidence_below' in condition",
    ),
}


def _validate_condition_for_rule_type(rule_type: str | None, condition: dict[str, Any]) -> None:
    """Raise ValueError when `condition` lacks the keys `rule_type` requires."""
    requirement = _CONDITION_REQUIREMENTS.get(rule_type or "")
    if requirement is None:
        return
    required_keys, message = requirement
    if not any(key in condition for key in required_keys):
        raise ValueError(message)


class RuleCondition(BaseModel):
    """Condition for a routing rule.

    Each rule_type has a specific condition structure:
    - mime_type: {"mime_type": "application/pdf"} or {"mime_types": ["text/html", ...]}
    - size: {"size_gt_mb": 10} and/or {"size_lt_mb": 100}
    - confidence: {"confidence_below": 0.7}
    - always: {} (empty — matches everything)
    """

    mime_type: str | None = None
    mime_types: list[str] | None = None
    size_gt_mb: float | None = None
    size_lt_mb: float | None = None
    confidence_below: float | None = None


class CreateRuleRequest(StrictRequest):
    """Request body for creating a routing rule."""

    rule_type: str = Field(
        ...,
        description="Rule type: mime_type, size, confidence, or always",
    )
    condition: dict[str, Any] = Field(
        ...,
        description="Rule condition (JSONB). Structure depends on rule_type.",
    )
    action: str = Field(
        ...,
        description="Action when rule matches: 'stage' or 'direct_ingest'",
    )
    priority: int = Field(
        default=100,
        ge=0,
        le=10000,
        description="Priority (lower = evaluated first). Default 100.",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this rule is active.",
    )

    @field_validator("rule_type")
    @classmethod
    def validate_rule_type(cls, v: str) -> str:
        allowed = {"mime_type", "size", "confidence", "always"}
        if v not in allowed:
            raise ValueError(f"rule_type must be one of {sorted(allowed)}, got '{v}'")
        return v

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        allowed = {"stage", "direct_ingest"}
        if v not in allowed:
            raise ValueError(f"action must be one of {sorted(allowed)}, got '{v}'")
        return v

    @field_validator("condition")
    @classmethod
    def validate_condition_for_type(cls, v: dict[str, Any], info: Any) -> dict[str, Any]:
        """Validate condition keys match the rule_type."""
        _validate_condition_for_rule_type(info.data.get("rule_type"), v)
        return v


class UpdateRuleRequest(StrictRequest):
    """Request body for updating a routing rule. All fields optional."""

    rule_type: str | None = None
    condition: dict[str, Any] | None = None
    action: str | None = None
    priority: int | None = Field(None, ge=0, le=10000)
    enabled: bool | None = None

    @field_validator("rule_type")
    @classmethod
    def validate_rule_type(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"mime_type", "size", "confidence", "always"}
        if v not in allowed:
            raise ValueError(f"rule_type must be one of {sorted(allowed)}, got '{v}'")
        return v

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"stage", "direct_ingest"}
        if v not in allowed:
            raise ValueError(f"action must be one of {sorted(allowed)}, got '{v}'")
        return v

    @model_validator(mode="after")
    def validate_condition_matches_rule_type(self) -> "UpdateRuleRequest":
        """Cross-validate condition against rule_type when both are provided."""
        if self.rule_type is not None and self.condition is not None:
            _validate_condition_for_rule_type(self.rule_type, self.condition)
        return self


class ContentRuleResponse(BaseModel):
    """Response schema for a single content routing rule."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    corpus_id: UUID
    rule_type: str
    condition: dict[str, Any]
    action: str
    priority: int
    enabled: bool
    created_at: datetime


class ContentRuleListResponse(BaseModel):
    """Response for listing content routing rules."""

    items: list[ContentRuleResponse]
    count: int


class RoutingDecision(BaseModel):
    """Result of the content routing engine evaluation."""

    action: str = Field(
        ...,
        description="Determined action: 'stage' or 'direct_ingest'",
    )
    matched_rule_id: UUID | None = Field(
        None,
        description="ID of the rule that matched, or None if default applied",
    )
    reason: str = Field(
        ...,
        description="Human-readable explanation of the routing decision",
    )
