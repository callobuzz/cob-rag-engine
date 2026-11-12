"""Process-level billing switches for code that has no request (usage metering, beat tasks).

One cached read of `Settings`; tests call `reset_billing_config_cache()` after changing the
environment. Request code can use `require_billing_enabled` as a route dependency.
"""

from functools import lru_cache

from fastapi import Request

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.core.config import get_settings


@lru_cache(maxsize=1)
def _read() -> tuple[bool, float]:
    settings = get_settings()
    return settings.billing_enabled, settings.usd_inr_rate


def billing_enabled() -> bool:
    """Metering, charging, posting and pausing all read this; it follows RAG_BILLING_ENABLED."""
    return _read()[0]


def usd_inr_rate() -> float:
    return _read()[1]


def reset_billing_config_cache() -> None:
    _read.cache_clear()


def require_billing_enabled(request: Request) -> None:
    """Route dependency: every Part B billing route answers 404 `billing_disabled` when off."""
    if not billing_enabled():
        raise APIError(404, "Billing is turned off on this engine.", "billing_disabled")
