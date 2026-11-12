"""The dry run of a delete: what would go, and the name to confirm it with."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DeletePreviewResponse(BaseModel):
    kind: Literal["knowledge_base", "document", "customer"]
    id: UUID
    name: str = Field(description="What it is called")
    confirm_name: str = Field(
        description="Send this as ?confirm_name= on the DELETE; an API key's delete needs it"
    )
    counts: dict[str, int] = Field(
        description="What the delete removes: documents, chunks, widget_keys, conversations, "
        "memories (a knowledge base); versions, chunks (a document); knowledge_bases and the "
        "knowledge-base totals, members, api_keys (a customer)"
    )
    irreversible: bool = Field(True, description="Deletes cannot be undone")
