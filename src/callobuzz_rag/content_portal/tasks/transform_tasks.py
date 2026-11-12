"""AI transformation Celery tasks for staged content."""

import asyncio
import logging

from celery import Task

from callobuzz_rag.core.config import Settings, get_settings
from callobuzz_rag.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="portal.transform_content",
    max_retries=3,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    queue="portal",
)
def transform_content_task(
    self: Task,
    staged_content_id: str,
    corpus_id: str,
    model_override: str | None = None,
) -> dict[str, str]:
    """Transform staged content using AI.

    Dispatches the async transform pipeline via asyncio.run().

    Args:
        staged_content_id: UUID string of the staged content record.
        corpus_id: UUID string of the corpus (for BYOK key resolution).
        model_override: Optional LLM model override.

    Returns:
        Dict with task result metadata.
    """
    self.update_state(
        state="PROCESSING",
        meta={"staged_content_id": staged_content_id},
    )

    settings = get_settings()

    try:
        asyncio.run(_run_transform(staged_content_id, corpus_id, settings, model_override))
        return {"status": "completed", "staged_content_id": staged_content_id}
    except (ConnectionError, TimeoutError):
        raise  # Let Celery autoretry handle these
    except Exception as e:
        logger.exception("Transform task failed for content %s: %s", staged_content_id, e)
        return {"status": "failed", "error": str(e), "staged_content_id": staged_content_id}


async def _run_transform(
    staged_content_id: str,
    corpus_id: str,
    settings: Settings,
    model_override: str | None,
) -> None:
    """Async wrapper for the transform pipeline."""
    from callobuzz_rag.content_portal.services.ai_transformer import (
        run_transform_pipeline,
    )

    await run_transform_pipeline(staged_content_id, corpus_id, settings, model_override)
