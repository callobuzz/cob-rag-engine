"""Shared base class for request bodies.

Pydantic's default (`extra="ignore"`) drops a field a model does not declare and lets the
request succeed, so a typo or a field from the wrong API version is a silent no-op. Every
request body inherits `StrictRequest` instead: an unknown field is a 422 whose `detail`
entry has type `extra_forbidden` and the field's path in `loc`. A subclass that sets its own
`model_config` keeps this (pydantic merges the configs).

Response models are not affected: they are built from the engine's own data.
"""

from pydantic import BaseModel, ConfigDict


class StrictRequest(BaseModel):
    """A request body that rejects fields it does not declare."""

    model_config = ConfigDict(extra="forbid")
