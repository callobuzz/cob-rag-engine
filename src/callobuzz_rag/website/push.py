"""Instant re-reads: the learn key and the push endpoint (BIZ2).

A site that publishes a page can tell the engine ("this URL changed") and the page is read again
within a minute. The knowledge base owns one **learn key** (`lk_` + 32 characters): stored only as
its SHA-256 hash, shown once when it is made, replaced (never re-shown) by making a new one (D4).
The key is the credential of a public endpoint, so everything it can do is small and bounded:

- it can only ask for a re-read of a URL that is on one of the knowledge base's own learned sites
  and not left out by the owner; the content always comes from the server's own fetch;
- per key: `RAG_WEBSITE_PUSH_PER_MINUTE` and `RAG_WEBSITE_PUSH_PER_DAY` URLs, a request over the
  limit is refused whole (nothing queued); per URL once a minute;
- the endpoint spends crawl and embedding work, so with Redis down it **fails closed** (503).

Nothing here touches the network; `api/routes/website_public.py` is the thin route.
"""

import hashlib
import logging
import re
import secrets
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.website import WebsitePage, WebsiteSettings, WebsiteSource
from callobuzz_rag.website.service import ensure_settings
from callobuzz_rag.website.urls import canonical, in_scope, is_excluded, url_hash

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)

KEY_PREFIX: Final = "lk_"
KEY_RE: Final = re.compile(r"^lk_[A-Za-z0-9_-]{32}$")
URL_ONCE_SECONDS: Final = 60
MAX_URLS: Final = 100
IP_PER_MINUTE: Final = 120  # requests per client address, before any key is looked up
_PARKED: Final = ("paused", "blocked")

INVALID_KEY: Final = "This update link is not valid. Make a new one in your dashboard."
UNAVAILABLE: Final = "Updates by link are not available right now. Please try again later."


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_key() -> tuple[str, str, str]:
    """(the key, its hash, the last four characters shown as a hint)."""
    raw = KEY_PREFIX + secrets.token_urlsafe(24)  # 32 URL-safe characters
    return raw, hash_key(raw), raw[-4:]


async def create_key(session: "AsyncSession", corpus: "Corpus", request: "Request | None") -> str:
    """Make the knowledge base's learn key (replacing any earlier one). Commits. The only time
    the key is returned."""
    row = await ensure_settings(session, corpus)
    raw, digest, hint = new_key()
    replaced = row.learn_key_hash is not None
    row.learn_key_hash, row.learn_key_hint = digest, hint
    row.learn_key_created_at = datetime.now(UTC)
    await record_audit(
        session,
        request,
        "website.learn_key_created",
        tenant_id=corpus.tenant_id,
        target_type="corpus",
        target_id=str(corpus.id),
        details={"corpus_id": str(corpus.id), "hint": hint, "replaced": replaced},
    )
    await session.commit()
    return raw


async def revoke_key(session: "AsyncSession", corpus: "Corpus", request: "Request | None") -> bool:
    """Remove the learn key. Commits. False when there was none."""
    row = await ensure_settings(session, corpus)
    if row.learn_key_hash is None:
        await session.commit()
        return False
    hint = row.learn_key_hint
    row.learn_key_hash = row.learn_key_hint = row.learn_key_created_at = None
    await record_audit(
        session,
        request,
        "website.learn_key_revoked",
        tenant_id=corpus.tenant_id,
        target_type="corpus",
        target_id=str(corpus.id),
        details={"corpus_id": str(corpus.id), "hint": hint},
    )
    await session.commit()
    return True


async def resolve_key(session: "AsyncSession", raw: str) -> WebsiteSettings | None:
    """The settings row a learn key belongs to (None for an unknown, malformed or replaced key
    alike: the caller answers all three the same way)."""
    if not KEY_RE.match(raw):
        return None
    return (
        await session.execute(
            select(WebsiteSettings).where(WebsiteSettings.learn_key_hash == hash_key(raw))
        )
    ).scalar_one_or_none()


# ------------------------------------------------------------------------------- Redis


def get_redis(app: Any) -> Any:
    """The app's Redis client for push and sensor limits (made on first use; tests set
    `app.state.website_redis`)."""
    client = getattr(app.state, "website_redis", None)
    if client is None:
        import redis.asyncio as aioredis

        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            app.state.settings.redis_url, socket_timeout=2, socket_connect_timeout=2
        )
        app.state.website_redis = client
    return client


