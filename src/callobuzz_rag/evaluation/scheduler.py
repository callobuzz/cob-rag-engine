"""Eval scheduling and config-change triggers.

Provides:
- settings_hash(): deterministic SHA256 of retrieval-related settings
- check_config_change(): detects settings drift against stored hash
- Celery tasks: run_scheduled_eval, check_and_trigger_eval
"""

import asyncio
import hashlib
import json
import logging
from typing import Any

from callobuzz_rag.core.config import Settings, get_settings
from callobuzz_rag.evaluation.optional import ragas_available

logger = logging.getLogger(__name__)

# Settings fields that affect retrieval quality.
# Changing any of these should trigger a re-evaluation.
_RETRIEVAL_SETTINGS_FIELDS: list[str] = [
    "retrieval_top_k_dense",
    "retrieval_top_k_sparse",
    "rrf_k",
    "mmr_lambda",
    "mmr_enabled",
    "temporal_decay_enabled",
    "temporal_decay_half_life_days",
    "rerank_model",
    "rerank_top_n_input",
    "rerank_top_k_output",
    "jina_model",
    "llm_model",
    "query_final_k",
    "hyde_enabled",
]


def settings_hash(settings: Settings) -> str:
    """Compute a deterministic SHA256 hash of retrieval-related settings.

    Only fields in _RETRIEVAL_SETTINGS_FIELDS are included. The hash is
    stable across restarts as long as the settings values are identical.

    Args:
        settings: Application settings instance.

    Returns:
        64-char lowercase hex SHA256 digest.
    """
    data: dict[str, object] = {}
    for field_name in sorted(_RETRIEVAL_SETTINGS_FIELDS):
        data[field_name] = getattr(settings, field_name)

    # json.dumps with sort_keys for deterministic serialization
    payload = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Config table key for storing the retrieval settings hash.
CONFIG_KEY_SETTINGS_HASH = "retrieval_settings_hash"


async def check_config_change(session: Any, settings: Settings) -> bool:
    """Compare current settings hash against the stored hash in the config table.

    If the hash differs (or no hash is stored yet), updates the stored hash
    and returns True. If identical, returns False.

    Args:
        session: Async SQLAlchemy session.
        settings: Current application settings.

    Returns:
        True if settings changed (or first run), False otherwise.
    """
    from sqlalchemy import select

    from callobuzz_rag.db.models.config import Config

    current_hash = settings_hash(settings)

    result = await session.execute(select(Config).where(Config.key == CONFIG_KEY_SETTINGS_HASH))
    config_row = result.scalar_one_or_none()

    if config_row is None:
        # First run — store the initial hash
        session.add(Config(key=CONFIG_KEY_SETTINGS_HASH, value={"hash": current_hash}))
        await session.commit()
        logger.info("Stored initial settings hash: %s", current_hash[:12])
        return True

    stored_hash = config_row.value.get("hash") if config_row.value else None

    if stored_hash == current_hash:
        return False

    # Hash changed — update stored value
    config_row.value = {"hash": current_hash}
    await session.commit()
    logger.info(
        "Settings hash changed: %s -> %s",
        (stored_hash or "none")[:12],
        current_hash[:12],
    )
    return True


def _create_session() -> Any:
    """Create an async session for use inside Celery tasks.

    Deferred import to avoid circular dependencies at module level.
    """
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    return factory()


def _create_webhook_dispatcher() -> Any | None:
    """Create a WebhookDispatcher for eval tasks. Returns None on failure."""
    try:
        from callobuzz_rag.core.webhooks import WebhookDispatcher
        from callobuzz_rag.db.engine import create_engine, create_session_factory

        settings = get_settings()
        engine = create_engine(settings)
        factory = create_session_factory(engine)
        return WebhookDispatcher(settings, session_factory=factory)
    except Exception:
        logger.debug("Webhook dispatcher not available", exc_info=True)
        return None


def _create_eval_runner() -> Any:
    """Create an EvaluationRunner instance with a session factory.

    Deferred import — evaluation module is heavy (RAGAS, embeddings).
    """
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.evaluation.runner import EvaluationRunner

    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    return EvaluationRunner(
        session_factory=factory,
        webhook_dispatcher=_create_webhook_dispatcher(),
    )


