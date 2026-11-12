"""Structured logging configuration.

Call configure_logging() at application startup (API lifespan, worker startup).
Uses structlog for structured JSON logging with correlation IDs.
"""

import logging
import re

import structlog

# BIZ2 redact: the push key is part of the URL path (IndexNow-style), and paths are logged.
_LEARN_KEY = re.compile(r"(/public/learn/)(lk_)?[A-Za-z0-9_-]{8,}")


def _mask_learn_key(match: re.Match[str]) -> str:
    return f"{match.group(1)}lk_…{match.group(0)[-4:]}"


class LearnKeyRedactor(logging.Filter):
    """Masks the push key in access-log lines: `/public/learn/lk_…ab12`. Never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and "/public/learn/" in record.msg:
            record.msg = _LEARN_KEY.sub(_mask_learn_key, record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _LEARN_KEY.sub(_mask_learn_key, a)
                if isinstance(a, str) and "/public/learn/" in a
                else a
                for a in record.args
            )
        return True


# /BIZ2 redact


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structured logging for the application."""
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, log_level.upper(), logging.INFO),
    )
    # BIZ2 redact: one filter on uvicorn's access logger (never added twice)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, LearnKeyRedactor) for f in access.filters):
        access.addFilter(LearnKeyRedactor())
    # /BIZ2 redact
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),  # Switch to JSONRenderer in production
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
    )
