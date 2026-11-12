"""CLI entry point for running RAGAS evaluation.

Usage: python -m callobuzz_rag.evaluation.cli [--sample-size 50] [--triggered-by manual]
"""

import argparse
import asyncio
import logging
import sys

from callobuzz_rag.core.config import get_settings
from callobuzz_rag.db.engine import create_engine, create_session_factory
from callobuzz_rag.evaluation.runner import EvaluationRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def _run_eval(sample_size: int, triggered_by: str) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    runner = EvaluationRunner(session_factory=session_factory)

    logger.info(
        "Starting RAGAS evaluation (sample_size=%d, triggered_by=%s)",
        sample_size,
        triggered_by,
    )
    eval_run = await runner.run(sample_size=sample_size, triggered_by=triggered_by)

    if eval_run.status == "completed":
        logger.info(
            "Evaluation completed — %d samples evaluated",
            eval_run.sample_size,
        )
        if eval_run.metrics:
            for name, data in eval_run.metrics.items():
                status = "PASS" if data.get("passed") else "FAIL"
                logger.info("  %s: %.4f [%s]", name, data.get("score", 0), status)
        else:
            logger.info("  No metrics (0 valid samples)")
    else:
        logger.error("Evaluation failed: %s", eval_run.error_message)
        sys.exit(1)

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RAGAS evaluation")
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument(
        "--triggered-by",
        default="manual",
        choices=["manual", "schedule", "config_change"],
    )
    args = parser.parse_args()
    asyncio.run(_run_eval(args.sample_size, args.triggered_by))


if __name__ == "__main__":
    main()