def run_scheduled_eval(
    corpus_id: Any | None = None,
    *,
    sample_size: int | None = None,
    run_id: Any | None = None,
    kind: str | None = None,
) -> dict[str, Any] | None:
    """Celery task: run a full RAGAS evaluation, or (`kind="golden"`) a golden-set run.

    Bridges Celery's sync execution model to async EvaluationRunner.
    Called by Celery Beat on the configured cron schedule.

    Args:
        corpus_id: Optional corpus to scope evaluation to.
        sample_size: Max query logs to evaluate (runner default when None).
        run_id: The queued EvalRun an admin trigger created, if any.
        kind: "golden" runs the queued golden run `run_id` (conversation C7); anything else
            is the traffic evaluation.

    Returns:
        A JSON-safe summary (id, status, sample_size, metrics), None on failure.
    """
    if kind != "golden" and run_id is None and not ragas_available():
        # The beat schedule fires this for traffic runs; without the add-on there is nothing
        # to do and nothing to report as an error.
        logger.info(
            "Skipping scheduled evaluation: the evaluation add-on (RAGAS) is not installed "
            "(pip install 'cob-rag-engine[eval]', Docker: INSTALL_EVAL=true)"
        )
        return {"status": "skipped", "reason": "evaluation_addon_missing"}
    logger.info("Starting scheduled evaluation run")
    try:
        result = asyncio.run(
            _run_eval_async(corpus_id=corpus_id, sample_size=sample_size, run_id=run_id, kind=kind)
        )
        logger.info("Scheduled eval complete: %s", result)
        return result
    except Exception:
        logger.exception("Scheduled evaluation failed")
        return None


async def _run_eval_async(
    corpus_id: Any | None = None,
    sample_size: int | None = None,
    run_id: Any | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Async inner function for run_scheduled_eval.

    Returns a plain dict: the Celery result backend serializes it as JSON, which
    an EvalRun ORM object is not.
    """
    import uuid

    if kind == "golden":
        return await _run_golden_async(_as_uuid(run_id))

    runner = _create_eval_runner()
    kwargs: dict[str, Any] = {
        "corpus_id": _as_uuid(corpus_id),
        "run_id": _as_uuid(run_id),
        "triggered_by": "manual" if run_id is not None else "schedule",
    }
    if sample_size:
        kwargs["sample_size"] = int(sample_size)
    run = await runner.run(**kwargs)
    return {
        "eval_run_id": str(run.id) if isinstance(run.id, uuid.UUID) else run.id,
        "status": run.status,
        "sample_size": run.sample_size,
        "metrics": run.metrics or {},
    }


async def _run_golden_async(run_id: Any) -> dict[str, Any]:
    """Run one queued golden run. The runner opens a session per use, so it gets the factory;
    the engine is disposed when the task's event loop ends."""
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.db.models import EvalRun
    from callobuzz_rag.evaluation.golden_runner import GoldenRunner

    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        await GoldenRunner(factory, settings).execute(run_id)
        async with factory() as session:
            run = await session.get(EvalRun, run_id)
            return {
                "eval_run_id": str(run_id),
                "status": run.status if run is not None else "missing",
            }
    finally:
        await engine.dispose()


def _as_uuid(value: Any | None) -> Any | None:
    """Celery hands ids over as strings; the models compare UUIDs."""
    import uuid

    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def check_and_trigger_eval() -> None:
    """Celery task: check if retrieval settings changed, trigger eval if so.

    Runs periodically (every 300s by default). Computes the current settings
    hash and compares against the stored hash in the config table. If different,
    dispatches run_scheduled_eval as an async Celery task.
    """
    logger.debug("Checking for config changes")
    try:
        changed = asyncio.run(_check_config_async())
        if changed:
            logger.info("Config change detected — triggering evaluation")
            from callobuzz_rag.worker.celery_app import celery_app

            celery_app.send_task("callobuzz_rag.run_evaluation")
        else:
            logger.debug("No config change detected")
    except Exception:
        logger.exception("Config change check failed")


async def _check_config_async() -> bool:
    """Async inner function for check_and_trigger_eval."""
    settings = get_settings()
    async with _create_session() as session:
        return await check_config_change(session, settings)
