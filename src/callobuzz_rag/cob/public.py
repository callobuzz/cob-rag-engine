"""The public, docs-only COB on the sign-in pages.

Visitors who are not signed in can ask COB about the engine. Answers come from the `public`
pages only, with no account data, no tools and nothing stored. The operator's model key pays,
so everything here is strict: a per-IP hourly limit and an engine-wide daily limit, both
**fail closed** (Redis down means no answer, never an unlimited one), and the client's earlier
turns are masked and never written anywhere.
"""

import hashlib
import ipaddress
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from callobuzz_rag.api.errors import APIError

logger = logging.getLogger(__name__)

HOUR_S: Final = 3600
IP_KEY_TTL_S: Final = HOUR_S + 60
DAY_KEY_TTL_S: Final = 86400 + 3600


def seconds_to_utc_midnight(now: datetime) -> int:
    """Whole seconds from `now` to the next 00:00 UTC (at least 1)."""
    now = now.astimezone(UTC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((midnight - now).total_seconds()))


def _seconds_to_next_hour(now: datetime) -> int:
    now = now.astimezone(UTC)
    return max(1, HOUR_S - (now.minute * 60 + now.second))


def _ip_bucket(ip: str) -> str:
    """The address a limit is counted against. An IPv6 visitor owns a whole /64 and can rotate
    inside it, so those share one bucket; anything unparseable is counted as written."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


@dataclass(frozen=True)
class PublicLimitResult:
    allowed: bool
    scope: str = ""  # "ip" | "daily" when refused
    retry_after: int = 0


class PublicCobLimiter:
    """Fixed windows in Redis (`INCR` + `EXPIRE NX`), keyed by the hash of the client IP."""

    def __init__(self, redis: Any, *, per_ip_per_hour: int, daily: int) -> None:
        self._redis = redis
        self._per_ip = per_ip_per_hour
        self._daily = daily

    async def _count(self, key: str, ttl: int) -> int:
        pipe = self._redis.pipeline()  # MULTI/EXEC by default
        pipe.incr(key)
        pipe.expire(key, ttl, nx=True)
        count, _ = await pipe.execute()
        return int(count)

    async def hit(self, ip: str, now: datetime) -> PublicLimitResult:
        """Count one message. The visitor's own limit is checked first, so a refused visitor
        never uses up the day's allowance for everyone else.

        Raises:
            APIError 503 `cob_public_unavailable` when Redis cannot answer.
        """
        now = now.astimezone(UTC)
        digest = hashlib.sha256(_ip_bucket(ip).encode("utf-8")).hexdigest()[:16]
        try:
            ip_count = await self._count(f"cobpub:ip:{digest}:{now:%Y%m%d%H}", IP_KEY_TTL_S)
            if ip_count > self._per_ip:
                return PublicLimitResult(False, "ip", _seconds_to_next_hour(now))
            day_count = await self._count(f"cobpub:day:{now:%Y%m%d}", DAY_KEY_TTL_S)
        except Exception as exc:
            logger.warning("Public COB limiter unavailable: %s", type(exc).__name__)
            raise APIError(503, "COB is unavailable right now.", "cob_public_unavailable") from exc
        if day_count > self._daily:
            return PublicLimitResult(False, "daily", seconds_to_utc_midnight(now))
        return PublicLimitResult(True)

    async def aclose(self) -> None:
        close = getattr(self._redis, "aclose", None)
        if close is not None:
            await close()


# ------------------------------------------------------------------ what a visitor may do

_SIGN_IN_HINT: Final = {
    "en": "Sign in to your workspace and ask me there. I can explain that once you are signed in.",
    "hi": ("अपने वर्कस्पेस में साइन इन करके मुझसे वहाँ पूछें। साइन इन होने के बाद मैं इसे समझा सकता हूँ।"),
    "hi-Latn": (
        "Apne workspace mein sign in karke mujhse wahin poochhiye. "
        "Sign in hone ke baad main ise samjha sakta hoon."
    ),
}
_RESTRICTED_AUDIENCES: Final = ["customer", "admin"]
DEFAULT_PROBE_THRESHOLD: Final = 0.5
HISTORY_ITEM_CHARS: Final = 2000


def sign_in_hint(language: str) -> str:
    """The reply to a question that only signed-in pages answer."""
    return _SIGN_IN_HINT.get(language, _SIGN_IN_HINT["en"])


async def restricted_hit(
    orchestrator: Any, corpus_id: Any, query: str, *, threshold: float | None
) -> bool:
    """Whether the customer or administrator pages match `query` as well as an answer must.
    Dense search only; the text found is discarded here, only the score is used."""
    from callobuzz_rag.cob.audience import _best_score

    best = await _best_score(orchestrator, corpus_id, query, _RESTRICTED_AUDIENCES)
    return best >= (DEFAULT_PROBE_THRESHOLD if threshold is None else threshold)


def mask_history(items: Any) -> Any:
    """The visitor's earlier messages for the model: personal data masked, each cut to
    `HISTORY_ITEM_CHARS`. They live in this request only."""
    from callobuzz_rag.conversation.pii import mask_pii
    from callobuzz_rag.llm.models import LLMMessage

    return [
        LLMMessage(role=item.role, content=mask_pii(item.content).text[:HISTORY_ITEM_CHARS])
        for item in items
    ]


def get_limiter(app: Any) -> PublicCobLimiter:
    """The engine's one limiter, created on first use (like the account limiter)."""
    limiter: PublicCobLimiter | None = getattr(app.state, "cob_public_limiter", None)
    if limiter is None:
        import redis.asyncio as aioredis

        settings = app.state.settings
        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url, socket_timeout=2, socket_connect_timeout=2
        )
        limiter = PublicCobLimiter(
            client,
            per_ip_per_hour=settings.cob_public_per_ip_per_hour,
            daily=settings.cob_public_daily_limit,
        )
        app.state.cob_public_limiter = limiter
    return limiter
