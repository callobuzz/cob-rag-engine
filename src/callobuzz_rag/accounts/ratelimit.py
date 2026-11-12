"""Rate limits and login lockout for the account routes (accounts A3).

Fixed windows in Redis (the `WidgetRateLimiter._hit` pattern, `widget/security.py`), but
**fail closed**: when Redis cannot answer, account routes answer 503
`rate_limiter_unavailable` instead of running unlimited. (The widget fails open; a
password guesser must not get unlimited tries because Redis blipped.)

Lockout is keyed by sha256 of the normalized e-mail whether or not an account exists, so
it reveals nothing: failures are counted in `acct:fail:{h}` over 15 minutes, and from the
5th failure a lock `acct:lock:{h}` of `min(900, 60 * 2**(n-5))` seconds refuses every
attempt, even with the right password. A success clears both. No Redis key holds a raw
e-mail or IP.
"""

import hashlib
import logging
from typing import Any, Final

from callobuzz_rag.accounts.passwords import normalize_email
from callobuzz_rag.api.errors import APIError

logger = logging.getLogger(__name__)

FAILURE_WINDOW_S: Final = 900
LOCK_AFTER: Final = 5
LOCK_BASE_S: Final = 60
LOCK_MAX_S: Final = 900


def lock_seconds(failures: int) -> int:
    """Lock length after `failures` consecutive failures (0 below the threshold)."""
    if failures < LOCK_AFTER:
        return 0
    return int(min(LOCK_MAX_S, LOCK_BASE_S * 2 ** (failures - LOCK_AFTER)))


def _h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unavailable() -> APIError:
    return APIError(
        503,
        "Sign-in is temporarily unavailable. Try again in a minute.",
        "rate_limiter_unavailable",
    )


class AccountRateLimiter:
    """Redis-backed limits for sign-in, refresh, reset and password changes."""

    def __init__(self, redis_url: str | None = None, *, client: Any = None) -> None:
        if client is not None:
            self._redis = client
        else:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(  # type: ignore[no-untyped-call]
                redis_url, socket_timeout=2, socket_connect_timeout=2
            )

    async def hit(self, bucket: str, identity: str, limit: int, window_s: int) -> None:
        """Count one request for (bucket, identity).

        Raises:
            APIError 429 `rate_limited` (with Retry-After) over the limit; 503
            `rate_limiter_unavailable` when Redis fails.
        """
        key = f"acct:rl:{bucket}:{_h(identity)}"
        try:
            pipe = self._redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_s, nx=True)
            pipe.ttl(key)
            count, _, ttl = await pipe.execute()
        except Exception as exc:
            logger.warning("Account rate limiter unavailable: %s", type(exc).__name__)
            raise _unavailable() from exc
        if int(count) > limit:
            retry = max(int(ttl), 1)
            raise APIError(
                429,
                "Too many attempts. Please wait and try again.",
                "rate_limited",
                headers={"Retry-After": str(retry)},
            )

    async def check_lock(self, email: str) -> None:
        """Raises APIError 429 `account_locked` (Retry-After) while the address is locked."""
        key = f"acct:lock:{_h(normalize_email(email))}"
        try:
            ttl = int(await self._redis.ttl(key))
        except Exception as exc:
            logger.warning("Account rate limiter unavailable: %s", type(exc).__name__)
            raise _unavailable() from exc
        if ttl > 0:
            raise APIError(
                429,
                "Too many failed sign-ins. Try again later.",
                "account_locked",
                headers={"Retry-After": str(ttl)},
            )

    async def record_failure(self, email: str) -> int:
        """Count a failed sign-in; lock the address from the 5th. Returns the count."""
        h = _h(normalize_email(email))
        try:
            pipe = self._redis.pipeline()
            pipe.incr(f"acct:fail:{h}")
            pipe.expire(f"acct:fail:{h}", FAILURE_WINDOW_S, nx=True)
            count, _ = await pipe.execute()
            failures = int(count)
            seconds = lock_seconds(failures)
            if seconds:
                await self._redis.set(f"acct:lock:{h}", "1", ex=seconds)
        except Exception as exc:
            logger.warning("Account rate limiter unavailable: %s", type(exc).__name__)
            raise _unavailable() from exc
        return failures

    async def clear_failures(self, email: str) -> None:
        h = _h(normalize_email(email))
        try:
            await self._redis.delete(f"acct:fail:{h}", f"acct:lock:{h}")
        except Exception as exc:
            logger.warning("Account rate limiter unavailable: %s", type(exc).__name__)
            raise _unavailable() from exc

    async def close(self) -> None:
        await self._redis.aclose()