async def charge(
    redis: Any, key_id: str, urls: int, settings: "Settings", now: float | None = None
) -> None:
    """Count `urls` against the key's minute and day limits.

    Raises:
        APIError: 429 `learn_rate_limited` (with Retry-After) over a limit, 503
            `learn_unavailable` when Redis cannot be reached (fail closed).
    """
    moment = time.time() if now is None else now
    limits = (
        ("m", 60, settings.website_push_per_minute),
        ("d", 86_400, settings.website_push_per_day),
    )
    try:
        for label, window, limit in limits:
            slot = int(moment // window)
            name = f"learn:{label}:{key_id}:{slot}"
            count = int(await redis.incrby(name, urls))
            await redis.expire(name, window * 2, nx=True)
            if count > limit:
                wait = max(1, int(window - (moment - slot * window)))
                raise APIError(
                    429,
                    "Too many updates in a short time. Please try again in a little while.",
                    "learn_rate_limited",
                    headers={"Retry-After": str(wait)},
                    retry_after=wait,
                )
    except APIError:
        raise
    except Exception as exc:
        logger.warning("Redis unavailable for the push limits: refusing", exc_info=True)
        raise APIError(503, UNAVAILABLE, "learn_unavailable") from exc


async def charge_ip(redis: Any, ip: str, now: float | None = None) -> None:
    """Count one request of this client address, before the key is looked at: random keys
    cost a database lookup each, so they are limited like everything else. Fails open (a Redis
    outage is not a reason to refuse a real site; `charge` fails closed right after).

    Raises:
        APIError: 429 `learn_rate_limited` (with Retry-After) over the limit.
    """
    moment = time.time() if now is None else now
    slot = int(moment // 60)
    name = f"learn:ip:{ip}:{slot}"
    try:
        count = int(await redis.incr(name))
        await redis.expire(name, 120, nx=True)
    except Exception:
        logger.warning("Redis unavailable for the push address limit: allowing", exc_info=True)
        return
    if count > IP_PER_MINUTE:
        wait = max(1, int(60 - (moment - slot * 60)))
        raise APIError(
            429,
            "Too many updates in a short time. Please try again in a little while.",
            "learn_rate_limited",
            headers={"Retry-After": str(wait)},
            retry_after=wait,
        )


async def release_urls(redis: Any, source_id: uuid.UUID, urls: Sequence[str]) -> None:
    """Forget that these URLs were queued (the queue refused them), so a retry is not counted
    as already done. Best effort."""
    try:
        if urls:
            await redis.delete(*[f"learn:u:{source_id}:{url_hash(u)}" for u in urls])
    except Exception:
        logger.warning("Could not release the push markers", exc_info=True)


async def once_per_minute(redis: Any, source_id: uuid.UUID, urls: Sequence[str]) -> list[str]:
    """The URLs not queued in the last minute (each one is marked as queued now)."""
    fresh: list[str] = []
    try:
        for url in urls:
            if await redis.set(
                f"learn:u:{source_id}:{url_hash(url)}", "1", ex=URL_ONCE_SECONDS, nx=True
            ):
                fresh.append(url)
    except Exception as exc:
        logger.warning("Redis unavailable for the push de-duplication: refusing", exc_info=True)
        raise APIError(503, UNAVAILABLE, "learn_unavailable") from exc
    return fresh


# ------------------------------------------------------------------------------ the URLs


@dataclass
class Accepted:
    by_source: dict[uuid.UUID, list[str]] = field(default_factory=dict)
    rejected: int = 0

    @property
    def accepted(self) -> int:
        return sum(len(v) for v in self.by_source.values())


async def accept_urls(
    session: "AsyncSession", settings_row: WebsiteSettings, urls: Sequence[str]
) -> Accepted:
    """Sort pushed URLs into those that may be re-read (on a live site of this knowledge base,
    not left out) and the rest. Query strings are dropped: query variants are never learned."""
    out = Accepted()
    sources = [
        s
        for s in (
            (
                await session.execute(
                    select(WebsiteSource).where(WebsiteSource.corpus_id == settings_row.corpus_id)
                )
            )
            .scalars()
            .all()
        )
        if s.status not in _PARKED
    ]
    excludes = list(settings_row.exclude_patterns or [])
    candidates: list[tuple[WebsiteSource, str]] = []
    for raw in dict.fromkeys(urls):
        url = canonical(raw)
        source = _owner_of(url, sources) if url is not None else None
        if url is None or source is None or is_excluded(url, excludes) is not None:
            out.rejected += 1
            continue
        candidates.append((source, url))
    left_out: set[tuple[uuid.UUID, str]] = set()
    if candidates:
        rows = (
            (
                await session.execute(
                    select(WebsitePage).where(
                        WebsitePage.corpus_id == settings_row.corpus_id,
                        WebsitePage.excluded_by_owner.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        left_out = {(p.source_id, p.url) for p in rows}
    for source, url in candidates:
        if (source.id, url) in left_out:
            out.rejected += 1
        else:
            out.by_source.setdefault(source.id, []).append(url)
    return out


def _owner_of(url: str, sources: Sequence[WebsiteSource]) -> WebsiteSource | None:
    from urllib.parse import urlsplit

    for source in sources:
        if in_scope(url, source.scope, port=urlsplit(source.origin).port):
            return source
    return None


def dispatch_urls(source_id: uuid.UUID, urls: Sequence[str], kind: str, trigger: str) -> bool:
    """Queue the server-side read of these URLs. False when the queue refused (logged)."""
    try:
        from callobuzz_rag.worker.website_tasks import learn_urls_task

        learn_urls_task.delay(str(source_id), list(urls), kind, trigger)
    except Exception:
        logger.error("Could not queue the page reads of %s", source_id, exc_info=True)
        return False
    return True
