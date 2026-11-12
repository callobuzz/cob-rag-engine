"""Replies of the demo routes (L5a)."""

from datetime import datetime

from pydantic import BaseModel, Field


class DemoStatusOut(BaseModel):
    enabled: bool = Field(description="Demo mode is on (RAG_DEMO_MODE)")
    running: bool = Field(description="A reset is running now")
    last_reset_at: datetime | None = Field(description="When the last good reset finished")
    last_result: str | None = Field(description="`ok` or `failed`: how the last reset ended")
    last_failed_step: str | None = Field(
        None, description="The step the last failed reset stopped at"
    )
    next_reset_at: datetime | None = Field(description="The next nightly reset (UTC)")
    model_calls_today: int | None = Field(
        description="Model calls counted today (UTC); null when the counter cannot be read"
    )
    model_calls_limit: int = Field(description="The daily ceiling (RAG_DEMO_MODEL_CALLS_PER_DAY)")
    answers_today: int | None = Field(
        None,
        description=(
            "Answers the whole demo has given since its day began (the last reset); "
            "null when the counter cannot be read"
        ),
    )
    answers_limit: int = Field(description="The demo's daily answers (RAG_DEMO_ANSWERS_PER_DAY)")
    answers_per_visitor_limit: int = Field(
        description="One visitor's daily answers (RAG_DEMO_ANSWERS_PER_VISITOR_PER_DAY)"
    )
